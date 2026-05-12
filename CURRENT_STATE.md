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

**Phase A is complete** as of 2026-05-08. All 11 slices (02 real-LLM machinery, 03 subtopics, 04 confidence/reason, §A4 legacy move, §A5 docs + paid DOAC run, 05 multi-topic, 07 wrong_assignments, 08 sticky curation, 09 low-confidence threshold, 11 channel overview, 12 run-id demote, 13 comparison readiness) have merged. Sticky-curation 3/3 paths (rename replay, wrong-topic suppression, wrong-subtopic suppression) validated end-to-end on real DOAC data 2026-05-09. CLI `--real` flag (issue 02b) merged 2026-05-08; `make_real_llm_callable()` is gated by `RALPH_ALLOW_REAL_LLM=1` and writes audit rows with tokens + cost (`extractor/pricing.py`).

**Phase B ("sample-based transcript refinement") is complete** as of 2026-05-12. All 6 slices on `main` (B1 `fetch-transcripts` CLI, B2 refinement schema + db helpers, B3 `refinement.py` core + `refine --stub/--real` CLI, B4 taxonomy-aware discovery prompt + never-downgrade `refine`/`manual` rows, B5 Refine UI sample-setup screen, B6 Refine UI proposal-review + before→after sanity panel). New tables `refinement_runs` / `refinement_episodes` / `taxonomy_proposals`; `assignment_source='refine'` + nullable `refinement_run_id` on the junction tables; new "Refine" stepper stage with `/api/refine*` endpoints (sample, fetch-transcripts+estimate, async run+poll, proposal accept/reject). Verify gate 369 green. **Not yet exercised against real DOAC data** — the paid operator runbook is `.scratch/phase-b-refinement/SMOKE.md`.

A Claude-Design-driven reskin and structural rebuild of `review_ui.py` landed across 2026-05-09/10 (16 commits): paper/ink/teal palette, topbar + 4-stage stepper, Supply / Discover / Consume stage pages, Review canvas with overview minimap + focused topic canvas, real Run-discovery button (`POST /api/discover`), Re-ingest, Edit channel form, Discover-row → Review, Supply pagination, Discover cost column, and stream/poll for in-flight discovery runs (async daemon thread + `GET /api/discovery_runs/<id>` polled every 1.5s).

The strategy is **retrofit in place**, not greenfield — most of the existing ~600KB of code (ingestion, schema, review UI, topic suggestion machinery) carries over with repurposed semantics.

---

## What exists today

### Phase A pipeline (live)
- `discover` / `analyze` CLI with required `--stub|--real` mutex; real mode additionally requires `RALPH_ALLOW_REAL_LLM=1`. Default model Haiku 4.5 (~$0.019 / 15 episodes; ~$0.05 / DOAC-sized run).
- `discovery.py` — Phase A runner: takes injected `LLMCallable`, produces a `DiscoveryRun` with topics + subtopics + per-episode multi-topic Assignments (confidence + reason). Sticky-curation fixed-point chain (`_apply_renames_to_payload` + `_suppress_wrong_assignments_in_run`) replays user renames + suppresses wrong-marked assignments before persisting.
- `extractor/` — only LLM-call module. Provider lifecycle, structured-output validation, retry-once (skipped on `max_tokens` truncation), audit logging to `llm_calls` with tokens + cost. `AnthropicRunner` streams single calls (`messages.stream(...).get_final_message()`) so `max_tokens` can dial to model max (64K for Haiku).
- `review_ui.py` curation GUI — reskinned + restructured. Topbar + 4-stage stepper-as-router; Supply / Discover / Consume / Review stage pages; Review canvas with topic-overview minimap + focused topic drill-down + subtopic tabs; episode rows with confidence/reason/`also_in` pills, Watch / Wrong-topic / Wrong-subtopic actions. Run discovery / Re-ingest / Edit channel buttons all wired. Stream/poll for in-flight runs.
- SQLite remains source of truth; `discovery_runs`, `topic_renames`, `wrong_assignments`, `llm_calls` tables added across Phase A. Errored runs persist `error_message` + `raw_response` so paid failures are recoverable.
- `legacy/` holds dormant Phase C code (`comparison_group_suggestions.py`, `group_analysis.py`, `markdown_export.py`, `processing.py`); CLI commands that touch it print `[legacy]` warnings.

### Reused as-is
- Channel and video ingestion (`youtube.py`, `db.py`)
- Pre-pivot taxonomy machinery (`topic_suggestions.py`, `subtopic_suggestions.py`)

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

Phase A and Phase B are both shipped **and both validated on real DOAC data** (Phase B smoke 2026-05-12, gate 373 green). The **shorts filter** feature is complete (3/3 slices merged 2026-05-11: `videos.duration_seconds` + per-channel/per-run `exclude_shorts` filter, default on, ≤180s cutoff; `discovery_runs` audit counts; review-UI shorts badge + per-episode length).

**Next moves (agreed 2026-05-12 — A & C done; next is Phase C scoping):**
1. ~~**A — shorts-visibility UI tweaks**~~ ✅ **done 2026-05-12** (`review_ui.py`). Run-row shorts badge always renders (4 states); per-episode coral "Short · m:ss" pill on episode cards. See WORKLOG.
2. ~~**C — real DOAC operator pass through Phase B**~~ ✅ **done 2026-05-12** on fresh `tmp/doac-smoke.sqlite` per `.scratch/phase-b-refinement/SMOKE.md`. End-to-end works (discover --real → 15 real transcripts → refine --real success: 76 proposals, 95 refine assignments, cost est ≈ actual → accept/reject incl. parent-missing auto-reject → before→after transcript-grade → re-discover doesn't downgrade refine rows). Surfaced & fixed **2 robustness bugs**: (a) `extractor/schema.py` treats `"k": null` for an optional prop as absent — Haiku emits `subtopic: null` and it was sinking the whole paid batch; (b) `db.write_refine_assignments` skips+counts (`skipped_unknown_topic`) an assignment naming a non-existent topic instead of `ValueError`-ing the whole run — Haiku occasionally hallucinates/paraphrases a topic name. Quality follow-up below (#3 in the polish list).
3. **Scope Phase C** ← **next** — claim extraction / embeddings / clustering / synthesis. No PRD or slice breakdown yet. ~$8 one-time backlog spend.

Smaller open threads still on the polish list:

0. **`_resolve_discovery_run_id` picks latest run by id regardless of status** — the Refine auto-sample grabbed errored run #10 (no assignments → all-blind-spot sample). Should probably prefer the latest *successful* run.

1. **Haiku subtopic-divergence auto-recover** ($-affecting). Live smoke surfaced Haiku occasionally producing assignments that reference subtopics it didn't declare in `payload.subtopics`. Strict validator in `discovery.py` raises `ValueError`; user pays ~$0.05 per occurrence. Fix: auto-recover by appending the missing subtopic to `payload.subtopics` before strict validation.
2. **Server-side Supply sort.** `supplySort='oldest'` is currently a client-side `.reverse()` of the loaded N — shows oldest *of loaded N*, not channel's true oldest. Push the ORDER BY toggle into `_build_supply_videos`.
3. **Refinement prompt over-proposes / near-misses the taxonomy** (quality, surfaced by the 2026-05-12 DOAC smoke). 15 episodes → 25 new-`topic` proposals, and the model paraphrases existing topic names (`Geopolitics & Global Power` vs the real `Geopolitics & Global Conflict`). `db.write_refine_assignments` now safely *drops* an unknown-topic assignment (no crash) — but the better fix is to (a) tighten `_REFINEMENT_SYSTEM` to discourage near-duplicate topic proposals / surface the collision, and/or (b) have `refinement.py` re-route a dropped unknown-topic assignment into `new_topic_proposals` so the operator sees it instead of it vanishing.

Beyond these, longer-tail items worth flagging when they bite:
- Fuzzy-match fallback for sticky-curation chain (Haiku word-choice variance silently bypasses rename/wrong-mark when the LLM rephrases — e.g., "Personal Development & Success" → "Personal Development & Discipline" gets a new topic_id, escapes the exact-string-match curation chain).
- `.wslconfig` mirrored networking switch (obsoletes 4 netsh portproxy rules; needs `wsl --shutdown` so do at a session boundary).

---

## Known project tensions

### 1. Existing code carries old conceptual baggage
Tables, columns, and modules related to comparison groups still exist. They are being moved to `legacy/`, not deleted, in case Phase C reveals we want pieces back. Carry the baggage; don't pay for a rewrite of working plumbing.

### 2. Phase C is tempting; live with A+B first
The exciting parts (consensus / conflict / advice extraction, Q&A) live in Phase C/D. Phase A and Phase B are now both built. The discipline is: do the real DOAC operator pass through Phase B, live with the refined topic map for a while, *then* commit the ~$8 backlog spend and the bigger build of Phase C. A+B may already be enough; we won't know until we use them on real data.

### 3. Multi-topic episodes affect the schema
Episodes can belong to many topics with confidence scores. Existing schema has primary + optional secondary topic — that needs extending (junction table) before MVP can ship cleanly. This is the first real schema change.

### 4. Confidence and provenance need to be visible
Auto-discovered assignments must show *why* an episode landed where it did (matched chapter title, matched description keyword, etc.) and *how confident* the system is. Without this, the curation UX feels like blind acceptance.

---

## Best next-step questions when resuming

1. Have any new $-affecting LLM bugs surfaced in the latest WORKLOG entry that aren't yet in the open-threads list?
2. Has the real DOAC operator pass through Phase B happened yet (per `.scratch/phase-b-refinement/SMOKE.md`)? Has Phase C been scoped (PRD + slices) yet?
3. Is the dev server still bound to a stale `tmp/*.sqlite` from a previous session, or has the user switched DBs?

---

## Suggested resume checklist

When restarting work:

1. Read:
   - `CURRENT_STATE.md` (this file)
   - `PROJECT_SUMMARY.md`
   - `ROADMAP.md`
   - `PRD_PHASE_A_TOPIC_MAP.md`
   - `WORKLOG.md` (most recent entries)
2. Identify the current focus: Phase A + B are both shipped; the open item is the real DOAC operator pass through Phase B, then scoping Phase C.
3. Verify the relevant workflow locally before changing it.
4. Make the smallest useful change.
5. Test the affected workflow.
6. Update these docs if reality changed.

---

## Change log notes

### 2026-05-12 — Phase B complete (sample-based transcript refinement)
- All 6 Phase B slices on `main` (`764a71a`): B1 `fetch-transcripts` CLI + non-legacy fetch path (`youtube.stub_transcript_fetcher`, `db.list_primary_channel_transcript_status`); B2 `refinement_runs` / `refinement_episodes` / `taxonomy_proposals` tables + `assignment_source='refine'` + `refinement_run_id` on the junction tables (CHECK-rebuild repair `_repair_video_topic_refine_source_constraint` + additive columns) + db helpers; B3 `refinement.py` core (`run_refinement` 3-stage: resolve+pool+pick → fetch transcripts → create-run+batch+persist; ⅔-coverage/⅓-blind-spot picker; `refinement.transcript@refinement-v1` extractor prompt; `stub_refinement_llm`; `make_real_refinement_llm_callable` gated by `RALPH_ALLOW_REAL_LLM=1`) + `refine --stub|--real [--yes]` CLI with pre-flight cost confirm; B4 taxonomy-aware discovery prompt (`DISCOVERY_PROMPT_VERSION` → `discovery-v5`, curated topic/subtopic names fed in) + `run_discovery` `ON CONFLICT` never downgrades `refine`/`manual` rows; B5 new "Refine" stepper stage + sample-setup screen (`GET /api/refine/sample`, `POST /api/refine/fetch-transcripts`, `POST /api/refine` daemon-thread, `GET /api/refine/status/<id>`); B6 proposal-review screen (`db.list_pending_taxonomy_proposals`, `POST /api/refine/proposal/{accept,reject}`, before→after sanity panel via `db.list_refinement_episode_changes` + `refinement_episodes.assignments_before_json`, "transcript-checked" pill on `refine`-source episode cards, re-run-Discover nudge).
- Verify gate **369 green**. Cheatsheet §2 + `docs/operator-workflow.md` Phase B section + `.scratch/phase-b-refinement/SMOKE.md` (real DOAC runbook) all current.
- **Not yet exercised against real DOAC data.** Next: the paid operator pass per `SMOKE.md` (fresh `discover --real` → Refine stage → accept proposals → `discover --real` re-run), then scope Phase C.

### 2026-05-08 — Phase A complete
- All 11 Phase A slices merged into `main`. Ralph harness validated: AFK runs (3 iterations × multiple slices) + HITL pacing for paid-LLM and design slices.
- Sticky-curation chain shipped (slice 08): `topic_renames` event log, `db.rename_topic`, `discovery._apply_renames_to_payload` fixed-point chain (cycle-guarded), `_suppress_wrong_assignments_in_run`, `topics.first_discovery_run_id` for new-topic "New" badge.
- GUI plan trilogy (slices 11/12/13) shipped Channel Overview panel, Run-ID demote into Run history (advanced) details, and 3-state comparison-readiness pill (`too_few` / `needs_transcripts` / `ready`).

### 2026-05-09 — CLI `--real` flag + audit + sticky-curation real-data validation
- Issue 02b: `discover` / `analyze` got required `--stub|--real` mutex; `make_real_llm_callable` enforces `RALPH_ALLOW_REAL_LLM=1` before any API call; `--model` overrides default.
- `extractor/anthropic_runner.py` writes `tokens_in` / `tokens_out` / `cost_estimate_usd` into `llm_calls`. New `extractor/pricing.py` carries Haiku/Sonnet/Opus list pricing; batch API gets 50% discount.
- `discovery_runs` gained `error_message` + `raw_response` columns; failed paid runs are now recoverable from disk.
- WSGI threading mixin in `review_ui.py` prevents connection-queue deadlock under VSCode port-forward auto-detection.
- 3/3 sticky-curation paths (rename replay, wrong-topic suppression, wrong-subtopic suppression) PASS on `tmp/doac-sticky.sqlite` after 2 paid Haiku 4.5 runs (~$0.057 cumulative).
- Two findings logged: (1) sticky-curation chain is exact-string-match — Haiku word-choice variance bypasses it; (2) `llm_calls` cost columns now wired (was open at the start of the session).

### 2026-05-09/10 — Claude Design hand-off + GUI rebuild (16 commits)
- Reskin to paper/ink/teal palette + Poppins/Source Serif 4/JetBrains Mono fonts.
- Topbar (wordmark + version + channel pill) + 4-stage stepper (`stepper-as-router`).
- Stage pages: Supply (real channel + videos + transcript pills, Newest/Oldest sort, Load-more pagination at 50/page, cap 500), Discover (real run history + cost column + clickable rows snap to Review), Consume (real topic list + static sketch claim card).
- Review canvas: minimap aside + topic-overview pillar grid (chips + dot grid + "X% high-confidence") + focused topic canvas (focus-head + subtopic tabs + episode rows with confidence/reason/`also_in` pills + Watch/Wrong-topic/Wrong-subtopic actions).
- Wired controls: Run discovery (`POST /api/discover`, modal-confirmed, mode-toggle stub/real), Re-ingest (`POST /api/reingest`, primary channel from DB), Edit channel (`POST /api/channel/edit`, modal form pre-filled).
- Discovery extractor: `max_tokens` default raised 4096 → 64000; truncation skips retry-once (saves ~50% on deterministic ceiling fails); single-call path streams via `messages.stream(...).get_final_message()` so high `max_tokens` doesn't trip the SDK's 10-min synchronous-timeout guard.
- Stream/poll for in-flight discovery: async daemon thread + `discovery_runs.status='running'` + `GET /api/discovery_runs/<id>` polled every 1.5s. Migration via `_repair_discovery_runs_status_constraint` (legacy_alter_table=ON + foreign_keys=OFF preserves child FKs).
- Live real-LLM smoke through the new UI: $0.0529, 50 assignments / 8 topics, sticky-curation new-topic-badge path exercised.
- Bug surfaced and not yet fixed: Haiku occasionally references undeclared subtopics in assignments → strict validator raises → user pays ~$0.05 per occurrence. Open as next slice.

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

### 2026-04-25
- Living project docs added to support resumable development.
- Captured that the UI exists because CLI-only testing and QA became too difficult in practice.
- Built the first-pass Topic Map view in `review_ui.py` (parts of which carry over).
