# 03 — `refinement.py` core + `refine --stub` CLI

Status: needs-triage
Type: AFK (slice code is offline — stub LLM + fake fetcher; `refine --real` is added but not exercised here. The real-LLM operator smoke is a separate HITL runbook.)
Branch: `feat/issue-03-refinement-core-and-cli`
Spec: `PRD_PHASE_B.md` (module 1 — "Refinement module"; the six slices §B3); `ROADMAP.md` §B; ADR 0001 (extractor-only LLM module)
User stories covered: 1, 2, 5, 6, 7, 9, 14, 15, 16
Likely two Ralph iterations: (3a) sample picker + replacement round + internal transcript fetch + run/episodes persistence; (3b) `refinement.transcript` prompt + per-transcript `Extractor.run_batch` + proposals/refine-assignments persistence + stub/real LLM + CLI + cost gate.

## Context

The deep module of Phase B. Given a discovery run and a sample of its episodes, fetch the sampled transcripts and make one LLM call per transcript that re-judges the episode's assignments from what was said and proposes taxonomy nodes the metadata pass missed. Idempotent at the run level. Goes through the `extractor/` registry — no second LLM-calling module.

## What to build

### `refinement.py`

- `run_refinement(db_path, discovery_run_id=None, llm=stub_refinement_llm, *, sample=None, transcript_fetcher=None, sample_size=15) -> RefinementRun`
  - `discovery_run_id` defaults to the channel's latest discovery run.
  - **Sample picker** (when `sample is None`): candidate pool = episodes assigned in that discovery run that are not Shorts (`duration_seconds > 180` or NULL) and have `transcript_status='available'` or no `video_transcripts` row (fetchable). Coverage slots ≈ ⌊⅔ · sample_size⌋: round-robin over topics ordered by episode count desc, taking each topic's single highest-confidence pool member not yet picked — every topic gets one before any gets two. Blind-spot slots = the remainder: pool ordered by lowest assignment confidence, then membership in the unassigned bucket; dedup against coverage. Topics with no pool members skipped silently; pool smaller than `sample_size` → proceed short with a warning. (`sample` given → use it verbatim, skip the picker.)
  - **Transcript fetch**: for picked IDs without an `available` row, fetch via the slice-B1 logic (`transcript_fetcher` injectable for tests). Episodes whose fetch returns non-`available` are dropped; ask the picker once for replacements from the remaining pool, then proceed (no unbounded loop). Record the surviving set in `refinement_episodes` with `transcript_status_at_run`.
  - **Per-transcript LLM call**: register prompt `refinement.transcript@refinement-v1` in `extractor/`. Context per episode = the transcript text (whole, no chunking, no summarize, no ad-read filter), the current taxonomy (topic names with their subtopics), and the episode's current metadata-grade assignments. Run all sampled episodes through `Extractor.run_batch` (it routes through the batch API at ≥10 calls automatically). Strict-JSON output, `additionalProperties: false`, schema-validated with one retry (Extractor owns this): `assignments: [{topic, subtopic?, confidence, reason}]`, `new_subtopic_proposals: [{name, parent_topic, evidence}]`, `new_topic_proposals: [{name, evidence}]`.
  - **Persistence**: create the `refinement_runs` row `status='pending'` BEFORE the batch (so a killed/declined run leaves an auditable row), flip to `running`, then `success`/`error`. For each sampled episode, write its refine `assignments` replace-wholesale (`db.write_refine_assignments` from slice B2 — `assignment_source='refine'`, user-curated `wrong_assignments` marks win, a topic not re-affirmed disappears for that episode). Insert all proposals as `taxonomy_proposals` rows (`status='pending'`, `parent_topic_name`, `evidence`, `source_video_id`). On any exception during the batch/persist, mark the run `error` and re-raise; no partial taxonomy nodes created (proposal/assignment writes only happen after a successful payload per episode — a mid-batch failure leaves the runs/episodes rows and an `error` status, nothing else).
  - `RefinementRun` dataclass: `run_id`, `discovery_run_id`, sampled episodes, proposals, per-episode reassignments — enough for the CLI to print a summary and for `/api/refine/status` (slice B5) to report.
- **`stub_refinement_llm`** — matches the per-transcript `LLMCallable` signature. For each episode: echo its current assignments back as `assignments` (visible no-op reassignment), plus one deterministic `new_subtopic_proposal` (`parent_topic` = the episode's top current topic, `name = f"Stub subtopic ({topic})"`, `evidence = "stub evidence"`). For the **first** sampled episode also emit one deterministic `new_topic_proposal` (`name = "Stub topic"`, `evidence = "stub evidence"`). Empty otherwise. Use for any free wiring check; tests inject it directly (like `discovery.stub_llm`).
- **`make_real_refinement_llm_callable(connection, *, model=None)`** — builds an `AnthropicRunner` + `Extractor`-backed adapter; raises unless `RALPH_ALLOW_REAL_LLM=1` (mirror `discovery.make_real_llm_callable`). Default model Haiku 4.5.
- `REFINEMENT_PROMPT_VERSION = "refinement-v1"`, written into `refinement_runs.prompt_version`.

### CLI (`cli.py`)

- `refine --db-path X --project-name Y [--discovery-run-id R] [--video-ids a,b,c] [--sample-size N] (--stub | --real) [--yes]` — `--stub`/`--real` mutex (required), like `discover`. `--stub` uses `stub_refinement_llm`, is free and deterministic. `--real` builds `make_real_refinement_llm_callable` (which enforces `RALPH_ALLOW_REAL_LLM=1` and fails fast). For `--real`: after the sample is finalized (post replacement round, so transcript count + rough token counts are known), print an estimated cost (Σ per-transcript `tokens_in` estimate × Haiku input price from `extractor/pricing.py` + a flat output allowance) and require `--yes` or an interactive `[y/N]` to proceed. Print a run summary (n sampled, n proposals by kind, n reassignments).
- `analyze` is **not** extended.

### Tests

- New `test_refinement.py`, added to `.ralph/verify.sh` default targets. Inject `stub_refinement_llm` + a fake `transcript_fetcher`. Cover: picker math (⅔/⅓ split, one-per-topic-before-two, blind-spot ordering, dedup), topics with no pool members skipped, pool < sample_size proceeds short, `sample=` bypasses the picker; the replacement round (a fake that returns `not_found` for one ID → it's dropped and replaced once, then proceeds); persistence (run lifecycle pending→running→success; `refinement_episodes` recorded; proposals inserted with the stub's deterministic shape including the first-episode topic proposal; refine assignments written replace-wholesale; a `wrong_assignments`-suppressed row not re-added); the error path (an LLM that raises → run `error`, no `taxonomy_proposals`/`video_topics` writes). No real LLM/network in the gate.
- Cheatsheet entries for `refine` (§1, current). WORKLOG entry. `docs/operator-workflow.md` Phase B section (sample setup → fetch → cost confirm → review → re-`discover`).

## Acceptance criteria

- [ ] `refine --stub` against a DB with a discovery run produces a `refinement_runs` row (`status='success'`), `refinement_episodes` for the sampled set, `taxonomy_proposals` (one subtopic proposal per sampled episode + one topic proposal for the first), and `refine`-source `video_topics`/`video_subtopics` rows for each sampled episode — and a second `refine --stub` run is non-destructive to the first (new run, new rows).
- [ ] The sample picker implements the ⅔ coverage / ⅓ blind-spot split with one-per-topic-before-two round-robin; `--video-ids` bypasses it; a dead transcript triggers exactly one replacement round then proceeds.
- [ ] Replace-wholesale: a sampled episode's refine assignments replace its prior non-curated assignments; a `wrong_assignments`-suppressed assignment is not re-added.
- [ ] LLM failure mid-run → run marked `error`, no partial taxonomy nodes or assignments persisted.
- [ ] `refine --real` is gated by `RALPH_ALLOW_REAL_LLM=1` AND prints a pre-flight cost estimate requiring `--yes`/interactive confirm; goes through `Extractor.run_batch`; lands `llm_calls` audit rows. (Not exercised end-to-end in the gate — covered by the operator smoke runbook.)
- [ ] `refinement.py` adds no new LLM-calling code outside the `extractor/` registry. `test_refinement.py` in the gate and green; `test_transcripts.py` untouched.

## Blocked by

- Slice 02 (`feat/issue-02-refinement-schema`) — needs the tables, the `'refine'` source value, and the db helpers.
- Slice 01 (`feat/issue-01-fetch-transcripts`) — reuses its transcript-fetch logic for the sample.
