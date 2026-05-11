# Operator smoke — shorts-filter slice A (stockpile duration data)

Branch: `feat/issue-01-shorts-stockpile`. Run these from a normal terminal (they hit the live YouTube API + write SQLite files, so they can't run in the sandboxed agent). Each fenced block is self-contained — copy-paste the whole block, no matter what directory your shell is in.

**Why every block starts with `cd ~/.openclaw/workspace`:** this repo *is* the `yt_channel_analyzer` Python package, so `python -m yt_channel_analyzer.cli` only resolves when the working dir is the package's **parent** (`~/.openclaw/workspace`) with `PYTHONPATH=.`. Running it from inside `yt_channel_analyzer/` gives `ModuleNotFoundError: No module named 'yt_channel_analyzer'`. The `.venv` also lives at the parent.

Prereq: `~/.openclaw/workspace/.env` has `YOUTUBE_API_KEY` set.

---

## 0. One-time setup — checkout the branch

```bash
cd ~/.openclaw/workspace/yt_channel_analyzer && git checkout feat/issue-01-shorts-stockpile && cd ~/.openclaw/workspace
```

After this, leave the shell sitting in `~/.openclaw/workspace`. (The blocks below `cd` there anyway, so it doesn't matter if you wander off.)

---

## Test 1 — fetch enrichment populates `duration_seconds`

`analyze --stub` runs the full ingest path (channel resolve → metadata upsert → videos fetch+upsert → stub discovery). The new code makes `fetch_channel_videos` do a follow-up `videos.list?part=contentDetails` call, so every fetched video should land with a non-NULL `duration_seconds`.

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && rm -f /tmp/shorts-smoke.sqlite && PYTHONPATH=. python3 -m yt_channel_analyzer.cli analyze \
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

Count any NULLs (should print `0`):

```bash
sqlite3 /tmp/shorts-smoke.sqlite "SELECT COUNT(*) AS null_durations FROM videos WHERE duration_seconds IS NULL;"
```

---

## Test 2 — `backfill-durations` populates legacy NULL rows, and is idempotent

`backfill-durations` is the path for DBs created before this branch (their `videos.duration_seconds` is NULL). To exercise it without needing an old DB, simulate one by blanking the durations we just fetched:

```bash
cp /tmp/shorts-smoke.sqlite /tmp/shorts-legacy.sqlite && sqlite3 /tmp/shorts-legacy.sqlite "UPDATE videos SET duration_seconds = NULL;" && sqlite3 /tmp/shorts-legacy.sqlite "SELECT COUNT(*) AS null_before FROM videos WHERE duration_seconds IS NULL;"
```

First backfill run — should populate them and print how many:

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && PYTHONPATH=. python3 -m yt_channel_analyzer.cli backfill-durations --db-path /tmp/shorts-legacy.sqlite
```

Second run — should be a no-op (prints `No videos missing duration_seconds...`):

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && PYTHONPATH=. python3 -m yt_channel_analyzer.cli backfill-durations --db-path /tmp/shorts-legacy.sqlite
```

Confirm everything got filled (should print `0`):

```bash
sqlite3 /tmp/shorts-legacy.sqlite "SELECT COUNT(*) AS null_after FROM videos WHERE duration_seconds IS NULL;"
```

**Pass if:** `null_before` > 0, the first run reports a backfill count, the second run prints the "No videos missing..." line, and `null_after` is 0.

(If you have a *real* pre-branch DB, point `--db-path` at it instead — same expectations. Note: `backfill-durations` only takes `--db-path`; it reads the primary channel from the DB itself.)

---

## Test 3 — schema columns exist on the migrated DB

The new columns are added via `ALTER TABLE ... ADD COLUMN` inside `ensure_schema()`, which the runs above already triggered. Confirm they're present (no `cd`/venv needed — pure sqlite3):

```bash
echo "--- videos ---"; sqlite3 /tmp/shorts-smoke.sqlite ".schema videos" | grep duration_seconds; echo "--- channels ---"; sqlite3 /tmp/shorts-smoke.sqlite ".schema channels" | grep exclude_shorts; echo "--- discovery_runs ---"; sqlite3 /tmp/shorts-smoke.sqlite ".schema discovery_runs" | grep -E "shorts_cutoff_seconds|n_episodes_total|n_shorts_excluded|n_orphaned_wrong_marks|n_orphaned_renames"
```

**Pass if:** you see `duration_seconds INTEGER` under `videos`, `exclude_shorts INTEGER NOT NULL DEFAULT 0` under `channels` (default 0 here — slice C flips it to 1), and the five `n_*` / `shorts_cutoff_seconds` columns under `discovery_runs`.

Optional — the issue spec's "byte-identical topic map before/after" check needs the real DOAC DB. If you still have it: `discover --stub` on it from `main`, dump the `discovery_topic_map` JSON, switch to this branch, `discover --stub` again, dump again, `diff`. If the DOAC DB is gone, skip — the change is pure additive `ADD COLUMN`, nothing it could alter.

---

## When all three pass

Tell Claude "slice A smoke passed" — it'll fast-forward `feat/issue-01-shorts-stockpile` into `main`, then start slice B (`feat/issue-02-shorts-filter-logic`).

Cleanup:

```bash
rm -f /tmp/shorts-smoke.sqlite /tmp/shorts-legacy.sqlite
```
