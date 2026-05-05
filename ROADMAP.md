# YouTube Channel Analyzer — Roadmap

## Roadmap intent

This roadmap reflects the **2026-05-04 vision pivot**. The project is now a phased podcast knowledge extractor (see `PROJECT_SUMMARY.md`). This file answers:

- What is already done?
- What is the current phase and its concrete next steps?
- What is intentionally deferred?

Update this file when priorities change.

---

## Done / substantially present

### Foundation (carries over unchanged)
- [x] project-scoped SQLite DB, rerun-safe setup
- [x] single-channel MVP operating model
- [x] channel resolution from ID / handle / URL
- [x] channel and video metadata ingestion
- [x] limited-fetch support for testing

### Existing structure (carries over with repurposed semantics)
- [x] `topics`, `subtopics` tables and CRUD
- [x] primary + optional secondary topic on a video
- [x] review UI with suggest/review/approve/apply patterns
- [x] first-pass Topic Map view in the GUI (April 2026)
- [x] Approve + apply flows for topic and subtopic suggestions
- [x] selected-topic detail panel with assigned/unassigned inventory

### Existing AI suggestion machinery (will be repurposed)
- [x] broad-topic suggestion generation, review, approve, rename, bulk apply, supersede
- [x] subtopic suggestion generation with cluster-size threshold
- [x] run-scoped suggestion history

### Existing comparison-group machinery (moving to `legacy/`)
- [x] comparison group CRUD and membership management
- [x] comparison group suggestion logic
- [x] selective transcript fetching for chosen groups
- [x] deterministic transcript processing
- [x] group-level analysis
- [x] group markdown export

These are not deleted. They move to `legacy/` for archival reference and possible Phase C revival.

---

## Current active focus — Phase A: Topic Map MVP

See `PRD_PHASE_A_TOPIC_MAP.md` for the detailed plan.

The smallest version of the app that solves a real user problem ("which episodes are worth my time?") with no transcript spend.

### Phase A — concrete next steps

#### A1. Schema extension for multi-topic membership
- [x] Add `video_topics` junction table: `(video_id, topic_id, confidence, source, reason)` — extended existing table with `confidence`, `reason`, `discovery_run_id`; `assignment_source` CHECK now allows `'auto'`
- [x] Add `video_subtopics` junction table: `(video_id, subtopic_id, confidence, source, reason)` — same shape as `video_topics`
- [x] Add `discovery_runs` table to track Phase A runs (one per channel discovery pass)
- [x] Repair path: `_repair_video_topic_assignment_source_constraint` rebuilds old-shape junction tables whose CHECK lacks `'auto'` (rename → recreate → INSERT SELECT → drop)
- [ ] Migration that backfills existing primary/secondary topic data into the junction table
- [ ] Existing primary/secondary columns retained for backward compatibility but no longer authoritative

#### A2. Discovery module (`discovery.py`)
- [x] Module skeleton with `DiscoveryVideo`, `DiscoveryAssignment`, `DiscoveryPayload`, `run_discovery(...)` — LLM injected as a callable; persists run + topics + assignments
- [x] Stub LLM (`stub_llm` returns one topic "General" with every video assigned, confidence=1.0); constants `STUB_MODEL` / `STUB_PROMPT_VERSION`
- [x] CLI `discover --db-path --project-name --stub` wired to `run_discovery` (the `--stub` flag is required until the real LLM lands)
- [ ] Pull all videos for the channel: title, description, chapter markers
- [ ] Pre-filter common boilerplate (sponsor reads, social calls-to-action) from descriptions
- [ ] Build a single batched LLM call (Haiku 4.5 or GPT-4o-mini)
- [ ] Prompt produces: list of broad topics with subtopics, plus per-episode topic/subtopic assignments with confidence (0.0–1.0) and a short reason string
- [ ] Validate response shape; reject malformed batches; retry once
- [ ] Persist to `topics`, `subtopics`, junction tables, `discovery_runs` *(persistence done; awaits real payload)*
- [x] CLI: `analyze --db-path --project-name --channel-input --stub` chains channel resolve → metadata upsert → videos fetch+upsert → `run_discovery(stub_llm)` (Ralph iteration 3, commit `cc70ccd`)

#### A3. Topic map UI (extend `review_ui.py`)
- [x] `/api/state` payload: `discovery_topic_map` key surfaces latest run's topics with episode count + average confidence (Ralph iteration 4, commit `89437b7`)
- [x] Render auto-discovered topic map in HTML/JS: topics with episode counts, subtopic counts, average confidence (Ralph iteration 5 — panel above the pre-pivot Topic Map; subtopic counts deferred until §A2 LLM produces real subtopics)
- [ ] Topic detail: subtopics + episodes assigned to each *(episodes done Ralph iteration 6; subtopics deferred until §A2 LLM produces them)*
- [x] Per-episode card: title, thumbnail, "why this episode is here" reason, confidence indicator (Ralph iteration 6, commit `f2db466`; faded/muted styling for low confidence; guest deferred — not currently extracted)
- [x] Episodes appear under every topic they belong to (multi-topic display) (Ralph iteration 6)
- [ ] Curation actions: rename topic, merge two topics, split a topic, move episode between subtopics, mark assignment as wrong
- [x] Sort options for episode lists: recency, confidence (Ralph iteration 7, JS-side per-topic dropdown, default recency; view count deferred — not currently ingested)

#### A4. Move legacy code
- [ ] Create `legacy/` directory
- [ ] Move `comparison_group_suggestions.py`, `group_analysis.py` to `legacy/`
- [ ] Move group-related parts of `markdown_export.py` to `legacy/`
- [ ] Move full-transcript pipeline parts of `processing.py` to `legacy/` (keep imports working from `legacy.processing` if anything still references them)
- [ ] Remove comparison-group surfaces from the GUI primary navigation; keep behind an "Advanced / Legacy" disclosure if needed for backward access
- [ ] Update `cli.py` so comparison-group commands still work but warn that they're legacy

#### A5. Documentation and operator guidance
- [ ] Document the Phase A end-to-end operator workflow
- [ ] Update `YT_ANALYZER_CHEATSHEET.md` to reflect the new primary commands
- [ ] First real run: ingest Diary of a CEO, run discovery, review the resulting topic map

---

## Future phases (planned, not active)

### Phase B — Sample-based taxonomy refinement
Priority: medium

Resume after Phase A has been used on at least one real channel for a week.

- [ ] Add transcript fetching for a sampled subset (15–20 episodes representative of all topics)
- [ ] Run a coarse claim extraction pass on the sample
- [ ] Cluster sampled claims; surface clusters that don't fit the existing taxonomy
- [ ] User reviews proposed taxonomy additions/splits
- [ ] Apply changes to the topic map

### Phase C — Full claim extraction and synthesis
Priority: medium-high (once Phase A feels right)

- [ ] Full transcript ingestion for the channel (`youtube-transcript-api`; Whisper fallback if needed)
- [ ] Boilerplate filtering (ad reads, intros)
- [ ] Claim extraction prompt (Haiku batch): atomic claims with topic, subtopic, speaker, claim type, confidence signals, source episode + timestamp
- [ ] `claims` table with full provenance
- [ ] Embed claims via local sentence-transformers; store in `sqlite-vec` table
- [ ] Per-topic clustering of claims
- [ ] Consensus surfacing (clusters with many distinct guests)
- [ ] Conflict surfacing (contradictory clusters within a topic — needs an LLM compare step)
- [ ] "Most useful advice" surfacing (advice-typed claims, ranked by cluster density and guest diversity)
- [ ] UI: per-topic Synthesis tab with consensus / conflict / advice sections
- [ ] All claim views link back to source episode + timestamp

### Phase D — Natural-language Q&A
Priority: medium-later

- [ ] Query embedding + retrieval over claim store
- [ ] Synthesis call (Sonnet) with retrieved claims as context
- [ ] Answers always cite source episodes + timestamps
- [ ] UI: search bar / question box on the topic map; dedicated answer view with sources

---

## Intentionally deferred or out of scope

Do not jump these unless priorities explicitly change:

- Multi-channel support (cross-channel querying, comparison)
- Public deployment, multi-user, auth
- Docker polish
- A separate vector DB (we're using `sqlite-vec`)
- Auto-applying AI suggestions without human review
- Continuous re-ingestion / live monitoring of new episodes (batch is fine for now)
- Mobile / native UI

---

## Open questions to revisit

- Does Phase A's metadata-only discovery produce a topic map that feels right for DOAC? If not, do we need chapter markers more aggressively, or jump to Phase B sooner?
- How visible should confidence be in the UI — a number, a faded style, both?
- After Phase A is in use, is the GUI still the primary surface, or do power users live in the CLI?
- For Phase C, is per-claim provenance (episode + timestamp) enough, or do we need to store the surrounding transcript window?
- For Phase D, what's the right answer length and citation format?

---

## Resume priorities

If resuming after a gap, start here:

1. Read `PROJECT_SUMMARY.md`, `CURRENT_STATE.md`, `PRD_PHASE_A_TOPIC_MAP.md`.
2. Check the latest `WORKLOG.md` entries for what was last worked on.
3. Find the next unchecked item in **Phase A — concrete next steps** above.
4. Verify the relevant workflow before changing it.
5. Make the smallest useful change.
6. Update `CURRENT_STATE.md` if the situation has shifted.
7. Update this roadmap if priorities have shifted.
