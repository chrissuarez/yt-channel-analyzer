# Operator smoke — shorts-filter slice A (stockpile duration data)

Branch: `feat/issue-01-shorts-stockpile`. Run these from a normal terminal (they hit the live YouTube API + write SQLite files, so they can't run in the sandboxed agent). Each fenced block is meant to be copy-pasted whole.

Prereqs: you're on the branch, and `~/.openclaw/workspace/.env` has `YOUTUBE_API_KEY` set.

---

## 0. One-time setup for the shell session

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && cd yt_channel_analyzer && git checkout feat/issue-01-shorts-stockpile
```

---

## Test 1 — fetch enrichment populates `duration_seconds`

`analyze --stub` runs the full ingest path (channel resolve → metadata upsert → videos fetch+upsert → stub discovery). The new code makes `fetch_channel_videos` do a follow-up `videos.list?part=contentDetails` call, so every fetched video should land with a non-NULL `duration_seconds`.

```bash
rm -f /tmp/shorts-smoke.sqlite
python -m yt_channel_analyzer.cli analyze \
  --db-path /tmp/shorts-smoke.sqlite \
  --project-name shorts-smoke \
  --channel-input "https://www.youtube.com/@TheDiaryOfACEO" \
  --limit 15 \
  --stub
```

Then check the durations:

```bash
sqlite3 /tmp/shorts-smoke.sqlite "SELECT youtube_video_id, duration_seconds, substr(title,1,50) FROM videos ORDER BY duration_seconds;"
```

**Pass if:** every row has a non-NULL `duration_seconds`. Bonus: any row with `duration_seconds <= 180` is a real Short — confirm by eyeballing its title (slice B will filter those).

Count any NULLs (should be 0):

```bash
sqlite3 /tmp/shorts-smoke.sqlite "SELECT COUNT(*) AS null_durations FROM videos WHERE duration_seconds IS NULL;"
```

---

## Test 2 — `backfill-durations` populates legacy NULL rows, and is idempotent

This needs a DB created **before** this branch (legacy rows have `duration_seconds = NULL`). If you have such a DB, set its path below. If you don't have one handy, you can fake one: run Test 1's `analyze` against a fresh DB **on `main`** first (where the column doesn't exist yet... actually it won't have the column at all — so instead just NULL the column out of the smoke DB to simulate legacy rows):

```bash
# Simulate a legacy DB: blank out the durations we just fetched
cp /tmp/shorts-smoke.sqlite /tmp/shorts-legacy.sqlite
sqlite3 /tmp/shorts-legacy.sqlite "UPDATE videos SET duration_seconds = NULL;"
sqlite3 /tmp/shorts-legacy.sqlite "SELECT COUNT(*) AS null_before FROM videos WHERE duration_seconds IS NULL;"
```

First backfill run — should populate them:

```bash
python -m yt_channel_analyzer.cli backfill-durations --db-path /tmp/shorts-legacy.sqlite
```

Second run — should be a no-op ("No videos missing duration_seconds..."):

```bash
python -m yt_channel_analyzer.cli backfill-durations --db-path /tmp/shorts-legacy.sqlite
```

Confirm everything got filled:

```bash
sqlite3 /tmp/shorts-legacy.sqlite "SELECT COUNT(*) AS null_after FROM videos WHERE duration_seconds IS NULL;"
```

**Pass if:** `null_before` > 0, the first run prints how many it backfilled, the second run prints the "No videos missing..." line, and `null_after` is 0.

(If you have a *real* pre-branch DB, just point `--db-path` at it instead of the simulated one — same expectations.)

---

## Test 3 — schema migration is non-destructive on an existing DB

The new columns are added via `ALTER TABLE ... ADD COLUMN` in `ensure_schema()`, so opening an old DB should just work and not change any existing data. The `analyze`/`backfill` runs above already exercised `ensure_schema()` against the smoke DB. To check the columns exist:

```bash
sqlite3 /tmp/shorts-smoke.sqlite ".schema videos" | grep duration_seconds
sqlite3 /tmp/shorts-smoke.sqlite ".schema channels" | grep exclude_shorts
sqlite3 /tmp/shorts-smoke.sqlite ".schema discovery_runs" | grep -E "shorts_cutoff_seconds|n_episodes_total|n_shorts_excluded|n_orphaned_wrong_marks|n_orphaned_renames"
```

**Pass if:** all three commands print the expected column line(s), and `channels.exclude_shorts` shows `DEFAULT 0` (slice C flips it to 1 later).

Optional — if you still have the real DOAC DB somewhere, the "byte-identical topic map before/after" check from the issue spec is: run `discover --stub` on it while on `main`, dump the `discovery_topic_map` JSON, switch to this branch, run `discover --stub` again, dump again, `diff` the two. If the DOAC DB is gone, skip this — the schema change is pure additive `ADD COLUMN`, so there's nothing it could alter.

---

## When all three pass

Tell Claude "slice A smoke passed" — it'll fast-forward `feat/issue-01-shorts-stockpile` into `main`, then start slice B (`feat/issue-02-shorts-filter-logic`).

Cleanup:

```bash
rm -f /tmp/shorts-smoke.sqlite /tmp/shorts-legacy.sqlite
```
