# Slice 02 — Discovery filter + per-run override

**Full spec (read it, follow acceptance criteria verbatim):** `.scratch/shorts-filter/issues/02-B-filter-logic.md`

Slice 2 of 3 in the shorts-filter feature. Slice A (schema + ingest + `backfill-durations`) is merged to `main` — `videos.duration_seconds`, `channels.exclude_shorts` (DEFAULT 0), and the 5 `discovery_runs` audit columns already exist. Design is **frozen** — do not relitigate. Background: `.scratch/shorts-filter/issues/` (01/02/03) and memory `project_shorts_filter.md`.

## Scope this iteration

After this slice the per-channel default is still `exclude_shorts=0` — behavior only changes when a user passes the CLI flag or hand-sets the column. (Slice C flips the default for everyone — out of scope here.)

1. **`discovery.py` filter logic:**
   - Module-level constant `SHORTS_CUTOFF_SECONDS = 180` (grep-able, overridable in tests).
   - Resolve effective `exclude_shorts` for the run: per-run CLI override wins if passed; else fall back to `channels.exclude_shorts`.
   - When the filter is active, **pre-filter the loaded episodes before building the LLM prompt** — upstream of both `_apply_renames_to_payload` and the LLM call itself. Cutoff: `duration_seconds <= SHORTS_CUTOFF_SECONDS`. `duration_seconds IS NULL` → treat as long, do NOT exclude (fail-safe to legacy behavior).
   - Populate audit fields on the new `discovery_runs` row: `shorts_cutoff_seconds = 180` when filter active (NULL when off); `n_episodes_total` = count before filter; `n_shorts_excluded` = count removed. (Leave `n_orphaned_wrong_marks` / `n_orphaned_renames` NULL — those land in slice C.)
   - Edge case: if filtering would leave **0 episodes**, raise a clear error mentioning `--include-shorts`. Do not persist an empty `DiscoveryRun`.
   - The pre-LLM filter point is load-bearing for the cost/correctness story — do not refactor it into a presentation-only filter.
2. **`cli.py`:** `discover` gains a mutex pair `--exclude-shorts` / `--include-shorts` (one-run override; neither flag → use the channel sticky setting). The override does **not** mutate `channels.exclude_shorts`.
3. **Tests** (`test_discovery.py`, `stub_llm`):
   - Channel `exclude_shorts=1`: shorts filtered, audit fields populated, topic map omits them.
   - Channel `exclude_shorts=0`: shorts included, `n_shorts_excluded=0`, `shorts_cutoff_seconds IS NULL`.
   - `--include-shorts` overrides `exclude_shorts=1`; `--exclude-shorts` overrides `exclude_shorts=0`.
   - All-shorts channel + filter active → raises the clear error.
   - Mix of NULL and non-NULL durations → NULLs kept (fail-safe to long).
   - Verify the filter is upstream of the LLM call (e.g. assert no prompt is built for an excluded video — pass a `stub_llm` wrapper that records what it received).

## Out of scope (slice C — do NOT touch)
- Flipping the per-channel default to 1 / the one-shot migration
- UI badge in `review_ui.py`
- `n_orphaned_wrong_marks` / `n_orphaned_renames` computation
- A writable per-channel toggle in the UI (deferred indefinitely)

## Constraints / HITL triggers
- **Real-LLM verify is a HITL pause.** Do all code + tests with `stub_llm` only. The acceptance item "re-run `discover --exclude-shorts` on DOAC with `RALPH_ALLOW_REAL_LLM=1`, confirm cleaner topic map + correct audit values" is the operator's job — end with `HITL_PAUSE` flagging it, do not call the real API.
- `.ralph/verify.sh` (discovery + extractor, offline) must stay green.
- Existing `wrong_assignments` rows targeting now-excluded videos go inert (not deleted) — they wake back up if the user later sets `exclude_shorts=0`. Slice C surfaces the count; this slice just must not delete them.
- Conventional commits; end commit messages with the `Co-Authored-By:` trailer.
