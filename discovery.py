from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from yt_channel_analyzer.db import connect, ensure_schema


@dataclass(frozen=True)
class DiscoveryVideo:
    youtube_video_id: str
    title: str
    description: str | None
    published_at: str | None


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
                description=row["description"],
                published_at=row["published_at"],
            )
            for row in video_rows
        ]
        video_id_by_yt = {row["youtube_video_id"]: row["id"] for row in video_rows}

        payload = llm(videos)

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
