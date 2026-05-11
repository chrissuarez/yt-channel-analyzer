# Operator smoke — shorts-filter slice B (discovery filter + per-run override)

Branch state: slice B is **merged to `main`** (it's behind the `--exclude-shorts` flag, channel default still 0, so merging before this smoke is safe). This smoke is the acceptance item that needs a **real LLM call** (~$0.02, Haiku 4.5, 15 episodes) — that's why the agent can't do it. After it passes, Claude starts slice C (the one that flips the default for everyone).

Every fenced block is self-contained — copy-paste whole, regardless of your shell's current dir. (Reminder: this repo *is* the `yt_channel_analyzer` package, so the CLI only resolves from the parent dir `~/.openclaw/workspace` with `PYTHONPATH=.`.)

Prereqs: `~/.openclaw/workspace/.env` has both `YOUTUBE_API_KEY` and `ANTHROPIC_API_KEY`.

---

## 0. Setup — be on `main`, build a fresh DB with durations

```bash
cd ~/.openclaw/workspace/yt_channel_analyzer && git checkout main && cd ~/.openclaw/workspace
```

Ingest a channel (this uses the slice-A path that fetches `duration_seconds` for every video). Use `analyze --stub` so ingest happens without spending LLM tokens yet:

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && rm -f /tmp/shortsB.sqlite && PYTHONPATH=. python3 -m yt_channel_analyzer.cli analyze \
  --db-path /tmp/shortsB.sqlite \
  --project-name shortsB \
  --channel-input "https://www.youtube.com/@TheDiaryOfACEO" \
  --limit 15 \
  --stub
```

Sanity — confirm at least one Short (`duration_seconds <= 180`) is present, otherwise the filter has nothing to do (try another channel or a bigger `--limit`):

```bash
sqlite3 /tmp/shortsB.sqlite "SELECT count(*) AS total, sum(duration_seconds <= 180) AS shorts FROM videos;"
```

If `shorts` is 0, bump `--limit` to e.g. 50 and re-run the `analyze` block, or pick a channel that posts Shorts.

---

## Test 1 — `discover --exclude-shorts` filters Shorts, populates audit fields, costs ~$0.02

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && RALPH_ALLOW_REAL_LLM=1 PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path /tmp/shortsB.sqlite \
  --project-name shortsB \
  --real \
  --exclude-shorts
```

Inspect the run's audit fields:

```bash
sqlite3 -header -column /tmp/shortsB.sqlite "SELECT id, status, shorts_cutoff_seconds, n_episodes_total, n_shorts_excluded FROM discovery_runs ORDER BY id DESC LIMIT 1;"
```

**Pass if:** `status = success`, `shorts_cutoff_seconds = 180`, `n_episodes_total` = the total from the sanity query, `n_shorts_excluded` = the `shorts` count from the sanity query (and > 0).

Confirm no excluded Short got a topic assignment in this run:

```bash
sqlite3 -header -column /tmp/shortsB.sqlite "
SELECT v.youtube_video_id, v.duration_seconds, count(vt.topic_id) AS topic_rows
FROM videos v
LEFT JOIN video_topics vt ON vt.video_id = v.id AND vt.discovery_run_id = (SELECT max(id) FROM discovery_runs)
WHERE v.duration_seconds <= 180
GROUP BY v.id;"
```

**Pass if:** every row shows `topic_rows = 0` (the Shorts were filtered before the LLM saw them, so they got no assignments this run).

---

## Test 2 — without the flag, default behavior is unchanged (no spend needed — use `--stub`)

The channel's `exclude_shorts` is still 0 (slice C flips it), so a plain `discover` should include everything and leave the audit fields off.

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path /tmp/shortsB.sqlite \
  --project-name shortsB \
  --stub
sqlite3 -header -column /tmp/shortsB.sqlite "SELECT id, status, shorts_cutoff_seconds, n_episodes_total, n_shorts_excluded FROM discovery_runs ORDER BY id DESC LIMIT 1;"
```

**Pass if:** `shorts_cutoff_seconds` is NULL (empty in the column output), `n_shorts_excluded = 0`, `n_episodes_total` = total video count.

---

## Test 3 (optional) — `--include-shorts` override + all-shorts error

`--include-shorts` should force-include even if you hand-set the channel to exclude:

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && sqlite3 /tmp/shortsB.sqlite "UPDATE channels SET exclude_shorts = 1;" && PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover --db-path /tmp/shortsB.sqlite --project-name shortsB --stub --include-shorts && sqlite3 -header -column /tmp/shortsB.sqlite "SELECT shorts_cutoff_seconds, n_shorts_excluded FROM discovery_runs ORDER BY id DESC LIMIT 1;" && sqlite3 /tmp/shortsB.sqlite "UPDATE channels SET exclude_shorts = 0;"
```

**Pass if:** that run shows `shorts_cutoff_seconds` NULL and `n_shorts_excluded = 0` (the override won), and the trailing `UPDATE` resets the channel so later tests aren't affected.

(Skip the "all-shorts channel raises a clear error" case unless you happen to have a channel that's entirely Shorts — it's covered by unit tests.)

---

## When Tests 1–2 pass

Tell Claude "slice B smoke passed" — it'll start slice C (`feat/issue-03-shorts-flip-default-ui`): flips `channels.exclude_shorts` to 1 for every channel via a one-shot migration, computes the curation-orphan counts, and adds the read-only badge to `review_ui.py`. That slice also ends in a real-LLM verify (re-run plain `discover` on this DB and confirm the topic map is cleaner than the prior run, with the badge showing the counts).

Cleanup:

```bash
rm -f /tmp/shortsB.sqlite
```
