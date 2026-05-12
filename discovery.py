from __future__ import annotations

import inspect
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from yt_channel_analyzer.db import connect, ensure_schema
from yt_channel_analyzer.extractor.errors import ExtractorError
from yt_channel_analyzer.extractor.registry import (
    Prompt,
    get_prompt,
    register_prompt,
)


# Duration (seconds) at or below which a video is treated as a YouTube Short
# and excluded from discovery when the shorts filter is active. Module-level so
# it's grep-able and overridable in tests. Videos with NULL ``duration_seconds``
# are treated as long (never excluded) — fail-safe to legacy behavior.
SHORTS_CUTOFF_SECONDS = 180


@dataclass(frozen=True)
class Chapter:
    start_seconds: int
    title: str


@dataclass(frozen=True)
class DiscoveryVideo:
    youtube_video_id: str
    title: str
    description: str | None
    published_at: str | None
    chapters: tuple[Chapter, ...] = ()


_CHAPTER_LINE = re.compile(r"^\s*\[?((?:\d+:)?\d{1,2}:\d{2})\]?\s+[-–—:|.)\]]?\s*(.+?)\s*$")


def _timestamp_to_seconds(ts: str) -> int | None:
    parts = ts.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        m, s = nums
        if not (0 <= s < 60 and m >= 0):
            return None
        return m * 60 + s
    if len(nums) == 3:
        h, m, s = nums
        if not (0 <= s < 60 and 0 <= m < 60 and h >= 0):
            return None
        return h * 3600 + m * 60 + s
    return None


_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Sponsor reads
    re.compile(
        r"\b(?:sponsored by|brought to you by|today'?s sponsor|our sponsors?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bsponsors?:", re.IGNORECASE),
    re.compile(
        r"\b(use (?:promo |discount )?code|promo code|discount code|coupon code|\d+\s?%\s?off)\b",
        re.IGNORECASE,
    ),
    # Subscribe / like / bell CTAs
    re.compile(
        r"\b(subscribe to (?:the|my|our|this)|hit (?:the )?bell|smash that like|"
        r"don'?t forget to (?:like|subscribe)|leave (?:a|us a) (?:like|comment|review))\b",
        re.IGNORECASE,
    ),
    # Follow-on-social CTAs
    re.compile(
        r"\b(follow (?:me|us|the show|the host) on|find (?:me|us|the show) on|"
        r"connect with (?:me|us) on)\b",
        re.IGNORECASE,
    ),
    # Lines that start with a social-platform label, e.g. "Twitter: @doac"
    re.compile(
        r"^\s*(?:instagram|twitter|tiktok|facebook|linkedin|threads|youtube|"
        r"patreon|discord|substack|x|website|newsletter)\s*[:\-–—]",
        re.IGNORECASE,
    ),
    # Bare URLs to social / podcast platforms
    re.compile(
        r"https?://(?:www\.)?(?:instagram\.com|twitter\.com|x\.com|tiktok\.com|"
        r"facebook\.com|linkedin\.com|threads\.net|patreon\.com|discord\.gg|"
        r"discord\.com|youtube\.com|youtu\.be|open\.spotify\.com|spotify\.com|"
        r"apple\.co|podcasts\.apple\.com)\b",
        re.IGNORECASE,
    ),
    # "Listen on …" / "Available on …" CTAs
    re.compile(
        r"\b(listen on (?:apple|spotify|amazon)|available on (?:apple|spotify|amazon))\b",
        re.IGNORECASE,
    ),
)


def _is_boilerplate_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in _BOILERPLATE_PATTERNS)


def strip_description_boilerplate(description: str | None) -> str | None:
    """Drop sponsor-read and social-CTA lines from a YouTube description.

    Chapter-marker lines (per `parse_chapters_from_description`'s line shape)
    are always preserved so the LLM still sees the episode's structure.
    Returns `None` for `None` input; otherwise returns a string that may be
    empty if the entire description was boilerplate.
    """
    if description is None:
        return None
    if not description:
        return description
    cleaned: list[str] = []
    for line in description.splitlines():
        if _CHAPTER_LINE.match(line):
            cleaned.append(line)
            continue
        if _is_boilerplate_line(line):
            continue
        cleaned.append(line)
    collapsed: list[str] = []
    blank = False
    for line in cleaned:
        if not line.strip():
            if blank:
                continue
            blank = True
            collapsed.append("")
        else:
            blank = False
            collapsed.append(line)
    while collapsed and not collapsed[0].strip():
        collapsed.pop(0)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return "\n".join(collapsed)


def parse_chapters_from_description(description: str | None) -> tuple[Chapter, ...]:
    """Extract chapter markers from a YouTube video description.

    YouTube treats a description as containing chapters when at least three
    timestamps are present, the first is `0:00`, and they are monotonically
    increasing. We follow the same rule conservatively — if any check fails,
    return an empty tuple so downstream code never sees half-parsed chapters.
    """
    if not description:
        return ()
    candidates: list[Chapter] = []
    for line in description.splitlines():
        match = _CHAPTER_LINE.match(line)
        if not match:
            continue
        ts, title = match.group(1), match.group(2).strip()
        seconds = _timestamp_to_seconds(ts)
        if seconds is None or not title:
            continue
        candidates.append(Chapter(start_seconds=seconds, title=title))
    if len(candidates) < 3:
        return ()
    if candidates[0].start_seconds != 0:
        return ()
    for prev, curr in zip(candidates, candidates[1:]):
        if curr.start_seconds <= prev.start_seconds:
            return ()
    return tuple(candidates)


@dataclass(frozen=True)
class DiscoverySubtopic:
    name: str
    parent_topic: str


@dataclass(frozen=True)
class DiscoveryAssignment:
    youtube_video_id: str
    topic_name: str
    confidence: float
    reason: str
    subtopic_name: str | None = None


@dataclass(frozen=True)
class DiscoveryPayload:
    topics: list[str]
    assignments: list[DiscoveryAssignment]
    subtopics: list[DiscoverySubtopic] = field(default_factory=list)


LLMCallable = Callable[[Sequence[DiscoveryVideo]], DiscoveryPayload]


STUB_TOPIC_NAME = "General"
STUB_MODEL = "stub"
STUB_PROMPT_VERSION = "stub-v0"


DISCOVERY_PROMPT_NAME = "discovery.topics"
DISCOVERY_PROMPT_VERSION = "discovery-v5"


_DISCOVERY_SYSTEM = (
    "You are an editorial assistant grouping podcast episodes into broad "
    "topics from titles, descriptions, and chapter markers.\n"
    "\n"
    "Reply with a single JSON object of the form:\n"
    '  {"topics": ["Topic A", "Topic B"], '
    '"subtopics": [{"name": "Sub A1", "parent_topic": "Topic A"}], '
    '"assignments": [\n'
    '    {"youtube_video_id": "<id1>", "topic": "Topic A", '
    '"subtopic": "Sub A1", "confidence": 0.85, '
    '"reason": "matched chapter title \'Sub A1\'"},\n'
    '    {"youtube_video_id": "<id1>", "topic": "Topic B", '
    '"confidence": 0.6, '
    '"reason": "second half discusses Topic B"}\n'
    "  ]}\n"
    "\n"
    "Rules:\n"
    "- Every supplied episode must appear in `assignments` at least once.\n"
    "- An episode may have multiple `assignments` entries with different "
    "`topic` values when it genuinely covers each topic. Only do this "
    "when the episode meaningfully covers each — secondary topics should "
    "be the exception, not the default. Do not over-tag: most episodes "
    "should have a single assignment.\n"
    "- Every `topic` in `assignments` must also appear in `topics`.\n"
    "- Choose 3-12 broad topics; reuse one topic across many episodes.\n"
    "- Propose 2-6 subtopics per topic; each subtopic's `parent_topic` "
    "must appear in `topics`.\n"
    "- For each assignment, pick a `subtopic` whose `parent_topic` matches "
    "the assignment's `topic`. Omit `subtopic` if no subtopic fits.\n"
    "- For each assignment, supply `confidence` between 0.0 and 1.0 "
    "reflecting how strongly the episode fits the topic, and a short "
    "`reason` string (e.g. \"title contains 'sleep'\", \"matched chapter "
    "title 'Gut Microbiome'\") explaining the placement.\n"
    "- If a \"taxonomy already curated\" block is supplied, reuse those exact "
    "topic and subtopic names for episodes that fit them; only introduce a "
    "new name when an episode covers a genuinely new theme not in the list.\n"
    "- Output JSON only — no prose, no markdown fences."
)


_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["topics", "assignments"],
    "properties": {
        "topics": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "subtopics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "parent_topic"],
                "properties": {
                    "name": {"type": "string"},
                    "parent_topic": {"type": "string"},
                },
            },
        },
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "youtube_video_id",
                    "topic",
                    "confidence",
                    "reason",
                ],
                "properties": {
                    "youtube_video_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "subtopic": {"type": "string"},
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


def _render_discovery_prompt(context: dict) -> str:
    videos = context.get("videos", [])
    lines: list[str] = [
        f"Episodes ({len(videos)} total). Identify broad topics and assign each.",
        "",
    ]
    taxonomy = context.get("taxonomy") or []
    if taxonomy:
        lines.append(
            "Taxonomy already curated for this channel — reuse these exact "
            "names where an episode fits one; you may also propose new "
            "topics/subtopics for genuinely new themes:"
        )
        for entry in taxonomy:
            lines.append(f"- {entry['topic']}")
            for sub in entry.get("subtopics", []) or []:
                lines.append(f"  - {sub}")
        lines.append("")
    for idx, video in enumerate(videos, start=1):
        lines.append(f"--- Episode {idx} ---")
        lines.append(f"id: {video['youtube_video_id']}")
        lines.append(f"title: {video['title']}")
        description = video.get("description")
        if description:
            lines.append(f"description: {description}")
        chapters = video.get("chapters") or []
        if chapters:
            lines.append("chapters:")
            for chapter in chapters:
                lines.append(f"  - {chapter}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def register_discovery_prompt() -> Prompt:
    """Register the discovery prompt; idempotent across repeat calls."""
    try:
        return get_prompt(DISCOVERY_PROMPT_NAME, DISCOVERY_PROMPT_VERSION)
    except ExtractorError:
        return register_prompt(
            name=DISCOVERY_PROMPT_NAME,
            version=DISCOVERY_PROMPT_VERSION,
            render=_render_discovery_prompt,
            schema=_DISCOVERY_SCHEMA,
            system=_DISCOVERY_SYSTEM,
        )


def _videos_to_context(videos: Sequence[DiscoveryVideo]) -> dict:
    return {
        "videos": [
            {
                "youtube_video_id": v.youtube_video_id,
                "title": v.title,
                "description": v.description,
                "chapters": [
                    f"{c.start_seconds}s {c.title}" for c in v.chapters
                ],
            }
            for v in videos
        ]
    }


def _payload_from_response(data: dict) -> "DiscoveryPayload":
    topics = list(data["topics"])
    subtopics = [
        DiscoverySubtopic(name=item["name"], parent_topic=item["parent_topic"])
        for item in data.get("subtopics", []) or []
    ]
    assignments = [
        DiscoveryAssignment(
            youtube_video_id=item["youtube_video_id"],
            topic_name=item["topic"],
            confidence=float(item["confidence"]),
            reason=item["reason"],
            subtopic_name=item.get("subtopic"),
        )
        for item in data["assignments"]
    ]
    return DiscoveryPayload(
        topics=topics, assignments=assignments, subtopics=subtopics
    )


def _call_llm_with_optional_correlation(
    llm: "LLMCallable",
    videos: Sequence[DiscoveryVideo],
    correlation_id: int,
    taxonomy: list[dict] | None = None,
) -> "DiscoveryPayload":
    """Pass ``correlation_id`` / ``taxonomy`` only when the callable accepts them.

    Test fixtures throughout the suite use bare ``def f(_videos): ...`` lambdas;
    the real adapter (`discovery_llm_via_extractor`) and ``stub_llm`` accept the
    kwargs. Detecting per-call keeps both shapes working without forcing every
    fixture to carry a no-op ``**kwargs``. ``taxonomy`` is the channel's
    current curated topic/subtopic names so a re-run reuses them (issue B4).
    """
    try:
        sig = inspect.signature(llm)
    except (TypeError, ValueError):
        return llm(videos)
    has_var_kw = any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in sig.parameters.values()
    )
    param_names = {param.name for param in sig.parameters.values()}
    kwargs: dict[str, Any] = {}
    if has_var_kw or "correlation_id" in param_names:
        kwargs["correlation_id"] = correlation_id
    if has_var_kw or "taxonomy" in param_names:
        kwargs["taxonomy"] = taxonomy
    return llm(videos, **kwargs)


def discovery_llm_via_extractor(extractor: Any) -> "LLMCallable":
    """Adapt an Extractor into the LLMCallable signature `run_discovery` expects.

    A single batched call: all videos are rendered into one prompt and the
    response is parsed into a DiscoveryPayload. Schema validation and one
    automatic retry on parse failure are owned by the Extractor itself.
    """
    register_discovery_prompt()

    def call(
        videos: Sequence[DiscoveryVideo],
        *,
        correlation_id: int | None = None,
        taxonomy: list[dict] | None = None,
    ) -> DiscoveryPayload:
        context = _videos_to_context(videos)
        if taxonomy:
            context["taxonomy"] = taxonomy
        result = extractor.run_one(
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            context,
            correlation_id=correlation_id,
        )
        return _payload_from_response(result.data)

    return call


def make_real_llm_callable(
    connection: sqlite3.Connection,
    *,
    model: str | None = None,
) -> "LLMCallable":
    """Construct a real-LLM `LLMCallable` (Anthropic).

    Gated behind `RALPH_ALLOW_REAL_LLM=1`; raises otherwise so the verify gate
    cannot accidentally spend tokens.
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
    return discovery_llm_via_extractor(extractor)


STUB_SUBTOPIC_NAME = "General sub"
STUB_SECONDARY_TOPIC_NAME = "Cross-cutting"


def stub_llm(
    videos: Sequence[DiscoveryVideo],
    *,
    correlation_id: int | None = None,
    taxonomy: list[dict] | None = None,
) -> DiscoveryPayload:
    """Hardcoded LLM stub: every video gets a primary-topic assignment, and
    the first video carries a second assignment under a secondary topic so
    the multi-topic display path is exercisable without spending tokens.

    Accepts ``correlation_id`` to match the post-2026-05-10 ``LLMCallable``
    convention (``run_discovery`` threads ``discovery_run_id`` through so the
    real-LLM adapter can stamp it on ``llm_calls`` for cost rollups), and
    ``taxonomy`` (the channel's curated topic/subtopic names, issue B4). The
    stub ignores both — it always emits its fixed ``General`` / ``Cross-cutting``
    shape, so re-runs are deterministic regardless of prior curation.
    """
    primary_assignments = [
        DiscoveryAssignment(
            youtube_video_id=video.youtube_video_id,
            topic_name=STUB_TOPIC_NAME,
            confidence=1.0,
            reason="stub assignment",
            subtopic_name=STUB_SUBTOPIC_NAME,
        )
        for video in videos
    ]
    secondary_assignments = (
        [
            DiscoveryAssignment(
                youtube_video_id=videos[0].youtube_video_id,
                topic_name=STUB_SECONDARY_TOPIC_NAME,
                confidence=0.6,
                reason="stub multi-topic assignment",
                subtopic_name=None,
            )
        ]
        if videos
        else []
    )
    return DiscoveryPayload(
        topics=[STUB_TOPIC_NAME, STUB_SECONDARY_TOPIC_NAME],
        subtopics=[
            DiscoverySubtopic(
                name=STUB_SUBTOPIC_NAME, parent_topic=STUB_TOPIC_NAME
            )
        ],
        assignments=primary_assignments + secondary_assignments,
    )


def _apply_renames_to_payload(
    connection: sqlite3.Connection,
    project_id: int,
    payload: DiscoveryPayload,
) -> DiscoveryPayload:
    """Rewrite topic names in ``payload`` through the project's rename log.

    Reads `topic_renames` rows for ``project_id`` (oldest first), builds a
    fixed-point map collapsing multi-hop chains (A→B then B→C resolves
    incoming "A" straight to "C"), then returns a new ``DiscoveryPayload``
    with rewritten ``topics`` (deduped after rewrite, preserving first-seen
    order), ``subtopics[i].parent_topic``, and ``assignments[i].topic_name``.
    Pure function: never mutates the database.
    """
    rows = connection.execute(
        """
        SELECT old_name, new_name
        FROM topic_renames
        WHERE project_id = ?
        ORDER BY id
        """,
        (project_id,),
    ).fetchall()
    direct: dict[str, str] = {}
    for row in rows:
        old_name = row["old_name"] if isinstance(row, sqlite3.Row) else row[0]
        new_name = row["new_name"] if isinstance(row, sqlite3.Row) else row[1]
        direct[old_name] = new_name

    def resolve(name: str) -> str:
        seen: set[str] = set()
        current = name
        while current in direct and current not in seen:
            seen.add(current)
            nxt = direct[current]
            if nxt == current:
                break
            current = nxt
        return current

    new_topics: list[str] = []
    seen_topics: set[str] = set()
    for topic in payload.topics:
        rewritten = resolve(topic)
        if rewritten in seen_topics:
            continue
        seen_topics.add(rewritten)
        new_topics.append(rewritten)

    new_subtopics = [
        DiscoverySubtopic(name=sub.name, parent_topic=resolve(sub.parent_topic))
        for sub in payload.subtopics
    ]
    new_assignments = [
        DiscoveryAssignment(
            youtube_video_id=a.youtube_video_id,
            topic_name=resolve(a.topic_name),
            confidence=a.confidence,
            reason=a.reason,
            subtopic_name=a.subtopic_name,
        )
        for a in payload.assignments
    ]
    return DiscoveryPayload(
        topics=new_topics,
        assignments=new_assignments,
        subtopics=new_subtopics,
    )


def _autoheal_dangling_subtopic_refs(payload: DiscoveryPayload) -> DiscoveryPayload:
    """Synthesize ``DiscoverySubtopic`` rows for assignment ``subtopic_name``s
    the LLM referenced but forgot to declare in ``payload.subtopics``.

    Surfaced 2026-05-10: Haiku 4.5 returns ``stop_reason=end_turn`` with a
    well-formed payload that nonetheless assigns episodes to subtopic names
    it never enumerated under a topic. Strict validation in the persistence
    loop raises ``ValueError`` and the caller pays ~$0.05 to re-roll. The
    fix: when an assignment references ``(topic_name=T, subtopic_name=S)``
    and ``T`` *is* in ``payload.topics`` but ``S`` is not declared under
    ``T`` in ``payload.subtopics``, append a ``DiscoverySubtopic(name=S,
    parent_topic=T)``. Dangling *topic* refs (T itself missing) still raise
    downstream — those need fresh data, not synthesis.
    """
    declared_topics = set(payload.topics)
    declared_pairs: set[tuple[str, str]] = {
        (sub.parent_topic, sub.name) for sub in payload.subtopics
    }
    healed: list[DiscoverySubtopic] = list(payload.subtopics)
    seen_added: set[tuple[str, str]] = set()
    for assignment in payload.assignments:
        sub_name = assignment.subtopic_name
        if not sub_name:
            continue
        topic_name = assignment.topic_name
        if topic_name not in declared_topics:
            continue
        pair = (topic_name, sub_name)
        if pair in declared_pairs or pair in seen_added:
            continue
        healed.append(DiscoverySubtopic(name=sub_name, parent_topic=topic_name))
        seen_added.add(pair)

    if not seen_added:
        return payload
    return DiscoveryPayload(
        topics=payload.topics,
        assignments=payload.assignments,
        subtopics=healed,
    )


def _suppress_wrong_assignments_in_run(
    connection: sqlite3.Connection,
    channel_id: int,
    run_id: int,
) -> None:
    """Delete any `video_topics` / `video_subtopics` rows the user previously
    marked wrong, restricted to the current run's inserts.

    `wrong_assignments.topic_id` is a stable id (topic rows survive renames),
    so name-rewriting via the rename map is irrelevant here — the curated
    topic id is what we suppress.
    """
    connection.execute(
        """
        DELETE FROM video_topics
        WHERE discovery_run_id = ?
          AND (video_id, topic_id) IN (
              SELECT wa.video_id, wa.topic_id
              FROM wrong_assignments wa
              JOIN videos v ON v.id = wa.video_id
              WHERE v.channel_id = ? AND wa.subtopic_id IS NULL
          )
        """,
        (run_id, channel_id),
    )
    connection.execute(
        """
        DELETE FROM video_subtopics
        WHERE discovery_run_id = ?
          AND (video_id, subtopic_id) IN (
              SELECT wa.video_id, wa.subtopic_id
              FROM wrong_assignments wa
              JOIN videos v ON v.id = wa.video_id
              WHERE v.channel_id = ? AND wa.subtopic_id IS NOT NULL
          )
        """,
        (run_id, channel_id),
    )


def allocate_discovery_run(
    db_path: str | Path,
    *,
    project_name: str,
    model: str,
    prompt_version: str,
) -> int:
    """Insert a ``discovery_runs`` row in status='running' and return its id.

    Used by the review-UI handler that spawns ``run_discovery`` in a
    background thread: pre-allocating up-front lets us return the run id to
    the client before the LLM call starts.
    """
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row

        project_row = connection.execute(
            "SELECT id FROM projects WHERE name = ?", (project_name,)
        ).fetchone()
        if project_row is None:
            raise ValueError(f"project not found: {project_name}")
        channel_row = connection.execute(
            """
            SELECT id FROM channels
            WHERE project_id = ? AND is_primary = 1
            ORDER BY id LIMIT 1
            """,
            (project_row["id"],),
        ).fetchone()
        if channel_row is None:
            raise ValueError(f"no primary channel for project: {project_name}")

        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO discovery_runs(channel_id, model, prompt_version, status)
            VALUES (?, ?, ?, 'running')
            """,
            (channel_row["id"], model, prompt_version),
        )
        connection.commit()
        return int(cursor.lastrowid)


def run_discovery(
    db_path: str | Path,
    *,
    project_name: str,
    llm: LLMCallable,
    model: str,
    prompt_version: str,
    run_id: int | None = None,
    exclude_shorts_override: bool | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row

        project_row = connection.execute(
            "SELECT id FROM projects WHERE name = ?", (project_name,)
        ).fetchone()
        if project_row is None:
            raise ValueError(f"project not found: {project_name}")
        project_id = project_row["id"]

        channel_row = connection.execute(
            """
            SELECT id, exclude_shorts FROM channels
            WHERE project_id = ? AND is_primary = 1
            ORDER BY id LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if channel_row is None:
            raise ValueError(f"no primary channel for project: {project_name}")
        channel_id = channel_row["id"]

        video_rows = connection.execute(
            """
            SELECT id, youtube_video_id, title, description, published_at,
                   duration_seconds
            FROM videos WHERE channel_id = ?
            ORDER BY id
            """,
            (channel_id,),
        ).fetchall()

        # Resolve the effective shorts filter for this run: a per-run CLI
        # override (True/False) wins; otherwise fall back to the channel's
        # sticky ``channels.exclude_shorts``. When active, pre-filter the
        # loaded episodes here — upstream of ``_apply_renames_to_payload`` and
        # the LLM call itself — so excluded Shorts never reach the prompt.
        # ``duration_seconds IS NULL`` is treated as long (never excluded).
        effective_exclude_shorts = (
            bool(channel_row["exclude_shorts"])
            if exclude_shorts_override is None
            else exclude_shorts_override
        )
        n_episodes_total = len(video_rows)
        shorts_cutoff_seconds: int | None = None
        n_shorts_excluded = 0
        # Curation-orphan counts: only meaningful when the filter is active —
        # left NULL on the run row when it's off. ``n_orphaned_wrong_marks`` is
        # known as soon as we know the cutoff; ``n_orphaned_renames`` needs the
        # post-filter assignment set, so it's computed after persistence below.
        n_orphaned_wrong_marks: int | None = None
        n_orphaned_renames: int | None = None
        if effective_exclude_shorts:
            shorts_cutoff_seconds = SHORTS_CUTOFF_SECONDS
            kept_rows = [
                row
                for row in video_rows
                if row["duration_seconds"] is None
                or row["duration_seconds"] > SHORTS_CUTOFF_SECONDS
            ]
            n_shorts_excluded = n_episodes_total - len(kept_rows)
            # Count wrong-marks whose target episode is now filtered out. These
            # rows are never deleted — if the user later flips exclude_shorts=0
            # and re-runs, they wake back up. Scoped to this channel.
            n_orphaned_wrong_marks = connection.execute(
                """
                SELECT COUNT(*) FROM wrong_assignments wa
                JOIN videos v ON v.id = wa.video_id
                WHERE v.channel_id = ?
                  AND v.duration_seconds IS NOT NULL
                  AND v.duration_seconds <= ?
                """,
                (channel_id, SHORTS_CUTOFF_SECONDS),
            ).fetchone()[0]
            if not kept_rows:
                message = (
                    "shorts filter (duration_seconds <= "
                    f"{SHORTS_CUTOFF_SECONDS}) would exclude all "
                    f"{n_episodes_total} episode(s) for this channel; re-run "
                    "with --include-shorts (or set channels.exclude_shorts = 0) "
                    "to include them"
                )
                if run_id is not None:
                    connection.execute(
                        """
                        UPDATE discovery_runs
                        SET status = 'error', error_message = ?
                        WHERE id = ?
                        """,
                        (message, run_id),
                    )
                    connection.commit()
                raise ValueError(message)
        else:
            kept_rows = list(video_rows)

        videos = [
            DiscoveryVideo(
                youtube_video_id=row["youtube_video_id"],
                title=row["title"],
                description=strip_description_boilerplate(row["description"]),
                published_at=row["published_at"],
                chapters=parse_chapters_from_description(row["description"]),
            )
            for row in kept_rows
        ]
        video_id_by_yt = {row["youtube_video_id"]: row["id"] for row in kept_rows}

        # Pre-allocate the discovery_runs row so we have a stable id to thread
        # as ``correlation_id`` into the LLM call. The Discover history view
        # joins ``llm_calls.cost_estimate_usd`` back to ``discovery_runs.id``
        # via that column, so without pre-allocation discovery rows would have
        # NULL cost. Inserted with status='running'; flipped to 'success' at
        # the bottom of the happy path or 'error' on the failure paths below.
        # If the caller already pre-allocated (e.g. the review-UI handler that
        # spawns this in a background thread and returns the id immediately),
        # we skip the INSERT and reuse their id.
        if run_id is None:
            pre_cursor = connection.cursor()
            pre_cursor.execute(
                """
                INSERT INTO discovery_runs(channel_id, model, prompt_version, status)
                VALUES (?, ?, ?, 'running')
                """,
                (channel_id, model, prompt_version),
            )
            run_id = pre_cursor.lastrowid
            connection.commit()

        # Stamp the shorts-filter audit fields on the run row (works for both
        # the self-allocated path above and a caller-pre-allocated ``run_id``).
        # ``shorts_cutoff_seconds`` is NULL when the filter is off.
        connection.execute(
            """
            UPDATE discovery_runs
            SET shorts_cutoff_seconds = ?, n_episodes_total = ?,
                n_shorts_excluded = ?, n_orphaned_wrong_marks = ?
            WHERE id = ?
            """,
            (
                shorts_cutoff_seconds,
                n_episodes_total,
                n_shorts_excluded,
                n_orphaned_wrong_marks,
                run_id,
            ),
        )
        connection.commit()

        # Load the channel's current curated taxonomy (topics + their
        # subtopics) so the LLM can reuse the exact names rather than minting
        # near-duplicates. ``topics.name`` already carries the renamed value
        # (``rename_topic`` updates the row), so the list is rename-resolved.
        # Empty on a first run; the prompt renderer skips the block then.
        taxonomy_rows = connection.execute(
            """
            SELECT t.id AS topic_id, t.name AS topic_name, s.name AS subtopic_name
            FROM topics t
            LEFT JOIN subtopics s ON s.topic_id = t.id
            WHERE t.project_id = ?
            ORDER BY t.name COLLATE NOCASE, s.name COLLATE NOCASE
            """,
            (project_id,),
        ).fetchall()
        taxonomy: list[dict] = []
        _tax_by_topic_id: dict[int, dict] = {}
        for row in taxonomy_rows:
            entry = _tax_by_topic_id.get(row["topic_id"])
            if entry is None:
                entry = {"topic": row["topic_name"], "subtopics": []}
                _tax_by_topic_id[row["topic_id"]] = entry
                taxonomy.append(entry)
            if row["subtopic_name"] is not None:
                entry["subtopics"].append(row["subtopic_name"])

        try:
            payload = _call_llm_with_optional_correlation(
                llm, videos, run_id, taxonomy
            )
        except Exception as exc:
            # LLM (or its retry) failed — flip the pre-allocated run to errored
            # so the failure is auditable, persist no partial topic / assignment
            # state, and re-raise for the caller. No raw_response: the LLM
            # raised before returning a payload.
            connection.execute(
                """
                UPDATE discovery_runs
                SET status = 'error', error_message = ?
                WHERE id = ?
                """,
                (str(exc), run_id),
            )
            connection.commit()
            raise

        raw_payload = payload
        try:
            payload = _apply_renames_to_payload(connection, project_id, payload)
            payload = _autoheal_dangling_subtopic_refs(payload)

            cursor = connection.cursor()

            topic_id_by_name: dict[str, int] = {}
            for topic_name in payload.topics:
                cursor.execute(
                    """
                    INSERT INTO topics(project_id, name, first_discovery_run_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(project_id, name) DO UPDATE SET name = excluded.name
                    """,
                    (project_id, topic_name, run_id),
                )
                row = cursor.execute(
                    "SELECT id FROM topics WHERE project_id = ? AND name = ?",
                    (project_id, topic_name),
                ).fetchone()
                topic_id_by_name[topic_name] = row["id"]

            subtopic_id_by_pair: dict[tuple[int, str], int] = {}
            for subtopic in payload.subtopics:
                parent_topic_id = topic_id_by_name.get(subtopic.parent_topic)
                if parent_topic_id is None:
                    raise ValueError(
                        "subtopic references topic not in payload.topics: "
                        f"{subtopic.parent_topic}"
                    )
                cursor.execute(
                    """
                    INSERT INTO subtopics(topic_id, name) VALUES (?, ?)
                    ON CONFLICT(topic_id, name) DO UPDATE SET name = excluded.name
                    """,
                    (parent_topic_id, subtopic.name),
                )
                row = cursor.execute(
                    "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
                    (parent_topic_id, subtopic.name),
                ).fetchone()
                subtopic_id_by_pair[(parent_topic_id, subtopic.name)] = row["id"]

            for assignment in payload.assignments:
                video_id = video_id_by_yt.get(assignment.youtube_video_id)
                if video_id is None:
                    raise ValueError(
                        f"unknown video in discovery payload: {assignment.youtube_video_id}"
                    )
                topic_id = topic_id_by_name.get(assignment.topic_name)
                if topic_id is None:
                    raise ValueError(
                        f"assignment references topic not in payload.topics: {assignment.topic_name}"
                    )
                cursor.execute(
                    """
                    INSERT INTO video_topics(
                        video_id, topic_id, assignment_type, assignment_source,
                        confidence, reason, discovery_run_id
                    ) VALUES (?, ?, 'secondary', 'auto', ?, ?, ?)
                    ON CONFLICT(video_id, topic_id) DO UPDATE SET
                        assignment_source = CASE
                            WHEN video_topics.assignment_source IN ('refine', 'manual')
                            THEN video_topics.assignment_source
                            ELSE excluded.assignment_source END,
                        confidence = CASE
                            WHEN video_topics.assignment_source IN ('refine', 'manual')
                            THEN video_topics.confidence
                            ELSE excluded.confidence END,
                        reason = CASE
                            WHEN video_topics.assignment_source IN ('refine', 'manual')
                            THEN video_topics.reason
                            ELSE excluded.reason END,
                        discovery_run_id = excluded.discovery_run_id
                    """,
                    (
                        video_id,
                        topic_id,
                        assignment.confidence,
                        assignment.reason,
                        run_id,
                    ),
                )

                if assignment.subtopic_name:
                    subtopic_id = subtopic_id_by_pair.get(
                        (topic_id, assignment.subtopic_name)
                    )
                    if subtopic_id is None:
                        raise ValueError(
                            "assignment references subtopic not in "
                            f"payload.subtopics under topic {assignment.topic_name!r}: "
                            f"{assignment.subtopic_name}"
                        )
                    cursor.execute(
                        """
                        INSERT INTO video_subtopics(
                            video_id, subtopic_id, assignment_source,
                            confidence, reason, discovery_run_id
                        ) VALUES (?, ?, 'auto', ?, ?, ?)
                        ON CONFLICT(video_id, subtopic_id) DO UPDATE SET
                            assignment_source = CASE
                                WHEN video_subtopics.assignment_source IN ('refine', 'manual')
                                THEN video_subtopics.assignment_source
                                ELSE excluded.assignment_source END,
                            confidence = CASE
                                WHEN video_subtopics.assignment_source IN ('refine', 'manual')
                                THEN video_subtopics.confidence
                                ELSE excluded.confidence END,
                            reason = CASE
                                WHEN video_subtopics.assignment_source IN ('refine', 'manual')
                                THEN video_subtopics.reason
                                ELSE excluded.reason END,
                            discovery_run_id = excluded.discovery_run_id
                        """,
                        (
                            video_id,
                            subtopic_id,
                            assignment.confidence,
                            assignment.reason,
                            run_id,
                        ),
                    )

            _suppress_wrong_assignments_in_run(connection, channel_id, run_id)

            # Now that this run's assignments are persisted (and wrong-marked
            # ones suppressed), count rename targets that lost all evidence:
            # a topic_renames row is orphaned if no episode kept in this run is
            # assigned to its target topic. Only computed when the filter is
            # active — left NULL otherwise. Like wrong-marks, the renames
            # themselves are never deleted, just counted.
            if effective_exclude_shorts:
                n_orphaned_renames = connection.execute(
                    """
                    SELECT COUNT(*) FROM topic_renames tr
                    WHERE tr.project_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM video_topics vt
                          WHERE vt.topic_id = tr.topic_id
                            AND vt.discovery_run_id = ?
                      )
                    """,
                    (project_id, run_id),
                ).fetchone()[0]
                connection.execute(
                    "UPDATE discovery_runs SET n_orphaned_renames = ? WHERE id = ?",
                    (n_orphaned_renames, run_id),
                )

            connection.execute(
                "UPDATE discovery_runs SET status = 'success' WHERE id = ?",
                (run_id,),
            )
            connection.commit()
        except Exception as exc:
            # Validation or persistence failed after the LLM returned a
            # payload — the API call has already been billed, so capture
            # the raw payload + error so the user can re-debug instead
            # of silently losing the response. The pre-allocated run row
            # survives the rollback (it was committed before the LLM call),
            # so we UPDATE it in place rather than inserting a duplicate.
            connection.rollback()
            connection.execute(
                """
                UPDATE discovery_runs
                SET status = 'error', error_message = ?, raw_response = ?
                WHERE id = ?
                """,
                (
                    str(exc),
                    json.dumps(asdict(raw_payload)),
                    run_id,
                ),
            )
            connection.commit()
            raise

        return run_id
