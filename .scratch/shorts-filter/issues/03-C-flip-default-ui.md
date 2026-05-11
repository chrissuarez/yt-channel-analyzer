# 03 — Flip default + orphan counts + UI badge

Status: needs-triage
Type: AFK
Blocked by: slices A and B

## Context

After slices A (data) and B (filter logic), `exclude_shorts` is still 0 by default and behavior only changes when a user passes the CLI flag. This slice flips the default for every channel, computes the curation-orphan counts so re-runs are debuggable, and surfaces both the filter and orphan counts in the review UI as a read-only badge.

This is the slice that **changes behavior for everyone.** The next time the user runs `discover` on any existing channel, the topic map will look different from prior runs. That difference is intended, and the badge + audit fields make it legible rather than mysterious.

## What to build

### Migration (`db.py`)

- Schema migration step: `UPDATE channels SET exclude_shorts = 1` (apply once, idempotent guard via a one-shot migration marker — match the pattern used by other one-shot migrations in `db.py`).
- New default for the column going forward: `DEFAULT 1` (so any newly-created channels also start with the filter on).

### Orphan counting (`discovery.py`)

When `run_discovery` filters episodes, after the filter runs and before the run row is finalized:

- `n_orphaned_wrong_marks` — count of `wrong_assignments` rows whose `video_id` corresponds to a now-filtered video. (One SQL query joining `wrong_assignments` against `videos.duration_seconds`.)
- `n_orphaned_renames` — count of `topic_renames` whose target topic/subtopic loses **all** evidence in this run because every supporting video was filtered. Computed by comparing the post-filter assignment set against the rename targets.

These rows are **not deleted** — only counted. If the user flips `exclude_shorts=0` later, the wrong-marks/renames wake back up.

### UI (`review_ui.py`)

In `_DISCOVERY_*` helpers / discovery topic-map header:

- Read-only one-line badge after the run header: `"X shorts excluded · Y curation actions inert (target episodes filtered)"`
- Hide entirely if `n_shorts_excluded == 0 && n_orphaned_* == 0` (avoid noise on channels with no shorts)
- Pure render-side: no new endpoint, no write path. Build off existing `build_state_payload()` envelope.

**Hard cap: ≤ 200 lines of net change in `review_ui.py`** — the Ralph harness HITL-pauses at 300. If implementation creeps past 200, stop and ask.

### Tests

- `test_discovery`:
  - Orphan counts populated correctly when filter excludes videos that have wrong-marks against them.
  - Orphan counts populated correctly when a rename's target subtopic loses all evidence post-filter.
  - Orphan counts are 0 when filter is off.
- `test_discovery` or a new lightweight `review_ui` test: badge JSON envelope contains the audit counts when `n_shorts_excluded > 0`, omitted otherwise.

## Acceptance criteria

- [ ] Schema migration sets `exclude_shorts=1` on every existing channel exactly once (idempotent)
- [ ] New `channels` rows default to `exclude_shorts=1`
- [ ] `run_discovery` populates `n_orphaned_wrong_marks` and `n_orphaned_renames` correctly
- [ ] Wrong-marks and renames are **not** deleted by the filter — verify they wake back up when `exclude_shorts` is flipped to 0 and discovery is re-run
- [ ] UI badge renders correctly in review_ui (manual verify against running server on `:8765`)
- [ ] `.ralph/verify.sh` green
- [ ] **Real-LLM HITL pause for verify** (`RALPH_ALLOW_REAL_LLM=1`): re-run `discover` on DOAC with the new default; confirm topic map is cleaner than the prior run, audit + orphan counts are populated, badge renders

## Out of scope

- Writable per-channel toggle in the UI — explicitly deferred. CLI flag + manual SQL is the v1 story for changing a channel's setting after migration.
- Dedup-style detection of short→parent video relationships (a Phase C concern at earliest, sitting next to claim extraction)
- A separate "settings" panel in the UI

## Notes

- This is the slice that costs LLM tokens on the verify pass. Slices A and B verify offline / with stub. Budget accordingly when scheduling.
- After this merges, update `WORKLOG.md` with a one-liner about the default flip so future iterations see why DOAC's topic map shape changed.
