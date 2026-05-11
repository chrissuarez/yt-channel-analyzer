# Operator smoke — shorts-filter slice C (flip default + orphan counts + badge)

**Branch `feat/issue-03-shorts-flip-default-ui` is NOT merged yet** — this slice rewrites the `channels` table (a destructive migration) and changes behavior for *every* channel, so it stays on its branch until you've reviewed the migration and run this smoke. After it passes, Claude fast-forwards it into `main` and the shorts-filter feature is done.

Code is in place; offline gate is green (295 tests). What's left is operator-only: (1) eyeball the `channels` rebuild migration, (2) confirm it runs correctly on a real pre-slice-C DB and is idempotent, (3) a real-LLM `discover` run (~$0.02) to see the badge + orphan counts on live data.

Every fenced block is self-contained — copy whole. (Reminder: CLI only resolves from `~/.openclaw/workspace` with `PYTHONPATH=.`.)

Prereqs: `.env` has `YOUTUBE_API_KEY` + `ANTHROPIC_API_KEY`.

---

## 0. Eyeball the migration (no commands — just read)

Open `db.py` and find `_repair_channels_exclude_shorts_default` (called from `ensure_schema()` alongside the other `_repair_*` functions). Confirm you're OK with: it only fires when the `channels` create-SQL still says `exclude_shorts INTEGER NOT NULL DEFAULT 0`; it runs `UPDATE channels SET exclude_shorts = 1` once, then RENAME→CREATE(new, `DEFAULT 1`)→INSERT SELECT(all columns by name)→DROP old, with `foreign_keys=OFF` + `legacy_alter_table=ON` so `videos`/`discovery_runs` rows that FK `channel_id` survive (same dance as `_repair_discovery_runs_status_constraint`). After the rebuild the create-SQL says `DEFAULT 1`, so it's a no-op forever after — that's the idempotency guard (no marker table, matching the existing repair functions).

If anything there bothers you, stop and tell Claude before proceeding.

---

## 1. Build a pre-slice-C DB (on `main`), then run the migration (on the branch)

While on `main`, ingest a channel — its `channels` table will have the old `DEFAULT 0`:

```bash
cd ~/.openclaw/workspace/yt_channel_analyzer && git checkout main && cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && rm -f /tmp/shortsC.sqlite && PYTHONPATH=. python3 -m yt_channel_analyzer.cli analyze \
  --db-path /tmp/shortsC.sqlite \
  --project-name shortsC \
  --channel-input "https://www.youtube.com/@TheDiaryOfACEO" \
  --limit 15 \
  --stub
```

Confirm it's the old shape (should print `... DEFAULT 0 ...` and `exclude_shorts = 0` for the channel):

```bash
sqlite3 /tmp/shortsC.sqlite ".schema channels" | grep exclude_shorts; sqlite3 -header -column /tmp/shortsC.sqlite "SELECT youtube_channel_id, exclude_shorts FROM channels;"
```

Now switch to the slice-C branch and trigger `ensure_schema()` against that DB (a read-only `show-channels` is enough — every db.py entry point runs `ensure_schema`):

```bash
cd ~/.openclaw/workspace/yt_channel_analyzer && git checkout feat/issue-03-shorts-flip-default-ui && cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && PYTHONPATH=. python3 -m yt_channel_analyzer.cli show-channels --db-path /tmp/shortsC.sqlite
```

Check the migration happened:

```bash
sqlite3 /tmp/shortsC.sqlite ".schema channels" | grep exclude_shorts; sqlite3 -header -column /tmp/shortsC.sqlite "SELECT youtube_channel_id, exclude_shorts FROM channels;"; echo "--- videos still there? ---"; sqlite3 /tmp/shortsC.sqlite "SELECT count(*) AS videos, count(duration_seconds) AS with_duration FROM videos;"
```

**Pass if:** the schema line now reads `exclude_shorts INTEGER NOT NULL DEFAULT 1`, every channel row shows `exclude_shorts = 1`, and the videos count + `with_duration` are unchanged from before the migration (child rows survived the `channels` rebuild).

---

## 2. Idempotency — a hand-set-back-to-0 channel stays 0

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && sqlite3 /tmp/shortsC.sqlite "UPDATE channels SET exclude_shorts = 0;" && PYTHONPATH=. python3 -m yt_channel_analyzer.cli show-channels --db-path /tmp/shortsC.sqlite && sqlite3 -header -column /tmp/shortsC.sqlite "SELECT youtube_channel_id, exclude_shorts FROM channels;"
```

**Pass if:** `exclude_shorts` is still `0` after the second `ensure_schema()` — the migration did not re-flip it (the create-SQL already says `DEFAULT 1`, so the repair function is a no-op now).

Reset it back to 1 for the next test:

```bash
sqlite3 /tmp/shortsC.sqlite "UPDATE channels SET exclude_shorts = 1;"
```

---

## 3. Real-LLM `discover` — badge + orphan counts on live data (~$0.02)

A plain `discover` now uses the flipped default (`exclude_shorts = 1`), so Shorts are filtered without any flag. Optionally first create a wrong-mark or rename so the orphan counts have something to count — easiest is just to run discover and see the shorts count:

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && RALPH_ALLOW_REAL_LLM=1 PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path /tmp/shortsC.sqlite \
  --project-name shortsC \
  --real
sqlite3 -header -column /tmp/shortsC.sqlite "SELECT id, status, shorts_cutoff_seconds, n_episodes_total, n_shorts_excluded, n_orphaned_wrong_marks, n_orphaned_renames FROM discovery_runs ORDER BY id DESC LIMIT 1;"
```

**Pass if:** `status = success`, `shorts_cutoff_seconds = 180`, `n_shorts_excluded > 0` (the Shorts in the 15 fetched videos), `n_orphaned_wrong_marks` and `n_orphaned_renames` are `0` (no curation on this fresh DB — non-NULL, not blank).

Now look at the badge in the UI:

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a && PYTHONPATH=. python3 -m yt_channel_analyzer.cli serve-review-ui --db-path /tmp/shortsC.sqlite --host 127.0.0.1
```

Open <http://127.0.0.1:8765> → the discovery topic-map header should show a line like **"N shorts excluded · 0 curation actions inert (target episodes filtered)"** under the run meta. (`Ctrl-C` the server when done.) If you want to see the "inert curation actions" half move off 0: in the UI, mark one of the *kept* episode's assignments as wrong, then re-run the `discover --real` block above and re-check — `n_orphaned_wrong_marks` only counts wrong-marks against *filtered* episodes, so to exercise it you'd mark-wrong an episode and then it'd need to be a Short... easier to just trust the unit tests for that path; the 0/0 badge rendering is the thing to eyeball here.

**Optional — "cleaner than the prior run":** if you kept `/tmp/shortsB.sqlite` from the slice-B smoke, that DB already has runs both with and without the filter; eyeball that the filtered run's topic map has fewer/cleaner topics. Not required.

---

## When 1–3 pass

Tell Claude "slice C smoke passed" — it'll fast-forward `feat/issue-03-shorts-flip-default-ui` into `main`, delete the branch + stale overlay, and the shorts-filter feature is complete (3/3 slices). It'll also add the closing WORKLOG note about the default flip.

Cleanup:

```bash
rm -f /tmp/shortsC.sqlite /tmp/shortsB.sqlite /tmp/shorts-smoke.sqlite /tmp/shorts-legacy.sqlite
```
