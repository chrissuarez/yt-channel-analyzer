from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from yt_channel_analyzer.db import connect, ensure_schema
from yt_channel_analyzer.extractor.errors import ExtractorError
from yt_channel_analyzer.extractor.registry import (
    Prompt,
    get_prompt,
    register_prompt,
)


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
class DiscoveryAssignment:
    youtube_video_id: str
    topic_name: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class DiscoveryPayload:
    topics: list[str]
    assignments: list[DiscoveryAssignment]


LLMCallable = Callable[[Sequence[DiscoveryVideo]], DiscoveryPayload]


STUB_TOPIC_NAME = "General"
STUB_MODEL = "stub"
STUB_PROMPT_VERSION = "stub-v0"


DISCOVERY_PROMPT_NAME = "discovery.topics"
DISCOVERY_PROMPT_VERSION = "discovery-v1"


_DISCOVERY_SYSTEM = (
    "You are an editorial assistant grouping podcast episodes into broad "
    "topics from titles, descriptions, and chapter markers.\n"
    "\n"
    "Reply with a single JSON object of the form:\n"
    '  {"topics": ["Topic A", "Topic B"], '
    '"assignments": [{"youtube_video_id": "<id>", "topic": "Topic A"}]}\n'
    "\n"
    "Rules:\n"
    "- Every supplied episode must appear exactly once in `assignments`.\n"
    "- Every `topic` in `assignments` must also appear in `topics`.\n"
    "- Choose 3-12 broad topics; reuse one topic across many episodes.\n"
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
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["youtube_video_id", "topic"],
                "properties": {
                    "youtube_video_id": {"type": "string"},
                    "topic": {"type": "string"},
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
    assignments = [
        DiscoveryAssignment(
            youtube_video_id=item["youtube_video_id"],
            topic_name=item["topic"],
            confidence=1.0,
            reason="",
        )
        for item in data["assignments"]
    ]
    return DiscoveryPayload(topics=topics, assignments=assignments)


def discovery_llm_via_extractor(extractor: Any) -> "LLMCallable":
    """Adapt an Extractor into the LLMCallable signature `run_discovery` expects.

    A single batched call: all videos are rendered into one prompt and the
    response is parsed into a DiscoveryPayload. Schema validation and one
    automatic retry on parse failure are owned by the Extractor itself.
    """
    register_discovery_prompt()

    def call(videos: Sequence[DiscoveryVideo]) -> DiscoveryPayload:
        context = _videos_to_context(videos)
        result = extractor.run_one(
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            context,
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


def stub_llm(videos: Sequence[DiscoveryVideo]) -> DiscoveryPayload:
    """Hardcoded LLM stub: one topic, every video assigned to it.

    Used by `discover --stub` to wire the end-to-end pipeline without
    spending tokens. Real LLM lands in slice 02.
    """
    return DiscoveryPayload(
        topics=[STUB_TOPIC_NAME],
        assignments=[
            DiscoveryAssignment(
                youtube_video_id=video.youtube_video_id,
                topic_name=STUB_TOPIC_NAME,
                confidence=1.0,
                reason="stub assignment",
            )
            for video in videos
        ],
    )


def run_discovery(
    db_path: str | Path,
    *,
    project_name: str,
    llm: LLMCallable,
    model: str,
    prompt_version: str,
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
            SELECT id FROM channels
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
            SELECT id, youtube_video_id, title, description, published_at
            FROM videos WHERE channel_id = ?
            ORDER BY id
            """,
            (channel_id,),
        ).fetchall()
        videos = [
            DiscoveryVideo(
                youtube_video_id=row["youtube_video_id"],
                title=row["title"],
                description=strip_description_boilerplate(row["description"]),
                published_at=row["published_at"],
                chapters=parse_chapters_from_description(row["description"]),
            )
            for row in video_rows
        ]
        video_id_by_yt = {row["youtube_video_id"]: row["id"] for row in video_rows}

        try:
            payload = llm(videos)
        except Exception:
            # LLM (or its retry) failed — record an errored run row so the
            # failure is auditable, persist no partial topic / assignment
            # state, and re-raise for the caller. Slice 02 acceptance:
            # "on second failure the run is marked errored and no partial
            # state is persisted".
            err_cursor = connection.cursor()
            err_cursor.execute(
                """
                INSERT INTO discovery_runs(channel_id, model, prompt_version, status)
                VALUES (?, ?, ?, 'error')
                """,
                (channel_id, model, prompt_version),
            )
            connection.commit()
            raise

        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO discovery_runs(channel_id, model, prompt_version, status)
            VALUES (?, ?, ?, 'success')
            """,
            (channel_id, model, prompt_version),
        )
        run_id = cursor.lastrowid

        topic_id_by_name: dict[str, int] = {}
        for topic_name in payload.topics:
            cursor.execute(
                """
                INSERT INTO topics(project_id, name) VALUES (?, ?)
                ON CONFLICT(project_id, name) DO UPDATE SET name = excluded.name
                """,
                (project_id, topic_name),
            )
            row = cursor.execute(
                "SELECT id FROM topics WHERE project_id = ? AND name = ?",
                (project_id, topic_name),
            ).fetchone()
            topic_id_by_name[topic_name] = row["id"]

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
                    assignment_source = excluded.assignment_source,
                    confidence = excluded.confidence,
                    reason = excluded.reason,
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

        connection.commit()
        return run_id
