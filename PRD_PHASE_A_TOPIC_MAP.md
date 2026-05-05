# PRD — Phase A: Topic Map MVP

**Status:** Draft, awaiting module sketch sign-off
**Created:** 2026-05-04
**Owner:** Chris
**Phase:** A (MVP) of the four-phase plan in `ROADMAP.md`

---

## Problem Statement

The user listens to long-running podcast channels (canonical example: *Diary of a CEO*, ~450 episodes) for advice and insight. Two problems result:

1. **Discovery is hard.** With hundreds of episodes, it is not obvious which ones are worth watching for any given interest area. Browsing by title is unreliable; episode metadata is dense and poorly organised across a backlog this big.
2. **Memory is hard.** Even after watching, the user cannot retain the great advice. There is no surface that says "this channel says X about gut health, across these guests."

Existing YouTube tooling solves neither problem. The earlier version of this project tried to solve both with a manually-curated taxonomy, which the user does not want to operate by hand at this scale.

The user wants the app to do the structuring. They want to curate the result, not author it from scratch.

## Solution

Phase A delivers the smallest version of the app that solves problem (1): a **browseable, auto-discovered, reviewable topic map of a single podcast channel**, built from episode metadata alone (no transcripts).

From the user's perspective:

1. They run a single command pointing at a channel (e.g. Diary of a CEO).
2. The app ingests every episode's title, description, and chapter markers.
3. The app makes one batched LLM call (~$0.10) that proposes broad topics with subtopics, and assigns every episode to one or more topics with a confidence score and a short reason.
4. The user opens a GUI showing the topic map.
5. They click "Health" → see the 5 health subtopics → click "Gut Health" → see the 12 episodes assigned, each with title, thumbnail, guest, the reason it landed there, and a confidence indicator.
6. They can rename topics, merge near-duplicates, split overcrowded topics, move episodes between subtopics, and mark assignments as wrong.
7. That's it. Useful immediately. Costs cents to run.

Problem (2) — consensus, conflict, and standout-advice extraction — is **explicitly deferred to Phase C**, after Phase A has been lived with and validated.

## User Stories

1. As a podcast listener, I want to point the app at a YouTube channel and have it ingest every episode's metadata, so that I don't have to manage a video list myself.
2. As a podcast listener, I want the app to propose a topic map of the channel automatically, so that I don't have to invent the categorisation by hand.
3. As a podcast listener, I want to see broad topics with episode counts, so that I can tell at a glance what the channel is mostly about.
4. As a podcast listener, I want to click into a broad topic and see its subtopics with episode counts, so that I can drill from "Health" down to "Gut Health" or "Sleep."
5. As a podcast listener, I want to see the list of episodes assigned to a subtopic, so that I can pick one to watch.
6. As a podcast listener, I want each episode card to show title, thumbnail, guest name, and a one-line reason it was assigned to this topic, so that I can judge relevance without opening the video.
7. As a podcast listener, I want a confidence indicator on each episode-to-topic assignment, so that I know which assignments to trust and which to review.
8. As a podcast listener, I want an episode that genuinely covers multiple topics to appear under all of them, so that I find it whether I'm browsing "Health" or "Mental Performance."
9. As a podcast listener, I want to rename a topic the LLM proposed, so that I can use language that fits how I think about it.
10. As a podcast listener, I want to merge two near-duplicate topics the LLM proposed (e.g. "Wealth" and "Money"), so that the map stays clean.
11. As a podcast listener, I want to split an overcrowded topic into smaller ones, so that browsing stays useful when one bucket has 80 episodes.
12. As a podcast listener, I want to move an episode from one subtopic to another, so that I can correct a clearly wrong assignment.
13. As a podcast listener, I want to mark an episode-to-topic assignment as wrong, so that the system has signal to learn from later.
14. As a podcast listener, I want to sort the episodes within a subtopic by recency, view count, or confidence, so that I can find the most relevant or freshest content.
15. As a podcast listener, I want low-confidence assignments visually distinguished (e.g. faded), so that I can quickly spot which assignments to review first.
16. As a podcast listener, I want the app to remember my curation edits, so that running discovery again doesn't undo my renames, merges, or moves.
17. As a podcast listener, I want to re-run discovery later (e.g. after the channel adds new episodes) without losing my curation work, so that the map stays fresh without restarting from scratch.
18. As a power user, I want a CLI command that runs ingestion + discovery in one go, so that I can script setup for a new channel.
19. As a power user, I want to see which discovery run produced a given topic, so that I can audit how the map was built.
20. As a power user, I want manual taxonomy commands (create topic, assign episode) to remain available for rare manual cases, so that I'm not forced to use the GUI for everything.
21. As a developer maintaining this, I want comparison-group code moved out of the primary surface to `legacy/`, so that future work isn't pulled toward a deprecated direction.
22. As a developer maintaining this, I want the schema to support both old (primary/secondary topic columns) and new (junction table) representations during the transition, so that I don't have to migrate everything in one shot.

## Implementation Decisions

### Major modules

The Phase A build introduces or modifies the following modules. Each has a deliberately simple interface so it can be tested in isolation.

#### 1. Discovery module (new) — the deep module of Phase A
**Responsibility:** Given a channel and its ingested episode metadata, produce a proposed topic taxonomy and per-episode multi-topic assignments. One batched LLM call. Idempotent at the run level (a new run produces a new `discovery_runs` row, never overwrites).

**Interface (conceptual):**
- Input: channel ID; access to the database
- Output: a `DiscoveryRun` containing proposed topics, subtopics, and per-episode assignments with confidence and reason
- Side effect: persists the run; does not mutate user-curated topics

**Why deep:** the entire LLM-driven discovery problem (prompt construction, batching, response validation, retry, persistence) lives behind a single function call. The caller does not see any of it. The interface is stable: future improvements (better prompts, different models, sample-based refinement) do not change the signature.

#### 2. Schema migration (modify `db.py`)
**Responsibility:** Extend the schema to support multi-topic episode membership with confidence and provenance, while leaving the legacy primary/secondary topic columns intact for the transition period.

**New tables:**
- `discovery_runs` — one row per discovery pass, with channel ID, timestamp, model, and prompt version
- `video_topics` — junction: video, topic, confidence (0.0–1.0), source ("auto" / "manual"), reason text, discovery_run_id (nullable for manual)
- `video_subtopics` — junction: video, subtopic, confidence, source, reason, discovery_run_id

**Migration:** backfills the existing primary/secondary topic data into `video_topics` as `source="manual"` with `confidence=1.0` so nothing is lost.

#### 3. Topic map UI (modify `review_ui.py`)
**Responsibility:** Render the auto-discovered topic map and provide the curation actions (rename, merge, split, move, mark wrong, sort). Reuses existing UI patterns (suggest/review/approve flows) but at the topic-map altitude rather than per-tag-suggestion altitude.

The April 2026 Topic Map view, selected-topic detail panel, and approve+apply patterns are direct prior art and largely carry over.

#### 4. CLI integration (modify `cli.py`)
**Responsibility:** Add an `analyze <channel>` command that chains `setup` → `ingest` → `discover` for a one-shot operator experience. Existing manual taxonomy commands stay as a power-user surface.

#### 5. Legacy folder (new directory)
**Responsibility:** Hold deprecated comparison-group machinery without deleting it. Files moved: `comparison_group_suggestions.py`, `group_analysis.py`, group-related code from `markdown_export.py`, full-transcript pipeline parts of `processing.py`. Imports updated; CLI commands for these still work but emit a deprecation notice.

### Architectural decisions

- **SQLite remains the source of truth.** Vectors are not introduced in Phase A. When Phase C adds them, they live in the same `.sqlite` file via the `sqlite-vec` extension.
- **One LLM call per discovery run.** Batching keeps the cost at ~$0.10 for a full channel and avoids partial-state failures across multiple in-flight requests.
- **Curation edits are sticky across re-runs.** A new discovery run produces a candidate diff against the curated state; it does not silently overwrite renames, merges, or moves the user has applied.
- **Confidence and provenance are first-class.** Every auto-assignment carries a confidence score and a short reason string. The UI surfaces both. Without provenance, curation feels like blind acceptance and the user cannot audit the LLM.
- **Multi-topic membership is the default.** Episodes appear under every topic they belong to. Single-topic membership was considered and explicitly rejected (a Huberman episode about sleep is genuinely also about neuroscience and recovery; forcing one bucket loses information).
- **Topic discovery uses metadata only in Phase A.** Titles, descriptions, chapter markers. No transcripts. This is what makes Phase A cost cents and ship fast.
- **Legacy code is moved, not deleted.** Phase C may want pieces back.

### LLM and prompt decisions

- **Model:** Claude Haiku 4.5 (or GPT-4o-mini) for discovery. Cheap, fast, strong enough for structured taxonomy work.
- **Batch API:** used by default. Realtime is not needed for backlog discovery.
- **Prompt output shape:** strictly structured JSON: list of broad topics with subtopics, plus list of per-episode assignments. Validated with a schema parse before persistence; one retry on malformed response.
- **Prompt versioning:** the prompt template carries a version string written into `discovery_runs` so old runs are traceable to the prompt that produced them.

### Operator experience

- `analyze <channel>` is the one-shot command: setup + ingest + discover.
- The GUI opens to the topic map of the most recent discovery run for the active channel.
- Re-running discovery is non-destructive to curation work; the user is shown a diff and approves additions/changes.

## Testing Decisions

### What makes a good test here
- Test external behaviour, not implementation details. The discovery prompt may evolve; the schema and module interfaces should not be coupled to a specific prompt revision.
- Mock the LLM call at the boundary. Discovery tests should drive the module with canned LLM responses, not real API calls.
- Cover the edge cases that matter for trust: malformed LLM responses, an episode assigned to zero topics, duplicate topic names with different casing, a discovery re-run that conflicts with curation edits.

### Modules with tests in Phase A

#### Discovery module — primary test surface
Tests should cover:
- Given canned metadata for a small fake channel and a canned LLM response, the module produces the expected `discovery_runs` row, topic rows, subtopic rows, and assignment rows.
- A malformed LLM response is rejected and retried once; on second failure the run is marked errored, no partial state persisted.
- An episode assigned multiple topics in the LLM response produces multiple `video_topics` rows.
- Confidence and reason fields round-trip correctly.
- Re-running discovery on a channel with existing curation edits does not silently overwrite them.

#### Schema migration
Tests should cover:
- Backfill from primary/secondary topic columns produces correct `video_topics` rows with `source="manual"` and `confidence=1.0`.
- Migration is idempotent (running it twice doesn't double-insert).

#### Topic map UI — light integration tests
Tests should cover:
- `/api/state` returns the topic map for the latest discovery run.
- Curation actions (rename, merge, move) update the database and survive a state refresh.
- Multi-topic episodes appear under all relevant topics.
- Low-confidence assignments are flagged in the response payload.

### Prior art to follow
- The existing review UI test patterns and the copied-DB smoke tests used in late-April 2026 worklog entries (e.g. "Copied-DB smoke test approved and applied a pending Psychology subtopic suggestion") are the right model: small SQLite fixtures, real DB, mocked external calls.
- `test_transcripts.py` shows the test infrastructure conventions in this project; new Phase A tests should sit alongside in the same style.

## Out of Scope

Explicitly **not** in Phase A. These are deferred to later phases (see `ROADMAP.md`).

- Transcript fetching, transcript processing, claim extraction.
- Consensus / conflict / standout-advice surfacing per topic.
- Embeddings, vector storage, `sqlite-vec` integration.
- Natural-language Q&A.
- Multi-channel support (cross-channel browse, comparison, query).
- Auto-applying AI suggestions without human review.
- A separate vector DB.
- Continuous re-ingestion / live monitoring of new episodes.
- Public deployment, multi-user, auth.
- Mobile / native UI.
- Removing legacy comparison-group code from disk (it moves to `legacy/`, stays in the repo).
- Schema cleanup of legacy primary/secondary topic columns (kept for transition).

## Further Notes

### Sequencing
A1 (schema) → A2 (discovery module) → A3 (UI) → A4 (legacy move) → A5 (docs + first real run). A4 can run in parallel with A3 if convenient. A5 happens last and includes the first real run on Diary of a CEO as the validation event.

### Definition of done for Phase A
The user can run `analyze diary-of-a-ceo`, get a topic map within minutes, browse topics → subtopics → episodes in the GUI, perform at least one curation action, and re-run discovery without losing the curation. The map is "good enough to be useful" by the user's own judgement — formal accuracy metrics are out of scope until Phase B/C creates the need for them.

### Why this PRD before code
The vision pivoted significantly on 2026-05-04. Writing this PRD forces shared understanding before any code is written under the new direction, and gives future sessions a single durable artifact to read instead of re-deriving the architecture from scratch.

### Publishing
The `to-prd` skill recommends publishing the PRD to a project issue tracker with a `needs-triage` label. No issue tracker is currently configured for this project. Run `/setup-matt-pocock-skills` if/when you want one wired up; until then, this file is the canonical PRD.
