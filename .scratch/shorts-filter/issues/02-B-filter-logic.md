# 02 — Discovery filter + per-run override

Status: needs-triage
Type: AFK

## Context

Slice A landed schema + ingest + backfill, but no behavior change. This slice wires the actual filter in `run_discovery` and exposes a per-run CLI override. The per-channel default is still `exclude_shorts=0` after this slice — so behavior only changes when a user explicitly opts in via CLI flag, or manually sets the column. Slice C flips the default for everyone.

## What to build

### Filter logic (`discovery.py`)

- Resolve effective `exclude_shorts` for the run:
  - Per-run CLI override wins if passed.
  - Otherwise fall back to `channels.exclude_shorts`.
- When filter is active, pre-filter the loaded episodes **before** building the LLM prompt (upstream of `_apply_renames_to_payload` and the LLM call itself):
  - Cutoff: `duration_seconds <= 180`.
  - `duration_seconds IS NULL` → treat as long (do NOT exclude). Fail-safe to legacy behavior.
- Populate audit fields on the new `DiscoveryRun` row:
  - `shorts_cutoff_seconds = 180` (when filter active; NULL when filter off)
  - `n_episodes_total` — count before filter
  - `n_shorts_excluded` — count removed by filter
- Edge case: if filtering would leave **0 episodes**, raise a clear error referencing `--include-shorts`. Do not produce an empty `DiscoveryRun`.

Define the cutoff as a module-level constant in `discovery.py` (e.g., `SHORTS_CUTOFF_SECONDS = 180`) so it's grep-able and easy to override in tests.

### CLI (`cli.py`)

- `discover` gains a mutex pair:
  - `--exclude-shorts` — force exclude for this run, regardless of channel setting.
  - `--include-shorts` — force include for this run, regardless of channel setting.
- Neither flag → use channel sticky setting.
- The override does **not** mutate `channels.exclude_shorts`. One-run only.

### Tests

- `test_discovery` cases with `stub_llm`:
  - Channel `exclude_shorts=1`: shorts are filtered, audit fields populated, topic map omits them.
  - Channel `exclude_shorts=0`: shorts included, audit fields show `n_shorts_excluded=0` and `shorts_cutoff_seconds IS NULL`.
  - `--include-shorts` overrides `exclude_shorts=1`.
  - `--exclude-shorts` overrides `exclude_shorts=0`.
  - All-shorts channel + filter active → raises clear error.
  - Mix of NULL and non-NULL durations: NULLs are kept (fail-safe to long).

## Acceptance criteria

- [ ] `run_discovery` honors `channels.exclude_shorts` and per-run override
- [ ] Filter happens upstream of the LLM call (verifiable: prompt for an excluded video is never built)
- [ ] Audit fields populated correctly on every `DiscoveryRun` row
- [ ] All-shorts edge case raises with a message pointing to `--include-shorts`
- [ ] `.ralph/verify.sh` green
- [ ] **Real-LLM HITL pause for verify** (`RALPH_ALLOW_REAL_LLM=1`): re-run `discover --exclude-shorts` on DOAC after slice A's backfill is done; confirm cleaner topic map and correct audit field values

## Out of scope

- Flipping the per-channel default to 1 (slice C)
- UI badge (slice C)
- Orphan counting for wrong-marks/renames (slice C)
- Writable per-channel toggle in the UI (deferred indefinitely; CLI override + manual SQL is enough for v1)

## Notes

- The pre-LLM filter point is load-bearing for the cost/correctness story we settled on during design — see `WORKLOG.md` for context. Don't refactor this into a presentation-only filter without revisiting.
- Existing `wrong_assignments` rows that target now-excluded videos go inert (not deleted). They wake back up if the user later flips to `exclude_shorts=0`. Slice C surfaces a count of these.
