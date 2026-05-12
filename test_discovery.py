from __future__ import annotations

import io
import json
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from yt_channel_analyzer.db import (
    connect,
    create_topic,
    ensure_schema,
    init_db,
    mark_assignment_wrong,
    rename_topic,
    upsert_videos_for_primary_channel,
)
from yt_channel_analyzer import cli
from yt_channel_analyzer.discovery import (
    DiscoveryAssignment,
    DiscoveryPayload,
    run_discovery,
    stub_llm,
)
from yt_channel_analyzer.extractor import FakeLLMRunner, registry as _registry_module
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
            self.assertIn("error_message", cols)
            self.assertIn("raw_response", cols)
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


class DescriptionBoilerplateTests(unittest.TestCase):
    def test_returns_none_and_empty_unchanged(self) -> None:
        from yt_channel_analyzer.discovery import strip_description_boilerplate

        self.assertIsNone(strip_description_boilerplate(None))
        self.assertEqual(strip_description_boilerplate(""), "")

    def test_strips_subscribe_cta(self) -> None:
        from yt_channel_analyzer.discovery import strip_description_boilerplate

        description = (
            "We talk about deep work and habits.\n"
            "Don't forget to subscribe to the channel!\n"
            "Hit the bell for more.\n"
        )
        cleaned = strip_description_boilerplate(description)
        self.assertEqual(cleaned, "We talk about deep work and habits.")

    def test_strips_sponsor_read_lines(self) -> None:
        from yt_channel_analyzer.discovery import strip_description_boilerplate

        description = (
            "An episode about sleep science.\n"
            "\n"
            "Sponsors:\n"
            "This episode is sponsored by Acme.\n"
            "Use code DOAC for 20% off your first order.\n"
            "\n"
            "More episodes coming soon.\n"
        )
        cleaned = strip_description_boilerplate(description)
        self.assertEqual(
            cleaned,
            "An episode about sleep science.\n\nMore episodes coming soon.",
        )

    def test_strips_social_handles_and_urls(self) -> None:
        from yt_channel_analyzer.discovery import strip_description_boilerplate

        description = (
            "Today we discuss habit formation.\n"
            "Follow me on Twitter for daily threads.\n"
            "Instagram: @host\n"
            "https://www.instagram.com/host\n"
            "Listen on Apple Podcasts.\n"
            "https://open.spotify.com/show/abc\n"
        )
        cleaned = strip_description_boilerplate(description)
        self.assertEqual(cleaned, "Today we discuss habit formation.")

    def test_preserves_chapter_lines_even_if_they_look_promotional(self) -> None:
        from yt_channel_analyzer.discovery import strip_description_boilerplate

        description = (
            "0:00 Intro\n"
            "5:00 Sponsors and what we cover today\n"
            "12:00 Wrap up\n"
        )
        cleaned = strip_description_boilerplate(description)
        self.assertEqual(
            cleaned,
            "0:00 Intro\n5:00 Sponsors and what we cover today\n12:00 Wrap up",
        )

    def test_collapses_consecutive_blank_lines_after_filtering(self) -> None:
        from yt_channel_analyzer.discovery import strip_description_boilerplate

        description = (
            "Episode notes.\n"
            "\n"
            "Sponsored by Acme.\n"
            "Use code SAVE10.\n"
            "\n"
            "More notes here.\n"
        )
        cleaned = strip_description_boilerplate(description)
        self.assertEqual(cleaned, "Episode notes.\n\nMore notes here.")

    def test_returns_empty_when_entire_description_is_boilerplate(self) -> None:
        from yt_channel_analyzer.discovery import strip_description_boilerplate

        description = (
            "Subscribe to the channel.\n"
            "Follow me on Twitter.\n"
            "https://www.instagram.com/host\n"
        )
        self.assertEqual(strip_description_boilerplate(description), "")

    def test_run_discovery_passes_filtered_description_but_original_chapters(self) -> None:
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
            description = (
                "0:00 Intro\n"
                "1:30 Topic A\n"
                "10:00 Topic B\n"
                "\n"
                "Sponsored by Acme. Use code SAVE10 for 15% off.\n"
                "Follow me on Twitter for more.\n"
                "https://www.instagram.com/host\n"
            )
            upsert_videos_for_primary_channel(
                db_path,
                videos=[
                    VideoMetadata(
                        youtube_video_id="vid1",
                        title="With chapters and sponsors",
                        description=description,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            seen: list = []

            def capturing_llm(videos):
                seen.extend(videos)
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

            video = seen[0]
            self.assertNotIn("Sponsored by", video.description or "")
            self.assertNotIn("Follow me on Twitter", video.description or "")
            self.assertNotIn("instagram.com", video.description or "")
            self.assertEqual(
                video.chapters,
                (
                    Chapter(0, "Intro"),
                    Chapter(90, "Topic A"),
                    Chapter(600, "Topic B"),
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
    def test_stub_llm_assigns_every_video_to_primary_and_one_to_secondary(
        self,
    ) -> None:
        from yt_channel_analyzer.discovery import (
            STUB_SECONDARY_TOPIC_NAME,
            STUB_TOPIC_NAME,
            DiscoveryVideo,
        )

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
        self.assertEqual(
            set(payload.topics), {STUB_TOPIC_NAME, STUB_SECONDARY_TOPIC_NAME}
        )

        primary = [
            a for a in payload.assignments if a.topic_name == STUB_TOPIC_NAME
        ]
        secondary = [
            a
            for a in payload.assignments
            if a.topic_name == STUB_SECONDARY_TOPIC_NAME
        ]

        self.assertEqual(
            {a.youtube_video_id for a in primary}, {"vid1", "vid2"}
        )
        for assignment in primary:
            self.assertEqual(assignment.confidence, 1.0)

        # Exactly one video carries a secondary-topic assignment so the
        # multi-topic display path can be exercised without paying for
        # an LLM. The same video appears in both topic buckets.
        self.assertEqual(len(secondary), 1)
        self.assertIn(secondary[0].youtube_video_id, {"vid1", "vid2"})


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
                # 2 primary-topic rows + 1 secondary-topic row from stub_llm
                self.assertEqual(len(assignments), 3)
                for row in assignments:
                    self.assertEqual(row["assignment_source"], "auto")

    def test_discover_stub_persists_multi_topic_video_under_two_topics(
        self,
    ) -> None:
        from yt_channel_analyzer.discovery import (
            STUB_SECONDARY_TOPIC_NAME,
            STUB_TOPIC_NAME,
        )

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
                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id, t.name AS topic_name
                    FROM video_topics vt
                    JOIN videos v ON v.id = vt.video_id
                    JOIN topics t ON t.id = vt.topic_id
                    """
                ).fetchall()

                topics_by_video: dict[str, set[str]] = {}
                for row in rows:
                    topics_by_video.setdefault(
                        row["youtube_video_id"], set()
                    ).add(row["topic_name"])

                # vid1 is the multi-topic stub video — must persist as two
                # distinct video_topics rows so it appears under both topics
                # in the GUI. vid2 stays single-topic.
                self.assertEqual(
                    topics_by_video["vid1"],
                    {STUB_TOPIC_NAME, STUB_SECONDARY_TOPIC_NAME},
                )
                self.assertEqual(topics_by_video["vid2"], {STUB_TOPIC_NAME})

    def test_discover_requires_mode_flag(self) -> None:
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

    def test_discover_stub_and_real_are_mutually_exclusive(self) -> None:
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
                        "--stub",
                        "--real",
                    ]
                )

    def test_discover_real_without_env_var_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("RALPH_ALLOW_REAL_LLM", None)
                with self.assertRaisesRegex(RuntimeError, "RALPH_ALLOW_REAL_LLM"):
                    cli.main(
                        [
                            "discover",
                            "--db-path",
                            str(db_path),
                            "--project-name",
                            "proj",
                            "--real",
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
                # 2 primary-topic rows + 1 secondary-topic row from stub_llm
                self.assertEqual(len(assignments), 3)
                for row in assignments:
                    self.assertEqual(row["assignment_source"], "auto")

    def test_analyze_requires_mode_flag(self) -> None:
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

    def test_analyze_stub_and_real_are_mutually_exclusive(self) -> None:
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
                        "--stub",
                        "--real",
                    ]
                )

    def test_analyze_real_without_env_var_raises(self) -> None:
        self._patch_youtube()
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("RALPH_ALLOW_REAL_LLM", None)
                with self.assertRaisesRegex(RuntimeError, "RALPH_ALLOW_REAL_LLM"):
                    cli.main(
                        [
                            "analyze",
                            "--db-path",
                            str(db_path),
                            "--project-name",
                            "doac",
                            "--channel-input",
                            "@doac",
                            "--real",
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

    def test_state_payload_discovery_run_id_selects_specific_run(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            first_run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: DiscoveryPayload(
                    topics=["Old Topic"],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="Old Topic",
                            confidence=0.5,
                            reason="early run",
                        ),
                    ],
                ),
                model="stub",
                prompt_version="stub-v0",
            )
            second_run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: DiscoveryPayload(
                    topics=["Fresh Topic"],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="Fresh Topic",
                            confidence=0.95,
                            reason="latest run",
                        ),
                    ],
                ),
                model="stub",
                prompt_version="stub-v0",
            )

            self.assertNotEqual(first_run_id, second_run_id)

            payload_default = build_state_payload(db_path)
            self.assertEqual(
                payload_default["discovery_topic_map"]["run_id"], second_run_id
            )

            payload_first = build_state_payload(
                db_path, discovery_run_id=first_run_id
            )
            topic_map_first = payload_first["discovery_topic_map"]
            self.assertEqual(topic_map_first["run_id"], first_run_id)
            self.assertEqual(
                {t["name"] for t in topic_map_first["topics"]}, {"Old Topic"}
            )

            payload_missing = build_state_payload(
                db_path, discovery_run_id=999_999
            )
            self.assertIsNone(payload_missing["discovery_topic_map"])

    def test_state_payload_episode_dicts_carry_also_in_for_multi_topic(self) -> None:
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
            health_eps = {ep["youtube_video_id"]: ep for ep in topics_by_name["Health"]["episodes"]}
            business_eps = {ep["youtube_video_id"]: ep for ep in topics_by_name["Business"]["episodes"]}
            # vid1 sits in both topics → each card reports the *other* topic.
            self.assertEqual(health_eps["vid1"]["also_in"], ["Business"])
            self.assertEqual(business_eps["vid1"]["also_in"], ["Health"])
            # vid2 only in Business → empty list, never null.
            self.assertEqual(business_eps["vid2"]["also_in"], [])


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

    def test_html_page_renders_also_in_pill_for_multi_topic_episodes(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-episode-also-in", html)
        self.assertIn("also in:", html)

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


class DiscoveryTopicMapSubtopicPayloadTests(unittest.TestCase):
    """Slice 03 / §A3 line 80: state payload exposes per-topic subtopic
    buckets with episode lists, plus an `unassigned_within_topic` bucket
    for episodes assigned to a topic but no subtopic."""

    def test_topic_payload_includes_subtopic_buckets_with_episodes(self) -> None:
        from yt_channel_analyzer.discovery import DiscoverySubtopic
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: DiscoveryPayload(
                    topics=["Health"],
                    subtopics=[
                        DiscoverySubtopic(name="Sleep", parent_topic="Health"),
                        DiscoverySubtopic(name="Diet", parent_topic="Health"),
                    ],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="Health",
                            confidence=0.9,
                            reason="title mentions sleep",
                            subtopic_name="Sleep",
                        ),
                        DiscoveryAssignment(
                            youtube_video_id="vid2",
                            topic_name="Health",
                            confidence=0.8,
                            reason="title mentions diet",
                            subtopic_name="Diet",
                        ),
                    ],
                ),
                model="stub",
                prompt_version="discovery-v2",
            )

            payload = build_state_payload(db_path)
            topics_by_name = {
                t["name"]: t for t in payload["discovery_topic_map"]["topics"]
            }
            health = topics_by_name["Health"]
            self.assertEqual(health["subtopic_count"], 2)
            buckets = {b["name"]: b for b in health["subtopics"]}
            self.assertEqual(set(buckets), {"Sleep", "Diet"})
            self.assertEqual(buckets["Sleep"]["episode_count"], 1)
            self.assertEqual(
                buckets["Sleep"]["episodes"][0]["youtube_video_id"], "vid1"
            )
            self.assertEqual(buckets["Diet"]["episode_count"], 1)
            self.assertEqual(
                buckets["Diet"]["episodes"][0]["youtube_video_id"], "vid2"
            )
            self.assertEqual(health["unassigned_within_topic"], [])

    def test_topic_payload_collects_episodes_without_subtopic_in_unassigned(self) -> None:
        from yt_channel_analyzer.discovery import DiscoverySubtopic
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: DiscoveryPayload(
                    topics=["Health"],
                    subtopics=[
                        DiscoverySubtopic(name="Sleep", parent_topic="Health"),
                    ],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="Health",
                            confidence=0.9,
                            reason="cue",
                            subtopic_name="Sleep",
                        ),
                        DiscoveryAssignment(
                            youtube_video_id="vid2",
                            topic_name="Health",
                            confidence=0.6,
                            reason="weak",
                            subtopic_name=None,
                        ),
                    ],
                ),
                model="stub",
                prompt_version="discovery-v2",
            )

            payload = build_state_payload(db_path)
            topics_by_name = {
                t["name"]: t for t in payload["discovery_topic_map"]["topics"]
            }
            health = topics_by_name["Health"]
            self.assertEqual(health["subtopic_count"], 1)
            self.assertEqual(
                {b["name"] for b in health["subtopics"]}, {"Sleep"}
            )
            sleep = health["subtopics"][0]
            self.assertEqual(
                {e["youtube_video_id"] for e in sleep["episodes"]}, {"vid1"}
            )
            self.assertEqual(
                {e["youtube_video_id"] for e in health["unassigned_within_topic"]},
                {"vid2"},
            )

    def test_topic_with_no_subtopics_has_empty_buckets(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
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
                            reason="cue",
                        ),
                    ],
                ),
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            health = next(
                t for t in payload["discovery_topic_map"]["topics"]
                if t["name"] == "Health"
            )
            self.assertEqual(health["subtopics"], [])
            self.assertEqual(health["subtopic_count"], 0)
            self.assertEqual(
                {e["youtube_video_id"] for e in health["unassigned_within_topic"]},
                {"vid1"},
            )


class DiscoveryTopicEpisodesHTMLTests(unittest.TestCase):
    def test_html_page_has_episode_list_renderer_hook(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-episode-list", html)
        self.assertIn("topic.episodes", html)

    def test_html_page_has_subtopic_bucket_renderer(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function renderDiscoverySubtopicBuckets", html)
        self.assertIn("discovery-subtopic-bucket", html)
        self.assertIn("Unassigned within topic", html)
        self.assertIn("Subtopics", html)

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


class _RegistryIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(_registry_module._PROMPTS)
        _registry_module._PROMPTS.clear()

    def tearDown(self) -> None:
        _registry_module._PROMPTS.clear()
        _registry_module._PROMPTS.update(self._saved)


class DiscoveryPromptRegistrationTests(_RegistryIsolation):
    def test_register_discovery_prompt_is_idempotent(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            register_discovery_prompt,
        )

        first = register_discovery_prompt()
        second = register_discovery_prompt()
        self.assertEqual(first.name, DISCOVERY_PROMPT_NAME)
        self.assertEqual(first.version, DISCOVERY_PROMPT_VERSION)
        self.assertIs(first, second)

    def test_render_includes_titles_descriptions_and_chapters(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt

        prompt = register_discovery_prompt()
        rendered = prompt.render(
            {
                "videos": [
                    {
                        "youtube_video_id": "vidA",
                        "title": "Sleep and the brain",
                        "description": "how sleep works",
                        "chapters": ["0: Intro", "120: Deep sleep"],
                    },
                    {
                        "youtube_video_id": "vidB",
                        "title": "Building a startup",
                        "description": None,
                        "chapters": [],
                    },
                ]
            }
        )
        self.assertIn("vidA", rendered)
        self.assertIn("Sleep and the brain", rendered)
        self.assertIn("how sleep works", rendered)
        self.assertIn("Intro", rendered)
        self.assertIn("Deep sleep", rendered)
        self.assertIn("vidB", rendered)
        self.assertIn("Building a startup", rendered)

    def test_schema_accepts_topics_and_assignments(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        validate(
            {
                "topics": ["Health"],
                "assignments": [
                    {
                        "youtube_video_id": "vidA",
                        "topic": "Health",
                        "confidence": 0.9,
                        "reason": "fixture",
                    },
                ],
            },
            prompt.schema,
        )

    def test_schema_rejects_missing_topics(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        with self.assertRaises(SchemaValidationError):
            validate(
                {"assignments": []},
                prompt.schema,
            )

    def test_schema_rejects_assignment_with_extra_keys(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        # Slice 04 added `confidence` + `reason` as recognized assignment
        # keys, so use a key still outside the schema.
        with self.assertRaises(SchemaValidationError):
            validate(
                {
                    "topics": ["Health"],
                    "assignments": [
                        {
                            "youtube_video_id": "vidA",
                            "topic": "Health",
                            "confidence": 0.9,
                            "reason": "fixture",
                            "priority": "high",
                        },
                    ],
                },
                prompt.schema,
            )

    def test_schema_accepts_subtopics_and_assignment_subtopic(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        validate(
            {
                "topics": ["Health"],
                "subtopics": [
                    {"name": "Sleep", "parent_topic": "Health"},
                ],
                "assignments": [
                    {
                        "youtube_video_id": "vidA",
                        "topic": "Health",
                        "subtopic": "Sleep",
                        "confidence": 0.9,
                        "reason": "fixture",
                    },
                ],
            },
            prompt.schema,
        )

    def test_schema_rejects_subtopic_with_extra_keys(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        with self.assertRaises(SchemaValidationError):
            validate(
                {
                    "topics": ["Health"],
                    "subtopics": [
                        {
                            "name": "Sleep",
                            "parent_topic": "Health",
                            "description": "extra",
                        },
                    ],
                    "assignments": [],
                },
                prompt.schema,
            )

    def test_schema_rejects_assignment_missing_confidence(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        with self.assertRaises(SchemaValidationError):
            validate(
                {
                    "topics": ["Health"],
                    "assignments": [
                        {
                            "youtube_video_id": "vidA",
                            "topic": "Health",
                            "reason": "fixture",
                        },
                    ],
                },
                prompt.schema,
            )

    def test_schema_rejects_assignment_missing_reason(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        with self.assertRaises(SchemaValidationError):
            validate(
                {
                    "topics": ["Health"],
                    "assignments": [
                        {
                            "youtube_video_id": "vidA",
                            "topic": "Health",
                            "confidence": 0.9,
                        },
                    ],
                },
                prompt.schema,
            )

    def test_schema_rejects_confidence_below_zero(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        with self.assertRaises(SchemaValidationError):
            validate(
                {
                    "topics": ["Health"],
                    "assignments": [
                        {
                            "youtube_video_id": "vidA",
                            "topic": "Health",
                            "confidence": -0.1,
                            "reason": "fixture",
                        },
                    ],
                },
                prompt.schema,
            )

    def test_schema_rejects_confidence_above_one(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        with self.assertRaises(SchemaValidationError):
            validate(
                {
                    "topics": ["Health"],
                    "assignments": [
                        {
                            "youtube_video_id": "vidA",
                            "topic": "Health",
                            "confidence": 1.5,
                            "reason": "fixture",
                        },
                    ],
                },
                prompt.schema,
            )

    def test_schema_rejects_empty_reason(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt
        from yt_channel_analyzer.extractor.errors import SchemaValidationError
        from yt_channel_analyzer.extractor.schema import validate

        prompt = register_discovery_prompt()
        with self.assertRaises(SchemaValidationError):
            validate(
                {
                    "topics": ["Health"],
                    "assignments": [
                        {
                            "youtube_video_id": "vidA",
                            "topic": "Health",
                            "confidence": 0.9,
                            "reason": "",
                        },
                    ],
                },
                prompt.schema,
            )


class ExtractorBackedLLMTests(_RegistryIsolation):
    def test_callable_round_trips_payload_via_extractor(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            DiscoveryVideo,
            discovery_llm_via_extractor,
            register_discovery_prompt,
        )
        from yt_channel_analyzer.extractor import Extractor

        register_discovery_prompt()
        runner = FakeLLMRunner()
        runner.add_response(
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            {
                "topics": ["Health", "Business"],
                "assignments": [
                    {
                        "youtube_video_id": "vid1",
                        "topic": "Health",
                        "confidence": 1.0,
                        "reason": "fixture",
                    },
                    {
                        "youtube_video_id": "vid2",
                        "topic": "Business",
                        "confidence": 1.0,
                        "reason": "fixture",
                    },
                ],
            },
        )

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            with connect(db_path) as conn:
                ensure_schema(conn)
                extractor = Extractor(connection=conn, runner=runner)
                callable = discovery_llm_via_extractor(extractor)
                videos = [
                    DiscoveryVideo(
                        youtube_video_id="vid1",
                        title="Sleep",
                        description="how sleep works",
                        published_at=None,
                    ),
                    DiscoveryVideo(
                        youtube_video_id="vid2",
                        title="Founders",
                        description="building",
                        published_at=None,
                    ),
                ]
                payload = callable(videos)

        self.assertEqual(payload.topics, ["Health", "Business"])
        ids = {(a.youtube_video_id, a.topic_name) for a in payload.assignments}
        self.assertEqual(
            ids, {("vid1", "Health"), ("vid2", "Business")}
        )
        # Slice 04: confidence + reason thread through from the LLM payload.
        for a in payload.assignments:
            self.assertEqual(a.confidence, 1.0)
            self.assertEqual(a.reason, "fixture")

    def test_callable_threads_varied_confidence_and_reason(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            DiscoveryVideo,
            discovery_llm_via_extractor,
            register_discovery_prompt,
        )
        from yt_channel_analyzer.extractor import Extractor

        register_discovery_prompt()
        runner = FakeLLMRunner()
        runner.add_response(
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            {
                "topics": ["Health", "Business"],
                "assignments": [
                    {
                        "youtube_video_id": "vid1",
                        "topic": "Health",
                        "confidence": 0.42,
                        "reason": "title contains 'sleep'",
                    },
                    {
                        "youtube_video_id": "vid2",
                        "topic": "Business",
                        "confidence": 0.87,
                        "reason": "matched chapter 'Founder stories'",
                    },
                ],
            },
        )

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            with connect(db_path) as conn:
                ensure_schema(conn)
                extractor = Extractor(connection=conn, runner=runner)
                callable = discovery_llm_via_extractor(extractor)
                videos = [
                    DiscoveryVideo(
                        youtube_video_id="vid1",
                        title="Sleep",
                        description="how sleep works",
                        published_at=None,
                    ),
                    DiscoveryVideo(
                        youtube_video_id="vid2",
                        title="Founders",
                        description="building",
                        published_at=None,
                    ),
                ]
                payload = callable(videos)

        by_id = {a.youtube_video_id: a for a in payload.assignments}
        self.assertAlmostEqual(by_id["vid1"].confidence, 0.42)
        self.assertEqual(by_id["vid1"].reason, "title contains 'sleep'")
        self.assertAlmostEqual(by_id["vid2"].confidence, 0.87)
        self.assertEqual(by_id["vid2"].reason, "matched chapter 'Founder stories'")

    def test_render_serializes_videos_into_one_prompt(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            DiscoveryVideo,
            discovery_llm_via_extractor,
            register_discovery_prompt,
        )
        from yt_channel_analyzer.extractor import Extractor

        register_discovery_prompt()
        runner = FakeLLMRunner()
        runner.add_response(
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            {
                "topics": ["T"],
                "assignments": [
                    {
                        "youtube_video_id": "vidX",
                        "topic": "T",
                        "confidence": 1.0,
                        "reason": "fixture",
                    }
                ],
            },
        )

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            with connect(db_path) as conn:
                ensure_schema(conn)
                extractor = Extractor(connection=conn, runner=runner)
                callable = discovery_llm_via_extractor(extractor)
                callable(
                    [
                        DiscoveryVideo(
                            youtube_video_id="vidX",
                            title="Hello",
                            description="world",
                            published_at=None,
                        )
                    ]
                )

        # Single batched call: one extractor invocation regardless of video count.
        self.assertEqual(len(runner.calls), 1)
        rendered = runner.calls[0].rendered_prompt
        self.assertIn("vidX", rendered)
        self.assertIn("Hello", rendered)
        self.assertIn("world", rendered)


class RealLLMGuardTests(_RegistryIsolation):
    def test_make_real_llm_callable_requires_env_var(self) -> None:
        import os

        from yt_channel_analyzer.discovery import make_real_llm_callable

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            with connect(db_path) as conn:
                ensure_schema(conn)
                prior = os.environ.pop("RALPH_ALLOW_REAL_LLM", None)
                try:
                    with self.assertRaises(RuntimeError) as ctx:
                        make_real_llm_callable(conn)
                    self.assertIn(
                        "RALPH_ALLOW_REAL_LLM", str(ctx.exception)
                    )
                finally:
                    if prior is not None:
                        os.environ["RALPH_ALLOW_REAL_LLM"] = prior

    def test_make_real_llm_callable_rejects_zero_value(self) -> None:
        import os

        from yt_channel_analyzer.discovery import make_real_llm_callable

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            with connect(db_path) as conn:
                ensure_schema(conn)
                prior = os.environ.get("RALPH_ALLOW_REAL_LLM")
                os.environ["RALPH_ALLOW_REAL_LLM"] = "0"
                try:
                    with self.assertRaises(RuntimeError):
                        make_real_llm_callable(conn)
                finally:
                    if prior is None:
                        os.environ.pop("RALPH_ALLOW_REAL_LLM", None)
                    else:
                        os.environ["RALPH_ALLOW_REAL_LLM"] = prior


class RunDiscoveryErrorPathTests(unittest.TestCase):
    """When the LLM call raises (e.g. parse failure after Extractor's retry),
    `run_discovery` records an errored `discovery_runs` row and persists no
    partial topic / assignment state. The exception is re-raised so callers
    can surface it.
    """

    def test_llm_error_marks_run_errored_and_persists_no_partial_state(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_VERSION,
            run_discovery,
        )
        from yt_channel_analyzer.extractor.errors import SchemaValidationError

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            def failing_llm(_videos):
                raise SchemaValidationError("malformed after retry")

            with self.assertRaises(SchemaValidationError):
                run_discovery(
                    db_path,
                    project_name="proj",
                    llm=failing_llm,
                    model="haiku-4-5",
                    prompt_version=DISCOVERY_PROMPT_VERSION,
                )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                runs = conn.execute(
                    "SELECT id, model, prompt_version, status FROM discovery_runs"
                ).fetchall()
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["status"], "error")
                self.assertEqual(runs[0]["model"], "haiku-4-5")
                self.assertEqual(runs[0]["prompt_version"], DISCOVERY_PROMPT_VERSION)

                err_row = conn.execute(
                    "SELECT error_message, raw_response FROM discovery_runs"
                ).fetchone()
                self.assertEqual(err_row["error_message"], "malformed after retry")
                self.assertIsNone(err_row["raw_response"])

                topics = conn.execute("SELECT id FROM topics").fetchall()
                self.assertEqual(topics, [])

                assignments = conn.execute(
                    "SELECT video_id FROM video_topics WHERE discovery_run_id = ?",
                    (runs[0]["id"],),
                ).fetchall()
                self.assertEqual(assignments, [])

    def test_llm_error_does_not_corrupt_prior_successful_run(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_VERSION,
            run_discovery,
            stub_llm,
        )
        from yt_channel_analyzer.extractor.errors import SchemaValidationError

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            ok_run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )

            def failing_llm(_videos):
                raise SchemaValidationError("malformed after retry")

            with self.assertRaises(SchemaValidationError):
                run_discovery(
                    db_path,
                    project_name="proj",
                    llm=failing_llm,
                    model="haiku-4-5",
                    prompt_version=DISCOVERY_PROMPT_VERSION,
                )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                runs = conn.execute(
                    "SELECT id, status FROM discovery_runs ORDER BY id"
                ).fetchall()
                self.assertEqual(len(runs), 2)
                self.assertEqual(runs[0]["id"], ok_run_id)
                self.assertEqual(runs[0]["status"], "success")
                self.assertEqual(runs[1]["status"], "error")

                # The successful run's assignments are still intact.
                # 2 primary-topic rows + 1 secondary-topic row from stub_llm
                first_run_assignments = conn.execute(
                    "SELECT video_id FROM video_topics WHERE discovery_run_id = ?",
                    (ok_run_id,),
                ).fetchall()
                self.assertEqual(len(first_run_assignments), 3)

    def test_validation_failure_persists_errored_run_with_raw_response(self) -> None:
        """When the LLM returns a payload that fails downstream validation
        (here: an assignment references a ``youtube_video_id`` that wasn't
        in the discover input — i.e. data the model hallucinated),
        `run_discovery` must persist an errored `discovery_runs` row
        carrying the raw payload + error message instead of silently
        rolling back the whole tx and losing the billed response.

        Note: the dangling-subtopic case that originally surfaced this
        error path on the 2026-05-08 run-1 retry is now auto-healed by
        ``_autoheal_dangling_subtopic_refs`` (slice 14), so this test
        uses an unknown-video trigger instead — that variant still
        raises and exercises the same paid-failure-recovery code path.
        """
        import json
        from dataclasses import asdict

        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_VERSION,
            DiscoveryAssignment,
            DiscoveryPayload,
            run_discovery,
        )

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            # Bad payload: assignment references a youtube_video_id
            # that wasn't in the discover input. Hits the "unknown
            # video in discovery payload" branch — distinct from the
            # auto-healed dangling-subtopic case.
            bad_payload = DiscoveryPayload(
                topics=["Brain"],
                subtopics=[],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid-hallucinated",
                        topic_name="Brain",
                        confidence=0.9,
                        reason="hallucinated video id",
                    ),
                ],
            )

            def bad_llm(_videos):
                return bad_payload

            with self.assertRaises(ValueError):
                run_discovery(
                    db_path,
                    project_name="proj",
                    llm=bad_llm,
                    model="haiku-4-5",
                    prompt_version=DISCOVERY_PROMPT_VERSION,
                )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                runs = conn.execute(
                    "SELECT id, status, error_message, raw_response "
                    "FROM discovery_runs"
                ).fetchall()
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["status"], "error")
                self.assertIn("vid-hallucinated", runs[0]["error_message"])

                stored = json.loads(runs[0]["raw_response"])
                self.assertEqual(stored, asdict(bad_payload))

                # No partial state survives the rollback.
                self.assertEqual(
                    conn.execute("SELECT id FROM topics").fetchall(), []
                )
                self.assertEqual(
                    conn.execute("SELECT id FROM subtopics").fetchall(), []
                )
                self.assertEqual(
                    conn.execute("SELECT video_id FROM video_topics").fetchall(),
                    [],
                )


class RunDiscoverySubtopicPersistenceTests(unittest.TestCase):
    """Slice 03: `run_discovery` persists `subtopics` + `video_subtopics`
    rows when the LLM payload includes subtopics. Missing-subtopic
    assignments leave the junction empty (graceful)."""

    def test_persists_subtopics_under_parent_topic(self) -> None:
        from yt_channel_analyzer.discovery import DiscoverySubtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = DiscoveryPayload(
                topics=["Health"],
                subtopics=[
                    DiscoverySubtopic(name="Sleep", parent_topic="Health"),
                    DiscoverySubtopic(name="Diet", parent_topic="Health"),
                ],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                        subtopic_name="Sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                        subtopic_name="Diet",
                    ),
                ],
            )

            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: payload,
                model="stub",
                prompt_version="discovery-v2",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT s.name, t.name AS topic_name
                    FROM subtopics s
                    JOIN topics t ON t.id = s.topic_id
                    ORDER BY s.name
                    """
                ).fetchall()
            pairs = {(r["topic_name"], r["name"]) for r in rows}
            self.assertEqual(pairs, {("Health", "Sleep"), ("Health", "Diet")})

    def test_persists_video_subtopics_with_auto_source_and_run_id(self) -> None:
        from yt_channel_analyzer.discovery import DiscoverySubtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = DiscoveryPayload(
                topics=["Health"],
                subtopics=[
                    DiscoverySubtopic(name="Sleep", parent_topic="Health"),
                ],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.7,
                        reason="title cue",
                        subtopic_name="Sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=0.5,
                        reason="weak",
                        subtopic_name="Sleep",
                    ),
                ],
            )

            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: payload,
                model="stub",
                prompt_version="discovery-v2",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id, s.name AS subtopic_name,
                           t.name AS topic_name,
                           vs.assignment_source, vs.confidence, vs.reason,
                           vs.discovery_run_id
                    FROM video_subtopics vs
                    JOIN videos v ON v.id = vs.video_id
                    JOIN subtopics s ON s.id = vs.subtopic_id
                    JOIN topics t ON t.id = s.topic_id
                    ORDER BY v.youtube_video_id
                    """
                ).fetchall()
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["assignment_source"], "auto")
                self.assertEqual(row["topic_name"], "Health")
                self.assertEqual(row["subtopic_name"], "Sleep")
                self.assertEqual(row["discovery_run_id"], run_id)
            by_id = {r["youtube_video_id"]: r for r in rows}
            self.assertAlmostEqual(by_id["vid1"]["confidence"], 0.7)
            self.assertEqual(by_id["vid1"]["reason"], "title cue")

    def test_assignment_without_subtopic_skips_junction_row(self) -> None:
        from yt_channel_analyzer.discovery import DiscoverySubtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = DiscoveryPayload(
                topics=["Health"],
                subtopics=[
                    DiscoverySubtopic(name="Sleep", parent_topic="Health"),
                ],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                        subtopic_name="Sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                        subtopic_name=None,
                    ),
                ],
            )

            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: payload,
                model="stub",
                prompt_version="discovery-v2",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id
                    FROM video_subtopics vs
                    JOIN videos v ON v.id = vs.video_id
                    """
                ).fetchall()
                topic_rows = conn.execute(
                    """
                    SELECT v.youtube_video_id
                    FROM video_topics vt
                    JOIN videos v ON v.id = vt.video_id
                    """
                ).fetchall()
            self.assertEqual({r["youtube_video_id"] for r in rows}, {"vid1"})
            # vid2 still appears in video_topics — only the subtopic junction
            # is skipped when no subtopic is named.
            self.assertEqual(
                {r["youtube_video_id"] for r in topic_rows}, {"vid1", "vid2"}
            )

    def test_subtopic_with_unknown_parent_topic_raises(self) -> None:
        from yt_channel_analyzer.discovery import DiscoverySubtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = DiscoveryPayload(
                topics=["Health"],
                subtopics=[
                    DiscoverySubtopic(
                        name="Sleep", parent_topic="Wellness"
                    ),
                ],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                    ),
                ],
            )

            with self.assertRaises(ValueError) as ctx:
                run_discovery(
                    db_path,
                    project_name="proj",
                    llm=lambda videos: payload,
                    model="stub",
                    prompt_version="discovery-v2",
                )
            self.assertIn("Wellness", str(ctx.exception))

    def test_assignment_subtopic_not_in_payload_is_autohealed(self) -> None:
        """Slice 14 changed this contract: a dangling subtopic ref under a
        declared topic is auto-healed (synthesized into payload.subtopics)
        rather than raising. This variant — with payload.subtopics empty
        on entry — exercises the path where the healed list is built from
        scratch, complementing RunDiscoverySubtopicAutohealTests which
        seeds one pre-declared subtopic.
        """
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = DiscoveryPayload(
                topics=["Health"],
                subtopics=[],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                        subtopic_name="Sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=1.0,
                        reason="",
                    ),
                ],
            )

            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: payload,
                model="stub",
                prompt_version="discovery-v2",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                run_status = conn.execute(
                    "SELECT status FROM discovery_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()["status"]
                self.assertEqual(run_status, "success")

                pairs = {
                    (r["topic_name"], r["sub_name"])
                    for r in conn.execute(
                        """
                        SELECT t.name AS topic_name, s.name AS sub_name
                        FROM subtopics s
                        JOIN topics t ON t.id = s.topic_id
                        """
                    ).fetchall()
                }
                self.assertEqual(pairs, {("Health", "Sleep")})

    def test_stub_llm_emits_one_subtopic_per_topic(self) -> None:
        from yt_channel_analyzer.discovery import (
            STUB_SUBTOPIC_NAME,
            STUB_TOPIC_NAME,
            DiscoveryVideo,
        )

        videos = [
            DiscoveryVideo(
                youtube_video_id="vidA",
                title="t",
                description=None,
                published_at=None,
            ),
            DiscoveryVideo(
                youtube_video_id="vidB",
                title="t",
                description=None,
                published_at=None,
            ),
        ]
        payload = stub_llm(videos)
        self.assertEqual(
            [(s.name, s.parent_topic) for s in payload.subtopics],
            [(STUB_SUBTOPIC_NAME, STUB_TOPIC_NAME)],
        )
        # Only primary-topic assignments carry the stub subtopic; the
        # secondary-topic multi-topic assignment has no subtopic.
        for assignment in payload.assignments:
            if assignment.topic_name == STUB_TOPIC_NAME:
                self.assertEqual(assignment.subtopic_name, STUB_SUBTOPIC_NAME)
            else:
                self.assertIsNone(assignment.subtopic_name)

    def test_payload_from_extractor_response_carries_subtopics(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            DiscoveryVideo,
            discovery_llm_via_extractor,
            register_discovery_prompt,
        )
        from yt_channel_analyzer.extractor import Extractor

        saved = dict(_registry_module._PROMPTS)
        _registry_module._PROMPTS.clear()
        try:
            register_discovery_prompt()
            runner = FakeLLMRunner()
            runner.add_response(
                DISCOVERY_PROMPT_NAME,
                DISCOVERY_PROMPT_VERSION,
                {
                    "topics": ["Health"],
                    "subtopics": [
                        {"name": "Sleep", "parent_topic": "Health"},
                    ],
                    "assignments": [
                        {
                            "youtube_video_id": "vid1",
                            "topic": "Health",
                            "subtopic": "Sleep",
                            "confidence": 1.0,
                            "reason": "fixture",
                        },
                    ],
                },
            )
            with TemporaryDirectory() as tmpdir:
                db_path = Path(tmpdir) / "test.sqlite3"
                _seed_channel_with_videos(db_path)
                with connect(db_path) as conn:
                    ensure_schema(conn)
                    extractor = Extractor(connection=conn, runner=runner)
                    callable_ = discovery_llm_via_extractor(extractor)
                    payload = callable_(
                        [
                            DiscoveryVideo(
                                youtube_video_id="vid1",
                                title="t",
                                description="d",
                                published_at=None,
                            )
                        ]
                    )
            self.assertEqual(
                [(s.name, s.parent_topic) for s in payload.subtopics],
                [("Sleep", "Health")],
            )
            self.assertEqual(payload.assignments[0].subtopic_name, "Sleep")
        finally:
            _registry_module._PROMPTS.clear()
            _registry_module._PROMPTS.update(saved)


class RunDiscoverySubtopicAutohealTests(unittest.TestCase):
    """Slice 14: when an assignment references a subtopic that the LLM
    didn't declare in `payload.subtopics`, `run_discovery` auto-heals
    the payload by appending a `DiscoverySubtopic(name=..., parent_topic=
    assignment.topic_name)` before persistence — instead of raising
    `ValueError` and forcing the user to re-pay for a fresh LLM call.

    Surfaced on the 2026-05-10 live real-LLM smoke (run 10 on
    `tmp/doac-sticky.sqlite`): Haiku 4.5, `stop_reason=end_turn`,
    `parse_status=ok`, but the model assigned a subtopic name it never
    declared. ~$0.05 lost per occurrence under the old strict-validator
    behavior.

    Auto-heal precondition: the assignment's `topic_name` *is* declared
    in `payload.topics`. Dangling topic refs still raise — those need
    fresh data, not synthesis.
    """

    def test_undeclared_subtopic_under_known_topic_is_autohealed(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_VERSION,
            DiscoverySubtopic,
        )

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            # Mirrors the real Haiku failure: payload declares topic
            # "Brain" + one subtopic "Sleep", but the assignment for
            # vid1 references subtopic "Discipline" — never declared.
            payload = DiscoveryPayload(
                topics=["Brain"],
                subtopics=[DiscoverySubtopic(name="Sleep", parent_topic="Brain")],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Brain",
                        confidence=0.9,
                        reason="dangling subtopic ref",
                        subtopic_name="Discipline",
                    ),
                ],
            )

            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda _videos: payload,
                model="stub",
                prompt_version=DISCOVERY_PROMPT_VERSION,
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row

                run_row = conn.execute(
                    "SELECT status FROM discovery_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                self.assertEqual(run_row["status"], "success")

                # Both the originally-declared subtopic and the
                # auto-healed one land under topic "Brain".
                pairs = {
                    (r["topic_name"], r["sub_name"])
                    for r in conn.execute(
                        """
                        SELECT t.name AS topic_name, s.name AS sub_name
                        FROM subtopics s
                        JOIN topics t ON t.id = s.topic_id
                        """
                    ).fetchall()
                }
                self.assertEqual(
                    pairs, {("Brain", "Sleep"), ("Brain", "Discipline")}
                )

                # The dangling assignment lands on the auto-healed
                # subtopic (vid1 → Discipline, NOT vid1 → Sleep).
                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id, s.name AS sub_name
                    FROM video_subtopics vs
                    JOIN videos v ON v.id = vs.video_id
                    JOIN subtopics s ON s.id = vs.subtopic_id
                    WHERE vs.discovery_run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
                self.assertEqual(
                    [(r["youtube_video_id"], r["sub_name"]) for r in rows],
                    [("vid1", "Discipline")],
                )


class RunDiscoveryConfidencePersistenceTests(unittest.TestCase):
    """Slice 04: model-emitted confidence + reason flow through
    `_payload_from_response` and land in `video_topics` (and
    `video_subtopics` when a subtopic is named) as the persisted values
    — no longer the prior 1.0 / "" placeholders."""

    def test_varied_confidence_and_reason_persist_to_video_topics(self) -> None:
        from yt_channel_analyzer.discovery import DiscoverySubtopic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = DiscoveryPayload(
                topics=["Health", "Business"],
                subtopics=[
                    DiscoverySubtopic(name="Sleep", parent_topic="Health"),
                ],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.42,
                        reason="title contains 'sleep'",
                        subtopic_name="Sleep",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Business",
                        confidence=0.87,
                        reason="matched chapter 'Founder stories'",
                    ),
                ],
            )

            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: payload,
                model="stub",
                prompt_version="discovery-v3",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id AS yt_id,
                           t.name AS topic_name,
                           vt.confidence AS confidence,
                           vt.reason AS reason
                    FROM video_topics vt
                    JOIN videos v ON v.id = vt.video_id
                    JOIN topics t ON t.id = vt.topic_id
                    ORDER BY v.youtube_video_id
                    """
                ).fetchall()

                sub_rows = conn.execute(
                    """
                    SELECT v.youtube_video_id AS yt_id,
                           s.name AS subtopic_name,
                           vs.confidence AS confidence,
                           vs.reason AS reason
                    FROM video_subtopics vs
                    JOIN videos v ON v.id = vs.video_id
                    JOIN subtopics s ON s.id = vs.subtopic_id
                    ORDER BY v.youtube_video_id
                    """
                ).fetchall()

        by_yt = {r["yt_id"]: r for r in rows}
        self.assertAlmostEqual(by_yt["vid1"]["confidence"], 0.42)
        self.assertEqual(by_yt["vid1"]["reason"], "title contains 'sleep'")
        self.assertAlmostEqual(by_yt["vid2"]["confidence"], 0.87)
        self.assertEqual(
            by_yt["vid2"]["reason"], "matched chapter 'Founder stories'"
        )

        # And the subtopic row inherits the assignment's confidence + reason.
        self.assertEqual(len(sub_rows), 1)
        self.assertEqual(sub_rows[0]["yt_id"], "vid1")
        self.assertAlmostEqual(sub_rows[0]["confidence"], 0.42)
        self.assertEqual(sub_rows[0]["reason"], "title contains 'sleep'")


class StickyCurationRenameReplayTests(unittest.TestCase):
    def test_rename_then_rerun_keeps_curated_name_with_episodes(self) -> None:
        from yt_channel_analyzer.db import rename_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )
            rename_topic(
                db_path,
                project_name="proj",
                current_name="General",
                new_name="WellbeingRenamed",
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                topic_rows = conn.execute(
                    "SELECT id, name FROM topics WHERE name IN ('General', 'WellbeingRenamed')"
                ).fetchall()
                names = {row["name"] for row in topic_rows}
                self.assertEqual(names, {"WellbeingRenamed"})
                self.assertEqual(len(topic_rows), 1)

                wellbeing_id = topic_rows[0]["id"]
                episode_rows = conn.execute(
                    """
                    SELECT v.youtube_video_id
                    FROM video_topics vt
                    JOIN videos v ON v.id = vt.video_id
                    WHERE vt.topic_id = ?
                    """,
                    (wellbeing_id,),
                ).fetchall()
                yt_ids = {row["youtube_video_id"] for row in episode_rows}
                self.assertEqual(yt_ids, {"vid1", "vid2"})

    def test_mark_wrong_then_rerun_suppresses_assignment(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )
            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Cross-cutting",
                youtube_video_id="vid1",
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                latest_run = conn.execute(
                    "SELECT id FROM discovery_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                rows = conn.execute(
                    """
                    SELECT v.youtube_video_id AS yt_id, t.name AS topic_name
                    FROM video_topics vt
                    JOIN videos v ON v.id = vt.video_id
                    JOIN topics t ON t.id = vt.topic_id
                    WHERE vt.discovery_run_id = ?
                    """,
                    (latest_run["id"],),
                ).fetchall()
            pairs = {(row["yt_id"], row["topic_name"]) for row in rows}
            self.assertNotIn(("vid1", "Cross-cutting"), pairs)
            # Suppression is targeted: the primary "General" assignment for
            # vid1 (and vid2) is untouched in the new run.
            self.assertIn(("vid1", "General"), pairs)
            self.assertIn(("vid2", "General"), pairs)

    def test_apply_renames_to_payload_collapses_multi_hop_chain(self) -> None:
        from yt_channel_analyzer.db import create_topic
        from yt_channel_analyzer.discovery import _apply_renames_to_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            anchor_id = create_topic(
                db_path, project_name="proj", topic_name="anchor"
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                project_id = conn.execute(
                    "SELECT id FROM projects WHERE name = 'proj'"
                ).fetchone()["id"]
                conn.executemany(
                    """
                    INSERT INTO topic_renames(project_id, topic_id, old_name, new_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (project_id, anchor_id, "A", "B"),
                        (project_id, anchor_id, "B", "C"),
                    ],
                )
                conn.commit()

                payload = DiscoveryPayload(
                    topics=["A"],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1",
                            topic_name="A",
                            confidence=0.9,
                            reason="cue",
                        ),
                    ],
                )
                rewritten = _apply_renames_to_payload(conn, project_id, payload)

            self.assertEqual(rewritten.topics, ["C"])
            self.assertEqual(rewritten.assignments[0].topic_name, "C")

    def test_apply_renames_to_payload_dedupes_after_rewrite(self) -> None:
        from yt_channel_analyzer.db import create_topic
        from yt_channel_analyzer.discovery import _apply_renames_to_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            anchor_id = create_topic(
                db_path, project_name="proj", topic_name="anchor"
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                project_id = conn.execute(
                    "SELECT id FROM projects WHERE name = 'proj'"
                ).fetchone()["id"]
                conn.execute(
                    """
                    INSERT INTO topic_renames(project_id, topic_id, old_name, new_name)
                    VALUES (?, ?, 'A', 'B')
                    """,
                    (project_id, anchor_id),
                )
                conn.commit()

                payload = DiscoveryPayload(
                    topics=["A", "B"],
                    assignments=[],
                )
                rewritten = _apply_renames_to_payload(conn, project_id, payload)

            self.assertEqual(rewritten.topics, ["B"])

    def test_topics_introduced_in_run_returns_only_new_names(self) -> None:
        from yt_channel_analyzer.review_ui import _topics_introduced_in_run

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            first_payload = DiscoveryPayload(
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
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: first_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            second_payload = DiscoveryPayload(
                topics=["Health", "Business", "Tech"],
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
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Tech",
                        confidence=0.7,
                        reason="r",
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

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                channel_id = conn.execute(
                    "SELECT channel_id FROM discovery_runs WHERE id = ?",
                    (second_run_id,),
                ).fetchone()["channel_id"]
                names = _topics_introduced_in_run(conn, channel_id, second_run_id)
            self.assertEqual(names, ["Tech"])

    def test_topics_introduced_in_run_empty_on_first_run(self) -> None:
        from yt_channel_analyzer.review_ui import _topics_introduced_in_run

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                channel_id = conn.execute(
                    "SELECT channel_id FROM discovery_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()["channel_id"]
                names = _topics_introduced_in_run(conn, channel_id, run_id)
            self.assertEqual(names, [])

    def test_state_payload_carries_new_topic_names(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            first_payload = DiscoveryPayload(
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
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: first_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            second_payload = DiscoveryPayload(
                topics=["Health", "Business", "Tech"],
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
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Tech",
                        confidence=0.7,
                        reason="r",
                    ),
                ],
            )
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: second_payload,
                model="stub",
                prompt_version="stub-v0",
            )

            payload = build_state_payload(db_path)
            topic_map = payload["discovery_topic_map"]
            self.assertEqual(topic_map["new_topic_names"], ["Tech"])

    def test_html_page_renders_new_topic_badge(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discovery-topic-new-badge", html)

    def test_rename_endpoint_records_topic_renames_row(self) -> None:
        from yt_channel_analyzer.db import rename_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )
            rename_topic(
                db_path,
                project_name="proj",
                current_name="General",
                new_name="GeneralRenamed",
            )
            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT old_name, new_name FROM topic_renames"
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["old_name"], "General")
            self.assertEqual(rows[0]["new_name"], "GeneralRenamed")

    def test_curation_survives_full_rerun_round_trip(self) -> None:
        from yt_channel_analyzer.db import mark_assignment_wrong, rename_topic
        from yt_channel_analyzer.review_ui import _topics_introduced_in_run

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            first_payload = DiscoveryPayload(
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
                        topic_name="Health",
                        confidence=0.8,
                        reason="r",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Business",
                        confidence=0.7,
                        reason="r",
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

            rename_topic(
                db_path,
                project_name="proj",
                current_name="Health",
                new_name="Wellbeing",
            )
            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Business",
                youtube_video_id="vid2",
            )

            # Second-run stub still emits the pre-curation names ("Health"
            # for what is now "Wellbeing", "Business" for the suppressed
            # assignment) and introduces a brand-new topic "Tech".
            second_payload = DiscoveryPayload(
                topics=["Health", "Business", "Tech"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Health",
                        confidence=0.9,
                        reason="r",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Health",
                        confidence=0.8,
                        reason="r",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2",
                        topic_name="Business",
                        confidence=0.7,
                        reason="r",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid1",
                        topic_name="Tech",
                        confidence=0.6,
                        reason="r",
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

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row

                # (a) The renamed topic survives: one row, name preserved,
                #     all original episode assignments still point at it.
                topic_rows = conn.execute(
                    "SELECT id, name FROM topics WHERE name IN ('Health', 'Wellbeing')"
                ).fetchall()
                self.assertEqual(
                    {row["name"] for row in topic_rows}, {"Wellbeing"}
                )
                self.assertEqual(len(topic_rows), 1)
                wellbeing_id = topic_rows[0]["id"]
                wellbeing_yt_ids = {
                    row["yt_id"]
                    for row in conn.execute(
                        """
                        SELECT v.youtube_video_id AS yt_id
                        FROM video_topics vt
                        JOIN videos v ON v.id = vt.video_id
                        WHERE vt.topic_id = ?
                        """,
                        (wellbeing_id,),
                    ).fetchall()
                }
                self.assertEqual(wellbeing_yt_ids, {"vid1", "vid2"})

                # (b) The marked-wrong (vid2, Business) assignment does not
                #     reappear in the second run's video_topics rows.
                second_pairs = {
                    (row["yt_id"], row["topic_name"])
                    for row in conn.execute(
                        """
                        SELECT v.youtube_video_id AS yt_id, t.name AS topic_name
                        FROM video_topics vt
                        JOIN videos v ON v.id = vt.video_id
                        JOIN topics t ON t.id = vt.topic_id
                        WHERE vt.discovery_run_id = ?
                        """,
                        (second_run_id,),
                    ).fetchall()
                }
                self.assertNotIn(("vid2", "Business"), second_pairs)

                # (c) `_topics_introduced_in_run` reports the brand-new "Tech"
                #     topic and nothing else.
                channel_id = conn.execute(
                    "SELECT channel_id FROM discovery_runs WHERE id = ?",
                    (second_run_id,),
                ).fetchone()["channel_id"]
                names = _topics_introduced_in_run(
                    conn, channel_id, second_run_id
                )
            self.assertEqual(names, ["Tech"])


class ChannelOverviewPayloadTests(unittest.TestCase):
    def test_state_payload_has_channel_overview_with_seeded_counts(self) -> None:
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
            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: run_payload,
                model="haiku-stub",
                prompt_version="discovery-v0",
            )

            payload = build_state_payload(db_path)
            overview = payload["channel_overview"]
            self.assertEqual(overview["channel_title"], "Channel")
            self.assertEqual(overview["channel_id"], "UC123")
            self.assertEqual(overview["video_count"], 2)
            self.assertEqual(overview["transcript_count"], 0)
            self.assertEqual(overview["topic_count"], 2)
            self.assertEqual(overview["subtopic_count"], 0)
            self.assertEqual(overview["comparison_group_count"], 0)
            latest = overview["latest_discovery"]
            self.assertIsNotNone(latest)
            self.assertEqual(latest["id"], run_id)
            self.assertEqual(latest["status"], "success")
            self.assertEqual(latest["model"], "haiku-stub")
            self.assertEqual(latest["prompt_version"], "discovery-v0")
            self.assertIn("started_at", latest)
            self.assertIsNotNone(latest["started_at"])

    def test_state_payload_channel_overview_latest_discovery_null_when_no_run(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            payload = build_state_payload(db_path)
            overview = payload["channel_overview"]
            self.assertIsNone(overview["latest_discovery"])
            self.assertEqual(overview["video_count"], 2)
            self.assertEqual(overview["topic_count"], 0)
            self.assertEqual(overview["subtopic_count"], 0)
            self.assertEqual(overview["transcript_count"], 0)
            self.assertEqual(overview["comparison_group_count"], 0)
            self.assertEqual(overview["channel_title"], "Channel")
            self.assertEqual(overview["channel_id"], "UC123")

    def test_state_payload_channel_overview_null_when_no_primary_channel(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.sqlite3"
            payload = build_state_payload(db_path)
            self.assertIsNone(payload["channel_overview"])
            self.assertIsNone(payload["channel_title"])
            self.assertIsNone(payload["channel_id"])


class ChannelOverviewHTMLTests(unittest.TestCase):
    def test_html_page_contains_channel_overview_panel_markup(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn('id="channel-overview-title"', html)
        self.assertIn('id="channel-overview-subtitle"', html)
        self.assertIn('id="channel-overview-stats"', html)
        self.assertIn('id="channel-overview-latest"', html)
        self.assertIn('class="panel channel-overview"', html)

    def test_html_page_panel_appears_above_discovery_topic_map(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        overview_idx = html.find('id="channel-overview-stats"')
        discovery_idx = html.find('id="discovery-topic-map-grid"')
        self.assertGreaterEqual(overview_idx, 0)
        self.assertGreaterEqual(discovery_idx, 0)
        self.assertLess(overview_idx, discovery_idx)

    def test_html_page_wires_render_channel_overview(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("function renderChannelOverview", html)
        self.assertIn(
            "renderChannelOverview(payload.channel_overview)", html
        )

    def test_html_page_renders_stat_tile_labels(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        for label in ("Videos", "Transcripts", "Topics", "Subtopics", "Comparison groups"):
            self.assertIn(f"'{label}'", html)

    def test_html_page_contains_latest_discovery_empty_state_copy(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("No discovery yet", html)
        self.assertIn("<code>analyze</code>", html)
        self.assertIn("<code>discover</code>", html)

    def test_ui_revision_advances_for_channel_overview_panel(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("channel-overview", UI_REVISION)

    def test_html_page_renders_no_primary_channel_hint(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("No primary channel set", html)


class RunHistoryAdvancedHTMLTests(unittest.TestCase):
    def test_html_page_wraps_run_select_in_run_history_details(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        details_start = html.find('<details class="run-history-advanced">')
        self.assertGreaterEqual(details_start, 0)
        details_end = html.find("</details>", details_start)
        self.assertGreater(details_end, details_start)
        details_block = html[details_start:details_end]
        self.assertIn('id="run-select"', details_block)
        self.assertIn("Run history (advanced)", details_block)

    def test_run_select_no_longer_in_primary_controls_row(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        controls_start = html.find('<div class="controls row">')
        self.assertGreaterEqual(controls_start, 0)
        controls_end = html.find("</div>", controls_start)
        self.assertGreater(controls_end, controls_start)
        primary_controls = html[controls_start:controls_end]
        self.assertNotIn('id="run-select"', primary_controls)

    def test_topic_and_subtopic_selects_remain_in_primary_controls_row(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        controls_start = html.find('<div class="controls row">')
        controls_end = html.find("</div>", controls_start)
        primary_controls = html[controls_start:controls_end]
        self.assertIn('id="topic-select"', primary_controls)
        self.assertIn('id="subtopic-select"', primary_controls)

    def test_run_history_block_contains_advanced_hint(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn(
            "Pick an older run to inspect its labels. Routine review uses the latest run automatically.",
            html,
        )

    def test_ui_revision_advances_for_run_history_advanced(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("channel-overview", UI_REVISION)
        self.assertIn("discovery", UI_REVISION)
        self.assertIn("run-history-advanced", UI_REVISION)


class LatestSubtopicRunIdByTopicTests(unittest.TestCase):
    def _seed_two_runs(self, db_path: Path) -> tuple[int, int]:
        """Seed Health + Business topics; run #1 has subtopic for Health, run #2 for Business."""
        from yt_channel_analyzer.db import create_topic_suggestion_run

        _seed_channel_with_videos(db_path)
        run_discovery(
            db_path,
            project_name="proj",
            llm=lambda videos: DiscoveryPayload(
                topics=["Health", "Business"],
                assignments=[
                    DiscoveryAssignment(
                        youtube_video_id="vid1", topic_name="Health",
                        confidence=0.9, reason="r1",
                    ),
                    DiscoveryAssignment(
                        youtube_video_id="vid2", topic_name="Business",
                        confidence=0.8, reason="r2",
                    ),
                ],
            ),
            model="stub",
            prompt_version="stub-v0",
        )
        run_a = create_topic_suggestion_run(db_path, model_name="stub-run-a")
        run_b = create_topic_suggestion_run(db_path, model_name="stub-run-b")
        with connect(db_path) as conn:
            project_id = conn.execute("SELECT id FROM projects").fetchone()[0]
            topic_ids = {
                row[0]: row[1]
                for row in conn.execute("SELECT name, id FROM topics").fetchall()
            }
            conn.execute(
                "INSERT INTO subtopic_suggestion_labels"
                "(project_id, topic_id, suggestion_run_id, name) VALUES (?, ?, ?, ?)",
                (project_id, topic_ids["Health"], run_a, "Sleep"),
            )
            conn.execute(
                "INSERT INTO subtopic_suggestion_labels"
                "(project_id, topic_id, suggestion_run_id, name) VALUES (?, ?, ?, ?)",
                (project_id, topic_ids["Business"], run_b, "Founders"),
            )
            conn.commit()
        return run_a, run_b

    def test_helper_returns_max_run_id_for_topic(self) -> None:
        from yt_channel_analyzer.review_ui import _latest_subtopic_run_id_for_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            run_a, run_b = self._seed_two_runs(db_path)
            self.assertEqual(_latest_subtopic_run_id_for_topic(db_path, "Health"), run_a)
            self.assertEqual(_latest_subtopic_run_id_for_topic(db_path, "Business"), run_b)

    def test_helper_returns_none_when_topic_has_no_subtopic_run(self) -> None:
        from yt_channel_analyzer.review_ui import _latest_subtopic_run_id_for_topic

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            run_discovery(
                db_path,
                project_name="proj",
                llm=lambda videos: DiscoveryPayload(
                    topics=["Health"],
                    assignments=[
                        DiscoveryAssignment(
                            youtube_video_id="vid1", topic_name="Health",
                            confidence=0.9, reason="r",
                        ),
                    ],
                ),
                model="stub",
                prompt_version="stub-v0",
            )
            self.assertIsNone(_latest_subtopic_run_id_for_topic(db_path, "Health"))
            self.assertIsNone(_latest_subtopic_run_id_for_topic(db_path, "Nonexistent"))

    def test_state_payload_carries_latest_subtopic_run_id_by_topic(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            run_a, run_b = self._seed_two_runs(db_path)
            payload = build_state_payload(db_path)
            self.assertIn("latest_subtopic_run_id_by_topic", payload)
            mapping = payload["latest_subtopic_run_id_by_topic"]
            self.assertEqual(mapping.get("Health"), run_a)
            self.assertEqual(mapping.get("Business"), run_b)

    def test_state_payload_empty_dict_when_no_subtopic_runs(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            payload = build_state_payload(db_path)
            self.assertEqual(payload["latest_subtopic_run_id_by_topic"], {})

    def test_html_topic_select_handler_reads_latest_subtopic_run_id_by_topic(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("latest_subtopic_run_id_by_topic", html)
        topic_handler_idx = html.find("topic-select')")
        self.assertGreaterEqual(topic_handler_idx, 0)
        change_idx = html.find("addEventListener('change'", topic_handler_idx)
        self.assertGreater(change_idx, topic_handler_idx)
        handler_block = html[change_idx:change_idx + 800]
        self.assertIn("latest_subtopic_run_id_by_topic", handler_block)
        self.assertIn("run-select", handler_block)


class TopicInventoryReadinessStateTests(unittest.TestCase):
    def _seed_subtopic_with_videos(
        self,
        db_path: Path,
        *,
        topic_name: str = "Health",
        subtopic_name: str = "Sleep",
        video_count: int,
        transcripts_available: int = 0,
        processed_ok: int = 0,
        extra_transcript_rows_per_video: int = 0,
    ) -> None:
        init_db(
            db_path,
            project_name="proj",
            channel_id="UC123",
            channel_title="Channel",
            channel_handle="@channel",
        )
        videos = [
            VideoMetadata(
                youtube_video_id=f"vid{i}",
                title=f"Episode {i}",
                description=f"desc {i}",
                published_at=f"2026-04-{i:02d}T12:00:00Z",
                thumbnail_url=None,
            )
            for i in range(1, video_count + 1)
        ]
        upsert_videos_for_primary_channel(db_path, videos=videos)
        with connect(db_path) as conn:
            project_id = conn.execute("SELECT id FROM projects").fetchone()[0]
            conn.execute(
                "INSERT INTO topics(project_id, name) VALUES (?, ?)",
                (project_id, topic_name),
            )
            topic_id = conn.execute(
                "SELECT id FROM topics WHERE project_id = ? AND name = ?",
                (project_id, topic_name),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO subtopics(topic_id, name) VALUES (?, ?)",
                (topic_id, subtopic_name),
            )
            subtopic_id = conn.execute(
                "SELECT id FROM subtopics WHERE topic_id = ? AND name = ?",
                (topic_id, subtopic_name),
            ).fetchone()[0]
            video_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM videos ORDER BY id"
                ).fetchall()
            ]
            for vid_id in video_ids:
                conn.execute(
                    "INSERT INTO video_subtopics(video_id, subtopic_id, assignment_source)"
                    " VALUES (?, ?, 'manual')",
                    (vid_id, subtopic_id),
                )
            for vid_id in video_ids[:transcripts_available]:
                conn.execute(
                    "INSERT INTO video_transcripts(video_id, transcript_status)"
                    " VALUES (?, 'available')",
                    (vid_id,),
                )
            for vid_id in video_ids[:processed_ok]:
                conn.execute(
                    "INSERT INTO processed_videos(video_id, processing_status)"
                    " VALUES (?, 'processed')",
                    (vid_id,),
                )
            conn.commit()

    def test_too_few_state_under_threshold(self) -> None:
        from yt_channel_analyzer.review_ui import _build_topic_inventory

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_subtopic_with_videos(
                db_path,
                video_count=2,
                transcripts_available=2,
                processed_ok=1,
            )
            inventory = _build_topic_inventory(db_path, topic_name="Health")
            self.assertIsNotNone(inventory)
            self.assertEqual(len(inventory["subtopics"]), 1)
            bucket = inventory["subtopics"][0]
            self.assertEqual(bucket["video_count"], 2)
            self.assertEqual(bucket["readiness_state"], "too_few")
            self.assertEqual(bucket["readiness_label"], "Too thin to compare")
            self.assertIn("Needs 3 more video", bucket["next_step"])
            self.assertEqual(bucket["transcript_count"], 2)
            self.assertEqual(bucket["processed_count"], 1)
            self.assertFalse(bucket["comparison_ready"])

    def test_needs_transcripts_state_enough_videos_zero_transcripts(self) -> None:
        from yt_channel_analyzer.review_ui import _build_topic_inventory

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_subtopic_with_videos(
                db_path,
                video_count=5,
                transcripts_available=0,
                processed_ok=0,
            )
            inventory = _build_topic_inventory(db_path, topic_name="Health")
            bucket = inventory["subtopics"][0]
            self.assertEqual(bucket["video_count"], 5)
            self.assertEqual(bucket["readiness_state"], "needs_transcripts")
            self.assertEqual(
                bucket["readiness_label"], "Enough videos, no transcripts"
            )
            self.assertIn("Fetch transcripts", bucket["next_step"])
            self.assertEqual(bucket["transcript_count"], 0)
            self.assertEqual(bucket["processed_count"], 0)
            self.assertFalse(bucket["comparison_ready"])

    def test_ready_state_enough_videos_with_transcripts(self) -> None:
        from yt_channel_analyzer.review_ui import _build_topic_inventory

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_subtopic_with_videos(
                db_path,
                video_count=5,
                transcripts_available=3,
                processed_ok=2,
            )
            inventory = _build_topic_inventory(db_path, topic_name="Health")
            bucket = inventory["subtopics"][0]
            self.assertEqual(bucket["video_count"], 5)
            self.assertEqual(bucket["readiness_state"], "ready")
            self.assertEqual(bucket["readiness_label"], "Ready for comparison")
            self.assertEqual(
                bucket["next_step"],
                "Enough videos to generate comparison-group suggestions.",
            )
            self.assertEqual(bucket["transcript_count"], 3)
            self.assertEqual(bucket["processed_count"], 2)
            self.assertTrue(bucket["comparison_ready"])

    def test_transcript_and_processed_counts_dedupe_per_video(self) -> None:
        """A video with both an available transcript and a processed row counts
        once for transcripts and once for processed — no Cartesian inflation."""
        from yt_channel_analyzer.review_ui import _build_topic_inventory

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_subtopic_with_videos(
                db_path,
                video_count=5,
                transcripts_available=5,
                processed_ok=5,
            )
            inventory = _build_topic_inventory(db_path, topic_name="Health")
            bucket = inventory["subtopics"][0]
            self.assertEqual(bucket["video_count"], 5)
            self.assertEqual(bucket["transcript_count"], 5)
            self.assertEqual(bucket["processed_count"], 5)
            self.assertEqual(bucket["readiness_state"], "ready")

    def test_empty_subtopic_bucket_has_zero_counts_and_too_few_state(self) -> None:
        """A subtopic with no videos yields zero counts and `too_few` state —
        the JS sub-line renders `0/0 transcripts` without crashing."""
        from yt_channel_analyzer.review_ui import _build_topic_inventory

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_subtopic_with_videos(
                db_path,
                video_count=0,
                transcripts_available=0,
                processed_ok=0,
            )
            inventory = _build_topic_inventory(db_path, topic_name="Health")
            self.assertIsNotNone(inventory)
            self.assertEqual(len(inventory["subtopics"]), 1)
            bucket = inventory["subtopics"][0]
            self.assertEqual(bucket["video_count"], 0)
            self.assertEqual(bucket["transcript_count"], 0)
            self.assertEqual(bucket["processed_count"], 0)
            self.assertEqual(bucket["readiness_state"], "too_few")
            self.assertFalse(bucket["comparison_ready"])


class ComparisonReadinessHTMLTests(unittest.TestCase):
    def test_html_page_carries_all_three_readiness_class_strings(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("readiness ready", html)
        self.assertIn("readiness needs-transcripts", html)
        self.assertIn("readiness thin", html)

    def test_html_page_includes_needs_transcripts_css_rule(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn(".readiness.needs-transcripts", html)

    def test_html_page_template_keys_pill_off_readiness_state(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("bucket.readiness_state", html)

    def test_html_page_renders_transcript_coverage_subline(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn(
            "${bucket.transcript_count}/${bucket.video_count}", html
        )
        self.assertIn("transcript-coverage", html)

    def test_ui_revision_advances_for_comparison_readiness(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("comparison-readiness", UI_REVISION)
        self.assertIn("channel-overview", UI_REVISION)
        self.assertIn("discovery", UI_REVISION)


class DiscoverRunsCostRollupTests(unittest.TestCase):
    """Cost-per-run rollup in the Discover history panel.

    `run_discovery` threads its newly-allocated `discovery_run_id` as
    `correlation_id` into the LLM call so `_build_discover_runs` can
    `SUM(cost_estimate_usd)` per run via `llm_calls.correlation_id`.
    """

    def test_run_discovery_threads_correlation_id_into_llm_calls(self) -> None:
        from yt_channel_analyzer.discovery import (
            DISCOVERY_PROMPT_NAME,
            DISCOVERY_PROMPT_VERSION,
            discovery_llm_via_extractor,
            register_discovery_prompt,
            run_discovery,
        )
        from yt_channel_analyzer.extractor import Extractor

        saved = dict(_registry_module._PROMPTS)
        _registry_module._PROMPTS.clear()
        try:
            register_discovery_prompt()
            runner = FakeLLMRunner()
            runner.add_response(
                DISCOVERY_PROMPT_NAME,
                DISCOVERY_PROMPT_VERSION,
                {
                    "topics": ["Health"],
                    "assignments": [
                        {
                            "youtube_video_id": "vid1",
                            "topic": "Health",
                            "confidence": 0.9,
                            "reason": "fixture",
                        },
                        {
                            "youtube_video_id": "vid2",
                            "topic": "Health",
                            "confidence": 0.9,
                            "reason": "fixture",
                        },
                    ],
                },
            )

            with TemporaryDirectory() as tmpdir:
                db_path = Path(tmpdir) / "test.sqlite3"
                _seed_channel_with_videos(db_path)
                with connect(db_path) as conn:
                    ensure_schema(conn)
                    extractor = Extractor(connection=conn, runner=runner)
                    callable_ = discovery_llm_via_extractor(extractor)
                    run_id = run_discovery(
                        db_path,
                        project_name="proj",
                        llm=callable_,
                        model="fake-model",
                        prompt_version=DISCOVERY_PROMPT_VERSION,
                    )

                with connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT correlation_id FROM llm_calls"
                    ).fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["correlation_id"], run_id)
        finally:
            _registry_module._PROMPTS.clear()
            _registry_module._PROMPTS.update(saved)

    def test_build_discover_runs_returns_cost_estimate_usd_when_seeded(
        self,
    ) -> None:
        from yt_channel_analyzer.discovery import stub_llm
        from yt_channel_analyzer.review_ui import _build_discover_runs

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )

            with connect(db_path) as conn:
                channel_id = conn.execute(
                    "SELECT id FROM channels WHERE is_primary = 1"
                ).fetchone()[0]
                # Seed two llm_calls rows (e.g. one initial + one retry) so the
                # SUM rollup is exercised, not just a single-row pass-through.
                conn.execute(
                    """
                    INSERT INTO llm_calls(
                        prompt_name, prompt_version, content_hash, model, provider,
                        is_batch, batch_size, parse_status, tokens_in, tokens_out,
                        cost_estimate_usd, correlation_id
                    ) VALUES (?, ?, ?, ?, ?, 0, 1, 'ok', 100, 50, ?, ?)
                    """,
                    ("discovery.topics", "stub-v0", "h", "x", "fake", 0.0012, run_id),
                )
                conn.execute(
                    """
                    INSERT INTO llm_calls(
                        prompt_name, prompt_version, content_hash, model, provider,
                        is_batch, batch_size, parse_status, tokens_in, tokens_out,
                        cost_estimate_usd, correlation_id
                    ) VALUES (?, ?, ?, ?, ?, 0, 1, 'ok', 100, 50, ?, ?)
                    """,
                    ("discovery.topics", "stub-v0", "h", "x", "fake", 0.0007, run_id),
                )
                conn.commit()

            runs = _build_discover_runs(db_path, channel_id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["id"], run_id)
            self.assertAlmostEqual(runs[0]["cost_estimate_usd"], 0.0019)

    def test_build_discover_runs_cost_is_none_when_no_llm_calls(self) -> None:
        from yt_channel_analyzer.discovery import stub_llm
        from yt_channel_analyzer.review_ui import _build_discover_runs

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )
            with connect(db_path) as conn:
                channel_id = conn.execute(
                    "SELECT id FROM channels WHERE is_primary = 1"
                ).fetchone()[0]

            runs = _build_discover_runs(db_path, channel_id)
            self.assertEqual(len(runs), 1)
            self.assertIsNone(runs[0]["cost_estimate_usd"])

    def test_html_discover_run_row_renders_cost_cell(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("dr-cost", html)
        self.assertIn("cost_estimate_usd", html)

    def test_ui_revision_advances_for_discover_cost(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("discover-cost", UI_REVISION)
        # Prior substrings preserved so earlier UI_REVISION assertions
        # still hold.
        self.assertIn("comparison-readiness", UI_REVISION)
        self.assertIn("channel-overview", UI_REVISION)
        self.assertIn("discovery", UI_REVISION)


class ReingestEndpointTests(unittest.TestCase):
    """`POST /api/reingest` re-fetches channel + video metadata via the
    injected fetchers and upserts both.  YouTube errors surface as 400s."""

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

    def _make_metadata(self, *, title: str = "Updated Channel") -> ChannelMetadata:
        return ChannelMetadata(
            youtube_channel_id="UC123",
            title=title,
            description="updated desc",
            custom_url="@channel",
            published_at="2026-01-01T00:00:00Z",
            thumbnail_url="https://example.invalid/thumb.jpg",
        )

    def _make_videos(self, count: int = 3) -> list[VideoMetadata]:
        return [
            VideoMetadata(
                youtube_video_id=f"new{i}",
                title=f"Fresh video {i}",
                description=f"desc {i}",
                published_at=f"2026-05-{i + 1:02d}T12:00:00Z",
                thumbnail_url=None,
            )
            for i in range(count)
        ]

    def test_reingest_returns_ok_with_stubbed_fetchers(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            captured_calls: dict[str, object] = {}

            def metadata_fetcher(channel_id: str) -> ChannelMetadata:
                captured_calls["metadata_channel_id"] = channel_id
                return self._make_metadata()

            def videos_fetcher(channel_id: str, *, limit: int) -> list[VideoMetadata]:
                captured_calls["videos_channel_id"] = channel_id
                captured_calls["videos_limit"] = limit
                return self._make_videos(3)

            app = ReviewUIApp(
                db_path,
                channel_metadata_fetcher=metadata_fetcher,
                channel_videos_fetcher=videos_fetcher,
            )
            status, body = self._call_app(app, "POST", "/api/reingest", body={})

            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["video_count"], 3)
            self.assertEqual(payload["channel_title"], "Updated Channel")
            self.assertEqual(payload["youtube_channel_id"], "UC123")
            self.assertIsNotNone(payload["last_refreshed_at"])
            self.assertIn("Re-ingested", payload["message"])
            self.assertEqual(captured_calls["metadata_channel_id"], "UC123")
            self.assertEqual(captured_calls["videos_channel_id"], "UC123")
            self.assertEqual(captured_calls["videos_limit"], 50)

    def test_reingest_persists_updated_channel_and_video_rows(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(
                db_path,
                channel_metadata_fetcher=lambda _: self._make_metadata(
                    title="Renamed By Reingest"
                ),
                channel_videos_fetcher=lambda _id, *, limit: self._make_videos(2),
            )
            status, _body = self._call_app(app, "POST", "/api/reingest", body={})
            self.assertEqual(status, "200 OK")

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                channel = conn.execute(
                    "SELECT title, last_refreshed_at FROM channels WHERE youtube_channel_id = ?",
                    ("UC123",),
                ).fetchone()
                self.assertEqual(channel["title"], "Renamed By Reingest")
                self.assertIsNotNone(channel["last_refreshed_at"])

                video_ids = {
                    row[0]
                    for row in conn.execute(
                        "SELECT youtube_video_id FROM videos"
                    ).fetchall()
                }
                self.assertIn("new0", video_ids)
                self.assertIn("new1", video_ids)
                # seeded videos remain — upsert is additive
                self.assertIn("vid1", video_ids)
                self.assertIn("vid2", video_ids)

    def test_reingest_clamps_oversized_limit_to_default(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp, REINGEST_DEFAULT_LIMIT

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            seen_limits: list[int] = []

            def videos_fetcher(channel_id: str, *, limit: int) -> list[VideoMetadata]:
                seen_limits.append(limit)
                return []

            app = ReviewUIApp(
                db_path,
                channel_metadata_fetcher=lambda _: self._make_metadata(),
                channel_videos_fetcher=videos_fetcher,
            )
            status, _body = self._call_app(
                app, "POST", "/api/reingest", body={"limit": 500}
            )
            self.assertEqual(status, "200 OK")
            self.assertEqual(seen_limits, [REINGEST_DEFAULT_LIMIT])

    def test_reingest_returns_400_when_metadata_fetcher_raises(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp
        from yt_channel_analyzer.youtube import YouTubeAPIError

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            def metadata_fetcher(_channel_id: str) -> ChannelMetadata:
                raise YouTubeAPIError("channel not found: UC123")

            def videos_fetcher(*_args, **_kwargs) -> list[VideoMetadata]:
                self.fail("videos fetcher should not be called when metadata fails")

            app = ReviewUIApp(
                db_path,
                channel_metadata_fetcher=metadata_fetcher,
                channel_videos_fetcher=videos_fetcher,
            )
            status, body = self._call_app(app, "POST", "/api/reingest", body={})
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("Re-ingest failed", payload["error"])
            self.assertIn("channel not found", payload["error"])

    def test_reingest_returns_400_when_videos_fetcher_raises(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp
        from yt_channel_analyzer.youtube import YouTubeAPIError

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            def videos_fetcher(*_args, **_kwargs) -> list[VideoMetadata]:
                raise YouTubeAPIError("uploads playlist not found for channel: UC123")

            app = ReviewUIApp(
                db_path,
                channel_metadata_fetcher=lambda _: self._make_metadata(),
                channel_videos_fetcher=videos_fetcher,
            )
            status, body = self._call_app(app, "POST", "/api/reingest", body={})
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("Re-ingest failed", payload["error"])
            self.assertIn("uploads playlist not found", payload["error"])

    def test_reingest_returns_400_when_no_primary_channel(self) -> None:
        from yt_channel_analyzer.db import ensure_schema
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with connect(db_path) as conn:
                ensure_schema(conn)

            app = ReviewUIApp(
                db_path,
                channel_metadata_fetcher=lambda _: self._make_metadata(),
                channel_videos_fetcher=lambda _id, *, limit: [],
            )
            status, body = self._call_app(app, "POST", "/api/reingest", body={})
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("primary channel", payload["error"])

    def test_reingest_default_fetcher_surfaces_missing_api_key(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            with mock.patch.dict(os.environ, {}, clear=True):
                app = ReviewUIApp(db_path)
                status, body = self._call_app(
                    app, "POST", "/api/reingest", body={}
                )
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("YOUTUBE_API_KEY", payload["error"])

    def test_reingest_button_html_calls_api_endpoint(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("supply-reingest-btn", html)
        self.assertIn("/api/reingest", html)
        self.assertIn("Re-ingesting", html)

    def test_ui_revision_advances_for_reingest(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("reingest", UI_REVISION)
        # Earlier UI_REVISION substrings preserved.
        self.assertIn("discover-cost", UI_REVISION)
        self.assertIn("comparison-readiness", UI_REVISION)
        self.assertIn("channel-overview", UI_REVISION)


class DiscoverEndpointTests(unittest.TestCase):
    """`POST /api/discover` drives `run_discovery` in stub or real mode via
    an injectable runner. Real mode rides the existing
    `RALPH_ALLOW_REAL_LLM=1` gate inside `make_real_llm_callable`; missing
    env surfaces as a 400 with the gate message verbatim."""

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

    def test_discover_stub_mode_creates_discovery_run(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            # run_in_background=False keeps the call synchronous so we can
            # observe the post-run DB state deterministically. Production
            # default (True) spawns a daemon thread per request.
            app = ReviewUIApp(db_path, run_in_background=False)
            status, body = self._call_app(
                app, "POST", "/api/discover", body={"mode": "stub"}
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["mode"], "stub")
            self.assertIsInstance(payload["run_id"], int)
            self.assertIn("started", payload["message"])

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, status FROM discovery_runs WHERE id = ?",
                    (payload["run_id"],),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "success")

    def test_discover_real_mode_without_env_var_returns_400(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            with mock.patch.dict(os.environ, {}, clear=True):
                app = ReviewUIApp(db_path)
                status, body = self._call_app(
                    app, "POST", "/api/discover", body={"mode": "real"}
                )
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("RALPH_ALLOW_REAL_LLM", payload["error"])

    def test_discover_real_mode_with_env_var_calls_injected_runner(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            recorded: dict[str, object] = {}

            def runner(db_path_arg, *, mode, run_id):
                # The handler pre-allocates the discovery_runs row and
                # passes its id to the runner, so the runner's role is
                # narrowed to "drive run_discovery against that id".
                recorded["db_path"] = Path(db_path_arg)
                recorded["mode"] = mode
                recorded["run_id"] = run_id
                return {"run_id": run_id, "model": "x", "prompt_version": "v"}

            with mock.patch.dict(
                os.environ, {"RALPH_ALLOW_REAL_LLM": "1"}, clear=True
            ):
                app = ReviewUIApp(
                    db_path, discover_runner=runner, run_in_background=False
                )
                status, body = self._call_app(
                    app, "POST", "/api/discover", body={"mode": "real"}
                )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["mode"], "real")
            self.assertIsInstance(payload["run_id"], int)
            # The model now comes from `_discover_mode_config(mode)`
            # (DEFAULT_MODEL for "real"), not from the runner's return value.
            self.assertTrue(payload["model"])
            self.assertEqual(recorded["mode"], "real")
            self.assertEqual(recorded["db_path"], db_path)
            self.assertEqual(recorded["run_id"], payload["run_id"])

    def test_discover_missing_mode_returns_400(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(app, "POST", "/api/discover", body={})
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("mode", payload["error"])

    def test_discover_unknown_mode_returns_400(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app, "POST", "/api/discover", body={"mode": "wild"}
            )
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("invalid mode", payload["error"])

    def test_run_discovery_button_html_calls_api_endpoint(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("discover-run-btn", html)
        self.assertIn("/api/discover", html)
        self.assertIn("discover-confirm-modal", html)

    def test_ui_revision_advances_for_run_discovery(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("run-discovery", UI_REVISION)
        # Earlier UI_REVISION substrings preserved.
        self.assertIn("reingest", UI_REVISION)
        self.assertIn("discover-cost", UI_REVISION)
        # New: streaming-poll marker for the async discovery slice.
        self.assertIn("streaming-poll", UI_REVISION)

    def test_discover_endpoint_pre_allocates_running_row_then_runs(self) -> None:
        """Sync test of the new async contract: pre-allocate a 'running'
        row, return its id immediately, then drive the runner against it.
        With ``run_in_background=False`` the runner finishes before the
        response — so we observe the row flipped to 'success' and the
        runner saw the pre-allocated id (not one of its own choosing)."""
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            seen: dict[str, object] = {}

            def runner(db_path_arg, *, mode, run_id):
                seen["run_id"] = run_id
                # Read the row state mid-runner: should be 'running' since
                # run_discovery hasn't finished its UPDATE yet.
                with connect(db_path_arg) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        "SELECT status FROM discovery_runs WHERE id = ?",
                        (run_id,),
                    ).fetchone()
                seen["mid_status"] = row["status"] if row else None
                # Manually finish the run (we're stubbing run_discovery here).
                with connect(db_path_arg) as conn:
                    conn.execute(
                        "UPDATE discovery_runs SET status = 'success' WHERE id = ?",
                        (run_id,),
                    )
                    conn.commit()
                return {"run_id": run_id, "model": "x", "prompt_version": "v"}

            app = ReviewUIApp(
                db_path, discover_runner=runner, run_in_background=False
            )
            status, body = self._call_app(
                app, "POST", "/api/discover", body={"mode": "stub"}
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(seen["mid_status"], "running")
            self.assertEqual(seen["run_id"], payload["run_id"])

    def test_discovery_run_status_endpoint_returns_row(self) -> None:
        """`GET /api/discovery_runs/<id>` returns a small status payload —
        the polling target the JS modal hits every 1.5s while a run is in
        flight. Bounded shape (no topic-map blob)."""
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(db_path, run_in_background=False)
            # Drive a stub run end-to-end so we have a 'success' row.
            self._call_app(app, "POST", "/api/discover", body={"mode": "stub"})

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id FROM discovery_runs ORDER BY id LIMIT 1"
                ).fetchone()
            run_id = int(row["id"])

            status, body = self._call_app(
                app, "GET", f"/api/discovery_runs/{run_id}"
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["id"], run_id)
            self.assertEqual(payload["status"], "success")
            self.assertIsNone(payload["error_message"])
            self.assertIn("model", payload)
            self.assertIn("prompt_version", payload)

    def test_discovery_run_status_endpoint_404_for_unknown_id(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            app = ReviewUIApp(db_path, run_in_background=False)
            status, body = self._call_app(
                app, "GET", "/api/discovery_runs/9999"
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("not found", json.loads(body)["error"])

    def test_discovery_runs_status_check_constraint_allows_running(self) -> None:
        """The schema allows 'running' as a status. A pre-existing DB built
        against the old CHECK (success|error) gets rebuilt by the migration
        in ``ensure_schema`` — verified by inserting a 'running' row after
        ensure_schema runs."""
        from yt_channel_analyzer.db import ensure_schema as _ensure
        from yt_channel_analyzer.discovery import allocate_discovery_run

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            # Force the old constraint to simulate a pre-migration DB.
            with connect(db_path) as conn:
                conn.executescript("PRAGMA foreign_keys = OFF;")
                conn.executescript("PRAGMA legacy_alter_table = ON;")
                conn.executescript(
                    """
                    DROP TABLE discovery_runs;
                    CREATE TABLE discovery_runs (
                        id INTEGER PRIMARY KEY,
                        channel_id INTEGER NOT NULL,
                        model TEXT NOT NULL,
                        prompt_version TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'success',
                        error_message TEXT,
                        raw_response TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                        CHECK (status IN ('success', 'error'))
                    );
                    """
                )
                conn.executescript("PRAGMA legacy_alter_table = OFF;")
                conn.executescript("PRAGMA foreign_keys = ON;")
                conn.commit()
            # Re-running ensure_schema rebuilds the table to allow 'running'.
            with connect(db_path) as conn:
                _ensure(conn)
                conn.commit()
            run_id = allocate_discovery_run(
                db_path,
                project_name="proj",
                model="m",
                prompt_version="v",
            )
            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT status FROM discovery_runs WHERE id = ?", (run_id,)
                ).fetchone()
            self.assertEqual(row["status"], "running")


class ChannelEditEndpointTests(unittest.TestCase):
    """`POST /api/channel/edit` updates the primary channel's display fields
    (title, handle, description). YouTube-derived fields stay untouched."""

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

    def test_edit_updates_title_handle_description(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app,
                "POST",
                "/api/channel/edit",
                body={
                    "title": "Friendlier Name",
                    "handle": "@friendly",
                    "description": "Curated description.",
                },
            )

            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["channel_title"], "Friendlier Name")
            self.assertEqual(payload["handle"], "@friendly")
            self.assertEqual(payload["description"], "Curated description.")
            self.assertEqual(payload["youtube_channel_id"], "UC123")
            self.assertIn("Updated channel", payload["message"])

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT title, handle, description, youtube_channel_id "
                    "FROM channels WHERE youtube_channel_id = ?",
                    ("UC123",),
                ).fetchone()
            self.assertEqual(row["title"], "Friendlier Name")
            self.assertEqual(row["handle"], "@friendly")
            self.assertEqual(row["description"], "Curated description.")
            self.assertEqual(row["youtube_channel_id"], "UC123")

    def test_edit_blank_handle_and_description_persist_as_null(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(db_path)
            status, _body = self._call_app(
                app,
                "POST",
                "/api/channel/edit",
                body={"title": "Just Title", "handle": "", "description": "   "},
            )
            self.assertEqual(status, "200 OK")

            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT title, handle, description FROM channels WHERE youtube_channel_id = ?",
                    ("UC123",),
                ).fetchone()
            self.assertEqual(row[0], "Just Title")
            self.assertIsNone(row[1])
            self.assertIsNone(row[2])

    def test_edit_missing_title_returns_400(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app, "POST", "/api/channel/edit", body={"handle": "@x"}
            )
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("title", payload["error"])

    def test_edit_blank_title_returns_400(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app, "POST", "/api/channel/edit", body={"title": "   "}
            )
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("title", payload["error"])

    def test_edit_returns_400_when_no_primary_channel(self) -> None:
        from yt_channel_analyzer.db import ensure_schema
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with connect(db_path) as conn:
                ensure_schema(conn)

            app = ReviewUIApp(db_path)
            status, body = self._call_app(
                app, "POST", "/api/channel/edit", body={"title": "X"}
            )
            self.assertEqual(status, "400 Bad Request")
            payload = json.loads(body)
            self.assertIn("primary channel", payload["error"])

    def test_edit_button_html_opens_edit_modal(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("supply-edit-btn", html)
        self.assertIn("channel-edit-modal", html)
        self.assertIn("/api/channel/edit", html)
        self.assertIn("openChannelEdit", html)

    def test_ui_revision_advances_for_edit_channel(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("edit-channel", UI_REVISION)
        # Earlier UI_REVISION substrings preserved.
        self.assertIn("run-discovery", UI_REVISION)
        self.assertIn("reingest", UI_REVISION)


def _seed_channel_with_n_videos(db_path: Path, count: int) -> None:
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
                youtube_video_id=f"vid{i:03d}",
                title=f"Video {i:03d}",
                description="desc",
                published_at=f"2026-01-{(i % 28) + 1:02d}T12:00:00Z",
                thumbnail_url=None,
            )
            for i in range(count)
        ],
    )


class SupplyPaginationTests(unittest.TestCase):
    """`build_state_payload` accepts an optional `supply_limit` and clamps it
    to [1, SUPPLY_MAX_LIMIT]; `/api/state?supply_limit=N` plumbs through; the
    Supply page renders a Load-more button when more videos exist."""

    def test_default_limit_caps_supply_videos_at_50(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_n_videos(db_path, 75)

            payload = build_state_payload(db_path)
            self.assertEqual(len(payload["supply_videos"]), 50)
            self.assertEqual(payload["supply_limit"], 50)

    def test_supply_limit_param_returns_more(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_n_videos(db_path, 120)

            payload = build_state_payload(db_path, supply_limit=100)
            self.assertEqual(len(payload["supply_videos"]), 100)
            self.assertEqual(payload["supply_limit"], 100)

    def test_supply_limit_clamps_below_one(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_n_videos(db_path, 10)

            payload = build_state_payload(db_path, supply_limit=0)
            self.assertEqual(payload["supply_limit"], 1)
            self.assertEqual(len(payload["supply_videos"]), 1)

    def test_supply_limit_clamps_to_max(self) -> None:
        from yt_channel_analyzer.review_ui import (
            SUPPLY_MAX_LIMIT,
            build_state_payload,
        )

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_n_videos(db_path, 5)

            payload = build_state_payload(db_path, supply_limit=10_000)
            self.assertEqual(payload["supply_limit"], SUPPLY_MAX_LIMIT)
            self.assertEqual(payload["supply_max_limit"], SUPPLY_MAX_LIMIT)

    def test_state_endpoint_parses_supply_limit_query(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_n_videos(db_path, 75)

            app = ReviewUIApp(db_path)
            environ = {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/api/state",
                "QUERY_STRING": "supply_limit=60",
                "wsgi.input": io.BytesIO(b""),
            }
            captured: dict[str, object] = {}

            def start_response(status: str, headers: list[tuple[str, str]]) -> None:
                captured["status"] = status

            body_bytes = b"".join(app(environ, start_response))
            self.assertEqual(captured["status"], "200 OK")
            payload = json.loads(body_bytes.decode("utf-8"))
            self.assertEqual(payload["supply_limit"], 60)
            self.assertEqual(len(payload["supply_videos"]), 60)

    def test_load_more_button_html_wired(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        html = ReviewUIApp._render_html_page()
        self.assertIn("supply-load-more", html)
        self.assertIn("loadMoreSupply", html)
        self.assertIn("supplyLimit", html)
        self.assertIn("supply_limit", html)

    def test_ui_revision_advances_for_supply_pagination(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("supply-pagination", UI_REVISION)
        # Earlier UI_REVISION substrings preserved.
        self.assertIn("edit-channel", UI_REVISION)
        self.assertIn("run-discovery", UI_REVISION)
        self.assertIn("reingest", UI_REVISION)


class Iso8601DurationParsingTests(unittest.TestCase):
    def test_parses_common_forms(self) -> None:
        from yt_channel_analyzer.youtube import parse_iso8601_duration

        self.assertEqual(parse_iso8601_duration("PT0S"), 0)
        self.assertEqual(parse_iso8601_duration("PT45S"), 45)
        self.assertEqual(parse_iso8601_duration("PT2M30S"), 150)
        self.assertEqual(parse_iso8601_duration("PT1H2M3S"), 3723)
        self.assertEqual(parse_iso8601_duration("PT1H"), 3600)
        self.assertEqual(parse_iso8601_duration("P1DT2H"), 93600)

    def test_missing_or_unparseable_returns_none(self) -> None:
        from yt_channel_analyzer.youtube import parse_iso8601_duration

        self.assertIsNone(parse_iso8601_duration(None))
        self.assertIsNone(parse_iso8601_duration(""))
        self.assertIsNone(parse_iso8601_duration("bogus"))
        self.assertIsNone(parse_iso8601_duration("2M30S"))  # missing leading P


class ShortsStockpileSchemaTests(unittest.TestCase):
    def test_fresh_schema_has_new_columns(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh.sqlite3"
            with connect(db_path) as conn:
                ensure_schema(conn)
                videos_cols = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
                channels_cols = {row[1] for row in conn.execute("PRAGMA table_info(channels)")}
                runs_cols = {row[1] for row in conn.execute("PRAGMA table_info(discovery_runs)")}
        self.assertIn("duration_seconds", videos_cols)
        self.assertIn("exclude_shorts", channels_cols)
        for col in (
            "shorts_cutoff_seconds",
            "n_episodes_total",
            "n_shorts_excluded",
            "n_orphaned_wrong_marks",
            "n_orphaned_renames",
        ):
            self.assertIn(col, runs_cols)

    def test_ensure_schema_alters_legacy_tables(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.sqlite3"
            with connect(db_path) as conn:
                # Pre-pivot-shaped tables that predate the new columns.
                conn.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
                    "youtube_channel_id TEXT NOT NULL, title TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE videos (id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, "
                    "youtube_video_id TEXT NOT NULL, title TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE discovery_runs (id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, "
                    "model TEXT NOT NULL, prompt_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running')"
                )
                conn.commit()
                ensure_schema(conn)
                videos_cols = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
                channels_cols = {row[1] for row in conn.execute("PRAGMA table_info(channels)")}
                runs_cols = {row[1] for row in conn.execute("PRAGMA table_info(discovery_runs)")}
        self.assertIn("duration_seconds", videos_cols)
        self.assertIn("exclude_shorts", channels_cols)
        self.assertTrue(
            {"shorts_cutoff_seconds", "n_episodes_total", "n_shorts_excluded",
             "n_orphaned_wrong_marks", "n_orphaned_renames"}.issubset(runs_cols)
        )

    def test_exclude_shorts_defaults_to_one(self) -> None:
        # Slice C flipped the column default: brand-new channels start with the
        # shorts filter on.
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "default.sqlite3"
            _seed_channel_with_videos(db_path)
            with connect(db_path) as conn:
                row = conn.execute("SELECT exclude_shorts FROM channels WHERE is_primary = 1").fetchone()
            self.assertEqual(row[0], 1)


class FetchVideoDurationsTests(unittest.TestCase):
    def test_fetch_channel_videos_enriches_with_duration(self) -> None:
        from yt_channel_analyzer import youtube

        def fake_fetch_json(url: str) -> dict:
            if "/channels?" in url:
                return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]}
            if "/playlistItems?" in url:
                return {
                    "items": [
                        {
                            "snippet": {"title": "Long ep", "description": "d", "publishedAt": "2026-01-01T00:00:00Z", "thumbnails": {}},
                            "contentDetails": {"videoId": "vidL", "videoPublishedAt": "2026-01-01T00:00:00Z"},
                        },
                        {
                            "snippet": {"title": "Short", "description": "d", "publishedAt": "2026-01-02T00:00:00Z", "thumbnails": {}},
                            "contentDetails": {"videoId": "vidS", "videoPublishedAt": "2026-01-02T00:00:00Z"},
                        },
                    ]
                }
            if "/videos?" in url:
                return {
                    "items": [
                        {"id": "vidL", "contentDetails": {"duration": "PT1H5M"}},
                        {"id": "vidS", "contentDetails": {"duration": "PT45S"}},
                    ]
                }
            raise AssertionError(f"unexpected URL: {url}")

        original = youtube.fetch_json
        youtube.fetch_json = fake_fetch_json
        try:
            videos = youtube.fetch_channel_videos("UC123", api_key="k", limit=25)
        finally:
            youtube.fetch_json = original
        by_id = {v.youtube_video_id: v.duration_seconds for v in videos}
        self.assertEqual(by_id, {"vidL": 3900, "vidS": 45})

    def test_fetch_video_durations_no_ids_makes_no_call(self) -> None:
        from yt_channel_analyzer import youtube

        original = youtube.fetch_json
        youtube.fetch_json = lambda url: (_ for _ in ()).throw(AssertionError("should not be called"))
        try:
            self.assertEqual(youtube.fetch_video_durations([]), {})
        finally:
            youtube.fetch_json = original


class BackfillDurationsCLITests(unittest.TestCase):
    def test_backfill_durations_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "backfill.sqlite3"
            _seed_channel_with_videos(db_path)  # vid1, vid2 with NULL duration

            calls: list[list[str]] = []

            def fake_fetch_video_durations(video_ids, *, api_key=None):
                calls.append(list(video_ids))
                return {"vid1": 4200, "vid2": 30}

            original = cli.fetch_video_durations
            cli.fetch_video_durations = fake_fetch_video_durations
            try:
                self.assertEqual(cli.main(["backfill-durations", "--db-path", str(db_path)]), 0)
                with connect(db_path) as conn:
                    rows = dict(conn.execute("SELECT youtube_video_id, duration_seconds FROM videos"))
                self.assertEqual(rows, {"vid1": 4200, "vid2": 30})
                self.assertEqual(calls, [["vid1", "vid2"]])

                # Second run: nothing missing, fetcher must not be invoked.
                self.assertEqual(cli.main(["backfill-durations", "--db-path", str(db_path)]), 0)
                self.assertEqual(calls, [["vid1", "vid2"]])
                with connect(db_path) as conn:
                    rows = dict(conn.execute("SELECT youtube_video_id, duration_seconds FROM videos"))
                self.assertEqual(rows, {"vid1": 4200, "vid2": 30})
            finally:
                cli.fetch_video_durations = original

    def test_backfill_durations_only_touches_null_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "partial.sqlite3"
            _seed_channel_with_videos(db_path)
            with connect(db_path) as conn:
                conn.execute("UPDATE videos SET duration_seconds = 999 WHERE youtube_video_id = 'vid1'")
                conn.commit()

            def fake_fetch_video_durations(video_ids, *, api_key=None):
                self.assertEqual(list(video_ids), ["vid2"])
                return {"vid2": 60}

            original = cli.fetch_video_durations
            cli.fetch_video_durations = fake_fetch_video_durations
            try:
                self.assertEqual(cli.main(["backfill-durations", "--db-path", str(db_path)]), 0)
            finally:
                cli.fetch_video_durations = original
            with connect(db_path) as conn:
                rows = dict(conn.execute("SELECT youtube_video_id, duration_seconds FROM videos"))
            self.assertEqual(rows, {"vid1": 999, "vid2": 60})


class ShortsFilterDiscoveryTests(unittest.TestCase):
    """Slice B — `run_discovery` shorts filter + per-run override + audit fields."""

    def _seed(self, db_path: Path, *, durations, exclude_shorts: int = 0) -> None:
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
                    youtube_video_id=f"vid{i}",
                    title=f"Episode {i}",
                    description="desc",
                    published_at=f"2026-04-0{i}T12:00:00Z",
                    thumbnail_url=None,
                    duration_seconds=dur,
                )
                for i, dur in enumerate(durations, start=1)
            ],
        )
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE channels SET exclude_shorts = ?", (exclude_shorts,)
            )
            conn.commit()

    @staticmethod
    def _recording_stub():
        seen: list[list[str]] = []

        def llm(videos, *, correlation_id=None):
            seen.append([v.youtube_video_id for v in videos])
            return stub_llm(videos, correlation_id=correlation_id)

        return llm, seen

    def _topic_map_video_ids(self, db_path: Path, run_id: int) -> set[str]:
        with connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT v.youtube_video_id
                FROM video_topics vt JOIN videos v ON v.id = vt.video_id
                WHERE vt.discovery_run_id = ?
                """,
                (run_id,),
            ).fetchall()
        return {r[0] for r in rows}

    def _run_audit(self, db_path: Path, run_id: int) -> sqlite3.Row:
        with connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """
                SELECT shorts_cutoff_seconds, n_episodes_total, n_shorts_excluded
                FROM discovery_runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()

    def test_channel_exclude_shorts_filters_audit_and_skips_llm(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            # 181 kept (> 180); 180 and 60 excluded (<= 180).
            self._seed(db_path, durations=[181, 180, 60], exclude_shorts=1)
            llm, seen = self._recording_stub()
            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=llm,
                model="stub",
                prompt_version="stub-v0",
            )
            self.assertEqual(self._topic_map_video_ids(db_path, run_id), {"vid1"})
            # Filter is upstream of the LLM: the prompt for an excluded video
            # is never built.
            self.assertEqual(seen, [["vid1"]])
            audit = self._run_audit(db_path, run_id)
            self.assertEqual(audit["shorts_cutoff_seconds"], 180)
            self.assertEqual(audit["n_episodes_total"], 3)
            self.assertEqual(audit["n_shorts_excluded"], 2)

    def test_channel_include_default_keeps_everything(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, durations=[300, 60, 600], exclude_shorts=0)
            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )
            self.assertEqual(
                self._topic_map_video_ids(db_path, run_id), {"vid1", "vid2", "vid3"}
            )
            audit = self._run_audit(db_path, run_id)
            self.assertIsNone(audit["shorts_cutoff_seconds"])
            self.assertEqual(audit["n_episodes_total"], 3)
            self.assertEqual(audit["n_shorts_excluded"], 0)

    def test_include_shorts_override_beats_channel_exclude(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, durations=[300, 60], exclude_shorts=1)
            self.assertEqual(
                cli.main(
                    [
                        "discover",
                        "--db-path",
                        str(db_path),
                        "--project-name",
                        "proj",
                        "--stub",
                        "--include-shorts",
                    ]
                ),
                0,
            )
            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                run_id = conn.execute("SELECT id FROM discovery_runs").fetchone()["id"]
                # Override does not mutate the channel's sticky setting.
                self.assertEqual(
                    conn.execute("SELECT exclude_shorts FROM channels").fetchone()[0],
                    1,
                )
            self.assertEqual(
                self._topic_map_video_ids(db_path, run_id), {"vid1", "vid2"}
            )
            audit = self._run_audit(db_path, run_id)
            self.assertIsNone(audit["shorts_cutoff_seconds"])
            self.assertEqual(audit["n_shorts_excluded"], 0)

    def test_exclude_shorts_override_beats_channel_include(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, durations=[300, 60], exclude_shorts=0)
            self.assertEqual(
                cli.main(
                    [
                        "discover",
                        "--db-path",
                        str(db_path),
                        "--project-name",
                        "proj",
                        "--stub",
                        "--exclude-shorts",
                    ]
                ),
                0,
            )
            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                run_id = conn.execute("SELECT id FROM discovery_runs").fetchone()["id"]
                self.assertEqual(
                    conn.execute("SELECT exclude_shorts FROM channels").fetchone()[0],
                    0,
                )
            self.assertEqual(self._topic_map_video_ids(db_path, run_id), {"vid1"})
            audit = self._run_audit(db_path, run_id)
            self.assertEqual(audit["shorts_cutoff_seconds"], 180)
            self.assertEqual(audit["n_episodes_total"], 2)
            self.assertEqual(audit["n_shorts_excluded"], 1)

    def test_shorts_flags_are_mutually_exclusive(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, durations=[300], exclude_shorts=0)
            with self.assertRaises(SystemExit):
                cli.main(
                    [
                        "discover",
                        "--db-path",
                        str(db_path),
                        "--project-name",
                        "proj",
                        "--stub",
                        "--exclude-shorts",
                        "--include-shorts",
                    ]
                )

    def test_all_shorts_channel_raises_clear_error_and_persists_no_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, durations=[60, 30], exclude_shorts=1)
            with self.assertRaises(ValueError) as ctx:
                run_discovery(
                    db_path,
                    project_name="proj",
                    llm=stub_llm,
                    model="stub",
                    prompt_version="stub-v0",
                )
            self.assertIn("--include-shorts", str(ctx.exception))
            with connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0],
                    0,
                )

    def test_null_durations_kept_as_long(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, durations=[None, 60, None], exclude_shorts=1)
            run_id = run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )
            # NULL-duration episodes are treated as long → kept; only vid2 drops.
            self.assertEqual(
                self._topic_map_video_ids(db_path, run_id), {"vid1", "vid3"}
            )
            audit = self._run_audit(db_path, run_id)
            self.assertEqual(audit["shorts_cutoff_seconds"], 180)
            self.assertEqual(audit["n_episodes_total"], 3)
            self.assertEqual(audit["n_shorts_excluded"], 1)


class ShortsFlipDefaultMigrationTests(unittest.TestCase):
    """Slice C — one-shot `exclude_shorts` default flip on existing DBs."""

    _PRE_C_CHANNELS_SQL = """
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
            exclude_shorts INTEGER NOT NULL DEFAULT 0 CHECK (exclude_shorts IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, youtube_channel_id)
        )
    """

    def _seed_pre_c_db(self, db_path: Path) -> None:
        with connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL "
                "UNIQUE, slug TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(self._PRE_C_CHANNELS_SQL)
            conn.execute(
                """
                CREATE TABLE videos (
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
                )
                """
            )
            conn.execute("INSERT INTO projects(name) VALUES ('p')")
            conn.execute(
                "INSERT INTO channels(project_id, youtube_channel_id, title, "
                "is_primary, exclude_shorts) VALUES (1, 'UCa', 'A', 1, 0)"
            )
            conn.execute(
                "INSERT INTO channels(project_id, youtube_channel_id, title, "
                "exclude_shorts) VALUES (1, 'UCb', 'B', 0)"
            )
            conn.execute(
                "INSERT INTO videos(channel_id, youtube_video_id, title) "
                "VALUES (1, 'v1', 'V1')"
            )
            conn.commit()

    def test_migration_flips_every_channel_once_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "preC.sqlite3"
            self._seed_pre_c_db(db_path)

            with connect(db_path) as conn:
                ensure_schema(conn)
                vals = [
                    r[0]
                    for r in conn.execute(
                        "SELECT exclude_shorts FROM channels "
                        "ORDER BY youtube_channel_id"
                    )
                ]
                self.assertEqual(vals, [1, 1])
                create_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name='channels'"
                ).fetchone()[0]
                self.assertIn(
                    "exclude_shorts INTEGER NOT NULL DEFAULT 1", create_sql
                )
                self.assertNotIn(
                    "exclude_shorts INTEGER NOT NULL DEFAULT 0", create_sql
                )
                # FK child rows survive the table rebuild.
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM videos WHERE channel_id = 1"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

            # A channel manually flipped back to 0 must not be re-flipped on the
            # next ensure_schema (idempotency guard = create-SQL inspection).
            with connect(db_path) as conn:
                conn.execute(
                    "UPDATE channels SET exclude_shorts = 0 "
                    "WHERE youtube_channel_id = 'UCb'"
                )
                conn.commit()
            with connect(db_path) as conn:
                ensure_schema(conn)
                self.assertEqual(
                    dict(
                        conn.execute(
                            "SELECT youtube_channel_id, exclude_shorts FROM channels"
                        )
                    ),
                    {"UCa": 1, "UCb": 0},
                )

    def test_fresh_db_defaults_exclude_shorts_to_one(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh.sqlite3"
            with connect(db_path) as conn:
                ensure_schema(conn)
                conn.execute(
                    "INSERT INTO projects(name) VALUES ('p')"
                )
                conn.execute(
                    "INSERT INTO channels(project_id, youtube_channel_id, title) "
                    "VALUES (1, 'UCx', 'X')"
                )
                conn.commit()
                self.assertEqual(
                    conn.execute(
                        "SELECT exclude_shorts FROM channels WHERE youtube_channel_id='UCx'"
                    ).fetchone()[0],
                    1,
                )


class ShortsOrphanCountTests(unittest.TestCase):
    """Slice C — `run_discovery` curation-orphan counts + UI badge payload."""

    @staticmethod
    def _orphan_llm():
        """A stub that always assigns every kept video to 'Long Topic' and,
        only when the short ``vidS`` survives the filter, also to
        'Shorts-Only Topic' — so that topic loses all evidence once the filter
        drops the short.
        """
        def llm(videos, *, correlation_id=None):
            ids = [v.youtube_video_id for v in videos]
            assignments = [
                DiscoveryAssignment(
                    youtube_video_id=i,
                    topic_name="Long Topic",
                    confidence=1.0,
                    reason="r",
                    subtopic_name=None,
                )
                for i in ids
            ]
            if "vidS" in ids:
                assignments.append(
                    DiscoveryAssignment(
                        youtube_video_id="vidS",
                        topic_name="Shorts-Only Topic",
                        confidence=0.9,
                        reason="r",
                        subtopic_name=None,
                    )
                )
            return DiscoveryPayload(
                topics=["Long Topic", "Shorts-Only Topic"],
                subtopics=[],
                assignments=assignments,
            )

        return llm

    def _seed(self, db_path: Path, *, exclude_shorts: int) -> None:
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
                    title="Long episode",
                    description="desc",
                    published_at="2026-04-01T12:00:00Z",
                    thumbnail_url=None,
                    duration_seconds=300,
                ),
                VideoMetadata(
                    youtube_video_id="vidS",
                    title="Short clip",
                    description="desc",
                    published_at="2026-04-02T12:00:00Z",
                    thumbnail_url=None,
                    duration_seconds=60,
                ),
            ],
        )
        self._set_exclude_shorts(db_path, exclude_shorts)

    @staticmethod
    def _set_exclude_shorts(db_path: Path, value: int) -> None:
        with connect(db_path) as conn:
            conn.execute("UPDATE channels SET exclude_shorts = ?", (value,))
            conn.commit()

    def _run(self, db_path: Path):
        return run_discovery(
            db_path,
            project_name="proj",
            llm=self._orphan_llm(),
            model="stub",
            prompt_version="stub-v0",
        )

    def _audit(self, db_path: Path, run_id: int):
        with connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT n_shorts_excluded, n_orphaned_wrong_marks, "
                "n_orphaned_renames FROM discovery_runs WHERE id = ?",
                (run_id,),
            ).fetchone()

    def test_orphan_counts_populated_and_curation_rows_survive(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            # Run 1 with the filter OFF: both videos present, both topics get
            # evidence; then the user curates against the short.
            self._seed(db_path, exclude_shorts=0)
            run1 = self._run(db_path)
            audit1 = self._audit(db_path, run1)
            self.assertEqual(audit1["n_shorts_excluded"], 0)
            self.assertIsNone(audit1["n_orphaned_wrong_marks"])
            self.assertIsNone(audit1["n_orphaned_renames"])

            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Long Topic",
                youtube_video_id="vidS",
            )
            rename_topic(
                db_path,
                project_name="proj",
                current_name="Shorts-Only Topic",
                new_name="Shorts-Only Topic (renamed)",
            )

            # Run 2 with the filter ON: vidS (60s) is dropped → its wrong-mark
            # and the rename whose target now has no kept evidence are counted.
            self._set_exclude_shorts(db_path, 1)
            run2 = self._run(db_path)
            audit2 = self._audit(db_path, run2)
            self.assertEqual(audit2["n_shorts_excluded"], 1)
            self.assertEqual(audit2["n_orphaned_wrong_marks"], 1)
            self.assertEqual(audit2["n_orphaned_renames"], 1)

            # The curation rows themselves are never deleted by the filter.
            with connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM wrong_assignments").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM topic_renames").fetchone()[0],
                    1,
                )

            # Run 3 with the filter OFF again: orphan counts go back to NULL and
            # the woken-back-up wrong-mark suppresses vidS's re-created topic
            # assignment.
            self._set_exclude_shorts(db_path, 0)
            run3 = self._run(db_path)
            audit3 = self._audit(db_path, run3)
            self.assertEqual(audit3["n_shorts_excluded"], 0)
            self.assertIsNone(audit3["n_orphaned_wrong_marks"])
            self.assertIsNone(audit3["n_orphaned_renames"])
            with connect(db_path) as conn:
                kept = {
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT v.youtube_video_id FROM video_topics vt "
                        "JOIN videos v ON v.id = vt.video_id "
                        "JOIN topics t ON t.id = vt.topic_id "
                        "WHERE vt.discovery_run_id = ? AND t.name = 'Long Topic'",
                        (run3,),
                    )
                }
            self.assertEqual(kept, {"vid1"})  # vidS suppressed by the wrong-mark

    def test_discovery_payload_carries_audit_counts_and_badge(self) -> None:
        from yt_channel_analyzer.review_ui import _build_discovery_topic_map

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, exclude_shorts=0)
            run1 = self._run(db_path)
            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="Long Topic",
                youtube_video_id="vidS",
            )
            rename_topic(
                db_path,
                project_name="proj",
                current_name="Shorts-Only Topic",
                new_name="Shorts-Only Topic (renamed)",
            )
            self._set_exclude_shorts(db_path, 1)
            run2 = self._run(db_path)

            block2 = _build_discovery_topic_map(db_path, run_id=run2)
            self.assertEqual(block2["n_shorts_excluded"], 1)
            self.assertEqual(block2["n_orphaned_wrong_marks"], 1)
            self.assertEqual(block2["n_orphaned_renames"], 1)
            self.assertEqual(
                block2["shorts_filter_badge"],
                "1 shorts excluded · 2 curation actions inert "
                "(target episodes filtered)",
            )

            # Filter-off run on this same channel: nothing excluded, nothing
            # inert → the badge is suppressed entirely.
            block1 = _build_discovery_topic_map(db_path, run_id=run1)
            self.assertIsNone(block1["shorts_filter_badge"])

    def test_badge_hidden_when_filter_off_and_no_orphans(self) -> None:
        from yt_channel_analyzer.review_ui import build_state_payload

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            self._seed(db_path, exclude_shorts=0)
            run1 = self._run(db_path)
            payload = build_state_payload(db_path, discovery_run_id=run1)
            self.assertIsNone(
                payload["discovery_topic_map"]["shorts_filter_badge"]
            )


class ShortsFilterBadgeHtmlTests(unittest.TestCase):
    def test_ui_revision_advances_for_shorts_filter(self) -> None:
        from yt_channel_analyzer.review_ui import UI_REVISION

        self.assertIn("shorts-filter", UI_REVISION)
        # Earlier UI_REVISION substrings preserved.
        self.assertIn("channel-overview", UI_REVISION)
        self.assertIn("discovery", UI_REVISION)

    def test_html_page_renders_shorts_badge(self) -> None:
        from yt_channel_analyzer.review_ui import HTML_PAGE

        self.assertIn("discovery-shorts-badge", HTML_PAGE)
        self.assertIn("shorts_filter_badge", HTML_PAGE)


class DiscoveryTaxonomyAwarenessRenderTests(_RegistryIsolation):
    def test_render_includes_curated_taxonomy_block_when_present(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt

        prompt = register_discovery_prompt()
        rendered = prompt.render(
            {
                "videos": [
                    {
                        "youtube_video_id": "vidA",
                        "title": "Sleep and the brain",
                        "description": None,
                        "chapters": [],
                    },
                ],
                "taxonomy": [
                    {"topic": "Wellbeing", "subtopics": ["Sleep", "Stress"]},
                    {"topic": "Career", "subtopics": []},
                ],
            }
        )
        self.assertIn("Taxonomy already curated", rendered)
        self.assertIn("Wellbeing", rendered)
        self.assertIn("Sleep", rendered)
        self.assertIn("Stress", rendered)
        self.assertIn("Career", rendered)

    def test_render_omits_taxonomy_block_when_absent_or_empty(self) -> None:
        from yt_channel_analyzer.discovery import register_discovery_prompt

        prompt = register_discovery_prompt()
        base_video = {
            "youtube_video_id": "vidA",
            "title": "Sleep and the brain",
            "description": None,
            "chapters": [],
        }
        for context in ({"videos": [base_video]}, {"videos": [base_video], "taxonomy": []}):
            rendered = prompt.render(context)
            self.assertNotIn("Taxonomy already curated", rendered)


class DiscoveryRerunTaxonomyAwarenessTests(unittest.TestCase):
    def test_rerun_passes_current_curated_taxonomy_to_llm(self) -> None:
        from yt_channel_analyzer.discovery import STUB_SUBTOPIC_NAME, STUB_TOPIC_NAME

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_llm,
                model="stub",
                prompt_version="stub-v0",
            )

            seen: dict[str, object] = {}

            def spy_llm(videos, *, correlation_id=None, taxonomy=None):
                seen["taxonomy"] = taxonomy
                return stub_llm(videos, correlation_id=correlation_id)

            run_discovery(
                db_path,
                project_name="proj",
                llm=spy_llm,
                model="stub",
                prompt_version="stub-v0",
            )

        taxonomy = seen["taxonomy"]
        self.assertIsNotNone(taxonomy)
        topics_seen = {entry["topic"] for entry in taxonomy}
        self.assertIn(STUB_TOPIC_NAME, topics_seen)
        wellbeing = next(e for e in taxonomy if e["topic"] == STUB_TOPIC_NAME)
        self.assertIn(STUB_SUBTOPIC_NAME, wellbeing["subtopics"])

    def test_first_run_passes_empty_taxonomy(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)

            seen: dict[str, object] = {}

            def spy_llm(videos, *, correlation_id=None, taxonomy=None):
                seen["taxonomy"] = taxonomy
                return stub_llm(videos, correlation_id=correlation_id)

            run_discovery(
                db_path,
                project_name="proj",
                llm=spy_llm,
                model="stub",
                prompt_version="stub-v0",
            )
        self.assertEqual(seen["taxonomy"], [])


class DiscoveryNeverDowngradeRefineTests(unittest.TestCase):
    def _ids(self, conn, *, yt_id, topic_name):
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT vt.video_id, vt.topic_id
            FROM video_topics vt
            JOIN videos v ON v.id = vt.video_id
            JOIN topics t ON t.id = vt.topic_id
            WHERE v.youtube_video_id = ? AND t.name = ?
            """,
            (yt_id, topic_name),
        ).fetchone()

    def _stamp_source(self, db_path, *, yt_id, topic_name, source, confidence, reason):
        with connect(db_path) as conn:
            row = self._ids(conn, yt_id=yt_id, topic_name=topic_name)
            conn.execute(
                """
                UPDATE video_topics SET assignment_source = ?, confidence = ?, reason = ?
                WHERE video_id = ? AND topic_id = ?
                """,
                (source, confidence, reason, row["video_id"], row["topic_id"]),
            )
            # Mirror onto the subtopic row stub_llm wrote (vid1 -> General/General sub).
            sub_row = conn.execute(
                """
                SELECT vs.video_id, vs.subtopic_id
                FROM video_subtopics vs
                JOIN videos v ON v.id = vs.video_id
                JOIN subtopics s ON s.id = vs.subtopic_id
                WHERE v.youtube_video_id = ? AND s.topic_id = ?
                """,
                (yt_id, row["topic_id"]),
            ).fetchone()
            if sub_row is not None:
                conn.execute(
                    """
                    UPDATE video_subtopics SET assignment_source = ?, confidence = ?, reason = ?
                    WHERE video_id = ? AND subtopic_id = ?
                    """,
                    (source, confidence, reason, sub_row[0], sub_row[1]),
                )
            conn.commit()

    def _read_source(self, db_path, *, yt_id, topic_name):
        with connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """
                SELECT vt.assignment_source AS source, vt.confidence AS confidence,
                       vt.reason AS reason
                FROM video_topics vt
                JOIN videos v ON v.id = vt.video_id
                JOIN topics t ON t.id = vt.topic_id
                WHERE v.youtube_video_id = ? AND t.name = ?
                """,
                (yt_id, topic_name),
            ).fetchone()

    def _rerun(self, db_path):
        run_discovery(
            db_path,
            project_name="proj",
            llm=stub_llm,
            model="stub",
            prompt_version="stub-v0",
        )

    def test_refine_row_keeps_source_confidence_reason_after_rerun(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            self._rerun(db_path)
            self._stamp_source(
                db_path,
                yt_id="vid1",
                topic_name="General",
                source="refine",
                confidence=0.95,
                reason="transcript-grade reassignment",
            )
            # stub_llm re-proposes (vid1, General) with confidence 1.0 / "stub assignment".
            self._rerun(db_path)
            row = self._read_source(db_path, yt_id="vid1", topic_name="General")
            self.assertEqual(row["source"], "refine")
            self.assertAlmostEqual(row["confidence"], 0.95)
            self.assertEqual(row["reason"], "transcript-grade reassignment")

            with connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                sub = conn.execute(
                    """
                    SELECT vs.assignment_source AS source, vs.confidence AS confidence
                    FROM video_subtopics vs
                    JOIN videos v ON v.id = vs.video_id
                    JOIN subtopics s ON s.id = vs.subtopic_id
                    JOIN topics t ON t.id = s.topic_id
                    WHERE v.youtube_video_id = 'vid1' AND t.name = 'General'
                    """
                ).fetchone()
            self.assertEqual(sub["source"], "refine")
            self.assertAlmostEqual(sub["confidence"], 0.95)

    def test_manual_row_keeps_source_confidence_reason_after_rerun(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            self._rerun(db_path)
            self._stamp_source(
                db_path,
                yt_id="vid1",
                topic_name="General",
                source="manual",
                confidence=0.5,
                reason="operator override",
            )
            self._rerun(db_path)
            row = self._read_source(db_path, yt_id="vid1", topic_name="General")
            self.assertEqual(row["source"], "manual")
            self.assertAlmostEqual(row["confidence"], 0.5)
            self.assertEqual(row["reason"], "operator override")

    def test_rerun_still_inserts_auto_row_for_new_pair(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            self._rerun(db_path)
            self._stamp_source(
                db_path,
                yt_id="vid1",
                topic_name="General",
                source="refine",
                confidence=0.95,
                reason="transcript-grade reassignment",
            )

            def stub_with_new_topic(videos, *, correlation_id=None, taxonomy=None):
                payload = stub_llm(videos, correlation_id=correlation_id)
                return DiscoveryPayload(
                    topics=[*payload.topics, "FreshTopic"],
                    subtopics=payload.subtopics,
                    assignments=[
                        *payload.assignments,
                        DiscoveryAssignment(
                            youtube_video_id="vid2",
                            topic_name="FreshTopic",
                            confidence=0.7,
                            reason="brand new theme",
                        ),
                    ],
                )

            run_discovery(
                db_path,
                project_name="proj",
                llm=stub_with_new_topic,
                model="stub",
                prompt_version="stub-v0",
            )
            fresh = self._read_source(db_path, yt_id="vid2", topic_name="FreshTopic")
            self.assertEqual(fresh["source"], "auto")
            # The refine row is still protected alongside.
            kept = self._read_source(db_path, yt_id="vid1", topic_name="General")
            self.assertEqual(kept["source"], "refine")

    def test_wrong_mark_still_suppresses_refine_row_on_rerun(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            self._rerun(db_path)
            self._stamp_source(
                db_path,
                yt_id="vid1",
                topic_name="General",
                source="refine",
                confidence=0.95,
                reason="transcript-grade reassignment",
            )
            mark_assignment_wrong(
                db_path,
                project_name="proj",
                topic_name="General",
                youtube_video_id="vid1",
            )
            self._rerun(db_path)
            self.assertIsNone(
                self._read_source(db_path, yt_id="vid1", topic_name="General")
            )


class RefineSampleEndpointTests(unittest.TestCase):
    def _call_app(self, app, method: str, path: str) -> tuple[str, str]:
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": "0",
            "wsgi.input": io.BytesIO(b""),
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

    def test_sample_endpoint_returns_picked_episodes(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(app, "GET", "/api/refine/sample")
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertIsNotNone(payload["discovery_run_id"])
            self.assertEqual(payload["pool_size"], 2)
            yt_ids = {e["youtube_video_id"] for e in payload["episodes"]}
            self.assertEqual(yt_ids, {"vid1", "vid2"})
            for episode in payload["episodes"]:
                self.assertIn(episode["slot_kind"], {"coverage", "blind_spot"})
                self.assertIn("topic", episode)
                self.assertIn("title", episode)
                self.assertIn("transcript_status", episode)

    def test_sample_endpoint_errors_when_no_discovery_run(self) -> None:
        from yt_channel_analyzer.review_ui import ReviewUIApp

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            app = ReviewUIApp(db_path)
            status, body = self._call_app(app, "GET", "/api/refine/sample")
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("discover", json.loads(body)["error"].lower())


class RefineFetchTranscriptsEndpointTests(unittest.TestCase):
    def _call_app(
        self, app, method: str, path: str, body: dict | None = None
    ) -> tuple[str, str]:
        raw = json.dumps(body or {}).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body_bytes = b"".join(app(environ, start_response))
        return str(captured["status"]), body_bytes.decode("utf-8")

    def _app(self, db_path: Path, **kwargs):
        from yt_channel_analyzer.review_ui import ReviewUIApp

        return ReviewUIApp(db_path, transcript_fetch_request_interval=0.0, **kwargs)

    def test_fetch_transcripts_updates_status_and_estimates_cost(self) -> None:
        from yt_channel_analyzer.youtube import stub_transcript_fetcher

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            app = self._app(db_path, transcript_fetcher=stub_transcript_fetcher)
            status, body = self._call_app(
                app, "POST", "/api/refine/fetch-transcripts",
                {"video_ids": ["vid1", "vid2", "vid1"]},
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(
                {e["youtube_video_id"] for e in payload["episodes"]},
                {"vid1", "vid2"},
            )
            self.assertTrue(all(e["available"] for e in payload["episodes"]))
            self.assertEqual(payload["n_available"], 2)
            self.assertEqual(payload["dropped"], [])
            self.assertGreater(payload["estimated_cost_usd"], 0.0)

    def test_fetch_transcripts_drops_unavailable_and_zero_cost(self) -> None:
        from yt_channel_analyzer.youtube import TranscriptRecord

        def dead_fetcher(video_id: str) -> TranscriptRecord:
            return TranscriptRecord(
                status="not_found", source=None, language_code=None, text=None
            )

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            app = self._app(db_path, transcript_fetcher=dead_fetcher)
            status, body = self._call_app(
                app, "POST", "/api/refine/fetch-transcripts",
                {"video_ids": ["vid1", "vid2"]},
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertFalse(any(e["available"] for e in payload["episodes"]))
            self.assertEqual(payload["n_available"], 0)
            self.assertEqual(sorted(payload["dropped"]), ["vid1", "vid2"])
            self.assertEqual(payload["estimated_cost_usd"], 0.0)

    def test_fetch_transcripts_rejects_foreign_video_id(self) -> None:
        from yt_channel_analyzer.youtube import stub_transcript_fetcher

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            app = self._app(db_path, transcript_fetcher=stub_transcript_fetcher)
            status, body = self._call_app(
                app, "POST", "/api/refine/fetch-transcripts",
                {"video_ids": ["vid1", "not-a-channel-video"]},
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("primary channel", json.loads(body)["error"])

    def test_fetch_transcripts_requires_video_ids(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            _seed_channel_with_videos(db_path)
            app = self._app(db_path)
            status, body = self._call_app(
                app, "POST", "/api/refine/fetch-transcripts", {"video_ids": []}
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("video_ids", json.loads(body)["error"])


class RefineRunEndpointTests(unittest.TestCase):
    def _call_app(
        self, app, method: str, path: str, body: dict | None = None
    ) -> tuple[str, str]:
        raw = json.dumps(body or {}).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
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

    def _app(self, db_path: Path, **kwargs):
        from yt_channel_analyzer.review_ui import ReviewUIApp
        from yt_channel_analyzer.youtube import stub_transcript_fetcher

        return ReviewUIApp(
            db_path,
            transcript_fetcher=kwargs.pop("transcript_fetcher", stub_transcript_fetcher),
            transcript_fetch_request_interval=0.0,
            run_in_background=False,
            **kwargs,
        )

    def test_refine_run_creates_run_and_status_reports_it(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)
            app = self._app(db_path)
            status, body = self._call_app(app, "POST", "/api/refine", {"mode": "stub"})
            self.assertEqual(status, "200 OK")
            run_id = json.loads(body)["refinement_run_id"]
            self.assertIsInstance(run_id, int)

            status, body = self._call_app(app, "GET", f"/api/refine/status/{run_id}")
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["id"], run_id)
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["n_sample"], 2)
            # stub LLM emits one subtopic proposal per episode + one topic
            # proposal for the first episode.
            self.assertEqual(payload["n_proposals"], 3)
            self.assertIsNotNone(payload["discovery_run_id"])
            self.assertNotIn("error", payload)

    def test_refine_run_video_ids_override_picks_only_those(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)
            app = self._app(db_path)
            status, body = self._call_app(
                app, "POST", "/api/refine", {"mode": "stub", "video_ids": ["vid1"]}
            )
            self.assertEqual(status, "200 OK")
            run_id = json.loads(body)["refinement_run_id"]

            _, body = self._call_app(app, "GET", f"/api/refine/status/{run_id}")
            payload = json.loads(body)
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["n_sample"], 1)
            self.assertEqual(payload["n_proposals"], 2)

    def test_refine_run_rejects_unknown_mode(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)
            app = self._app(db_path)
            status, body = self._call_app(
                app, "POST", "/api/refine", {"mode": "bogus"}
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("mode", json.loads(body)["error"])

    def test_refine_run_real_mode_requires_env_gate(self) -> None:
        import os as _os

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)
            app = self._app(db_path)
            saved = _os.environ.pop("RALPH_ALLOW_REAL_LLM", None)
            try:
                status, body = self._call_app(
                    app, "POST", "/api/refine", {"mode": "real"}
                )
            finally:
                if saved is not None:
                    _os.environ["RALPH_ALLOW_REAL_LLM"] = saved
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("RALPH_ALLOW_REAL_LLM", json.loads(body)["error"])

    def test_refine_run_status_unknown_id_is_400(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            self._seed_run(db_path)
            app = self._app(db_path)
            status, body = self._call_app(app, "GET", "/api/refine/status/9999")
            self.assertEqual(status, "400 Bad Request")
            self.assertIn("not found", json.loads(body)["error"].lower())


if __name__ == "__main__":
    unittest.main()
