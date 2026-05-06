from __future__ import annotations

import io
import json
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
from yt_channel_analyzer import cli
from yt_channel_analyzer.discovery import (
    DiscoveryAssignment,
    DiscoveryPayload,
    run_discovery,
    stub_llm,
)
from yt_channel_analyzer.youtube import ChannelMetadata, VideoMetadata


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


class ChapterParsingTests(unittest.TestCase):
    def test_parses_typical_doac_style_description(self) -> None:
        from yt_channel_analyzer.discovery import (
            Chapter,
            parse_chapters_from_description,
        )

        description = (
            "In this episode we cover sleep.\n"
            "\n"
            "0:00 Intro\n"
            "2:15 Why sleep matters\n"
            "15:42 Practical tips\n"
            "1:02:30 Wrap up\n"
            "\n"
            "Sponsored by Acme."
        )
        chapters = parse_chapters_from_description(description)
        self.assertEqual(
            chapters,
            (
                Chapter(0, "Intro"),
                Chapter(135, "Why sleep matters"),
                Chapter(942, "Practical tips"),
                Chapter(3750, "Wrap up"),
            ),
        )

    def test_returns_empty_when_description_missing(self) -> None:
        from yt_channel_analyzer.discovery import parse_chapters_from_description

        self.assertEqual(parse_chapters_from_description(None), ())
        self.assertEqual(parse_chapters_from_description(""), ())

    def test_returns_empty_when_fewer_than_three_timestamps(self) -> None:
        from yt_channel_analyzer.discovery import parse_chapters_from_description

        description = "0:00 Intro\n3:00 Outro\n"
        self.assertEqual(parse_chapters_from_description(description), ())

    def test_returns_empty_when_first_timestamp_is_not_zero(self) -> None:
        from yt_channel_analyzer.discovery import parse_chapters_from_description

        description = "0:30 Hello\n2:00 Middle\n5:00 End\n"
        self.assertEqual(parse_chapters_from_description(description), ())

    def test_returns_empty_when_timestamps_not_monotonic(self) -> None:
        from yt_channel_analyzer.discovery import parse_chapters_from_description

        description = "0:00 Intro\n5:00 Middle\n3:00 Backwards\n"
        self.assertEqual(parse_chapters_from_description(description), ())

    def test_skips_lines_without_timestamps(self) -> None:
        from yt_channel_analyzer.discovery import (
            Chapter,
            parse_chapters_from_description,
        )

        description = (
            "Some prose without a timestamp.\n"
            "0:00 Intro\n"
            "Another prose line.\n"
            "1:00 Topic A\n"
            "2:00 Topic B\n"
        )
        chapters = parse_chapters_from_description(description)
        self.assertEqual(
            chapters,
            (
                Chapter(0, "Intro"),
                Chapter(60, "Topic A"),
                Chapter(120, "Topic B"),
            ),
        )


class DiscoveryVideoChaptersTests(unittest.TestCase):
    def test_run_discovery_passes_parsed_chapters_to_llm(self) -> None:
        from yt_channel_analyzer.discovery import Chapter

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            chapter_desc = (
                "0:00 Intro\n"
                "1:30 Sleep cycles\n"
                "10:00 Q&A\n"
            )
            upsert_videos_for_primary_channel(
                db_path,
                videos=[
                    VideoMetadata(
                        youtube_video_id="vid1",
                        title="With chapters",
                        description=chapter_desc,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid2",
                        title="No chapters",
                        description="just prose",
                        published_at="2026-04-06T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            seen_videos: list = []

            def capturing_llm(videos):
                seen_videos.extend(videos)
                return DiscoveryPayload(
                    topics=["General"],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id=v.youtube_video_id,
                            topic_name="General",
                            confidence=1.0,
                            reason="r",
                        )
                        for v in videos
                    ],
                )

            run_discovery(
                db_path,
                project_name="proj",
                llm=capturing_llm,
                model="stub",
                prompt_version="stub-v0",
            )

            by_id = {v.youtube_video_id: v for v in seen_videos}
            self.assertEqual(
                by_id["vid1"].chapters,
                (
                    Chapter(0, "Intro"),
                    Chapter(90, "Sleep cycles"),
                    Chapter(600, "Q&A"),
                ),
            )
            self.assertEqual(by_id["vid2"].chapters, ())


class StubLLMTests(unittest.TestCase):
    def test_stub_llm_returns_one_topic_covering_all_videos(self) -> None:
        from yt_channel_analyzer.discovery import DiscoveryVideo

        videos = [
            DiscoveryVideo(
                youtube_video_id="vid1",
                title="t1",
                description=None,
                published_at=None,
            ),
            DiscoveryVideo(
                youtube_video_id="vid2",
                title="t2",
                description=None,
                published_at=None,
            ),
        ]
        payload = stub_llm(videos)
        self.assertEqual(len(payload.topics), 1)
        topic = payload.topics[0]
        assigned_ids = {a.youtube_video_id for a in payload.assignments}
        self.assertEqual(assigned_ids, {"vid1", "vid2"})
        for assignment in payload.assignments:
            self.assertEqual(assignment.topic_name, topic)
            self.assertEqual(assignment.confidence, 1.0)


class DiscoverCLITests(unittest.TestCase):
    def test_discover_stub_creates_run_and_assignments(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            exit_code = cli.main(
                [
                    "discover",
                    "--db-path",
                    str(db_path),
                    "--project-name",
                    "proj",
                    "--stub",
                ]
            )
            self.assertEqual(exit_code, 0)

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                runs = conn.execute(
                    "SELECT id, model, prompt_version, status FROM discovery_runs"
                ).fetchall()
                self.assertEqual(len(runs), 1)
                run = runs[0]
                self.assertEqual(run["model"], "stub")
                self.assertEqual(run["status"], "success")

                assignments = conn.execute(
                    "SELECT video_id, topic_id, assignment_source, discovery_run_id "
                    "FROM video_topics WHERE discovery_run_id = ?",
                    (run["id"],),
                ).fetchall()
                self.assertEqual(len(assignments), 2)
                for row in assignments:
                    self.assertEqual(row["assignment_source"], "auto")

    def test_discover_requires_stub_flag(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            with self.assertRaises(SystemExit):
                cli.main(
                    [
                        "discover",
                        "--db-path",
                        str(db_path),
                        "--project-name",
                        "proj",
                    ]
                )


class AnalyzeCLITests(unittest.TestCase):
    def _patch_youtube(self) -> None:
        cli.resolve_canonical_channel_id = lambda channel_input: "UC_resolved"
        cli.fetch_channel_metadata = lambda channel_id: ChannelMetadata(
            youtube_channel_id="UC_resolved",
            title="Diary of a CEO",
            description="A podcast",
            custom_url="@doac",
            published_at="2017-01-01T00:00:00Z",
            thumbnail_url=None,
        )
        cli.fetch_channel_videos = lambda youtube_channel_id, *, limit: [
            VideoMetadata(
                youtube_video_id="vid_a",
                title="Episode A",
                description=None,
                published_at="2026-04-01T00:00:00Z",
                thumbnail_url=None,
            ),
            VideoMetadata(
                youtube_video_id="vid_b",
                title="Episode B",
                description=None,
                published_at="2026-04-02T00:00:00Z",
                thumbnail_url=None,
            ),
        ]

    def setUp(self) -> None:
        from yt_channel_analyzer import youtube
        self._original = (
            cli.resolve_canonical_channel_id,
            cli.fetch_channel_metadata,
            cli.fetch_channel_videos,
        )
        self._youtube = youtube

    def tearDown(self) -> None:
        (
            cli.resolve_canonical_channel_id,
            cli.fetch_channel_metadata,
            cli.fetch_channel_videos,
        ) = self._original

    def test_analyze_chains_setup_ingest_and_discover(self) -> None:
        self._patch_youtube()
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            exit_code = cli.main(
                [
                    "analyze",
                    "--db-path",
                    str(db_path),
                    "--project-name",
                    "doac",
                    "--channel-input",
                    "@doac",
                    "--stub",
                ]
            )
            self.assertEqual(exit_code, 0)

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                project = conn.execute(
                    "SELECT id, name FROM projects WHERE name = 'doac'"
                ).fetchone()
                self.assertIsNotNone(project)

                channel = conn.execute(
                    "SELECT id, youtube_channel_id, is_primary "
                    "FROM channels WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()
                self.assertEqual(channel["youtube_channel_id"], "UC_resolved")
                self.assertEqual(channel["is_primary"], 1)

                videos = conn.execute(
                    "SELECT youtube_video_id FROM videos WHERE channel_id = ? "
                    "ORDER BY youtube_video_id",
                    (channel["id"],),
                ).fetchall()
                self.assertEqual(
                    [v["youtube_video_id"] for v in videos],
                    ["vid_a", "vid_b"],
                )

                runs = conn.execute(
                    "SELECT id, model, status FROM discovery_runs"
                ).fetchall()
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["model"], "stub")
                self.assertEqual(runs[0]["status"], "success")

                assignments = conn.execute(
                    "SELECT assignment_source FROM video_topics "
                    "WHERE discovery_run_id = ?",
                    (runs[0]["id"],),
                ).fetchall()
                self.assertEqual(len(assignments), 2)
                for row in assignments:
                    self.assertEqual(row["assignment_source"], "auto")

    def test_analyze_requires_stub_flag(self) -> None:
        self._patch_youtube()
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with self.assertRaises(SystemExit):
                cli.main(
                    [
                        "analyze",
                        "--db-path",
                        str(db_path),
                        "--project-name",
                        "doac",
                        "--channel-input",
                        "@doac",
                    ]
                )


class DiscoveryStatePayloadTests(unittest.TestCase):
    def test_state_payload_has_no_discovery_topic_map_when_no_run(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = build_state_payload(db_path)
            self.assertIn("discovery_topic_map", payload)
            self.assertIsNone(payload["discovery_topic_map"])

    def test_state_payload_discovery_topic_map_reflects_latest_run(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_payload = DiscoveryPayload(
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
                llm=lambda videos: run_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topic_map = payload["discovery_topic_map"]
            self.assertIsNotNone(topic_map)
            self.assertEqual(topic_map["run_id"], run_id)
            self.assertEqual(topic_map["model"], "stub")
            self.assertEqual(topic_map["prompt_version"], "stub-v0")
            self.assertEqual(topic_map["status"], "success")

            topics_by_name = {t["name"]: t for t in topic_map["topics"]}
            self.assertEqual(set(topics_by_name), {"Health", "Business"})
            self.assertEqual(topics_by_name["Business"]["episode_count"], 2)
            self.assertEqual(topics_by_name["Health"]["episode_count"], 1)
            # Sort order: highest episode_count first.
            self.assertEqual(topic_map["topics"][0]["name"], "Business")
            self.assertAlmostEqual(topics_by_name["Health"]["avg_confidence"], 0.9)
            self.assertAlmostEqual(topics_by_name["Business"]["avg_confidence"], 0.6)

    def test_state_payload_discovery_topic_map_uses_only_latest_run(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            first_payload = DiscoveryPayload(
                topics=["Old Topic"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Old Topic",
                        confidence=0.5,
                        reason="early run",
                    ),
                ],
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: first_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            second_payload = DiscoveryPayload(
                topics=["Fresh Topic"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Fresh Topic",
                        confidence=0.95,
                        reason="latest run",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Fresh Topic",
                        confidence=0.85,
                        reason="latest run",
                    ),
                ],
            )
            second_run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: second_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topic_map = payload["discovery_topic_map"]
            self.assertEqual(topic_map["run_id"], second_run_id)
            names = {t["name"] for t in topic_map["topics"]}
            self.assertEqual(names, {"Fresh Topic"})


class DiscoveryTopicMapHTMLTests(unittest.TestCase):
    def test_html_page_contains_discovery_topic_map_section(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn('id="discovery-topic-map-grid"', html)
        self.assertIn("Auto-Discovered Topics", html)

    def test_html_page_wires_render_discovery_topic_map(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function renderDiscoveryTopicMap", html)
        self.assertIn(
            "renderDiscoveryTopicMap(payload.discovery_topic_map)", html
        )

    def test_ui_revision_advances_for_discovery_topic_map_panel(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)

    def test_inline_script_parses_as_javascript(self) -> None:
        """Run `node --check` on the rendered inline <script>. Catches JS
        breakage from Python triple-quoted-string escape mistakes (e.g. a
        bare ``\\n`` inside a single-quoted JS string)."""
        import re
        import shutil
        import subprocess
        import tempfile

        from yt_channel_analyzer.review_ui import ReviewUIApp

        node = shutil.which("node")
        if node is None:
            self.skipTest("node not available")
        html = ReviewUIApp._render_html_page()
        match = re.search(r"<script>(.*?)</script>", html, re.S)
        self.assertIsNotNone(match, "rendered HTML has no <script> block")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False
        ) as fh:
            fh.write(match.group(1))
            js_path = fh.name
        result = subprocess.run(
            [node, "--check", js_path], capture_output=True, text=True
        )
        self.assertEqual(
            result.returncode,
            0,
            f"inline <script> failed JS syntax check:\n{result.stderr}",
        )


class DiscoveryTopicMapEpisodesPayloadTests(unittest.TestCase):
    def test_topic_includes_episode_list_with_reason_and_confidence(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_payload = DiscoveryPayload(
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
                ],
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: run_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topic_map = payload["discovery_topic_map"]
            topics_by_name = {t["name"]: t for t in topic_map["topics"]}
            health_episodes = topics_by_name["Health"]["episodes"]
            self.assertEqual(len(health_episodes), 1)
            ep = health_episodes[0]
            self.assertEqual(ep["youtube_video_id"], "vid1")
            self.assertEqual(ep["title"], "Sleep and the brain")
            self.assertIn("thumbnail_url", ep)
            self.assertIn("published_at", ep)
            self.assertEqual(ep["reason"], "title mentions sleep")
            self.assertAlmostEqual(ep["confidence"], 0.9)

            business_episodes = topics_by_name["Business"]["episodes"]
            self.assertEqual(
                {e["youtube_video_id"] for e in business_episodes},
                {"vid2"},
            )

    def test_multi_topic_episode_appears_under_each_topic(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_payload = DiscoveryPayload(
                topics=["Health", "Business"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="title mentions sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Business",
                        confidence=0.4,
                        reason="brain-as-startup metaphor",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Business",
                        confidence=0.8,
                        reason="title mentions startup",
                    ),
                ],
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: run_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topics_by_name = {
                t["name"]: t for t in payload["discovery_topic_map"]["topics"]
            }
            health_ids = {e["youtube_video_id"] for e in topics_by_name["Health"]["episodes"]}
            business_ids = {e["youtube_video_id"] for e in topics_by_name["Business"]["episodes"]}
            self.assertIn("vid1", health_ids)
            self.assertIn("vid1", business_ids)

            business_episodes = topics_by_name["Business"]["episodes"]
            confidences = [e["confidence"] for e in business_episodes]
            self.assertEqual(confidences, sorted(confidences, reverse=True))
            vid1_business = next(
                e for e in business_episodes if e["youtube_video_id"] == "vid1"
            )
            self.assertEqual(vid1_business["reason"], "brain-as-startup metaphor")
            self.assertAlmostEqual(vid1_business["confidence"], 0.4)


class DiscoveryTopicEpisodesHTMLTests(unittest.TestCase):
    def test_html_page_has_episode_list_renderer_hook(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-episode-list", html)
        self.assertIn("topic.episodes", html)

    def test_ui_revision_advances_for_episode_list(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


class DiscoveryEpisodeSortHTMLTests(unittest.TestCase):
    def test_html_page_has_episode_sort_dropdown_markup(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-episode-sort", html)
        self.assertIn('value="recency"', html)
        self.assertIn('value="confidence"', html)

    def test_html_page_defines_sort_function(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function sortDiscoveryEpisodes", html)

    def test_default_sort_is_recency(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("DEFAULT_DISCOVERY_SORT", html)
        self.assertIn("'recency'", html)

    def test_ui_revision_advances_for_episode_sort(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


class DiscoveryTopicRenameTests(unittest.TestCase):
    def _call_app(
        self,
        app,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[str, str]:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(payload),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body_bytes = b"".join(app(environ, start_response))
        return str(captured["status"]), body_bytes.decode("utf-8")

    def _seed_run(self, db_path: Path) -> None:
        _seed_channel_with_videos(db_path)
        run_discovery(
            db_path,
            project_name="proj",
            llm=lambda videos: DiscoveryPayload(
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
                ],
            ),
            model="stub",
            prompt_version="stub-v0",
        )

    def test_rename_endpoint_renames_topic_in_db(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/rename",
                body={"current_name": "Health", "new_name": "Wellness"},
            )

            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload.get("ok"))

            with connect(db_path) as conn:
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM topics").fetchall()
                }
            self.assertIn("Wellness", names)
            self.assertNotIn("Health", names)

    def test_rename_endpoint_updates_state_payload(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp, build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)

            app = ReviewUIApp(db_path)
            self._call_app(
                app,
                "POST",
                "/api/discovery/topic/rename",
                body={"current_name": "Health", "new_name": "Wellness"},
            )

            payload = build_state_payload(db_path)
            topics_by_name = {
                t["name"]: t for t in payload["discovery_topic_map"]["topics"]
            }
            self.assertIn("Wellness", topics_by_name)
            self.assertNotIn("Health", topics_by_name)
            wellness_episode_ids = {
                e["youtube_video_id"]
                for e in topics_by_name["Wellness"]["episodes"]
            }
            self.assertEqual(wellness_episode_ids, {"vid1"})

    def test_rename_endpoint_rejects_unknown_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/rename",
                body={"current_name": "DoesNotExist", "new_name": "Nope"},
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("not found", body.lower())

    def test_rename_endpoint_rejects_collision_with_existing_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/rename",
                body={"current_name": "Health", "new_name": "Business"},
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("already exists", body.lower())

    def test_html_page_defines_rename_discovery_topic_function(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function renameDiscoveryTopic", html)
        self.assertIn("/api/discovery/topic/rename", html)

    def test_html_page_renders_rename_button_per_discovery_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-topic-rename", html)

    def test_ui_revision_advances_for_rename(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


class DiscoveryTopicMergeTests(unittest.TestCase):
    def _call_app(
        self,
        app,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[str, str]:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(payload),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body_bytes = b"".join(app(environ, start_response))
        return str(captured["status"]), body_bytes.decode("utf-8")

    def _seed_two_topics(self, db_path: Path) -> None:
        _seed_channel_with_videos(db_path)
        run_discovery(
            db_path,
            project_name="proj",
            llm=lambda videos: DiscoveryPayload(
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
                ],
            ),
            model="stub",
            prompt_version="stub-v0",
        )

    def test_merge_topics_repoints_assignments_and_deletes_source(self) -> None:
        from yt_channel_analyzer.db import merge_topics

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_two_topics(db_path)

            stats = merge_topics(
                db_path,
                project_name="proj",
                source_name="Health",
                target_name="Business",
            )
            self.assertEqual(stats["moved_episode_assignments"], 1)
            self.assertEqual(stats["dropped_episode_collisions"], 0)

            with connect(db_path) as conn:
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM topics").fetchall()
                }
                self.assertEqual(names, {"Business"})

                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id, t.name, vt.confidence, vt.reason
                    FROM video_topics vt
                    JOIN topics t ON t.id = vt.topic_id
                    JOIN videos v ON v.id = vt.video_id
                    ORDER BY v.youtube_video_id
                    """
                ).fetchall()
            assignments = {(r[0], r[1]) for r in rows}
            self.assertEqual(
                assignments,
                {("vid1", "Business"), ("vid2", "Business")},
            )
            vid1_row = next(r for r in rows if r[0] == "vid1")
            self.assertAlmostEqual(vid1_row[2], 0.9)
            self.assertEqual(vid1_row[3], "title mentions sleep")

    def test_merge_topics_drops_colliding_source_row_keeping_target(self) -> None:
        from yt_channel_analyzer.db import merge_topics

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            # Both topics get vid1 — target's reason should win after merge.
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: DiscoveryPayload(
                    topics=["Health", "Business"],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="Health",
                            confidence=0.9,
                            reason="source reason",
                        ),
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="Business",
                            confidence=0.4,
                            reason="target reason",
                        ),
                    ],
                ),
                model="stub",
                prompt_version="stub-v0",
            )

            stats = merge_topics(
                db_path,
                project_name="proj",
                source_name="Health",
                target_name="Business",
            )
            self.assertEqual(stats["dropped_episode_collisions"], 1)
            self.assertEqual(stats["moved_episode_assignments"], 0)

            with connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT vt.confidence, vt.reason
                    FROM video_topics vt
                    JOIN topics t ON t.id = vt.topic_id
                    JOIN videos v ON v.id = vt.video_id
                    WHERE v.youtube_video_id = 'vid1' AND t.name = 'Business'
                    """
                ).fetchone()
            self.assertAlmostEqual(row[0], 0.4)
            self.assertEqual(row[1], "target reason")

    def test_merge_topics_rejects_unknown_source(self) -> None:
        from yt_channel_analyzer.db import merge_topics

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_two_topics(db_path)
            with self.assertRaises(ValueError):
                merge_topics(
                    db_path,
                    project_name="proj",
                    source_name="Nope",
                    target_name="Business",
                )

    def test_merge_topics_rejects_unknown_target(self) -> None:
        from yt_channel_analyzer.db import merge_topics

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_two_topics(db_path)
            with self.assertRaises(ValueError):
                merge_topics(
                    db_path,
                    project_name="proj",
                    source_name="Health",
                    target_name="Nope",
                )

    def test_merge_topics_rejects_same_topic(self) -> None:
        from yt_channel_analyzer.db import merge_topics

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_two_topics(db_path)
            with self.assertRaises(ValueError):
                merge_topics(
                    db_path,
                    project_name="proj",
                    source_name="Health",
                    target_name="Health",
                )

    def test_merge_topics_repoints_subtopics_and_handles_collisions(self) -> None:
        from yt_channel_analyzer.db import merge_topics

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_two_topics(db_path)
            with connect(db_path) as conn:
                source_id = conn.execute(
                    "SELECT id FROM topics WHERE name = 'Health'"
                ).fetchone()[0]
                target_id = conn.execute(
                    "SELECT id FROM topics WHERE name = 'Business'"
                ).fetchone()[0]
                # Source-only subtopic.
                conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Sleep')",
                    (source_id,),
                )
                # Colliding subtopic on both topics.
                conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Habits')",
                    (source_id,),
                )
                conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Habits')",
                    (target_id,),
                )
                conn.commit()

            stats = merge_topics(
                db_path,
                project_name="proj",
                source_name="Health",
                target_name="Business",
            )
            self.assertEqual(stats["merged_subtopic_collisions"], 1)
            self.assertEqual(stats["moved_subtopics"], 1)

            with connect(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT s.name, t.name AS topic_name
                    FROM subtopics s
                    JOIN topics t ON t.id = s.topic_id
                    ORDER BY s.name
                    """
                ).fetchall()
            self.assertEqual(
                [(r[0], r[1]) for r in rows],
                [("Habits", "Business"), ("Sleep", "Business")],
            )

    def test_merge_endpoint_merges_topics_in_db(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_two_topics(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/merge",
                body={"source_name": "Health", "target_name": "Business"},
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload.get("ok"))

            with connect(db_path) as conn:
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM topics").fetchall()
                }
            self.assertEqual(names, {"Business"})

    def test_merge_endpoint_rejects_unknown_source(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_two_topics(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/merge",
                body={"source_name": "Nope", "target_name": "Business"},
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("not found", body.lower())

    def test_html_page_defines_merge_discovery_topic_function(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function mergeDiscoveryTopic", html)
        self.assertIn("/api/discovery/topic/merge", html)

    def test_html_page_renders_merge_button_per_discovery_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-topic-merge", html)

    def test_ui_revision_advances_for_merge(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


class DiscoveryTopicSplitTests(unittest.TestCase):
    def _call_app(
        self,
        app,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[str, str]:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(payload),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body_bytes = b"".join(app(environ, start_response))
        return str(captured["status"]), body_bytes.decode("utf-8")

    def _seed_one_topic_two_videos(self, db_path: Path) -> None:
        _seed_channel_with_videos(db_path)
        run_discovery(
            db_path,
            project_name="proj",
            llm=lambda videos: DiscoveryPayload(
                topics=["Health"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="title mentions sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=0.6,
                        reason="metaphorical health",
                    ),
                ],
            ),
            model="stub",
            prompt_version="stub-v0",
        )

    def test_split_topic_creates_new_topic_and_moves_selected_episodes(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)

            stats = split_topic(
                db_path,
                project_name="proj",
                source_name="Health",
                new_name="Sleep",
                youtube_video_ids=["vid1"],
            )
            self.assertEqual(stats["moved_episode_assignments"], 1)
            self.assertEqual(stats["dropped_subtopic_assignments"], 0)
            self.assertEqual(stats["skipped_video_ids"], [])

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                names = {
                    row["name"]
                    for row in conn.execute("SELECT name FROM topics").fetchall()
                }
                self.assertEqual(names, {"Health", "Sleep"})
                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id, t.name AS topic_name,
                           vt.confidence, vt.reason, vt.assignment_source
                    FROM video_topics vt
                    JOIN topics t ON t.id = vt.topic_id
                    JOIN videos v ON v.id = vt.video_id
                    ORDER BY t.name, v.youtube_video_id
                    """
                ).fetchall()
            assignments = {(r["youtube_video_id"], r["topic_name"]) for r in rows}
            self.assertEqual(
                assignments, {("vid2", "Health"), ("vid1", "Sleep")}
            )
            sleep_row = next(r for r in rows if r["topic_name"] == "Sleep")
            # Per-row provenance preserved during the move.
            self.assertAlmostEqual(sleep_row["confidence"], 0.9)
            self.assertEqual(sleep_row["reason"], "title mentions sleep")
            self.assertEqual(sleep_row["assignment_source"], "auto")

    def test_split_topic_drops_orphan_subtopic_rows_for_moved_videos(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)
            with connect(db_path) as conn:
                source_id = conn.execute(
                    "SELECT id FROM topics WHERE name = 'Health'"
                ).fetchone()[0]
                vid1_id = conn.execute(
                    "SELECT id FROM videos WHERE youtube_video_id = 'vid1'"
                ).fetchone()[0]
                vid2_id = conn.execute(
                    "SELECT id FROM videos WHERE youtube_video_id = 'vid2'"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Routines')",
                    (source_id,),
                )
                sub_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO video_subtopics(video_id, subtopic_id, "
                    "assignment_source) VALUES (?, ?, 'auto')",
                    (vid1_id, sub_id),
                )
                conn.execute(
                    "INSERT INTO video_subtopics(video_id, subtopic_id, "
                    "assignment_source) VALUES (?, ?, 'auto')",
                    (vid2_id, sub_id),
                )
                conn.commit()

            stats = split_topic(
                db_path,
                project_name="proj",
                source_name="Health",
                new_name="Sleep",
                youtube_video_ids=["vid1"],
            )
            self.assertEqual(stats["dropped_subtopic_assignments"], 1)

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id FROM video_subtopics"
                ).fetchall()
            self.assertEqual({r[0] for r in rows}, {vid2_id})

    def test_split_topic_reports_skipped_video_ids(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)

            stats = split_topic(
                db_path,
                project_name="proj",
                source_name="Health",
                new_name="Sleep",
                youtube_video_ids=["vid1", "ghost"],
            )
            self.assertEqual(stats["moved_episode_assignments"], 1)
            self.assertEqual(stats["skipped_video_ids"], ["ghost"])

    def test_split_topic_rejects_existing_new_name(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: DiscoveryPayload(
                    topics=["Health", "Business"],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="Health",
                            confidence=0.9,
                            reason="r",
                        ),
                        DiscoveryAssignment(
                            youtube_video_id="vid2",
                            topic_name="Business",
                            confidence=0.8,
                            reason="r",
                        ),
                    ],
                ),
                model="stub",
                prompt_version="stub-v0",
            )
            with self.assertRaises(ValueError):
                split_topic(
                    db_path,
                    project_name="proj",
                    source_name="Health",
                    new_name="Business",
                    youtube_video_ids=["vid1"],
                )

    def test_split_topic_rejects_unknown_source(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)
            with self.assertRaises(ValueError):
                split_topic(
                    db_path,
                    project_name="proj",
                    source_name="Nope",
                    new_name="Sleep",
                    youtube_video_ids=["vid1"],
                )

    def test_split_topic_rejects_same_source_and_new_name(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)
            with self.assertRaises(ValueError):
                split_topic(
                    db_path,
                    project_name="proj",
                    source_name="Health",
                    new_name="Health",
                    youtube_video_ids=["vid1"],
                )

    def test_split_topic_rejects_when_no_supplied_video_is_in_source(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)
            with self.assertRaises(ValueError):
                split_topic(
                    db_path,
                    project_name="proj",
                    source_name="Health",
                    new_name="Sleep",
                    youtube_video_ids=["ghost"],
                )

    def test_split_topic_requires_video_ids(self) -> None:
        from yt_channel_analyzer.db import split_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)
            with self.assertRaises(ValueError):
                split_topic(
                    db_path,
                    project_name="proj",
                    source_name="Health",
                    new_name="Sleep",
                    youtube_video_ids=[],
                )

    def test_split_endpoint_splits_topic_in_db(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/split",
                body={
                    "source_name": "Health",
                    "new_name": "Sleep",
                    "youtube_video_ids": ["vid1"],
                },
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload.get("ok"))

            with connect(db_path) as conn:
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM topics").fetchall()
                }
            self.assertEqual(names, {"Health", "Sleep"})

    def test_split_endpoint_rejects_unknown_source(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/split",
                body={
                    "source_name": "Nope",
                    "new_name": "Sleep",
                    "youtube_video_ids": ["vid1"],
                },
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("not found", body.lower())

    def test_split_endpoint_rejects_empty_video_ids(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_one_topic_two_videos(db_path)
            app = ReviewUIApp(db_path)
            status, _body = self._call_app(
                app,
                "POST",
                "/api/discovery/topic/split",
                body={
                    "source_name": "Health",
                    "new_name": "Sleep",
                    "youtube_video_ids": [],
                },
            )
            self.assertEqual(status, "400 Bad Request")

    def test_html_page_defines_split_discovery_topic_function(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function splitDiscoveryTopic", html)
        self.assertIn("/api/discovery/topic/split", html)

    def test_html_page_renders_split_button_per_discovery_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-topic-split", html)

    def test_ui_revision_advances_for_split(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


class DiscoveryEpisodeMoveSubtopicTests(unittest.TestCase):
    def _call_app(
        self,
        app,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[str, str]:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(payload),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body_bytes = b"".join(app(environ, start_response))
        return str(captured["status"]), body_bytes.decode("utf-8")

    def _seed_topic_with_two_subtopics(self, db_path: Path) -> dict[str, int]:
        """Seed proj/Health with subtopics Sleep & Stress, vid1 in Sleep."""
        _seed_channel_with_videos(db_path)
        run_discovery(
            db_path,
            project_name="proj",
            llm=lambda videos: DiscoveryPayload(
                topics=["Health"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="r1",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=0.7,
                        reason="r2",
                    ),
                ],
            ),
            model="stub",
            prompt_version="stub-v0",
        )
        with connect(db_path) as conn:
            health_id = conn.execute(
                "SELECT id FROM topics WHERE name = 'Health'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Sleep')",
                (health_id,),
            )
            sleep_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Stress')",
                (health_id,),
            )
            stress_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            vid1_id = conn.execute(
                "SELECT id FROM videos WHERE youtube_video_id = 'vid1'"
            ).fetchone()[0]
            vid2_id = conn.execute(
                "SELECT id FROM videos WHERE youtube_video_id = 'vid2'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO video_subtopics(video_id, subtopic_id, "
                "assignment_source) VALUES (?, ?, 'auto')",
                (vid1_id, sleep_id),
            )
            conn.commit()
        return {
            "health_id": health_id,
            "sleep_id": sleep_id,
            "stress_id": stress_id,
            "vid1_id": vid1_id,
            "vid2_id": vid2_id,
        }

    def test_move_episode_subtopic_repoints_existing_row(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_two_subtopics(db_path)

            stats = move_episode_subtopic(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid1",
                target_subtopic_name="Stress",
            )
            self.assertEqual(stats["moved"], 1)
            self.assertEqual(stats["inserted"], 0)
            self.assertEqual(stats["previous_subtopic_name"], "Sleep")
            self.assertEqual(stats["target_subtopic_id"], ids["stress_id"])

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id, subtopic_id FROM video_subtopics"
                ).fetchall()
            self.assertEqual(rows, [(ids["vid1_id"], ids["stress_id"])])

    def test_move_episode_subtopic_inserts_when_no_existing_row(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_two_subtopics(db_path)

            stats = move_episode_subtopic(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid2",
                target_subtopic_name="Stress",
            )
            self.assertEqual(stats["moved"], 0)
            self.assertEqual(stats["inserted"], 1)
            self.assertIsNone(stats["previous_subtopic_name"])
            self.assertEqual(stats["target_subtopic_id"], ids["stress_id"])

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id, subtopic_id, assignment_source "
                    "FROM video_subtopics ORDER BY video_id"
                ).fetchall()
            self.assertEqual(
                rows,
                [
                    (ids["vid1_id"], ids["sleep_id"], "auto"),
                    (ids["vid2_id"], ids["stress_id"], "manual"),
                ],
            )

    def test_move_episode_subtopic_noop_when_already_on_target(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_two_subtopics(db_path)

            stats = move_episode_subtopic(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid1",
                target_subtopic_name="Sleep",
            )
            self.assertEqual(stats["moved"], 0)
            self.assertEqual(stats["inserted"], 0)
            self.assertEqual(stats["previous_subtopic_name"], "Sleep")

    def test_move_episode_subtopic_rejects_target_under_other_topic(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_two_subtopics(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO topics(project_id, name)
                    VALUES ((SELECT id FROM projects WHERE name = 'proj'), 'Business')
                    """
                )
                business_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Founders')",
                    (business_id,),
                )
                conn.commit()
            with self.assertRaises(ValueError):
                move_episode_subtopic(
                    db_path,
                    project_name="proj",
                    topic_name="Health",
                    youtube_video_id="vid1",
                    target_subtopic_name="Founders",
                )

    def test_move_episode_subtopic_rejects_unknown_topic(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_two_subtopics(db_path)
            with self.assertRaises(ValueError):
                move_episode_subtopic(
                    db_path,
                    project_name="proj",
                    topic_name="Nope",
                    youtube_video_id="vid1",
                    target_subtopic_name="Stress",
                )

    def test_move_episode_subtopic_rejects_unknown_subtopic(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_two_subtopics(db_path)
            with self.assertRaises(ValueError):
                move_episode_subtopic(
                    db_path,
                    project_name="proj",
                    topic_name="Health",
                    youtube_video_id="vid1",
                    target_subtopic_name="Ghost",
                )

    def test_move_episode_subtopic_rejects_unknown_video(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_two_subtopics(db_path)
            with self.assertRaises(ValueError):
                move_episode_subtopic(
                    db_path,
                    project_name="proj",
                    topic_name="Health",
                    youtube_video_id="ghost",
                    target_subtopic_name="Stress",
                )

    def test_move_episode_subtopic_rejects_video_not_on_topic(self) -> None:
        from yt_channel_analyzer.db import move_episode_subtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_two_subtopics(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO topics(project_id, name)
                    VALUES ((SELECT id FROM projects WHERE name = 'proj'), 'Business')
                    """
                )
                business_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Founders')",
                    (business_id,),
                )
                # Re-point vid2 from Health to Business so it is *not* on Health.
                conn.execute(
                    "UPDATE video_topics SET topic_id = ? "
                    "WHERE video_id = (SELECT id FROM videos WHERE "
                    "youtube_video_id = 'vid2')",
                    (business_id,),
                )
                conn.commit()
            with self.assertRaises(ValueError):
                move_episode_subtopic(
                    db_path,
                    project_name="proj",
                    topic_name="Health",
                    youtube_video_id="vid2",
                    target_subtopic_name="Stress",
                )

    def test_move_endpoint_moves_in_db(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_two_subtopics(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/episode/move-subtopic",
                body={
                    "topic_name": "Health",
                    "youtube_video_id": "vid1",
                    "target_subtopic_name": "Stress",
                },
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload.get("ok"))

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id, subtopic_id FROM video_subtopics"
                ).fetchall()
            self.assertEqual(rows, [(ids["vid1_id"], ids["stress_id"])])

    def test_move_endpoint_rejects_unknown_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_two_subtopics(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/episode/move-subtopic",
                body={
                    "topic_name": "Nope",
                    "youtube_video_id": "vid1",
                    "target_subtopic_name": "Stress",
                },
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("not found", body.lower())

    def test_html_page_defines_move_episode_subtopic_function(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function moveEpisodeSubtopic", html)
        self.assertIn("/api/discovery/episode/move-subtopic", html)

    def test_html_page_renders_move_button_per_subtopic_video(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("subtopic-video-move", html)

    def test_ui_revision_advances_for_move(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


class DiscoveryEpisodeMarkWrongTests(unittest.TestCase):
    def _call_app(
        self,
        app,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[str, str]:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(payload),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body_bytes = b"".join(app(environ, start_response))
        return str(captured["status"]), body_bytes.decode("utf-8")

    def _seed_topic_with_subtopic(self, db_path: Path) -> dict[str, int]:
        """Seed proj/Health with subtopic Sleep; vid1 on Health & Sleep, vid2 on Health only."""
        _seed_channel_with_videos(db_path)
        run_discovery(
            db_path,
            project_name="proj",
            llm=lambda videos: DiscoveryPayload(
                topics=["Health"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="r1",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=0.7,
                        reason="r2",
                    ),
                ],
            ),
            model="stub",
            prompt_version="stub-v0",
        )
        with connect(db_path) as conn:
            health_id = conn.execute(
                "SELECT id FROM topics WHERE name = 'Health'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Sleep')",
                (health_id,),
            )
            sleep_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            vid1_id = conn.execute(
                "SELECT id FROM videos WHERE youtube_video_id = 'vid1'"
            ).fetchone()[0]
            vid2_id = conn.execute(
                "SELECT id FROM videos WHERE youtube_video_id = 'vid2'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO video_subtopics(video_id, subtopic_id, "
                "assignment_source) VALUES (?, ?, 'auto')",
                (vid1_id, sleep_id),
            )
            conn.commit()
        return {
            "health_id": health_id,
            "sleep_id": sleep_id,
            "vid1_id": vid1_id,
            "vid2_id": vid2_id,
        }

    def test_mark_wrong_topic_removes_video_topics_row(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_subtopic(db_path)

            stats = mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid2",
            )
            self.assertEqual(stats["topic_id"], ids["health_id"])
            self.assertIsNone(stats["subtopic_id"])
            self.assertIsInstance(stats["event_id"], int)

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id FROM video_topics WHERE topic_id = ?",
                    (ids["health_id"],),
                ).fetchall()
            self.assertEqual(rows, [(ids["vid1_id"],)])

    def test_mark_wrong_topic_also_drops_video_subtopics_under_topic(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_subtopic(db_path)

            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid1",
            )

            with connect(db_path) as conn:
                topic_rows = conn.execute(
                    "SELECT video_id FROM video_topics WHERE topic_id = ?",
                    (ids["health_id"],),
                ).fetchall()
                subtopic_rows = conn.execute(
                    "SELECT video_id FROM video_subtopics WHERE subtopic_id = ?",
                    (ids["sleep_id"],),
                ).fetchall()
            self.assertEqual(topic_rows, [(ids["vid2_id"],)])
            self.assertEqual(subtopic_rows, [])

    def test_mark_wrong_subtopic_removes_only_video_subtopics_row(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_subtopic(db_path)

            stats = mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid1",
                subtopic_name="Sleep",
            )
            self.assertEqual(stats["subtopic_id"], ids["sleep_id"])

            with connect(db_path) as conn:
                topic_rows = conn.execute(
                    "SELECT video_id FROM video_topics WHERE topic_id = ?",
                    (ids["health_id"],),
                ).fetchall()
                subtopic_rows = conn.execute(
                    "SELECT video_id FROM video_subtopics WHERE subtopic_id = ?",
                    (ids["sleep_id"],),
                ).fetchall()
            self.assertEqual(
                sorted(topic_rows),
                sorted([(ids["vid1_id"],), (ids["vid2_id"],)]),
            )
            self.assertEqual(subtopic_rows, [])

    def test_mark_wrong_records_event_in_wrong_assignments(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_subtopic(db_path)

            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid2",
                reason="off-topic",
            )

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id, topic_id, subtopic_id, reason "
                    "FROM wrong_assignments"
                ).fetchall()
            self.assertEqual(
                rows,
                [(ids["vid2_id"], ids["health_id"], None, "off-topic")],
            )

    def test_mark_wrong_subtopic_records_subtopic_id(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_subtopic(db_path)

            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Health",
                youtube_video_id="vid1",
                subtopic_name="Sleep",
            )

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id, topic_id, subtopic_id "
                    "FROM wrong_assignments"
                ).fetchall()
            self.assertEqual(
                rows,
                [(ids["vid1_id"], ids["health_id"], ids["sleep_id"])],
            )

    def test_mark_wrong_rejects_unknown_topic(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_subtopic(db_path)
            with self.assertRaises(ValueError):
                mark_assignment_wrong(
                    db_path,
                    project_name="proj",
                    topic_name="Nope",
                    youtube_video_id="vid1",
                )

    def test_mark_wrong_rejects_unknown_video(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_subtopic(db_path)
            with self.assertRaises(ValueError):
                mark_assignment_wrong(
                    db_path,
                    project_name="proj",
                    topic_name="Health",
                    youtube_video_id="ghost",
                )

    def test_mark_wrong_rejects_unknown_subtopic(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_subtopic(db_path)
            with self.assertRaises(ValueError):
                mark_assignment_wrong(
                    db_path,
                    project_name="proj",
                    topic_name="Health",
                    youtube_video_id="vid1",
                    subtopic_name="Ghost",
                )

    def test_mark_wrong_rejects_video_not_on_topic(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_subtopic(db_path)
            with connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO topics(project_id, name)
                    VALUES ((SELECT id FROM projects WHERE name = 'proj'), 'Business')
                    """
                )
                conn.commit()
            with self.assertRaises(ValueError):
                mark_assignment_wrong(
                    db_path,
                    project_name="proj",
                    topic_name="Business",
                    youtube_video_id="vid1",
                )

    def test_mark_wrong_rejects_video_not_on_subtopic(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_subtopic(db_path)
            with self.assertRaises(ValueError):
                mark_assignment_wrong(
                    db_path,
                    project_name="proj",
                    topic_name="Health",
                    youtube_video_id="vid2",
                    subtopic_name="Sleep",
                )

    def test_mark_wrong_endpoint_removes_topic_assignment(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_subtopic(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/episode/mark-wrong",
                body={
                    "topic_name": "Health",
                    "youtube_video_id": "vid2",
                },
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload.get("ok"))

            with connect(db_path) as conn:
                topic_rows = conn.execute(
                    "SELECT video_id FROM video_topics WHERE topic_id = ?",
                    (ids["health_id"],),
                ).fetchall()
                event_count = conn.execute(
                    "SELECT COUNT(*) FROM wrong_assignments"
                ).fetchone()[0]
            self.assertEqual(topic_rows, [(ids["vid1_id"],)])
            self.assertEqual(event_count, 1)

    def test_mark_wrong_endpoint_removes_subtopic_assignment(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            ids = self._seed_topic_with_subtopic(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/episode/mark-wrong",
                body={
                    "topic_name": "Health",
                    "youtube_video_id": "vid1",
                    "subtopic_name": "Sleep",
                },
            )
            self.assertEqual(status, "200 OK")
            with connect(db_path) as conn:
                subtopic_rows = conn.execute(
                    "SELECT video_id FROM video_subtopics WHERE subtopic_id = ?",
                    (ids["sleep_id"],),
                ).fetchall()
                topic_rows = conn.execute(
                    "SELECT video_id FROM video_topics WHERE topic_id = ?",
                    (ids["health_id"],),
                ).fetchall()
            self.assertEqual(subtopic_rows, [])
            self.assertEqual(
                sorted(topic_rows),
                sorted([(ids["vid1_id"],), (ids["vid2_id"],)]),
            )

    def test_mark_wrong_endpoint_rejects_unknown_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_topic_with_subtopic(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/discovery/episode/mark-wrong",
                body={
                    "topic_name": "Nope",
                    "youtube_video_id": "vid1",
                },
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("not found", body.lower())

    def test_html_page_defines_mark_episode_wrong_function(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function markEpisodeWrong", html)
        self.assertIn("/api/discovery/episode/mark-wrong", html)

    def test_html_page_renders_wrong_button_per_episode(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-episode-wrong", html)
        self.assertIn("subtopic-video-wrong", html)

    def test_ui_revision_advances_for_mark_wrong(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


class DiscoveryLowConfidenceThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        import os

        self._saved_env = os.environ.pop("YTA_LOW_CONFIDENCE_THRESHOLD", None)

    def tearDown(self) -> None:
        import os

        if self._saved_env is None:
            os.environ.pop("YTA_LOW_CONFIDENCE_THRESHOLD", None)
        else:
            os.environ["YTA_LOW_CONFIDENCE_THRESHOLD"] = self._saved_env

    def test_default_threshold_is_half(self) -> None:
        from yt_channel_analyzer.review_ui import (
            DEFAULT_LOW_CONFIDENCE_THRESHOLD,
            _load_low_confidence_threshold,
        )

        self.assertEqual(DEFAULT_LOW_CONFIDENCE_THRESHOLD, 0.5)
        self.assertEqual(_load_low_confidence_threshold(), 0.5)

    def test_env_var_overrides_threshold(self) -> None:
        import os

        from yt_channel_analyzer.review_ui import _load_low_confidence_threshold

        os.environ["YTA_LOW_CONFIDENCE_THRESHOLD"] = "0.7"
        self.assertAlmostEqual(_load_low_confidence_threshold(), 0.7)

    def test_invalid_env_var_falls_back_to_default(self) -> None:
        import os

        from yt_channel_analyzer.review_ui import _load_low_confidence_threshold

        os.environ["YTA_LOW_CONFIDENCE_THRESHOLD"] = "not-a-number"
        self.assertEqual(_load_low_confidence_threshold(), 0.5)

    def test_out_of_range_env_var_falls_back_to_default(self) -> None:
        import os

        from yt_channel_analyzer.review_ui import _load_low_confidence_threshold

        os.environ["YTA_LOW_CONFIDENCE_THRESHOLD"] = "1.5"
        self.assertEqual(_load_low_confidence_threshold(), 0.5)

    def test_low_confidence_class_below_threshold(self) -> None:
        from yt_channel_analyzer.review_ui import _low_confidence_class

        self.assertEqual(_low_confidence_class(0.2, 0.5), "low")

    def test_low_confidence_class_at_or_above_threshold(self) -> None:
        from yt_channel_analyzer.review_ui import _low_confidence_class

        self.assertEqual(_low_confidence_class(0.5, 0.5), "")
        self.assertEqual(_low_confidence_class(0.9, 0.5), "")

    def test_low_confidence_class_handles_none(self) -> None:
        from yt_channel_analyzer.review_ui import _low_confidence_class

        self.assertEqual(_low_confidence_class(None, 0.5), "")

    def test_payload_includes_low_confidence_threshold(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            run_payload = DiscoveryPayload(
                topics=["Health"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="title mentions sleep",
                    ),
                ],
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: run_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topic_map = payload["discovery_topic_map"]
            self.assertIn("low_confidence_threshold", topic_map)
            self.assertEqual(topic_map["low_confidence_threshold"], 0.5)

    def test_payload_threshold_reflects_env_override(self) -> None:
        import os

        from yt_channel_analyzer.review_ui import build_state_payload

        os.environ["YTA_LOW_CONFIDENCE_THRESHOLD"] = "0.42"
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            run_payload = DiscoveryPayload(
                topics=["Health"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="r",
                    ),
                ],
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: run_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topic_map = payload["discovery_topic_map"]
            self.assertAlmostEqual(topic_map["low_confidence_threshold"], 0.42)

    def test_mixed_confidence_fixture_classifies_low_episodes(self) -> None:
        from yt_channel_analyzer.review_ui import (
            _low_confidence_class,
            build_state_payload,
        )
        from yt_channel_analyzer.youtube import VideoMetadata
        from yt_channel_analyzer.db import (
            init_db,
            upsert_videos_for_primary_channel,
        )

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
                        youtube_video_id="vidLow",
                        title="Low conf",
                        description="",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vidMid",
                        title="Mid conf",
                        description="",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vidHigh",
                        title="High conf",
                        description="",
                        published_at="2026-04-03T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            run_payload = DiscoveryPayload(
                topics=["Mixed"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vidLow",
                        topic_name="Mixed",
                        confidence=0.2,
                        reason="weak match",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vidMid",
                        topic_name="Mixed",
                        confidence=0.5,
                        reason="exactly threshold",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vidHigh",
                        topic_name="Mixed",
                        confidence=0.9,
                        reason="strong match",
                    ),
                ],
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: run_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topic_map = payload["discovery_topic_map"]
            threshold = topic_map["low_confidence_threshold"]
            episodes = {
                ep["youtube_video_id"]: ep
                for ep in topic_map["topics"][0]["episodes"]
            }

            self.assertEqual(
                _low_confidence_class(episodes["vidLow"]["confidence"], threshold),
                "low",
            )
            self.assertEqual(
                _low_confidence_class(episodes["vidMid"]["confidence"], threshold),
                "",
            )
            self.assertEqual(
                _low_confidence_class(episodes["vidHigh"]["confidence"], threshold),
                "",
            )

    def test_html_uses_payload_threshold_not_hardcoded_dual_thresholds(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("low_confidence_threshold", html)
        self.assertNotIn("0.33", html)
        self.assertNotIn("0.66", html)
        self.assertNotIn("very-low", html)

    def test_html_defines_low_confidence_episode_style(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn(".discovery-episode.low", html)

    def test_ui_revision_advances_for_low_confidence_threshold(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discovery", UI_REVISION)


if __name__ == "__main__":
    unittest.main()
