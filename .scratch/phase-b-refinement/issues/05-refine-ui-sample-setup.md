# 05 — Refine UI: sample-setup screen + `/api/refine` async run

Status: done (2026-05-12 — see WORKLOG / ROADMAP §B5; 4 iterations: parts 1–3 endpoints, part 4 front end)
Type: AFK (scoped to stay under the 300-changed-line `review_ui.py` HITL-pause threshold; part 4 ran ~344 lines — done interactively, not AFK)
Branch: `feat/issue-05-refine-ui-setup`
Spec: `PRD_PHASE_B.md` (modules 5–6 — "Refine UI"; the six slices §B5); `ROADMAP.md` §B
User stories covered: 1, 2, 3, 4, 5

## Context

The first of two Refine-stage UI slices. Adds a new stand-alone "Refine" stage to the stepper with the sample-setup screen: shows the auto-picked sample, lets the operator edit it, fetch transcripts, see a cost estimate, and kick off the (async) refinement run. The proposal-review screen is slice B6.

## What to build

- **New "Refine" stage** in the topbar/stepper (alongside Supply / Discover / Consume). Selecting it renders the sample-setup screen for the active channel's latest discovery run.
- **`GET /api/refine/sample[?discovery_run_id=R]`** — returns the auto-picked sample (the slice-B3 picker, no side effects): per episode `{youtube_video_id, title, topic, confidence, transcript_status, slot_kind ['coverage'|'blind_spot']}`. Also returns the candidate-pool size and the discovery run it's based on.
- **Sample-setup screen** (HTML/JS in `review_ui.py`): table of the picked episodes (title, the topic it covers, current confidence, transcript status, slot kind), with **remove** per row and an **add by video ID / URL** box (validates the ID belongs to the primary channel). A **Fetch transcripts & estimate** action: runs the slice-B1 fetch logic for the picked IDs (POST endpoint, e.g. `/api/refine/fetch-transcripts` taking the ID list), updates each row's transcript status, drops episodes that came back non-`available` with a visible note, and shows the cost estimate (Σ per-transcript `tokens_in` estimate × Haiku input price from `extractor/pricing.py` + flat output allowance). Then a **Run refinement ($X.XX)** button.
- **`POST /api/refine`** — body: the finalized video-ID list (+ optional `discovery_run_id`). Starts `run_refinement` on a daemon thread (same async pattern as `/api/discover`), returns a refinement-run id immediately. **`GET /api/refine/status/<id>`** — polled (~1.5s, like discovery runs): `{status, n_sample, n_proposals, error?}`. On `success`, the UI flips to the proposal-review screen (B6 — until B6 lands, just show "run complete, N proposals" and a link/placeholder).
- Reuse existing patterns: the async-run + poll plumbing from `/api/discover` and `GET /api/discovery_runs/<id>`, `formatDuration`/`formatDate`, the existing card/table CSS. Bump `UI_REVISION`.
- **Tests**: state-payload / endpoint tests in the existing UI test file (or a new one in the gate): `GET /api/refine/sample` returns a well-formed sample for a seeded discovery run; the HTML page renders the new "Refine" stage; `POST /api/refine` creates a `pending` `refinement_runs` row and the status endpoint reports it; the fetch-transcripts endpoint updates transcript statuses (use the slice-B1 stub fetcher / injectable). Keep the `review_ui.py` diff under ~300 lines — if the screen + endpoints can't fit, land the endpoints + stage shell here and the richer setup interactions as a follow-up.
- WORKLOG entry. Cheatsheet/operator-workflow note about the Refine stage (or fold into B6).

## Acceptance criteria

- [ ] A new "Refine" stage appears in the stepper and renders the sample-setup screen for the active channel's latest discovery run.
- [ ] `GET /api/refine/sample` returns the ⅔/⅓ auto-picked sample with per-episode topic/confidence/transcript-status/slot-kind and the pool size, with no side effects.
- [ ] The operator can remove episodes and add one by video ID/URL (rejected if not on the primary channel); a **Fetch transcripts & estimate** action fetches the picked transcripts, updates statuses, drops dead ones with a note, and shows a cost estimate.
- [ ] **Run refinement** POSTs to `/api/refine`, which runs `run_refinement` on a daemon thread; `GET /api/refine/status/<id>` is pollable and reports `pending`→`running`→`success`/`error`.
- [ ] `review_ui.py` net diff stays under the 300-line HITL-pause threshold; `UI_REVISION` bumped. Verify gate green; `test_transcripts.py` untouched.

## Blocked by

- Slice 03 (`feat/issue-03-refinement-core-and-cli`) — needs `run_refinement`, the picker, and the `refinement_runs`/`refinement_episodes` tables.
