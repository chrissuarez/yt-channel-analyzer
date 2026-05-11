# PRD — Phase B: Sample-Based Transcript Refinement

**Status:** Draft, awaiting sign-off (design walked via /grill-me 2026-05-11; every fork operator-confirmed)
**Created:** 2026-05-11
**Owner:** Chris
**Phase:** B of the four-phase plan in `PROJECT_SUMMARY.md` / `ROADMAP.md`
**Depends on:** Phase A (complete), shorts filter (complete — Phase B samples non-Shorts only)

---

## Problem Statement

Phase A produces an auto-discovered topic map from episode **metadata only** — titles, descriptions, chapter markers. That's enough to ship a useful "which episodes are worth my time?" surface for cents, but it has a structural blind spot: **a topic or subtopic that only ever comes up *in conversation* — never in a title, description, or chapter heading — is invisible to the metadata pass.** A guest spends twenty minutes on cold-water immersion in an episode titled "The Longevity Doctor: ..."; the metadata pass files it under "Longevity" and never learns "Cold Exposure" is a thing this channel covers.

The user has lived with the Phase A map and wants the obvious next increment: *"the map is approximately right but it's missing things that only surface when people actually talk."* They do not want to pay to transcribe and process the entire channel yet (that's Phase C) — they want a cheap, targeted pass over a representative sample that surfaces the gaps, which they then curate into the taxonomy.

## Solution

Phase B delivers **sample-based refinement**: process the transcripts of ~15 representative non-Short episodes, and for each, ask an LLM — given the episode's transcript *and the current taxonomy* — to (1) re-judge that episode's topic/subtopic assignments from what was actually said, and (2) propose topics or subtopics the channel covers that aren't in the taxonomy yet, each backed by transcript evidence and the episode that surfaced it. The operator reviews the proposals (accept → it becomes a real taxonomy node), sanity-checks the sampled episodes' transcript-grade reassignments, and then re-runs Phase A discovery — now taxonomy-aware — to spread the enriched taxonomy across the whole channel.

From the user's perspective:

1. Phase A discovery has run (ideally a fresh run, now that the Shorts default is on). They open the new **Refine** stage.
2. The app proposes a ~15-episode sample against that discovery run — ⅔ "coverage" (one episode per topic, highest-confidence member) + ⅓ "blind spot" (lowest-confidence assignments and the unassigned bucket, where the metadata pass was weakest). All non-Shorts; all with captions available (or fetchable).
3. The user tweaks the sample if they want (add/remove episodes), clicks **Fetch transcripts**, sees a cost estimate (~$0.40 for 15), confirms.
4. The app fetches the transcripts, makes one LLM call per transcript (Claude Haiku, batch API), and produces: a list of **proposed new topics/subtopics** (each with the transcript snippet justifying it and the episode it came from), and **transcript-grade reassignments** for the sampled episodes.
5. The user accepts the good proposals — each accepted node becomes a real `topic`/`subtopic`. They glance at the before→after for the sampled episodes and mark-wrong anything that's off. (The sampled episodes now carry transcript-checked assignments, badged as such in the topic map.)
6. The user re-runs Phase A `discover` (one click, ~$0.02). It's now told the curated taxonomy and reuses those names, so the newly-accepted subtopics get populated channel-wide with metadata-grade assignments. Sticky curation protects the names.
7. The map is now more complete. Costs well under a dollar all-in.

What Phase B is **not**: it does not extract claims, build embeddings, cluster, or fetch the whole channel's transcripts. Those are Phase C. Phase B's deliverable is *a more complete taxonomy* plus *transcript-grade assignments for a sample* plus *a reusable transcript-fetch command Phase C will build on*.

## User Stories

1. As a podcast listener, I want the app to propose a small representative sample of episodes to transcribe, so that I don't have to decide which ones are "representative."
2. As a podcast listener, I want the sample to deliberately include the episodes the metadata pass was least sure about, so that refinement looks hardest where the map is weakest.
3. As a podcast listener, I want to edit the proposed sample before anything is fetched or paid for, so that I keep control over what gets processed.
4. As a podcast listener, I want to see an estimated cost before the LLM pass runs, and to confirm it, so that I'm never surprised by spend.
5. As a podcast listener, I want the app to fetch transcripts only for the sample, so that Phase B stays cheap and fast.
6. As a podcast listener, I want each transcribed episode re-judged against the taxonomy from what was actually said, so that the sampled episodes get better-than-metadata assignments.
7. As a podcast listener, I want the app to propose new subtopics (and, rarely, new topics) the channel covers that aren't in the map yet, each with a transcript quote and the episode it came from, so that I can judge the proposal without re-listening.
8. As a podcast listener, I want to accept or reject each proposal individually, so that accepting one creates a real taxonomy node and rejecting one makes it go away.
9. As a podcast listener, I want to see what changed for each sampled episode (topics added/dropped, subtopics corrected), so that I can sanity-check the transcript-grade reassignments and fix any that are wrong.
10. As a podcast listener, I want a clear nudge to re-run discovery after accepting changes, so that the enriched taxonomy actually spreads across the channel.
11. As a podcast listener, I want episodes that have been transcript-checked visibly marked in the topic map, so that I know which assignments are higher-confidence than the metadata-only ones.
12. As a podcast listener, I want re-running discovery after refinement to *not* downgrade or wipe the transcript-grade assignments, so that the better data survives a cheap metadata re-run.
13. As a power user, I want a `fetch-transcripts` CLI command that can fetch a chosen subset (by ID, or all the ones still missing) for a channel, so that I can script transcript ingestion and resume it after interruption.
14. As a power user, I want a `refine` CLI command with a `--stub` mode, so that I can sanity-check the whole pipeline without spending tokens.
15. As a power user, I want refinement runs audited (which episodes, which model, which prompt version, what it cost), so that I can see how the map was refined.
16. As a developer maintaining this, I want a free deterministic stub LLM and an injectable transcript fetcher, so that the pipeline is unit-testable without YouTube or an API key.
17. As a developer maintaining this, I want the refinement schema added cleanly alongside the existing schema (one CHECK-constraint rebuild, otherwise additive), so that existing databases migrate without data loss.
18. As a developer maintaining this, I want the new transcript-fetch path to be its own non-legacy surface, leaving the deprecated `fetch-group-transcripts` command untouched, so that future work isn't pulled toward the retired comparison-group framing.

## Implementation Decisions

### Major modules

#### 1. Refinement module (new — `refinement.py`) — the deep module of Phase B
**Responsibility:** Given a discovery run and a sample of its episodes, produce transcript-grade reassignments for those episodes plus a set of taxonomy proposals. One LLM call per sampled transcript. Idempotent at the run level (a new run produces a new `refinement_runs` row, never overwrites).

**Interface (conceptual):**
- `run_refinement(db_path, discovery_run_id, llm, *, sample=None, transcript_fetcher=None, sample_size=15) -> RefinementRun`
  - `llm` is an injected per-transcript `LLMCallable` (tests pass `stub_refinement_llm` directly — same pattern as `discovery.stub_llm`).
  - `transcript_fetcher` is an injected `Callable[[str], TranscriptRecord]` (param already exists on `youtube.fetch_video_transcript`); tests pass a fake.
  - `sample` is an optional explicit list of video IDs that bypasses the auto-picker.
- Output: a `RefinementRun` carrying the sampled episodes (post replacement round), the proposals, and the reassignments. Side effect: persists the run, episodes, proposals, and `assignment_source='refine'` rows; does not mutate user-curated state (`wrong_assignments` marks win).
- `stub_refinement_llm` — matches the per-transcript `LLMCallable` signature; for each episode echoes its current assignments back as `assignments` and emits one deterministic `new_subtopic_proposal` (parent = the episode's top current topic); for the *first* sampled episode also emits one deterministic `new_topic_proposal`. Use for any free wiring check.
- `make_real_refinement_llm_callable(connection, *, model=None)` — builds the `Extractor`-backed adapter; raises unless `RALPH_ALLOW_REAL_LLM=1` (mirrors `discovery.make_real_llm_callable`).

**The sample picker** (inside `run_refinement` when `sample is None`):
- Candidate pool = episodes assigned in `discovery_run_id` that are not Shorts (`duration_seconds > 180` or NULL) and have `transcript_status='available'` or no `video_transcripts` row yet (fetchable).
- Coverage slots (~⅔ of `sample_size`, rounding down): round-robin over topics ordered by episode count desc, taking each topic's single highest-confidence pool member not yet picked — every topic gets one before any gets two.
- Blind-spot slots (the rest): pool ordered by lowest assignment confidence, then membership in the unassigned bucket; dedup against coverage picks.
- Topics with zero pool members are skipped silently; if the pool is smaller than `sample_size` the run proceeds short with a warning.
- After transcript fetch, episodes whose transcript came back `disabled`/`not_found`/etc. are dropped; the picker is asked once for replacements from the remaining pool, then the run proceeds (no unbounded retry loop).

**The per-transcript LLM call** sees: the episode's transcript text (whole, no chunking, no summarize step — a DOAC episode is ~15–30K tokens, well inside Haiku's window; no ad-read pre-filtering in Phase B), the current taxonomy (topic names with their subtopics), and the episode's current metadata-grade assignments. It returns strict JSON, schema-validated by `Extractor` with one retry:
- `assignments`: `[{topic, subtopic?, confidence, reason}]` — the episode re-judged from the transcript; `reason` cites what was said. May add/drop topics and correct subtopics relative to the metadata-grade set.
- `new_subtopic_proposals`: `[{name, parent_topic, evidence, ...}]` — subtopics not in the current taxonomy; `parent_topic` must name an existing topic; `evidence` is a short transcript-grounded justification.
- `new_topic_proposals`: `[{name, evidence}]` — broad topics not in the taxonomy at all. Expected near-empty (the metadata pass is usually right at the broad level); kept in the schema, a later slice may drop it if it stays empty in practice.

**Persistence rules:**
- For each sampled episode, the refine `assignments` **replace** that episode's non-curated `video_topics`/`video_subtopics` rows wholesale — a topic the transcript pass didn't re-affirm disappears for that episode. Rows protected by a `wrong_assignments` mark are not re-added (existing suppression logic). New rows are `assignment_source='refine'`, `discovery_run_id=NULL`, `refinement_run_id=<this run>`.
- Each proposed node → one `taxonomy_proposals` row, `status='pending'`. Accepting (via the UI/db helper) creates the `topics`/`subtopics` row if absent (idempotent — if it already exists by then, just mark `accepted`) and resolves `parent_topic` through the rename map (`discovery._apply_renames_to_payload` machinery) or rejects the proposal if the parent no longer exists.
- The `refinement_runs` row is created `status='pending'` before the LLM batch, flipped to `running`, then `success`/`error`. A killed or cost-declined run leaves an auditable `pending` row, not a phantom. Each per-transcript call lands an `llm_calls` audit row via `Extractor` (tokens + cost) for post-hoc spend accounting.

**Why deep:** the whole sample→fetch→per-transcript-LLM→validate→persist pipeline lives behind one function. The caller (CLI, review UI) sees a `RefinementRun`. Future improvements (ad-read pre-filtering, summarize-first for very long transcripts, a smarter picker) don't change the signature.

#### 2. Transcript fetch (new CLI surface, modify `cli.py`; reuse `youtube.fetch_video_transcript` + `video_transcripts` table)
**Responsibility:** A non-legacy general transcript fetcher. `fetch-transcripts --db-path X [--video-ids a,b,c | --missing-only | --limit N | --refinement-run-id R] [--stub]` — selector mutex, error if no selector (don't accidentally fetch hundreds). `--missing-only` = all primary-channel videos with no `video_transcripts` row or a retryable status (`rate_limited`/`request_failed`/`error`); `--refinement-run-id` = exactly the episodes a refinement run needs (`run_refinement` calls this logic internally too). Sequential fetch with a small fixed inter-request sleep; exponential capped backoff on `rate_limited`; each result persisted immediately via `upsert_video_transcript` so a killed run resumes cleanly with `--missing-only`. One line per video, closing tally (`available: N, disabled: N, not_found: N, …`). `--stub` uses a built-in fake fetcher (returns `available` with placeholder text for any ID). No `--force` re-fetch of `available` rows and no parallelism in this version. The legacy `fetch-group-transcripts` command is left exactly as-is. No schema change — `video_transcripts` already has the right shape and status vocab.

#### 3. Refinement schema (modify `db.py`)
**New tables:**
- `refinement_runs` — `(id, channel_id, discovery_run_id, model, prompt_version, status ['pending'|'running'|'success'|'error'], n_sample, created_at)`. Mirrors `discovery_runs`.
- `refinement_episodes` — `(refinement_run_id, video_id, transcript_status_at_run)` — which episodes were actually in the sample after the replacement round.
- `taxonomy_proposals` — `(id, refinement_run_id, kind ['topic'|'subtopic'], name, parent_topic_name, evidence, source_video_id, status ['pending'|'accepted'|'rejected'], resolved_at)`.

**Junction-table change:** `video_topics` and `video_subtopics` gain `assignment_source='refine'` (CHECK-constraint rebuild via a `_repair_*` function — the rename→recreate→INSERT SELECT→drop dance with `foreign_keys=OFF`+`legacy_alter_table=ON`, identical to the slice-A/C migrations and `_repair_video_topic_assignment_source_constraint`) and a nullable `refinement_run_id INTEGER` FK (additive `ALTER TABLE ADD COLUMN` via `ensure_schema`, like `discovery_run_id`). Added to both the CREATE statements and `REQUIRED_TABLE_COLUMNS`.

**db.py helpers:** create/advance-status a refinement run; insert proposals; accept/reject a proposal (accept creates the node, idempotent, parent-resolved); write a sampled episode's refine assignment set (replace-wholesale, curated-wins). The topic-map query in `review_ui.py` widens to include `assignment_source='refine'` rows for the run's topics (they have `discovery_run_id NULL`).

#### 4. Discovery prompt taxonomy awareness (modify `discovery.py`)
**Responsibility:** Feed the current curated topic + subtopic names into the discovery prompt ("here is the taxonomy so far — reuse these names exactly where they fit; you may add new ones"), so a post-Phase-B `discover` run reuses the accepted names and spreads them channel-wide. Bump `DISCOVERY_PROMPT_VERSION`. Also make `run_discovery`'s `ON CONFLICT` on the junction tables **never downgrade** a row whose `assignment_source` is `refine` or `manual` — keep its source and its transcript-grade `confidence`/`reason` (a metadata re-run can still *add* new auto rows for new topics, and `wrong_assignments` suppression still applies). This change is in Phase B's scope because it's the mechanism by which accepted proposals reach the rest of the channel; it also makes sticky curation land more cleanly in general.

#### 5–6. Refine UI (modify `review_ui.py`) — new stand-alone stepper stage, two screens
**Screen A — sample setup:** the auto-picked set against the latest discovery run (per row: episode title, the topic it covers, current confidence, transcript status), add/remove controls, free-text "add by video ID/URL" box. A **Fetch transcripts & estimate** action runs the `fetch-transcripts` logic for the picked IDs, drops dead ones with a visible note and offers replacements, then shows the cost estimate (sum of per-transcript `tokens_in` estimates × Haiku input price from `extractor/pricing.py` + a flat output allowance). A **Run refinement ($X.XX)** button POSTs to `/api/refine`, which runs `run_refinement` on a daemon thread (same async pattern as `/api/discover`); `/api/refine/status/<id>` is polled.
**Screen B — proposal review (after the run completes):** `taxonomy_proposals` grouped — new subtopics under each parent topic, then new topics — each card showing `name`, `parent_topic`, the `evidence` snippet, and the source episode, with **Accept / Reject** (accept creates the node and marks `accepted`; reject marks `rejected`; both idempotent). All `pending` proposals across runs are shown, grouped by run, newest first; accepting one whose node already exists is a no-op mark-accepted. Below: a compact **before→after** panel per sampled episode (topics added/dropped, subtopics corrected) so the operator can sanity-check and mark-wrong any bad reassignment (reusing the existing mark-wrong endpoint). A closing nudge — "Run discovery again to spread accepted changes across the channel" — linking the Discover stage's run button. Episodes whose assignment row is `assignment_source='refine'` get a small **"transcript-checked"** pill next to the confidence indicator in the topic map.
The two screens ship as two slices so each `review_ui.py` change stays under the 300-line Ralph HITL pause. Reuses existing patterns throughout (async-run+poll, suggest/review/accept cards, mark-wrong, `formatDuration`/`formatDate`).

### Architectural decisions

- **One LLM call per sampled transcript** (not one pooled call over all transcripts). 15 transcripts pooled would exceed Haiku's context; pooling would force a summarize-first step. Per-transcript fits `Extractor.run_batch` (which already flips to the batch API at ≥10 calls), isolates a bad transcript, and matches Phase B's per-episode question. No cross-episode synthesis here — that's Phase C.
- **Whole transcript, no chunking, no summarize step, no ad-read pre-filter in Phase B.** A DOAC episode fits Haiku's window whole; pre-filtering would save ~10% of ~$0.40 — not worth building now. Phase C, at full-channel scale, will revisit.
- **The sample is operator-editable before any spend.** Auto-pick is a proposal; the user adjusts it; only then does transcript-fetch + the LLM batch fire. `refine --video-ids` bypasses the picker entirely.
- **Refine assignments replace a sampled episode's prior assignments wholesale** (deletions included), except user-curated state. **A later `discover` run never downgrades a `refine`/`manual` row.** Transcript-verified data is strictly better than metadata-grade; a metadata re-run shouldn't clobber it. The small cost — `discover` can't auto-remove a now-stale refine assignment — is covered by mark-wrong, same as today.
- **New tables, not the pre-pivot `topic_suggestion_*` family.** Those are shaped around per-video tag suggestions with an approval-then-apply two-step, keyed off `topic_suggestion_runs`, and `topic_suggestions.py` uses OpenAI directly (predating the `extractor/` consolidation, ADR 0001). Bending Phase B into that schema means vestigial columns and a mismatched mental model. The old machinery is left untouched, not deleted, not rebuilt.
- **`extractor/` stays the only LLM-call module.** `refinement.py` registers a `refinement.transcript` prompt in the registry and goes through `Extractor.run_batch` — no second LLM-calling module (ADR 0001).
- **SQLite remains the source of truth.** No vectors in Phase B (Phase C adds them via `sqlite-vec`, same `.sqlite` file).
- **The new transcript-fetch path is non-legacy; the old one is left alone.** `fetch-transcripts` is the forward surface; `fetch-group-transcripts` keeps working for anyone with comparison groups but is not extended.
- **Cost is gated twice for `refine --real`:** `make_real_refinement_llm_callable` enforces `RALPH_ALLOW_REAL_LLM=1` (fails fast before any API call), and the CLI/GUI shows a pre-flight cost estimate and requires `--yes` (CLI) or a click-through (GUI) before the batch fires.

### LLM and prompt decisions

- **Model:** Claude Haiku 4.5 (or GPT-4o-mini). Cheap, fast, strong enough for per-transcript structured extraction. ~$0.02–0.03 per episode, ~$0.40 for a 15-episode sample.
- **Batch API:** used automatically — a 15-sample run is ≥10 calls so `Extractor.run_batch` routes through the batch API; a small sample (<10) uses realtime. Fine either way; no realtime requirement for refinement.
- **Prompt output shape:** strict JSON (`assignments` / `new_subtopic_proposals` / `new_topic_proposals`), `additionalProperties: false`, schema-validated before persistence, one retry on malformed (owned by `Extractor`). Haiku's habit of fencing JSON is already handled by `runner._strip_code_fence`.
- **Prompt versioning:** `REFINEMENT_PROMPT_VERSION` written into `refinement_runs.prompt_version`. The `discover` prompt change bumps `DISCOVERY_PROMPT_VERSION`.

### Operator experience

- Recommended first step: a fresh `discover --real` now that the Shorts default is on, so refinement samples from a clean non-Short run.
- `refine --db-path X --project-name Y [--discovery-run-id R] [--video-ids …] [--sample-size N] --stub|--real [--yes]` — `--stub` is free and deterministic for wiring checks; `--real` requires `RALPH_ALLOW_REAL_LLM=1` and shows a cost estimate that `--yes` (or interactive `[y/N]`) must confirm.
- `fetch-transcripts --db-path X [--video-ids … | --missing-only | --limit N | --refinement-run-id R] [--stub]` — resumable via `--missing-only`.
- GUI: the **Refine** stage walks sample setup → cost confirm → (async run) → proposal review; ends nudging a re-`discover`.
- `analyze` (the one-shot setup+ingest+discover) is **not** extended — refinement needs human sample review and can't be one-shot.

### Testing

- New `test_transcripts_fetch.py` (slice 1) and `test_refinement.py` (slice 3) go into `.ralph/verify.sh`'s default targets. The gate-excluded `test_transcripts.py` stays excluded and untouched (its 2 pre-existing legacy failures are not in Phase B's scope; do not widen the gate to it).
- Refinement tests inject `stub_refinement_llm` + a fake `transcript_fetcher`; no real LLM/network in the gate. Picker math, the replacement round, replace-wholesale persistence, curated-wins, and the error path (run left `pending`/`error`, no partial taxonomy) are all unit-covered.
- Real transcript fetch and `refine --real` are operator-only (HITL pause), exercised via `.scratch/phase-b-refinement/SMOKE.md` (real fetch on a handful of DOAC episodes + real `refine` ~$0.40 + UI eyeball + a follow-up `discover --real` to confirm spread).

### Documentation artifacts

- This file (`PRD_PHASE_B.md`), in the `PRD_PHASE_A_TOPIC_MAP.md` style.
- A new **Phase B** section in `ROADMAP.md` with the six slices as checkbox groups (these drive Ralph iteration units).
- `.scratch/phase-b-refinement/` — issue files (`NN-<slug>.md`), a PRD copy, and the operator `SMOKE.md`.
- `YT_ANALYZER_CHEATSHEET.md` — entries for `fetch-transcripts` and `refine` (current, not `[legacy]`).
- `docs/operator-workflow.md` — a Phase B section appended to the end-to-end recipe.
- `CONTEXT.md` — new glossary terms: `Transcript` (the fetched-and-stored text of an Episode), `RefinementRun` (one sample-based refinement pass), `TaxonomyProposal` (a pending/accepted/rejected proposed Topic or Subtopic). The existing `_Avoid_` discipline applies.
- `WORKLOG.md` — terse entry per slice.

## The six slices (→ `ROADMAP.md` §B, → `/to-issues`)

Each a vertical tracer-bullet slice, each its own `feat/issue-NN-<slug>` branch off `main`. Dependency order: 1 → 2 → 3; 4 anytime after `main`; 5 → 6 after 3. Docs/cheatsheet/WORKLOG updates folded into slices 1, 3, 6 (and the ROADMAP/PRD/CONTEXT/operator-workflow groundwork lands with this PRD before slicing).

1. **`fetch-transcripts` CLI + non-legacy fetch path** — selector-mutex command, resumable via `--missing-only`, rate-limit backoff, `--stub` fake fetcher, injectable fetcher, status tally; reuses `video_transcripts`; new `test_transcripts_fetch.py` → gate. No LLM, no refinement. (Vertical: CLI → `youtube` fetch → DB → stdout.)
2. **Refinement schema + db helpers** — `refinement_runs` / `refinement_episodes` / `taxonomy_proposals` tables; `assignment_source='refine'` + `refinement_run_id` on the junction tables (CHECK-rebuild repair + additive column); db.py helpers (create/advance run, insert/accept/reject proposal, write refine assignments); topic-map query widened to include refine rows. Additive + the one rebuild. No CLI/LLM.
3. **`refinement.py` core + `refine --stub` CLI** — the ⅔/⅓ picker + replacement round; internal transcript fetch for the sample; `refinement.transcript` prompt registered in `extractor/`; per-transcript `Extractor.run_batch`; persist run/episodes/proposals/refine-assignments (replace-wholesale, curated-wins); `stub_refinement_llm` (+ deterministic topic proposal for the first episode); `make_real_refinement_llm_callable` (`RALPH_ALLOW_REAL_LLM=1`); CLI `refine … --stub|--real [--yes]` with cost-estimate + confirm; `test_refinement.py` → gate. (Likely two Ralph iterations: picker+fetch, then LLM+persist+CLI.)
4. **Discovery prompt taxonomy awareness** — feed curated topic/subtopic names into the discovery prompt; bump `DISCOVERY_PROMPT_VERSION`; never-downgrade `ON CONFLICT` for `refine`/`manual` rows. Small, independent.
5. **Refine UI — sample-setup screen** — new stepper stage; `/api/refine/sample` (GET auto-pick) + `/api/refine` (POST → daemon `run_refinement`) + `/api/refine/status/<id>`; the setup/edit/fetch-estimate/confirm screen. Stays under 300 changed lines in `review_ui.py`.
6. **Refine UI — proposal-review screen** — render proposals, Accept/Reject endpoints, before→after sanity panel, re-run-discovery nudge, "transcript-checked" pill on refine-source episode cards in the topic map.

## Out of scope (Phase C and later)

- Claim extraction (the `claims` table, `claim_extraction.py`), embeddings (`sqlite-vec`), claim clustering, consensus/conflict/best-advice synthesis — Phase C.
- Full-channel transcript fetch — Phase C uses the `fetch-transcripts` command built here with `--missing-only` over the whole channel; Phase B only fetches its sample.
- Natural-language Q&A over the channel — Phase D.
- Ad-read / sponsor-segment pre-filtering of transcripts; summarize-first for unusually long transcripts; a smarter sample picker — possible later refinements behind the unchanged `run_refinement` interface.
- Retiring or redirecting the legacy `fetch-group-transcripts` / `processing.py` surface.
