"""Phase B slice 3: ``refinement.py`` core + ``refine`` CLI.

Covers the sample picker (2/3 coverage / 1/3 blind-spot split, one-per-topic
round-robin, blind-spot ordering, ``sample=`` bypass), the one replacement
round for dead transcripts, the run lifecycle + persistence (proposals,
replace-wholesale refine assignments, wrong-assignment suppression), the
error path, the ``extractor/``-backed adapter (audit rows + correlation id),
the ``RALPH_ALLOW_REAL_LLM`` gate, and the ``refine --stub`` CLI. No real LLM
or network in the gate — ``stub_refinement_llm`` + a fake transcript fetcher.
"""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from yt_channel_analyzer import cli, db
from yt_channel_analyzer.extractor.fake import FakeLLMRunner
from yt_channel_analyzer.extractor.runner import Extractor
from yt_channel_analyzer.refinement import (
    REFINEMENT_PROMPT_NAME,
    REFINEMENT_PROMPT_VERSION,
    allocate_refinement_run,
    make_real_refinement_llm_callable,
    refinement_llm_via_extractor,
    register_refinement_prompt,
    run_refinement,
    select_refinement_sample,
    stub_refinement_llm,
)
from yt_channel_analyzer.youtube import TranscriptRecord


_QUIET = lambda *_args, **_kwargs: None  # noqa: E731 — test sink for ``out``


def _fresh_db(tmp: str) -> Path:
    db_path = Path(tmp) / "t.sqlite3"
    with db.connect(db_path) as conn:
        db.ensure_schema(conn)
        conn.commit()
    return db_path


def _seed(db_path: Path, videos: list[tuple[str, int | None, list[tuple[str, float]]]]) -> dict:
    """Seed one project / primary channel / discovery run.

    ``videos`` is ``[(youtube_id, duration_seconds_or_None, [(topic_name, confidence), ...]), ...]``;
    topics are created on first reference and every ``(topic, confidence)`` pair
    becomes an ``'auto'`` ``video_topics`` row in the discovery run.
    """
    with db.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project_id = conn.execute("INSERT INTO projects(name) VALUES ('proj')").lastrowid
        channel_id = conn.execute(
            "INSERT INTO channels(project_id, youtube_channel_id, title, is_primary) VALUES (?, 'UC1', 'Chan', 1)",
            (project_id,),
        ).lastrowid
        discovery_run_id = conn.execute(
            "INSERT INTO discovery_runs(channel_id, model, prompt_version, status) VALUES (?, 'stub', 'v1', 'success')",
            (channel_id,),
        ).lastrowid
        topic_ids: dict[str, int] = {}

        def topic_id(name: str) -> int:
            if name not in topic_ids:
                topic_ids[name] = conn.execute(
                    "INSERT INTO topics(project_id, name, first_discovery_run_id) VALUES (?, ?, ?)",
                    (project_id, name, discovery_run_id),
                ).lastrowid
            return topic_ids[name]

        vid_by_yt: dict[str, int] = {}
        for youtube_id, duration, assigns in videos:
            video_id = conn.execute(
                "INSERT INTO videos(channel_id, youtube_video_id, title, duration_seconds) VALUES (?, ?, ?, ?)",
                (channel_id, youtube_id, youtube_id.upper(), duration),
            ).lastrowid
            vid_by_yt[youtube_id] = video_id
            for topic_name, confidence in assigns:
                conn.execute(
                    "INSERT INTO video_topics(video_id, topic_id, assignment_type, assignment_source, "
                    "confidence, reason, discovery_run_id) VALUES (?, ?, 'secondary', 'auto', ?, 'r', ?)",
                    (video_id, topic_id(topic_name), confidence, discovery_run_id),
                )
        conn.commit()
    return {
        "project_id": project_id,
        "channel_id": channel_id,
        "discovery_run_id": discovery_run_id,
        "vid_by_yt": vid_by_yt,
        "topic_ids": topic_ids,
    }


def _fetcher(unavailable: tuple[str, ...] = ()):
    bad = set(unavailable)

    def fetch(video_id: str) -> TranscriptRecord:
        if video_id in bad:
            return TranscriptRecord(status="not_found", source=None, language_code=None, text=None)
        return TranscriptRecord(
            status="available", source="generated", language_code="en", text=f"transcript for {video_id}"
        )

    return fetch


def _query(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with db.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


class StubRunTests(unittest.TestCase):
    def test_stub_run_persists_run_episodes_proposals_and_refine_assignments(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(
                db_path,
                [
                    ("a1", 600, [("Health", 0.5)]),
                    ("a2", 600, [("Health", 0.6)]),
                    ("a3", 600, [("Health", 0.7)]),
                ],
            )
            result = run_refinement(
                db_path,
                project_name="proj",
                llm=stub_refinement_llm,
                transcript_fetcher=_fetcher(),
                sample_size=15,
                out=_QUIET,
            )
            self.assertEqual(result.status, "success")

            runs = _query(db_path, "SELECT id, status, n_sample FROM refinement_runs")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "success")
            self.assertEqual(runs[0]["n_sample"], 3)
            self.assertEqual(runs[0]["id"], result.run_id)

            episodes = _query(
                db_path, "SELECT video_id, transcript_status_at_run FROM refinement_episodes WHERE refinement_run_id = ?", (result.run_id,)
            )
            self.assertEqual(len(episodes), 3)
            self.assertTrue(all(row["transcript_status_at_run"] == "available" for row in episodes))

            proposals = _query(db_path, "SELECT kind, name, status FROM taxonomy_proposals ORDER BY id")
            self.assertEqual(sum(1 for p in proposals if p["kind"] == "subtopic"), 3)
            topic_proposals = [p for p in proposals if p["kind"] == "topic"]
            self.assertEqual(len(topic_proposals), 1)
            self.assertEqual(topic_proposals[0]["name"], "Stub topic")
            self.assertTrue(all(p["status"] == "pending" for p in proposals))

            refine_rows = _query(
                db_path, "SELECT video_id, assignment_source, refinement_run_id FROM video_topics WHERE assignment_source = 'refine'"
            )
            self.assertEqual(len(refine_rows), 3)
            self.assertTrue(all(r["refinement_run_id"] == result.run_id for r in refine_rows))

    def test_second_run_is_non_destructive_to_the_first(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.5)]), ("a2", 600, [("Health", 0.6)])])
            first = run_refinement(db_path, project_name="proj", transcript_fetcher=_fetcher(), out=_QUIET)
            second = run_refinement(db_path, project_name="proj", transcript_fetcher=_fetcher(), out=_QUIET)
            self.assertNotEqual(first.run_id, second.run_id)
            runs = _query(db_path, "SELECT id, status FROM refinement_runs ORDER BY id")
            self.assertEqual(len(runs), 2)
            self.assertTrue(all(r["status"] == "success" for r in runs))
            # Run 1's episodes survive untouched.
            self.assertEqual(
                len(_query(db_path, "SELECT 1 FROM refinement_episodes WHERE refinement_run_id = ?", (first.run_id,))),
                2,
            )


class PickerTests(unittest.TestCase):
    def test_two_thirds_coverage_round_robin_then_blind_spot_ordering(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(
                db_path,
                [
                    ("a1", 600, [("A", 0.9)]),
                    ("a2", 600, [("A", 0.8)]),
                    ("a3", 600, [("A", 0.7)]),
                    ("b1", 600, [("B", 0.6)]),
                    ("b2", 600, [("B", 0.5)]),
                    ("c1", 600, [("C", 0.4)]),
                    ("u1", 600, []),
                    ("u2", 600, []),
                ],
            )
            rev = {v: k for k, v in ids["vid_by_yt"].items()}
            with db.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                picked, remaining = select_refinement_sample(
                    conn, channel_id=ids["channel_id"], discovery_run_id=ids["discovery_run_id"], sample_size=6
                )
            # n_coverage = 4: A,B,C each get one, then A gets a second.
            # remainder = 2: lowest-confidence assigned first (b2 0.5 then a3 0.7);
            # the unassigned bucket (u1, u2) sorts last and is the leftover pool.
            self.assertEqual([rev[v] for v in picked], ["a1", "b1", "c1", "a2", "b2", "a3"])
            self.assertEqual([rev[v] for v in remaining], ["u1", "u2"])

    def test_topics_with_no_pool_members_are_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(
                db_path,
                [
                    ("a1", 600, [("A", 0.9)]),
                    # A Short assigned to topic B — B contributes nothing fetchable.
                    ("s1", 10, [("B", 0.9)]),
                ],
            )
            rev = {v: k for k, v in ids["vid_by_yt"].items()}
            with db.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                picked, _ = select_refinement_sample(
                    conn, channel_id=ids["channel_id"], discovery_run_id=ids["discovery_run_id"], sample_size=15
                )
            self.assertEqual([rev[v] for v in picked], ["a1"])


class SampleArgTests(unittest.TestCase):
    def test_explicit_sample_bypasses_the_picker(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(
                db_path,
                [
                    ("a1", 600, [("Health", 0.5)]),
                    ("a2", 600, [("Health", 0.6)]),
                    ("a3", 600, [("Health", 0.7)]),
                ],
            )
            result = run_refinement(
                db_path,
                project_name="proj",
                llm=stub_refinement_llm,
                sample=["a2", "a1"],
                transcript_fetcher=_fetcher(),
                out=_QUIET,
            )
            self.assertEqual(result.sampled_youtube_ids, ["a2", "a1"])
            episode_video_ids = {
                row["video_id"]
                for row in _query(db_path, "SELECT video_id FROM refinement_episodes WHERE refinement_run_id = ?", (result.run_id,))
            }
            self.assertEqual(episode_video_ids, {ids["vid_by_yt"]["a1"], ids["vid_by_yt"]["a2"]})

    def test_unknown_sample_id_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.5)])])
            with self.assertRaises(ValueError):
                run_refinement(db_path, project_name="proj", sample=["nope"], transcript_fetcher=_fetcher(), out=_QUIET)


class ReplacementRoundTests(unittest.TestCase):
    def test_dead_transcript_is_dropped_and_replaced_once(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(
                db_path,
                [
                    ("a1", 600, [("A", 0.9)]),
                    ("a2", 600, [("A", 0.8)]),
                    ("a3", 600, [("A", 0.7)]),
                    ("a4", 600, [("A", 0.6)]),
                ],
            )
            # Picker (sample_size=3): coverage = a1, a2; blind-spot = a4 (lower
            # confidence than a3); a3 is the leftover pool used for replacement.
            result = run_refinement(
                db_path,
                project_name="proj",
                llm=stub_refinement_llm,
                transcript_fetcher=_fetcher(unavailable=("a4",)),
                sample_size=3,
                out=_QUIET,
            )
            self.assertEqual(result.status, "success")
            self.assertEqual(result.sampled_youtube_ids, ["a1", "a2", "a3"])
            episode_video_ids = {
                row["video_id"]
                for row in _query(db_path, "SELECT video_id FROM refinement_episodes WHERE refinement_run_id = ?", (result.run_id,))
            }
            self.assertEqual(episode_video_ids, {ids["vid_by_yt"][y] for y in ("a1", "a2", "a3")})
            self.assertNotIn(ids["vid_by_yt"]["a4"], episode_video_ids)
            a4_status = _query(
                db_path, "SELECT transcript_status FROM video_transcripts WHERE video_id = ?", (ids["vid_by_yt"]["a4"],)
            )
            self.assertEqual(a4_status[0]["transcript_status"], "not_found")


class PersistenceEdgeTests(unittest.TestCase):
    def test_wrong_marked_assignment_is_not_re_added_by_refine(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(db_path, [("a1", 600, [("Health", 0.9)])])
            with db.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO wrong_assignments(video_id, topic_id) VALUES (?, ?)",
                    (ids["vid_by_yt"]["a1"], ids["topic_ids"]["Health"]),
                )
                conn.commit()
            result = run_refinement(
                db_path,
                project_name="proj",
                llm=stub_refinement_llm,
                sample=["a1"],
                transcript_fetcher=_fetcher(),
                out=_QUIET,
            )
            self.assertEqual(result.status, "success")
            # The 'auto' row was deleted by replace-wholesale; the echoed
            # 'refine' row was suppressed by the wrong-mark.
            self.assertEqual(
                _query(db_path, "SELECT COUNT(*) AS n FROM video_topics WHERE video_id = ?", (ids["vid_by_yt"]["a1"],))[0]["n"],
                0,
            )
            self.assertGreaterEqual(result.reassignments[0]["suppressed"], 1)

    def test_llm_failure_marks_run_error_and_persists_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(db_path, [("a1", 600, [("Health", 0.9)])])

            def boom(_episodes, _taxonomy, **_kwargs):
                raise RuntimeError("synthetic LLM failure")

            with self.assertRaises(RuntimeError):
                run_refinement(
                    db_path,
                    project_name="proj",
                    llm=boom,
                    sample=["a1"],
                    transcript_fetcher=_fetcher(),
                    out=_QUIET,
                )
            runs = _query(db_path, "SELECT status FROM refinement_runs")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "error")
            self.assertEqual(_query(db_path, "SELECT COUNT(*) AS n FROM taxonomy_proposals")[0]["n"], 0)
            # No refine rows; the original 'auto' assignment is restored by rollback.
            self.assertEqual(_query(db_path, "SELECT COUNT(*) AS n FROM video_topics WHERE assignment_source = 'refine'")[0]["n"], 0)
            self.assertEqual(
                _query(db_path, "SELECT assignment_source FROM video_topics WHERE video_id = ?", (ids["vid_by_yt"]["a1"],))[0]["assignment_source"],
                "auto",
            )

    def test_cost_confirm_declined_leaves_run_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            ids = _seed(db_path, [("a1", 600, [("Health", 0.9)])])
            result = run_refinement(
                db_path,
                project_name="proj",
                llm=stub_refinement_llm,
                sample=["a1"],
                transcript_fetcher=_fetcher(),
                confirm=lambda _info: False,
                out=_QUIET,
            )
            self.assertEqual(result.status, "pending")
            self.assertEqual(_query(db_path, "SELECT status FROM refinement_runs")[0]["status"], "pending")
            self.assertEqual(_query(db_path, "SELECT COUNT(*) AS n FROM taxonomy_proposals")[0]["n"], 0)
            self.assertEqual(
                _query(db_path, "SELECT assignment_source FROM video_topics WHERE video_id = ?", (ids["vid_by_yt"]["a1"],))[0]["assignment_source"],
                "auto",
            )


class ExtractorAdapterTests(unittest.TestCase):
    def test_runs_through_extractor_with_audit_rows_and_correlation_id(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.9)])])
            register_refinement_prompt()
            runner = FakeLLMRunner()
            runner.add_response(
                REFINEMENT_PROMPT_NAME,
                REFINEMENT_PROMPT_VERSION,
                {
                    "assignments": [{"topic": "Health", "confidence": 0.95, "reason": "transcript discusses health at length"}],
                    "new_subtopic_proposals": [],
                    "new_topic_proposals": [],
                },
            )
            extractor_conn = db.connect(db_path)
            try:
                llm = refinement_llm_via_extractor(Extractor(connection=extractor_conn, runner=runner))
                result = run_refinement(
                    db_path,
                    project_name="proj",
                    llm=llm,
                    sample=["a1"],
                    transcript_fetcher=_fetcher(),
                    model="fake-model",
                    out=_QUIET,
                )
            finally:
                extractor_conn.close()
            self.assertEqual(result.status, "success")
            calls = _query(db_path, "SELECT prompt_name, prompt_version, parse_status, correlation_id FROM llm_calls")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["prompt_name"], REFINEMENT_PROMPT_NAME)
            self.assertEqual(calls[0]["prompt_version"], REFINEMENT_PROMPT_VERSION)
            self.assertEqual(calls[0]["parse_status"], "ok")
            self.assertEqual(calls[0]["correlation_id"], result.run_id)

    def test_real_llm_callable_is_gated(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            with mock.patch.dict(os.environ):
                os.environ.pop("RALPH_ALLOW_REAL_LLM", None)
                with db.connect(db_path) as conn:
                    with self.assertRaises(RuntimeError):
                        make_real_refinement_llm_callable(conn)


class ProceedShortTests(unittest.TestCase):
    def test_pool_smaller_than_sample_size_proceeds_with_a_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.5)]), ("a2", 600, [("Health", 0.6)])])
            messages: list[str] = []
            result = run_refinement(
                db_path,
                project_name="proj",
                llm=stub_refinement_llm,
                transcript_fetcher=_fetcher(),
                sample_size=15,
                out=messages.append,
            )
            self.assertEqual(result.status, "success")
            self.assertTrue(any("proceeding short" in m for m in messages))
            self.assertEqual(len(_query(db_path, "SELECT 1 FROM refinement_episodes WHERE refinement_run_id = ?", (result.run_id,))), 2)


class RefineCliTests(unittest.TestCase):
    def test_refine_stub_cli_creates_a_successful_run(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.5)]), ("a2", 600, [("Health", 0.6)])])
            exit_code = cli.main(["refine", "--db-path", str(db_path), "--project-name", "proj", "--stub"])
            self.assertEqual(exit_code, 0)
            runs = _query(db_path, "SELECT status FROM refinement_runs")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "success")

    def test_refine_requires_stub_or_real(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.5)])])
            with self.assertRaises(SystemExit):
                cli.main(["refine", "--db-path", str(db_path), "--project-name", "proj"])


class DescribeRefinementSampleTests(unittest.TestCase):
    def test_describes_picked_sample_with_slots_topics_and_pool_size(self) -> None:
        from yt_channel_analyzer.refinement import describe_refinement_sample

        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(
                db_path,
                [
                    ("a1", 600, [("A", 0.9)]),
                    ("a2", 600, [("A", 0.8)]),
                    ("a3", 600, [("A", 0.7)]),
                    ("b1", 600, [("B", 0.6)]),
                    ("b2", 600, [("B", 0.5)]),
                    ("c1", 600, [("C", 0.4)]),
                    ("u1", 600, []),
                    ("u2", 600, []),
                ],
            )
            db.upsert_video_transcript(
                db_path,
                youtube_video_id="a1",
                transcript=TranscriptRecord(
                    status="available", source="generated", language_code="en", text="t"
                ),
            )
            result = describe_refinement_sample(db_path, project_name="proj", sample_size=6)
            self.assertEqual(result["pool_size"], 8)
            self.assertIsNotNone(result["discovery_run_id"])
            episodes = result["episodes"]
            self.assertEqual(
                [e["youtube_video_id"] for e in episodes],
                ["a1", "b1", "c1", "a2", "b2", "a3"],
            )
            kinds = {e["youtube_video_id"]: e["slot_kind"] for e in episodes}
            self.assertEqual(kinds["a1"], "coverage")
            self.assertEqual(kinds["b2"], "blind_spot")
            a1 = next(e for e in episodes if e["youtube_video_id"] == "a1")
            self.assertEqual(a1["topic"], "A")
            self.assertEqual(a1["confidence"], 0.9)
            self.assertEqual(a1["title"], "A1")
            self.assertEqual(a1["transcript_status"], "available")

    def test_unknown_discovery_run_id_raises(self) -> None:
        from yt_channel_analyzer.refinement import describe_refinement_sample

        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("A", 0.9)])])
            with self.assertRaises(ValueError):
                describe_refinement_sample(
                    db_path, project_name="proj", discovery_run_id=999
                )


class PreAllocatedRunTests(unittest.TestCase):
    def test_run_id_updates_pre_allocated_row_instead_of_inserting(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.5)]), ("a2", 600, [("Health", 0.6)])])
            run_id = allocate_refinement_run(db_path, project_name="proj")
            pending = _query(
                db_path, "SELECT status, n_sample, discovery_run_id FROM refinement_runs WHERE id = ?", (run_id,)
            )[0]
            self.assertEqual(pending["status"], "pending")
            self.assertIsNone(pending["n_sample"])

            result = run_refinement(
                db_path,
                project_name="proj",
                transcript_fetcher=_fetcher(),
                run_id=run_id,
                out=_QUIET,
            )
            self.assertEqual(result.run_id, run_id)
            self.assertEqual(result.status, "success")
            runs = _query(db_path, "SELECT id, status, n_sample, discovery_run_id FROM refinement_runs")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["id"], run_id)
            self.assertEqual(runs[0]["status"], "success")
            self.assertEqual(runs[0]["n_sample"], 2)
            self.assertIsNotNone(runs[0]["discovery_run_id"])

    def test_failure_flips_pre_allocated_row_to_error(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = _fresh_db(tmp)
            _seed(db_path, [("a1", 600, [("Health", 0.5)])])
            run_id = allocate_refinement_run(db_path, project_name="proj")
            with self.assertRaises(ValueError):
                run_refinement(
                    db_path,
                    project_name="proj",
                    sample=["not-a-video"],
                    transcript_fetcher=_fetcher(),
                    run_id=run_id,
                    out=_QUIET,
                )
            row = _query(
                db_path, "SELECT status, error_message FROM refinement_runs WHERE id = ?", (run_id,)
            )[0]
            self.assertEqual(row["status"], "error")
            self.assertIsNotNone(row["error_message"])


if __name__ == "__main__":
    unittest.main()
