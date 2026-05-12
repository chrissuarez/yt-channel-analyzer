# Phase A operator workflow

End-to-end recipe for going from a YouTube channel to a curated topic map.
Phase A is metadata-only (titles, descriptions, chapters) — no transcripts.

For background, see `PROJECT_SUMMARY.md` and `PRD_PHASE_A_TOPIC_MAP.md`.
For per-command reference, see `YT_ANALYZER_CHEATSHEET.md`.

---

## 0. Prerequisites

```bash
cd /home/chris/.openclaw/workspace
source .venv/bin/activate
set -a; source .env; set +a   # exposes ANTHROPIC_API_KEY, YOUTUBE_API_KEY
```

The Phase A pipeline calls Anthropic (Haiku 4.5 by default) and the
YouTube Data API. Real-LLM mode is gated by `RALPH_ALLOW_REAL_LLM=1`
on top of passing `--real` (instead of `--stub`) — see step 3.

---

## 1. Initialize the project DB

One DB per channel/project. The init step writes schema + the primary
channel row in one shot.

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli init-db \
  --db-path ./tmp/doac.sqlite \
  --project-name "doac" \
  --channel-id UCGq-a57w-aPwyi3pW7XLiHw \
  --channel-title "The Diary Of A CEO" \
  --channel-handle "@thediaryofaceo"
```

If you only have the handle, run `fetch-channel` first to resolve it:

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli fetch-channel \
  --db-path ./tmp/doac.sqlite \
  --project-name "doac" \
  "@thediaryofaceo"
```

---

## 2. Ingest videos

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli fetch-videos \
  --db-path ./tmp/doac.sqlite \
  --limit 50
```

Sanity-check the ingest:

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli show-project-overview \
  --db-path ./tmp/doac.sqlite
```

Trust `Video count:` in the overview — `show-videos` is a sample, not
a full dump.

---

## 3. Run discovery

`discover` is the Phase A topic-map pipeline: one batched LLM call
proposes broad topics + subtopics and assigns each episode multi-topic
with confidence and a short reason. Results are persisted under a
`discovery_runs` row so re-runs are tracked.

### Stub mode (free, deterministic, for development)

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/doac.sqlite \
  --project-name "doac" \
  --stub
```

### Real-LLM mode (paid, ~$0.019 per 15 episodes on Haiku 4.5)

```bash
RALPH_ALLOW_REAL_LLM=1 \
  PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/doac.sqlite \
  --project-name "doac" \
  --real
```

`--stub` and `--real` are a required mutex — pass exactly one. The
`RALPH_ALLOW_REAL_LLM=1` env var plus `ANTHROPIC_API_KEY` are both
required for `--real`; without the env var, the run fails fast with a
`RuntimeError` before any API call. Override the model with
`--model claude-sonnet-4-6` (or any other Anthropic model id);
default is `extractor.anthropic_runner.DEFAULT_MODEL` (Haiku 4.5).

For an instrumented real-LLM smoke (token-cost report, written to
`/tmp/doac-smoke-<ts>.db`), the script at `.scratch/issue-02/smoke.py`
remains the canonical reference:

```bash
PYTHONPATH=. python3 yt_channel_analyzer/.scratch/issue-02/smoke.py
```

---

## 4. Review the topic map

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli serve-review-ui \
  --db-path ./tmp/doac.sqlite
```

Default bind is `127.0.0.1:8765`. The Discovery view lists topics with
episode counts; expand a topic to see its subtopics with per-subtopic
counts; expand a subtopic to see assigned episodes with confidence,
reason, and thumbnail.

Low-confidence filter: assignments below `YTA_LOW_CONFIDENCE_THRESHOLD`
(default `0.5`) get visually flagged. Tune with:

```bash
YTA_LOW_CONFIDENCE_THRESHOLD=0.6 \
  PYTHONPATH=. python3 -m yt_channel_analyzer.cli serve-review-ui \
  --db-path ./tmp/doac.sqlite
```

---

## 5. Curate

In the Discovery view, each episode card has **Wrong topic** and
**Wrong subtopic** buttons. Clicking either:

- Removes that assignment from `video_topics` / `video_subtopics`
- Records the event in the `wrong_assignments` table for future
  re-runs to learn from (slice 08 territory).

For renames, splits, merges, and manual additions, fall through to
the CLI taxonomy commands documented in `YT_ANALYZER_CHEATSHEET.md`
§§ 2 + 4 (`create-topic`, `rename-topic`, `assign-topic`,
`create-subtopic`, `rename-subtopic`, etc.).

---

## 6. Re-run discovery

Discovery runs are append-only; each call writes a new
`discovery_runs` row. The review UI shows the latest run's
assignments. To compare runs, query `discovery_runs` directly:

```bash
sqlite3 ./tmp/doac.sqlite \
  'SELECT id, created_at, model, prompt_version FROM discovery_runs ORDER BY id'
```

Sticky curation (decisions persisting across re-runs) is issue 08 —
not yet wired.

---

## Phase B — sample-based transcript refinement

Once a discovery run looks reasonable, refine a representative sample of
episodes from their transcripts (one LLM call per transcript) to catch
topics/subtopics the title+description pass missed and re-judge a few
episodes from what was actually said. ~$0.40 for a ~15-episode sample.

```bash
# 1. (recommended) a fresh discovery run with the Shorts filter on, so the
#    sample comes from a clean non-Short run
RALPH_ALLOW_REAL_LLM=1 python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/doac.sqlite --project-name "DOAC" --real

# 2. wiring sanity check (offline — stub LLM + fake transcript fetcher)
python3 -m yt_channel_analyzer.cli refine \
  --db-path ./tmp/doac.sqlite --project-name "DOAC" --stub

# 3. real run — prints the picked sample size + an estimated cost and asks
#    to confirm before the LLM call (transcripts for the sample are fetched
#    automatically; or pre-fetch with `fetch-transcripts`)
RALPH_ALLOW_REAL_LLM=1 python3 -m yt_channel_analyzer.cli refine \
  --db-path ./tmp/doac.sqlite --project-name "DOAC" --real        # --yes to skip the prompt

# 4. inspect what it proposed
sqlite3 ./tmp/doac.sqlite \
  'SELECT kind, name, parent_topic_name, status FROM taxonomy_proposals ORDER BY id'
```

Steps 2–4 also have a UI: `serve-review-ui` → the **Refine** stage (4th
stepper step). It shows the auto-picked sample (editable — remove rows, add
by video ID/URL), a "Fetch transcripts & estimate" action, and a "Run
refinement ($X.XX)" button (`--real` confirms with the dollar estimate;
needs `RALPH_ALLOW_REAL_LLM=1` on the server). It polls to completion, then
renders, below the sample:

- a **proposal-review screen** — every pending `taxonomy_proposal` grouped
  by run (newest first; subtopics under their parent topic, then topics),
  each card carrying the name / parent / transcript evidence / source
  episode and an **Accept** / **Reject** button. Accept creates the real
  `topics`/`subtopics` node (parent resolved through the rename log;
  idempotent — re-accepting just ensures the node exists; a parent that no
  longer exists is reported and the proposal marked rejected). Reject just
  marks the row. Both refresh the screen in place.
- a **before → after** sanity panel — per sampled episode, the topics
  added / dropped and subtopics corrected vs. the metadata pass, plus the
  full set of transcript-grade assignments with a "✗ wrong" control per
  topic (reuses `/api/discovery/episode/mark-wrong`).
- a **"re-run Discover"** nudge once you've accepted anything this session.

Accepting a proposal creates the real `topics`/`subtopics` row. After
accepting, re-run `discover` — it is taxonomy-aware (feeds the curated
names into the prompt) and never downgrades a `refine`/`manual` assignment,
so the re-run spreads the accepted nodes across the rest of the channel
without touching the transcript-grade rows. `refine`-source episodes show a
"transcript-checked" pill in the topic map. `refinement_runs` rows are
append-only like `discovery_runs`; a re-`refine` is non-destructive to
earlier runs.

Operator runbook for a first real pass: `.scratch/phase-b-refinement/SMOKE.md`.

---

## What's not in Phase A

The legacy AI suggestion pipeline (`suggest-topics`,
`suggest-subtopics`, `suggest-comparison-groups`) and the
group-analysis pipeline (`fetch-group-transcripts`,
`process-group-videos`, `analyze-comparison-group`,
`export-group-markdown`) still exist in the CLI. They predate Phase A
and target Phase C (transcripts, claims, group-level analysis). They
are not part of the Phase A operator workflow and call sites that
touch the moved-to-`legacy/` modules will print a `[legacy]` stderr
warning.

See `YT_ANALYZER_CHEATSHEET.md` §§ 3, 5, 6, 7, 8 if you need them.
