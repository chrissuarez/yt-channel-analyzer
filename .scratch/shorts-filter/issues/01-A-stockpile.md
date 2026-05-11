# 01 — Stockpile duration data (no behavior change)

Status: needs-triage
Type: AFK

## Context

YouTube Shorts (re-cuts of long videos) inflate topic frequency in `DiscoveryRun` outputs because the same theme is counted on both the long video and its short. We're going to filter shorts at the discovery layer (per-channel sticky setting, default exclude) — but first we need to capture the data we'd filter on without breaking any existing behavior.

This slice is **purely additive**: schema columns, ingest enrichment, backfill CLI. No filter logic, no default change, no UI change. After this merges, every existing and new channel still produces the exact same topic map.

## What to build

### Schema (in `db.py`)

- `videos.duration_seconds INTEGER NULL` — populated by ingest going forward; NULL on legacy rows until backfilled.
- `channels.exclude_shorts INTEGER NOT NULL DEFAULT 0` — sticky per-channel setting. **Default 0 in this slice** (no behavior change). Slice C will flip the default to 1 and migrate existing rows.
- `discovery_runs` audit fields (added but unused this slice; populated in slices B/C):
  - `shorts_cutoff_seconds INTEGER NULL`
  - `n_episodes_total INTEGER NULL`
  - `n_shorts_excluded INTEGER NULL`
  - `n_orphaned_wrong_marks INTEGER NULL`
  - `n_orphaned_renames INTEGER NULL`

All additions go through `db.ensure_schema()` as `ALTER TABLE ... ADD COLUMN` for back-compat with existing DBs. No data migration in this slice.

### Ingest (`youtube.py`)

- `fetch_recent_videos` does its existing `playlistItems` call, then a follow-up `videos.list?part=contentDetails` for the returned IDs (batched up to 50 per request — already the YT API max).
- Parse ISO 8601 `duration` (e.g., `PT2M30S`) into integer seconds.
- `VideoMetadata` gains `duration_seconds: int | None` field.
- Persist into the new `videos.duration_seconds` column.

### CLI (`cli.py`)

- New command: `backfill-durations <channel>` — finds rows with `duration_seconds IS NULL` for that channel, batches `videos.list?part=contentDetails` calls 50 IDs at a time, updates rows. Idempotent. Requires `YOUTUBE_API_KEY`.

### Tests

- `test_discovery` / `test_extractor` fixtures that build `VideoMetadata` add `duration_seconds=None` or a sane integer; otherwise unchanged.
- New unit tests for ISO 8601 duration parsing edge cases (`PT0S`, `PT1H2M3S`, missing field).
- New test for `backfill-durations` against an in-memory DB with a stub fetcher.

## Acceptance criteria

- [ ] All five new columns present after `db.ensure_schema()` runs against an existing DB (test against a snapshot of the DOAC DB if convenient, otherwise a fixture)
- [ ] `fetch_recent_videos` populates `duration_seconds` for every returned `VideoMetadata` (assuming the YT API returns the field)
- [ ] `backfill-durations <channel>` is idempotent and only touches `WHERE duration_seconds IS NULL`
- [ ] Existing test gate (`.ralph/verify.sh`) still passes
- [ ] Verify on DOAC: run `discover --stub` before and after the migration; the resulting topic map is byte-identical (no behavior change)

## Out of scope

- Filtering shorts from discovery (slice B)
- Flipping the `exclude_shorts` default (slice C)
- UI changes
- Orphan-count computation (slice C)

## Notes

- Schema migration is structurally additive only — no implicit network calls. `backfill-durations` is the explicit path for legacy data.
- One extra YT API quota unit per 50 videos on every fetch. Negligible against the 10,000/day default.
