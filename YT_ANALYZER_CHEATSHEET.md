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

---

## 1. Create a fresh test DB

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

## 2. Broad topics

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

## 3. AI broad-topic suggestions

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

## 4. Subtopics

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

## 5. AI subtopic suggestions

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

## 6. Comparison groups

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

## 7. AI comparison-group suggestions

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

## 8. Transcript / processing / analysis

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

## 9. Search / overview / cleanup

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

## Fastest end-to-end smoke test

```bash
python3 -m yt_channel_analyzer.cli init-db \
  --db-path ./tmp/smoke.sqlite \
  --project-name "smoke" \
  --channel-id UCGq-a57w-aPwyi3pW7XLiHw \
  --channel-title "The Diary Of A CEO" \
  --channel-handle "@thediaryofaceo"

python3 -m yt_channel_analyzer.cli fetch-channel \
  --db-path ./tmp/smoke.sqlite \
  --project-name "smoke" \
  "@thediaryofaceo"

python3 -m yt_channel_analyzer.cli fetch-videos \
  --db-path ./tmp/smoke.sqlite \
  --limit 20

python3 -m yt_channel_analyzer.cli suggest-topics \
  --db-path ./tmp/smoke.sqlite

python3 -m yt_channel_analyzer.cli review-topic-suggestions \
  --db-path ./tmp/smoke.sqlite
```

---

## Notes

- Use `./tmp/project-fresh.sqlite` for the current main working DB unless you intentionally want a fresh test DB.
- Load `.env` before AI suggestion commands so `OPENAI_API_KEY` and `YOUTUBE_API_KEY` are available.
- `show-videos` is a sample view, not a full dump. Trust the `Video count:` line.
- AI suggestion workflows are reviewable first. Nothing should auto-apply unless you explicitly approve/apply.
- Keep this file updated whenever commands or CLI argument shapes change.
