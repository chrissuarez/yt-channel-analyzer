from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from yt_channel_analyzer.cli import main
from yt_channel_analyzer.legacy.comparison_group_suggestions import (
    SuggestedComparisonGroupLabel,
    VideoComparisonGroupSuggestion,
    _build_prompt as _build_comparison_group_prompt,
    _response_schema as _comparison_group_response_schema,
    suggest_comparison_groups_for_video,
)
from yt_channel_analyzer.db import (
    add_video_to_comparison_group,
    approve_comparison_group_suggestion_label,
    approve_subtopic_suggestion_label,
    approve_topic_suggestion_label,
    apply_topic_suggestion_to_video,
    bulk_apply_topic_suggestion_label,
    connect,
    create_comparison_group,
    create_comparison_group_suggestion_run,
    create_subtopic,
    create_subtopic_suggestion_run,
    create_topic,
    create_topic_suggestion_run,
    ensure_schema,
    assign_subtopic_to_video,
    assign_topic_to_video,
    get_group_transcript_statuses,
    get_comparison_group_suggestion_review_rows,
    get_latest_topic_suggestion_run_id,
    get_subtopic_suggestion_review_rows,
    get_topic_suggestion_review_rows,
    get_video_subtopic_assignments,
    get_video_topic_assignments,
    init_db,
    list_approved_comparison_groups_for_subtopic,
    list_comparison_groups,
    list_group_videos,
    list_subtopics,
    list_topics,
    list_topic_suggestion_runs,
    list_video_comparison_group_suggestions,
    list_video_subtopic_suggestions,
    list_video_topic_suggestions,
    reject_comparison_group_suggestion_label,
    reject_subtopic_suggestion_label,
    reject_topic_suggestion_label,
    remove_video_from_comparison_group,
    rename_comparison_group,
    rename_comparison_group_suggestion_label,
    rename_subtopic,
    rename_subtopic_suggestion_label,
    rename_topic,
    resolve_comparison_group,
    store_video_comparison_group_suggestion,
    store_video_subtopic_suggestion,
    store_video_topic_suggestion,
    summarize_comparison_group_suggestion_labels,
    summarize_subtopic_suggestion_labels,
    summarize_topic_suggestion_labels,
    supersede_stale_topic_suggestions,
    upsert_video_transcript,
    upsert_videos_for_primary_channel,
)
import sys
import types
from unittest.mock import patch

from yt_channel_analyzer.subtopic_suggestions import (
    _build_prompt as _build_subtopic_prompt,
    _response_schema as _subtopic_response_schema,
    SuggestedSubtopicLabel,
    VideoSubtopicSuggestion,
    suggest_subtopics_for_video,
)
from yt_channel_analyzer.topic_suggestions import (
    SuggestedTopicLabel,
    VideoTopicSuggestion,
    _build_prompt,
    _canonicalize_topic_label,
    _resolve_reusable_label,
    _response_schema,
    suggest_topics_for_video,
)
from yt_channel_analyzer.youtube import (
    TranscriptRecord,
    VideoMetadata,
    _classify_transcript_exception,
    _default_transcript_fetcher,
    _safe_exception_detail,
)


class FakeNoTranscriptFound(Exception):
    pass


class FakeTranscriptsDisabled(Exception):
    pass


class FakeVideoUnavailable(Exception):
    pass


class TranscriptClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.known_errors = {
            "NoTranscriptFound": FakeNoTranscriptFound,
            "TranscriptsDisabled": FakeTranscriptsDisabled,
            "VideoUnavailable": FakeVideoUnavailable,
        }

    def test_classifies_known_transcript_errors(self) -> None:
        self.assertEqual(
            _classify_transcript_exception(FakeTranscriptsDisabled("disabled"), self.known_errors),
            "disabled",
        )
        self.assertEqual(
            _classify_transcript_exception(FakeNoTranscriptFound("none"), self.known_errors),
            "not_found",
        )
        self.assertEqual(
            _classify_transcript_exception(FakeVideoUnavailable("gone"), self.known_errors),
            "unavailable",
        )

    def test_classifies_rate_limited_errors(self) -> None:
        exc = RuntimeError("Too many requests from YouTube")
        self.assertEqual(_classify_transcript_exception(exc, self.known_errors), "rate_limited")

    def test_classifies_request_failures(self) -> None:
        exc = ConnectionError("HTTP connection timed out")
        self.assertEqual(_classify_transcript_exception(exc, self.known_errors), "request_failed")

    def test_falls_back_to_error_for_unknown_exceptions(self) -> None:
        exc = RuntimeError("completely unexpected")
        self.assertEqual(_classify_transcript_exception(exc, self.known_errors), "error")

    def test_safe_exception_detail_normalizes_and_truncates(self) -> None:
        raw = " token=secret\n\nToo many requests   from upstream " + ("x" * 400)
        detail = _safe_exception_detail(RuntimeError(raw))
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertNotIn("\n", detail)
        self.assertLessEqual(len(detail), 300)


class DefaultTranscriptFetcherCompatibilityTests(unittest.TestCase):
    def test_uses_installed_api_list_shape_and_prefers_manual_then_generated(self) -> None:
        class FakeNoTranscriptFound(Exception):
            pass

        class FakeFetchedTranscriptSnippet:
            def __init__(self, text: str) -> None:
                self.text = text

        class FakeTranscript:
            def __init__(self, *, language_code: str, is_generated: bool, segments: list[object]) -> None:
                self.language_code = language_code
                self.is_generated = is_generated
                self._segments = segments

            def fetch(self) -> list[object]:
                return self._segments

        class FakeTranscriptList:
            def __init__(self) -> None:
                self.manual = FakeTranscript(
                    language_code="en",
                    is_generated=False,
                    segments=[FakeFetchedTranscriptSnippet("Manual"), FakeFetchedTranscriptSnippet("captions")],
                )
                self.generated = FakeTranscript(
                    language_code="en",
                    is_generated=True,
                    segments=[{"text": "Generated"}, {"text": "captions"}],
                )

            def find_manually_created_transcript(self, languages: list[str]) -> FakeTranscript:
                return self.manual

            def find_generated_transcript(self, languages: list[str]) -> FakeTranscript:
                return self.generated

            def __iter__(self):
                yield self.manual
                yield self.generated

        class FakeYouTubeTranscriptApi:
            def list(self, video_id: str) -> FakeTranscriptList:
                self.video_id = video_id
                return FakeTranscriptList()

        fake_module = types.ModuleType("youtube_transcript_api")
        fake_module.NoTranscriptFound = FakeNoTranscriptFound
        fake_module.TranscriptsDisabled = type("FakeTranscriptsDisabled", (Exception,), {})
        fake_module.VideoUnavailable = type("FakeVideoUnavailable", (Exception,), {})
        fake_module.YouTubeTranscriptApi = FakeYouTubeTranscriptApi

        with patch.dict(sys.modules, {"youtube_transcript_api": fake_module}):
            fetcher = _default_transcript_fetcher()
            transcript = fetcher("abc123")

        self.assertEqual(transcript.status, "available")
        self.assertEqual(transcript.source, "manual")
        self.assertEqual(transcript.language_code, "en")
        self.assertEqual(transcript.text, "Manual captions")


class TopicSuggestionPromptTests(unittest.TestCase):
    def test_prompt_tightens_secondary_and_vague_label_reuse_rules(self) -> None:
        prompt = _build_prompt(
            project_name="Diary of a CEO",
            approved_topic_names=["Evergreen", "Health & Wellness", "Longevity & Toxins"],
            video_title="Why Ultra-Processed Food Wrecks Metabolism",
            video_description="A discussion of metabolic dysfunction, insulin resistance, and diet.",
        )

        self.assertIn("Return exactly one primary topic and zero or one secondary topic.", prompt)
        self.assertIn("secondary topic is optional and should usually be empty", prompt)
        self.assertIn("Strongly disfavour reusing vague or catch-all labels such as 'Evergreen'", prompt)
        self.assertIn("introduce a new concrete broad-topic label instead", prompt)
        self.assertIn("prefer the broader reusable label Health & Wellness", prompt)
        self.assertIn("A secondary topic should be rare", prompt)

    def test_response_schema_allows_at_most_one_secondary_topic(self) -> None:
        schema = _response_schema()
        secondary = schema["properties"]["secondary_topics"]
        self.assertEqual(secondary["maxItems"], 1)

    def test_resolve_reusable_label_collapses_health_family_variants(self) -> None:
        resolved, reused = _resolve_reusable_label("Health Science", ["Health & Wellness", "Politics"])

        self.assertEqual(resolved, "Health & Wellness")
        self.assertTrue(reused)

    def test_resolve_reusable_label_broadens_subtopic_like_variants(self) -> None:
        resolved, reused = _resolve_reusable_label("Human Behavior", ["Health & Wellness"])

        self.assertEqual(resolved, "Psychology")
        self.assertFalse(reused)

    def test_canonicalize_topic_label_normalizes_basic_variants(self) -> None:
        self.assertEqual(_canonicalize_topic_label("Health & Wellness"), _canonicalize_topic_label("health and wellness"))

    def test_suggest_topics_deduplicates_and_caps_secondary_topics(self) -> None:
        class FakeResponse:
            output_text = json.dumps(
                {
                    "primary_topic": {
                        "label": "Health Science",
                        "assignment_type": "primary",
                        "reuse_existing": False,
                        "rationale": "The title and description are centrally about metabolism.",
                    },
                    "secondary_topics": [
                        {
                            "label": "health and wellness",
                            "assignment_type": "secondary",
                            "reuse_existing": False,
                            "rationale": "Broadly related, but still central.",
                        },
                        {
                            "label": "Longevity & Toxins",
                            "assignment_type": "secondary",
                            "reuse_existing": True,
                            "rationale": "Also mentioned.",
                        },
                    ],
                }
            )

        class FakeResponses:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClient:
            responses = FakeResponses()

        with patch("yt_channel_analyzer.topic_suggestions._get_openai_client", return_value=FakeClient()):
            suggestion = suggest_topics_for_video(
                project_name="Diary of a CEO",
                approved_topic_names=["Health & Wellness", "Longevity & Toxins"],
                youtube_video_id="abc123",
                video_title="Why Ultra-Processed Food Wrecks Metabolism",
                video_description="A discussion of metabolic dysfunction, insulin resistance, and diet.",
            )

        self.assertEqual(suggestion.primary_topic.label, "Health & Wellness")
        self.assertEqual(len(suggestion.secondary_topics), 1)
        self.assertEqual(suggestion.secondary_topics[0].label, "Longevity & Toxins")


class SubtopicSuggestionPromptTests(unittest.TestCase):
    def test_prompt_prefers_reuse_within_existing_broad_topic(self) -> None:
        prompt = _build_subtopic_prompt(
            project_name="Diary of a CEO",
            broad_topic_name="Health & Wellness",
            approved_subtopics=[
                {"name": "Sleep", "description": "Sleep quality and habits"},
                {"name": "Recovery", "description": "Recovery protocols"},
            ],
            video_title="Why Sleep Debt Crushes Recovery",
            video_description="A discussion of sleep debt, rest, and physical recovery.",
        )

        self.assertIn("Stay inside the given broad topic. Do not suggest a different broad topic.", prompt)
        self.assertIn("Prefer reusing an existing approved subtopic when it is a strong, natural fit", prompt)
        self.assertIn("Return exactly one primary subtopic and no secondary subtopics.", prompt)

    def test_response_schema_allows_only_primary_subtopic(self) -> None:
        schema = _subtopic_response_schema()
        primary = schema["properties"]["primary_subtopic"]
        self.assertEqual(primary["properties"]["assignment_type"]["enum"], ["primary"])

    def test_suggest_subtopics_returns_one_primary_subtopic(self) -> None:
        class FakeResponse:
            output_text = json.dumps(
                {
                    "primary_subtopic": {
                        "label": "Sleep",
                        "assignment_type": "primary",
                        "reuse_existing": True,
                        "rationale": "The title and description are centrally about sleep.",
                    }
                }
            )

        class FakeResponses:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClient:
            responses = FakeResponses()

        with patch("yt_channel_analyzer.subtopic_suggestions._get_openai_client", return_value=FakeClient()):
            suggestion = suggest_subtopics_for_video(
                project_name="Diary of a CEO",
                broad_topic_name="Health & Wellness",
                approved_subtopics=[{"name": "Sleep", "description": "Sleep quality and habits"}],
                youtube_video_id="abc123",
                video_title="Why Sleep Debt Crushes Recovery",
                video_description="A discussion of sleep debt and rest.",
            )

        self.assertEqual(suggestion.primary_subtopic.label, "Sleep")
        self.assertTrue(suggestion.primary_subtopic.reuse_existing)


class TranscriptPersistenceTests(unittest.TestCase):
    def test_persists_transcript_detail_for_group_statuses(self) -> None:
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
                        youtube_video_id="vid123",
                        title="Video",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Topic")
            create_subtopic(db_path, topic_name="Topic", subtopic_name="Subtopic")
            create_comparison_group(db_path, subtopic_name="Subtopic", group_name="Group")
            add_video_to_comparison_group(db_path, video_id="vid123", group_name="Group")

            upsert_video_transcript(
                db_path,
                youtube_video_id="vid123",
                transcript=TranscriptRecord(
                    status="request_failed",
                    source=None,
                    language_code=None,
                    text=None,
                    detail="HTTP connection timed out",
                ),
            )

            group = resolve_comparison_group(db_path, group_name="Group")
            rows = get_group_transcript_statuses(db_path, group_id=group["id"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["transcript_status"], "request_failed")
            self.assertEqual(rows[0]["transcript_detail"], "HTTP connection timed out")

    def test_schema_repair_adds_new_transcript_columns_and_statuses(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "repair.sqlite3"
            with connect(db_path) as connection:
                connection.executescript(
                    """
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE projects (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        slug TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
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
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        UNIQUE(project_id, youtube_channel_id)
                    );
                    CREATE TABLE videos (
                        id INTEGER PRIMARY KEY,
                        channel_id INTEGER NOT NULL,
                        youtube_video_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        published_at TEXT,
                        description TEXT,
                        thumbnail_url TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                        UNIQUE(channel_id, youtube_video_id)
                    );
                    CREATE TABLE video_transcripts (
                        video_id INTEGER PRIMARY KEY,
                        transcript_status TEXT NOT NULL,
                        transcript_source TEXT,
                        language_code TEXT,
                        transcript_text TEXT,
                        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                        CHECK (transcript_status IN ('available', 'unavailable', 'disabled', 'not_found', 'error')),
                        CHECK (transcript_source IN ('manual', 'generated') OR transcript_source IS NULL)
                    );
                    """
                )
                ensure_schema(connection)
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(video_transcripts)").fetchall()
                }
                self.assertIn("transcript_detail", columns)
                project_id = connection.execute(
                    "INSERT INTO projects(name) VALUES ('proj') RETURNING id"
                ).fetchone()[0]
                channel_id = connection.execute(
                    "INSERT INTO channels(project_id, youtube_channel_id, title, is_primary) VALUES (?, 'UC1', 'Channel', 1) RETURNING id",
                    (project_id,),
                ).fetchone()[0]
                video_id = connection.execute(
                    "INSERT INTO videos(channel_id, youtube_video_id, title) VALUES (?, 'vid1', 'Video') RETURNING id",
                    (channel_id,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO video_transcripts(video_id, transcript_status, transcript_detail) VALUES (?, 'rate_limited', 'Too many requests')",
                    (video_id,),
                )
                connection.commit()


class TopicSuggestionCliRegressionTests(unittest.TestCase):
    def test_summary_matches_list_for_latest_run_pending_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-suggestions.sqlite3"
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
                        youtube_video_id="vid-old",
                        title="Old Video",
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-new",
                        title="New Video",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            old_run = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=old_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-old",
                    video_title="Old Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Old Label",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Old Label", run_id=old_run)

            latest_run = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=latest_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-new",
                    video_title="New Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="primary",
                    ),
                    secondary_topics=[
                        SuggestedTopicLabel(
                            label="Recovery",
                            assignment_type="secondary",
                            reuse_existing=False,
                            rationale="secondary",
                        )
                    ],
                    raw_response_json="{}",
                ),
            )

            list_rows = list_video_topic_suggestions(db_path, status="pending")
            self.assertEqual([row["youtube_video_id"] for row in list_rows], ["vid-new", "vid-new"])
            self.assertEqual([row["suggested_label"] for row in list_rows], ["Sleep", "Recovery"])
            self.assertTrue(all(row["run_id"] == latest_run for row in list_rows))
            self.assertTrue(all(row["label_status"] == "pending" for row in list_rows))

            summary_rows = summarize_topic_suggestion_labels(db_path, status="pending")
            self.assertEqual([row["name"] for row in summary_rows], ["Recovery", "Sleep"])
            self.assertTrue(all(row["run_id"] == latest_run for row in summary_rows))
            self.assertTrue(all(row["status"] == "pending" for row in summary_rows))
            self.assertEqual(summary_rows[0]["suggestion_count"], 1)
            self.assertEqual(summary_rows[0]["secondary_count"], 1)
            self.assertEqual(summary_rows[1]["suggestion_count"], 1)
            self.assertEqual(summary_rows[1]["primary_count"], 1)


class TopicSuggestionRunWorkflowTests(unittest.TestCase):
    def test_cli_summary_status_filter_matches_visible_pending_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "suggestions-cli.sqlite3"
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
                        youtube_video_id="vid-old",
                        title="Old Video",
                        description="Old desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-new",
                        title="New Video",
                        description="New desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            run1 = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=run1,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-old",
                    video_title="Old Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Archive",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="Old run label.",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Archive", run_id=run1)

            run2 = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=run2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-new",
                    video_title="New Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="Primary pending label.",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            stdout = io.StringIO()
            argv = [
                "yt-channel-analyzer",
                "summarize-topic-suggestion-labels",
                "--db-path",
                str(db_path),
                "--run-id",
                str(run2),
                "--status",
                "pending",
            ]
            with patch.object(sys, "argv", argv):
                with redirect_stdout(stdout):
                    exit_code = main()

            output_lines = stdout.getvalue().strip().splitlines()
            self.assertEqual(exit_code, 0)
            self.assertEqual(output_lines[0], f"Run: {run2}")
            self.assertEqual(len(output_lines), 2)
            self.assertIn("Sleep | status=pending | suggestions=1 | primary=1 | secondary=0", output_lines[1])

    def test_summary_aggregates_same_rows_as_list_for_run_and_status_filters(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "summary-rowset.sqlite3"
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
                        youtube_video_id="vid-old",
                        title="Old Video",
                        description="Old desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-new-1",
                        title="New Video 1",
                        description="New desc 1",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-new-2",
                        title="New Video 2",
                        description="New desc 2",
                        published_at="2026-04-03T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            run1 = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=run1,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-old",
                    video_title="Old Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old run",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Sleep", run_id=run1)

            run2 = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=run2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-new-1",
                    video_title="New Video 1",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="new primary",
                    ),
                    secondary_topics=[
                        SuggestedTopicLabel(
                            label="Recovery",
                            assignment_type="secondary",
                            reuse_existing=False,
                            rationale="new secondary",
                        )
                    ],
                    raw_response_json="{}",
                ),
            )
            store_video_topic_suggestion(
                db_path,
                run_id=run2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-new-2",
                    video_title="New Video 2",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="another new primary",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            list_rows = list_video_topic_suggestions(db_path, status="pending")
            self.assertEqual(len(list_rows), 3)
            self.assertTrue(all(row["run_id"] == run2 for row in list_rows))

            summary_rows = summarize_topic_suggestion_labels(db_path, status="pending")
            self.assertEqual(len(summary_rows), 2)
            self.assertEqual([row["name"] for row in summary_rows], ["Sleep", "Recovery"])
            self.assertTrue(all(row["run_id"] == run2 for row in summary_rows))

            sleep_row = summary_rows[0]
            recovery_row = summary_rows[1]
            self.assertEqual(sleep_row["status"], "pending")
            self.assertEqual(sleep_row["suggestion_count"], 2)
            self.assertEqual(sleep_row["primary_count"], 2)
            self.assertEqual(sleep_row["secondary_count"], 0)
            self.assertEqual(recovery_row["suggestion_count"], 1)
            self.assertEqual(recovery_row["primary_count"], 0)
            self.assertEqual(recovery_row["secondary_count"], 1)

            old_summary_rows = summarize_topic_suggestion_labels(db_path, status="rejected", run_id=run1)
            self.assertEqual(len(old_summary_rows), 1)
            self.assertEqual(old_summary_rows[0]["name"], "Sleep")
            self.assertEqual(old_summary_rows[0]["run_id"], run1)
            self.assertEqual(old_summary_rows[0]["suggestion_count"], 1)

    def test_run_scoped_review_defaults_to_latest_and_keeps_history_queryable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "suggestions.sqlite3"
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
                        youtube_video_id="vid-old",
                        title="Old Video",
                        description="Old desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-new",
                        title="New Video",
                        description="New desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            run1 = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=run1,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-old",
                    video_title="Old Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Metabolism",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="Old run label.",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            run2 = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=run2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-new",
                    video_title="New Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Metabolism",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="New run label.",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            self.assertEqual(get_latest_topic_suggestion_run_id(db_path), run2)
            default_rows = list_video_topic_suggestions(db_path)
            self.assertEqual(len(default_rows), 1)
            self.assertEqual(default_rows[0]["youtube_video_id"], "vid-new")
            old_rows = list_video_topic_suggestions(db_path, run_id=run1)
            self.assertEqual(len(old_rows), 1)
            self.assertEqual(old_rows[0]["youtube_video_id"], "vid-old")

            default_summary = summarize_topic_suggestion_labels(db_path)
            self.assertEqual(len(default_summary), 1)
            self.assertEqual(default_summary[0]["run_id"], run2)
            explicit_old_summary = summarize_topic_suggestion_labels(db_path, run_id=run1)
            self.assertEqual(explicit_old_summary[0]["run_id"], run1)

    def test_bulk_apply_and_supersede_keep_assignments_separate(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bulk.sqlite3"
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
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid2",
                        title="Video 2",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            stale_run = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=stale_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid1",
                    video_title="Video 1",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="stale",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            active_run = create_topic_suggestion_run(db_path, model_name="model-b")
            for video_id in ["vid1", "vid2"]:
                store_video_topic_suggestion(
                    db_path,
                    run_id=active_run,
                    suggestion=VideoTopicSuggestion(
                        youtube_video_id=video_id,
                        video_title=f"Video {video_id[-1]}",
                        primary_topic=SuggestedTopicLabel(
                            label="Sleep",
                            assignment_type="primary",
                            reuse_existing=False,
                            rationale="active",
                        ),
                        secondary_topics=[],
                        raw_response_json="{}",
                    ),
                )

            topic_id = approve_topic_suggestion_label(db_path, suggested_label="Sleep", run_id=active_run)
            self.assertGreater(topic_id, 0)
            matched, applied, skipped = bulk_apply_topic_suggestion_label(
                db_path,
                suggested_label="Sleep",
                run_id=active_run,
            )
            self.assertEqual((matched, applied, skipped), (2, 2, 0))

            for video_id in ["vid1", "vid2"]:
                rows = [row for row in get_video_topic_assignments(db_path, video_id=video_id) if row["topic_name"]]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["topic_name"], "Sleep")
                self.assertEqual(rows[0]["assignment_source"], "suggested")

            summary = supersede_stale_topic_suggestions(db_path, keep_run_id=active_run, suggested_label="Sleep")
            self.assertEqual(summary["superseded"], 1)
            self.assertEqual(summary["matched"], 1)
            self.assertEqual(summary["skipped"], 0)
            stale_rows = summarize_topic_suggestion_labels(db_path, run_id=stale_run)
            self.assertEqual(stale_rows[0]["status"], "superseded")
            current_rows = summarize_topic_suggestion_labels(db_path, run_id=active_run)
            self.assertEqual(current_rows[0]["status"], "approved")


class TopicSuggestionSupersedeCommandTests(unittest.TestCase):
    def test_supersede_stale_topic_suggestions_only_updates_pending_older_runs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "supersede.sqlite3"
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
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid2",
                        title="Video 2",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid3",
                        title="Video 3",
                        description="desc",
                        published_at="2026-04-03T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid4",
                        title="Video 4",
                        description="desc",
                        published_at="2026-04-04T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            stale_run_1 = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=stale_run_1,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid1",
                    video_title="Video 1",
                    primary_topic=SuggestedTopicLabel(
                        label="Old Pending",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old pending",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            store_video_topic_suggestion(
                db_path,
                run_id=stale_run_1,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid2",
                    video_title="Video 2",
                    primary_topic=SuggestedTopicLabel(
                        label="Old Rejected",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old rejected",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Old Rejected", run_id=stale_run_1)

            stale_run_2 = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=stale_run_2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid3",
                    video_title="Video 3",
                    primary_topic=SuggestedTopicLabel(
                        label="Old Approved",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old approved",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            approve_topic_suggestion_label(db_path, suggested_label="Old Approved", run_id=stale_run_2)

            keep_run = create_topic_suggestion_run(db_path, model_name="model-c")
            store_video_topic_suggestion(
                db_path,
                run_id=keep_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid4",
                    video_title="Video 4",
                    primary_topic=SuggestedTopicLabel(
                        label="Current Pending",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="current",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            summary = supersede_stale_topic_suggestions(db_path, keep_run_id=keep_run)
            self.assertEqual(
                summary,
                {
                    "keep_run_id": keep_run,
                    "older_runs_affected": 1,
                    "matched": 3,
                    "superseded": 1,
                    "skipped": 2,
                },
            )

            stale_pending = summarize_topic_suggestion_labels(db_path, run_id=stale_run_1)
            statuses = {row["name"]: row["status"] for row in stale_pending}
            self.assertEqual(statuses["Old Pending"], "superseded")
            self.assertEqual(statuses["Old Rejected"], "rejected")

            stale_approved = summarize_topic_suggestion_labels(db_path, run_id=stale_run_2)
            self.assertEqual(stale_approved[0]["status"], "approved")

            current_rows = summarize_topic_suggestion_labels(db_path, run_id=keep_run)
            self.assertEqual(current_rows[0]["status"], "pending")

            list_rows = list_video_topic_suggestions(db_path, run_id=stale_run_1, status="superseded")
            self.assertEqual(len(list_rows), 1)
            self.assertEqual(list_rows[0]["suggested_label"], "Old Pending")

            review_rows = get_topic_suggestion_review_rows(db_path, run_id=stale_run_1, status="pending")
            self.assertEqual(review_rows, [])

            rerun_summary = supersede_stale_topic_suggestions(db_path, keep_run_id=keep_run)
            self.assertEqual(rerun_summary["superseded"], 0)
            self.assertEqual(rerun_summary["matched"], 3)
            self.assertEqual(rerun_summary["skipped"], 3)

    def test_supersede_stale_topic_suggestions_cli_reports_summary(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "supersede-cli.sqlite3"
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
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid2",
                        title="Video 2",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )
            stale_run = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=stale_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid1",
                    video_title="Video 1",
                    primary_topic=SuggestedTopicLabel(
                        label="Stale Label",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="stale",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            keep_run = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=keep_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid2",
                    video_title="Video 2",
                    primary_topic=SuggestedTopicLabel(
                        label="Fresh Label",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="fresh",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            stdout = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "yt-channel-analyzer",
                    "supersede-stale-topic-suggestions",
                    "--db-path",
                    str(db_path),
                    "--keep-run-id",
                    str(keep_run),
                ],
            ), redirect_stdout(stdout):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertIn(f"Run kept active: {keep_run}", stdout.getvalue())
            self.assertIn("older runs affected: 1", stdout.getvalue())
            self.assertIn("matched: 1", stdout.getvalue())
            self.assertIn("superseded: 1", stdout.getvalue())
            self.assertIn("skipped: 0", stdout.getvalue())


class TopicSuggestionReviewCommandTests(unittest.TestCase):
    def test_review_rows_group_pending_labels_with_samples_and_existing_topic_flag(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-rows.sqlite3"
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
                        title="How sleep affects focus",
                        description="desc",
                        published_at="2026-04-03T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid2",
                        title="Best recovery habits",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid3",
                        title="Sleep routine mistakes",
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Sleep")
            run_id = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=run_id,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid1",
                    video_title="How sleep affects focus",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=True,
                        rationale="clear sleep topic",
                    ),
                    secondary_topics=[
                        SuggestedTopicLabel(
                            label="Recovery",
                            assignment_type="secondary",
                            reuse_existing=False,
                            rationale="clear recovery overlap",
                        )
                    ],
                    raw_response_json="{}",
                ),
            )
            store_video_topic_suggestion(
                db_path,
                run_id=run_id,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid3",
                    video_title="Sleep routine mistakes",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=True,
                        rationale="another sleep fit",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            store_video_topic_suggestion(
                db_path,
                run_id=run_id,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid2",
                    video_title="Best recovery habits",
                    primary_topic=SuggestedTopicLabel(
                        label="Recovery",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="recovery-led video",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            rows = get_topic_suggestion_review_rows(db_path, run_id=run_id, sample_limit=2)
            self.assertEqual([row["name"] for row in rows], ["Recovery", "Recovery", "Sleep", "Sleep"])
            self.assertEqual(rows[0]["video_count"], 2)
            self.assertEqual(rows[0]["approved_topic_exists"], 0)
            self.assertEqual(rows[2]["video_count"], 2)
            self.assertEqual(rows[2]["approved_topic_exists"], 1)
            self.assertEqual(
                [row["video_title"] for row in rows[:2]],
                ["Best recovery habits", "How sleep affects focus"],
            )
            self.assertEqual(
                [row["video_title"] for row in rows[2:]],
                ["How sleep affects focus", "Sleep routine mistakes"],
            )

    def test_review_topic_suggestions_cli_defaults_to_latest_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-cli.sqlite3"
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
                        youtube_video_id="vid-old",
                        title="Old video",
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-new-1",
                        title="New sleep video",
                        description="desc",
                        published_at="2026-04-03T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-new-2",
                        title="New recovery video",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )
            old_run = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=old_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-old",
                    video_title="Old video",
                    primary_topic=SuggestedTopicLabel(
                        label="Archive",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )

            create_topic(db_path, project_name="proj", topic_name="Sleep")
            latest_run = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=latest_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-new-1",
                    video_title="New sleep video",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=True,
                        rationale="sleep",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            store_video_topic_suggestion(
                db_path,
                run_id=latest_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-new-2",
                    video_title="New recovery video",
                    primary_topic=SuggestedTopicLabel(
                        label="Recovery",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="recovery",
                    ),
                    secondary_topics=[
                        SuggestedTopicLabel(
                            label="Sleep",
                            assignment_type="secondary",
                            reuse_existing=True,
                            rationale="sleep overlap",
                        )
                    ],
                    raw_response_json="{}",
                ),
            )

            list_rows = list_video_topic_suggestions(db_path, run_id=latest_run, status="pending")
            self.assertEqual([(row["youtube_video_id"], row["suggested_label"]) for row in list_rows], [
                ("vid-new-1", "Sleep"),
                ("vid-new-2", "Recovery"),
                ("vid-new-2", "Sleep"),
            ])

            summary_rows = summarize_topic_suggestion_labels(db_path, run_id=latest_run, status="pending")
            self.assertEqual([(row["name"], row["suggestion_count"]) for row in summary_rows], [
                ("Sleep", 2),
                ("Recovery", 1),
            ])

            stdout = io.StringIO()
            argv = [
                "yt-channel-analyzer",
                "review-topic-suggestions",
                "--db-path",
                str(db_path),
                "--run-id",
                str(latest_run),
                "--sample-limit",
                "2",
            ]
            with patch.object(sys, "argv", argv):
                with redirect_stdout(stdout):
                    exit_code = main()

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(
                f"Run {latest_run} contains 3 pending video suggestions across 2 labels",
                output,
            )
            self.assertIn("Label: Sleep | videos=2 | primary=1 | secondary=1 | reuses existing approved topic", output)
            self.assertIn("Label: Recovery | videos=1 | primary=1 | secondary=0 | new suggested topic", output)
            self.assertIn("- [primary] New sleep video (vid-new-1)", output)
            self.assertIn("- [secondary] New recovery video (vid-new-2)", output)
            self.assertIn("python3 -m yt_channel_analyzer.cli approve-topic-suggestion-label --db-path", output)
            self.assertNotIn("Archive", output)

    def test_run_scoped_reviewed_label_can_be_approved_by_exact_visible_name(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "topic-review-approve.sqlite3"
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
                        youtube_video_id="vid-politics",
                        title="Politics Video",
                        description=None,
                        published_at="2026-04-06T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )

            old_run = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=old_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-politics",
                    video_title="Politics Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Politics",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old run",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Politics", run_id=old_run)

            review_run = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=review_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-politics",
                    video_title="Politics Video",
                    primary_topic=SuggestedTopicLabel(
                        label="Politics",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="current run",
                    ),
                    secondary_topics=[
                        SuggestedTopicLabel(
                            label="Politics & Economics",
                            assignment_type="secondary",
                            reuse_existing=False,
                            rationale="current overlap",
                        )
                    ],
                    raw_response_json="{}",
                ),
            )

            review_rows = get_topic_suggestion_review_rows(db_path, run_id=review_run, status="pending", sample_limit=2)
            visible_labels = []
            for row in review_rows:
                if row["name"] not in visible_labels:
                    visible_labels.append(row["name"])
            self.assertEqual(visible_labels, ["Politics", "Politics & Economics"])

            summary_rows = summarize_topic_suggestion_labels(db_path, run_id=review_run, status="pending")
            self.assertEqual([row["name"] for row in summary_rows], ["Politics", "Politics & Economics"])

            topic_id = approve_topic_suggestion_label(db_path, suggested_label="Politics", run_id=review_run)
            self.assertGreater(topic_id, 0)

            pending_after = summarize_topic_suggestion_labels(db_path, run_id=review_run, status="pending")
            self.assertEqual([row["name"] for row in pending_after], ["Politics & Economics"])
            approved_rows = summarize_topic_suggestion_labels(db_path, run_id=review_run, status="approved")
            self.assertEqual([row["name"] for row in approved_rows], ["Politics"])

    def test_bulk_apply_uses_same_run_scoped_label_resolution_as_review_and_approve(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "topic-bulk-apply.sqlite3"
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
                        youtube_video_id="vid-health-1",
                        title="Health One",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-health-2",
                        title="Health Two",
                        description=None,
                        published_at="2026-04-04T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            old_run = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=old_run,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid-health-1",
                    video_title="Health One",
                    primary_topic=SuggestedTopicLabel(
                        label="Health & Wellness",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old run",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Health & Wellness", run_id=old_run)

            review_run = create_topic_suggestion_run(db_path, model_name="model-b")
            for youtube_video_id, title in (("vid-health-1", "Health One"), ("vid-health-2", "Health Two")):
                store_video_topic_suggestion(
                    db_path,
                    run_id=review_run,
                    suggestion=VideoTopicSuggestion(
                        youtube_video_id=youtube_video_id,
                        video_title=title,
                        primary_topic=SuggestedTopicLabel(
                            label="Health & Wellness",
                            assignment_type="primary",
                            reuse_existing=False,
                            rationale="current run",
                        ),
                        secondary_topics=[],
                        raw_response_json="{}",
                    ),
                )

            review_rows = get_topic_suggestion_review_rows(db_path, run_id=review_run, status="pending", sample_limit=2)
            self.assertEqual([row["name"] for row in review_rows if row["sample_rank"] == 1], ["Health & Wellness"])

            topic_id = approve_topic_suggestion_label(db_path, suggested_label="Health & Wellness", run_id=review_run)
            self.assertGreater(topic_id, 0)

            matched, applied, skipped = bulk_apply_topic_suggestion_label(
                db_path,
                suggested_label="Health & Wellness",
                run_id=review_run,
            )
            self.assertEqual((matched, applied, skipped), (2, 2, 0))

            for video_id in ["vid-health-1", "vid-health-2"]:
                rows = [row for row in get_video_topic_assignments(db_path, video_id=video_id) if row["topic_name"]]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["topic_name"], "Health & Wellness")
                self.assertEqual(rows[0]["assignment_type"], "primary")
                self.assertEqual(rows[0]["assignment_source"], "suggested")


class SubtopicSuggestionReviewWorkflowTests(unittest.TestCase):
    def test_subtopic_suggestion_rows_are_persisted_separately_and_reviewable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "subtopic-review.sqlite3"
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
                        youtube_video_id="vid-1",
                        title="How To Sleep Better Tonight",
                        description="Actionable sleep hygiene and circadian tips.",
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-2",
                        title="Morning Light And Sleep Timing",
                        description="Why morning sunlight changes your sleep schedule.",
                        published_at="2026-04-04T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health & Wellness")
            assign_topic_to_video(db_path, video_id="vid-1", topic_name="Health & Wellness", assignment_type="primary")
            assign_topic_to_video(db_path, video_id="vid-2", topic_name="Health & Wellness", assignment_type="primary")
            create_subtopic(db_path, topic_name="Health & Wellness", subtopic_name="Sleep")

            run_id = create_subtopic_suggestion_run(db_path, topic_name="Health & Wellness", model_name="model-a")
            store_video_subtopic_suggestion(
                db_path,
                run_id=run_id,
                topic_name="Health & Wellness",
                suggestion=VideoSubtopicSuggestion(
                    youtube_video_id="vid-1",
                    video_title="How To Sleep Better Tonight",
                    broad_topic="Health & Wellness",
                    primary_subtopic=SuggestedSubtopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=True,
                        rationale="Strong fit with the existing sleep subtopic.",
                    ),
                    raw_response_json="{}",
                ),
            )
            store_video_subtopic_suggestion(
                db_path,
                run_id=run_id,
                topic_name="Health & Wellness",
                suggestion=VideoSubtopicSuggestion(
                    youtube_video_id="vid-2",
                    video_title="Morning Light And Sleep Timing",
                    broad_topic="Health & Wellness",
                    primary_subtopic=SuggestedSubtopicLabel(
                        label="Circadian Rhythm",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="More specific than the existing subtopics.",
                    ),
                    raw_response_json="{}",
                ),
            )

            rows = list_video_subtopic_suggestions(db_path, topic_name="Health & Wellness", run_id=run_id)
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["suggested_label"] for row in rows], ["Sleep", "Circadian Rhythm"])
            review_rows = get_subtopic_suggestion_review_rows(
                db_path,
                topic_name="Health & Wellness",
                run_id=run_id,
                sample_limit=2,
            )
            self.assertEqual(sorted(row["name"] for row in review_rows if row["sample_rank"] == 1), ["Circadian Rhythm", "Sleep"])
            summary_rows = summarize_subtopic_suggestion_labels(db_path, topic_name="Health & Wellness", run_id=run_id)
            self.assertEqual(sorted(row["name"] for row in summary_rows), ["Circadian Rhythm", "Sleep"])

            renamed_id = rename_subtopic_suggestion_label(
                db_path,
                topic_name="Health & Wellness",
                current_name="Circadian Rhythm",
                new_name="Sleep Timing",
                run_id=run_id,
            )
            self.assertGreater(renamed_id, 0)
            subtopic_id = approve_subtopic_suggestion_label(
                db_path,
                topic_name="Health & Wellness",
                suggested_label="Sleep Timing",
                run_id=run_id,
            )
            self.assertGreater(subtopic_id, 0)
            rejected = reject_subtopic_suggestion_label(
                db_path,
                topic_name="Health & Wellness",
                suggested_label="Sleep",
                run_id=run_id,
            )
            self.assertEqual(rejected, 1)
            self.assertEqual([row["name"] for row in list_subtopics(db_path, topic_name="Health & Wellness")], ["Sleep", "Sleep Timing"])
            vid1_assignments = [row for row in get_video_subtopic_assignments(db_path, video_id="vid-1") if row["subtopic_name"]]
            vid2_assignments = [row for row in get_video_subtopic_assignments(db_path, video_id="vid-2") if row["subtopic_name"]]
            self.assertEqual(vid1_assignments, [])
            self.assertEqual(vid2_assignments, [])

    def test_review_subtopic_suggestions_cli_defaults_to_latest_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "subtopic-review-cli.sqlite3"
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
                        youtube_video_id="vid-1",
                        title="Protein For Recovery",
                        description="A guide to workout recovery nutrition.",
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health & Wellness")
            assign_topic_to_video(db_path, video_id="vid-1", topic_name="Health & Wellness", assignment_type="primary")
            run_id = create_subtopic_suggestion_run(db_path, topic_name="Health & Wellness", model_name="model-a")
            store_video_subtopic_suggestion(
                db_path,
                run_id=run_id,
                topic_name="Health & Wellness",
                suggestion=VideoSubtopicSuggestion(
                    youtube_video_id="vid-1",
                    video_title="Protein For Recovery",
                    broad_topic="Health & Wellness",
                    primary_subtopic=SuggestedSubtopicLabel(
                        label="Recovery Nutrition",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="Concrete and reusable within the broad topic.",
                    ),
                    raw_response_json="{}",
                ),
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "review-subtopic-suggestions",
                    "--db-path",
                    str(db_path),
                    "--topic",
                    "Health & Wellness",
                ])
            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn(f"Latest suggestion run {run_id} contains 1 pending subtopic suggestions across 1 labels for topic Health & Wellness", output)
            self.assertIn("Label: Recovery Nutrition | videos=1 | new suggested subtopic", output)
            self.assertIn("approve-subtopic-suggestion-label", output)

    def test_subtopic_review_merge_reuses_existing_label_name_within_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "subtopic-review-merge.sqlite3"
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
                        youtube_video_id="vid-1",
                        title="How To Sleep Better Tonight",
                        description="Actionable sleep hygiene tips.",
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-2",
                        title="Morning Light And Sleep Timing",
                        description="Why morning sunlight changes your sleep schedule.",
                        published_at="2026-04-04T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health & Wellness")
            assign_topic_to_video(db_path, video_id="vid-1", topic_name="Health & Wellness", assignment_type="primary")
            assign_topic_to_video(db_path, video_id="vid-2", topic_name="Health & Wellness", assignment_type="primary")
            create_subtopic(db_path, topic_name="Health & Wellness", subtopic_name="Sleep")

            run_id = create_subtopic_suggestion_run(db_path, topic_name="Health & Wellness", model_name="model-a")
            store_video_subtopic_suggestion(
                db_path,
                run_id=run_id,
                topic_name="Health & Wellness",
                suggestion=VideoSubtopicSuggestion(
                    youtube_video_id="vid-1",
                    video_title="How To Sleep Better Tonight",
                    broad_topic="Health & Wellness",
                    primary_subtopic=SuggestedSubtopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=True,
                        rationale="Strong fit with the existing sleep subtopic.",
                    ),
                    raw_response_json="{}",
                ),
            )
            store_video_subtopic_suggestion(
                db_path,
                run_id=run_id,
                topic_name="Health & Wellness",
                suggestion=VideoSubtopicSuggestion(
                    youtube_video_id="vid-2",
                    video_title="Morning Light And Sleep Timing",
                    broad_topic="Health & Wellness",
                    primary_subtopic=SuggestedSubtopicLabel(
                        label="Circadian Rhythm",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="More specific than the existing subtopics.",
                    ),
                    raw_response_json="{}",
                ),
            )

            merged_pending_id = rename_subtopic_suggestion_label(
                db_path,
                topic_name="Health & Wellness",
                current_name="Circadian Rhythm",
                new_name="Sleep",
                run_id=run_id,
            )

            pending_rows = summarize_subtopic_suggestion_labels(db_path, topic_name="Health & Wellness", run_id=run_id, status="pending")
            self.assertEqual(len(pending_rows), 1)
            self.assertEqual(pending_rows[0]["name"], "Sleep")
            self.assertEqual(pending_rows[0]["suggestion_count"], 2)

            with connect(db_path) as connection:
                label_rows = connection.execute(
                    """
                    SELECT name, status
                    FROM subtopic_suggestion_labels
                    WHERE suggestion_run_id = ?
                    ORDER BY id
                    """,
                    (run_id,),
                ).fetchall()
            self.assertEqual(label_rows, [("Sleep", "pending"), ("Circadian Rhythm", "superseded")])

            approved_id = approve_subtopic_suggestion_label(
                db_path,
                topic_name="Health & Wellness",
                suggested_label="Sleep",
                approved_name="Sleep",
                run_id=run_id,
            )
            self.assertEqual(approved_id, merged_pending_id)

            approved_rows = summarize_subtopic_suggestion_labels(db_path, topic_name="Health & Wellness", run_id=run_id, status="approved")
            self.assertEqual(len(approved_rows), 1)
            self.assertEqual(approved_rows[0]["name"], "Sleep")
            self.assertEqual(approved_rows[0]["suggestion_count"], 2)


class ComparisonGroupSuggestionTests(unittest.TestCase):
    def test_comparison_group_prompt_and_schema_are_constrained(self) -> None:
        prompt = _build_comparison_group_prompt(
            project_name="proj",
            broad_topic_name="Health & Wellness",
            subtopic_name="Sleep",
            approved_comparison_groups=[{"name": "Sleep Routines", "description": "Bedtime routine videos.", "member_count": 4}],
            video_title="Best Bedtime Routine For Deep Sleep",
            video_description="An evening checklist for sleep quality.",
        )
        self.assertIn("Approved subtopic: Sleep", prompt)
        self.assertIn("Approved comparison groups within this subtopic", prompt)
        self.assertIn("Sleep Routines", prompt)
        self.assertIn("members=4", prompt)
        schema = _comparison_group_response_schema()
        self.assertEqual(schema["required"], ["primary_comparison_group"])

    def test_suggest_comparison_groups_reuses_existing_label_when_close_match(self) -> None:
        payload = {
            "primary_comparison_group": {
                "label": "sleep routines",
                "reuse_existing": False,
                "rationale": "Same concept as the approved label.",
            }
        }

        class FakeResponse:
            output_text = json.dumps(payload)

        class FakeResponses:
            def create(self, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        class FakeClient:
            def __init__(self):
                self.responses = FakeResponses()

        with patch("yt_channel_analyzer.legacy.comparison_group_suggestions._get_openai_client", return_value=FakeClient()):
            suggestion = suggest_comparison_groups_for_video(
                project_name="proj",
                broad_topic_name="Health & Wellness",
                subtopic_name="Sleep",
                approved_comparison_groups=[{"name": "Sleep Routines", "description": None, "member_count": 2}],
                youtube_video_id="vid-1",
                video_title="How To Build A Sleep Routine",
                video_description="Bedtime habits that help you fall asleep.",
            )

        self.assertEqual(suggestion.primary_comparison_group.label, "Sleep Routines")
        self.assertTrue(suggestion.primary_comparison_group.reuse_existing)

    def test_comparison_group_suggestion_rows_are_persisted_separately_and_reviewable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "comparison-group-review.sqlite3"
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
                        youtube_video_id="vid-1",
                        title="Best Sleep Routine For Deep Sleep",
                        description="A bedtime routine for better sleep.",
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-2",
                        title="Fix Jet Lag Faster",
                        description="Travel-focused sleep reset tactics.",
                        published_at="2026-04-04T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health & Wellness")
            create_subtopic(db_path, topic_name="Health & Wellness", subtopic_name="Sleep")
            assign_topic_to_video(db_path, video_id="vid-1", topic_name="Health & Wellness", assignment_type="primary")
            assign_topic_to_video(db_path, video_id="vid-2", topic_name="Health & Wellness", assignment_type="primary")
            assign_subtopic_to_video(db_path, video_id="vid-1", subtopic_name="Sleep")
            assign_subtopic_to_video(db_path, video_id="vid-2", subtopic_name="Sleep")
            create_comparison_group(db_path, subtopic_name="Sleep", group_name="Sleep Routines")

            approved_groups = list_approved_comparison_groups_for_subtopic(db_path, subtopic_name="Sleep")
            self.assertEqual([row["name"] for row in approved_groups], ["Sleep Routines"])

            run_id = create_comparison_group_suggestion_run(db_path, subtopic_name="Sleep", model_name="model-a")
            store_video_comparison_group_suggestion(
                db_path,
                run_id=run_id,
                subtopic_name="Sleep",
                suggestion=VideoComparisonGroupSuggestion(
                    youtube_video_id="vid-1",
                    video_title="Best Sleep Routine For Deep Sleep",
                    broad_topic="Health & Wellness",
                    subtopic="Sleep",
                    primary_comparison_group=SuggestedComparisonGroupLabel(
                        label="Sleep Routines",
                        reuse_existing=True,
                        rationale="Strong fit with the existing routine bucket.",
                    ),
                    raw_response_json="{}",
                ),
            )
            store_video_comparison_group_suggestion(
                db_path,
                run_id=run_id,
                subtopic_name="Sleep",
                suggestion=VideoComparisonGroupSuggestion(
                    youtube_video_id="vid-2",
                    video_title="Fix Jet Lag Faster",
                    broad_topic="Health & Wellness",
                    subtopic="Sleep",
                    primary_comparison_group=SuggestedComparisonGroupLabel(
                        label="Jet Lag Recovery",
                        reuse_existing=False,
                        rationale="Concrete new comparison group within sleep.",
                    ),
                    raw_response_json="{}",
                ),
            )

            rows = list_video_comparison_group_suggestions(db_path, subtopic_name="Sleep", run_id=run_id)
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["suggested_label"] for row in rows], ["Sleep Routines", "Jet Lag Recovery"])
            review_rows = get_comparison_group_suggestion_review_rows(db_path, subtopic_name="Sleep", run_id=run_id, sample_limit=2)
            self.assertEqual(sorted(row["name"] for row in review_rows if row["sample_rank"] == 1), ["Jet Lag Recovery", "Sleep Routines"])
            summary_rows = summarize_comparison_group_suggestion_labels(db_path, subtopic_name="Sleep", run_id=run_id)
            self.assertEqual(sorted(row["name"] for row in summary_rows), ["Jet Lag Recovery", "Sleep Routines"])

            renamed_id = rename_comparison_group_suggestion_label(
                db_path,
                subtopic_name="Sleep",
                current_name="Jet Lag Recovery",
                new_name="Travel Sleep Reset",
                run_id=run_id,
            )
            self.assertGreater(renamed_id, 0)
            group_id = approve_comparison_group_suggestion_label(
                db_path,
                subtopic_name="Sleep",
                suggested_label="Travel Sleep Reset",
                run_id=run_id,
            )
            self.assertGreater(group_id, 0)
            rejected = reject_comparison_group_suggestion_label(
                db_path,
                subtopic_name="Sleep",
                suggested_label="Sleep Routines",
                run_id=run_id,
            )
            self.assertEqual(rejected, 1)
            self.assertEqual(
                [row["name"] for row in list_comparison_groups(db_path, subtopic_name="Sleep")],
                ["Sleep Routines", "Travel Sleep Reset"],
            )
            self.assertEqual(list_group_videos(db_path, group_id=group_id), [])

    def test_review_comparison_group_suggestions_cli_defaults_to_latest_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "comparison-group-review-cli.sqlite3"
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
                        youtube_video_id="vid-1",
                        title="Best Bedtime Routine",
                        description="Sleep routine steps for easier nights.",
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health & Wellness")
            create_subtopic(db_path, topic_name="Health & Wellness", subtopic_name="Sleep")
            assign_topic_to_video(db_path, video_id="vid-1", topic_name="Health & Wellness", assignment_type="primary")
            assign_subtopic_to_video(db_path, video_id="vid-1", subtopic_name="Sleep")
            run_id = create_comparison_group_suggestion_run(db_path, subtopic_name="Sleep", model_name="model-a")
            store_video_comparison_group_suggestion(
                db_path,
                run_id=run_id,
                subtopic_name="Sleep",
                suggestion=VideoComparisonGroupSuggestion(
                    youtube_video_id="vid-1",
                    video_title="Best Bedtime Routine",
                    broad_topic="Health & Wellness",
                    subtopic="Sleep",
                    primary_comparison_group=SuggestedComparisonGroupLabel(
                        label="Sleep Routines",
                        reuse_existing=False,
                        rationale="Concrete and reusable comparison bucket.",
                    ),
                    raw_response_json="{}",
                ),
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "review-comparison-group-suggestions",
                    "--db-path",
                    str(db_path),
                    "--subtopic",
                    "Sleep",
                ])
            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn(f"Latest suggestion run {run_id} contains 1 pending comparison-group suggestions across 1 labels for subtopic Sleep", output)
            self.assertIn("Label: Sleep Routines | videos=1 | new suggested comparison group", output)
            self.assertIn("approve-comparison-group-suggestion-label", output)

    def test_comparison_group_review_merge_reuses_existing_label_name_within_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "comparison-group-review-merge.sqlite3"
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
                        youtube_video_id="vid-1",
                        title="Best Sleep Routine For Deep Sleep",
                        description="Routine-focused sleep video.",
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid-2",
                        title="Fix Jet Lag Faster",
                        description="Travel-focused sleep reset tactics.",
                        published_at="2026-04-04T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Health & Wellness")
            create_subtopic(db_path, topic_name="Health & Wellness", subtopic_name="Sleep")
            assign_topic_to_video(db_path, video_id="vid-1", topic_name="Health & Wellness", assignment_type="primary")
            assign_topic_to_video(db_path, video_id="vid-2", topic_name="Health & Wellness", assignment_type="primary")
            assign_subtopic_to_video(db_path, video_id="vid-1", subtopic_name="Sleep")
            assign_subtopic_to_video(db_path, video_id="vid-2", subtopic_name="Sleep")
            create_comparison_group(db_path, subtopic_name="Sleep", group_name="Sleep Routines")

            run_id = create_comparison_group_suggestion_run(db_path, subtopic_name="Sleep", model_name="model-a")
            store_video_comparison_group_suggestion(
                db_path,
                run_id=run_id,
                subtopic_name="Sleep",
                suggestion=VideoComparisonGroupSuggestion(
                    youtube_video_id="vid-1",
                    video_title="Best Sleep Routine For Deep Sleep",
                    broad_topic="Health & Wellness",
                    subtopic="Sleep",
                    primary_comparison_group=SuggestedComparisonGroupLabel(
                        label="Sleep Routines",
                        reuse_existing=True,
                        rationale="Strong fit with the existing routine bucket.",
                    ),
                    raw_response_json="{}",
                ),
            )
            store_video_comparison_group_suggestion(
                db_path,
                run_id=run_id,
                subtopic_name="Sleep",
                suggestion=VideoComparisonGroupSuggestion(
                    youtube_video_id="vid-2",
                    video_title="Fix Jet Lag Faster",
                    broad_topic="Health & Wellness",
                    subtopic="Sleep",
                    primary_comparison_group=SuggestedComparisonGroupLabel(
                        label="Jet Lag Recovery",
                        reuse_existing=False,
                        rationale="Concrete new comparison group within sleep.",
                    ),
                    raw_response_json="{}",
                ),
            )

            merged_pending_id = rename_comparison_group_suggestion_label(
                db_path,
                subtopic_name="Sleep",
                current_name="Jet Lag Recovery",
                new_name="Sleep Routines",
                run_id=run_id,
            )

            pending_rows = summarize_comparison_group_suggestion_labels(db_path, subtopic_name="Sleep", run_id=run_id, status="pending")
            self.assertEqual(len(pending_rows), 1)
            self.assertEqual(pending_rows[0]["name"], "Sleep Routines")
            self.assertEqual(pending_rows[0]["suggestion_count"], 2)

            approved_id = approve_comparison_group_suggestion_label(
                db_path,
                subtopic_name="Sleep",
                suggested_label="Sleep Routines",
                approved_name="Sleep Routines",
                run_id=run_id,
            )
            self.assertEqual(approved_id, merged_pending_id)

            approved_rows = summarize_comparison_group_suggestion_labels(db_path, subtopic_name="Sleep", run_id=run_id, status="approved")
            self.assertEqual(len(approved_rows), 1)
            self.assertEqual(approved_rows[0]["name"], "Sleep Routines")
            self.assertEqual(approved_rows[0]["suggestion_count"], 2)


class EditingManagementTests(unittest.TestCase):
    def test_rename_operations_preserve_relationships_and_listings(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "edit.sqlite3"
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
                        youtube_video_id="vid123",
                        title="Video",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Topic")
            create_subtopic(db_path, topic_name="Topic", subtopic_name="Subtopic")
            create_comparison_group(db_path, subtopic_name="Subtopic", group_name="Group")
            assign_topic_to_video(db_path, video_id="vid123", topic_name="Topic", assignment_type="primary")
            assign_subtopic_to_video(db_path, video_id="vid123", subtopic_name="Subtopic")
            add_video_to_comparison_group(db_path, video_id="vid123", group_name="Group")

            topic_id = rename_topic(db_path, project_name="proj", current_name="Topic", new_name="Renamed Topic")
            subtopic_id = rename_subtopic(
                db_path,
                topic_name="Renamed Topic",
                current_name="Subtopic",
                new_name="Renamed Subtopic",
            )
            group_id = rename_comparison_group(
                db_path,
                subtopic_name="Renamed Subtopic",
                current_name="Group",
                new_name="Renamed Group",
            )

            self.assertEqual(topic_id, list_topics(db_path)[0]["id"])
            self.assertEqual(list_topics(db_path)[0]["name"], "Renamed Topic")
            self.assertEqual(subtopic_id, list_subtopics(db_path, topic_name="Renamed Topic")[0]["id"])
            self.assertEqual(list_subtopics(db_path, topic_name="Renamed Topic")[0]["name"], "Renamed Subtopic")
            self.assertEqual(group_id, list_comparison_groups(db_path, subtopic_name="Renamed Subtopic")[0]["id"])
            self.assertEqual(list_comparison_groups(db_path, subtopic_name="Renamed Subtopic")[0]["name"], "Renamed Group")

            topic_assignments = [row for row in get_video_topic_assignments(db_path, video_id="vid123") if row["topic_name"]]
            self.assertEqual(topic_assignments[0]["topic_name"], "Renamed Topic")
            subtopic_assignments = [row for row in get_video_subtopic_assignments(db_path, video_id="vid123") if row["subtopic_name"]]
            self.assertEqual(subtopic_assignments[0]["topic_name"], "Renamed Topic")
            self.assertEqual(subtopic_assignments[0]["subtopic_name"], "Renamed Subtopic")
            group = resolve_comparison_group(db_path, group_name="Renamed Group")
            self.assertEqual(group["id"], group_id)
            self.assertEqual(len(list_group_videos(db_path, group_id=group_id)), 1)

    def test_remove_video_from_group_keeps_other_records_intact(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remove.sqlite3"
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
                        youtube_video_id="vid123",
                        title="Video",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            create_topic(db_path, project_name="proj", topic_name="Topic")
            create_subtopic(db_path, topic_name="Topic", subtopic_name="Subtopic")
            create_comparison_group(db_path, subtopic_name="Subtopic", group_name="Group")
            add_video_to_comparison_group(db_path, video_id="vid123", group_name="Group")

            remove_video_from_comparison_group(db_path, video_id="vid123", group_name="Group")

            group = resolve_comparison_group(db_path, group_name="Group")
            self.assertEqual(list_group_videos(db_path, group_id=group["id"]), [])
            self.assertEqual(len(list_topics(db_path)), 1)
            self.assertEqual(len(list_subtopics(db_path, topic_name="Topic")), 1)
            with connect(db_path) as connection:
                video_count = connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
                transcript_count = connection.execute("SELECT COUNT(*) FROM video_transcripts").fetchone()[0]
            self.assertEqual(video_count, 1)
            self.assertEqual(transcript_count, 0)
            with self.assertRaisesRegex(ValueError, "video vid123 is not in comparison group: Group"):
                remove_video_from_comparison_group(db_path, video_id="vid123", group_name="Group")

    def test_management_operations_fail_clearly_for_missing_targets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "missing.sqlite3"
            init_db(
                db_path,
                project_name="proj",
                channel_id="UC123",
                channel_title="Channel",
                channel_handle="@channel",
            )
            with self.assertRaisesRegex(ValueError, "topic not found: Missing"):
                rename_topic(db_path, project_name="proj", current_name="Missing", new_name="Other")
            create_topic(db_path, project_name="proj", topic_name="Topic")
            with self.assertRaisesRegex(ValueError, "subtopic not found: Missing"):
                rename_subtopic(db_path, topic_name="Topic", current_name="Missing", new_name="Other")
            create_subtopic(db_path, topic_name="Topic", subtopic_name="Subtopic")
            with self.assertRaisesRegex(ValueError, "comparison group not found: Missing"):
                rename_comparison_group(db_path, subtopic_name="Subtopic", current_name="Missing", new_name="Other")
            create_comparison_group(db_path, subtopic_name="Subtopic", group_name="Group")
            upsert_videos_for_primary_channel(
                db_path,
                videos=[
                    VideoMetadata(
                        youtube_video_id="vid123",
                        title="Video",
                        description=None,
                        published_at="2026-04-05T12:00:00Z",
                        thumbnail_url=None,
                    )
                ],
            )
            with self.assertRaisesRegex(ValueError, "video not found: missing-video"):
                remove_video_from_comparison_group(db_path, video_id="missing-video", group_name="Group")
            with self.assertRaisesRegex(ValueError, "comparison group not found: Missing"):
                remove_video_from_comparison_group(db_path, video_id="vid123", group_name="Missing")


if __name__ == "__main__":
    unittest.main()


class TopicSuggestionRunListingTests(unittest.TestCase):
    def test_list_topic_suggestion_runs_returns_run_level_counts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "run-list.sqlite3"
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
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid2",
                        title="Video 2",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid3",
                        title="Video 3",
                        description="desc",
                        published_at="2026-04-03T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            run1 = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=run1,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid1",
                    video_title="Video 1",
                    primary_topic=SuggestedTopicLabel(
                        label="Archive",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old primary",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Archive", run_id=run1)

            run2 = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=run2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid2",
                    video_title="Video 2",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="sleep primary",
                    ),
                    secondary_topics=[
                        SuggestedTopicLabel(
                            label="Recovery",
                            assignment_type="secondary",
                            reuse_existing=False,
                            rationale="recovery overlap",
                        )
                    ],
                    raw_response_json="{}",
                ),
            )
            store_video_topic_suggestion(
                db_path,
                run_id=run2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid3",
                    video_title="Video 3",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="sleep again",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            approve_topic_suggestion_label(db_path, suggested_label="Sleep", run_id=run2)
            supersede_stale_topic_suggestions(db_path, keep_run_id=run2, suggested_label="Recovery")

            rows = list_topic_suggestion_runs(db_path)
            self.assertEqual([row["id"] for row in rows], [run2, run1])

            latest = rows[0]
            self.assertEqual(latest["model_name"], "model-b")
            self.assertEqual(latest["label_count"], 2)
            self.assertEqual(latest["suggestion_row_count"], 3)
            self.assertEqual(latest["pending_label_count"], 1)
            self.assertEqual(latest["approved_label_count"], 1)
            self.assertEqual(latest["rejected_label_count"], 0)
            self.assertEqual(latest["superseded_label_count"], 0)

            older = rows[1]
            self.assertEqual(older["model_name"], "model-a")
            self.assertEqual(older["label_count"], 1)
            self.assertEqual(older["suggestion_row_count"], 1)
            self.assertEqual(older["pending_label_count"], 0)
            self.assertEqual(older["approved_label_count"], 0)
            self.assertEqual(older["rejected_label_count"], 1)
            self.assertEqual(older["superseded_label_count"], 0)

    def test_list_topic_suggestion_runs_cli_prints_human_readable_summary(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "run-list-cli.sqlite3"
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
                        description="desc",
                        published_at="2026-04-01T12:00:00Z",
                        thumbnail_url=None,
                    ),
                    VideoMetadata(
                        youtube_video_id="vid2",
                        title="Video 2",
                        description="desc",
                        published_at="2026-04-02T12:00:00Z",
                        thumbnail_url=None,
                    ),
                ],
            )

            run1 = create_topic_suggestion_run(db_path, model_name="model-a")
            store_video_topic_suggestion(
                db_path,
                run_id=run1,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid1",
                    video_title="Video 1",
                    primary_topic=SuggestedTopicLabel(
                        label="Archive",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="old primary",
                    ),
                    secondary_topics=[],
                    raw_response_json="{}",
                ),
            )
            reject_topic_suggestion_label(db_path, suggested_label="Archive", run_id=run1)

            run2 = create_topic_suggestion_run(db_path, model_name="model-b")
            store_video_topic_suggestion(
                db_path,
                run_id=run2,
                suggestion=VideoTopicSuggestion(
                    youtube_video_id="vid2",
                    video_title="Video 2",
                    primary_topic=SuggestedTopicLabel(
                        label="Sleep",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="sleep primary",
                    ),
                    secondary_topics=[
                        SuggestedTopicLabel(
                            label="Recovery",
                            assignment_type="secondary",
                            reuse_existing=False,
                            rationale="recovery overlap",
                        )
                    ],
                    raw_response_json="{}",
                ),
            )

            stdout = io.StringIO()
            argv = [
                "yt-channel-analyzer",
                "list-topic-suggestion-runs",
                "--db-path",
                str(db_path),
            ]
            with patch.object(sys, "argv", argv):
                with redirect_stdout(stdout):
                    exit_code = main()

            output_lines = stdout.getvalue().strip().splitlines()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(output_lines), 2)
            self.assertIn(f"run={run2}", output_lines[0])
            self.assertIn("model=model-b", output_lines[0])
            self.assertIn("labels=2", output_lines[0])
            self.assertIn("suggestions=2", output_lines[0])
            self.assertIn("pending=2", output_lines[0])
            self.assertIn("approved=0", output_lines[0])
            self.assertIn("rejected=0", output_lines[0])
            self.assertIn("superseded=0", output_lines[0])
            self.assertIn(f"run={run1}", output_lines[1])
            self.assertIn("model=model-a", output_lines[1])
            self.assertIn("labels=1", output_lines[1])
            self.assertIn("suggestions=1", output_lines[1])
            self.assertIn("pending=0", output_lines[1])
            self.assertIn("approved=0", output_lines[1])
            self.assertIn("rejected=1", output_lines[1])
            self.assertIn("superseded=0", output_lines[1])


class ReviewUIAppTests(unittest.TestCase):
    def _call_app(self, app, method: str, path: str, *, query: str = "", body: dict[str, object] | None = None) -> tuple[str, dict[str, str], str]:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(payload),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = dict(headers)

        response_body = b"".join(app(environ, start_response)).decode("utf-8")
        return str(captured["status"]), dict(captured["headers"]), response_body

    def _seed_review_db(self, db_path: Path) -> tuple[int, int]:
        from yt_channel_analyzer.youtube import VideoMetadata

        init_db(
            db_path,
            project_name="proj",
            channel_id="chan-1",
            channel_title="Demo Channel",
            channel_handle="@demo",
        )
        upsert_videos_for_primary_channel(
            db_path,
            videos=[
                VideoMetadata(
                    youtube_video_id="vid-1",
                    title="Sleep habits for creators",
                    description="Sleep and recovery tips.",
                    published_at="2024-01-01T00:00:00Z",
                    thumbnail_url=None,
                ),
                VideoMetadata(
                    youtube_video_id="vid-2",
                    title="Recovery workflow",
                    description="Recovery systems and routines.",
                    published_at="2024-01-02T00:00:00Z",
                    thumbnail_url=None,
                ),
                VideoMetadata(
                    youtube_video_id="vid-3",
                    title="Better sleep setup",
                    description="Sleep setup and recovery environment.",
                    published_at="2024-01-03T00:00:00Z",
                    thumbnail_url=None,
                ),
            ],
        )

        create_topic(db_path, project_name="proj", topic_name="Health & Wellness")
        assign_topic_to_video(db_path, video_id="vid-1", topic_name="Health & Wellness", assignment_type="primary")
        assign_topic_to_video(db_path, video_id="vid-2", topic_name="Health & Wellness", assignment_type="primary")
        create_subtopic(db_path, topic_name="Health & Wellness", subtopic_name="Sleep")

        topic_run_id = create_topic_suggestion_run(db_path, model_name="model-topic")
        store_video_topic_suggestion(
            db_path,
            run_id=topic_run_id,
            suggestion=VideoTopicSuggestion(
                youtube_video_id="vid-1",
                video_title="Sleep habits for creators",
                raw_response_json="{}",
                primary_topic=SuggestedTopicLabel(
                    label="Sleep",
                    assignment_type="primary",
                    reuse_existing=False,
                    rationale="Specific broad topic for sleep-focused content.",
                ),
                secondary_topics=[],
            ),
        )
        store_video_topic_suggestion(
            db_path,
            run_id=topic_run_id,
            suggestion=VideoTopicSuggestion(
                youtube_video_id="vid-2",
                video_title="Recovery workflow",
                raw_response_json="{}",
                primary_topic=SuggestedTopicLabel(
                    label="Recovery",
                    assignment_type="primary",
                    reuse_existing=False,
                    rationale="Recovery-focused workflow content.",
                ),
                secondary_topics=[],
            ),
        )
        store_video_topic_suggestion(
            db_path,
            run_id=topic_run_id,
            suggestion=VideoTopicSuggestion(
                youtube_video_id="vid-3",
                video_title="Better sleep setup",
                raw_response_json="{}",
                primary_topic=SuggestedTopicLabel(
                    label="Sleep",
                    assignment_type="primary",
                    reuse_existing=False,
                    rationale="Another sleep-focused broad topic suggestion.",
                ),
                secondary_topics=[],
            ),
        )

        subtopic_run_id = create_subtopic_suggestion_run(db_path, topic_name="Health & Wellness", model_name="model-subtopic")
        store_video_subtopic_suggestion(
            db_path,
            run_id=subtopic_run_id,
            topic_name="Health & Wellness",
            suggestion=VideoSubtopicSuggestion(
                youtube_video_id="vid-1",
                video_title="Sleep habits for creators",
                raw_response_json="{}",
                broad_topic="Health & Wellness",
                primary_subtopic=SuggestedSubtopicLabel(
                    label="Sleep",
                    assignment_type="primary",
                    reuse_existing=True,
                    rationale="Matches the existing approved subtopic.",
                ),
            ),
        )
        store_video_subtopic_suggestion(
            db_path,
            run_id=subtopic_run_id,
            topic_name="Health & Wellness",
            suggestion=VideoSubtopicSuggestion(
                youtube_video_id="vid-2",
                video_title="Recovery workflow",
                raw_response_json="{}",
                broad_topic="Health & Wellness",
                primary_subtopic=SuggestedSubtopicLabel(
                    label="Circadian Rhythm",
                    assignment_type="primary",
                    reuse_existing=False,
                    rationale="A more specific reusable subtopic.",
                ),
            ),
        )
        return topic_run_id, subtopic_run_id

    def _seed_comparison_review_db(self, db_path: Path) -> int:
        self._seed_review_db(db_path)

        assign_subtopic_to_video(db_path, video_id="vid-1", subtopic_name="Sleep")
        assign_subtopic_to_video(db_path, video_id="vid-2", subtopic_name="Sleep")
        create_comparison_group(db_path, subtopic_name="Sleep", group_name="Sleep Routines")
        add_video_to_comparison_group(db_path, video_id="vid-1", group_name="Sleep Routines")

        comparison_run_id = create_comparison_group_suggestion_run(
            db_path,
            subtopic_name="Sleep",
            model_name="model-comparison",
        )
        store_video_comparison_group_suggestion(
            db_path,
            run_id=comparison_run_id,
            subtopic_name="Sleep",
            suggestion=VideoComparisonGroupSuggestion(
                youtube_video_id="vid-1",
                video_title="Sleep habits for creators",
                broad_topic="Health & Wellness",
                subtopic="Sleep",
                primary_comparison_group=SuggestedComparisonGroupLabel(
                    label="Sleep Routines",
                    reuse_existing=True,
                    rationale="Matches the existing routine-oriented group.",
                ),
                raw_response_json="{}",
            ),
        )
        store_video_comparison_group_suggestion(
            db_path,
            run_id=comparison_run_id,
            subtopic_name="Sleep",
            suggestion=VideoComparisonGroupSuggestion(
                youtube_video_id="vid-2",
                video_title="Recovery workflow",
                broad_topic="Health & Wellness",
                subtopic="Sleep",
                primary_comparison_group=SuggestedComparisonGroupLabel(
                    label="Wind Down Rituals",
                    reuse_existing=False,
                    rationale="A new reusable group for nighttime routines.",
                ),
                raw_response_json="{}",
            ),
        )
        return comparison_run_id

    def test_review_ui_state_and_mutations_work_for_topic_and_subtopic_flows(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui.sqlite3"
            topic_run_id, subtopic_run_id = self._seed_review_db(db_path)
            app = build_review_app(db_path)

            status, headers, body = self._call_app(app, "GET", "/")
            self.assertEqual(status, "200 OK")
            self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
            self.assertIn("YT Channel Analyzer Review UI", body)

            status, headers, body = self._call_app(app, "GET", "/api/state", query=f"run_id={topic_run_id}")
            self.assertEqual(status, "200 OK")
            self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
            payload = json.loads(body)
            self.assertEqual(payload["run_id"], topic_run_id)
            self.assertEqual(payload["dataset_name"], "review-ui.sqlite3")
            self.assertEqual(payload["dataset_video_count"], 3)
            self.assertEqual(payload["current_run"]["scope_label"], "Topic suggestions")
            self.assertEqual(payload["topic_reviews"]["eligible_video_count"], 3)
            self.assertEqual(payload["topic_reviews"]["summary"]["pending"], 2)
            self.assertEqual([item["name"] for item in payload["topic_reviews"]["pending"]], ["Sleep", "Recovery"])

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/topic/approve",
                body={"run_id": topic_run_id, "label": "Sleep", "approved_name": "Sleep & Recovery"},
            )
            self.assertEqual(status, "200 OK")
            status, _, body = self._call_app(
                app,
                "POST",
                "/api/topic/bulk-apply",
                body={"run_id": topic_run_id, "label": "Sleep & Recovery"},
            )
            self.assertEqual(status, "200 OK")
            result = json.loads(body)
            self.assertIn("Matched 2, applied 1, skipped 1", result["message"])
            assignments = [row for row in get_video_topic_assignments(db_path, video_id="vid-3") if row["topic_name"]]
            self.assertEqual(assignments[0]["topic_name"], "Sleep & Recovery")
            self.assertEqual(assignments[0]["assignment_source"], "suggested")

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={subtopic_run_id}&topic=Health+%26+Wellness",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["subtopic_reviews"]["eligible_video_count"], 2)
            self.assertEqual(payload["subtopic_reviews"]["summary"]["pending"], 2)
            self.assertEqual([item["name"] for item in payload["subtopic_reviews"]["pending"]], ["Circadian Rhythm", "Sleep"])

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/subtopic/rename",
                body={
                    "run_id": subtopic_run_id,
                    "topic": "Health & Wellness",
                    "current_name": "Circadian Rhythm",
                    "new_name": "Sleep Timing",
                },
            )
            self.assertEqual(status, "200 OK")
            status, _, body = self._call_app(
                app,
                "POST",
                "/api/subtopic/reject",
                body={"run_id": subtopic_run_id, "topic": "Health & Wellness", "label": "Sleep"},
            )
            self.assertEqual(status, "200 OK")
            status, _, body = self._call_app(
                app,
                "POST",
                "/api/subtopic/approve",
                body={"run_id": subtopic_run_id, "topic": "Health & Wellness", "label": "Sleep Timing"},
            )
            self.assertEqual(status, "200 OK")
            subtopics = [row["name"] for row in list_subtopics(db_path, topic_name="Health & Wellness")]
            self.assertEqual(subtopics, ["Sleep", "Sleep Timing"])

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={subtopic_run_id}&topic=Health+%26+Wellness",
            )
            payload = json.loads(body)
            self.assertEqual(payload["subtopic_reviews"]["summary"]["pending"], 0)
            self.assertEqual(payload["subtopic_reviews"]["summary"]["approved"], 1)
            self.assertEqual(payload["subtopic_reviews"]["summary"]["rejected"], 1)

    def test_review_ui_returns_validation_errors_as_json(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui-errors.sqlite3"
            topic_run_id, _ = self._seed_review_db(db_path)
            app = build_review_app(db_path)

            status, headers, body = self._call_app(
                app,
                "POST",
                "/api/topic/approve",
                body={"run_id": topic_run_id},
            )
            self.assertEqual(status, "400 Bad Request")
            self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
            payload = json.loads(body)
            self.assertIn("missing required field: label", payload["error"])

    def test_review_ui_subtopic_approve_can_merge_into_existing_name(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui-subtopic-merge.sqlite3"
            _, subtopic_run_id = self._seed_review_db(db_path)
            app = build_review_app(db_path)

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/subtopic/approve",
                body={
                    "run_id": subtopic_run_id,
                    "topic": "Health & Wellness",
                    "label": "Circadian Rhythm",
                    "approved_name": "Sleep",
                },
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertIn("Approved subtopic label 'Circadian Rhythm' as 'Sleep'", payload["message"])

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={subtopic_run_id}&topic=Health+%26+Wellness",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["subtopic_reviews"]["summary"]["pending"], 0)
            self.assertEqual(payload["subtopic_reviews"]["summary"]["approved"], 1)
            self.assertEqual(payload["subtopic_reviews"]["approved"][0]["name"], "Sleep")
            self.assertEqual(payload["subtopic_reviews"]["approved"][0]["suggestion_count"], 2)

    def test_review_ui_can_apply_approved_topic_suggestions_to_individual_videos(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui-topic-apply.sqlite3"
            topic_run_id, _ = self._seed_review_db(db_path)
            app = build_review_app(db_path)

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/topic/approve",
                body={"run_id": topic_run_id, "label": "Sleep", "approved_name": "Sleep & Recovery"},
            )
            self.assertEqual(status, "200 OK")

            status, _, body = self._call_app(app, "GET", "/api/state", query=f"run_id={topic_run_id}")
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            approved_item = next(item for item in payload["topic_reviews"]["approved"] if item["name"] == "Sleep & Recovery")
            self.assertEqual(approved_item["apply_ready_count"], 1)
            self.assertEqual(approved_item["blocked_count"], 1)
            self.assertEqual(
                [item["youtube_video_id"] for item in approved_item["applications"] if item["can_apply"]],
                ["vid-3"],
            )

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/topic/apply-video",
                body={"run_id": topic_run_id, "label": "Sleep & Recovery", "video_id": "vid-3"},
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertIn("Applied topic label 'Sleep & Recovery' to video 'vid-3'", payload["message"])

            assignments = [row for row in get_video_topic_assignments(db_path, video_id="vid-3") if row["topic_name"]]
            self.assertEqual(assignments[0]["topic_name"], "Sleep & Recovery")
            self.assertEqual(assignments[0]["assignment_source"], "suggested")

            status, _, body = self._call_app(app, "GET", "/api/state", query=f"run_id={topic_run_id}")
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            approved_item = next(item for item in payload["topic_reviews"]["approved"] if item["name"] == "Sleep & Recovery")
            applied_application = next(item for item in approved_item["applications"] if item["youtube_video_id"] == "vid-3")
            self.assertFalse(applied_application["can_apply"])
            self.assertEqual(applied_application["status_label"], "Already applied")
            self.assertEqual(approved_item["applied_count"], 1)

    def test_review_ui_can_apply_approved_subtopic_suggestions_to_individual_videos(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui-subtopic-apply.sqlite3"
            _, subtopic_run_id = self._seed_review_db(db_path)
            app = build_review_app(db_path)

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/subtopic/approve",
                body={"run_id": subtopic_run_id, "topic": "Health & Wellness", "label": "Circadian Rhythm", "approved_name": "Sleep Timing"},
            )
            self.assertEqual(status, "200 OK")

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={subtopic_run_id}&topic=Health+%26+Wellness",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            approved_item = next(item for item in payload["subtopic_reviews"]["approved"] if item["name"] == "Sleep Timing")
            self.assertEqual(approved_item["apply_ready_count"], 1)
            self.assertEqual(approved_item["applications"][0]["youtube_video_id"], "vid-2")
            self.assertTrue(approved_item["applications"][0]["can_apply"])

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/subtopic/apply-video",
                body={
                    "run_id": subtopic_run_id,
                    "topic": "Health & Wellness",
                    "label": "Sleep Timing",
                    "video_id": "vid-2",
                },
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertIn("Applied subtopic label 'Sleep Timing' to video 'vid-2'", payload["message"])

            assignments = [row for row in get_video_subtopic_assignments(db_path, video_id="vid-2") if row["subtopic_name"]]
            self.assertEqual(assignments[0]["subtopic_name"], "Sleep Timing")
            self.assertEqual(assignments[0]["assignment_source"], "suggested")

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={subtopic_run_id}&topic=Health+%26+Wellness",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            approved_item = next(item for item in payload["subtopic_reviews"]["approved"] if item["name"] == "Sleep Timing")
            self.assertEqual(approved_item["applied_count"], 1)
            self.assertFalse(approved_item["applications"][0]["can_apply"])
            self.assertEqual(approved_item["applications"][0]["status_label"], "Already applied")

    def test_review_ui_can_generate_topic_and_subtopic_suggestion_runs(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui-generate.sqlite3"
            self._seed_review_db(db_path)
            app = build_review_app(db_path)

            with patch(
                "yt_channel_analyzer.review_ui.suggest_topics_for_video",
                side_effect=lambda **kwargs: VideoTopicSuggestion(
                    youtube_video_id=kwargs["youtube_video_id"],
                    video_title=kwargs["video_title"],
                    raw_response_json="{}",
                    primary_topic=SuggestedTopicLabel(
                        label=f"Topic for {kwargs['youtube_video_id']}",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="Generated in UI test.",
                    ),
                    secondary_topics=[],
                ),
            ):
                status, _, body = self._call_app(
                    app,
                    "POST",
                    "/api/generate/topics",
                    body={"model": "ui-test-model", "limit": 2},
                )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            topic_run_id = payload["run_id"]
            self.assertIn("Generated topic suggestions for 2 video(s)", payload["message"])

            status, _, body = self._call_app(app, "GET", "/api/state", query=f"run_id={topic_run_id}")
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["dataset_name"], "review-ui-generate.sqlite3")
            self.assertEqual(payload["dataset_video_count"], 3)
            self.assertEqual(payload["current_run"]["model_name"], "ui-test-model")
            self.assertEqual(payload["current_run"]["scope_label"], "Topic suggestions")
            self.assertEqual(payload["topic_reviews"]["eligible_video_count"], 3)
            self.assertEqual(payload["topic_reviews"]["summary"]["pending"], 2)
            self.assertEqual(len(payload["topic_reviews"]["pending"]), 2)

            with patch(
                "yt_channel_analyzer.review_ui.suggest_subtopics_for_video",
                side_effect=lambda **kwargs: VideoSubtopicSuggestion(
                    youtube_video_id=kwargs["youtube_video_id"],
                    video_title=kwargs["video_title"],
                    raw_response_json="{}",
                    broad_topic=kwargs["broad_topic_name"],
                    primary_subtopic=SuggestedSubtopicLabel(
                        label="Sleep Timing",
                        assignment_type="primary",
                        reuse_existing=False,
                        rationale="Generated in UI test.",
                    ),
                ),
            ):
                status, _, body = self._call_app(
                    app,
                    "POST",
                    "/api/generate/subtopics",
                    body={"topic": "Health & Wellness", "model": "ui-subtopic-model", "limit": 1},
                )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            subtopic_run_id = payload["run_id"]
            self.assertIn("Generated subtopic suggestions for 1 video(s)", payload["message"])

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={subtopic_run_id}&topic=Health+%26+Wellness",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertIn("ui-subtopic-model", payload["current_run"]["model_name"])
            self.assertEqual(payload["current_run"]["scope_label"], "Subtopic suggestions · Health & Wellness")
            self.assertEqual(payload["subtopic_reviews"]["selected_topic"], "Health & Wellness")
            self.assertEqual(payload["subtopic_reviews"]["eligible_video_count"], 2)
            self.assertEqual(payload["subtopic_reviews"]["summary"]["pending"], 1)
            self.assertEqual(payload["subtopic_reviews"]["pending"][0]["name"], "Sleep Timing")

    def test_review_ui_exposes_comparison_group_state_and_review_mutations(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui-comparison.sqlite3"
            comparison_run_id = self._seed_comparison_review_db(db_path)
            app = build_review_app(db_path)

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={comparison_run_id}&topic=Health+%26+Wellness&subtopic=Sleep",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["current_run"]["scope_label"], "Comparison groups · Sleep")
            self.assertEqual(payload["comparison_reviews"]["selected_subtopic"], "Sleep")
            self.assertEqual(payload["comparison_reviews"]["eligible_video_count"], 2)
            self.assertEqual(payload["comparison_reviews"]["summary"]["pending"], 2)
            self.assertEqual(
                [item["name"] for item in payload["comparison_reviews"]["pending"]],
                ["Sleep Routines", "Wind Down Rituals"],
            )
            self.assertEqual(payload["comparison_reviews"]["approved_groups"][0]["name"], "Sleep Routines")

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/comparison-group/reject",
                body={
                    "run_id": comparison_run_id,
                    "subtopic": "Sleep",
                    "label": "Sleep Routines",
                },
            )
            self.assertEqual(status, "200 OK")

            status, _, body = self._call_app(
                app,
                "POST",
                "/api/comparison-group/rename",
                body={
                    "run_id": comparison_run_id,
                    "subtopic": "Sleep",
                    "current_name": "Wind Down Rituals",
                    "new_name": "Bedtime Rituals",
                },
            )
            self.assertEqual(status, "200 OK")
            status, _, body = self._call_app(
                app,
                "POST",
                "/api/comparison-group/approve",
                body={
                    "run_id": comparison_run_id,
                    "subtopic": "Sleep",
                    "label": "Bedtime Rituals",
                    "approved_name": "Sleep Routines",
                },
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertIn("Approved comparison-group label 'Bedtime Rituals' as 'Sleep Routines'", payload["message"])

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={comparison_run_id}&topic=Health+%26+Wellness&subtopic=Sleep",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["comparison_reviews"]["summary"]["pending"], 0)
            self.assertEqual(payload["comparison_reviews"]["summary"]["approved"], 1)
            self.assertEqual(payload["comparison_reviews"]["summary"]["rejected"], 0)
            self.assertEqual([row["name"] for row in list_comparison_groups(db_path, subtopic_name="Sleep")], ["Sleep Routines"])

    def test_review_ui_can_generate_comparison_group_suggestion_runs(self) -> None:
        from yt_channel_analyzer.review_ui import build_review_app

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-ui-comparison-generate.sqlite3"
            self._seed_comparison_review_db(db_path)
            app = build_review_app(db_path)

            with patch(
                "yt_channel_analyzer.review_ui.suggest_comparison_groups_for_video",
                side_effect=lambda **kwargs: VideoComparisonGroupSuggestion(
                    youtube_video_id=kwargs["youtube_video_id"],
                    video_title=kwargs["video_title"],
                    broad_topic=kwargs["broad_topic_name"],
                    subtopic=kwargs["subtopic_name"],
                    primary_comparison_group=SuggestedComparisonGroupLabel(
                        label="Sleep Routines",
                        reuse_existing=True,
                        rationale="Generated in UI test.",
                    ),
                    raw_response_json="{}",
                ),
            ):
                status, _, body = self._call_app(
                    app,
                    "POST",
                    "/api/generate/comparison-groups",
                    body={
                        "topic": "Health & Wellness",
                        "subtopic": "Sleep",
                        "model": "ui-comparison-model",
                        "limit": 1,
                    },
                )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            comparison_run_id = payload["run_id"]
            self.assertIn("Generated comparison-group suggestions for 1 video(s)", payload["message"])

            status, _, body = self._call_app(
                app,
                "GET",
                "/api/state",
                query=f"run_id={comparison_run_id}&topic=Health+%26+Wellness&subtopic=Sleep",
            )
            self.assertEqual(status, "200 OK")
            payload = json.loads(body)
            self.assertEqual(payload["current_run"]["scope_label"], "Comparison groups · Sleep")
            self.assertEqual(payload["current_run"]["model_name"], "comparison-group:Sleep:ui-comparison-model")
            self.assertEqual(payload["comparison_reviews"]["summary"]["pending"], 1)
            self.assertEqual(payload["comparison_reviews"]["pending"][0]["name"], "Sleep Routines")
