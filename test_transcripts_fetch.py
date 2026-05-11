from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from yt_channel_analyzer.cli import (
    _resolve_fetch_transcript_video_ids,
    main,
    run_fetch_transcripts,
)
from yt_channel_analyzer.db import (
    connect,
    init_db,
    upsert_video_transcript,
    upsert_videos_for_primary_channel,
)
from yt_channel_analyzer.youtube import TranscriptRecord, VideoMetadata


def _seed_channel(db_path: Path, video_specs: list[tuple[str, str]]) -> None:
    """video_specs: (youtube_video_id, published_at) — order doesn't matter; the
    newest published_at sorts first in selector output."""
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
                youtube_video_id=vid,
                title=f"Video {vid}",
                description=None,
                published_at=published_at,
                thumbnail_url=None,
            )
            for vid, published_at in video_specs
        ],
    )


def _transcript_status(db_path: Path, youtube_video_id: str) -> str | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT video_transcripts.transcript_status
            FROM video_transcripts
            JOIN videos ON videos.id = video_transcripts.video_id
            WHERE videos.youtube_video_id = ?
            """,
            (youtube_video_id,),
        ).fetchone()
    return row[0] if row else None


def _transcript_text(db_path: Path, youtube_video_id: str) -> str | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT video_transcripts.transcript_text
            FROM video_transcripts
            JOIN videos ON videos.id = video_transcripts.video_id
            WHERE videos.youtube_video_id = ?
            """,
            (youtube_video_id,),
        ).fetchone()
    return row[0] if row else None


class _RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _args(**kwargs):
    import argparse

    defaults = {"video_ids": None, "missing_only": False, "limit": None, "refinement_run_id": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class SelectorResolutionTests(unittest.TestCase):
    def test_missing_only_includes_unfetched_and_retryable_skips_available(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(
                db_path,
                [
                    ("a", "2026-01-01T00:00:00Z"),  # available -> skipped
                    ("b", "2026-02-01T00:00:00Z"),  # request_failed -> retryable
                    ("c", "2026-03-01T00:00:00Z"),  # unfetched
                ],
            )
            upsert_video_transcript(
                db_path,
                youtube_video_id="a",
                transcript=TranscriptRecord(status="available", source="manual", language_code="en", text="hi"),
            )
            upsert_video_transcript(
                db_path,
                youtube_video_id="b",
                transcript=TranscriptRecord(status="request_failed", source=None, language_code=None, text=None),
            )
            ids = _resolve_fetch_transcript_video_ids(db_path, _args(missing_only=True))
            # newest-published first: c (Mar) then b (Feb); a excluded.
            self.assertEqual(ids, ["c", "b"])

    def test_limit_takes_n_most_recent_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(
                db_path,
                [
                    ("old", "2026-01-01T00:00:00Z"),
                    ("mid", "2026-02-01T00:00:00Z"),
                    ("new", "2026-03-01T00:00:00Z"),
                ],
            )
            self.assertEqual(_resolve_fetch_transcript_video_ids(db_path, _args(limit=2)), ["new", "mid"])

    def test_limit_must_be_positive(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            with self.assertRaises(ValueError):
                _resolve_fetch_transcript_video_ids(db_path, _args(limit=0))

    def test_video_ids_resolves_listed_and_dedups(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z"), ("b", "2026-02-01T00:00:00Z")])
            ids = _resolve_fetch_transcript_video_ids(db_path, _args(video_ids="b, a , b"))
            self.assertEqual(ids, ["b", "a"])

    def test_video_ids_rejects_non_primary_channel_id(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            with self.assertRaises(ValueError):
                _resolve_fetch_transcript_video_ids(db_path, _args(video_ids="a,nope"))

    def test_video_ids_rejects_empty(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            with self.assertRaises(ValueError):
                _resolve_fetch_transcript_video_ids(db_path, _args(video_ids="  , "))

    def test_refinement_run_id_not_yet_available(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            with self.assertRaises(ValueError):
                _resolve_fetch_transcript_video_ids(db_path, _args(refinement_run_id=7))


class FetchLoopTests(unittest.TestCase):
    def test_persists_each_result_and_returns_tally(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z"), ("b", "2026-02-01T00:00:00Z")])

            def fetcher(video_id: str) -> TranscriptRecord:
                if video_id == "a":
                    return TranscriptRecord(status="available", source="generated", language_code="en", text="hi a")
                return TranscriptRecord(status="not_found", source=None, language_code=None, text=None)

            sleep = _RecordingSleep()
            tally = run_fetch_transcripts(
                db_path, ["a", "b"], transcript_fetcher=fetcher, sleep=sleep, out=lambda *_: None
            )
            self.assertEqual(dict(tally), {"available": 1, "not_found": 1})
            self.assertEqual(_transcript_status(db_path, "a"), "available")
            self.assertEqual(_transcript_text(db_path, "a"), "hi a")
            self.assertEqual(_transcript_status(db_path, "b"), "not_found")
            # one inter-request sleep between the two videos, no backoff
            self.assertEqual(len(sleep.calls), 1)

    def test_rate_limited_triggers_backoff_then_retries(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])

            calls = {"n": 0}

            def fetcher(video_id: str) -> TranscriptRecord:
                calls["n"] += 1
                if calls["n"] == 1:
                    return TranscriptRecord(status="rate_limited", source=None, language_code=None, text=None)
                return TranscriptRecord(status="available", source="generated", language_code="en", text="ok")

            sleep = _RecordingSleep()
            tally = run_fetch_transcripts(
                db_path, ["a"], transcript_fetcher=fetcher, sleep=sleep, base_backoff=2.0, out=lambda *_: None
            )
            self.assertEqual(dict(tally), {"available": 1})
            self.assertEqual(calls["n"], 2)
            self.assertEqual(_transcript_status(db_path, "a"), "available")
            # exactly one backoff sleep (no inter-request sleep — single video)
            self.assertEqual(sleep.calls, [2.0])

    def test_rate_limited_gives_up_after_max_retries(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])

            def fetcher(video_id: str) -> TranscriptRecord:
                return TranscriptRecord(status="rate_limited", source=None, language_code=None, text=None)

            sleep = _RecordingSleep()
            tally = run_fetch_transcripts(
                db_path,
                ["a"],
                transcript_fetcher=fetcher,
                sleep=sleep,
                max_rate_limit_retries=3,
                base_backoff=1.0,
                max_backoff=4.0,
                out=lambda *_: None,
            )
            self.assertEqual(dict(tally), {"rate_limited": 1})
            self.assertEqual(_transcript_status(db_path, "a"), "rate_limited")
            # 3 capped-exponential backoffs: 1, 2, 4
            self.assertEqual(sleep.calls, [1.0, 2.0, 4.0])

    def test_resume_with_missing_only_picks_up_where_it_left_off(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(
                db_path,
                [("a", "2026-01-01T00:00:00Z"), ("b", "2026-02-01T00:00:00Z"), ("c", "2026-03-01T00:00:00Z")],
            )

            # First pass: a + b succeed, c fails.
            def first_fetcher(video_id: str) -> TranscriptRecord:
                if video_id == "c":
                    return TranscriptRecord(status="request_failed", source=None, language_code=None, text=None)
                return TranscriptRecord(status="available", source="generated", language_code="en", text=f"t{video_id}")

            ids = _resolve_fetch_transcript_video_ids(db_path, _args(missing_only=True))
            run_fetch_transcripts(db_path, ids, transcript_fetcher=first_fetcher, sleep=lambda *_: None, out=lambda *_: None)
            self.assertEqual(_transcript_status(db_path, "c"), "request_failed")

            # Resume: only c is still outstanding.
            remaining = _resolve_fetch_transcript_video_ids(db_path, _args(missing_only=True))
            self.assertEqual(remaining, ["c"])
            seen: list[str] = []

            def second_fetcher(video_id: str) -> TranscriptRecord:
                seen.append(video_id)
                return TranscriptRecord(status="available", source="generated", language_code="en", text="tc")

            run_fetch_transcripts(db_path, remaining, transcript_fetcher=second_fetcher, sleep=lambda *_: None, out=lambda *_: None)
            self.assertEqual(seen, ["c"])  # no duplicate work
            self.assertEqual(_transcript_status(db_path, "c"), "available")


class CliEndToEndTests(unittest.TestCase):
    def test_stub_populates_available_rows_without_network(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z"), ("b", "2026-02-01T00:00:00Z")])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["fetch-transcripts", "--db-path", str(db_path), "--missing-only", "--stub"])
            self.assertEqual(rc, 0)
            self.assertEqual(_transcript_status(db_path, "a"), "available")
            self.assertEqual(_transcript_status(db_path, "b"), "available")
            self.assertEqual(_transcript_text(db_path, "a"), "<stub transcript for a>")
            out = buf.getvalue()
            self.assertIn("available: 2", out)
            self.assertIn("a | available | generated | en", out)

    def test_stub_is_idempotent_skips_already_available(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            with redirect_stdout(io.StringIO()):
                main(["fetch-transcripts", "--db-path", str(db_path), "--missing-only", "--stub"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["fetch-transcripts", "--db-path", str(db_path), "--missing-only", "--stub"])
            self.assertEqual(rc, 0)
            self.assertIn("Nothing to fetch", buf.getvalue())

    def test_video_ids_selector_via_cli(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z"), ("b", "2026-02-01T00:00:00Z")])
            with redirect_stdout(io.StringIO()):
                rc = main(["fetch-transcripts", "--db-path", str(db_path), "--video-ids", "a", "--stub"])
            self.assertEqual(rc, 0)
            self.assertEqual(_transcript_status(db_path, "a"), "available")
            self.assertIsNone(_transcript_status(db_path, "b"))

    def test_no_selector_errors(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
                main(["fetch-transcripts", "--db-path", str(db_path)])
            self.assertEqual(ctx.exception.code, 2)

    def test_two_selectors_errors(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
                main(["fetch-transcripts", "--db-path", str(db_path), "--missing-only", "--limit", "1"])
            self.assertEqual(ctx.exception.code, 2)

    def test_unknown_video_id_errors_cleanly(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "t.sqlite3"
            _seed_channel(db_path, [("a", "2026-01-01T00:00:00Z")])
            buf = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main(["fetch-transcripts", "--db-path", str(db_path), "--video-ids", "zzz", "--stub"])
            self.assertEqual(rc, 2)
            self.assertIn("primary channel", err.getvalue())


if __name__ == "__main__":
    unittest.main()
