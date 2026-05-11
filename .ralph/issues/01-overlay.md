# Slice 01 — Stockpile duration data (no behavior change)

**Full spec (read it, follow acceptance criteria verbatim):** `.scratch/shorts-filter/issues/01-A-stockpile.md`

This is slice 1 of 3 in the shorts-filter feature. The design is **frozen** — do not relitigate any decision. Background + rationale: `.scratch/shorts-filter/issues/` (01, 02, 03) and memory `project_shorts_filter.md`.

## Scope this iteration (purely additive — zero behavior change)

1. **`db.py` schema** via `db.ensure_schema()` using `ALTER TABLE ... ADD COLUMN` (back-compat with existing DBs, no data migration):
   - `videos.duration_seconds INTEGER NULL`
   - `channels.exclude_shorts INTEGER NOT NULL DEFAULT 0` — **default 0 in this slice** (slice C flips it)
   - `discovery_runs`: `shorts_cutoff_seconds INTEGER NULL`, `n_episodes_total INTEGER NULL`, `n_shorts_excluded INTEGER NULL`, `n_orphaned_wrong_marks INTEGER NULL`, `n_orphaned_renames INTEGER NULL` (added now, populated in B/C)
2. **`youtube.py` ingest:** `fetch_recent_videos` does a follow-up `videos.list?part=contentDetails` (batched ≤50) after `playlistItems`; parse ISO-8601 `duration` (`PT2M30S` etc.) → int seconds; `VideoMetadata` gains `duration_seconds: int | None`; persist into the new column.
3. **`cli.py`:** new `backfill-durations <channel>` command — finds `duration_seconds IS NULL` rows for the channel, batches `videos.list?part=contentDetails` 50 IDs at a time, updates rows. Idempotent. Requires `YOUTUBE_API_KEY`.
4. **Tests:** fixtures building `VideoMetadata` add `duration_seconds=None`/sane int; new unit tests for ISO-8601 parsing edge cases (`PT0S`, `PT1H2M3S`, missing field); new test for `backfill-durations` against an in-memory DB with a stub fetcher.

## Out of scope (later slices — do NOT touch)
- Filtering shorts from `run_discovery` (slice B)
- Flipping the `exclude_shorts` default / one-shot migration (slice C)
- Orphan-count computation (slice C)
- Any `review_ui.py` change

## Constraints / HITL triggers
- **Real YouTube fetch is a HITL pause.** Do not call the live YT API; write code + stub-backed tests only. The actual `fetch_recent_videos` / `backfill-durations` smoke against a real channel is the operator's job — flag it with `HITL_PAUSE`.
- Schema migration must be structurally additive only — **no implicit network calls** in `ensure_schema()`.
- Verify gate is offline for this slice: `.ralph/verify.sh` (discovery + extractor) must stay green. Acceptance also wants a `discover --stub` byte-identical check on DOAC before/after — if the DOAC DB isn't present in-sandbox, note it and leave for the operator.
- Conventional commits; end commit messages with the `Co-Authored-By:` trailer.
