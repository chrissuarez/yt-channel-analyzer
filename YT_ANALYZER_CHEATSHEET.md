# yt_channel_analyzer Cheat Sheet

Practical command reference for the current CLI-first `yt_channel_analyzer` build.

## Setup

From the workspace root:

```bash
cd /home/chris/.openclaw/workspace
source .venv/bin/activate
set -a
source .env
set +a
```

For the end-to-end Phase A walkthrough, see
[`docs/operator-workflow.md`](docs/operator-workflow.md). This file is
the per-command reference.

---

## 1. Phase A: discover + review (primary workflow)

The single-pass topic-map pipeline. One LLM call per run produces broad
topics + subtopics + per-episode multi-topic assignments with
confidence and reason.

### Run discovery (stub)

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/doac.sqlite \
  --project-name "doac" \
  --stub
```

`--stub` and `--real` are a required mutex on `discover` and `analyze` —
pass exactly one. Stub is free + deterministic (no API call); use it
for wiring sanity checks.

### Run discovery (real LLM, ~$0.019 per 15 episodes)

```bash
RALPH_ALLOW_REAL_LLM=1 \
  PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/doac.sqlite \
  --project-name "doac" \
  --real
```

`RALPH_ALLOW_REAL_LLM=1` and `ANTHROPIC_API_KEY` are both required for
`--real`. Override the model with `--model claude-sonnet-4-6` (or any
Anthropic model id); default is Haiku 4.5. For an instrumented
real-LLM smoke with token-cost reporting, see
`.scratch/issue-02/smoke.py`.

### Serve the review UI

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli serve-review-ui \
  --db-path ./tmp/doac.sqlite
```

Default bind `127.0.0.1:8000`. Discovery view lists topics → expand to
subtopics → expand to episodes with confidence + reason + thumbnail.

### Tune low-confidence flagging

```bash
YTA_LOW_CONFIDENCE_THRESHOLD=0.6 \
  PYTHONPATH=. python3 -m yt_channel_analyzer.cli serve-review-ui \
  --db-path ./tmp/doac.sqlite
```

Default threshold `0.5`. Assignments below threshold render with a
visual flag. Curate them via **Wrong topic** / **Wrong subtopic**
buttons in the UI; events are logged to the `wrong_assignments` table.

### List discovery runs (sqlite3)

```bash
sqlite3 ./tmp/doac.sqlite \
  'SELECT id, created_at, model, prompt_version FROM discovery_runs ORDER BY id'
```

---

## 2. Create a fresh test DB

### Init DB

```bash
python3 -m yt_channel_analyzer.cli init-db \
  --db-path ./tmp/test.sqlite \
  --project-name "test" \
  --channel-id UCGq-a57w-aPwyi3pW7XLiHw \
  --channel-title "The Diary Of A CEO" \
  --channel-handle "@thediaryofaceo"
```

### Fetch channel metadata

```bash
python3 -m yt_channel_analyzer.cli fetch-channel \
  --db-path ./tmp/test.sqlite \
  --project-name "test" \
  "@thediaryofaceo"
```

### Fetch latest videos

```bash
python3 -m yt_channel_analyzer.cli fetch-videos \
  --db-path ./tmp/test.sqlite \
  --limit 20
```

### Inspect import

```bash
python3 -m yt_channel_analyzer.cli show-project-overview --db-path ./tmp/test.sqlite
python3 -m yt_channel_analyzer.cli show-videos --db-path ./tmp/test.sqlite
```

---

## 3. Broad topics (manual curation)

### Create topic manually

```bash
python3 -m yt_channel_analyzer.cli create-topic \
  --db-path ./tmp/test.sqlite \
  --topic-name "Health & Wellness"
```

### List topics

```bash
python3 -m yt_channel_analyzer.cli list-topics \
  --db-path ./tmp/test.sqlite
```

### Assign topic manually

```bash
python3 -m yt_channel_analyzer.cli assign-topic \
  --db-path ./tmp/test.sqlite \
  --video-id fgNa77-6-JM \
  --topic-name "Health & Wellness" \
  --assignment-type primary
```

### Inspect one video’s topics

```bash
python3 -m yt_channel_analyzer.cli show-video-topics \
  --db-path ./tmp/test.sqlite \
  --video-id fgNa77-6-JM
```

---

## 4. AI broad-topic suggestions *(legacy multi-step flow — superseded by §1 `discover` for Phase A; kept for Phase C use)*

### Generate suggestions

```bash
python3 -m yt_channel_analyzer.cli suggest-topics \
  --db-path ./tmp/test.sqlite
```

### List suggestion runs

```bash
python3 -m yt_channel_analyzer.cli list-topic-suggestion-runs \
  --db-path ./tmp/test.sqlite
```

### Review latest run

```bash
python3 -m yt_channel_analyzer.cli review-topic-suggestions \
  --db-path ./tmp/test.sqlite
```

### Review a specific run

```bash
python3 -m yt_channel_analyzer.cli review-topic-suggestions \
  --db-path ./tmp/test.sqlite \
  --run-id 3
```

### Summarize grouped labels

```bash
python3 -m yt_channel_analyzer.cli summarize-topic-suggestion-labels \
  --db-path ./tmp/test.sqlite \
  --run-id 3 \
  --status pending
```

### Approve a suggested label

```bash
python3 -m yt_channel_analyzer.cli approve-topic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --run-id 3 \
  --label "Health & Wellness"
```

Approving the label creates or approves the topic label only. It does not assign that topic to videos yet.
Run bulk apply next so downstream subtopic suggestions can see those videos.

### Approve and rename in one step

```bash
python3 -m yt_channel_analyzer.cli approve-topic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --run-id 3 \
  --label "Health" \
  --approved-name "Health & Wellness"
```

### Reject a suggested label

```bash
python3 -m yt_channel_analyzer.cli reject-topic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --run-id 3 \
  --label "Law"
```

### Rename a pending suggested label

```bash
python3 -m yt_channel_analyzer.cli rename-topic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --run-id 3 \
  --current-name "Health Science" \
  --new-name "Health & Wellness"
```

### Bulk apply an approved label

```bash
python3 -m yt_channel_analyzer.cli bulk-apply-topic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --run-id 3 \
  --label "Health & Wellness"
```

### Supersede stale pending suggestions from older runs

```bash
python3 -m yt_channel_analyzer.cli supersede-stale-topic-suggestions \
  --db-path ./tmp/test.sqlite \
  --keep-run-id 3
```

---

## 5. Subtopics (manual curation)

### Create subtopic manually

```bash
python3 -m yt_channel_analyzer.cli create-subtopic \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness" \
  --name "Longevity & Toxins"
```

### List subtopics under a topic

```bash
python3 -m yt_channel_analyzer.cli list-subtopics \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness"
```

### Assign subtopic manually

```bash
python3 -m yt_channel_analyzer.cli assign-subtopic \
  --db-path ./tmp/test.sqlite \
  --video-id fgNa77-6-JM \
  --subtopic "Longevity & Toxins"
```

### Inspect one video’s subtopics

```bash
python3 -m yt_channel_analyzer.cli show-video-subtopics \
  --db-path ./tmp/test.sqlite \
  --video-id fgNa77-6-JM
```

---

## 6. AI subtopic suggestions *(legacy multi-step flow — superseded by §1 `discover`)*

### Generate subtopic suggestions for one approved broad topic

```bash
python3 -m yt_channel_analyzer.cli suggest-subtopics \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness"
```

### Review latest subtopic suggestion run for a topic

```bash
python3 -m yt_channel_analyzer.cli review-subtopic-suggestions \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness"
```

### List subtopic suggestions

```bash
python3 -m yt_channel_analyzer.cli list-subtopic-suggestions \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness"
```

### Summarize subtopic suggestion labels

```bash
python3 -m yt_channel_analyzer.cli summarize-subtopic-suggestion-labels \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness"
```

### Approve subtopic suggestion label

```bash
python3 -m yt_channel_analyzer.cli approve-subtopic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness" \
  --label "Longevity & Toxins"
```

### Reject subtopic suggestion label

```bash
python3 -m yt_channel_analyzer.cli reject-subtopic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness" \
  --label "Bad Label"
```

### Rename pending subtopic suggestion label

```bash
python3 -m yt_channel_analyzer.cli rename-subtopic-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --topic "Health & Wellness" \
  --current-name "Aging Science" \
  --new-name "Longevity & Toxins"
```

---

## 7. Comparison groups *(Phase C — module under `legacy/`; CLI prints `[legacy]` warning)*

### Create comparison group manually

```bash
python3 -m yt_channel_analyzer.cli create-comparison-group \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins" \
  --name "Longevity - Toxins"
```

### List comparison groups

```bash
python3 -m yt_channel_analyzer.cli list-comparison-groups \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins"
```

### Add a video to a comparison group

```bash
python3 -m yt_channel_analyzer.cli add-video-to-comparison-group \
  --db-path ./tmp/test.sqlite \
  --video-id fgNa77-6-JM \
  --group "Longevity - Toxins"
```

### Inspect a group

```bash
python3 -m yt_channel_analyzer.cli show-comparison-group \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

---

## 8. AI comparison-group suggestions *(Phase C — module under `legacy/`; CLI prints `[legacy]` warning)*

### Generate comparison-group suggestions for one approved subtopic

```bash
python3 -m yt_channel_analyzer.cli suggest-comparison-groups \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins"
```

### Review latest comparison-group suggestion run

```bash
python3 -m yt_channel_analyzer.cli review-comparison-group-suggestions \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins"
```

### List comparison-group suggestions

```bash
python3 -m yt_channel_analyzer.cli list-comparison-group-suggestions \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins"
```

### Summarize comparison-group suggestion labels

```bash
python3 -m yt_channel_analyzer.cli summarize-comparison-group-suggestion-labels \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins"
```

### Approve comparison-group suggestion label

```bash
python3 -m yt_channel_analyzer.cli approve-comparison-group-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins" \
  --label "Environmental Toxins"
```

### Reject comparison-group suggestion label

```bash
python3 -m yt_channel_analyzer.cli reject-comparison-group-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins" \
  --label "Bad Group"
```

### Rename pending comparison-group suggestion label

```bash
python3 -m yt_channel_analyzer.cli rename-comparison-group-suggestion-label \
  --db-path ./tmp/test.sqlite \
  --subtopic "Longevity & Toxins" \
  --current-name "Detox" \
  --new-name "Environmental Toxins"
```

---

## 9. Transcript / processing / analysis *(Phase C — modules under `legacy/`; CLI prints `[legacy]` warning)*

### Fetch transcripts for one group

```bash
python3 -m yt_channel_analyzer.cli fetch-group-transcripts \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

### Inspect transcript results

```bash
python3 -m yt_channel_analyzer.cli show-group-transcripts \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

### Process videos in group

```bash
python3 -m yt_channel_analyzer.cli process-group-videos \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

### Inspect processing

```bash
python3 -m yt_channel_analyzer.cli show-group-processing \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

### Analyze group

```bash
python3 -m yt_channel_analyzer.cli analyze-comparison-group \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

### Inspect analysis

```bash
python3 -m yt_channel_analyzer.cli show-group-analysis \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

### Export markdown

```bash
python3 -m yt_channel_analyzer.cli export-group-markdown \
  --db-path ./tmp/test.sqlite \
  --group-name "Longevity - Toxins"
```

---

## 10. Search / overview / cleanup

### Search stored library

```bash
python3 -m yt_channel_analyzer.cli search-library \
  --db-path ./tmp/test.sqlite \
  --query "plastic receipts"
```

### Overview

```bash
python3 -m yt_channel_analyzer.cli show-project-overview \
  --db-path ./tmp/test.sqlite
```

### Rename topic

```bash
python3 -m yt_channel_analyzer.cli rename-topic \
  --db-path ./tmp/test.sqlite \
  --current-name "Health" \
  --new-name "Health & Wellness"
```

### Rename subtopic

```bash
python3 -m yt_channel_analyzer.cli rename-subtopic \
  --db-path ./tmp/test.sqlite \
  --current-name "Aging Science" \
  --new-name "Longevity & Toxins"
```

### Rename comparison group

```bash
python3 -m yt_channel_analyzer.cli rename-comparison-group \
  --db-path ./tmp/test.sqlite \
  --current-name "Detox" \
  --new-name "Environmental Toxins"
```

---

## Fastest end-to-end smoke test (Phase A, stub)

```bash
PYTHONPATH=. python3 -m yt_channel_analyzer.cli init-db \
  --db-path ./tmp/smoke.sqlite \
  --project-name "smoke" \
  --channel-id UCGq-a57w-aPwyi3pW7XLiHw \
  --channel-title "The Diary Of A CEO" \
  --channel-handle "@thediaryofaceo"

PYTHONPATH=. python3 -m yt_channel_analyzer.cli fetch-channel \
  --db-path ./tmp/smoke.sqlite \
  --project-name "smoke" \
  "@thediaryofaceo"

PYTHONPATH=. python3 -m yt_channel_analyzer.cli fetch-videos \
  --db-path ./tmp/smoke.sqlite \
  --limit 20

PYTHONPATH=. python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/smoke.sqlite \
  --project-name "smoke" \
  --stub

PYTHONPATH=. python3 -m yt_channel_analyzer.cli serve-review-ui \
  --db-path ./tmp/smoke.sqlite
```

---

## Notes

- Use `./tmp/project-fresh.sqlite` for the current main working DB unless you intentionally want a fresh test DB.
- Load `.env` before any real-LLM command so `ANTHROPIC_API_KEY` and `YOUTUBE_API_KEY` are available.
- `show-videos` is a sample view, not a full dump. Trust the `Video count:` line.
- AI suggestion workflows are reviewable first. Nothing should auto-apply unless you explicitly approve/apply.
- Phase A primary path is `discover` + `serve-review-ui` (§1). The §§ 4, 6, 7, 8, 9 multi-step suggestion + comparison-group flows are legacy / Phase C.
- Keep this file updated whenever commands or CLI argument shapes change.
