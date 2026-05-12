"""Phase B slice 2: refinement schema + db helpers.

Covers the new tables (`refinement_runs`, `refinement_episodes`,
`taxonomy_proposals`), the `assignment_source='refine'` + `refinement_run_id`
junction-table change and its idempotent old-DB migration, the db helpers
(run lifecycle, sampled-episode recording, proposal insert/accept/reject with
parent-rename resolution, replace-wholesale refine-assignment writes), and the
widened review-UI topic-map query.
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from yt_channel_analyzer import db
from yt_channel_analyzer.review_ui import _build_discovery_topic_map


def _fresh_db(tmpdir: str) -> Path:
    db_path = Path(tmpdir) / "test.sqlite3"
    with db.connect(db_path) as conn:
        db.ensure_schema(conn)
        conn.commit()
    return db_path


def _seed_project(conn: sqlite3.Connection) -> dict[str, int]:
    """Minimal project/channel/video/discovery_run/topic graph."""
    project_id = conn.execute("INSERT INTO projects(name) VALUES ('proj')").lastrowid
    channel_id = conn.execute(
        "INSERT INTO channels(project_id, youtube_channel_id, title, is_primary) "
        "VALUES (?, 'UC1', 'Channel', 1)",
        (project_id,),
    ).lastrowid
    video_id = conn.execute(
        "INSERT INTO videos(channel_id, youtube_video_id, title) VALUES (?, 'vid1', 'Video 1')",
        (channel_id,),
    ).lastrowid
    video2_id = conn.execute(
        "INSERT INTO videos(channel_id, youtube_video_id, title) VALUES (?, 'vid2', 'Video 2')",
        (channel_id,),
    ).lastrowid
    discovery_run_id = conn.execute(
        "INSERT INTO discovery_runs(channel_id, model, prompt_version, status) "
        "VALUES (?, 'stub', 'v1', 'success')",
        (channel_id,),
    ).lastrowid
    topic_id = conn.execute(
        "INSERT INTO topics(project_id, name, first_discovery_run_id) VALUES (?, 'Health', ?)",
        (project_id, discovery_run_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO video_topics(video_id, topic_id, assignment_type, assignment_source, "
        "confidence, reason, discovery_run_id) VALUES (?, ?, 'secondary', 'auto', 0.5, 'r', ?)",
        (video_id, topic_id, discovery_run_id),
    )
    conn.execute(
        "INSERT INTO video_topics(video_id, topic_id, assignment_type, assignment_source, "
        "confidence, reason, discovery_run_id) VALUES (?, ?, 'secondary', 'auto', 0.6, 'r2', ?)",
        (video2_id, topic_id, discovery_run_id),
    )
    return {
        "project_id": project_id,
        "channel_id": channel_id,
        "video_id": video_id,
        "video2_id": video2_id,
        "discovery_run_id": discovery_run_id,
        "topic_id": topic_id,
    }


# Old-shape schema fragment: video_topics / video_subtopics without 'refine' in
# the CHECK and without refinement_run_id, plus enough of the rest to run
# ensure_schema. Mirrors the kind of DB that exists before this slice.
_OLD_SHAPE_SQL = """
CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, slug TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE channels(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, youtube_channel_id TEXT NOT NULL, title TEXT NOT NULL, is_primary INTEGER NOT NULL DEFAULT 0, exclude_shorts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE videos(id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, youtube_video_id TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE discovery_runs(id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running', error_message TEXT, raw_response TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, CHECK(status IN ('running','success','error')));
CREATE TABLE topics(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, first_discovery_run_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(project_id,name));
CREATE TABLE subtopics(id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(topic_id,name));
CREATE TABLE video_topics(video_id INTEGER NOT NULL, topic_id INTEGER NOT NULL, assignment_type TEXT NOT NULL DEFAULT 'secondary', assignment_source TEXT NOT NULL DEFAULT 'manual', confidence REAL, reason TEXT, discovery_run_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(video_id,topic_id), CHECK(assignment_type IN ('primary','secondary')), CHECK(assignment_source IN ('manual','import','suggested','auto')));
CREATE TABLE video_subtopics(video_id INTEGER NOT NULL, subtopic_id INTEGER NOT NULL, assignment_source TEXT NOT NULL DEFAULT 'manual', confidence REAL, reason TEXT, discovery_run_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(video_id,subtopic_id), CHECK(assignment_source IN ('manual','import','suggested','auto')));
INSERT INTO projects(name) VALUES ('proj');
INSERT INTO channels(project_id,youtube_channel_id,title,is_primary) VALUES (1,'UC1','Channel',1);
INSERT INTO videos(channel_id,youtube_video_id,title) VALUES (1,'vid1','Video 1');
INSERT INTO discovery_runs(channel_id,model,prompt_version,status) VALUES (1,'stub','v1','success');
INSERT INTO topics(project_id,name,first_discovery_run_id) VALUES (1,'Health',1);
INSERT INTO subtopics(topic_id,name) VALUES (1,'Sleep');
INSERT INTO video_topics(video_id,topic_id,assignment_type,assignment_source,confidence,reason,discovery_run_id) VALUES (1,1,'primary','auto',0.5,'r',1);
INSERT INTO video_subtopics(video_id,subtopic_id,assignment_source,confidence,reason,discovery_run_id) VALUES (1,1,'auto',0.5,'r',1);
"""


class RefinementSchemaTests(unittest.TestCase):
    def test_fresh_schema_has_refinement_tables_and_columns(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _fresh_db(tmpdir)
            with db.connect(db_path) as conn:
                for table in ("refinement_runs", "refinement_episodes", "taxonomy_proposals"):
                    self.assertTrue(db._table_exists(conn, table), table)
                self.assertIn("refinement_run_id", db._get_existing_columns(conn, "video_topics"))
                self.assertIn("refinement_run_id", db._get_existing_columns(conn, "video_subtopics"))
                self.assertIn(
                    "assignments_before_json",
                    db._get_existing_columns(conn, "refinement_episodes"),
                )
                vt_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'video_topics'"
                ).fetchone()[0]
                self.assertIn("'refine'", vt_sql)
                vs_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'video_subtopics'"
                ).fetchone()[0]
                self.assertIn("'refine'", vs_sql)

    def test_old_db_migrates_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "old.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.executescript("PRAGMA foreign_keys = ON;")
            conn.executescript(_OLD_SHAPE_SQL)
            conn.commit()

            db.ensure_schema(conn)
            conn.commit()

            # Migrated: 'refine' in CHECK, refinement_run_id present, new tables exist.
            vt_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'video_topics'"
            ).fetchone()[0]
            self.assertIn("'refine'", vt_sql)
            self.assertIn("refinement_run_id", db._get_existing_columns(conn, "video_topics"))
            self.assertIn("refinement_run_id", db._get_existing_columns(conn, "video_subtopics"))
            for table in ("refinement_runs", "refinement_episodes", "taxonomy_proposals"):
                self.assertTrue(db._table_exists(conn, table), table)
            # Existing rows preserved.
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM video_topics").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM video_subtopics").fetchone()[0], 1)
            row = conn.execute(
                "SELECT assignment_type, assignment_source, confidence, discovery_run_id "
                "FROM video_topics WHERE video_id = 1 AND topic_id = 1"
            ).fetchone()
            self.assertEqual(row, ("primary", "auto", 0.5, 1))

            # Idempotent: second/third ensure_schema is a no-op and loses nothing.
            db.ensure_schema(conn)
            db.ensure_schema(conn)
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM video_topics").fetchone()[0], 1)
            # 'refine' now usable.
            conn.execute(
                "INSERT INTO video_topics(video_id, topic_id, assignment_source, refinement_run_id) "
                "VALUES (1, 1, 'refine', NULL) ON CONFLICT(video_id, topic_id) DO UPDATE SET assignment_source = 'refine'"
            )
            conn.commit()
            conn.close()


class RefinementRunHelperTests(unittest.TestCase):
    def test_run_lifecycle_and_episode_recording(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _fresh_db(tmpdir)
            with db.connect(db_path) as conn:
                ids = _seed_project(conn)
                run_id = db.create_refinement_run(
                    conn,
                    channel_id=ids["channel_id"],
                    discovery_run_id=ids["discovery_run_id"],
                    model="haiku-4.5",
                    prompt_version="refine-v1",
                    n_sample=1,
                )
                status = conn.execute(
                    "SELECT status FROM refinement_runs WHERE id = ?", (run_id,)
                ).fetchone()[0]
                self.assertEqual(status, "pending")
                db.set_refinement_run_status(conn, run_id, "running")
                db.set_refinement_run_status(conn, run_id, "success")
                self.assertEqual(
                    conn.execute("SELECT status FROM refinement_runs WHERE id = ?", (run_id,)).fetchone()[0],
                    "success",
                )
                with self.assertRaises(ValueError):
                    db.set_refinement_run_status(conn, run_id, "bogus")

                # add_refinement_episodes is idempotent, updates the stored
                # status, records the before-snapshot, and a later status-only
                # call (no snapshot) keeps the prior snapshot (COALESCE).
                db.add_refinement_episodes(
                    conn, run_id, [(ids["video_id"], "available", '[{"topic": "Health"}]')]
                )
                db.add_refinement_episodes(conn, run_id, [(ids["video_id"], "unavailable")])
                rows = conn.execute(
                    "SELECT video_id, transcript_status_at_run, assignments_before_json "
                    "FROM refinement_episodes WHERE refinement_run_id = ?",
                    (run_id,),
                ).fetchall()
                self.assertEqual(
                    rows, [(ids["video_id"], "unavailable", '[{"topic": "Health"}]')]
                )
                conn.commit()

    def test_list_refinement_episode_changes_before_after(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _fresh_db(tmpdir)
            with db.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                ids = _seed_project(conn)
                run_id = db.create_refinement_run(
                    conn,
                    channel_id=ids["channel_id"],
                    discovery_run_id=ids["discovery_run_id"],
                    model="haiku-4.5",
                    prompt_version="refine-v1",
                    n_sample=1,
                )
                db.set_refinement_run_status(conn, run_id, "running")
                db.add_refinement_episodes(
                    conn,
                    run_id,
                    [(
                        ids["video_id"],
                        "available",
                        '[{"topic": "Health", "subtopic": null, "confidence": 0.5, "reason": "meta"}]',
                    )],
                )
                db.write_refine_assignments(
                    conn,
                    channel_id=ids["channel_id"],
                    refinement_run_id=run_id,
                    video_id=ids["video_id"],
                    assignments=[{
                        "topic_name": "Health",
                        "subtopic_name": "Deep Sleep",
                        "confidence": 0.95,
                        "reason": "transcript",
                    }],
                )
                db.set_refinement_run_status(conn, run_id, "success")
                conn.commit()
                changes = db.list_refinement_episode_changes(conn, ids["project_id"])
        self.assertEqual(len(changes), 1)
        run = changes[0]
        self.assertEqual(run["refinement_run_id"], run_id)
        self.assertEqual(len(run["episodes"]), 1)
        ep = run["episodes"][0]
        self.assertEqual(ep["youtube_video_id"], "vid1")
        self.assertEqual(
            ep["before"],
            [{"topic": "Health", "subtopic": None, "confidence": 0.5, "reason": "meta"}],
        )
        self.assertEqual(len(ep["after"]), 1)
        self.assertEqual(ep["after"][0]["topic"], "Health")
        self.assertEqual(ep["after"][0]["subtopic"], "Deep Sleep")
        self.assertEqual(ep["after"][0]["assignment_source"], "refine")

    def test_proposal_accept_creates_node_and_resolves_renamed_parent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _fresh_db(tmpdir)
            with db.connect(db_path) as conn:
                ids = _seed_project(conn)
                run_id = db.create_refinement_run(
                    conn,
                    channel_id=ids["channel_id"],
                    discovery_run_id=ids["discovery_run_id"],
                    model="haiku-4.5",
                    prompt_version="refine-v1",
                    n_sample=1,
                )
                # Rename the seeded topic Health -> Wellbeing.
                conn.execute(
                    "INSERT INTO topic_renames(project_id, topic_id, old_name, new_name) VALUES (?, ?, 'Health', 'Wellbeing')",
                    (ids["project_id"], ids["topic_id"]),
                )
                conn.execute("UPDATE topics SET name = 'Wellbeing' WHERE id = ?", (ids["topic_id"],))

                proposal_ids = db.insert_taxonomy_proposals(
                    conn,
                    run_id,
                    [
                        # parent named with the pre-rename name — must resolve through the log.
                        {"kind": "subtopic", "name": "Cold Plunges", "parent_topic_name": "Health", "evidence": "talks about ice baths", "source_video_id": ids["video_id"]},
                        {"kind": "topic", "name": "Productivity", "evidence": "whole episode on it"},
                    ],
                )
                self.assertEqual(len(proposal_ids), 2)

                sub_result = db.accept_taxonomy_proposal(conn, proposal_ids[0])
                self.assertEqual(sub_result["status"], "accepted")
                self.assertEqual(sub_result["parent_topic_name"], "Wellbeing")
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT id FROM subtopics WHERE topic_id = ? AND name = 'Cold Plunges'",
                        (ids["topic_id"],),
                    ).fetchone()
                )
                # Idempotent: accepting again is fine, no duplicate subtopic.
                db.accept_taxonomy_proposal(conn, proposal_ids[0])
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM subtopics WHERE topic_id = ? AND name = 'Cold Plunges'",
                        (ids["topic_id"],),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT status FROM taxonomy_proposals WHERE id = ?", (proposal_ids[0],)).fetchone()[0],
                    "accepted",
                )

                topic_result = db.accept_taxonomy_proposal(conn, proposal_ids[1])
                self.assertEqual(topic_result["status"], "accepted")
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT id FROM topics WHERE project_id = ? AND name = 'Productivity'",
                        (ids["project_id"],),
                    ).fetchone()
                )
                conn.commit()

    def test_proposal_accept_with_missing_parent_is_rejected(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _fresh_db(tmpdir)
            with db.connect(db_path) as conn:
                ids = _seed_project(conn)
                run_id = db.create_refinement_run(
                    conn,
                    channel_id=ids["channel_id"],
                    discovery_run_id=ids["discovery_run_id"],
                    model="haiku-4.5",
                    prompt_version="refine-v1",
                    n_sample=1,
                )
                (proposal_id,) = db.insert_taxonomy_proposals(
                    conn, run_id, [{"kind": "subtopic", "name": "Orphan", "parent_topic_name": "No Such Topic"}]
                )
                result = db.accept_taxonomy_proposal(conn, proposal_id)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["reason"], "parent_topic_missing")
                self.assertEqual(
                    conn.execute("SELECT status FROM taxonomy_proposals WHERE id = ?", (proposal_id,)).fetchone()[0],
                    "rejected",
                )
                # Explicit reject path.
                (p2,) = db.insert_taxonomy_proposals(conn, run_id, [{"kind": "topic", "name": "Maybe Not"}])
                db.reject_taxonomy_proposal(conn, p2)
                self.assertEqual(
                    conn.execute("SELECT status FROM taxonomy_proposals WHERE id = ?", (p2,)).fetchone()[0],
                    "rejected",
                )
                with self.assertRaises(ValueError):
                    db.reject_taxonomy_proposal(conn, 99999)
                conn.commit()

    def test_write_refine_assignments_replaces_wholesale_and_respects_wrong_marks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _fresh_db(tmpdir)
            with db.connect(db_path) as conn:
                ids = _seed_project(conn)
                run_id = db.create_refinement_run(
                    conn,
                    channel_id=ids["channel_id"],
                    discovery_run_id=ids["discovery_run_id"],
                    model="haiku-4.5",
                    prompt_version="refine-v1",
                    n_sample=1,
                )
                # A second topic, assigned MANUALLY — must survive the wholesale replace.
                manual_topic_id = conn.execute(
                    "INSERT INTO topics(project_id, name) VALUES (?, 'Curated Topic')", (ids["project_id"],)
                ).lastrowid
                conn.execute(
                    "INSERT INTO video_topics(video_id, topic_id, assignment_type, assignment_source) "
                    "VALUES (?, ?, 'secondary', 'manual')",
                    (ids["video_id"], manual_topic_id),
                )
                # A third topic that the refine pass will affirm.
                affirmed_topic_id = conn.execute(
                    "INSERT INTO topics(project_id, name) VALUES (?, 'Affirmed Topic')", (ids["project_id"],)
                ).lastrowid
                # User marked the seeded 'Health' topic wrong for this video.
                conn.execute(
                    "INSERT INTO wrong_assignments(video_id, topic_id, subtopic_id, reason) VALUES (?, ?, NULL, 'no')",
                    (ids["video_id"], ids["topic_id"]),
                )

                summary = db.write_refine_assignments(
                    conn,
                    channel_id=ids["channel_id"],
                    refinement_run_id=run_id,
                    video_id=ids["video_id"],
                    assignments=[
                        {"topic_name": "Health", "confidence": 0.9, "reason": "transcript says health"},  # suppressed
                        {"topic_name": "Affirmed Topic", "subtopic_name": "Deep Dive", "confidence": 0.8, "reason": "x"},
                    ],
                )
                self.assertEqual(summary["suppressed"], 1)

                rows = {
                    r[0]: (r[1], r[2])
                    for r in conn.execute(
                        "SELECT topic_id, assignment_source, refinement_run_id FROM video_topics WHERE video_id = ?",
                        (ids["video_id"],),
                    ).fetchall()
                }
                # Manual row untouched.
                self.assertEqual(rows[manual_topic_id], ("manual", None))
                # Auto 'Health' row deleted AND not re-added (wrong-marked).
                self.assertNotIn(ids["topic_id"], rows)
                # New refine row for the affirmed topic, tagged with the run id.
                self.assertEqual(rows[affirmed_topic_id], ("refine", run_id))
                # Subtopic created + assigned with source 'refine'.
                sub_row = conn.execute(
                    "SELECT vs.assignment_source, vs.refinement_run_id FROM video_subtopics vs "
                    "JOIN subtopics s ON s.id = vs.subtopic_id WHERE vs.video_id = ? AND s.name = 'Deep Dive'",
                    (ids["video_id"],),
                ).fetchone()
                self.assertEqual(sub_row, ("refine", run_id))

                # An unknown topic in a refine assignment is skipped (counted),
                # not a hard error — one hallucinated topic must not sink a paid batch.
                skip_summary = db.write_refine_assignments(
                    conn,
                    channel_id=ids["channel_id"],
                    refinement_run_id=run_id,
                    video_id=ids["video_id"],
                    assignments=[
                        {"topic_name": "Never Heard Of It", "confidence": 0.9, "reason": "x"},
                        {"topic_name": "Affirmed Topic", "confidence": 0.8, "reason": "y"},
                    ],
                )
                self.assertEqual(skip_summary["skipped_unknown_topic"], 1)
                self.assertEqual(skip_summary["topics_written"], 1)
                conn.rollback()


class RefineTopicMapVisibilityTests(unittest.TestCase):
    def test_topic_map_includes_refine_source_rows_and_carries_assignment_source(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _fresh_db(tmpdir)
            with db.connect(db_path) as conn:
                ids = _seed_project(conn)
                # Give vid1 the discovery subtopic so the seeded data is realistic.
                seeded_sub_id = conn.execute(
                    "INSERT INTO subtopics(topic_id, name) VALUES (?, 'Sleep')", (ids["topic_id"],)
                ).lastrowid
                conn.execute(
                    "INSERT INTO video_subtopics(video_id, subtopic_id, assignment_source, confidence, reason, discovery_run_id) "
                    "VALUES (?, ?, 'auto', 0.5, 'r', ?)",
                    (ids["video_id"], seeded_sub_id, ids["discovery_run_id"]),
                )
                run_id = db.create_refinement_run(
                    conn,
                    channel_id=ids["channel_id"],
                    discovery_run_id=ids["discovery_run_id"],
                    model="haiku-4.5",
                    prompt_version="refine-v1",
                    n_sample=1,
                )
                # Refine pass re-judges vid2: keeps it on 'Health' but with a transcript-grade
                # confidence/reason and a (newly relevant) subtopic.
                db.write_refine_assignments(
                    conn,
                    channel_id=ids["channel_id"],
                    refinement_run_id=run_id,
                    video_id=ids["video2_id"],
                    assignments=[
                        {"topic_name": "Health", "subtopic_name": "Cold Exposure", "confidence": 0.95, "reason": "transcript discusses ice baths at length"},
                    ],
                )
                conn.commit()

            topic_map = _build_discovery_topic_map(db_path, run_id=ids["discovery_run_id"])
            self.assertIsNotNone(topic_map)
            # Find the 'Health' topic block and its episodes.
            health = next(t for t in topic_map["topics"] if t["name"] == "Health")
            all_eps: list[dict] = list(health.get("episodes", []))
            for sub in health.get("subtopics", []):
                all_eps.extend(sub.get("episodes", []))
            by_yt = {ep["youtube_video_id"]: ep for ep in all_eps}
            self.assertIn("vid1", by_yt)
            self.assertIn("vid2", by_yt)
            self.assertEqual(by_yt["vid1"]["assignment_source"], "auto")
            self.assertEqual(by_yt["vid2"]["assignment_source"], "refine")
            self.assertAlmostEqual(by_yt["vid2"]["confidence"], 0.95)
            # The refine episode landed under its new subtopic bucket.
            cold = next((s for s in health.get("subtopics", []) if s["name"] == "Cold Exposure"), None)
            self.assertIsNotNone(cold)
            self.assertEqual([ep["youtube_video_id"] for ep in cold["episodes"]], ["vid2"])


if __name__ == "__main__":
    unittest.main()
