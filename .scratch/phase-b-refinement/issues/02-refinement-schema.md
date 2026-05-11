# 02 — Refinement schema + db helpers

Status: DONE — branch `feat/issue-02-refinement-schema`. `db.py`: 3 new tables (`refinement_runs`/`refinement_episodes`/`taxonomy_proposals`) in `SCHEMA_STATEMENTS`+`REQUIRED_TABLE_COLUMNS`; `refinement_run_id` col + `'refine'` in the `assignment_source` CHECK on the `video_topics`/`video_subtopics` CREATEs; `_repair_video_topic_refine_source_constraint` wired into `ensure_schema` (mirrors the `'auto'` repair, guard = `'refine'` not in live create-SQL; INSERT…SELECT deliberately omits `refinement_run_id` so it composes safely with the `'auto'` rebuild that runs just before it — column defaults NULL on the rebuilt table). Helpers: `build_topic_rename_resolver`, `create_refinement_run`, `set_refinement_run_status`, `add_refinement_episodes`, `insert_taxonomy_proposals`, `accept_taxonomy_proposal` (creates the `topics`/`subtopics` row idempotently, resolves a renamed parent, marks the proposal `rejected` on a missing parent), `reject_taxonomy_proposal`, `write_refine_assignments` (replace-wholesale of non-`manual` rows, `manual` rows survive, `wrong_assignments`-suppressed rows not re-added, refine subtopic created if absent, unknown topic → ValueError). `review_ui._build_discovery_topic_map` topic/episode/subtopic queries also pick up `assignment_source='refine'` rows for the run's topics; per-episode payload carries `assignment_source`. New `test_refinement_schema.py` (7 tests) added to `.ralph/verify.sh`; gate 319 green. `test_transcripts.py` untouched. NOTE: the CHECK-rebuild is a destructive-migration HITL-pause trigger per `.ralph/PROMPT.md` — flag for operator review at merge.
Type: AFK (but the `_repair_*` CHECK-rebuild is a destructive-migration HITL-pause trigger per `.ralph/PROMPT.md` — the iteration agent should pause for operator review of the rebuild before merge)
Branch: `feat/issue-02-refinement-schema`
Spec: `PRD_PHASE_B.md` (module 3 — "Refinement schema"; the six slices §B2); `ROADMAP.md` §B
User stories covered: 15, 17

## Context

Phase B needs to record refinement runs, the episodes they sampled, the taxonomy proposals they produced, and the transcript-grade assignments they wrote. This slice is the schema + `db.py` helpers only — no CLI, no LLM, no `refinement.py`. Mostly additive; one CHECK-constraint rebuild on the junction tables to add a new `assignment_source` value.

## What to build

### New tables (in `db.py` — added to `TABLE_STATEMENTS` CREATEs and `REQUIRED_TABLE_COLUMNS`)

- `refinement_runs` — `(id INTEGER PK, channel_id, discovery_run_id, model TEXT, prompt_version TEXT, status TEXT CHECK (status IN ('pending','running','success','error')), n_sample INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)`. Mirrors `discovery_runs`. FK `channel_id → channels(id) ON DELETE CASCADE`; `discovery_run_id → discovery_runs(id) ON DELETE SET NULL`.
- `refinement_episodes` — `(refinement_run_id, video_id, transcript_status_at_run TEXT, PRIMARY KEY (refinement_run_id, video_id))`. FKs to `refinement_runs(id) ON DELETE CASCADE` and `videos(id) ON DELETE CASCADE`.
- `taxonomy_proposals` — `(id INTEGER PK, refinement_run_id, kind TEXT CHECK (kind IN ('topic','subtopic')), name TEXT, parent_topic_name TEXT, evidence TEXT, source_video_id INTEGER, status TEXT CHECK (status IN ('pending','accepted','rejected')) DEFAULT 'pending', resolved_at TEXT)`. FKs to `refinement_runs(id) ON DELETE CASCADE` and `videos(id) ON DELETE SET NULL`. `parent_topic_name` NULL for `kind='topic'`.

### Junction-table change (`video_topics` and `video_subtopics`)

- Add `'refine'` to the `assignment_source` CHECK constraint (currently allows `'auto'` / `'manual'` / etc.). This requires a `_repair_*` rebuild: rename → recreate with the new CHECK → `INSERT ... SELECT` all columns by name → drop old, with `PRAGMA foreign_keys=OFF` + `PRAGMA legacy_alter_table=ON` so FK children survive. **Mirror `_repair_video_topic_assignment_source_constraint` exactly** (it does this same dance for `'auto'`). Idempotency guard: only fire when the live CREATE-SQL still lacks `'refine'`. Wire it into `ensure_schema()` alongside the other `_repair_*` functions.
- Add `refinement_run_id INTEGER NULL` to both junction tables — additive `ALTER TABLE ... ADD COLUMN` via `ensure_schema()`, exactly like the existing `discovery_run_id` column. FK `→ refinement_runs(id) ON DELETE SET NULL`. Add to `REQUIRED_TABLE_COLUMNS`.

### `db.py` helpers

- `create_refinement_run(connection, *, channel_id, discovery_run_id, model, prompt_version, n_sample) -> int` (inserts `status='pending'`); `set_refinement_run_status(connection, run_id, status)`.
- `add_refinement_episodes(connection, run_id, rows)` where `rows = [(video_id, transcript_status_at_run), ...]`.
- `insert_taxonomy_proposals(connection, run_id, proposals)`; `accept_taxonomy_proposal(connection, proposal_id)` — creates the `topics`/`subtopics` row if absent (idempotent — if it exists, just mark accepted), resolving `parent_topic_name` through the project's rename map (reuse `discovery._apply_renames_to_payload`'s map-building, or a small shared helper); if the parent no longer exists, mark the proposal `rejected` and return a clear signal. `reject_taxonomy_proposal(connection, proposal_id)`.
- `write_refine_assignments(connection, *, channel_id, refinement_run_id, video_id, assignments)` — replaces that video's non-curated `video_topics`/`video_subtopics` rows wholesale with the given `assignments` (`assignment_source='refine'`, `discovery_run_id=NULL`, `refinement_run_id=<run>`); rows suppressed by a `wrong_assignments` mark are not re-added (reuse the existing suppression query).
- Widen the topic-map query in `review_ui.py`'s `_build_discovery_topic_map` (or wherever the episode rows are selected) to include `assignment_source='refine'` rows for the run's topics (they carry `discovery_run_id NULL`). Carry an `assignment_source` field through to the per-episode payload so slice B6 can render the "transcript-checked" pill. (UI rendering is B6; this slice just makes the data reachable.)

### Tests

- Add to `test_discovery` (or a new `test_refinement_schema.py` folded into the gate): fresh CREATE has the new tables + columns; `ensure_schema()` on an old-shape DB ALTERs in `refinement_run_id` and rebuilds the junction CHECK to include `'refine'` (and is a no-op on a second call); `accept_taxonomy_proposal` creates the subtopic idempotently and resolves a renamed parent; `accept` on a proposal whose parent was deleted marks it rejected; `write_refine_assignments` replaces wholesale and does not re-add a `wrong_assignments`-suppressed row; the topic-map query returns `refine`-source rows.

## Acceptance criteria

- [ ] `refinement_runs`, `refinement_episodes`, `taxonomy_proposals` exist on a fresh DB and are ALTERed/rebuilt into an old DB by `ensure_schema()`; the migration is idempotent and preserves all `video_topics`/`video_subtopics`/`videos`/`discovery_runs` rows.
- [ ] `video_topics`/`video_subtopics` accept `assignment_source='refine'` and have a nullable `refinement_run_id`.
- [ ] The db helpers above exist and behave as specified (run lifecycle, episode recording, proposal insert/accept/reject with parent-rename resolution, replace-wholesale refine-assignment writes that respect `wrong_assignments`).
- [ ] The topic-map query includes `refine`-source rows and the per-episode payload carries `assignment_source`.
- [ ] No CLI or LLM code in this slice. Verify gate green; `test_transcripts.py` untouched.

## Blocked by

- Slice 01 (`feat/issue-01-fetch-transcripts`) — not a hard code dependency, but B2 should land after B1 so the schema work sits on top of the transcript-fetch path it'll be used with. (If preferred, B1 and B2 can proceed in parallel off `main`; they don't touch the same files.)
