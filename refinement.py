"""Phase B — sample-based transcript refinement.

Given a discovery run and a sample of its episodes, fetch the sampled
transcripts and make one LLM call per transcript that re-judges the episode's
topic/subtopic assignments from what was actually said and proposes taxonomy
nodes the metadata pass missed. Idempotent at the run level. The LLM goes
through the ``extractor/`` registry — this module registers a typed prompt and
calls ``Extractor.run_batch``; it adds no second LLM-calling code path
(see ADR 0001).

The pipeline (``run_refinement``) is three stages so the transcript fetch — a
network walk that writes ``video_transcripts`` rows one at a time — runs
without holding the main DB connection:

1. resolve channel + discovery run, build the eligible pool, pick the sample;
2. fetch transcripts for the picked episodes (one replacement round for any
   that come back unavailable, then proceed short);
3. open a fresh connection, snapshot the taxonomy + each episode's current
   assignments, create the ``refinement_runs`` row (``status='pending'`` before
   any spend so a killed/declined run is auditable), optionally confirm cost,
   run the batch, and persist proposals + replace-wholesale refine assignments.

``stub_refinement_llm`` matches the per-batch ``LLMCallable`` signature and is
free + deterministic — pass it (or use ``refine --stub``) for any wiring check.
Tests inject it directly together with a fake ``transcript_fetcher``.
"""
from __future__ import annotations

import inspect
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from yt_channel_analyzer import db
from yt_channel_analyzer.db import connect, ensure_schema, upsert_video_transcript
from yt_channel_analyzer.extractor.errors import ExtractorError
from yt_channel_analyzer.extractor.pricing import estimate_cost
from yt_channel_analyzer.extractor.registry import Prompt, get_prompt, register_prompt
from yt_channel_analyzer.youtube import fetch_video_transcript


# Episodes at or below this duration are YouTube Shorts and never sampled
# (mirrors ``discovery.SHORTS_CUTOFF_SECONDS``; NULL duration counts as long).
SHORTS_CUTOFF_SECONDS = 180

STUB_MODEL = "stub"
# The model a ``refine --real`` run uses (Haiku); cost estimates assume it.
# Kept in sync with ``extractor.anthropic_runner.DEFAULT_MODEL`` by hand to
# avoid importing the Anthropic SDK path just for a string.
DEFAULT_REAL_MODEL = "claude-haiku-4-5-20251001"

REFINEMENT_PROMPT_NAME = "refinement.transcript"
REFINEMENT_PROMPT_VERSION = "refinement-v1"

# Rough cost-estimate constants for the ``refine --real`` pre-flight gate. The
# transcript dominates input tokens (~4 chars/token); the per-episode flat
# add-on covers the prompt scaffold + taxonomy + current-assignment lines, and
# a fixed output allowance covers the structured JSON reply.
_CHARS_PER_TOKEN = 4
_PROMPT_SCAFFOLD_TOKENS = 800
_OUTPUT_TOKENS_PER_EPISODE = 600


@dataclass(frozen=True)
class RefinementEpisodeContext:
    video_id: int  # internal videos.id
    youtube_video_id: str
    transcript_text: str
    current_assignments: list[dict[str, Any]]


@dataclass(frozen=True)
class RefinementPayload:
    """One transcript's re-judgement. ``assignments`` items are
    ``{topic, subtopic?, confidence, reason}``; proposal items carry
    ``name``/``evidence`` (+ ``parent_topic`` for subtopic proposals)."""

    assignments: list[dict[str, Any]] = field(default_factory=list)
    new_subtopic_proposals: list[dict[str, Any]] = field(default_factory=list)
    new_topic_proposals: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RefinementRun:
    run_id: int
    discovery_run_id: int | None
    sampled_youtube_ids: list[str]
    proposals: list[dict[str, Any]]
    reassignments: list[dict[str, Any]]
    status: str


# A batch LLM call: re-judge every sampled episode against the current taxonomy.
LLMCallable = Callable[
    [Sequence[RefinementEpisodeContext], list[dict[str, Any]]],
    list[RefinementPayload],
]


# --------------------------------------------------------------------------
# Extractor-registry prompt
# --------------------------------------------------------------------------

_REFINEMENT_SYSTEM = (
    "You are an editorial assistant re-judging one podcast episode from its "
    "full transcript.\n"
    "\n"
    "You are given the channel's current topic taxonomy (topics with their "
    "subtopics), the episode's current metadata-derived assignments, and the "
    "transcript. Reply with a single JSON object:\n"
    '  {"assignments": [{"topic": "Topic A", "subtopic": "Sub A1", '
    '"confidence": 0.9, "reason": "..."}],\n'
    '   "new_subtopic_proposals": [{"name": "New Sub", '
    '"parent_topic": "Topic A", "evidence": "..."}],\n'
    '   "new_topic_proposals": [{"name": "New Topic", "evidence": "..."}]}\n'
    "\n"
    "Rules:\n"
    "- `assignments` reflects what the transcript actually covers; an episode "
    "may have several entries when it genuinely covers each topic, but most "
    "episodes have one. Do not over-tag.\n"
    "- Every `topic` in `assignments` must be one of the existing taxonomy "
    "topics — do NOT invent a topic there. If the transcript clearly covers "
    "something with no fitting topic, raise it under `new_topic_proposals` "
    "instead (this should be rare).\n"
    "- A `subtopic` in an assignment is optional; it may be an existing "
    "subtopic of that topic or one you also list in `new_subtopic_proposals`.\n"
    "- Propose a new subtopic only when the transcript clearly covers a theme "
    "not already a subtopic of its parent topic; `parent_topic` must be an "
    "existing taxonomy topic. Include short `evidence` (a phrase or paraphrase "
    "from the transcript) for every proposal.\n"
    "- Supply `confidence` between 0.0 and 1.0 and a short `reason` for each "
    "assignment.\n"
    "- Output JSON only — no prose, no markdown fences."
)


_REFINEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["assignments", "new_subtopic_proposals", "new_topic_proposals"],
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic", "confidence", "reason"],
                "properties": {
                    "topic": {"type": "string", "minLength": 1},
                    "subtopic": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
        "new_subtopic_proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "parent_topic", "evidence"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "parent_topic": {"type": "string", "minLength": 1},
                    "evidence": {"type": "string"},
                },
            },
        },
        "new_topic_proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "evidence"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "evidence": {"type": "string"},
                },
            },
        },
    },
}


def _render_refinement_prompt(context: dict) -> str:
    lines: list[str] = [f"Episode id: {context.get('youtube_video_id', '')}", ""]
    lines.append("Current taxonomy (topic: subtopics):")
    taxonomy = context.get("taxonomy") or []
    if not taxonomy:
        lines.append("  (empty)")
    for topic in taxonomy:
        subs = ", ".join(topic.get("subtopics") or []) or "(none)"
        lines.append(f"  - {topic['topic']}: {subs}")
    lines.append("")
    lines.append("This episode's current metadata-derived assignments:")
    current = context.get("current_assignments") or []
    if not current:
        lines.append("  (none)")
    for assignment in current:
        sub = f" / {assignment['subtopic']}" if assignment.get("subtopic") else ""
        lines.append(
            f"  - {assignment['topic']}{sub} "
            f"(confidence {assignment.get('confidence')}; {assignment.get('reason')})"
        )
    lines.append("")
    lines.append("Transcript:")
    lines.append(context.get("transcript") or "")
    return "\n".join(lines).rstrip() + "\n"


def register_refinement_prompt() -> Prompt:
    """Register the refinement prompt; idempotent across repeat calls."""
    try:
        return get_prompt(REFINEMENT_PROMPT_NAME, REFINEMENT_PROMPT_VERSION)
    except ExtractorError:
        return register_prompt(
            name=REFINEMENT_PROMPT_NAME,
            version=REFINEMENT_PROMPT_VERSION,
            render=_render_refinement_prompt,
            schema=_REFINEMENT_SCHEMA,
            system=_REFINEMENT_SYSTEM,
        )


def _payload_from_response(data: dict) -> RefinementPayload:
    return RefinementPayload(
        assignments=list(data.get("assignments") or []),
        new_subtopic_proposals=list(data.get("new_subtopic_proposals") or []),
        new_topic_proposals=list(data.get("new_topic_proposals") or []),
    )


def refinement_llm_via_extractor(extractor: Any) -> LLMCallable:
    """Adapt an Extractor into the refinement ``LLMCallable``.

    One job per sampled episode; ``Extractor.run_batch`` routes through the
    batch API automatically at >=10 jobs. Schema validation + retry-once are
    owned by the Extractor. ``correlation_id`` (the refinement run id) is
    stamped on every ``llm_calls`` audit row for cost rollups.
    """
    register_refinement_prompt()

    def call(
        episodes: Sequence[RefinementEpisodeContext],
        taxonomy: list[dict[str, Any]],
        *,
        correlation_id: int | None = None,
    ) -> list[RefinementPayload]:
        jobs = [
            (
                REFINEMENT_PROMPT_NAME,
                REFINEMENT_PROMPT_VERSION,
                {
                    "youtube_video_id": ep.youtube_video_id,
                    "transcript": ep.transcript_text,
                    "taxonomy": taxonomy,
                    "current_assignments": ep.current_assignments,
                },
                correlation_id,
            )
            for ep in episodes
        ]
        results = extractor.run_batch(jobs)
        return [_payload_from_response(r.data) for r in results]

    return call


def make_real_refinement_llm_callable(
    connection: sqlite3.Connection,
    *,
    model: str | None = None,
) -> LLMCallable:
    """Construct a real-LLM refinement callable (Anthropic).

    Gated behind ``RALPH_ALLOW_REAL_LLM=1``; raises otherwise so the verify
    gate cannot accidentally spend tokens. Mirrors
    ``discovery.make_real_llm_callable``.
    """
    if os.environ.get("RALPH_ALLOW_REAL_LLM") != "1":
        raise RuntimeError(
            "Real LLM calls are gated behind RALPH_ALLOW_REAL_LLM=1. "
            "Set the env var to confirm you intend to spend money."
        )
    from yt_channel_analyzer.extractor.anthropic_runner import (
        DEFAULT_MODEL,
        AnthropicRunner,
    )
    from yt_channel_analyzer.extractor.runner import Extractor

    runner = AnthropicRunner(model=model or DEFAULT_MODEL)
    extractor = Extractor(connection=connection, runner=runner)
    return refinement_llm_via_extractor(extractor)


# --------------------------------------------------------------------------
# Stub LLM
# --------------------------------------------------------------------------

STUB_TOPIC_PROPOSAL_NAME = "Stub topic"
STUB_EVIDENCE = "stub evidence"


def _stub_subtopic_name(parent_topic: str) -> str:
    return f"Stub subtopic ({parent_topic})"


def stub_refinement_llm(
    episodes: Sequence[RefinementEpisodeContext],
    taxonomy: list[dict[str, Any]],
    *,
    correlation_id: int | None = None,
) -> list[RefinementPayload]:
    """Free, deterministic refinement stub: echo each episode's current
    assignments back as transcript-grade `assignments`, emit one
    `new_subtopic_proposal` per episode under its top current topic, and one
    `new_topic_proposal` for the first sampled episode only. Ignores
    ``correlation_id`` (present to match the ``LLMCallable`` convention)."""
    payloads: list[RefinementPayload] = []
    for index, episode in enumerate(episodes):
        assignments: list[dict[str, Any]] = []
        for current in episode.current_assignments:
            entry: dict[str, Any] = {
                "topic": current["topic"],
                "confidence": (
                    current.get("confidence")
                    if current.get("confidence") is not None
                    else 1.0
                ),
                "reason": current.get("reason") or "stub refine echo",
            }
            if current.get("subtopic"):
                entry["subtopic"] = current["subtopic"]
            assignments.append(entry)

        if episode.current_assignments:
            top_topic = max(
                episode.current_assignments,
                key=lambda a: (a.get("confidence") or 0.0),
            )["topic"]
        elif taxonomy:
            top_topic = taxonomy[0]["topic"]
        else:
            top_topic = "General"

        payloads.append(
            RefinementPayload(
                assignments=assignments,
                new_subtopic_proposals=[
                    {
                        "name": _stub_subtopic_name(top_topic),
                        "parent_topic": top_topic,
                        "evidence": STUB_EVIDENCE,
                    }
                ],
                new_topic_proposals=(
                    [{"name": STUB_TOPIC_PROPOSAL_NAME, "evidence": STUB_EVIDENCE}]
                    if index == 0
                    else []
                ),
            )
        )
    return payloads


def _invoke_llm(
    llm: LLMCallable,
    episodes: Sequence[RefinementEpisodeContext],
    taxonomy: list[dict[str, Any]],
    correlation_id: int,
) -> list[RefinementPayload]:
    try:
        sig = inspect.signature(llm)
    except (TypeError, ValueError):
        return list(llm(episodes, taxonomy))
    for param in sig.parameters.values():
        if param.name == "correlation_id" or param.kind is inspect.Parameter.VAR_KEYWORD:
            return list(llm(episodes, taxonomy, correlation_id=correlation_id))
    return list(llm(episodes, taxonomy))


# --------------------------------------------------------------------------
# Sample picker
# --------------------------------------------------------------------------


def _build_pool(
    connection: sqlite3.Connection, channel_id: int, discovery_run_id: int
) -> tuple[dict[int, dict[str, Any]], dict[str, list[int]], list[str]]:
    """Build the eligible-episode pool for ``discovery_run_id``.

    Eligible = a video of ``channel_id`` that is not a Short
    (``duration_seconds > 180`` or NULL) and is transcript-fetchable (no
    ``video_transcripts`` row, or one with ``transcript_status='available'``).

    Returns ``(pool, topic_members, topic_order)`` where ``pool`` maps
    ``video_id -> {youtube_video_id, best_confidence}`` (``best_confidence`` is
    ``None`` for episodes unassigned in this run — the "blind-spot" bucket),
    ``topic_members`` maps a topic name to its pool members sorted by their
    in-topic confidence desc, and ``topic_order`` lists topic names by total
    assigned-episode count in the run desc (name asc to break ties).
    """
    video_rows = connection.execute(
        "SELECT id, youtube_video_id, duration_seconds FROM videos WHERE channel_id = ?",
        (channel_id,),
    ).fetchall()
    status_by_video = {
        row["id"]: row["st"]
        for row in connection.execute(
            """
            SELECT v.id AS id, vtr.transcript_status AS st
            FROM videos v
            LEFT JOIN video_transcripts vtr ON vtr.video_id = v.id
            WHERE v.channel_id = ?
            """,
            (channel_id,),
        ).fetchall()
    }
    fetchable: dict[int, str] = {}
    for row in video_rows:
        duration = row["duration_seconds"]
        if duration is not None and duration <= SHORTS_CUTOFF_SECONDS:
            continue
        if status_by_video.get(row["id"]) not in (None, "available"):
            continue
        fetchable[row["id"]] = row["youtube_video_id"]

    assignment_rows = connection.execute(
        """
        SELECT vt.video_id AS video_id, t.name AS topic, vt.confidence AS confidence
        FROM video_topics vt
        JOIN topics t ON t.id = vt.topic_id
        WHERE vt.discovery_run_id = ?
        """,
        (discovery_run_id,),
    ).fetchall()

    topic_count: dict[str, int] = defaultdict(int)
    topic_members_raw: dict[str, list[tuple[int, float]]] = defaultdict(list)
    best_confidence: dict[int, float] = {}
    for row in assignment_rows:
        topic_count[row["topic"]] += 1
        video_id = row["video_id"]
        if video_id not in fetchable:
            continue
        conf = row["confidence"] if row["confidence"] is not None else 0.0
        topic_members_raw[row["topic"]].append((video_id, conf))
        if video_id not in best_confidence or conf > best_confidence[video_id]:
            best_confidence[video_id] = conf

    pool = {
        video_id: {
            "youtube_video_id": youtube_video_id,
            "best_confidence": best_confidence.get(video_id),
        }
        for video_id, youtube_video_id in fetchable.items()
    }
    topic_members = {
        topic: [vid for vid, _ in sorted(members, key=lambda item: (-item[1], item[0]))]
        for topic, members in topic_members_raw.items()
    }
    topic_order = sorted(topic_members, key=lambda t: (-topic_count[t], t))
    return pool, topic_members, topic_order


def _pick(
    pool: dict[int, dict[str, Any]],
    topic_members: dict[str, list[int]],
    topic_order: list[str],
    sample_size: int,
) -> tuple[list[int], list[int], list[int]]:
    """Pick the sample: ~2/3 coverage slots filled by round-robin over topics
    (each topic's single highest-confidence unpicked member; every topic gets
    one before any gets two), the remainder filled by blind-spot slots (pool
    ordered by lowest assignment confidence, then the unassigned bucket).

    Returns ``(coverage_video_ids, blindspot_video_ids, remaining_pool_video_ids)``
    — the picked sample is ``coverage + blindspot``; ``remaining`` is the leftover
    pool in blind-spot order, used for the one replacement round.
    """
    n_coverage = (sample_size * 2) // 3
    queues = {topic: list(topic_members.get(topic, [])) for topic in topic_order}
    picked_set: set[int] = set()
    coverage: list[int] = []
    while len(coverage) < n_coverage:
        progressed = False
        for topic in topic_order:
            if len(coverage) >= n_coverage:
                break
            queue = queues[topic]
            while queue and queue[0] in picked_set:
                queue.pop(0)
            if queue:
                vid = queue.pop(0)
                coverage.append(vid)
                picked_set.add(vid)
                progressed = True
        if not progressed:
            break

    def blind_key(video_id: int) -> tuple[int, float, int]:
        confidence = pool[video_id]["best_confidence"]
        if confidence is None:
            return (1, 0.0, video_id)
        return (0, confidence, video_id)

    blind_ordered = sorted(
        (vid for vid in pool if vid not in picked_set), key=blind_key
    )
    remainder = max(sample_size - len(coverage), 0)
    blindspot = blind_ordered[:remainder]
    blindspot_set = set(blindspot)
    remaining = [vid for vid in blind_ordered if vid not in blindspot_set]
    return coverage, blindspot, remaining


def select_refinement_sample(
    connection: sqlite3.Connection,
    *,
    channel_id: int,
    discovery_run_id: int,
    sample_size: int = 15,
) -> tuple[list[int], list[int]]:
    """Public picker entry point: returns ``(picked_video_ids, remaining_pool)``
    (internal ``videos.id``). See ``_build_pool`` / ``_pick`` for the rules."""
    pool, topic_members, topic_order = _build_pool(
        connection, channel_id, discovery_run_id
    )
    coverage, blindspot, remaining = _pick(
        pool, topic_members, topic_order, sample_size
    )
    return coverage + blindspot, remaining


def _resolve_discovery_run_id(
    connection: sqlite3.Connection,
    *,
    channel_id: int,
    discovery_run_id: int | None,
    project_name: str,
) -> int:
    """Return ``discovery_run_id`` if it belongs to ``channel_id``; otherwise the
    channel's latest discovery run. Raises ``ValueError`` if neither resolves."""
    if discovery_run_id is None:
        row = connection.execute(
            "SELECT id FROM discovery_runs WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
            (channel_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"no discovery run for project {project_name!r}; run `discover` first"
            )
        return int(row["id"])
    row = connection.execute(
        "SELECT id FROM discovery_runs WHERE id = ? AND channel_id = ?",
        (discovery_run_id, channel_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"discovery run {discovery_run_id} not found for this channel")
    return int(row["id"])


def describe_refinement_sample(
    db_path: str | Path,
    *,
    project_name: str,
    discovery_run_id: int | None = None,
    sample_size: int = 15,
) -> dict[str, Any]:
    """Read-only Refine-UI sample description (no side effects).

    Runs the slice-B3 picker against ``discovery_run_id`` (or the channel's
    latest run) and returns ``{discovery_run_id, pool_size, episodes}`` where each
    episode is ``{video_id, youtube_video_id, title, topic, confidence,
    transcript_status, slot_kind}`` (``slot_kind`` is ``'coverage'`` or
    ``'blind_spot'``; ``topic``/``confidence`` are the episode's highest-confidence
    assignment in that run, or ``None`` for the unassigned blind-spot bucket).
    """
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        _project_id, channel_id = _resolve_channel(connection, project_name)
        run_id = _resolve_discovery_run_id(
            connection,
            channel_id=channel_id,
            discovery_run_id=discovery_run_id,
            project_name=project_name,
        )
        pool, topic_members, topic_order = _build_pool(connection, channel_id, run_id)
        coverage, blindspot, _remaining = _pick(
            pool, topic_members, topic_order, sample_size
        )
        coverage_set = set(coverage)
        picked = coverage + blindspot
        if not picked:
            return {"discovery_run_id": run_id, "pool_size": len(pool), "episodes": []}
        placeholders = ",".join("?" * len(picked))
        meta = {
            row["id"]: (row["youtube_video_id"], row["title"], row["transcript_status"])
            for row in connection.execute(
                f"""
                SELECT v.id AS id, v.youtube_video_id AS youtube_video_id,
                       v.title AS title, vtr.transcript_status AS transcript_status
                FROM videos v
                LEFT JOIN video_transcripts vtr ON vtr.video_id = v.id
                WHERE v.id IN ({placeholders})
                """,
                picked,
            ).fetchall()
        }
        topic_by_video: dict[int, tuple[str, float | None]] = {}
        for row in connection.execute(
            f"""
            SELECT vt.video_id AS video_id, t.name AS topic, vt.confidence AS confidence
            FROM video_topics vt
            JOIN topics t ON t.id = vt.topic_id
            WHERE vt.discovery_run_id = ? AND vt.video_id IN ({placeholders})
            ORDER BY vt.confidence DESC, t.name
            """,
            [run_id, *picked],
        ).fetchall():
            topic_by_video.setdefault(row["video_id"], (row["topic"], row["confidence"]))
        episodes: list[dict[str, Any]] = []
        for video_id in picked:
            youtube_video_id, title, transcript_status = meta.get(
                video_id, (None, None, None)
            )
            topic, confidence = topic_by_video.get(video_id, (None, None))
            episodes.append(
                {
                    "video_id": video_id,
                    "youtube_video_id": youtube_video_id,
                    "title": title,
                    "topic": topic,
                    "confidence": confidence,
                    "transcript_status": transcript_status,
                    "slot_kind": "coverage" if video_id in coverage_set else "blind_spot",
                }
            )
    return {"discovery_run_id": run_id, "pool_size": len(pool), "episodes": episodes}


# --------------------------------------------------------------------------
# DB read helpers (snapshots taken inside the persistence connection)
# --------------------------------------------------------------------------


def _resolve_channel(
    connection: sqlite3.Connection, project_name: str
) -> tuple[int, int]:
    project_row = connection.execute(
        "SELECT id FROM projects WHERE name = ?", (project_name,)
    ).fetchone()
    if project_row is None:
        raise ValueError(f"project not found: {project_name}")
    project_id = int(project_row["id"])
    channel_row = connection.execute(
        "SELECT id FROM channels WHERE project_id = ? AND is_primary = 1 ORDER BY id LIMIT 1",
        (project_id,),
    ).fetchone()
    if channel_row is None:
        raise ValueError(f"no primary channel for project: {project_name}")
    return project_id, int(channel_row["id"])


def _transcript_info(
    connection: sqlite3.Connection, youtube_ids: Sequence[str]
) -> dict[str, tuple[str | None, str | None]]:
    if not youtube_ids:
        return {}
    placeholders = ",".join("?" * len(youtube_ids))
    rows = connection.execute(
        f"""
        SELECT v.youtube_video_id AS yt, vtr.transcript_status AS st,
               vtr.transcript_text AS txt
        FROM videos v
        LEFT JOIN video_transcripts vtr ON vtr.video_id = v.id
        WHERE v.youtube_video_id IN ({placeholders})
        """,
        list(youtube_ids),
    ).fetchall()
    return {row["yt"]: (row["st"], row["txt"]) for row in rows}


def _taxonomy_snapshot(
    connection: sqlite3.Connection, project_id: int
) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for topic in connection.execute(
        "SELECT id, name FROM topics WHERE project_id = ? ORDER BY name", (project_id,)
    ).fetchall():
        subtopics = connection.execute(
            "SELECT name FROM subtopics WHERE topic_id = ? ORDER BY name", (topic["id"],)
        ).fetchall()
        snapshot.append(
            {"topic": topic["name"], "subtopics": [s["name"] for s in subtopics]}
        )
    return snapshot


def _current_assignments(
    connection: sqlite3.Connection, video_id: int
) -> list[dict[str, Any]]:
    subtopic_by_topic_id = {
        row["topic_id"]: row["subtopic"]
        for row in connection.execute(
            """
            SELECT s.topic_id AS topic_id, s.name AS subtopic
            FROM video_subtopics vs
            JOIN subtopics s ON s.id = vs.subtopic_id
            WHERE vs.video_id = ?
            """,
            (video_id,),
        ).fetchall()
    }
    assignments: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT t.id AS topic_id, t.name AS topic, vt.confidence AS confidence,
               vt.reason AS reason
        FROM video_topics vt
        JOIN topics t ON t.id = vt.topic_id
        WHERE vt.video_id = ?
        ORDER BY vt.confidence DESC, t.name
        """,
        (video_id,),
    ).fetchall():
        assignments.append(
            {
                "topic": row["topic"],
                "subtopic": subtopic_by_topic_id.get(row["topic_id"]),
                "confidence": row["confidence"],
                "reason": row["reason"],
            }
        )
    return assignments


def _estimate_cost_usd(
    model: str, episodes: Sequence[RefinementEpisodeContext]
) -> float:
    if not episodes:
        return 0.0
    tokens_in = sum(
        len(ep.transcript_text) // _CHARS_PER_TOKEN + _PROMPT_SCAFFOLD_TOKENS
        for ep in episodes
    )
    tokens_out = _OUTPUT_TOKENS_PER_EPISODE * len(episodes)
    cost = estimate_cost(model, tokens_in, tokens_out, is_batch=len(episodes) >= 10)
    return cost if cost is not None else 0.0


def estimate_refinement_cost_usd(
    db_path: str | Path,
    *,
    youtube_video_ids: Sequence[str],
    model: str = DEFAULT_REAL_MODEL,
) -> float:
    """Cost estimate (USD) for a ``refine --real`` run over the given episodes —
    Σ per-transcript token estimate × the model's input price + a flat output
    allowance per episode (the same arithmetic ``run_refinement``'s confirm
    prompt uses). Read-only. Episodes whose stored transcript is not
    ``'available'`` (or absent) contribute nothing — the picker drops them
    before the LLM call. Unknown ``youtube_video_ids`` are ignored."""
    if not youtube_video_ids:
        return 0.0
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        info = _transcript_info(connection, list(youtube_video_ids))
    contexts: list[RefinementEpisodeContext] = []
    for youtube_id in youtube_video_ids:
        status, text = info.get(youtube_id, (None, None))
        if status == "available":
            contexts.append(
                RefinementEpisodeContext(
                    video_id=0,
                    youtube_video_id=youtube_id,
                    transcript_text=text or "",
                    current_assignments=[],
                )
            )
    return _estimate_cost_usd(model, contexts)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def run_refinement(
    db_path: str | Path,
    *,
    project_name: str,
    discovery_run_id: int | None = None,
    llm: LLMCallable = stub_refinement_llm,
    sample: Sequence[str] | None = None,
    transcript_fetcher: Callable[[str], Any] | None = None,
    sample_size: int = 15,
    model: str = STUB_MODEL,
    confirm: Callable[[dict[str, Any]], bool] | None = None,
    out: Callable[[str], Any] = print,
) -> RefinementRun:
    """Run sample-based transcript refinement against the primary channel of
    ``project_name``. See the module docstring for the staged flow.

    ``sample`` (YouTube video ids) bypasses the picker. ``confirm`` — used by
    ``refine --real`` — is called once after the sample is finalized with
    ``{refinement_run_id, n_episodes, estimated_cost_usd, video_ids}``; if it
    returns falsy, the run is left ``pending`` and no LLM call is made.
    """
    # --- stage 1: resolve, pool, pick (read-only) ---
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        project_id, channel_id = _resolve_channel(connection, project_name)

        if discovery_run_id is None:
            row = connection.execute(
                "SELECT id FROM discovery_runs WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
                (channel_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"no discovery run for project {project_name!r}; run `discover` first"
                )
            discovery_run_id = int(row["id"])
        else:
            row = connection.execute(
                "SELECT id FROM discovery_runs WHERE id = ? AND channel_id = ?",
                (discovery_run_id, channel_id),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"discovery run {discovery_run_id} not found for this channel"
                )

        youtube_id_by_video_id = {
            r["id"]: r["youtube_video_id"]
            for r in connection.execute(
                "SELECT id, youtube_video_id FROM videos WHERE channel_id = ?",
                (channel_id,),
            ).fetchall()
        }
        video_id_by_youtube_id = {v: k for k, v in youtube_id_by_video_id.items()}

        if sample is not None:
            picked: list[int] = []
            for youtube_id in sample:
                if youtube_id not in video_id_by_youtube_id:
                    raise ValueError(
                        f"not a video of the primary channel: {youtube_id}"
                    )
                video_id = video_id_by_youtube_id[youtube_id]
                if video_id not in picked:
                    picked.append(video_id)
            if not picked:
                raise ValueError("sample was empty")
            remaining_pool: list[int] = []
        else:
            pool, topic_members, topic_order = _build_pool(
                connection, channel_id, discovery_run_id
            )
            coverage, blindspot, remaining_pool = _pick(
                pool, topic_members, topic_order, sample_size
            )
            picked = coverage + blindspot
            if not picked:
                raise ValueError(
                    "no eligible episodes for refinement (all Shorts, no "
                    "fetchable transcripts, or the discovery run has no "
                    "assignments)"
                )
            if len(pool) < sample_size:
                out(
                    f"warning: only {len(pool)} eligible episode(s) for the "
                    f"sample (requested {sample_size}); proceeding short"
                )

        picked_youtube_ids = [youtube_id_by_video_id[v] for v in picked]
        remaining_youtube_ids = [youtube_id_by_video_id[v] for v in remaining_pool]
        info = _transcript_info(
            connection, picked_youtube_ids + remaining_youtube_ids
        )
        status_by_youtube_id: dict[str, str | None] = {
            yt: (info.get(yt, (None, None))[0]) for yt in picked_youtube_ids
        }
        status_by_youtube_id.update(
            {yt: (info.get(yt, (None, None))[0]) for yt in remaining_youtube_ids}
        )

    # --- stage 2: fetch transcripts (no held connection) ---
    def ensure_transcript(youtube_id: str) -> str | None:
        if status_by_youtube_id.get(youtube_id) == "available":
            return "available"
        record = fetch_video_transcript(
            youtube_id, transcript_fetcher=transcript_fetcher
        )
        upsert_video_transcript(
            db_path, youtube_video_id=youtube_id, transcript=record
        )
        status_by_youtube_id[youtube_id] = record.status
        return record.status

    survivors: list[tuple[int, str]] = []
    dropped = 0
    for video_id, youtube_id in zip(picked, picked_youtube_ids):
        if ensure_transcript(youtube_id) == "available":
            survivors.append((video_id, youtube_id))
        else:
            dropped += 1
    if dropped and remaining_pool:
        needed = dropped
        for video_id, youtube_id in zip(remaining_pool, remaining_youtube_ids):
            if needed <= 0:
                break
            if ensure_transcript(youtube_id) == "available":
                survivors.append((video_id, youtube_id))
                needed -= 1
    if not survivors:
        raise ValueError(
            "no usable transcripts for the refinement sample (every fetch "
            "returned a non-available status)"
        )

    # --- stage 3: persist run, run the batch, persist results ---
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row

        text_info = _transcript_info(connection, [yt for _, yt in survivors])
        taxonomy = _taxonomy_snapshot(connection, project_id)
        contexts = [
            RefinementEpisodeContext(
                video_id=video_id,
                youtube_video_id=youtube_id,
                transcript_text=(text_info.get(youtube_id, (None, None))[1] or ""),
                current_assignments=_current_assignments(connection, video_id),
            )
            for video_id, youtube_id in survivors
        ]

        run_id = db.create_refinement_run(
            connection,
            channel_id=channel_id,
            discovery_run_id=discovery_run_id,
            model=model,
            prompt_version=REFINEMENT_PROMPT_VERSION,
            n_sample=len(contexts),
        )
        db.add_refinement_episodes(
            connection,
            run_id,
            [(video_id, status_by_youtube_id.get(youtube_id)) for video_id, youtube_id in survivors],
        )
        connection.commit()

        if confirm is not None:
            estimated = _estimate_cost_usd(model, contexts)
            if not confirm(
                {
                    "refinement_run_id": run_id,
                    "n_episodes": len(contexts),
                    "estimated_cost_usd": estimated,
                    "video_ids": [yt for _, yt in survivors],
                }
            ):
                out("refinement aborted before the LLM call (run left pending)")
                return RefinementRun(
                    run_id=run_id,
                    discovery_run_id=discovery_run_id,
                    sampled_youtube_ids=[yt for _, yt in survivors],
                    proposals=[],
                    reassignments=[],
                    status="pending",
                )

        db.set_refinement_run_status(connection, run_id, "running")
        connection.commit()

        try:
            payloads = _invoke_llm(llm, contexts, taxonomy, run_id)
            if len(payloads) != len(contexts):
                raise ValueError(
                    f"refinement LLM returned {len(payloads)} payload(s) for "
                    f"{len(contexts)} episode(s)"
                )
            proposals: list[dict[str, Any]] = []
            reassignments: list[dict[str, Any]] = []
            for ctx, payload in zip(contexts, payloads):
                result = db.write_refine_assignments(
                    connection,
                    channel_id=channel_id,
                    refinement_run_id=run_id,
                    video_id=ctx.video_id,
                    assignments=[
                        {
                            "topic_name": a["topic"],
                            "subtopic_name": a.get("subtopic"),
                            "confidence": a.get("confidence"),
                            "reason": a.get("reason"),
                        }
                        for a in payload.assignments
                    ],
                )
                reassignments.append(result)
                for proposal in payload.new_topic_proposals:
                    proposals.append(
                        {
                            "kind": "topic",
                            "name": proposal["name"],
                            "parent_topic_name": None,
                            "evidence": proposal.get("evidence"),
                            "source_video_id": ctx.video_id,
                        }
                    )
                for proposal in payload.new_subtopic_proposals:
                    proposals.append(
                        {
                            "kind": "subtopic",
                            "name": proposal["name"],
                            "parent_topic_name": proposal.get("parent_topic"),
                            "evidence": proposal.get("evidence"),
                            "source_video_id": ctx.video_id,
                        }
                    )
            db.insert_taxonomy_proposals(connection, run_id, proposals)
            db.set_refinement_run_status(connection, run_id, "success")
            connection.commit()
        except Exception:
            connection.rollback()
            # The run + episodes rows were committed before the batch, so the
            # rollback only undoes any partial proposal / refine-assignment
            # writes; flip the surviving run row to 'error' for the audit log.
            connection.execute(
                "UPDATE refinement_runs SET status = 'error' WHERE id = ?", (run_id,)
            )
            connection.commit()
            raise

        return RefinementRun(
            run_id=run_id,
            discovery_run_id=discovery_run_id,
            sampled_youtube_ids=[yt for _, yt in survivors],
            proposals=proposals,
            reassignments=reassignments,
            status="success",
        )
