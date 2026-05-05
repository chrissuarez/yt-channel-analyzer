# YouTube Channel Analyzer — Current State

## Purpose of this file

Quickest re-entry point when resuming work after a break.

Update it whenever:
- active focus changes
- a major bug is fixed
- a workflow becomes the new default
- the roadmap meaningfully changes

---

## Current project state

The project pivoted on **2026-05-04** from a *manual research workbench* framing to a *podcast knowledge extractor* framing. The vision now is:

> Point the app at a single podcast channel. Get an auto-discovered, reviewable topic map (Phase A). Later, get per-topic consensus / conflict / advice extraction (Phase C). Eventually, ask the channel free-form questions (Phase D).

See `PROJECT_SUMMARY.md` for the full restated vision and `ROADMAP.md` for the phased plan.

The strategy is **retrofit in place**, not greenfield — most of the existing ~600KB of code (ingestion, schema, review UI, topic suggestion machinery) carries over with repurposed semantics.

---

## What exists today

### Reused as-is
- Channel and video ingestion (`youtube.py`, `db.py`)
- SQLite schema (`channels`, `videos`, `topics`, `subtopics`)
- CLI setup, ingestion, taxonomy commands (`cli.py`)

### Repurposed under new semantics
- Review UI (`review_ui.py`) — review and curate the auto-discovered topic map
- Topic and subtopic suggestion machinery (`topic_suggestions.py`, `subtopic_suggestions.py`) — feed from metadata-derived discovery rather than direct title prompting
- The Topic Map view added in late April 2026 — already partway toward the new MVP

### Moved (or to be moved) to `legacy/`
- `comparison_group_suggestions.py`
- `group_analysis.py`
- Group markdown export from `markdown_export.py`
- Full-transcript pipeline from `processing.py` (dormant until Phase C)

---

## Current working assumptions

- SQLite is authoritative; vectors live in the same file via `sqlite-vec` once needed.
- One channel per project; multi-channel deferred.
- AI suggests, the human curates. Auto-discovered topics are reviewable, not authoritative.
- Episodes can belong to **multiple topics**, each assignment carrying a confidence score.
- Confidence is visible in the UI — low-confidence assignments are the ones the user should review first.
- GUI-led for routine use; CLI underneath for setup, automation, debugging.
- Phase A is metadata-only (titles, descriptions, chapters). Transcripts are NOT touched in MVP.

---

## Current build focus

### Primary focus: ship Phase A (the topic map MVP)

See `PRD_PHASE_A_TOPIC_MAP.md` for the detailed PRD.

The MVP user journey:
1. CLI: `analyze <channel>` ingests metadata + chapters.
2. **Discovery step** runs once, batched LLM call (~$0.10) → proposes broad topics + subtopics, assigns each episode multi-topic with confidence and short reason.
3. GUI opens to a **topic map view**: topics with episode counts.
4. Click a topic → see subtopics → see episodes assigned, sorted by recency or view count, each with title, thumbnail, guest, and a short "why this episode is here" line.
5. User can merge / rename / split topics, move episodes between subtopics, mark assignments wrong.
6. No transcripts. No claims. No Q&A. Just a working topic map.

### Why this focus
- It is the smallest version of the app that actually solves a problem the user has today ("which episodes are worth my time?").
- It validates the core human-in-the-loop curation pattern at the topic-map altitude.
- It costs cents to run a full channel through, so iteration is cheap.
- It produces an early, reviewable artifact before any commitment to the bigger Phase C spend.

---

## Known project tensions

### 1. Existing code carries old conceptual baggage
Tables, columns, and modules related to comparison groups still exist. They are being moved to `legacy/`, not deleted, in case Phase C reveals we want pieces back. Carry the baggage; don't pay for a rewrite of working plumbing.

### 2. Phase C is tempting; Phase A first
The exciting parts (consensus / conflict / advice extraction, Q&A) live in Phase C/D. The discipline is: ship Phase A and live with it for a while before committing the ~$8 backlog spend and the bigger build of Phase C. Phase A may already be enough; we won't know until we use it.

### 3. Multi-topic episodes affect the schema
Episodes can belong to many topics with confidence scores. Existing schema has primary + optional secondary topic — that needs extending (junction table) before MVP can ship cleanly. This is the first real schema change.

### 4. Confidence and provenance need to be visible
Auto-discovered assignments must show *why* an episode landed where it did (matched chapter title, matched description keyword, etc.) and *how confident* the system is. Without this, the curation UX feels like blind acceptance.

---

## Best next-step questions when resuming

1. Has the Phase A topic-map pipeline been built yet? If yes, has it been used on DOAC?
2. Has the schema been extended for multi-topic episode membership with confidence?
3. Has comparison-group code been moved to `legacy/`?
4. Is the review UI rendering the auto-discovered topic map, or still the old per-tag review surface?
5. What's the narrowest thing blocking the user from running Phase A end-to-end on Diary of a CEO?

---

## Suggested resume checklist

When restarting work:

1. Read:
   - `CURRENT_STATE.md` (this file)
   - `PROJECT_SUMMARY.md`
   - `ROADMAP.md`
   - `PRD_PHASE_A_TOPIC_MAP.md`
   - `WORKLOG.md` (most recent entries)
2. Identify the current Phase A build slice in progress.
3. Verify the relevant workflow locally before changing it.
4. Make the smallest useful change.
5. Test the affected workflow.
6. Update these docs if reality changed.

---

## Change log notes

### 2026-05-04 — Slice 01 session 1 (schema + stub discovery)
- Schema: `discovery_runs` table added; `video_topics`/`video_subtopics` extended with
  `confidence`, `reason`, `discovery_run_id`; `assignment_source` CHECK now includes `'auto'`.
- New module `discovery.py` with `run_discovery()` taking an injected LLM callable. 5 TDD
  tests in `test_discovery.py`.
- Slice 01 split across two sessions; session 2 picks up CHECK-constraint repair, CLI,
  GUI, and legacy move. See WORKLOG.md 2026-05-04 slice-01 entry for the resume plan.

### 2026-05-04 — Vision pivot + planning session
- Project reframed from "manual research workbench" to "podcast knowledge extractor."
- Unit of analysis confirmed as **the claim** (long-term); MVP unit is the episode-with-tags.
- MVP scope locked: **Phase A — topic map of the channel + episodes per topic, no transcripts.**
- Multi-topic episodes with visible confidence confirmed as the assignment model.
- Code strategy: retrofit in place. Comparison-group machinery → `legacy/`.
- LLM strategy: tiered models, batch APIs, local embeddings, `sqlite-vec`, process-once-store-forever.
- `PROJECT_SUMMARY.md`, `ROADMAP.md`, this file, and `WORKLOG.md` updated to reflect the new direction.
- `PRD_PHASE_A_TOPIC_MAP.md` written.
- Issue tracker bootstrapped (`/setup-matt-pocock-skills`): local-markdown convention under `.scratch/`, default triage labels, single-context layout. `AGENTS.md` and `docs/agents/*.md` created.
- Phase A broken into 11 vertical slices in `.scratch/phase-a-topic-map/issues/` (00–10), all `Status: needs-triage`.
- Architecture review (`/improve-codebase-architecture`) surfaced 5 deepening candidates. Candidate 2 (the **Extractor** Module) was fully designed: see [`docs/adr/0001-extractor-module.md`](docs/adr/0001-extractor-module.md), captured in slice 00.
- `CONTEXT.md` created with the project's domain glossary (Channel, Episode, Topic, Subtopic, Assignment, DiscoveryRun, Curation, TopicMap, Extractor, Claim).
- Candidates 1 (topic_map persistence), 3 (review_service), 4 (taxonomy_curation), 5 (rest of db.py) identified but **not yet designed**. Plan them before the slice they unblock — see `WORKLOG.md` 2026-05-04 entry for the schedule.

## Implementation start (next session)

Recommended starting point for the next coding session:

1. Read `PROJECT_SUMMARY.md`, this file, `PRD_PHASE_A_TOPIC_MAP.md`, `CONTEXT.md`, `docs/adr/0001-extractor-module.md`.
2. Read `.scratch/phase-a-topic-map/README.md` and the slice 00 + slice 01 issue files.
3. Slices 00 and 01 are both unblocked and can run in parallel. Slice 00 (Extractor) has zero coupling to anything else; slice 01 (tracer bullet) is intentionally allowed to be quick-and-dirty.
4. **Before starting slice 02**, run `/improve-codebase-architecture` again and design Candidate 1 (the `topic_map` persistence Module). Otherwise `discovery.py` will couple to `db.py` directly and you'll have to refactor it.
5. Same for Candidates 3 (before slice 06) and 4 (before slice 08).

### 2026-04-25
- Living project docs added to support resumable development.
- Captured that the UI exists because CLI-only testing and QA became too difficult in practice.
- Built the first-pass Topic Map view in `review_ui.py` (parts of which carry over).
