from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from yt_channel_analyzer.db import (
    connect,
    create_topic,
    ensure_schema,
    init_db,
    upsert_videos_for_primary_channel,
)
from yt_channel_analyzer.discovery import (
    DiscoveryAssignment,
    DiscoveryPayload,
    run_discovery,
)
from yt_channel_analyzer.youtube import VideoMetadata


def _seed_channel_with_videos(db_path: Path) -> None:
    init_db(
        db_path,
        project_name="proj",
        channel_id="UC123",
        channel_title="Channel",
        channel_handle="@channel",
    )
    upsert_videos_for_primary_channel(
        db_path,
        videos=[
            VideoMetadata(
                youtube_video_id="vid1",
                title="Sleep and the brain",
                description="how sleep works",
                published_at="2026-04-05T12:00:00Z",
                thumbnail_url=None,
            ),
            VideoMetadata(
                youtube_video_id="vid2",
                title="Building a startup",
                description="founder stories",
                published_at="2026-04-06T12:00:00Z",
                thumbnail_url=None,
            ),
        ],
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


class DiscoverySchemaTests(unittest.TestCase):
    def test_ensure_schema_creates_discovery_runs_table(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            with connect(db_path) as conn:
                cols = _columns(conn, "discovery_runs")
            self.assertIn("id", cols)
            self.assertIn("channel_id", cols)
            self.assertIn("model", cols)
            self.assertIn("prompt_version", cols)
            self.assertIn("status", cols)
            self.assertIn("created_at", cols)


    def test_video_topics_has_discovery_columns(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            with connect(db_path) as conn:
                cols = _columns(conn, "video_topics")
            self.assertIn("confidence", cols)
            self.assertIn("reason", cols)
            self.assertIn("discovery_run_id", cols)

    def test_video_subtopics_has_discovery_columns(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            with connect(db_path) as conn:
                cols = _columns(conn, "video_subtopics")
            self.assertIn("confidence", cols)
            self.assertIn("reason", cols)
            self.assertIn("discovery_run_id", cols)

    def test_ensure_schema_repairs_old_video_topics_check_constraint(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            upsert_videos_for_primary_channel(
                db_path,
                videos=[
                    VideoMetadata(
                        youtube_video_id="vid1",
                        title="Video 1",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health")

            with connect(db_path) as conn:
                # Simulate a database created before 'auto' was a valid
                # assignment_source: drop the modern tables and recreate them
                # with the pre-change CHECK constraint.
                conn.executescript(
                    """
                    DROP TABLE IF EXISTS video_topics;
                    DROP TABLE IF EXISTS video_subtopics;
                    CREATE TABLE video_topics (
                        video_id INTEGER NOT NULL,
                        topic_id INTEGER NOT NULL,
                        assignment_type TEXT NOT NULL DEFAULT 'secondary',
                        assignment_source TEXT NOT NULL DEFAULT 'manual',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(video_id, topic_id),
                        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
                        CHECK (assignment_type IN ('primary', 'secondary')),
                        CHECK (assignment_source IN ('manual', 'import', 'suggested'))
                    );
                    CREATE TABLE video_subtopics (
                        video_id INTEGER NOT NULL,
                        subtopic_id INTEGER NOT NULL,
                        assignment_source TEXT NOT NULL DEFAULT 'manual',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(video_id, subtopic_id),
                        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                        FOREIGN KEY(subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
                        CHECK (assignment_source IN ('manual', 'import', 'suggested'))
                    );
                    """
                )
                conn.commit()

            with connect(db_path) as conn:
                ensure_schema(conn)
                conn.commit()

            with connect(db_path) as conn:
                channel_id = conn.execute(
                    "SELECT id FROM channels WHERE youtube_channel_id = 'UC123'"
                ).fetchone()[0]
                video_id = conn.execute(
                    "SELECT id FROM videos WHERE youtube_video_id = 'vid1'"
                ).fetchone()[0]
                topic_id = conn.execute(
                    "SELECT id FROM topics WHERE name = 'Health'"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO discovery_runs(channel_id, model, prompt_version) "
                    "VALUES (?, ?, ?)",
                    (channel_id, "stub", "v0"),
                )
                run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                # The repair must allow 'auto' on both junction tables.
                conn.execute(
                    "INSERT INTO video_topics("
                    "  video_id, topic_id, assignment_type, assignment_source, "
                    "  confidence, reason, discovery_run_id"
                    ") VALUES (?, ?, 'secondary', 'auto', 0.7, 'r', ?)",
                    (video_id, topic_id, run_id),
                )
                # Insert a subtopic and assign with 'auto' too.
                conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, ?)",
                    (topic_id, "Sleep"),
                )
                subtopic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO video_subtopics("
                    "  video_id, subtopic_id, assignment_source, "
                    "  confidence, reason, discovery_run_id"
                    ") VALUES (?, ?, 'auto', 0.5, 'r', ?)",
                    (video_id, subtopic_id, run_id),
                )
                conn.commit()

                vt_source = conn.execute(
                    "SELECT assignment_source FROM video_topics "
                    "WHERE video_id = ? AND topic_id = ?",
                    (video_id, topic_id),
                ).fetchone()[0]
                vs_source = conn.execute(
                    "SELECT assignment_source FROM video_subtopics "
                    "WHERE video_id = ? AND subtopic_id = ?",
                    (video_id, subtopic_id),
                ).fetchone()[0]
            self.assertEqual(vt_source, "auto")
            self.assertEqual(vs_source, "auto")

    def test_repair_preserves_existing_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            upsert_videos_for_primary_channel(
                db_path,
                videos=[
                    VideoMetadata(
                        youtube_video_id="vid1",
                        title="Video 1",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health")

            with connect(db_path) as conn:
                conn.executescript(
                    """
                    DROP TABLE IF EXISTS video_topics;
                    CREATE TABLE video_topics (
                        video_id INTEGER NOT NULL,
                        topic_id INTEGER NOT NULL,
                        assignment_type TEXT NOT NULL DEFAULT 'secondary',
                        assignment_source TEXT NOT NULL DEFAULT 'manual',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(video_id, topic_id),
                        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
                        CHECK (assignment_type IN ('primary', 'secondary')),
                        CHECK (assignment_source IN ('manual', 'import', 'suggested'))
                    );
                    """
                )
                video_id = conn.execute(
                    "SELECT id FROM videos WHERE youtube_video_id = 'vid1'"
                ).fetchone()[0]
                topic_id = conn.execute(
                    "SELECT id FROM topics WHERE name = 'Health'"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO video_topics(video_id, topic_id, assignment_type, "
                    "assignment_source) VALUES (?, ?, 'primary', 'manual')",
                    (video_id, topic_id),
                )
                conn.commit()

            with connect(db_path) as conn:
                ensure_schema(conn)
                conn.commit()

            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT assignment_type, assignment_source "
                    "FROM video_topics WHERE video_id = ? AND topic_id = ?",
                    (video_id, topic_id),
                ).fetchone()
            self.assertEqual(row[0], "primary")
            self.assertEqual(row[1], "manual")

    def test_video_topics_accepts_auto_assignment_source(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            upsert_videos_for_primary_channel(
                db_path,
                videos=[
                    VideoMetadata(
                        youtube_video_id="vid1",
                        title="Video 1",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health")
            with connect(db_path) as conn:
                channel_id = conn.execute(
                    "SELECT id FROM channels WHERE youtube_channel_id = 'UC123'"
                ).fetchone()[0]
                video_id = conn.execute(
                    "SELECT id FROM videos WHERE youtube_video_id = 'vid1'"
                ).fetchone()[0]
                topic_id = conn.execute(
                    "SELECT id FROM topics WHERE name = 'Health'"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO discovery_runs(channel_id, model, prompt_version) "
                    "VALUES (?, ?, ?)",
                    (channel_id, "stub", "v0"),
                )
                run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO video_topics("
                    "  video_id, topic_id, assignment_type, assignment_source, "
                    "  confidence, reason, discovery_run_id"
                    ") VALUES (?, ?, 'secondary', 'auto', 0.83, 'matched chapter', ?)",
                    (video_id, topic_id, run_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT assignment_source, confidence, reason, discovery_run_id "
                    "FROM video_topics WHERE video_id = ? AND topic_id = ?",
                    (video_id, topic_id),
                ).fetchone()
            self.assertEqual(row[0], "auto")
            self.assertAlmostEqual(row[1], 0.83)
            self.assertEqual(row[2], "matched chapter")
            self.assertEqual(row[3], run_id)


class StubDiscoveryRunTests(unittest.TestCase):
    def test_run_discovery_persists_run_topics_and_assignments(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = DiscoveryPayload(
                topics=["Health", "Business"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="title mentions sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Business",
                        confidence=0.8,
                        reason="title mentions startup",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Business",
                        confidence=0.4,
                        reason="brain-as-startup metaphor",
                    ),
                ],
            )

            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: payload,
                model="stub",
                prompt_version="stub-v0",
            )

            self.assertIsInstance(run_id, int)
            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                run_row = conn.execute(
                    "SELECT model, prompt_version, status FROM discovery_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                self.assertEqual(run_row["model"], "stub")
                self.assertEqual(run_row["prompt_version"], "stub-v0")
                self.assertEqual(run_row["status"], "success")

                topic_names = {
                    row["name"]
                    for row in conn.execute("SELECT name FROM topics").fetchall()
                }
                self.assertEqual(topic_names, {"Health", "Business"})

                assignment_rows = conn.execute(
                    """
                    SELECT v.youtube_video_id, t.name AS topic_name,
                           vt.assignment_source, vt.confidence, vt.reason,
                           vt.discovery_run_id
                    FROM video_topics vt
                    JOIN videos v ON v.id = vt.video_id
                    JOIN topics t ON t.id = vt.topic_id
                    WHERE vt.discovery_run_id = ?
                    ORDER BY v.youtube_video_id, t.name
                    """,
                    (run_id,),
                ).fetchall()
            self.assertEqual(len(assignment_rows), 3)
            triples = {
                (r["youtube_video_id"], r["topic_name"], r["assignment_source"])
                for r in assignment_rows
            }
            self.assertEqual(
                triples,
                {
                    ("vid1", "Business", "auto"),
                    ("vid1", "Health", "auto"),
                    ("vid2", "Business", "auto"),
                },
            )
            vid1_health = next(
                r
                for r in assignment_rows
                if r["youtube_video_id"] == "vid1" and r["topic_name"] == "Health"
            )
            self.assertAlmostEqual(vid1_health["confidence"], 0.9)
            self.assertEqual(vid1_health["reason"], "title mentions sleep")
            self.assertEqual(vid1_health["discovery_run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
