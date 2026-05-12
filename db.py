from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from yt_channel_analyzer.legacy.group_analysis import GroupAnalysisArtifact
from yt_channel_analyzer.legacy.processing import ProcessedVideoArtifact, TranscriptChunk
from yt_channel_analyzer.legacy.comparison_group_suggestions import VideoComparisonGroupSuggestion
from yt_channel_analyzer.subtopic_suggestions import VideoSubtopicSuggestion
from yt_channel_analyzer.topic_suggestions import VideoTopicSuggestion
from yt_channel_analyzer.youtube import ChannelMetadata, TranscriptRecord, VideoMetadata

SCHEMA_STATEMENTS = [
    """
    PRAGMA foreign_keys = ON;
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        slug TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        youtube_channel_id TEXT NOT NULL,
        title TEXT NOT NULL,
        handle TEXT,
        description TEXT,
        published_at TEXT,
        thumbnail_url TEXT,
        last_refreshed_at TEXT,
        is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
        exclude_shorts INTEGER NOT NULL DEFAULT 1 CHECK (exclude_shorts IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        UNIQUE(project_id, youtube_channel_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        youtube_video_id TEXT NOT NULL,
        title TEXT NOT NULL,
        published_at TEXT,
        description TEXT,
        thumbnail_url TEXT,
        duration_seconds INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
        UNIQUE(channel_id, youtube_video_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        first_discovery_run_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(first_discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
        UNIQUE(project_id, name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS video_topics (
        video_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL,
        assignment_type TEXT NOT NULL DEFAULT 'secondary',
        assignment_source TEXT NOT NULL DEFAULT 'manual',
        confidence REAL,
        reason TEXT,
        discovery_run_id INTEGER,
        refinement_run_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(video_id, topic_id),
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
        FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
        FOREIGN KEY(refinement_run_id) REFERENCES refinement_runs(id) ON DELETE SET NULL,
        CHECK (assignment_type IN ('primary', 'secondary')),
        CHECK (assignment_source IN ('manual', 'import', 'suggested', 'auto', 'refine'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS subtopics (
        id INTEGER PRIMARY KEY,
        topic_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
        UNIQUE(topic_id, name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS video_subtopics (
        video_id INTEGER NOT NULL,
        subtopic_id INTEGER NOT NULL,
        assignment_source TEXT NOT NULL DEFAULT 'manual',
        confidence REAL,
        reason TEXT,
        discovery_run_id INTEGER,
        refinement_run_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(video_id, subtopic_id),
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        FOREIGN KEY(subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
        FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
        FOREIGN KEY(refinement_run_id) REFERENCES refinement_runs(id) ON DELETE SET NULL,
        CHECK (assignment_source IN ('manual', 'import', 'suggested', 'auto', 'refine'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS comparison_groups (
        id INTEGER PRIMARY KEY,
        subtopic_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        target_size INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
        UNIQUE(subtopic_id, name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS comparison_group_videos (
        comparison_group_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(comparison_group_id, video_id),
        FOREIGN KEY(comparison_group_id) REFERENCES comparison_groups(id) ON DELETE CASCADE,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_runs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        run_kind TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
        CHECK (run_kind IN ('channel_metadata', 'video_metadata')),
        CHECK (status IN ('success', 'error'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS video_transcripts (
        video_id INTEGER PRIMARY KEY,
        transcript_status TEXT NOT NULL,
        transcript_source TEXT,
        language_code TEXT,
        transcript_text TEXT,
        transcript_detail TEXT,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        CHECK (transcript_status IN ('available', 'unavailable', 'disabled', 'not_found', 'rate_limited', 'request_failed', 'error')),
        CHECK (transcript_source IN ('manual', 'generated') OR transcript_source IS NULL)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_videos (
        video_id INTEGER PRIMARY KEY,
        processing_status TEXT NOT NULL,
        summary_text TEXT,
        transcript_char_count INTEGER NOT NULL DEFAULT 0,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        processing_detail TEXT,
        processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        CHECK (processing_status IN ('processed', 'transcript_missing', 'transcript_unavailable', 'transcript_disabled', 'transcript_not_found', 'transcript_rate_limited', 'transcript_request_failed', 'transcript_error'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_video_chunks (
        video_id INTEGER NOT NULL,
        chunk_index INTEGER NOT NULL,
        chunk_text TEXT NOT NULL,
        start_char INTEGER NOT NULL,
        end_char INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(video_id, chunk_index),
        FOREIGN KEY(video_id) REFERENCES processed_videos(video_id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS group_analyses (
        comparison_group_id INTEGER PRIMARY KEY,
        analysis_version TEXT NOT NULL,
        processed_video_count INTEGER NOT NULL DEFAULT 0,
        skipped_video_count INTEGER NOT NULL DEFAULT 0,
        shared_themes_json TEXT NOT NULL,
        repeated_recommendations_json TEXT NOT NULL,
        notable_differences_json TEXT NOT NULL,
        analysis_detail TEXT,
        analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(comparison_group_id) REFERENCES comparison_groups(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS markdown_exports (
        comparison_group_id INTEGER NOT NULL,
        export_kind TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        source_updated_at TEXT,
        exported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(comparison_group_id, export_kind, relative_path),
        FOREIGN KEY(comparison_group_id) REFERENCES comparison_groups(id) ON DELETE CASCADE,
        CHECK (export_kind IN ('video', 'group_summary'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS topic_suggestion_runs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        model_name TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        CHECK (status IN ('success', 'error'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS topic_suggestion_labels (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        suggestion_run_id INTEGER,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(suggestion_run_id) REFERENCES topic_suggestion_runs(id) ON DELETE SET NULL,
        UNIQUE(project_id, suggestion_run_id, name),
        CHECK (status IN ('pending', 'approved', 'rejected', 'superseded'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS video_topic_suggestions (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        suggestion_label_id INTEGER NOT NULL,
        assignment_type TEXT NOT NULL,
        rationale TEXT,
        reuse_existing INTEGER NOT NULL DEFAULT 0 CHECK (reuse_existing IN (0, 1)),
        raw_response_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TEXT,
        FOREIGN KEY(run_id) REFERENCES topic_suggestion_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        FOREIGN KEY(suggestion_label_id) REFERENCES topic_suggestion_labels(id) ON DELETE CASCADE,
        UNIQUE(video_id, suggestion_label_id, assignment_type),
        CHECK (assignment_type IN ('primary', 'secondary'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS subtopic_suggestion_labels (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL,
        suggestion_run_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
        FOREIGN KEY(suggestion_run_id) REFERENCES topic_suggestion_runs(id) ON DELETE CASCADE,
        UNIQUE(topic_id, suggestion_run_id, name),
        CHECK (status IN ('pending', 'approved', 'rejected', 'superseded'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS video_subtopic_suggestions (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        suggestion_label_id INTEGER NOT NULL,
        assignment_type TEXT NOT NULL,
        rationale TEXT,
        reuse_existing INTEGER NOT NULL DEFAULT 0 CHECK (reuse_existing IN (0, 1)),
        raw_response_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TEXT,
        FOREIGN KEY(run_id) REFERENCES topic_suggestion_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        FOREIGN KEY(suggestion_label_id) REFERENCES subtopic_suggestion_labels(id) ON DELETE CASCADE,
        UNIQUE(run_id, video_id),
        CHECK (assignment_type IN ('primary'))
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS comparison_group_suggestion_labels (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        subtopic_id INTEGER NOT NULL,
        suggestion_run_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
        FOREIGN KEY(suggestion_run_id) REFERENCES topic_suggestion_runs(id) ON DELETE CASCADE,
        UNIQUE(subtopic_id, suggestion_run_id, name),
        CHECK (status IN ('pending', 'approved', 'rejected', 'superseded'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS video_comparison_group_suggestions (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        suggestion_label_id INTEGER NOT NULL,
        rationale TEXT,
        reuse_existing INTEGER NOT NULL DEFAULT 0 CHECK (reuse_existing IN (0, 1)),
        raw_response_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TEXT,
        FOREIGN KEY(run_id) REFERENCES topic_suggestion_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        FOREIGN KEY(suggestion_label_id) REFERENCES comparison_group_suggestion_labels(id) ON DELETE CASCADE,
        UNIQUE(run_id, video_id),
        UNIQUE(run_id, video_id, suggestion_label_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS discovery_runs (
        id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        error_message TEXT,
        raw_response TEXT,
        shorts_cutoff_seconds INTEGER,
        n_episodes_total INTEGER,
        n_shorts_excluded INTEGER,
        n_orphaned_wrong_marks INTEGER,
        n_orphaned_renames INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
        CHECK (status IN ('running', 'success', 'error'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
        id INTEGER PRIMARY KEY,
        prompt_name TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        provider TEXT NOT NULL,
        is_batch INTEGER NOT NULL DEFAULT 0 CHECK (is_batch IN (0, 1)),
        batch_size INTEGER NOT NULL DEFAULT 1,
        parse_status TEXT NOT NULL CHECK (parse_status IN ('ok', 'retry', 'failed')),
        tokens_in INTEGER,
        tokens_out INTEGER,
        cost_estimate_usd REAL,
        correlation_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS wrong_assignments (
        id INTEGER PRIMARY KEY,
        video_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL,
        subtopic_id INTEGER,
        reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
        FOREIGN KEY(subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS topic_renames (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL,
        old_name TEXT NOT NULL,
        new_name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS refinement_runs (
        id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        discovery_run_id INTEGER,
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        n_sample INTEGER,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
        FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
        CHECK (status IN ('pending', 'running', 'success', 'error'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS refinement_episodes (
        refinement_run_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        transcript_status_at_run TEXT,
        assignments_before_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(refinement_run_id, video_id),
        FOREIGN KEY(refinement_run_id) REFERENCES refinement_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS taxonomy_proposals (
        id INTEGER PRIMARY KEY,
        refinement_run_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        parent_topic_name TEXT,
        evidence TEXT,
        source_video_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        resolved_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(refinement_run_id) REFERENCES refinement_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(source_video_id) REFERENCES videos(id) ON DELETE SET NULL,
        CHECK (kind IN ('topic', 'subtopic')),
        CHECK (status IN ('pending', 'accepted', 'rejected'))
    );
    """,
]

INDEX_STATEMENTS = [
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_one_primary_per_project
    ON channels(project_id)
    WHERE is_primary = 1;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_video_topics_one_primary_per_video
    ON video_topics(video_id)
    WHERE assignment_type = 'primary';
    """,
]

REQUIRED_TABLE_COLUMNS = {
    "projects": {
        "id": "INTEGER PRIMARY KEY",
        "name": "TEXT NOT NULL UNIQUE",
        "slug": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "channels": {
        "id": "INTEGER PRIMARY KEY",
        "project_id": "INTEGER NOT NULL",
        "youtube_channel_id": "TEXT NOT NULL",
        "title": "TEXT NOT NULL",
        "handle": "TEXT",
        "description": "TEXT",
        "published_at": "TEXT",
        "thumbnail_url": "TEXT",
        "last_refreshed_at": "TEXT",
        "is_primary": "INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1))",
        "exclude_shorts": "INTEGER NOT NULL DEFAULT 1 CHECK (exclude_shorts IN (0, 1))",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "videos": {
        "id": "INTEGER PRIMARY KEY",
        "channel_id": "INTEGER NOT NULL",
        "youtube_video_id": "TEXT NOT NULL",
        "title": "TEXT NOT NULL",
        "published_at": "TEXT",
        "description": "TEXT",
        "thumbnail_url": "TEXT",
        "duration_seconds": "INTEGER",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "topics": {
        "id": "INTEGER PRIMARY KEY",
        "project_id": "INTEGER NOT NULL",
        "name": "TEXT NOT NULL",
        "description": "TEXT",
        "first_discovery_run_id": "INTEGER",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "video_topics": {
        "video_id": "INTEGER NOT NULL",
        "topic_id": "INTEGER NOT NULL",
        "assignment_type": "TEXT NOT NULL DEFAULT 'secondary'",
        "assignment_source": "TEXT NOT NULL DEFAULT 'manual'",
        "confidence": "REAL",
        "reason": "TEXT",
        "discovery_run_id": "INTEGER",
        "refinement_run_id": "INTEGER",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "subtopics": {
        "id": "INTEGER PRIMARY KEY",
        "topic_id": "INTEGER NOT NULL",
        "name": "TEXT NOT NULL",
        "description": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "video_subtopics": {
        "video_id": "INTEGER NOT NULL",
        "subtopic_id": "INTEGER NOT NULL",
        "assignment_source": "TEXT NOT NULL DEFAULT 'manual'",
        "confidence": "REAL",
        "reason": "TEXT",
        "discovery_run_id": "INTEGER",
        "refinement_run_id": "INTEGER",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "comparison_groups": {
        "id": "INTEGER PRIMARY KEY",
        "subtopic_id": "INTEGER NOT NULL",
        "name": "TEXT NOT NULL",
        "description": "TEXT",
        "target_size": "INTEGER",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "comparison_group_videos": {
        "comparison_group_id": "INTEGER NOT NULL",
        "video_id": "INTEGER NOT NULL",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "fetch_runs": {
        "id": "INTEGER PRIMARY KEY",
        "project_id": "INTEGER NOT NULL",
        "channel_id": "INTEGER NOT NULL",
        "run_kind": "TEXT NOT NULL",
        "status": "TEXT NOT NULL",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "video_transcripts": {
        "video_id": "INTEGER PRIMARY KEY",
        "transcript_status": "TEXT NOT NULL",
        "transcript_source": "TEXT",
        "language_code": "TEXT",
        "transcript_text": "TEXT",
        "transcript_detail": "TEXT",
        "fetched_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "processed_videos": {
        "video_id": "INTEGER PRIMARY KEY",
        "processing_status": "TEXT NOT NULL",
        "summary_text": "TEXT",
        "transcript_char_count": "INTEGER NOT NULL DEFAULT 0",
        "chunk_count": "INTEGER NOT NULL DEFAULT 0",
        "processing_detail": "TEXT",
        "processed_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "processed_video_chunks": {
        "video_id": "INTEGER NOT NULL",
        "chunk_index": "INTEGER NOT NULL",
        "chunk_text": "TEXT NOT NULL",
        "start_char": "INTEGER NOT NULL",
        "end_char": "INTEGER NOT NULL",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "group_analyses": {
        "comparison_group_id": "INTEGER PRIMARY KEY",
        "analysis_version": "TEXT NOT NULL",
        "processed_video_count": "INTEGER NOT NULL DEFAULT 0",
        "skipped_video_count": "INTEGER NOT NULL DEFAULT 0",
        "shared_themes_json": "TEXT NOT NULL",
        "repeated_recommendations_json": "TEXT NOT NULL",
        "notable_differences_json": "TEXT NOT NULL",
        "analysis_detail": "TEXT",
        "analyzed_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "topic_suggestion_runs": {
        "id": "INTEGER PRIMARY KEY",
        "project_id": "INTEGER NOT NULL",
        "model_name": "TEXT NOT NULL",
        "status": "TEXT NOT NULL",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "topic_suggestion_labels": {
        "id": "INTEGER PRIMARY KEY",
        "project_id": "INTEGER NOT NULL",
        "suggestion_run_id": "INTEGER",
        "name": "TEXT NOT NULL",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "reviewed_at": "TEXT",
    },
    "video_topic_suggestions": {
        "id": "INTEGER PRIMARY KEY",
        "run_id": "INTEGER NOT NULL",
        "video_id": "INTEGER NOT NULL",
        "suggestion_label_id": "INTEGER NOT NULL",
        "assignment_type": "TEXT NOT NULL",
        "rationale": "TEXT",
        "reuse_existing": "INTEGER NOT NULL DEFAULT 0 CHECK (reuse_existing IN (0, 1))",
        "raw_response_json": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "reviewed_at": "TEXT",
    },
    "markdown_exports": {
        "comparison_group_id": "INTEGER NOT NULL",
        "export_kind": "TEXT NOT NULL",
        "relative_path": "TEXT NOT NULL",
        "source_updated_at": "TEXT",
        "exported_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "topic_renames": {
        "id": "INTEGER PRIMARY KEY",
        "project_id": "INTEGER NOT NULL",
        "topic_id": "INTEGER NOT NULL",
        "old_name": "TEXT NOT NULL",
        "new_name": "TEXT NOT NULL",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "discovery_runs": {
        "id": "INTEGER PRIMARY KEY",
        "channel_id": "INTEGER NOT NULL",
        "model": "TEXT NOT NULL",
        "prompt_version": "TEXT NOT NULL",
        "status": "TEXT NOT NULL DEFAULT 'running'",
        "error_message": "TEXT",
        "raw_response": "TEXT",
        "shorts_cutoff_seconds": "INTEGER",
        "n_episodes_total": "INTEGER",
        "n_shorts_excluded": "INTEGER",
        "n_orphaned_wrong_marks": "INTEGER",
        "n_orphaned_renames": "INTEGER",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "refinement_runs": {
        "id": "INTEGER PRIMARY KEY",
        "channel_id": "INTEGER NOT NULL",
        "discovery_run_id": "INTEGER",
        "model": "TEXT NOT NULL",
        "prompt_version": "TEXT NOT NULL",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "n_sample": "INTEGER",
        "error_message": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "refinement_episodes": {
        "refinement_run_id": "INTEGER NOT NULL",
        "video_id": "INTEGER NOT NULL",
        "transcript_status_at_run": "TEXT",
        "assignments_before_json": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "taxonomy_proposals": {
        "id": "INTEGER PRIMARY KEY",
        "refinement_run_id": "INTEGER NOT NULL",
        "kind": "TEXT NOT NULL",
        "name": "TEXT NOT NULL",
        "parent_topic_name": "TEXT",
        "evidence": "TEXT",
        "source_video_id": "INTEGER",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "resolved_at": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
}


@dataclass(frozen=True)
class PrimaryChannelRecord:
    project_id: int
    channel_id: int
    youtube_channel_id: str
    title: str


@dataclass(frozen=True)
class LibrarySearchResult:
    source_type: str
    youtube_video_id: str | None
    video_title: str | None
    group_id: int | None
    group_name: str | None
    topic_name: str | None
    subtopic_name: str | None
    snippet: str
    score: float


def get_project_overview(db_path: str | Path) -> dict[str, object]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row

        project_row = connection.execute(
            """
            SELECT id, name
            FROM projects
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        if project_row is None:
            raise ValueError("no project found in database")

        channel_row = connection.execute(
            """
            SELECT id, youtube_channel_id, title, handle
            FROM channels
            WHERE project_id = ? AND is_primary = 1
            ORDER BY id
            LIMIT 1
            """,
            (project_row["id"],),
        ).fetchone()
        if channel_row is None:
            raise ValueError("no primary channel found in database")

        counts_row = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM videos WHERE channel_id = ?) AS video_count,
                (SELECT COUNT(*)
                 FROM video_transcripts
                 JOIN videos ON videos.id = video_transcripts.video_id
                 WHERE videos.channel_id = ?) AS transcript_count,
                (SELECT COUNT(*)
                 FROM processed_videos
                 JOIN videos ON videos.id = processed_videos.video_id
                 WHERE videos.channel_id = ?) AS processed_video_count,
                (SELECT COUNT(*)
                 FROM markdown_exports
                 JOIN comparison_groups ON comparison_groups.id = markdown_exports.comparison_group_id
                 JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
                 JOIN topics ON topics.id = subtopics.topic_id
                 WHERE topics.project_id = ?) AS export_count
            """,
            (channel_row["id"], channel_row["id"], channel_row["id"], project_row["id"]),
        ).fetchone()

        topic_rows = connection.execute(
            """
            SELECT
                topics.id,
                topics.name,
                COUNT(DISTINCT video_topics.video_id) AS topic_assignment_count,
                COUNT(DISTINCT subtopics.id) AS subtopic_count
            FROM topics
            LEFT JOIN video_topics ON video_topics.topic_id = topics.id
            LEFT JOIN subtopics ON subtopics.topic_id = topics.id
            WHERE topics.project_id = ?
            GROUP BY topics.id, topics.name
            ORDER BY topics.name COLLATE NOCASE, topics.id
            """,
            (project_row["id"],),
        ).fetchall()

        subtopic_rows = connection.execute(
            """
            SELECT
                subtopics.id,
                subtopics.topic_id,
                subtopics.name,
                COUNT(DISTINCT video_subtopics.video_id) AS subtopic_assignment_count,
                COUNT(DISTINCT comparison_groups.id) AS group_count
            FROM subtopics
            LEFT JOIN video_subtopics ON video_subtopics.subtopic_id = subtopics.id
            LEFT JOIN comparison_groups ON comparison_groups.subtopic_id = subtopics.id
            GROUP BY subtopics.id, subtopics.topic_id, subtopics.name
            ORDER BY subtopics.name COLLATE NOCASE, subtopics.id
            """
        ).fetchall()

        group_rows = connection.execute(
            """
            SELECT
                comparison_groups.id,
                comparison_groups.subtopic_id,
                comparison_groups.name,
                COUNT(DISTINCT comparison_group_videos.video_id) AS member_count,
                COUNT(DISTINCT video_transcripts.video_id) AS transcript_count,
                COUNT(DISTINCT processed_videos.video_id) AS processed_video_count,
                COUNT(DISTINCT markdown_exports.relative_path) AS export_count
            FROM comparison_groups
            LEFT JOIN comparison_group_videos
                ON comparison_group_videos.comparison_group_id = comparison_groups.id
            LEFT JOIN video_transcripts
                ON video_transcripts.video_id = comparison_group_videos.video_id
            LEFT JOIN processed_videos
                ON processed_videos.video_id = comparison_group_videos.video_id
            LEFT JOIN markdown_exports
                ON markdown_exports.comparison_group_id = comparison_groups.id
            GROUP BY comparison_groups.id, comparison_groups.subtopic_id, comparison_groups.name
            ORDER BY comparison_groups.name COLLATE NOCASE, comparison_groups.id
            """
        ).fetchall()

    groups_by_subtopic: dict[int, list[dict[str, object]]] = {}
    for row in group_rows:
        groups_by_subtopic.setdefault(row["subtopic_id"], []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "member_count": row["member_count"],
                "transcript_count": row["transcript_count"],
                "processed_video_count": row["processed_video_count"],
                "export_count": row["export_count"],
            }
        )

    subtopics_by_topic: dict[int, list[dict[str, object]]] = {}
    for row in subtopic_rows:
        subtopics_by_topic.setdefault(row["topic_id"], []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "subtopic_assignment_count": row["subtopic_assignment_count"],
                "group_count": row["group_count"],
                "groups": groups_by_subtopic.get(row["id"], []),
            }
        )

    topics = [
        {
            "id": row["id"],
            "name": row["name"],
            "topic_assignment_count": row["topic_assignment_count"],
            "subtopic_count": row["subtopic_count"],
            "subtopics": subtopics_by_topic.get(row["id"], []),
        }
        for row in topic_rows
    ]

    return {
        "project_name": project_row["name"],
        "channel": {
            "youtube_channel_id": channel_row["youtube_channel_id"],
            "title": channel_row["title"],
            "handle": channel_row["handle"],
        },
        "counts": {
            "videos": counts_row["video_count"],
            "transcripts": counts_row["transcript_count"],
            "processed_videos": counts_row["processed_video_count"],
            "exports": counts_row["export_count"],
        },
        "topics": topics,
    }


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _get_existing_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _ensure_required_columns(connection: sqlite3.Connection) -> None:
    for table_name, required_columns in REQUIRED_TABLE_COLUMNS.items():
        existing_columns = _get_existing_columns(connection, table_name)
        if not existing_columns:
            continue
        for column_name, column_definition in required_columns.items():
            if column_name in existing_columns:
                continue
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )


def _repair_fetch_runs_run_kind_constraint(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "fetch_runs"):
        return

    create_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'fetch_runs'"
    ).fetchone()
    if create_sql_row is None:
        return

    create_sql = create_sql_row[0] or ""
    if "video_metadata" in create_sql:
        return

    connection.executescript(
        """
        ALTER TABLE fetch_runs RENAME TO fetch_runs_old;
        CREATE TABLE fetch_runs (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            run_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
            CHECK (run_kind IN ('channel_metadata', 'video_metadata')),
            CHECK (status IN ('success', 'error'))
        );
        INSERT INTO fetch_runs(id, project_id, channel_id, run_kind, status, created_at)
        SELECT id, project_id, channel_id, run_kind, status, created_at
        FROM fetch_runs_old;
        DROP TABLE fetch_runs_old;
        """
    )


def _repair_video_transcripts_constraint(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "video_transcripts"):
        return

    create_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'video_transcripts'"
    ).fetchone()
    if create_sql_row is None:
        return

    create_sql = create_sql_row[0] or ""
    if all(token in create_sql for token in ("transcript_detail", "rate_limited", "request_failed")):
        return

    connection.executescript(
        """
        ALTER TABLE video_transcripts RENAME TO video_transcripts_old;
        CREATE TABLE video_transcripts (
            video_id INTEGER PRIMARY KEY,
            transcript_status TEXT NOT NULL,
            transcript_source TEXT,
            language_code TEXT,
            transcript_text TEXT,
            transcript_detail TEXT,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
            CHECK (transcript_status IN ('available', 'unavailable', 'disabled', 'not_found', 'rate_limited', 'request_failed', 'error')),
            CHECK (transcript_source IN ('manual', 'generated') OR transcript_source IS NULL)
        );
        INSERT INTO video_transcripts(video_id, transcript_status, transcript_source, language_code, transcript_text, transcript_detail, fetched_at, created_at)
        SELECT video_id, transcript_status, transcript_source, language_code, transcript_text, NULL, fetched_at, created_at
        FROM video_transcripts_old;
        DROP TABLE video_transcripts_old;
        """
    )


def _repair_topic_suggestion_tables(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "video_topic_suggestions_old"):
        connection.execute("DROP TABLE video_topic_suggestions_old")
    if _table_exists(connection, "topic_suggestion_labels_old"):
        connection.execute("DROP TABLE topic_suggestion_labels_old")

    labels_need_repair = False
    if _table_exists(connection, "topic_suggestion_labels"):
        create_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'topic_suggestion_labels'"
        ).fetchone()
        create_sql = (create_sql_row[0] or "") if create_sql_row else ""
        labels_need_repair = (
            "UNIQUE(project_id, suggestion_run_id, name)" not in create_sql or "superseded" not in create_sql
        )

    suggestions_need_repair = False
    if _table_exists(connection, "video_topic_suggestions"):
        create_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'video_topic_suggestions'"
        ).fetchone()
        create_sql = (create_sql_row[0] or "") if create_sql_row else ""
        suggestions_need_repair = (
            "UNIQUE(video_id, suggestion_label_id, assignment_type)" not in create_sql
            or 'REFERENCES "topic_suggestion_labels_old"' in create_sql
            or "REFERENCES topic_suggestion_labels_old" in create_sql
        )

    if not labels_need_repair and not suggestions_need_repair:
        return

    connection.executescript("PRAGMA foreign_keys = OFF;")

    if suggestions_need_repair:
        connection.executescript(
            """
            ALTER TABLE video_topic_suggestions RENAME TO video_topic_suggestions_old;
            """
        )

    if labels_need_repair:
        connection.executescript(
            """
            ALTER TABLE topic_suggestion_labels RENAME TO topic_suggestion_labels_old;
            CREATE TABLE topic_suggestion_labels (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                suggestion_run_id INTEGER,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(suggestion_run_id) REFERENCES topic_suggestion_runs(id) ON DELETE SET NULL,
                UNIQUE(project_id, suggestion_run_id, name),
                CHECK (status IN ('pending', 'approved', 'rejected', 'superseded'))
            );
            INSERT INTO topic_suggestion_labels(id, project_id, suggestion_run_id, name, status, created_at, reviewed_at)
            SELECT id, project_id, suggestion_run_id, name,
                CASE WHEN status IN ('pending', 'approved', 'rejected', 'superseded') THEN status ELSE 'pending' END,
                created_at, reviewed_at
            FROM topic_suggestion_labels_old;
            DROP TABLE topic_suggestion_labels_old;
            """
        )

    if suggestions_need_repair:
        connection.executescript(
            """
            CREATE TABLE video_topic_suggestions (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                suggestion_label_id INTEGER NOT NULL,
                assignment_type TEXT NOT NULL,
                rationale TEXT,
                reuse_existing INTEGER NOT NULL DEFAULT 0 CHECK (reuse_existing IN (0, 1)),
                raw_response_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                FOREIGN KEY(run_id) REFERENCES topic_suggestion_runs(id) ON DELETE CASCADE,
                FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY(suggestion_label_id) REFERENCES topic_suggestion_labels(id) ON DELETE CASCADE,
                UNIQUE(video_id, suggestion_label_id, assignment_type),
                CHECK (assignment_type IN ('primary', 'secondary'))
            );
            INSERT INTO video_topic_suggestions(
                id, run_id, video_id, suggestion_label_id, assignment_type, rationale,
                reuse_existing, raw_response_json, created_at, reviewed_at
            )
            SELECT id, run_id, video_id, suggestion_label_id, assignment_type, rationale,
                reuse_existing, raw_response_json, created_at, reviewed_at
            FROM video_topic_suggestions_old;
            DROP TABLE video_topic_suggestions_old;
            """
        )

    connection.executescript("PRAGMA foreign_keys = ON;")


def _repair_video_topic_assignment_source_constraint(
    connection: sqlite3.Connection,
) -> None:
    if _table_exists(connection, "video_topics"):
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'video_topics'"
        ).fetchone()
        create_sql = (sql_row[0] or "") if sql_row else ""
        if "'auto'" not in create_sql:
            connection.executescript(
                """
                ALTER TABLE video_topics RENAME TO video_topics_old;
                CREATE TABLE video_topics (
                    video_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    assignment_type TEXT NOT NULL DEFAULT 'secondary',
                    assignment_source TEXT NOT NULL DEFAULT 'manual',
                    confidence REAL,
                    reason TEXT,
                    discovery_run_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(video_id, topic_id),
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                    FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
                    FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
                    CHECK (assignment_type IN ('primary', 'secondary')),
                    CHECK (assignment_source IN ('manual', 'import', 'suggested', 'auto'))
                );
                INSERT INTO video_topics(
                    video_id, topic_id, assignment_type, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                )
                SELECT video_id, topic_id, assignment_type, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                FROM video_topics_old;
                DROP TABLE video_topics_old;
                """
            )

    if _table_exists(connection, "video_subtopics"):
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'video_subtopics'"
        ).fetchone()
        create_sql = (sql_row[0] or "") if sql_row else ""
        if "'auto'" not in create_sql:
            connection.executescript(
                """
                ALTER TABLE video_subtopics RENAME TO video_subtopics_old;
                CREATE TABLE video_subtopics (
                    video_id INTEGER NOT NULL,
                    subtopic_id INTEGER NOT NULL,
                    assignment_source TEXT NOT NULL DEFAULT 'manual',
                    confidence REAL,
                    reason TEXT,
                    discovery_run_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(video_id, subtopic_id),
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                    FOREIGN KEY(subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
                    FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
                    CHECK (assignment_source IN ('manual', 'import', 'suggested', 'auto'))
                );
                INSERT INTO video_subtopics(
                    video_id, subtopic_id, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                )
                SELECT video_id, subtopic_id, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                FROM video_subtopics_old;
                DROP TABLE video_subtopics_old;
                """
            )


def _repair_video_topic_refine_source_constraint(
    connection: sqlite3.Connection,
) -> None:
    """Rebuild ``video_topics`` / ``video_subtopics`` to allow
    ``assignment_source='refine'`` and carry a nullable ``refinement_run_id``.

    Mirrors ``_repair_video_topic_assignment_source_constraint``'s rename →
    recreate → INSERT...SELECT → drop dance (no other table FKs into these two,
    so the plain dance is enough; ``idx_video_topics_one_primary_per_video`` is
    re-created by ``INDEX_STATEMENTS`` afterwards). The ``'refine'`` substring
    check on the live create-SQL *is* the idempotency guard — no marker table —
    matching the other ``_repair_*`` functions here. The INSERT...SELECT below
    deliberately does *not* name ``refinement_run_id`` (it defaults NULL on the
    rebuilt table): any DB reaching this branch predates the ``'refine'`` source
    so has no refine rows, and the ``'auto'`` repair that runs just before this
    may itself have rebuilt the table without that column.
    """
    if _table_exists(connection, "video_topics"):
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'video_topics'"
        ).fetchone()
        create_sql = (sql_row[0] or "") if sql_row else ""
        if "'refine'" not in create_sql:
            connection.executescript(
                """
                ALTER TABLE video_topics RENAME TO video_topics_old;
                CREATE TABLE video_topics (
                    video_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    assignment_type TEXT NOT NULL DEFAULT 'secondary',
                    assignment_source TEXT NOT NULL DEFAULT 'manual',
                    confidence REAL,
                    reason TEXT,
                    discovery_run_id INTEGER,
                    refinement_run_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(video_id, topic_id),
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                    FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
                    FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
                    FOREIGN KEY(refinement_run_id) REFERENCES refinement_runs(id) ON DELETE SET NULL,
                    CHECK (assignment_type IN ('primary', 'secondary')),
                    CHECK (assignment_source IN ('manual', 'import', 'suggested', 'auto', 'refine'))
                );
                INSERT INTO video_topics(
                    video_id, topic_id, assignment_type, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                )
                SELECT video_id, topic_id, assignment_type, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                FROM video_topics_old;
                DROP TABLE video_topics_old;
                """
            )

    if _table_exists(connection, "video_subtopics"):
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'video_subtopics'"
        ).fetchone()
        create_sql = (sql_row[0] or "") if sql_row else ""
        if "'refine'" not in create_sql:
            connection.executescript(
                """
                ALTER TABLE video_subtopics RENAME TO video_subtopics_old;
                CREATE TABLE video_subtopics (
                    video_id INTEGER NOT NULL,
                    subtopic_id INTEGER NOT NULL,
                    assignment_source TEXT NOT NULL DEFAULT 'manual',
                    confidence REAL,
                    reason TEXT,
                    discovery_run_id INTEGER,
                    refinement_run_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(video_id, subtopic_id),
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                    FOREIGN KEY(subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
                    FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs(id) ON DELETE SET NULL,
                    FOREIGN KEY(refinement_run_id) REFERENCES refinement_runs(id) ON DELETE SET NULL,
                    CHECK (assignment_source IN ('manual', 'import', 'suggested', 'auto', 'refine'))
                );
                INSERT INTO video_subtopics(
                    video_id, subtopic_id, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                )
                SELECT video_id, subtopic_id, assignment_source,
                    confidence, reason, discovery_run_id, created_at
                FROM video_subtopics_old;
                DROP TABLE video_subtopics_old;
                """
            )


def _repair_discovery_runs_status_constraint(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "discovery_runs"):
        return

    create_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'discovery_runs'"
    ).fetchone()
    if create_sql_row is None:
        return

    create_sql = create_sql_row[0] or ""
    if "'running'" in create_sql:
        return

    # Rebuild the parent table without breaking child FK references
    # (topics, video_topics, video_subtopics all reference discovery_runs.id).
    # legacy_alter_table=ON keeps the RENAME from rewriting child schemas to
    # point at discovery_runs_old; foreign_keys=OFF keeps the DROP at the end
    # from firing ON DELETE SET NULL on existing child rows.
    connection.executescript("PRAGMA foreign_keys = OFF;")
    connection.executescript("PRAGMA legacy_alter_table = ON;")
    connection.executescript(
        """
        ALTER TABLE discovery_runs RENAME TO discovery_runs_old;
        CREATE TABLE discovery_runs (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            error_message TEXT,
            raw_response TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
            CHECK (status IN ('running', 'success', 'error'))
        );
        INSERT INTO discovery_runs(
            id, channel_id, model, prompt_version, status,
            error_message, raw_response, created_at
        )
        SELECT id, channel_id, model, prompt_version, status,
            error_message, raw_response, created_at
        FROM discovery_runs_old;
        DROP TABLE discovery_runs_old;
        """
    )
    connection.executescript("PRAGMA legacy_alter_table = OFF;")
    connection.executescript("PRAGMA foreign_keys = ON;")


def _repair_channels_exclude_shorts_default(connection: sqlite3.Connection) -> None:
    """One-shot migration: flip the shorts filter on for every channel.

    Pre-slice-C DBs have ``exclude_shorts INTEGER NOT NULL DEFAULT 0``. This
    flips every existing channel to ``exclude_shorts = 1`` once, then rebuilds
    the ``channels`` table so the create-SQL says ``DEFAULT 1``. After the
    rebuild the substring check below fails, so this is a no-op on every
    subsequent ``ensure_schema()`` — that create-SQL inspection *is* the
    idempotency guard (matching the other repair functions in this file; no
    marker table). A channel a user manually sets back to 0 post-migration
    stays 0, because the UPDATE only runs while the create-SQL still says
    ``DEFAULT 0``.
    """
    if not _table_exists(connection, "channels"):
        return
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'channels'"
    ).fetchone()
    create_sql = (sql_row[0] or "") if sql_row else ""
    if "exclude_shorts INTEGER NOT NULL DEFAULT 0" not in create_sql:
        return

    connection.execute("UPDATE channels SET exclude_shorts = 1")
    # Rebuild ``channels`` with the new default. videos/discovery_runs FK
    # channel_id, so use the same legacy_alter_table + foreign_keys=OFF dance
    # as _repair_discovery_runs_status_constraint to keep child schemas/rows
    # intact across the RENAME/DROP. INDEX_STATEMENTS (run after this in
    # ensure_schema) recreates idx_channels_one_primary_per_project.
    connection.executescript("PRAGMA foreign_keys = OFF;")
    connection.executescript("PRAGMA legacy_alter_table = ON;")
    connection.executescript(
        """
        ALTER TABLE channels RENAME TO channels_old;
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            youtube_channel_id TEXT NOT NULL,
            title TEXT NOT NULL,
            handle TEXT,
            description TEXT,
            published_at TEXT,
            thumbnail_url TEXT,
            last_refreshed_at TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
            exclude_shorts INTEGER NOT NULL DEFAULT 1 CHECK (exclude_shorts IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, youtube_channel_id)
        );
        INSERT INTO channels(
            id, project_id, youtube_channel_id, title, handle, description,
            published_at, thumbnail_url, last_refreshed_at, is_primary,
            exclude_shorts, created_at
        )
        SELECT id, project_id, youtube_channel_id, title, handle, description,
            published_at, thumbnail_url, last_refreshed_at, is_primary,
            exclude_shorts, created_at
        FROM channels_old;
        DROP TABLE channels_old;
        """
    )
    connection.executescript("PRAGMA legacy_alter_table = OFF;")
    connection.executescript("PRAGMA foreign_keys = ON;")


def ensure_schema(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    for statement in SCHEMA_STATEMENTS:
        cursor.executescript(statement)
    _ensure_required_columns(connection)
    _repair_fetch_runs_run_kind_constraint(connection)
    _repair_video_transcripts_constraint(connection)
    _repair_topic_suggestion_tables(connection)
    _repair_video_topic_assignment_source_constraint(connection)
    _repair_video_topic_refine_source_constraint(connection)
    _repair_discovery_runs_status_constraint(connection)
    _repair_channels_exclude_shorts_default(connection)
    for statement in INDEX_STATEMENTS:
        cursor.executescript(statement)


def _sqlite_supports_fts5(connection: sqlite3.Connection) -> bool:
    rows = connection.execute("PRAGMA compile_options").fetchall()
    return any("ENABLE_FTS5" in row[0] for row in rows)


def _normalize_search_query(query: str) -> list[str]:
    terms = [term.strip().lower() for term in query.split() if term.strip()]
    return list(dict.fromkeys(terms))


def _build_snippet(text: str, terms: list[str], *, width: int = 140) -> str:
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return ""
    lowered = collapsed.lower()
    match_index = min((lowered.find(term) for term in terms if term and lowered.find(term) != -1), default=-1)
    if match_index < 0:
        snippet = collapsed[:width]
        return snippet if len(collapsed) <= width else f"{snippet}..."
    start = max(0, match_index - (width // 3))
    end = min(len(collapsed), start + width)
    snippet = collapsed[start:end]
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(collapsed):
        snippet = f"{snippet}..."
    return snippet


def _validate_search_filters(
    connection: sqlite3.Connection,
    *,
    group_name: str | None,
    topic_name: str | None,
    subtopic_name: str | None,
) -> None:
    if group_name is not None:
        row = connection.execute(
            "SELECT id FROM comparison_groups WHERE name = ? ORDER BY id LIMIT 1",
            (group_name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"comparison group not found: {group_name}")
    if topic_name is not None:
        row = connection.execute(
            "SELECT id FROM topics WHERE name = ? ORDER BY id LIMIT 1",
            (topic_name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"topic not found: {topic_name}")
    if subtopic_name is not None:
        row = connection.execute(
            "SELECT id FROM subtopics WHERE name = ? ORDER BY id LIMIT 1",
            (subtopic_name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"subtopic not found: {subtopic_name}")


def _search_source_rows(
    connection: sqlite3.Connection,
    *,
    source_type: str,
    text_column: str,
    table_name: str,
    joins: str,
    where_conditions: list[str],
    params: list[object],
    terms: list[str],
    limit: int,
) -> list[LibrarySearchResult]:
    select_sql = f"""
        SELECT
            ? AS source_type,
            videos.youtube_video_id,
            videos.title AS video_title,
            comparison_groups.id AS group_id,
            comparison_groups.name AS group_name,
            topics.name AS topic_name,
            subtopics.name AS subtopic_name,
            {text_column} AS body_text
        FROM {table_name}
        {joins}
        WHERE {' AND '.join(where_conditions)}
    """
    rows = connection.execute(select_sql, [source_type, *params]).fetchall()
    matches: list[LibrarySearchResult] = []
    for row in rows:
        body_text = row[7] or ""
        lowered = body_text.lower()
        score = 0.0
        for term in terms:
            occurrences = lowered.count(term)
            if occurrences:
                score += occurrences
                if row[2] and term in row[2].lower():
                    score += 2.0
                if row[4] and term in row[4].lower():
                    score += 1.5
                if row[5] and term in row[5].lower():
                    score += 1.0
                if row[6] and term in row[6].lower():
                    score += 1.0
        if score <= 0:
            continue
        matches.append(
            LibrarySearchResult(
                source_type=row[0],
                youtube_video_id=row[1],
                video_title=row[2],
                group_id=row[3],
                group_name=row[4],
                topic_name=row[5],
                subtopic_name=row[6],
                snippet=_build_snippet(body_text, terms),
                score=score,
            )
        )
    matches.sort(
        key=lambda item: (
            -item.score,
            item.source_type,
            item.group_name or "",
            item.video_title or "",
            item.youtube_video_id or "",
            item.snippet,
        )
    )
    return matches[:limit]


def _fts_search_library(
    connection: sqlite3.Connection,
    *,
    query: str,
    group_name: str | None,
    topic_name: str | None,
    subtopic_name: str | None,
    limit: int,
) -> list[LibrarySearchResult]:
    connection.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS library_search_fts USING fts5(
            source_type,
            youtube_video_id,
            video_title,
            group_id UNINDEXED,
            group_name,
            topic_name,
            subtopic_name,
            body_text
        );
        DELETE FROM library_search_fts;
        INSERT INTO library_search_fts(source_type, youtube_video_id, video_title, group_id, group_name, topic_name, subtopic_name, body_text)
        SELECT
            'transcript',
            videos.youtube_video_id,
            videos.title,
            comparison_groups.id,
            comparison_groups.name,
            topics.name,
            subtopics.name,
            video_transcripts.transcript_text
        FROM video_transcripts
        JOIN videos ON videos.id = video_transcripts.video_id
        LEFT JOIN comparison_group_videos ON comparison_group_videos.video_id = videos.id
        LEFT JOIN comparison_groups ON comparison_groups.id = comparison_group_videos.comparison_group_id
        LEFT JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
        LEFT JOIN topics ON topics.id = subtopics.topic_id
        WHERE video_transcripts.transcript_text IS NOT NULL

        UNION ALL

        SELECT
            'video_summary',
            videos.youtube_video_id,
            videos.title,
            comparison_groups.id,
            comparison_groups.name,
            topics.name,
            subtopics.name,
            processed_videos.summary_text
        FROM processed_videos
        JOIN videos ON videos.id = processed_videos.video_id
        LEFT JOIN comparison_group_videos ON comparison_group_videos.video_id = videos.id
        LEFT JOIN comparison_groups ON comparison_groups.id = comparison_group_videos.comparison_group_id
        LEFT JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
        LEFT JOIN topics ON topics.id = subtopics.topic_id
        WHERE processed_videos.summary_text IS NOT NULL

        UNION ALL

        SELECT
            'group_analysis',
            NULL,
            NULL,
            comparison_groups.id,
            comparison_groups.name,
            topics.name,
            subtopics.name,
            group_analyses.analysis_detail
        FROM group_analyses
        JOIN comparison_groups ON comparison_groups.id = group_analyses.comparison_group_id
        LEFT JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
        LEFT JOIN topics ON topics.id = subtopics.topic_id
        WHERE group_analyses.analysis_detail IS NOT NULL;
        """
    )

    filters = ["library_search_fts MATCH ?"]
    params: list[object] = [query]
    if group_name is not None:
        filters.append("group_name = ?")
        params.append(group_name)
    if topic_name is not None:
        filters.append("topic_name = ?")
        params.append(topic_name)
    if subtopic_name is not None:
        filters.append("subtopic_name = ?")
        params.append(subtopic_name)

    rows = connection.execute(
        f"""
        SELECT
            source_type,
            youtube_video_id,
            video_title,
            group_id,
            group_name,
            topic_name,
            subtopic_name,
            snippet(library_search_fts, 7, '<<', '>>', '...', 18) AS snippet_text,
            bm25(library_search_fts) AS rank
        FROM library_search_fts
        WHERE {' AND '.join(filters)}
        ORDER BY rank, source_type, group_name, video_title, youtube_video_id
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [
        LibrarySearchResult(
            source_type=row[0],
            youtube_video_id=row[1],
            video_title=row[2],
            group_id=row[3],
            group_name=row[4],
            topic_name=row[5],
            subtopic_name=row[6],
            snippet=(row[7] or "").replace("<<", "").replace(">>", ""),
            score=float(-row[8]),
        )
        for row in rows
    ]


def _fallback_search_library(
    connection: sqlite3.Connection,
    *,
    query: str,
    group_name: str | None,
    topic_name: str | None,
    subtopic_name: str | None,
    limit: int,
) -> list[LibrarySearchResult]:
    terms = _normalize_search_query(query)
    if not terms:
        return []

    base_joins = """
        JOIN videos ON videos.id = {table_name}.video_id
        LEFT JOIN comparison_group_videos ON comparison_group_videos.video_id = videos.id
        LEFT JOIN comparison_groups ON comparison_groups.id = comparison_group_videos.comparison_group_id
        LEFT JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
        LEFT JOIN topics ON topics.id = subtopics.topic_id
    """

    filters: list[str] = ["{text_column} IS NOT NULL"]
    params: list[object] = []
    if group_name is not None:
        filters.append("comparison_groups.name = ?")
        params.append(group_name)
    if topic_name is not None:
        filters.append("topics.name = ?")
        params.append(topic_name)
    if subtopic_name is not None:
        filters.append("subtopics.name = ?")
        params.append(subtopic_name)

    results: list[LibrarySearchResult] = []
    results.extend(
        _search_source_rows(
            connection,
            source_type="transcript",
            text_column="video_transcripts.transcript_text",
            table_name="video_transcripts",
            joins=base_joins.format(table_name="video_transcripts"),
            where_conditions=filters,
            params=params,
            terms=terms,
            limit=limit,
        )
    )
    results.extend(
        _search_source_rows(
            connection,
            source_type="video_summary",
            text_column="processed_videos.summary_text",
            table_name="processed_videos",
            joins=base_joins.format(table_name="processed_videos"),
            where_conditions=filters,
            params=params,
            terms=terms,
            limit=limit,
        )
    )

    group_filters = ["group_analyses.analysis_detail IS NOT NULL"]
    group_params: list[object] = []
    if group_name is not None:
        group_filters.append("comparison_groups.name = ?")
        group_params.append(group_name)
    if topic_name is not None:
        group_filters.append("topics.name = ?")
        group_params.append(topic_name)
    if subtopic_name is not None:
        group_filters.append("subtopics.name = ?")
        group_params.append(subtopic_name)
    results.extend(
        _search_source_rows(
            connection,
            source_type="group_analysis",
            text_column="group_analyses.analysis_detail",
            table_name="group_analyses",
            joins="""
                JOIN comparison_groups ON comparison_groups.id = group_analyses.comparison_group_id
                LEFT JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
                LEFT JOIN topics ON topics.id = subtopics.topic_id
                LEFT JOIN comparison_group_videos ON comparison_group_videos.comparison_group_id = comparison_groups.id
                LEFT JOIN videos ON videos.id = comparison_group_videos.video_id
            """,
            where_conditions=group_filters,
            params=group_params,
            terms=terms,
            limit=limit,
        )
    )

    deduped: dict[tuple[str, str | None, int | None, str], LibrarySearchResult] = {}
    for item in results:
        key = (item.source_type, item.youtube_video_id, item.group_id, item.snippet)
        existing = deduped.get(key)
        if existing is None or item.score > existing.score:
            deduped[key] = item

    ordered = sorted(
        deduped.values(),
        key=lambda item: (
            -item.score,
            item.source_type,
            item.group_name or "",
            item.video_title or "",
            item.youtube_video_id or "",
            item.snippet,
        ),
    )
    return ordered[:limit]


def search_library(
    db_path: str | Path,
    *,
    query: str,
    group_name: str | None = None,
    topic_name: str | None = None,
    subtopic_name: str | None = None,
    limit: int = 10,
) -> tuple[list[LibrarySearchResult], str]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")
    if limit <= 0:
        raise ValueError("limit must be greater than 0")

    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        _validate_search_filters(
            connection,
            group_name=group_name,
            topic_name=topic_name,
            subtopic_name=subtopic_name,
        )

        has_videos = connection.execute("SELECT 1 FROM videos LIMIT 1").fetchone()
        if has_videos is None:
            raise ValueError("no stored videos found in database")

        artifact_count = connection.execute(
            """
            SELECT (
                (SELECT COUNT(*) FROM video_transcripts WHERE transcript_text IS NOT NULL) +
                (SELECT COUNT(*) FROM processed_videos WHERE summary_text IS NOT NULL) +
                (SELECT COUNT(*) FROM group_analyses WHERE analysis_detail IS NOT NULL)
            )
            """
        ).fetchone()[0]
        if artifact_count == 0:
            raise ValueError("no searchable stored artefacts found in database")

        if _sqlite_supports_fts5(connection):
            return _fts_search_library(
                connection,
                query=normalized_query,
                group_name=group_name,
                topic_name=topic_name,
                subtopic_name=subtopic_name,
                limit=limit,
            ), "fts5"
        return _fallback_search_library(
            connection,
            query=normalized_query,
            group_name=group_name,
            topic_name=topic_name,
            subtopic_name=subtopic_name,
            limit=limit,
        ), "fallback"


def init_db(
    db_path: str | Path,
    *,
    project_name: str,
    channel_id: str,
    channel_title: str,
    channel_handle: str | None = None,
) -> Path:
    db_file = Path(db_path)
    with connect(db_file) as connection:
        ensure_schema(connection)

        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO projects(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (project_name,),
        )
        project_row = cursor.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1",
            (project_name,),
        ).fetchone()
        project_db_id = project_row[0]

        existing_channel_row = cursor.execute(
            """
            SELECT id, youtube_channel_id, title, handle, is_primary
            FROM channels
            WHERE project_id = ?
            ORDER BY is_primary DESC, id
            LIMIT 1
            """,
            (project_db_id,),
        ).fetchone()
        if existing_channel_row is None:
            cursor.execute(
                """
                INSERT INTO channels(project_id, youtube_channel_id, title, handle, is_primary)
                VALUES (?, ?, ?, ?, 1)
                """,
                (project_db_id, channel_id, channel_title, channel_handle),
            )
        else:
            cursor.execute(
                """
                UPDATE channels
                SET youtube_channel_id = ?,
                    title = ?,
                    handle = ?,
                    is_primary = 1
                WHERE id = ?
                """,
                (channel_id, channel_title, channel_handle, existing_channel_row[0]),
            )
            cursor.execute(
                "UPDATE channels SET is_primary = 0 WHERE project_id = ? AND id != ?",
                (project_db_id, existing_channel_row[0]),
            )
        connection.commit()

    return db_file


def upsert_channel_metadata(
    db_path: str | Path,
    *,
    project_name: str,
    metadata: ChannelMetadata,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()

        cursor.execute(
            "INSERT INTO projects(name) VALUES (?) ON CONFLICT DO NOTHING",
            (project_name,),
        )
        project_row = cursor.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1",
            (project_name,),
        ).fetchone()
        project_id = project_row[0]

        cursor.execute(
            """
            INSERT INTO channels(
                project_id, youtube_channel_id, title, handle, description, published_at,
                thumbnail_url, last_refreshed_at, is_primary
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
            ON CONFLICT(project_id, youtube_channel_id) DO UPDATE SET
                title = excluded.title,
                handle = excluded.handle,
                description = excluded.description,
                published_at = excluded.published_at,
                thumbnail_url = excluded.thumbnail_url,
                last_refreshed_at = CURRENT_TIMESTAMP
            """,
            (
                project_id,
                metadata.youtube_channel_id,
                metadata.title,
                metadata.handle,
                metadata.description,
                metadata.published_at,
                metadata.thumbnail_url,
            ),
        )

        channel_row = cursor.execute(
            "SELECT id FROM channels WHERE project_id = ? AND youtube_channel_id = ?",
            (project_id, metadata.youtube_channel_id),
        ).fetchone()
        channel_id = channel_row[0]
        cursor.execute(
            """
            INSERT INTO fetch_runs(project_id, channel_id, run_kind, status)
            VALUES (?, ?, 'channel_metadata', 'success')
            """,
            (project_id, channel_id),
        )
        connection.commit()

    return channel_id


def update_channel_fields(
    db_path: str | Path,
    *,
    channel_id: int,
    title: str,
    handle: str | None,
    description: str | None,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.execute(
            """
            UPDATE channels
            SET title = ?, handle = ?, description = ?
            WHERE id = ?
            """,
            (title, handle, description, channel_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"no channel with id {channel_id}")
        connection.commit()


def get_primary_channel(db_path: str | Path) -> PrimaryChannelRecord:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT project_id, id AS channel_id, youtube_channel_id, title
            FROM channels
            WHERE is_primary = 1
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise ValueError("no primary channel found in database")
    return PrimaryChannelRecord(
        project_id=row["project_id"],
        channel_id=row["channel_id"],
        youtube_channel_id=row["youtube_channel_id"],
        title=row["title"],
    )


def upsert_videos_for_primary_channel(
    db_path: str | Path,
    *,
    videos: list[VideoMetadata],
) -> int:
    primary_channel = get_primary_channel(db_path)
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        for video in videos:
            cursor.execute(
                """
                INSERT INTO videos(channel_id, youtube_video_id, title, published_at, description, thumbnail_url, duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, youtube_video_id) DO UPDATE SET
                    title = excluded.title,
                    published_at = excluded.published_at,
                    description = excluded.description,
                    thumbnail_url = excluded.thumbnail_url,
                    duration_seconds = COALESCE(excluded.duration_seconds, videos.duration_seconds)
                """,
                (
                    primary_channel.channel_id,
                    video.youtube_video_id,
                    video.title,
                    video.published_at,
                    video.description,
                    video.thumbnail_url,
                    video.duration_seconds,
                ),
            )

        cursor.execute(
            """
            INSERT INTO fetch_runs(project_id, channel_id, run_kind, status)
            VALUES (?, ?, 'video_metadata', 'success')
            """,
            (primary_channel.project_id, primary_channel.channel_id),
        )
        connection.commit()
    return len(videos)


def get_video_ids_missing_duration_for_primary_channel(db_path: str | Path) -> list[str]:
    """YouTube video IDs of the primary channel's rows with NULL duration_seconds."""
    primary_channel = get_primary_channel(db_path)
    with connect(db_path) as connection:
        ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT youtube_video_id
            FROM videos
            WHERE channel_id = ? AND duration_seconds IS NULL
            ORDER BY youtube_video_id
            """,
            (primary_channel.channel_id,),
        ).fetchall()
    return [row[0] for row in rows]


def list_primary_channel_transcript_status(db_path: str | Path) -> list[sqlite3.Row]:
    """Every primary-channel video paired with its current transcript status
    (``transcript_status`` is NULL when the video has no ``video_transcripts``
    row yet), newest first. Backs the ``fetch-transcripts`` selectors."""
    primary_channel = get_primary_channel(db_path)
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT videos.youtube_video_id,
                   videos.title,
                   videos.published_at,
                   video_transcripts.transcript_status
            FROM videos
            LEFT JOIN video_transcripts ON video_transcripts.video_id = videos.id
            WHERE videos.channel_id = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """,
            (primary_channel.channel_id,),
        ).fetchall()


def update_video_durations_for_primary_channel(
    db_path: str | Path,
    *,
    durations_by_video_id: dict[str, int | None],
) -> int:
    """Set duration_seconds for the given primary-channel video IDs. Only touches
    rows still NULL, so re-running is idempotent. Returns rows updated."""
    primary_channel = get_primary_channel(db_path)
    updated = 0
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        for youtube_video_id, duration_seconds in durations_by_video_id.items():
            if duration_seconds is None:
                continue
            cursor.execute(
                """
                UPDATE videos
                SET duration_seconds = ?
                WHERE channel_id = ? AND youtube_video_id = ? AND duration_seconds IS NULL
                """,
                (duration_seconds, primary_channel.channel_id, youtube_video_id),
            )
            updated += cursor.rowcount
        connection.commit()
    return updated


def create_topic(
    db_path: str | Path,
    *,
    project_name: str,
    topic_name: str,
    description: str | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO projects(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (project_name,),
        )
        project_id = cursor.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1",
            (project_name,),
        ).fetchone()[0]
        cursor.execute(
            """
            INSERT INTO topics(project_id, name, description)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id, name) DO UPDATE SET
                description = excluded.description
            """,
            (project_id, topic_name, description),
        )
        topic_id = cursor.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, topic_name),
        ).fetchone()[0]
        connection.commit()
    return topic_id


def list_topics(db_path: str | Path) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                topics.id,
                topics.name,
                topics.description,
                COUNT(video_topics.video_id) AS assignment_count,
                SUM(CASE WHEN video_topics.assignment_type = 'primary' THEN 1 ELSE 0 END) AS primary_count,
                SUM(CASE WHEN video_topics.assignment_type = 'secondary' THEN 1 ELSE 0 END) AS secondary_count
            FROM topics
            LEFT JOIN video_topics ON video_topics.topic_id = topics.id
            GROUP BY topics.id, topics.name, topics.description
            ORDER BY topics.name COLLATE NOCASE, topics.id
            """
        ).fetchall()
    return rows


def rename_topic(
    db_path: str | Path,
    *,
    project_name: str,
    current_name: str,
    new_name: str,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        topic_row = cursor.execute(
            """
            SELECT topics.id, topics.project_id
            FROM topics
            JOIN projects ON projects.id = topics.project_id
            WHERE projects.name = ? AND topics.name = ?
            ORDER BY topics.id
            LIMIT 1
            """,
            (project_name, current_name),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"topic not found: {current_name}")
        topic_id, project_id = topic_row[0], topic_row[1]
        try:
            cursor.execute(
                "UPDATE topics SET name = ? WHERE id = ?",
                (new_name, topic_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"topic already exists: {new_name}") from exc
        cursor.execute(
            """
            INSERT INTO topic_renames(project_id, topic_id, old_name, new_name)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, topic_id, current_name, new_name),
        )
        connection.commit()
    return topic_id


def merge_topics(
    db_path: str | Path,
    *,
    project_name: str,
    source_name: str,
    target_name: str,
) -> dict[str, int]:
    """Merge ``source_name`` into ``target_name`` within the project.

    Re-points all `video_topics`, `subtopics`, and `subtopic_suggestion_labels`
    rows from the source topic to the target topic. On collision, target
    wins: colliding source rows are dropped (so the target's existing
    assignment_type / source / confidence / reason are preserved). The
    source topic row is then deleted. Returns a stats dict.
    """
    if source_name == target_name:
        raise ValueError("source and target must differ")
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        rows = cursor.execute(
            """
            SELECT topics.id, topics.name
            FROM topics
            JOIN projects ON projects.id = topics.project_id
            WHERE projects.name = ? AND topics.name IN (?, ?)
            """,
            (project_name, source_name, target_name),
        ).fetchall()
        by_name = {row[1]: row[0] for row in rows}
        if source_name not in by_name:
            raise ValueError(f"topic not found: {source_name}")
        if target_name not in by_name:
            raise ValueError(f"topic not found: {target_name}")
        source_id = by_name[source_name]
        target_id = by_name[target_name]

        dropped_video_topics = cursor.execute(
            """
            DELETE FROM video_topics
            WHERE topic_id = ?
              AND video_id IN (SELECT video_id FROM video_topics WHERE topic_id = ?)
            """,
            (source_id, target_id),
        ).rowcount
        moved_video_topics = cursor.execute(
            "UPDATE video_topics SET topic_id = ? WHERE topic_id = ?",
            (target_id, source_id),
        ).rowcount

        merged_subtopics = 0
        moved_subtopics = 0
        # Find subtopic name collisions between the two topics.
        collisions = cursor.execute(
            """
            SELECT s.id AS source_subtopic_id, t.id AS target_subtopic_id
            FROM subtopics s
            JOIN subtopics t ON t.topic_id = ? AND t.name = s.name
            WHERE s.topic_id = ?
            """,
            (target_id, source_id),
        ).fetchall()
        for source_subtopic_id, target_subtopic_id in collisions:
            cursor.execute(
                """
                DELETE FROM video_subtopics
                WHERE subtopic_id = ?
                  AND video_id IN (SELECT video_id FROM video_subtopics WHERE subtopic_id = ?)
                """,
                (source_subtopic_id, target_subtopic_id),
            )
            cursor.execute(
                "UPDATE video_subtopics SET subtopic_id = ? WHERE subtopic_id = ?",
                (target_subtopic_id, source_subtopic_id),
            )
            cursor.execute("DELETE FROM subtopics WHERE id = ?", (source_subtopic_id,))
            merged_subtopics += 1
        moved_subtopics = cursor.execute(
            "UPDATE subtopics SET topic_id = ? WHERE topic_id = ?",
            (target_id, source_id),
        ).rowcount

        cursor.execute(
            """
            DELETE FROM subtopic_suggestion_labels
            WHERE topic_id = ?
              AND (suggestion_run_id, name) IN (
                  SELECT suggestion_run_id, name
                  FROM subtopic_suggestion_labels
                  WHERE topic_id = ?
              )
            """,
            (source_id, target_id),
        )
        cursor.execute(
            "UPDATE subtopic_suggestion_labels SET topic_id = ? WHERE topic_id = ?",
            (target_id, source_id),
        )

        cursor.execute("DELETE FROM topics WHERE id = ?", (source_id,))
        connection.commit()
    return {
        "target_topic_id": target_id,
        "moved_episode_assignments": moved_video_topics,
        "dropped_episode_collisions": dropped_video_topics,
        "moved_subtopics": moved_subtopics,
        "merged_subtopic_collisions": merged_subtopics,
    }


def split_topic(
    db_path: str | Path,
    *,
    project_name: str,
    source_name: str,
    new_name: str,
    youtube_video_ids: list[str],
) -> dict[str, int | list[str]]:
    """Split selected episodes off ``source_name`` into a new topic ``new_name``.

    Creates ``new_name`` as a fresh topic in the project, then re-points each
    selected `video_topics` row from the source topic to the new topic. Video
    IDs that are unknown or not currently assigned to the source topic are
    skipped and reported in the ``skipped_video_ids`` stat. Any
    `video_subtopics` rows belonging to subtopics under the source topic for
    moved videos are dropped (the new topic has no subtopics yet, so leaving
    them attached to the source's subtopics would orphan them visually).
    """
    if source_name == new_name:
        raise ValueError("source and new name must differ")
    if not youtube_video_ids:
        raise ValueError("youtube_video_ids must not be empty")
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        project_row = cursor.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1",
            (project_name,),
        ).fetchone()
        if project_row is None:
            raise ValueError(f"project not found: {project_name}")
        project_id = project_row[0]
        source_row = cursor.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, source_name),
        ).fetchone()
        if source_row is None:
            raise ValueError(f"topic not found: {source_name}")
        source_id = source_row[0]
        existing_new = cursor.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, new_name),
        ).fetchone()
        if existing_new is not None:
            raise ValueError(f"topic already exists: {new_name}")

        placeholders = ",".join("?" for _ in youtube_video_ids)
        eligible = cursor.execute(
            f"""
            SELECT v.id, v.youtube_video_id
            FROM videos v
            JOIN video_topics vt ON vt.video_id = v.id
            WHERE vt.topic_id = ? AND v.youtube_video_id IN ({placeholders})
            """,
            (source_id, *youtube_video_ids),
        ).fetchall()
        eligible_internal_ids = [row[0] for row in eligible]
        eligible_youtube_ids = {row[1] for row in eligible}
        skipped = [yid for yid in youtube_video_ids if yid not in eligible_youtube_ids]
        if not eligible_internal_ids:
            raise ValueError(
                f"none of the supplied videos are assigned to '{source_name}'"
            )

        cursor.execute(
            "INSERT INTO topics(project_id, name) VALUES (?, ?)",
            (project_id, new_name),
        )
        new_topic_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]

        eligible_placeholders = ",".join("?" for _ in eligible_internal_ids)
        moved = cursor.execute(
            f"""
            UPDATE video_topics SET topic_id = ?
            WHERE topic_id = ? AND video_id IN ({eligible_placeholders})
            """,
            (new_topic_id, source_id, *eligible_internal_ids),
        ).rowcount
        dropped_subtopics = cursor.execute(
            f"""
            DELETE FROM video_subtopics
            WHERE video_id IN ({eligible_placeholders})
              AND subtopic_id IN (SELECT id FROM subtopics WHERE topic_id = ?)
            """,
            (*eligible_internal_ids, source_id),
        ).rowcount

        connection.commit()
    return {
        "new_topic_id": new_topic_id,
        "moved_episode_assignments": moved,
        "dropped_subtopic_assignments": dropped_subtopics,
        "skipped_video_ids": skipped,
    }


def move_episode_subtopic(
    db_path: str | Path,
    *,
    project_name: str,
    topic_name: str,
    youtube_video_id: str,
    target_subtopic_name: str,
) -> dict[str, int | str | None]:
    """Move (or attach) ``youtube_video_id`` to ``target_subtopic_name`` under
    ``topic_name`` within ``project_name``.

    The video must already be assigned to the topic. If it has an existing
    `video_subtopics` row whose subtopic is *under* this topic, that row is
    re-pointed at the target subtopic (no-op when already on the target).
    Otherwise a new row is inserted with ``assignment_source='manual'`` to
    flag this as a curation move.
    """
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        project_row = cursor.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1",
            (project_name,),
        ).fetchone()
        if project_row is None:
            raise ValueError(f"project not found: {project_name}")
        project_id = project_row[0]
        topic_row = cursor.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, topic_name),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"topic not found: {topic_name}")
        topic_id = topic_row[0]
        target_row = cursor.execute(
            "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
            (topic_id, target_subtopic_name),
        ).fetchone()
        if target_row is None:
            raise ValueError(
                f"subtopic not found under '{topic_name}': {target_subtopic_name}"
            )
        target_subtopic_id = target_row[0]
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {youtube_video_id}")
        video_internal_id = video_row[0]
        on_topic = cursor.execute(
            "SELECT 1 FROM video_topics WHERE video_id = ? AND topic_id = ?",
            (video_internal_id, topic_id),
        ).fetchone()
        if on_topic is None:
            raise ValueError(
                f"video '{youtube_video_id}' is not assigned to topic '{topic_name}'"
            )

        existing = cursor.execute(
            """
            SELECT vs.subtopic_id, s.name
            FROM video_subtopics vs
            JOIN subtopics s ON s.id = vs.subtopic_id
            WHERE vs.video_id = ? AND s.topic_id = ?
            ORDER BY vs.subtopic_id
            LIMIT 1
            """,
            (video_internal_id, topic_id),
        ).fetchone()

        moved = 0
        inserted = 0
        previous_subtopic_name: str | None = None
        if existing is None:
            cursor.execute(
                """
                INSERT INTO video_subtopics(video_id, subtopic_id, assignment_source)
                VALUES (?, ?, 'manual')
                """,
                (video_internal_id, target_subtopic_id),
            )
            inserted = 1
        else:
            existing_subtopic_id, previous_subtopic_name = existing
            if existing_subtopic_id != target_subtopic_id:
                cursor.execute(
                    """
                    UPDATE video_subtopics
                    SET subtopic_id = ?, assignment_source = 'manual'
                    WHERE video_id = ? AND subtopic_id = ?
                    """,
                    (target_subtopic_id, video_internal_id, existing_subtopic_id),
                )
                moved = 1
        connection.commit()
    return {
        "moved": moved,
        "inserted": inserted,
        "previous_subtopic_name": previous_subtopic_name,
        "target_subtopic_id": target_subtopic_id,
    }


def mark_assignment_wrong(
    db_path: str | Path,
    *,
    project_name: str,
    topic_name: str,
    youtube_video_id: str,
    subtopic_name: str | None = None,
    reason: str | None = None,
) -> dict[str, int | str | None]:
    """Mark a topic (or subtopic) assignment for ``youtube_video_id`` as wrong.

    When ``subtopic_name`` is None: deletes the matching ``video_topics`` row
    (the video is no longer on this topic). When provided: deletes the matching
    ``video_subtopics`` row (the video is no longer on this subtopic, but
    remains on the parent topic). The action is recorded in
    ``wrong_assignments`` so future code (slice 08) can replay or use it as
    training signal.
    """
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        project_row = cursor.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1",
            (project_name,),
        ).fetchone()
        if project_row is None:
            raise ValueError(f"project not found: {project_name}")
        project_id = project_row[0]
        topic_row = cursor.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, topic_name),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"topic not found: {topic_name}")
        topic_id = topic_row[0]
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {youtube_video_id}")
        video_internal_id = video_row[0]

        subtopic_id: int | None = None
        if subtopic_name is None:
            removed = cursor.execute(
                "DELETE FROM video_topics WHERE video_id = ? AND topic_id = ?",
                (video_internal_id, topic_id),
            ).rowcount
            if removed == 0:
                raise ValueError(
                    f"video '{youtube_video_id}' is not assigned to topic '{topic_name}'"
                )
            cursor.execute(
                "DELETE FROM video_subtopics "
                "WHERE video_id = ? AND subtopic_id IN ("
                "SELECT id FROM subtopics WHERE topic_id = ?)",
                (video_internal_id, topic_id),
            )
        else:
            subtopic_lookup = cursor.execute(
                "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
                (topic_id, subtopic_name),
            ).fetchone()
            if subtopic_lookup is None:
                raise ValueError(
                    f"subtopic not found under '{topic_name}': {subtopic_name}"
                )
            subtopic_id = subtopic_lookup[0]
            removed = cursor.execute(
                "DELETE FROM video_subtopics WHERE video_id = ? AND subtopic_id = ?",
                (video_internal_id, subtopic_id),
            ).rowcount
            if removed == 0:
                raise ValueError(
                    f"video '{youtube_video_id}' is not assigned to subtopic "
                    f"'{subtopic_name}' under '{topic_name}'"
                )

        cursor.execute(
            """
            INSERT INTO wrong_assignments(video_id, topic_id, subtopic_id, reason)
            VALUES (?, ?, ?, ?)
            """,
            (video_internal_id, topic_id, subtopic_id, reason),
        )
        event_id = cursor.lastrowid
        connection.commit()
    return {
        "event_id": event_id,
        "topic_id": topic_id,
        "subtopic_id": subtopic_id,
        "video_id": video_internal_id,
    }


def assign_topic_to_video(
    db_path: str | Path,
    *,
    video_id: str,
    topic_name: str,
    assignment_type: str,
    assignment_source: str = "manual",
) -> None:
    if assignment_type not in {"primary", "secondary"}:
        raise ValueError("assignment_type must be 'primary' or 'secondary'")

    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id, channel_id FROM videos WHERE youtube_video_id = ?",
            (video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {video_id}")

        topic_row = cursor.execute(
            """
            SELECT topics.id
            FROM topics
            JOIN channels ON channels.project_id = topics.project_id
            JOIN videos ON videos.channel_id = channels.id
            WHERE videos.id = ? AND topics.name = ?
            ORDER BY topics.id
            LIMIT 1
            """,
            (video_row[0], topic_name),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"topic not found in video project: {topic_name}")

        cursor.execute(
            """
            INSERT INTO video_topics(video_id, topic_id, assignment_type, assignment_source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id, topic_id) DO UPDATE SET
                assignment_type = excluded.assignment_type,
                assignment_source = excluded.assignment_source
            """,
            (video_row[0], topic_row[0], assignment_type, assignment_source),
        )
        connection.commit()


def get_video_topic_assignments(db_path: str | Path, *, video_id: str) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                videos.youtube_video_id,
                videos.title AS video_title,
                topics.name AS topic_name,
                video_topics.assignment_type,
                video_topics.assignment_source
            FROM videos
            LEFT JOIN video_topics ON video_topics.video_id = videos.id
            LEFT JOIN topics ON topics.id = video_topics.topic_id
            WHERE videos.youtube_video_id = ?
            ORDER BY CASE video_topics.assignment_type WHEN 'primary' THEN 0 ELSE 1 END, topics.name COLLATE NOCASE
            """,
            (video_id,),
        ).fetchall()
    if not rows:
        raise ValueError(f"video not found: {video_id}")
    return rows


def create_subtopic(
    db_path: str | Path,
    *,
    topic_name: str,
    subtopic_name: str,
    description: str | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        topic_row = cursor.execute(
            "SELECT id FROM topics WHERE name = ? ORDER BY id LIMIT 1",
            (topic_name,),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"topic not found: {topic_name}")
        cursor.execute(
            """
            INSERT INTO subtopics(topic_id, name, description)
            VALUES (?, ?, ?)
            ON CONFLICT(topic_id, name) DO UPDATE SET
                description = excluded.description
            """,
            (topic_row[0], subtopic_name, description),
        )
        subtopic_id = cursor.execute(
            "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
            (topic_row[0], subtopic_name),
        ).fetchone()[0]
        connection.commit()
    return subtopic_id


def list_subtopics(db_path: str | Path, *, topic_name: str) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                subtopics.id,
                topics.name AS topic_name,
                subtopics.name,
                subtopics.description,
                COUNT(video_subtopics.video_id) AS assignment_count
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            LEFT JOIN video_subtopics ON video_subtopics.subtopic_id = subtopics.id
            WHERE topics.name = ?
            GROUP BY subtopics.id, topics.name, subtopics.name, subtopics.description
            ORDER BY subtopics.name COLLATE NOCASE, subtopics.id
            """,
            (topic_name,),
        ).fetchall()
    return rows


def rename_subtopic(
    db_path: str | Path,
    *,
    topic_name: str,
    current_name: str,
    new_name: str,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        subtopic_row = cursor.execute(
            """
            SELECT subtopics.id
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            WHERE topics.name = ? AND subtopics.name = ?
            ORDER BY subtopics.id
            LIMIT 1
            """,
            (topic_name, current_name),
        ).fetchone()
        if subtopic_row is None:
            raise ValueError(f"subtopic not found: {current_name}")
        try:
            cursor.execute(
                "UPDATE subtopics SET name = ? WHERE id = ?",
                (new_name, subtopic_row[0]),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"subtopic already exists: {new_name}") from exc
        connection.commit()
    return subtopic_row[0]


def assign_subtopic_to_video(
    db_path: str | Path,
    *,
    video_id: str,
    subtopic_name: str,
    assignment_source: str = "manual",
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id, channel_id FROM videos WHERE youtube_video_id = ?",
            (video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {video_id}")

        subtopic_row = cursor.execute(
            """
            SELECT subtopics.id
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            JOIN channels ON channels.project_id = topics.project_id
            JOIN videos ON videos.channel_id = channels.id
            WHERE videos.id = ? AND subtopics.name = ?
            ORDER BY subtopics.id
            LIMIT 1
            """,
            (video_row[0], subtopic_name),
        ).fetchone()
        if subtopic_row is None:
            raise ValueError(f"subtopic not found in video project: {subtopic_name}")

        cursor.execute(
            """
            INSERT INTO video_subtopics(video_id, subtopic_id, assignment_source)
            VALUES (?, ?, ?)
            ON CONFLICT(video_id, subtopic_id) DO UPDATE SET
                assignment_source = excluded.assignment_source
            """,
            (video_row[0], subtopic_row[0], assignment_source),
        )
        connection.commit()


def get_video_subtopic_assignments(db_path: str | Path, *, video_id: str) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                videos.youtube_video_id,
                videos.title AS video_title,
                topics.name AS topic_name,
                subtopics.name AS subtopic_name,
                video_subtopics.assignment_source
            FROM videos
            LEFT JOIN video_subtopics ON video_subtopics.video_id = videos.id
            LEFT JOIN subtopics ON subtopics.id = video_subtopics.subtopic_id
            LEFT JOIN topics ON topics.id = subtopics.topic_id
            WHERE videos.youtube_video_id = ?
            ORDER BY topics.name COLLATE NOCASE, subtopics.name COLLATE NOCASE
            """,
            (video_id,),
        ).fetchall()
    if not rows:
        raise ValueError(f"video not found: {video_id}")
    return rows


def remove_topic_from_video(
    db_path: str | Path,
    *,
    video_id: str,
    topic_name: str,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {video_id}")

        topic_row = cursor.execute(
            """
            SELECT topics.id
            FROM topics
            JOIN channels ON channels.project_id = topics.project_id
            JOIN videos ON videos.channel_id = channels.id
            WHERE videos.id = ? AND topics.name = ?
            ORDER BY topics.id
            LIMIT 1
            """,
            (video_row[0], topic_name),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"topic not found in video project: {topic_name}")

        deleted = cursor.execute(
            "DELETE FROM video_topics WHERE video_id = ? AND topic_id = ?",
            (video_row[0], topic_row[0]),
        ).rowcount
        if deleted == 0:
            raise ValueError(f"video {video_id} is not assigned to topic: {topic_name}")
        connection.commit()


def remove_subtopic_from_video(
    db_path: str | Path,
    *,
    video_id: str,
    subtopic_name: str,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {video_id}")

        subtopic_row = cursor.execute(
            """
            SELECT subtopics.id
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            JOIN channels ON channels.project_id = topics.project_id
            JOIN videos ON videos.channel_id = channels.id
            WHERE videos.id = ? AND subtopics.name = ?
            ORDER BY subtopics.id
            LIMIT 1
            """,
            (video_row[0], subtopic_name),
        ).fetchone()
        if subtopic_row is None:
            raise ValueError(f"subtopic not found in video project: {subtopic_name}")

        deleted = cursor.execute(
            "DELETE FROM video_subtopics WHERE video_id = ? AND subtopic_id = ?",
            (video_row[0], subtopic_row[0]),
        ).rowcount
        if deleted == 0:
            raise ValueError(f"video {video_id} is not assigned to subtopic: {subtopic_name}")
        connection.commit()


def create_comparison_group(
    db_path: str | Path,
    *,
    subtopic_name: str,
    group_name: str,
    description: str | None = None,
    target_size: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        subtopic_row = cursor.execute(
            "SELECT id FROM subtopics WHERE name = ? ORDER BY id LIMIT 1",
            (subtopic_name,),
        ).fetchone()
        if subtopic_row is None:
            raise ValueError(f"subtopic not found: {subtopic_name}")
        cursor.execute(
            """
            INSERT INTO comparison_groups(subtopic_id, name, description, target_size)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(subtopic_id, name) DO UPDATE SET
                description = excluded.description,
                target_size = excluded.target_size
            """,
            (subtopic_row[0], group_name, description, target_size),
        )
        group_id = cursor.execute(
            "SELECT id FROM comparison_groups WHERE subtopic_id = ? AND name = ?",
            (subtopic_row[0], group_name),
        ).fetchone()[0]
        connection.commit()
    return group_id


def list_comparison_groups(db_path: str | Path, *, subtopic_name: str) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                comparison_groups.id,
                topics.name AS topic_name,
                subtopics.name AS subtopic_name,
                comparison_groups.name,
                comparison_groups.description,
                comparison_groups.target_size,
                COUNT(comparison_group_videos.video_id) AS member_count
            FROM comparison_groups
            JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
            JOIN topics ON topics.id = subtopics.topic_id
            LEFT JOIN comparison_group_videos
                ON comparison_group_videos.comparison_group_id = comparison_groups.id
            WHERE subtopics.name = ?
            GROUP BY
                comparison_groups.id,
                topics.name,
                subtopics.name,
                comparison_groups.name,
                comparison_groups.description,
                comparison_groups.target_size
            ORDER BY comparison_groups.name COLLATE NOCASE, comparison_groups.id
            """,
            (subtopic_name,),
        ).fetchall()
    return rows


def rename_comparison_group(
    db_path: str | Path,
    *,
    subtopic_name: str,
    current_name: str,
    new_name: str,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        group_row = cursor.execute(
            """
            SELECT comparison_groups.id
            FROM comparison_groups
            JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
            WHERE subtopics.name = ? AND comparison_groups.name = ?
            ORDER BY comparison_groups.id
            LIMIT 1
            """,
            (subtopic_name, current_name),
        ).fetchone()
        if group_row is None:
            raise ValueError(f"comparison group not found: {current_name}")
        try:
            cursor.execute(
                "UPDATE comparison_groups SET name = ? WHERE id = ?",
                (new_name, group_row[0]),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"comparison group already exists: {new_name}") from exc
        connection.commit()
    return group_row[0]


def add_video_to_comparison_group(
    db_path: str | Path,
    *,
    video_id: str,
    group_name: str,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {video_id}")

        group_row = cursor.execute(
            "SELECT id FROM comparison_groups WHERE name = ? ORDER BY id LIMIT 1",
            (group_name,),
        ).fetchone()
        if group_row is None:
            raise ValueError(f"comparison group not found: {group_name}")

        cursor.execute(
            """
            INSERT INTO comparison_group_videos(comparison_group_id, video_id)
            VALUES (?, ?)
            ON CONFLICT(comparison_group_id, video_id) DO NOTHING
            """,
            (group_row[0], video_row[0]),
        )
        connection.commit()


def move_video_between_comparison_groups(
    db_path: str | Path,
    *,
    video_id: str,
    from_group_name: str,
    to_group_name: str,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()

        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {video_id}")
        video_db_id = video_row[0]

        from_group_row = cursor.execute(
            "SELECT id FROM comparison_groups WHERE name = ? ORDER BY id LIMIT 1",
            (from_group_name,),
        ).fetchone()
        if from_group_row is None:
            raise ValueError(f"comparison group not found: {from_group_name}")

        to_group_row = cursor.execute(
            "SELECT id FROM comparison_groups WHERE name = ? ORDER BY id LIMIT 1",
            (to_group_name,),
        ).fetchone()
        if to_group_row is None:
            raise ValueError(f"comparison group not found: {to_group_name}")

        membership_row = cursor.execute(
            """
            SELECT 1
            FROM comparison_group_videos
            WHERE comparison_group_id = ? AND video_id = ?
            """,
            (from_group_row[0], video_db_id),
        ).fetchone()
        if membership_row is None:
            raise ValueError(f"video {video_id} is not in comparison group: {from_group_name}")

        try:
            connection.execute("BEGIN")
            cursor.execute(
                """
                INSERT INTO comparison_group_videos(comparison_group_id, video_id)
                VALUES (?, ?)
                ON CONFLICT(comparison_group_id, video_id) DO NOTHING
                """,
                (to_group_row[0], video_db_id),
            )
            cursor.execute(
                "DELETE FROM comparison_group_videos WHERE comparison_group_id = ? AND video_id = ?",
                (from_group_row[0], video_db_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def remove_video_from_comparison_group(
    db_path: str | Path,
    *,
    video_id: str,
    group_name: str,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {video_id}")

        group_row = cursor.execute(
            "SELECT id FROM comparison_groups WHERE name = ? ORDER BY id LIMIT 1",
            (group_name,),
        ).fetchone()
        if group_row is None:
            raise ValueError(f"comparison group not found: {group_name}")

        deleted = cursor.execute(
            "DELETE FROM comparison_group_videos WHERE comparison_group_id = ? AND video_id = ?",
            (group_row[0], video_row[0]),
        ).rowcount
        if deleted == 0:
            raise ValueError(f"video {video_id} is not in comparison group: {group_name}")
        connection.commit()


def get_comparison_group_details(db_path: str | Path, *, group_name: str) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                comparison_groups.name AS group_name,
                comparison_groups.description,
                comparison_groups.target_size,
                topics.name AS topic_name,
                subtopics.name AS subtopic_name,
                videos.youtube_video_id,
                videos.title AS video_title,
                videos.published_at
            FROM comparison_groups
            JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
            JOIN topics ON topics.id = subtopics.topic_id
            LEFT JOIN comparison_group_videos
                ON comparison_group_videos.comparison_group_id = comparison_groups.id
            LEFT JOIN videos ON videos.id = comparison_group_videos.video_id
            WHERE comparison_groups.name = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """,
            (group_name,),
        ).fetchall()
    if not rows:
        raise ValueError(f"comparison group not found: {group_name}")
    return rows


def resolve_comparison_group(db_path: str | Path, *, group_name: str | None = None, group_id: int | None = None) -> sqlite3.Row:
    if bool(group_name) == bool(group_id):
        raise ValueError("provide exactly one of group_name or group_id")

    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        if group_id is not None:
            row = connection.execute(
                """
                SELECT comparison_groups.id, comparison_groups.name
                FROM comparison_groups
                WHERE comparison_groups.id = ?
                """,
                (group_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT comparison_groups.id, comparison_groups.name
                FROM comparison_groups
                WHERE comparison_groups.name = ?
                ORDER BY comparison_groups.id
                LIMIT 1
                """,
                (group_name,),
            ).fetchone()
    if row is None:
        raise ValueError("comparison group not found")
    return row


def list_group_videos(db_path: str | Path, *, group_id: int) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT videos.id, videos.youtube_video_id, videos.title
            FROM comparison_group_videos
            JOIN videos ON videos.id = comparison_group_videos.video_id
            WHERE comparison_group_videos.comparison_group_id = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """,
            (group_id,),
        ).fetchall()


def upsert_video_transcript(
    db_path: str | Path,
    *,
    youtube_video_id: str,
    transcript: TranscriptRecord,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {youtube_video_id}")
        cursor.execute(
            """
            INSERT INTO video_transcripts(video_id, transcript_status, transcript_source, language_code, transcript_text, transcript_detail, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_id) DO UPDATE SET
                transcript_status = excluded.transcript_status,
                transcript_source = excluded.transcript_source,
                language_code = excluded.language_code,
                transcript_text = excluded.transcript_text,
                transcript_detail = excluded.transcript_detail,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (
                video_row[0],
                transcript.status,
                transcript.source,
                transcript.language_code,
                transcript.text,
                transcript.detail,
            ),
        )
        connection.commit()


def get_group_transcripts_for_processing(db_path: str | Path, *, group_id: int) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT
                videos.id AS video_id,
                videos.youtube_video_id,
                videos.title AS video_title,
                video_transcripts.transcript_status,
                video_transcripts.transcript_source,
                video_transcripts.language_code,
                video_transcripts.transcript_text,
                video_transcripts.transcript_detail
            FROM comparison_group_videos
            JOIN videos ON videos.id = comparison_group_videos.video_id
            LEFT JOIN video_transcripts ON video_transcripts.video_id = videos.id
            WHERE comparison_group_videos.comparison_group_id = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """,
            (group_id,),
        ).fetchall()


def upsert_processed_video_artifacts(
    db_path: str | Path,
    *,
    youtube_video_id: str,
    artifact: ProcessedVideoArtifact,
    chunks: list[TranscriptChunk],
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id FROM videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {youtube_video_id}")
        video_db_id = video_row[0]
        cursor.execute(
            """
            INSERT INTO processed_videos(
                video_id, processing_status, summary_text, transcript_char_count, chunk_count, processing_detail, processed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_id) DO UPDATE SET
                processing_status = excluded.processing_status,
                summary_text = excluded.summary_text,
                transcript_char_count = excluded.transcript_char_count,
                chunk_count = excluded.chunk_count,
                processing_detail = excluded.processing_detail,
                processed_at = CURRENT_TIMESTAMP
            """,
            (
                video_db_id,
                artifact.processing_status,
                artifact.summary_text,
                artifact.transcript_char_count,
                artifact.chunk_count,
                artifact.detail,
            ),
        )
        cursor.execute("DELETE FROM processed_video_chunks WHERE video_id = ?", (video_db_id,))
        cursor.executemany(
            """
            INSERT INTO processed_video_chunks(video_id, chunk_index, chunk_text, start_char, end_char)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (video_db_id, chunk.chunk_index, chunk.chunk_text, chunk.start_char, chunk.end_char)
                for chunk in chunks
            ],
        )
        connection.commit()


def upsert_group_analysis(
    db_path: str | Path,
    *,
    group_id: int,
    artifact: GroupAnalysisArtifact,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO group_analyses(
                comparison_group_id,
                analysis_version,
                processed_video_count,
                skipped_video_count,
                shared_themes_json,
                repeated_recommendations_json,
                notable_differences_json,
                analysis_detail,
                analyzed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(comparison_group_id) DO UPDATE SET
                analysis_version = excluded.analysis_version,
                processed_video_count = excluded.processed_video_count,
                skipped_video_count = excluded.skipped_video_count,
                shared_themes_json = excluded.shared_themes_json,
                repeated_recommendations_json = excluded.repeated_recommendations_json,
                notable_differences_json = excluded.notable_differences_json,
                analysis_detail = excluded.analysis_detail,
                analyzed_at = CURRENT_TIMESTAMP
            """,
            (
                group_id,
                artifact.analysis_version,
                artifact.processed_video_count,
                artifact.skipped_video_count,
                artifact.shared_themes_json,
                artifact.repeated_recommendations_json,
                artifact.notable_differences_json,
                artifact.analysis_detail,
            ),
        )
        connection.commit()


def get_group_analysis(db_path: str | Path, *, group_id: int) -> sqlite3.Row | None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT

                comparison_groups.id AS group_id,
                comparison_groups.name AS group_name,
                group_analyses.analysis_version,
                group_analyses.processed_video_count,
                group_analyses.skipped_video_count,
                group_analyses.shared_themes_json,
                group_analyses.repeated_recommendations_json,
                group_analyses.notable_differences_json,
                group_analyses.analysis_detail,
                group_analyses.analyzed_at
            FROM comparison_groups
            LEFT JOIN group_analyses ON group_analyses.comparison_group_id = comparison_groups.id
            WHERE comparison_groups.id = ?
            """,
            (group_id,),
        ).fetchone()


def get_group_processed_video_results(db_path: str | Path, *, group_id: int) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT
                comparison_groups.id AS group_id,
                comparison_groups.name AS group_name,
                videos.youtube_video_id,
                videos.title AS video_title,
                videos.published_at,
                processed_videos.processing_status,
                processed_videos.summary_text,
                processed_videos.chunk_count,
                processed_videos.transcript_char_count,
                processed_videos.processing_detail,
                processed_videos.processed_at
            FROM comparison_groups
            JOIN comparison_group_videos ON comparison_group_videos.comparison_group_id = comparison_groups.id
            JOIN videos ON videos.id = comparison_group_videos.video_id
            LEFT JOIN processed_videos ON processed_videos.video_id = videos.id
            WHERE comparison_groups.id = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """,
            (group_id,),
        ).fetchall()


def get_group_transcript_statuses(db_path: str | Path, *, group_id: int) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT
                comparison_groups.id AS group_id,
                comparison_groups.name AS group_name,
                videos.youtube_video_id,
                videos.title AS video_title,
                video_transcripts.transcript_status,
                video_transcripts.transcript_source,
                video_transcripts.language_code,
                video_transcripts.transcript_detail,
                LENGTH(video_transcripts.transcript_text) AS transcript_chars,
                video_transcripts.fetched_at
            FROM comparison_groups
            JOIN comparison_group_videos ON comparison_group_videos.comparison_group_id = comparison_groups.id
            JOIN videos ON videos.id = comparison_group_videos.video_id
            LEFT JOIN video_transcripts ON video_transcripts.video_id = videos.id
            WHERE comparison_groups.id = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """,
            (group_id,),
        ).fetchall()


def get_stored_channels(db_path: str | Path) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT youtube_channel_id, title, handle, description, published_at, thumbnail_url, last_refreshed_at
            FROM channels
            ORDER BY id
            """
        ).fetchall()
    return rows


def record_markdown_export(
    db_path: str | Path,
    *,
    group_id: int,
    export_kind: str,
    relative_path: str,
    source_updated_at: str | None,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO markdown_exports(comparison_group_id, export_kind, relative_path, source_updated_at, exported_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(comparison_group_id, export_kind, relative_path) DO UPDATE SET
                source_updated_at = excluded.source_updated_at,
                exported_at = CURRENT_TIMESTAMP
            """,
            (group_id, export_kind, relative_path, source_updated_at),
        )
        connection.commit()


def get_markdown_exports(db_path: str | Path, *, group_id: int) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT comparison_group_id, export_kind, relative_path, source_updated_at, exported_at
            FROM markdown_exports
            WHERE comparison_group_id = ?
            ORDER BY export_kind, relative_path
            """,
            (group_id,),
        ).fetchall()


def get_video_summary(db_path: str | Path, *, sample_limit: int = 5) -> tuple[int, list[sqlite3.Row]]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        total = connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        rows = connection.execute(
            """
            SELECT youtube_video_id, title, published_at
            FROM videos
            ORDER BY published_at DESC, id DESC
            LIMIT ?
            """,
            (sample_limit,),
        ).fetchall()
    return total, rows


def list_videos_for_topic_suggestions(db_path: str | Path, *, limit: int | None = None) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        sql = (
            """
            SELECT videos.youtube_video_id, videos.title, videos.description
            FROM videos
            ORDER BY videos.published_at DESC, videos.id DESC
            """
        )
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += "\nLIMIT ?"
            params = (limit,)
        return connection.execute(sql, params).fetchall()


def list_videos_for_subtopic_suggestions(
    db_path: str | Path,
    *,
    topic_name: str,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        sql = (
            """
            SELECT DISTINCT videos.youtube_video_id, videos.title, videos.description
            FROM videos
            JOIN video_topics ON video_topics.video_id = videos.id
            JOIN topics ON topics.id = video_topics.topic_id
            WHERE topics.name = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """
        )
        params: list[object] = [topic_name]
        if limit is not None:
            sql += "\nLIMIT ?"
            params.append(limit)
        return connection.execute(sql, tuple(params)).fetchall()


def list_approved_subtopics_for_topic(db_path: str | Path, *, topic_name: str) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT subtopics.name, subtopics.description
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            WHERE topics.name = ?
            ORDER BY subtopics.name COLLATE NOCASE, subtopics.id
            """,
            (topic_name,),
        ).fetchall()


def list_approved_comparison_groups_for_subtopic(db_path: str | Path, *, subtopic_name: str) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT
                comparison_groups.name,
                comparison_groups.description,
                COUNT(comparison_group_videos.video_id) AS member_count
            FROM comparison_groups
            JOIN subtopics ON subtopics.id = comparison_groups.subtopic_id
            LEFT JOIN comparison_group_videos
                ON comparison_group_videos.comparison_group_id = comparison_groups.id
            WHERE subtopics.name = ?
            GROUP BY comparison_groups.id, comparison_groups.name, comparison_groups.description
            ORDER BY comparison_groups.name COLLATE NOCASE, comparison_groups.id
            """,
            (subtopic_name,),
        ).fetchall()


def list_videos_for_comparison_group_suggestions(
    db_path: str | Path,
    *,
    subtopic_name: str,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        sql = (
            """
            SELECT DISTINCT
                videos.youtube_video_id,
                videos.title,
                videos.description,
                topics.name AS topic_name,
                subtopics.name AS subtopic_name
            FROM videos
            JOIN video_subtopics ON video_subtopics.video_id = videos.id
            JOIN subtopics ON subtopics.id = video_subtopics.subtopic_id
            JOIN topics ON topics.id = subtopics.topic_id
            WHERE subtopics.name = ?
            ORDER BY videos.published_at DESC, videos.id DESC
            """
        )
        params: list[object] = [subtopic_name]
        if limit is not None:
            sql += "\nLIMIT ?"
            params.append(limit)
        return connection.execute(sql, tuple(params)).fetchall()


def create_comparison_group_suggestion_run(
    db_path: str | Path,
    *,
    subtopic_name: str,
    model_name: str,
    status: str = "success",
) -> int:
    return create_topic_suggestion_run(db_path, model_name=f"comparison-group:{subtopic_name}:{model_name}", status=status)


def store_video_comparison_group_suggestion(
    db_path: str | Path,
    *,
    run_id: int,
    subtopic_name: str,
    suggestion: VideoComparisonGroupSuggestion,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id, channel_id FROM videos WHERE youtube_video_id = ?",
            (suggestion.youtube_video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {suggestion.youtube_video_id}")

        subtopic_row = cursor.execute(
            """
            SELECT subtopics.id, topics.name AS topic_name
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            WHERE subtopics.name = ?
            ORDER BY subtopics.id
            LIMIT 1
            """,
            (subtopic_name,),
        ).fetchone()
        if subtopic_row is None:
            raise ValueError(f"subtopic not found: {subtopic_name}")

        project_id = cursor.execute(
            "SELECT project_id FROM channels WHERE id = ?",
            (video_row[1],),
        ).fetchone()[0]

        cursor.execute(
            """
            INSERT INTO comparison_group_suggestion_labels(project_id, subtopic_id, suggestion_run_id, name, description)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subtopic_id, suggestion_run_id, name) DO UPDATE SET
                description = excluded.description,
                status = 'pending',
                reviewed_at = NULL
            """,
            (
                project_id,
                subtopic_row[0],
                run_id,
                suggestion.primary_comparison_group.label,
                None,
            ),
        )
        label_row = cursor.execute(
            """
            SELECT id FROM comparison_group_suggestion_labels
            WHERE subtopic_id = ? AND suggestion_run_id = ? AND name = ?
            """,
            (subtopic_row[0], run_id, suggestion.primary_comparison_group.label),
        ).fetchone()

        cursor.execute(
            """
            INSERT INTO video_comparison_group_suggestions(
                run_id, video_id, suggestion_label_id, rationale, reuse_existing, raw_response_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, video_id) DO UPDATE SET
                suggestion_label_id = excluded.suggestion_label_id,
                rationale = excluded.rationale,
                reuse_existing = excluded.reuse_existing,
                raw_response_json = excluded.raw_response_json,
                created_at = CURRENT_TIMESTAMP,
                reviewed_at = NULL
            """,
            (
                run_id,
                video_row[0],
                label_row[0],
                suggestion.primary_comparison_group.rationale,
                1 if suggestion.primary_comparison_group.reuse_existing else 0,
                suggestion.raw_response_json,
            ),
        )
        connection.commit()
        return 1


def list_video_comparison_group_suggestions(
    db_path: str | Path,
    *,
    subtopic_name: str | None = None,
    status: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        filters = []
        params: list[object] = []
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is not None:
            filters.append("video_comparison_group_suggestions.run_id = ?")
            params.append(resolved_run_id)
        if subtopic_name is not None:
            filters.append("subtopics.name = ?")
            params.append(subtopic_name)
        if status is not None:
            filters.append("comparison_group_suggestion_labels.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        return connection.execute(
            f"""
            SELECT
                video_comparison_group_suggestions.id,
                video_comparison_group_suggestions.run_id,
                videos.youtube_video_id,
                videos.title AS video_title,
                topics.name AS topic_name,
                subtopics.name AS subtopic_name,
                comparison_group_suggestion_labels.name AS suggested_label,
                comparison_group_suggestion_labels.status AS label_status,
                video_comparison_group_suggestions.reuse_existing,
                video_comparison_group_suggestions.rationale,
                video_comparison_group_suggestions.created_at,
                video_comparison_group_suggestions.reviewed_at
            FROM video_comparison_group_suggestions
            JOIN videos ON videos.id = video_comparison_group_suggestions.video_id
            JOIN comparison_group_suggestion_labels
                ON comparison_group_suggestion_labels.id = video_comparison_group_suggestions.suggestion_label_id
            JOIN subtopics ON subtopics.id = comparison_group_suggestion_labels.subtopic_id
            JOIN topics ON topics.id = subtopics.topic_id
            {where_sql}
            ORDER BY subtopics.name COLLATE NOCASE, videos.published_at DESC, videos.id DESC
            """,
            params,
        ).fetchall()


def summarize_comparison_group_suggestion_labels(
    db_path: str | Path,
    *,
    subtopic_name: str | None = None,
    status: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        filters = []
        params: list[object] = []
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is not None:
            filters.append("video_comparison_group_suggestions.run_id = ?")
            params.append(resolved_run_id)
        if subtopic_name is not None:
            filters.append("subtopics.name = ?")
            params.append(subtopic_name)
        if status is not None:
            filters.append("comparison_group_suggestion_labels.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        return connection.execute(
            f"""
            SELECT
                video_comparison_group_suggestions.run_id AS run_id,
                topics.name AS topic_name,
                subtopics.name AS subtopic_name,
                comparison_group_suggestion_labels.name,
                comparison_group_suggestion_labels.status,
                COUNT(video_comparison_group_suggestions.id) AS suggestion_count,
                SUM(CASE WHEN video_comparison_group_suggestions.reuse_existing = 1 THEN 1 ELSE 0 END) AS reuse_existing_count,
                MIN(video_comparison_group_suggestions.created_at) AS first_suggested_at,
                MAX(video_comparison_group_suggestions.created_at) AS last_suggested_at
            FROM video_comparison_group_suggestions
            JOIN comparison_group_suggestion_labels
                ON comparison_group_suggestion_labels.id = video_comparison_group_suggestions.suggestion_label_id
            JOIN subtopics ON subtopics.id = comparison_group_suggestion_labels.subtopic_id
            JOIN topics ON topics.id = subtopics.topic_id
            {where_sql}
            GROUP BY video_comparison_group_suggestions.run_id, topics.name, subtopics.name, comparison_group_suggestion_labels.name, comparison_group_suggestion_labels.status
            ORDER BY subtopics.name COLLATE NOCASE, comparison_group_suggestion_labels.status, suggestion_count DESC, comparison_group_suggestion_labels.name COLLATE NOCASE
            """,
            params,
        ).fetchall()


def get_comparison_group_suggestion_review_rows(
    db_path: str | Path,
    *,
    subtopic_name: str,
    run_id: int | None = None,
    status: str = "pending",
    sample_limit: int = 3,
) -> list[sqlite3.Row]:
    if sample_limit <= 0:
        raise ValueError("sample_limit must be greater than 0")

    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            return []
        return connection.execute(
            """
            WITH scoped_suggestions AS (
                SELECT
                    video_comparison_group_suggestions.run_id,
                    video_comparison_group_suggestions.suggestion_label_id,
                    video_comparison_group_suggestions.video_id
                FROM video_comparison_group_suggestions
                JOIN comparison_group_suggestion_labels
                    ON comparison_group_suggestion_labels.id = video_comparison_group_suggestions.suggestion_label_id
                JOIN subtopics ON subtopics.id = comparison_group_suggestion_labels.subtopic_id
                WHERE video_comparison_group_suggestions.run_id = ?
                  AND subtopics.name = ?
                  AND comparison_group_suggestion_labels.status = ?
            ),
            label_counts AS (
                SELECT
                    scoped_suggestions.suggestion_label_id,
                    COUNT(*) AS video_count
                FROM scoped_suggestions
                GROUP BY scoped_suggestions.suggestion_label_id
            ),
            sampled_videos AS (
                SELECT
                    scoped_suggestions.suggestion_label_id,
                    videos.youtube_video_id,
                    videos.title AS video_title,
                    ROW_NUMBER() OVER (
                        PARTITION BY scoped_suggestions.suggestion_label_id
                        ORDER BY videos.published_at DESC, videos.id DESC
                    ) AS sample_rank
                FROM scoped_suggestions
                JOIN videos ON videos.id = scoped_suggestions.video_id
            )
            SELECT
                ? AS run_id,
                topics.name AS topic_name,
                subtopics.name AS subtopic_name,
                comparison_group_suggestion_labels.name,
                comparison_group_suggestion_labels.status,
                label_counts.video_count,
                EXISTS (
                    SELECT 1
                    FROM comparison_groups
                    WHERE comparison_groups.subtopic_id = comparison_group_suggestion_labels.subtopic_id
                      AND comparison_groups.name = comparison_group_suggestion_labels.name
                ) AS approved_group_exists,
                sampled_videos.youtube_video_id,
                sampled_videos.video_title,
                sampled_videos.sample_rank
            FROM label_counts
            JOIN comparison_group_suggestion_labels
                ON comparison_group_suggestion_labels.id = label_counts.suggestion_label_id
            JOIN subtopics ON subtopics.id = comparison_group_suggestion_labels.subtopic_id
            JOIN topics ON topics.id = subtopics.topic_id
            LEFT JOIN sampled_videos
                ON sampled_videos.suggestion_label_id = label_counts.suggestion_label_id
               AND sampled_videos.sample_rank <= ?
            ORDER BY label_counts.video_count DESC, comparison_group_suggestion_labels.name COLLATE NOCASE, sampled_videos.sample_rank
            """,
            (resolved_run_id, subtopic_name, status, resolved_run_id, sample_limit),
        ).fetchall()


def _get_comparison_group_suggestion_label_row(
    connection: sqlite3.Connection,
    *,
    suggested_label: str,
    subtopic_name: str,
    run_id: int | None = None,
    status: str | None = None,
) -> sqlite3.Row | None:
    connection.row_factory = sqlite3.Row
    filters = ["comparison_group_suggestion_labels.name = ?", "subtopics.name = ?"]
    params: list[object] = [suggested_label, subtopic_name]
    resolved_run_id = _resolve_topic_suggestion_run_id(connection, run_id)
    if resolved_run_id is not None:
        filters.append("video_comparison_group_suggestions.run_id = ?")
        params.append(resolved_run_id)
    if status is not None:
        filters.append("comparison_group_suggestion_labels.status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(filters)}"
    return connection.execute(
        f"""
        SELECT
            comparison_group_suggestion_labels.id,
            comparison_group_suggestion_labels.project_id,
            comparison_group_suggestion_labels.subtopic_id,
            comparison_group_suggestion_labels.suggestion_run_id,
            comparison_group_suggestion_labels.name,
            comparison_group_suggestion_labels.status,
            video_comparison_group_suggestions.run_id AS matched_run_id
        FROM video_comparison_group_suggestions
        JOIN comparison_group_suggestion_labels
            ON comparison_group_suggestion_labels.id = video_comparison_group_suggestions.suggestion_label_id
        JOIN subtopics ON subtopics.id = comparison_group_suggestion_labels.subtopic_id
        {where_sql}
        GROUP BY
            comparison_group_suggestion_labels.id,
            comparison_group_suggestion_labels.project_id,
            comparison_group_suggestion_labels.subtopic_id,
            comparison_group_suggestion_labels.suggestion_run_id,
            comparison_group_suggestion_labels.name,
            comparison_group_suggestion_labels.status,
            video_comparison_group_suggestions.run_id
        ORDER BY video_comparison_group_suggestions.run_id DESC, comparison_group_suggestion_labels.id
        LIMIT 1
        """,
        params,
    ).fetchone()


def approve_comparison_group_suggestion_label(
    db_path: str | Path,
    *,
    subtopic_name: str,
    suggested_label: str,
    approved_name: str | None = None,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_comparison_group_suggestion_label_row(
            connection,
            suggested_label=suggested_label,
            subtopic_name=subtopic_name,
            run_id=run_id,
        )
        if row is None:
            raise ValueError(f"suggested comparison-group label not found: {suggested_label}")
        final_name = approved_name or suggested_label
        cursor.execute(
            """
            INSERT INTO comparison_groups(subtopic_id, name)
            VALUES (?, ?)
            ON CONFLICT(subtopic_id, name) DO NOTHING
            """,
            (row["subtopic_id"], final_name),
        )
        group_id = cursor.execute(
            "SELECT id FROM comparison_groups WHERE subtopic_id = ? AND name = ?",
            (row["subtopic_id"], final_name),
        ).fetchone()[0]
        _upsert_comparison_group_suggestion_label_review_state(
            cursor,
            source_row=row,
            final_name=final_name,
            final_status="approved",
        )
        connection.commit()
        return group_id


def reject_comparison_group_suggestion_label(
    db_path: str | Path,
    *,
    subtopic_name: str,
    suggested_label: str,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_comparison_group_suggestion_label_row(
            connection,
            suggested_label=suggested_label,
            subtopic_name=subtopic_name,
            run_id=run_id,
        )
        if row is None:
            raise ValueError(f"suggested comparison-group label not found: {suggested_label}")
        updated = cursor.execute(
            """
            UPDATE comparison_group_suggestion_labels
            SET status = 'rejected',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        ).rowcount
        cursor.execute(
            """
            UPDATE video_comparison_group_suggestions
            SET reviewed_at = CURRENT_TIMESTAMP
            WHERE suggestion_label_id = ?
            """,
            (row["id"],),
        )
        connection.commit()
        return updated


def rename_comparison_group_suggestion_label(
    db_path: str | Path,
    *,
    subtopic_name: str,
    current_name: str,
    new_name: str,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_comparison_group_suggestion_label_row(
            connection,
            suggested_label=current_name,
            subtopic_name=subtopic_name,
            run_id=run_id,
        )
        if row is None:
            raise ValueError(f"suggested comparison-group label not found: {current_name}")
        label_id = _upsert_comparison_group_suggestion_label_review_state(
            cursor,
            source_row=row,
            final_name=new_name,
            final_status="pending",
        )
        connection.commit()
        return label_id


def _upsert_comparison_group_suggestion_label_review_state(
    cursor: sqlite3.Cursor,
    *,
    source_row: sqlite3.Row,
    final_name: str,
    final_status: str,
) -> int:
    target_row = cursor.execute(
        """
        SELECT id
        FROM comparison_group_suggestion_labels
        WHERE subtopic_id = ?
          AND suggestion_run_id = ?
          AND name = ?
          AND id != ?
        ORDER BY id
        LIMIT 1
        """,
        (source_row["subtopic_id"], source_row["suggestion_run_id"], final_name, source_row["id"]),
    ).fetchone()
    reviewed_at_sql = "CURRENT_TIMESTAMP" if final_status == "approved" else "NULL"

    if target_row is None:
        cursor.execute(
            f"""
            UPDATE comparison_group_suggestion_labels
            SET status = ?,
                name = ?,
                reviewed_at = {reviewed_at_sql}
            WHERE id = ?
            """,
            (final_status, final_name, source_row["id"]),
        )
        cursor.execute(
            f"""
            UPDATE video_comparison_group_suggestions
            SET reviewed_at = {reviewed_at_sql}
            WHERE suggestion_label_id = ?
            """,
            (source_row["id"],),
        )
        return int(source_row["id"])

    target_id = int(target_row[0])
    cursor.execute(
        """
        UPDATE video_comparison_group_suggestions
        SET suggestion_label_id = ?
        WHERE suggestion_label_id = ?
        """,
        (target_id, source_row["id"]),
    )
    cursor.execute(
        f"""
        UPDATE comparison_group_suggestion_labels
        SET status = ?,
            reviewed_at = {reviewed_at_sql}
        WHERE id = ?
        """,
        (final_status, target_id),
    )
    cursor.execute(
        f"""
        UPDATE video_comparison_group_suggestions
        SET reviewed_at = {reviewed_at_sql}
        WHERE suggestion_label_id = ?
        """,
        (target_id,),
    )
    cursor.execute(
        """
        UPDATE comparison_group_suggestion_labels
        SET status = 'superseded',
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (source_row["id"],),
    )
    return target_id


def create_subtopic_suggestion_run(
    db_path: str | Path,
    *,
    topic_name: str,
    model_name: str,
    status: str = "success",
) -> int:
    return create_topic_suggestion_run(db_path, model_name=f"subtopic:{topic_name}:{model_name}", status=status)


def store_video_subtopic_suggestion(
    db_path: str | Path,
    *,
    run_id: int,
    topic_name: str,
    suggestion: VideoSubtopicSuggestion,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id, channel_id FROM videos WHERE youtube_video_id = ?",
            (suggestion.youtube_video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {suggestion.youtube_video_id}")

        topic_row = cursor.execute(
            "SELECT id FROM topics WHERE name = ? ORDER BY id LIMIT 1",
            (topic_name,),
        ).fetchone()
        if topic_row is None:
            raise ValueError(f"topic not found: {topic_name}")

        project_id = cursor.execute(
            "SELECT project_id FROM channels WHERE id = ?",
            (video_row[1],),
        ).fetchone()[0]

        cursor.execute(
            """
            INSERT INTO subtopic_suggestion_labels(project_id, topic_id, suggestion_run_id, name, description)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(topic_id, suggestion_run_id, name) DO UPDATE SET
                description = excluded.description,
                status = 'pending',
                reviewed_at = NULL
            """,
            (
                project_id,
                topic_row[0],
                run_id,
                suggestion.primary_subtopic.label,
                None,
            ),
        )
        label_row = cursor.execute(
            """
            SELECT id FROM subtopic_suggestion_labels
            WHERE topic_id = ? AND suggestion_run_id = ? AND name = ?
            """,
            (topic_row[0], run_id, suggestion.primary_subtopic.label),
        ).fetchone()

        cursor.execute(
            """
            INSERT INTO video_subtopic_suggestions(
                run_id, video_id, suggestion_label_id, assignment_type, rationale, reuse_existing, raw_response_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, video_id) DO UPDATE SET
                suggestion_label_id = excluded.suggestion_label_id,
                assignment_type = excluded.assignment_type,
                rationale = excluded.rationale,
                reuse_existing = excluded.reuse_existing,
                raw_response_json = excluded.raw_response_json,
                created_at = CURRENT_TIMESTAMP,
                reviewed_at = NULL
            """,
            (
                run_id,
                video_row[0],
                label_row[0],
                suggestion.primary_subtopic.assignment_type,
                suggestion.primary_subtopic.rationale,
                1 if suggestion.primary_subtopic.reuse_existing else 0,
                suggestion.raw_response_json,
            ),
        )
        connection.commit()
        return 1


def list_video_subtopic_suggestions(
    db_path: str | Path,
    *,
    topic_name: str | None = None,
    status: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        filters = []
        params: list[object] = []
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is not None:
            filters.append("video_subtopic_suggestions.run_id = ?")
            params.append(resolved_run_id)
        if topic_name is not None:
            filters.append("topics.name = ?")
            params.append(topic_name)
        if status is not None:
            filters.append("subtopic_suggestion_labels.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        return connection.execute(
            f"""
            SELECT
                video_subtopic_suggestions.id,
                video_subtopic_suggestions.run_id,
                videos.youtube_video_id,
                videos.title AS video_title,
                topics.name AS topic_name,
                subtopic_suggestion_labels.name AS suggested_label,
                subtopic_suggestion_labels.status AS label_status,
                video_subtopic_suggestions.assignment_type,
                video_subtopic_suggestions.reuse_existing,
                video_subtopic_suggestions.rationale,
                video_subtopic_suggestions.created_at,
                video_subtopic_suggestions.reviewed_at
            FROM video_subtopic_suggestions
            JOIN videos ON videos.id = video_subtopic_suggestions.video_id
            JOIN subtopic_suggestion_labels ON subtopic_suggestion_labels.id = video_subtopic_suggestions.suggestion_label_id
            JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
            {where_sql}
            ORDER BY topics.name COLLATE NOCASE, videos.published_at DESC, videos.id DESC
            """,
            params,
        ).fetchall()


def list_subtopic_suggestion_application_rows(
    db_path: str | Path,
    *,
    topic_name: str | None = None,
    suggested_label: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            return []

        filters = [
            "video_subtopic_suggestions.run_id = ?",
            "subtopic_suggestion_labels.status = 'approved'",
        ]
        params: list[object] = [resolved_run_id]
        if topic_name is not None:
            filters.append("topics.name = ?")
            params.append(topic_name)
        if suggested_label is not None:
            filters.append("subtopic_suggestion_labels.name = ?")
            params.append(suggested_label)
        where_sql = f"WHERE {' AND '.join(filters)}"
        return connection.execute(
            f"""
            SELECT
                video_subtopic_suggestions.run_id,
                videos.youtube_video_id,
                videos.title AS video_title,
                topics.name AS topic_name,
                subtopic_suggestion_labels.name AS suggested_label,
                EXISTS (
                    SELECT 1
                    FROM video_subtopics
                    JOIN subtopics ON subtopics.id = video_subtopics.subtopic_id
                    WHERE video_subtopics.video_id = videos.id
                      AND subtopics.topic_id = subtopic_suggestion_labels.topic_id
                      AND subtopics.name = subtopic_suggestion_labels.name
                ) AS already_applied,
                video_subtopic_suggestions.created_at,
                video_subtopic_suggestions.reviewed_at
            FROM video_subtopic_suggestions
            JOIN videos ON videos.id = video_subtopic_suggestions.video_id
            JOIN subtopic_suggestion_labels ON subtopic_suggestion_labels.id = video_subtopic_suggestions.suggestion_label_id
            JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
            {where_sql}
            ORDER BY topics.name COLLATE NOCASE, subtopic_suggestion_labels.name COLLATE NOCASE, videos.published_at DESC, videos.id DESC
            """,
            params,
        ).fetchall()


def summarize_subtopic_suggestion_labels(
    db_path: str | Path,
    *,
    topic_name: str | None = None,
    status: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        filters = []
        params: list[object] = []
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is not None:
            filters.append("video_subtopic_suggestions.run_id = ?")
            params.append(resolved_run_id)
        if topic_name is not None:
            filters.append("topics.name = ?")
            params.append(topic_name)
        if status is not None:
            filters.append("subtopic_suggestion_labels.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        return connection.execute(
            f"""
            SELECT
                video_subtopic_suggestions.run_id AS run_id,
                topics.name AS topic_name,
                subtopic_suggestion_labels.name,
                subtopic_suggestion_labels.status,
                COUNT(video_subtopic_suggestions.id) AS suggestion_count,
                SUM(CASE WHEN video_subtopic_suggestions.reuse_existing = 1 THEN 1 ELSE 0 END) AS reuse_existing_count,
                MIN(video_subtopic_suggestions.created_at) AS first_suggested_at,
                MAX(video_subtopic_suggestions.created_at) AS last_suggested_at
            FROM video_subtopic_suggestions
            JOIN subtopic_suggestion_labels ON subtopic_suggestion_labels.id = video_subtopic_suggestions.suggestion_label_id
            JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
            {where_sql}
            GROUP BY video_subtopic_suggestions.run_id, topics.name, subtopic_suggestion_labels.name, subtopic_suggestion_labels.status
            ORDER BY topics.name COLLATE NOCASE, subtopic_suggestion_labels.status, suggestion_count DESC, subtopic_suggestion_labels.name COLLATE NOCASE
            """,
            params,
        ).fetchall()


def get_subtopic_suggestion_review_rows(
    db_path: str | Path,
    *,
    topic_name: str,
    run_id: int | None = None,
    status: str = "pending",
    sample_limit: int = 3,
) -> list[sqlite3.Row]:
    if sample_limit <= 0:
        raise ValueError("sample_limit must be greater than 0")

    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            return []
        return connection.execute(
            """
            WITH scoped_suggestions AS (
                SELECT
                    video_subtopic_suggestions.run_id,
                    video_subtopic_suggestions.suggestion_label_id,
                    video_subtopic_suggestions.video_id
                FROM video_subtopic_suggestions
                JOIN subtopic_suggestion_labels
                    ON subtopic_suggestion_labels.id = video_subtopic_suggestions.suggestion_label_id
                JOIN topics
                    ON topics.id = subtopic_suggestion_labels.topic_id
                WHERE video_subtopic_suggestions.run_id = ?
                  AND topics.name = ?
                  AND subtopic_suggestion_labels.status = ?
            ),
            label_counts AS (
                SELECT
                    scoped_suggestions.suggestion_label_id,
                    COUNT(*) AS video_count
                FROM scoped_suggestions
                GROUP BY scoped_suggestions.suggestion_label_id
            ),
            sampled_videos AS (
                SELECT
                    scoped_suggestions.suggestion_label_id,
                    videos.youtube_video_id,
                    videos.title AS video_title,
                    ROW_NUMBER() OVER (
                        PARTITION BY scoped_suggestions.suggestion_label_id
                        ORDER BY videos.published_at DESC, videos.id DESC
                    ) AS sample_rank
                FROM scoped_suggestions
                JOIN videos ON videos.id = scoped_suggestions.video_id
            )
            SELECT
                ? AS run_id,
                topics.name AS topic_name,
                subtopic_suggestion_labels.name,
                subtopic_suggestion_labels.status,
                label_counts.video_count,
                EXISTS (
                    SELECT 1
                    FROM subtopics
                    WHERE subtopics.topic_id = subtopic_suggestion_labels.topic_id
                      AND subtopics.name = subtopic_suggestion_labels.name
                ) AS approved_subtopic_exists,
                sampled_videos.youtube_video_id,
                sampled_videos.video_title,
                sampled_videos.sample_rank
            FROM label_counts
            JOIN subtopic_suggestion_labels
                ON subtopic_suggestion_labels.id = label_counts.suggestion_label_id
            JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
            LEFT JOIN sampled_videos
                ON sampled_videos.suggestion_label_id = label_counts.suggestion_label_id
               AND sampled_videos.sample_rank <= ?
            ORDER BY label_counts.video_count DESC, subtopic_suggestion_labels.name COLLATE NOCASE, sampled_videos.sample_rank
            """,
            (resolved_run_id, topic_name, status, resolved_run_id, sample_limit),
        ).fetchall()


def _get_subtopic_suggestion_label_row(
    connection: sqlite3.Connection,
    *,
    suggested_label: str,
    topic_name: str,
    run_id: int | None = None,
    status: str | None = None,
) -> sqlite3.Row | None:
    connection.row_factory = sqlite3.Row
    filters = ["subtopic_suggestion_labels.name = ?", "topics.name = ?"]
    params: list[object] = [suggested_label, topic_name]
    resolved_run_id = _resolve_topic_suggestion_run_id(connection, run_id)
    if resolved_run_id is not None:
        filters.append("video_subtopic_suggestions.run_id = ?")
        params.append(resolved_run_id)
    if status is not None:
        filters.append("subtopic_suggestion_labels.status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(filters)}"
    return connection.execute(
        f"""
        SELECT
            subtopic_suggestion_labels.id,
            subtopic_suggestion_labels.project_id,
            subtopic_suggestion_labels.topic_id,
            subtopic_suggestion_labels.suggestion_run_id,
            subtopic_suggestion_labels.name,
            subtopic_suggestion_labels.status,
            video_subtopic_suggestions.run_id AS matched_run_id
        FROM video_subtopic_suggestions
        JOIN subtopic_suggestion_labels
            ON subtopic_suggestion_labels.id = video_subtopic_suggestions.suggestion_label_id
        JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
        {where_sql}
        GROUP BY
            subtopic_suggestion_labels.id,
            subtopic_suggestion_labels.project_id,
            subtopic_suggestion_labels.topic_id,
            subtopic_suggestion_labels.suggestion_run_id,
            subtopic_suggestion_labels.name,
            subtopic_suggestion_labels.status,
            video_subtopic_suggestions.run_id
        ORDER BY video_subtopic_suggestions.run_id DESC, subtopic_suggestion_labels.id
        LIMIT 1
        """,
        params,
    ).fetchone()


def approve_subtopic_suggestion_label(
    db_path: str | Path,
    *,
    topic_name: str,
    suggested_label: str,
    approved_name: str | None = None,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_subtopic_suggestion_label_row(
            connection,
            suggested_label=suggested_label,
            topic_name=topic_name,
            run_id=run_id,
        )
        if row is None:
            raise ValueError(f"suggested subtopic label not found: {suggested_label}")
        final_name = approved_name or suggested_label
        cursor.execute(
            """
            INSERT INTO subtopics(topic_id, name)
            VALUES (?, ?)
            ON CONFLICT(topic_id, name) DO NOTHING
            """,
            (row["topic_id"], final_name),
        )
        subtopic_id = cursor.execute(
            "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
            (row["topic_id"], final_name),
        ).fetchone()[0]
        _upsert_subtopic_suggestion_label_review_state(
            cursor,
            source_row=row,
            final_name=final_name,
            final_status="approved",
        )
        connection.commit()
        return subtopic_id


def reject_subtopic_suggestion_label(
    db_path: str | Path,
    *,
    topic_name: str,
    suggested_label: str,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_subtopic_suggestion_label_row(
            connection,
            suggested_label=suggested_label,
            topic_name=topic_name,
            run_id=run_id,
        )
        if row is None:
            raise ValueError(f"suggested subtopic label not found: {suggested_label}")
        updated = cursor.execute(
            """
            UPDATE subtopic_suggestion_labels
            SET status = 'rejected',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        ).rowcount
        cursor.execute(
            """
            UPDATE video_subtopic_suggestions
            SET reviewed_at = CURRENT_TIMESTAMP
            WHERE suggestion_label_id = ?
            """,
            (row["id"],),
        )
        connection.commit()
        return updated


def rename_subtopic_suggestion_label(
    db_path: str | Path,
    *,
    topic_name: str,
    current_name: str,
    new_name: str,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_subtopic_suggestion_label_row(
            connection,
            suggested_label=current_name,
            topic_name=topic_name,
            run_id=run_id,
        )
        if row is None:
            raise ValueError(f"suggested subtopic label not found: {current_name}")
        label_id = _upsert_subtopic_suggestion_label_review_state(
            cursor,
            source_row=row,
            final_name=new_name,
            final_status="pending",
        )
        connection.commit()
        return label_id


def _upsert_subtopic_suggestion_label_review_state(
    cursor: sqlite3.Cursor,
    *,
    source_row: sqlite3.Row,
    final_name: str,
    final_status: str,
) -> int:
    target_row = cursor.execute(
        """
        SELECT id
        FROM subtopic_suggestion_labels
        WHERE topic_id = ?
          AND suggestion_run_id = ?
          AND name = ?
          AND id != ?
        ORDER BY id
        LIMIT 1
        """,
        (source_row["topic_id"], source_row["suggestion_run_id"], final_name, source_row["id"]),
    ).fetchone()
    reviewed_at_sql = "CURRENT_TIMESTAMP" if final_status == "approved" else "NULL"

    if target_row is None:
        cursor.execute(
            f"""
            UPDATE subtopic_suggestion_labels
            SET status = ?,
                name = ?,
                reviewed_at = {reviewed_at_sql}
            WHERE id = ?
            """,
            (final_status, final_name, source_row["id"]),
        )
        cursor.execute(
            f"""
            UPDATE video_subtopic_suggestions
            SET reviewed_at = {reviewed_at_sql}
            WHERE suggestion_label_id = ?
            """,
            (source_row["id"],),
        )
        return int(source_row["id"])

    target_id = int(target_row[0])
    cursor.execute(
        """
        UPDATE video_subtopic_suggestions
        SET suggestion_label_id = ?
        WHERE suggestion_label_id = ?
        """,
        (target_id, source_row["id"]),
    )
    cursor.execute(
        f"""
        UPDATE subtopic_suggestion_labels
        SET status = ?,
            reviewed_at = {reviewed_at_sql}
        WHERE id = ?
        """,
        (final_status, target_id),
    )
    cursor.execute(
        f"""
        UPDATE video_subtopic_suggestions
        SET reviewed_at = {reviewed_at_sql}
        WHERE suggestion_label_id = ?
        """,
        (target_id,),
    )
    cursor.execute(
        """
        UPDATE subtopic_suggestion_labels
        SET status = 'superseded',
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (source_row["id"],),
    )
    return target_id


def list_approved_topic_names(db_path: str | Path) -> list[str]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT topics.name
            FROM topics
            ORDER BY topics.name COLLATE NOCASE, topics.id
            """
        ).fetchall()
    return [row[0] for row in rows]


def get_latest_topic_suggestion_run_id(db_path: str | Path) -> int | None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        row = connection.execute(
            """
            SELECT id
            FROM topic_suggestion_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return int(row[0])


def list_topic_suggestion_runs(db_path: str | Path) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            WITH label_counts AS (
                SELECT
                    suggestion_run_id AS run_id,
                    COUNT(*) AS label_count,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_label_count,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_label_count,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_label_count,
                    SUM(CASE WHEN status = 'superseded' THEN 1 ELSE 0 END) AS superseded_label_count
                FROM topic_suggestion_labels
                WHERE suggestion_run_id IS NOT NULL
                GROUP BY suggestion_run_id
            ),
            suggestion_counts AS (
                SELECT
                    run_id,
                    COUNT(*) AS suggestion_row_count
                FROM video_topic_suggestions
                GROUP BY run_id
            )
            SELECT
                topic_suggestion_runs.id,
                topic_suggestion_runs.created_at,
                topic_suggestion_runs.model_name,
                topic_suggestion_runs.status AS run_status,
                COALESCE(label_counts.label_count, 0) AS label_count,
                COALESCE(suggestion_counts.suggestion_row_count, 0) AS suggestion_row_count,
                COALESCE(label_counts.pending_label_count, 0) AS pending_label_count,
                COALESCE(label_counts.approved_label_count, 0) AS approved_label_count,
                COALESCE(label_counts.rejected_label_count, 0) AS rejected_label_count,
                COALESCE(label_counts.superseded_label_count, 0) AS superseded_label_count
            FROM topic_suggestion_runs
            LEFT JOIN label_counts ON label_counts.run_id = topic_suggestion_runs.id
            LEFT JOIN suggestion_counts ON suggestion_counts.run_id = topic_suggestion_runs.id
            ORDER BY topic_suggestion_runs.id DESC
            """
        ).fetchall()


def _resolve_topic_suggestion_run_id(db_path_or_connection: str | Path | sqlite3.Connection, run_id: int | None) -> int | None:
    if run_id is not None:
        return run_id
    if isinstance(db_path_or_connection, sqlite3.Connection):
        ensure_schema(db_path_or_connection)
        row = db_path_or_connection.execute(
            "SELECT id FROM topic_suggestion_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    return get_latest_topic_suggestion_run_id(db_path_or_connection)


def create_topic_suggestion_run(
    db_path: str | Path,
    *,
    model_name: str,
    status: str = "success",
) -> int:
    primary_channel = get_primary_channel(db_path)
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO topic_suggestion_runs(project_id, model_name, status)
            VALUES (?, ?, ?)
            """,
            (primary_channel.project_id, model_name, status),
        )
        connection.commit()
        return cursor.lastrowid


def store_video_topic_suggestion(
    db_path: str | Path,
    *,
    run_id: int,
    suggestion: VideoTopicSuggestion,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        video_row = cursor.execute(
            "SELECT id, channel_id FROM videos WHERE youtube_video_id = ?",
            (suggestion.youtube_video_id,),
        ).fetchone()
        if video_row is None:
            raise ValueError(f"video not found: {suggestion.youtube_video_id}")
        project_id = cursor.execute(
            """
            SELECT channels.project_id
            FROM channels
            WHERE channels.id = ?
            """,
            (video_row[1],),
        ).fetchone()[0]

        labels = [suggestion.primary_topic, *suggestion.secondary_topics]
        inserted = 0
        for label in labels:
            cursor.execute(
                """
                INSERT INTO topic_suggestion_labels(project_id, suggestion_run_id, name)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id, suggestion_run_id, name) DO NOTHING
                """,
                (project_id, run_id, label.label),
            )
            label_row = cursor.execute(
                "SELECT id FROM topic_suggestion_labels WHERE project_id = ? AND suggestion_run_id = ? AND name = ?",
                (project_id, run_id, label.label),
            ).fetchone()
            cursor.execute(
                """
                INSERT INTO video_topic_suggestions(
                    run_id, video_id, suggestion_label_id, assignment_type, rationale, reuse_existing, raw_response_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id, suggestion_label_id, assignment_type) DO UPDATE SET
                    rationale = excluded.rationale,
                    reuse_existing = excluded.reuse_existing,
                    raw_response_json = excluded.raw_response_json,
                    created_at = CURRENT_TIMESTAMP,
                    reviewed_at = NULL
                """,
                (
                    run_id,
                    video_row[0],
                    label_row[0],
                    label.assignment_type,
                    label.rationale,
                    1 if label.reuse_existing else 0,
                    suggestion.raw_response_json,
                ),
            )
            inserted += 1
        connection.commit()
        return inserted


def list_video_topic_suggestions(
    db_path: str | Path,
    *,
    status: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        filters = []
        params: list[object] = []
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is not None:
            filters.append("video_topic_suggestions.run_id = ?")
            params.append(resolved_run_id)
        if status is not None:
            filters.append("topic_suggestion_labels.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        return connection.execute(
            f"""
            SELECT
                video_topic_suggestions.id,
                video_topic_suggestions.run_id,
                videos.youtube_video_id,
                videos.title AS video_title,
                topic_suggestion_labels.name AS suggested_label,
                topic_suggestion_labels.status AS label_status,
                video_topic_suggestions.assignment_type,
                video_topic_suggestions.reuse_existing,
                video_topic_suggestions.rationale,
                video_topic_suggestions.created_at,
                video_topic_suggestions.reviewed_at
            FROM video_topic_suggestions
            JOIN videos ON videos.id = video_topic_suggestions.video_id
            JOIN topic_suggestion_labels ON topic_suggestion_labels.id = video_topic_suggestions.suggestion_label_id
            {where_sql}
            ORDER BY videos.published_at DESC, videos.id DESC, CASE video_topic_suggestions.assignment_type WHEN 'primary' THEN 0 ELSE 1 END, topic_suggestion_labels.name COLLATE NOCASE
            """,
            params,
        ).fetchall()


def _get_topic_suggestion_label_row(
    connection: sqlite3.Connection,
    *,
    suggested_label: str,
    run_id: int | None = None,
    status: str | None = None,
) -> sqlite3.Row | None:
    connection.row_factory = sqlite3.Row
    filters = ["topic_suggestion_labels.name = ?"]
    params: list[object] = [suggested_label]
    resolved_run_id = _resolve_topic_suggestion_run_id(connection, run_id)
    if resolved_run_id is not None:
        filters.append("video_topic_suggestions.run_id = ?")
        params.append(resolved_run_id)
    if status is not None:
        filters.append("topic_suggestion_labels.status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(filters)}"
    return connection.execute(
        f"""
        SELECT
            topic_suggestion_labels.id,
            topic_suggestion_labels.project_id,
            topic_suggestion_labels.suggestion_run_id,
            topic_suggestion_labels.name,
            topic_suggestion_labels.status,
            video_topic_suggestions.run_id AS matched_run_id
        FROM video_topic_suggestions
        JOIN topic_suggestion_labels
            ON topic_suggestion_labels.id = video_topic_suggestions.suggestion_label_id
        {where_sql}
        GROUP BY
            topic_suggestion_labels.id,
            topic_suggestion_labels.project_id,
            topic_suggestion_labels.suggestion_run_id,
            topic_suggestion_labels.name,
            topic_suggestion_labels.status,
            video_topic_suggestions.run_id
        ORDER BY video_topic_suggestions.run_id DESC, topic_suggestion_labels.id
        LIMIT 1
        """,
        params,
    ).fetchone()


def approve_topic_suggestion_label(
    db_path: str | Path,
    *,
    suggested_label: str,
    approved_name: str | None = None,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_topic_suggestion_label_row(connection, suggested_label=suggested_label, run_id=run_id)
        if row is None:
            raise ValueError(f"suggested label not found: {suggested_label}")
        label_id = row["id"]
        project_id = row["project_id"]
        final_name = approved_name or suggested_label
        cursor.execute(
            """
            INSERT INTO topics(project_id, name)
            VALUES (?, ?)
            ON CONFLICT(project_id, name) DO NOTHING
            """,
            (project_id, final_name),
        )
        topic_id = cursor.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, final_name),
        ).fetchone()[0]
        cursor.execute(
            """
            UPDATE topic_suggestion_labels
            SET status = 'approved',
                name = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (final_name, label_id),
        )
        cursor.execute(
            """
            UPDATE video_topic_suggestions
            SET reviewed_at = CURRENT_TIMESTAMP
            WHERE suggestion_label_id = ?
            """,
            (label_id,),
        )
        connection.commit()
        return topic_id


def reject_topic_suggestion_label(
    db_path: str | Path,
    *,
    suggested_label: str,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_topic_suggestion_label_row(connection, suggested_label=suggested_label, run_id=run_id)
        if row is None:
            raise ValueError(f"suggested label not found: {suggested_label}")
        updated = cursor.execute(
            """
            UPDATE topic_suggestion_labels
            SET status = 'rejected',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        ).rowcount
        cursor.execute(
            """
            UPDATE video_topic_suggestions
            SET reviewed_at = CURRENT_TIMESTAMP
            WHERE suggestion_label_id = ?
            """,
            (row["id"],),
        )
        connection.commit()
        return updated


def rename_topic_suggestion_label(
    db_path: str | Path,
    *,
    current_name: str,
    new_name: str,
    run_id: int | None = None,
) -> int:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        row = _get_topic_suggestion_label_row(connection, suggested_label=current_name, run_id=run_id)
        if row is None:
            raise ValueError(f"suggested label not found: {current_name}")
        try:
            cursor.execute(
                "UPDATE topic_suggestion_labels SET name = ? WHERE id = ?",
                (new_name, row["id"]),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"suggested label already exists: {new_name}") from exc
        connection.commit()
        return row["id"]


def apply_topic_suggestion_to_video(
    db_path: str | Path,
    *,
    video_id: str,
    suggested_label: str,
    run_id: int | None = None,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            raise ValueError("no topic suggestion runs found")
        row = cursor.execute(
            """
            SELECT video_topic_suggestions.assignment_type, topics.id
            FROM video_topic_suggestions
            JOIN videos ON videos.id = video_topic_suggestions.video_id
            JOIN topic_suggestion_labels ON topic_suggestion_labels.id = video_topic_suggestions.suggestion_label_id
            JOIN channels ON channels.id = videos.channel_id
            JOIN topics ON topics.project_id = channels.project_id AND topics.name = topic_suggestion_labels.name
            WHERE videos.youtube_video_id = ?
              AND topic_suggestion_labels.name = ?
              AND topic_suggestion_labels.status = 'approved'
              AND video_topic_suggestions.run_id = ?
            ORDER BY video_topic_suggestions.id
            LIMIT 1
            """,
            (video_id, suggested_label, resolved_run_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"approved suggestion not found for video {video_id}: {suggested_label}")
        existing = cursor.execute(
            """
            SELECT 1
            FROM video_topics
            JOIN videos ON videos.id = video_topics.video_id
            JOIN topics ON topics.id = video_topics.topic_id
            WHERE videos.youtube_video_id = ?
              AND topics.name = ?
            LIMIT 1
            """,
            (video_id, suggested_label),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"approved suggestion already applied to video {video_id}: {suggested_label}")

        conflicting_primary = None
        if row[0] == "primary":
            conflicting_primary = cursor.execute(
                """
                SELECT 1
                FROM video_topics
                JOIN videos ON videos.id = video_topics.video_id
                JOIN topics ON topics.id = video_topics.topic_id
                WHERE videos.youtube_video_id = ?
                  AND video_topics.assignment_type = 'primary'
                  AND topics.name != ?
                LIMIT 1
                """,
                (video_id, suggested_label),
            ).fetchone()
        if conflicting_primary is not None:
            raise ValueError(f"video {video_id} already has a different primary topic")
        assign_topic_to_video(
            db_path,
            video_id=video_id,
            topic_name=suggested_label,
            assignment_type=row[0],
            assignment_source="suggested",
        )


def list_topic_suggestion_application_rows(
    db_path: str | Path,
    *,
    suggested_label: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            return []

        filters = [
            "video_topic_suggestions.run_id = ?",
            "topic_suggestion_labels.status = 'approved'",
        ]
        params: list[object] = [resolved_run_id]
        if suggested_label is not None:
            filters.append("topic_suggestion_labels.name = ?")
            params.append(suggested_label)
        where_sql = f"WHERE {' AND '.join(filters)}"
        return connection.execute(
            f"""
            SELECT
                video_topic_suggestions.run_id,
                videos.youtube_video_id,
                videos.title AS video_title,
                topic_suggestion_labels.name AS suggested_label,
                video_topic_suggestions.assignment_type,
                EXISTS (
                    SELECT 1
                    FROM video_topics
                    JOIN topics ON topics.id = video_topics.topic_id
                    WHERE video_topics.video_id = videos.id
                      AND topics.name = topic_suggestion_labels.name
                ) AS already_applied,
                CASE
                    WHEN video_topic_suggestions.assignment_type != 'primary' THEN 0
                    WHEN EXISTS (
                        SELECT 1
                        FROM video_topics
                        JOIN topics ON topics.id = video_topics.topic_id
                        WHERE video_topics.video_id = videos.id
                          AND video_topics.assignment_type = 'primary'
                          AND topics.name != topic_suggestion_labels.name
                    ) THEN 1
                    ELSE 0
                END AS conflicting_primary,
                video_topic_suggestions.created_at,
                video_topic_suggestions.reviewed_at
            FROM video_topic_suggestions
            JOIN videos ON videos.id = video_topic_suggestions.video_id
            JOIN topic_suggestion_labels ON topic_suggestion_labels.id = video_topic_suggestions.suggestion_label_id
            {where_sql}
            ORDER BY topic_suggestion_labels.name COLLATE NOCASE, videos.published_at DESC, videos.id DESC, CASE video_topic_suggestions.assignment_type WHEN 'primary' THEN 0 ELSE 1 END
            """,
            params,
        ).fetchall()


def apply_subtopic_suggestion_to_video(
    db_path: str | Path,
    *,
    video_id: str,
    topic_name: str,
    suggested_label: str,
    run_id: int | None = None,
) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            raise ValueError("no topic suggestion runs found")
        row = cursor.execute(
            """
            SELECT subtopics.id
            FROM video_subtopic_suggestions
            JOIN videos ON videos.id = video_subtopic_suggestions.video_id
            JOIN subtopic_suggestion_labels ON subtopic_suggestion_labels.id = video_subtopic_suggestions.suggestion_label_id
            JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
            JOIN subtopics ON subtopics.topic_id = topics.id AND subtopics.name = subtopic_suggestion_labels.name
            WHERE videos.youtube_video_id = ?
              AND topics.name = ?
              AND subtopic_suggestion_labels.name = ?
              AND subtopic_suggestion_labels.status = 'approved'
              AND video_subtopic_suggestions.run_id = ?
            ORDER BY video_subtopic_suggestions.id
            LIMIT 1
            """,
            (video_id, topic_name, suggested_label, resolved_run_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"approved subtopic suggestion not found for video {video_id}: {suggested_label}")
        existing = cursor.execute(
            """
            SELECT 1
            FROM video_subtopics
            JOIN videos ON videos.id = video_subtopics.video_id
            JOIN subtopics ON subtopics.id = video_subtopics.subtopic_id
            JOIN topics ON topics.id = subtopics.topic_id
            WHERE videos.youtube_video_id = ?
              AND topics.name = ?
              AND subtopics.name = ?
            LIMIT 1
            """,
            (video_id, topic_name, suggested_label),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"approved subtopic suggestion already applied to video {video_id}: {suggested_label}")

        assign_subtopic_to_video(
            db_path,
            video_id=video_id,
            subtopic_name=suggested_label,
            assignment_source="suggested",
        )


def summarize_topic_suggestion_labels(
    db_path: str | Path,
    *,
    status: str | None = None,
    run_id: int | None = None,
) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        filters = []
        params: list[object] = []
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is not None:
            filters.append("video_topic_suggestions.run_id = ?")
            params.append(resolved_run_id)
        if status is not None:
            filters.append("topic_suggestion_labels.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        return connection.execute(
            f"""
            SELECT
                video_topic_suggestions.run_id AS run_id,
                topic_suggestion_labels.name,
                topic_suggestion_labels.status,
                COUNT(video_topic_suggestions.id) AS suggestion_count,
                SUM(CASE WHEN video_topic_suggestions.assignment_type = 'primary' THEN 1 ELSE 0 END) AS primary_count,
                SUM(CASE WHEN video_topic_suggestions.assignment_type = 'secondary' THEN 1 ELSE 0 END) AS secondary_count,
                MIN(video_topic_suggestions.created_at) AS first_suggested_at,
                MAX(video_topic_suggestions.created_at) AS last_suggested_at
            FROM video_topic_suggestions
            JOIN topic_suggestion_labels
                ON topic_suggestion_labels.id = video_topic_suggestions.suggestion_label_id
            {where_sql}
            GROUP BY video_topic_suggestions.run_id, topic_suggestion_labels.name, topic_suggestion_labels.status
            ORDER BY topic_suggestion_labels.status, suggestion_count DESC, topic_suggestion_labels.name COLLATE NOCASE
            """,
            params,
        ).fetchall()


def get_topic_suggestion_review_rows(
    db_path: str | Path,
    *,
    run_id: int | None = None,
    status: str = "pending",
    sample_limit: int = 3,
) -> list[sqlite3.Row]:
    if sample_limit <= 0:
        raise ValueError("sample_limit must be greater than 0")

    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            return []
        return connection.execute(
            """
            WITH scoped_suggestions AS (
                SELECT
                    video_topic_suggestions.run_id,
                    video_topic_suggestions.suggestion_label_id,
                    video_topic_suggestions.video_id,
                    video_topic_suggestions.assignment_type
                FROM video_topic_suggestions
                JOIN topic_suggestion_labels
                    ON topic_suggestion_labels.id = video_topic_suggestions.suggestion_label_id
                WHERE video_topic_suggestions.run_id = ?
                  AND topic_suggestion_labels.status = ?
            ),
            label_counts AS (
                SELECT
                    scoped_suggestions.suggestion_label_id,
                    COUNT(*) AS video_count,
                    SUM(CASE WHEN scoped_suggestions.assignment_type = 'primary' THEN 1 ELSE 0 END) AS primary_count,
                    SUM(CASE WHEN scoped_suggestions.assignment_type = 'secondary' THEN 1 ELSE 0 END) AS secondary_count
                FROM scoped_suggestions
                GROUP BY scoped_suggestions.suggestion_label_id
            ),
            sampled_videos AS (
                SELECT
                    scoped_suggestions.suggestion_label_id,
                    videos.youtube_video_id,
                    videos.title AS video_title,
                    scoped_suggestions.assignment_type,
                    ROW_NUMBER() OVER (
                        PARTITION BY scoped_suggestions.suggestion_label_id
                        ORDER BY CASE scoped_suggestions.assignment_type WHEN 'primary' THEN 0 ELSE 1 END,
                                 videos.published_at DESC,
                                 videos.id DESC
                    ) AS sample_rank
                FROM scoped_suggestions
                JOIN videos
                    ON videos.id = scoped_suggestions.video_id
            )
            SELECT
                ? AS run_id,
                topic_suggestion_labels.name,
                topic_suggestion_labels.status,
                label_counts.video_count,
                label_counts.primary_count,
                label_counts.secondary_count,
                EXISTS (
                    SELECT 1
                    FROM topics
                    WHERE topics.project_id = topic_suggestion_labels.project_id
                      AND topics.name = topic_suggestion_labels.name
                ) AS approved_topic_exists,
                sampled_videos.youtube_video_id,
                sampled_videos.video_title,
                sampled_videos.assignment_type,
                sampled_videos.sample_rank
            FROM label_counts
            JOIN topic_suggestion_labels
                ON topic_suggestion_labels.id = label_counts.suggestion_label_id
            LEFT JOIN sampled_videos
                ON sampled_videos.suggestion_label_id = label_counts.suggestion_label_id
               AND sampled_videos.sample_rank <= ?
            ORDER BY label_counts.video_count DESC, topic_suggestion_labels.name COLLATE NOCASE, sampled_videos.sample_rank
            """,
            (
                resolved_run_id,
                status,
                resolved_run_id,
                sample_limit,
            ),
        ).fetchall()


def bulk_apply_topic_suggestion_label(
    db_path: str | Path,
    *,
    suggested_label: str,
    run_id: int | None = None,
) -> tuple[int, int, int]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.cursor()
        resolved_run_id = _resolve_topic_suggestion_run_id(db_path, run_id)
        if resolved_run_id is None:
            raise ValueError("no topic suggestion runs found")

        run_exists = cursor.execute(
            "SELECT 1 FROM topic_suggestion_runs WHERE id = ? LIMIT 1",
            (resolved_run_id,),
        ).fetchone()
        if run_exists is None:
            raise ValueError(f"topic suggestion run not found: {resolved_run_id}")

        label_row = _get_topic_suggestion_label_row(
            connection,
            suggested_label=suggested_label,
            run_id=resolved_run_id,
            status="approved",
        )
        if label_row is None:
            unresolved_label_row = _get_topic_suggestion_label_row(
                connection,
                suggested_label=suggested_label,
                run_id=resolved_run_id,
            )
            if unresolved_label_row is None:
                raise ValueError(f"suggested label not found for run {resolved_run_id}: {suggested_label}")
            raise ValueError(f"suggested label is not approved for run {resolved_run_id}: {suggested_label}")

        rows = cursor.execute(
            """
            SELECT videos.youtube_video_id, video_topic_suggestions.assignment_type
            FROM video_topic_suggestions
            JOIN videos ON videos.id = video_topic_suggestions.video_id
            JOIN topic_suggestion_labels ON topic_suggestion_labels.id = video_topic_suggestions.suggestion_label_id
            WHERE video_topic_suggestions.run_id = ?
              AND video_topic_suggestions.suggestion_label_id = ?
              AND topic_suggestion_labels.status = 'approved'
            ORDER BY video_topic_suggestions.id
            """,
            (resolved_run_id, label_row[0]),
        ).fetchall()

    matched = len(rows)
    applied = 0
    for youtube_video_id, assignment_type in rows:
        with connect(db_path) as connection:
            ensure_schema(connection)
            existing = connection.execute(
                """
                SELECT 1
                FROM video_topics
                JOIN videos ON videos.id = video_topics.video_id
                JOIN topics ON topics.id = video_topics.topic_id
                WHERE videos.youtube_video_id = ?
                  AND topics.name = ?
                LIMIT 1
                """,
                (youtube_video_id, suggested_label),
            ).fetchone()
            conflicting_primary = None
            if assignment_type == "primary":
                conflicting_primary = connection.execute(
                    """
                    SELECT 1
                    FROM video_topics
                    JOIN videos ON videos.id = video_topics.video_id
                    WHERE videos.youtube_video_id = ?
                      AND video_topics.assignment_type = 'primary'
                    LIMIT 1
                    """,
                    (youtube_video_id,),
                ).fetchone()
        if existing is not None or conflicting_primary is not None:
            continue
        assign_topic_to_video(
            db_path,
            video_id=youtube_video_id,
            topic_name=suggested_label,
            assignment_type=assignment_type,
            assignment_source="suggested",
        )
        applied += 1
    skipped = matched - applied
    return matched, applied, skipped


def supersede_stale_topic_suggestions(
    db_path: str | Path,
    *,
    keep_run_id: int,
    suggested_label: str | None = None,
) -> dict[str, int]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()

        keep_run = cursor.execute(
            "SELECT id FROM topic_suggestion_runs WHERE id = ? LIMIT 1",
            (keep_run_id,),
        ).fetchone()
        if keep_run is None:
            raise ValueError(f"topic suggestion run not found: {keep_run_id}")

        params: list[object] = [keep_run_id]
        label_sql = ""
        if suggested_label is not None:
            label_sql = " AND topic_suggestion_labels.name = ?"
            params.append(suggested_label)

        candidate_rows = cursor.execute(
            f"""
            SELECT
                topic_suggestion_labels.id,
                topic_suggestion_labels.status,
                topic_suggestion_labels.suggestion_run_id
            FROM topic_suggestion_labels
            WHERE topic_suggestion_labels.suggestion_run_id < ?
              {label_sql}
            """,
            params,
        ).fetchall()

        matched = len(candidate_rows)
        superseded_ids = [row["id"] for row in candidate_rows if row["status"] == "pending"]
        superseded = len(superseded_ids)
        skipped = matched - superseded
        affected_run_ids = {row["suggestion_run_id"] for row in candidate_rows if row["status"] == "pending"}

        if superseded_ids:
            placeholders = ", ".join("?" for _ in superseded_ids)
            cursor.execute(
                f"""
                UPDATE topic_suggestion_labels
                SET status = 'superseded',
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                superseded_ids,
            )
            cursor.execute(
                f"""
                UPDATE video_topic_suggestions
                SET reviewed_at = CURRENT_TIMESTAMP
                WHERE suggestion_label_id IN ({placeholders})
                """,
                superseded_ids,
            )

        connection.commit()
        return {
            "keep_run_id": keep_run_id,
            "older_runs_affected": len(affected_run_ids),
            "matched": matched,
            "superseded": superseded,
            "skipped": skipped,
        }


# ---------------------------------------------------------------------------
# Phase B — refinement runs, sampled episodes, taxonomy proposals
#
# These helpers take an open ``connection`` (the caller — ``refinement.py`` in
# slice B3 — owns the transaction and calls ``ensure_schema``); they do not
# commit. They mirror the conventions of ``discovery.py``'s internal helpers.
# ---------------------------------------------------------------------------

_REFINEMENT_RUN_STATUSES = {"pending", "running", "success", "error"}


def build_topic_rename_resolver(
    connection: sqlite3.Connection, project_id: int
) -> Callable[[str], str]:
    """Return a function mapping a topic name through this project's
    ``topic_renames`` log to its current name, collapsing multi-hop chains
    (A→B then B→C resolves "A" straight to "C"). Mirrors the fixed-point logic
    in ``discovery._apply_renames_to_payload``; unknown names pass through
    unchanged.
    """
    rows = connection.execute(
        "SELECT old_name, new_name FROM topic_renames WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    direct: dict[str, str] = {row[0]: row[1] for row in rows}

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

    return resolve


def create_refinement_run(
    connection: sqlite3.Connection,
    *,
    channel_id: int,
    discovery_run_id: int | None,
    model: str,
    prompt_version: str,
    n_sample: int | None,
) -> int:
    """Insert a ``refinement_runs`` row in ``status='pending'`` and return its id."""
    cursor = connection.execute(
        """
        INSERT INTO refinement_runs(
            channel_id, discovery_run_id, model, prompt_version, status, n_sample
        ) VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (channel_id, discovery_run_id, model, prompt_version, n_sample),
    )
    return int(cursor.lastrowid)


def set_refinement_run_status(
    connection: sqlite3.Connection, run_id: int, status: str
) -> None:
    if status not in _REFINEMENT_RUN_STATUSES:
        raise ValueError(
            f"invalid refinement run status: {status!r} "
            f"(expected one of {sorted(_REFINEMENT_RUN_STATUSES)})"
        )
    connection.execute(
        "UPDATE refinement_runs SET status = ? WHERE id = ?", (status, run_id)
    )


def set_refinement_run_error(
    connection: sqlite3.Connection, run_id: int, message: str
) -> None:
    """Flip a refinement run to ``status='error'`` and record ``message``."""
    connection.execute(
        "UPDATE refinement_runs SET status = 'error', error_message = ? WHERE id = ?",
        (message, run_id),
    )


def add_refinement_episodes(
    connection: sqlite3.Connection,
    run_id: int,
    rows: list[tuple],
) -> None:
    """Record the episodes a refinement run sampled.

    ``rows`` is ``[(video_id, transcript_status_at_run[, assignments_before_json]), ...]``
    (internal video ids). The optional third element is a JSON string of the
    episode's metadata-derived assignments *before* this run — the before-side
    of the proposal-review sanity panel. Idempotent per ``(run_id, video_id)``:
    re-recording updates the stored transcript status and only overwrites the
    before-snapshot when a non-NULL one is supplied.
    """
    payload: list[tuple] = []
    for row in rows:
        video_id, status = row[0], row[1]
        before_json = row[2] if len(row) > 2 else None
        payload.append((run_id, video_id, status, before_json))
    connection.executemany(
        """
        INSERT INTO refinement_episodes(
            refinement_run_id, video_id, transcript_status_at_run, assignments_before_json
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(refinement_run_id, video_id) DO UPDATE SET
            transcript_status_at_run = excluded.transcript_status_at_run,
            assignments_before_json = COALESCE(
                excluded.assignments_before_json, refinement_episodes.assignments_before_json
            )
        """,
        payload,
    )


def insert_taxonomy_proposals(
    connection: sqlite3.Connection,
    run_id: int,
    proposals: list[dict[str, Any]],
) -> list[int]:
    """Insert ``taxonomy_proposals`` rows (all ``status='pending'``) and return
    the new ids in order.

    Each proposal dict: ``kind`` ('topic'|'subtopic'), ``name``,
    ``parent_topic_name`` (ignored / forced NULL for ``kind='topic'``),
    ``evidence`` (optional), ``source_video_id`` (optional internal id).
    """
    new_ids: list[int] = []
    for proposal in proposals:
        kind = proposal["kind"]
        if kind not in {"topic", "subtopic"}:
            raise ValueError(f"invalid taxonomy proposal kind: {kind!r}")
        parent = proposal.get("parent_topic_name") if kind == "subtopic" else None
        if kind == "subtopic" and not parent:
            raise ValueError("subtopic proposal requires parent_topic_name")
        cursor = connection.execute(
            """
            INSERT INTO taxonomy_proposals(
                refinement_run_id, kind, name, parent_topic_name, evidence, source_video_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                kind,
                proposal["name"],
                parent,
                proposal.get("evidence"),
                proposal.get("source_video_id"),
            ),
        )
        new_ids.append(int(cursor.lastrowid))
    return new_ids


def _project_id_for_refinement_run(
    connection: sqlite3.Connection, run_id: int
) -> int:
    row = connection.execute(
        """
        SELECT channels.project_id
        FROM refinement_runs
        JOIN channels ON channels.id = refinement_runs.channel_id
        WHERE refinement_runs.id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"refinement run not found: {run_id}")
    return int(row[0])


def accept_taxonomy_proposal(
    connection: sqlite3.Connection, proposal_id: int
) -> dict[str, Any]:
    """Accept a taxonomy proposal: create the real ``topics``/``subtopics`` row
    if it does not already exist, then mark the proposal ``accepted``.

    For ``kind='subtopic'`` the parent topic name is resolved through the
    project's rename log first; if the parent no longer exists the proposal is
    marked ``rejected`` instead and the returned dict carries
    ``status='rejected'`` with ``reason='parent_topic_missing'``. Idempotent —
    re-accepting an already-accepted proposal is a no-op beyond ensuring the
    node exists.
    """
    row = connection.execute(
        """
        SELECT id, refinement_run_id, kind, name, parent_topic_name, status
        FROM taxonomy_proposals WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"taxonomy proposal not found: {proposal_id}")
    pid, run_id, kind, name, parent_topic_name, _status = row
    project_id = _project_id_for_refinement_run(connection, int(run_id))
    resolve = build_topic_rename_resolver(connection, project_id)

    if kind == "topic":
        existing = connection.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, name),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO topics(project_id, name) VALUES (?, ?)",
                (project_id, name),
            )
        connection.execute(
            "UPDATE taxonomy_proposals SET status = 'accepted', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pid,),
        )
        return {"proposal_id": int(pid), "status": "accepted", "kind": "topic", "name": name}

    # kind == 'subtopic'
    resolved_parent = resolve(parent_topic_name) if parent_topic_name else parent_topic_name
    parent_row = connection.execute(
        "SELECT id FROM topics WHERE project_id = ? AND name = ?",
        (project_id, resolved_parent),
    ).fetchone()
    if parent_row is None:
        connection.execute(
            "UPDATE taxonomy_proposals SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pid,),
        )
        return {
            "proposal_id": int(pid),
            "status": "rejected",
            "kind": "subtopic",
            "name": name,
            "parent_topic_name": resolved_parent,
            "reason": "parent_topic_missing",
        }
    topic_id = int(parent_row[0])
    existing = connection.execute(
        "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
        (topic_id, name),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO subtopics(topic_id, name) VALUES (?, ?)",
            (topic_id, name),
        )
    connection.execute(
        "UPDATE taxonomy_proposals SET status = 'accepted', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (pid,),
    )
    return {
        "proposal_id": int(pid),
        "status": "accepted",
        "kind": "subtopic",
        "name": name,
        "parent_topic_name": resolved_parent,
    }


def list_pending_taxonomy_proposals(
    connection: sqlite3.Connection, project_id: int
) -> list[dict[str, Any]]:
    """All ``status='pending'`` taxonomy proposals for the project's refinement
    runs — newest run first, then subtopics before topics, then name. Each row
    carries the source episode's youtube id + title so the review card can link
    it. ``connection.row_factory`` must be :class:`sqlite3.Row`."""
    rows = connection.execute(
        """
        SELECT tp.id AS proposal_id,
               tp.refinement_run_id AS refinement_run_id,
               tp.kind AS kind,
               tp.name AS name,
               tp.parent_topic_name AS parent_topic_name,
               tp.evidence AS evidence,
               tp.source_video_id AS source_video_id,
               tp.created_at AS created_at,
               v.youtube_video_id AS source_youtube_video_id,
               v.title AS source_title
        FROM taxonomy_proposals tp
        JOIN refinement_runs rr ON rr.id = tp.refinement_run_id
        JOIN channels ch ON ch.id = rr.channel_id
        LEFT JOIN videos v ON v.id = tp.source_video_id
        WHERE ch.project_id = ? AND tp.status = 'pending'
        ORDER BY tp.refinement_run_id DESC,
                 CASE tp.kind WHEN 'subtopic' THEN 0 ELSE 1 END,
                 tp.parent_topic_name COLLATE NOCASE,
                 tp.name COLLATE NOCASE
        """,
        (project_id,),
    ).fetchall()
    return [
        {
            "proposal_id": int(r["proposal_id"]),
            "refinement_run_id": int(r["refinement_run_id"]),
            "kind": r["kind"],
            "name": r["name"],
            "parent_topic_name": r["parent_topic_name"],
            "evidence": r["evidence"],
            "source_video_id": (
                int(r["source_video_id"]) if r["source_video_id"] is not None else None
            ),
            "source_youtube_video_id": r["source_youtube_video_id"],
            "source_title": r["source_title"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _assignments_after_for_video(
    connection: sqlite3.Connection, video_id: int
) -> list[dict[str, Any]]:
    """Current ``refine``/``manual`` topic assignments for ``video_id`` (the
    after-side of the proposal-review panel), with their subtopic if any."""
    subtopic_by_topic_id = {
        int(r["topic_id"]): r["subtopic"]
        for r in connection.execute(
            """
            SELECT s.topic_id AS topic_id, s.name AS subtopic
            FROM video_subtopics vs JOIN subtopics s ON s.id = vs.subtopic_id
            WHERE vs.video_id = ? AND vs.assignment_source IN ('refine', 'manual')
            """,
            (video_id,),
        ).fetchall()
    }
    out: list[dict[str, Any]] = []
    for r in connection.execute(
        """
        SELECT t.id AS topic_id, t.name AS topic, vt.confidence AS confidence,
               vt.reason AS reason, vt.assignment_source AS assignment_source
        FROM video_topics vt JOIN topics t ON t.id = vt.topic_id
        WHERE vt.video_id = ? AND vt.assignment_source IN ('refine', 'manual')
        ORDER BY vt.confidence DESC, t.name COLLATE NOCASE
        """,
        (video_id,),
    ).fetchall():
        out.append(
            {
                "topic": r["topic"],
                "subtopic": subtopic_by_topic_id.get(int(r["topic_id"])),
                "confidence": r["confidence"],
                "reason": r["reason"],
                "assignment_source": r["assignment_source"],
            }
        )
    return out


def list_refinement_episode_changes(
    connection: sqlite3.Connection, project_id: int
) -> list[dict[str, Any]]:
    """Per ``success`` refinement run of the project (newest first), the sampled
    episodes with their assignments ``before`` the run (the stored snapshot) and
    ``after`` (current ``refine``/``manual`` rows). Drives the before→after
    sanity panel. ``connection.row_factory`` must be :class:`sqlite3.Row`."""
    runs = connection.execute(
        """
        SELECT rr.id AS run_id, rr.discovery_run_id AS discovery_run_id,
               rr.n_sample AS n_sample, rr.created_at AS created_at
        FROM refinement_runs rr JOIN channels ch ON ch.id = rr.channel_id
        WHERE ch.project_id = ? AND rr.status = 'success'
        ORDER BY rr.id DESC
        """,
        (project_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for run in runs:
        episodes: list[dict[str, Any]] = []
        for ep in connection.execute(
            """
            SELECT re.video_id AS video_id, re.assignments_before_json AS before_json,
                   v.youtube_video_id AS youtube_video_id, v.title AS title
            FROM refinement_episodes re JOIN videos v ON v.id = re.video_id
            WHERE re.refinement_run_id = ?
            ORDER BY v.title COLLATE NOCASE
            """,
            (int(run["run_id"]),),
        ).fetchall():
            try:
                before = json.loads(ep["before_json"]) if ep["before_json"] else []
            except (TypeError, ValueError):
                before = []
            episodes.append(
                {
                    "video_id": int(ep["video_id"]),
                    "youtube_video_id": ep["youtube_video_id"],
                    "title": ep["title"],
                    "before": before if isinstance(before, list) else [],
                    "after": _assignments_after_for_video(connection, int(ep["video_id"])),
                }
            )
        result.append(
            {
                "refinement_run_id": int(run["run_id"]),
                "discovery_run_id": run["discovery_run_id"],
                "n_sample": run["n_sample"],
                "created_at": run["created_at"],
                "episodes": episodes,
            }
        )
    return result


def reject_taxonomy_proposal(
    connection: sqlite3.Connection, proposal_id: int
) -> dict[str, Any]:
    """Mark a taxonomy proposal ``rejected``. Does not delete any taxonomy row
    a prior ``accept`` may have created (other episodes may already use it)."""
    affected = connection.execute(
        "UPDATE taxonomy_proposals SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (proposal_id,),
    ).rowcount
    if affected == 0:
        raise ValueError(f"taxonomy proposal not found: {proposal_id}")
    return {"proposal_id": int(proposal_id), "status": "rejected"}


def write_refine_assignments(
    connection: sqlite3.Connection,
    *,
    channel_id: int,
    refinement_run_id: int,
    video_id: int,
    assignments: list[dict[str, Any]],
) -> dict[str, int]:
    """Replace ``video_id``'s non-curated topic/subtopic assignments wholesale
    with transcript-grade ``assignments`` (``assignment_source='refine'``).

    ``video_id`` is an internal id; each assignment dict carries ``topic_name``
    (required, resolved through the project's rename log — must already exist),
    optional ``subtopic_name`` (created under the topic if absent), and optional
    ``confidence`` / ``reason``. ``assignment_source='manual'`` rows survive
    untouched (and re-affirming their topic does not overwrite them). Rows the
    operator previously marked via ``wrong_assignments`` are not re-added.
    """
    project_row = connection.execute(
        "SELECT project_id FROM channels WHERE id = ?", (channel_id,)
    ).fetchone()
    if project_row is None:
        raise ValueError(f"channel not found: {channel_id}")
    project_id = int(project_row[0])
    resolve = build_topic_rename_resolver(connection, project_id)

    wrong_topic_ids = {
        int(r[0])
        for r in connection.execute(
            "SELECT topic_id FROM wrong_assignments WHERE video_id = ? AND subtopic_id IS NULL",
            (video_id,),
        ).fetchall()
    }
    wrong_subtopic_ids = {
        int(r[0])
        for r in connection.execute(
            "SELECT subtopic_id FROM wrong_assignments WHERE video_id = ? AND subtopic_id IS NOT NULL",
            (video_id,),
        ).fetchall()
    }

    connection.execute(
        "DELETE FROM video_subtopics WHERE video_id = ? AND assignment_source <> 'manual'",
        (video_id,),
    )
    connection.execute(
        "DELETE FROM video_topics WHERE video_id = ? AND assignment_source <> 'manual'",
        (video_id,),
    )

    topics_written = 0
    subtopics_written = 0
    suppressed = 0
    skipped_unknown_topic = 0
    for assignment in assignments:
        topic_name = resolve(assignment["topic_name"])
        topic_row = connection.execute(
            "SELECT id FROM topics WHERE project_id = ? AND name = ?",
            (project_id, topic_name),
        ).fetchone()
        if topic_row is None:
            # The model occasionally invents a topic in `assignments` despite the
            # prompt forbidding it; drop that one assignment rather than failing
            # the whole (paid) batch. A genuinely new theme is usually also raised
            # under new_topic_proposals, where the operator can promote it.
            skipped_unknown_topic += 1
            continue
        topic_id = int(topic_row[0])
        if topic_id in wrong_topic_ids:
            suppressed += 1
            continue
        confidence = assignment.get("confidence")
        reason = assignment.get("reason")
        connection.execute(
            """
            INSERT INTO video_topics(
                video_id, topic_id, assignment_type, assignment_source,
                confidence, reason, discovery_run_id, refinement_run_id
            ) VALUES (?, ?, 'secondary', 'refine', ?, ?, NULL, ?)
            ON CONFLICT(video_id, topic_id) DO NOTHING
            """,
            (video_id, topic_id, confidence, reason, refinement_run_id),
        )
        topics_written += 1

        subtopic_name = assignment.get("subtopic_name")
        if not subtopic_name:
            continue
        connection.execute(
            "INSERT INTO subtopics(topic_id, name) VALUES (?, ?) ON CONFLICT(topic_id, name) DO NOTHING",
            (topic_id, subtopic_name),
        )
        subtopic_row = connection.execute(
            "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
            (topic_id, subtopic_name),
        ).fetchone()
        subtopic_id = int(subtopic_row[0])
        if subtopic_id in wrong_subtopic_ids:
            suppressed += 1
            continue
        connection.execute(
            """
            INSERT INTO video_subtopics(
                video_id, subtopic_id, assignment_source,
                confidence, reason, discovery_run_id, refinement_run_id
            ) VALUES (?, ?, 'refine', ?, ?, NULL, ?)
            ON CONFLICT(video_id, subtopic_id) DO NOTHING
            """,
            (video_id, subtopic_id, confidence, reason, refinement_run_id),
        )
        subtopics_written += 1

    return {
        "video_id": int(video_id),
        "topics_written": topics_written,
        "subtopics_written": subtopics_written,
        "suppressed": suppressed,
        "skipped_unknown_topic": skipped_unknown_topic,
    }
