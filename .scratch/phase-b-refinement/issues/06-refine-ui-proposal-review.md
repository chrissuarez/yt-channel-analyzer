# 06 — Refine UI: proposal-review screen + transcript-checked pill

Status: done (2026-05-12 — see WORKLOG / ROADMAP §B6; 2 iterations: part 1 proposal-review + transcript-checked pill, part 2 before→after sanity panel + `assignments_before_json`)
Type: AFK (scoped to stay under the 300-changed-line `review_ui.py` HITL-pause threshold; landed ≈150 lines across the two parts)
Branch: `feat/issue-06-refine-ui-proposal-review`
Spec: `PRD_PHASE_B.md` (modules 5–6 — "Refine UI"; the six slices §B6); `ROADMAP.md` §B
User stories covered: 7, 8, 9, 10, 11

## Context

The second Refine-stage UI slice. After a refinement run completes, the operator reviews the taxonomy proposals (accept → real node), sanity-checks the sampled episodes' transcript-grade reassignments, and is nudged to re-run discovery. Also surfaces `refine`-source assignments in the topic map with a "transcript-checked" pill.

## What to build

- **Proposal-review screen** (rendered when a refinement run is `success`, or when navigating to Refine with completed runs present). Lists `taxonomy_proposals`, grouped: new subtopics under each parent topic, then new topics — **all `pending` proposals across all refinement runs**, grouped by run, newest run first. Each card: `name`, `parent_topic` (for subtopics), the `evidence` snippet, and the source episode (title, link). **Accept** / **Reject** buttons per card:
  - `POST /api/refine/proposal/accept` `{proposal_id}` → `db.accept_taxonomy_proposal` (creates the `topics`/`subtopics` row if absent, parent resolved through the rename map; idempotent; if the parent no longer exists, the proposal is rejected and that's reported back). `POST /api/refine/proposal/reject` `{proposal_id}` → `db.reject_taxonomy_proposal`. Both update the card in place.
- **Before→after sanity panel**: per sampled episode in the run, show its assignments before the run vs. after (topics added / dropped, subtopics corrected), reading the `refine`-source rows vs. what the discovery run had. A **mark-wrong** control per after-assignment, reusing the existing `/api/discovery/episode/mark-wrong` endpoint (no new endpoint).
- **Re-run-discovery nudge**: a closing banner — "Accepted N changes. Run discovery again to spread them across the channel." — linking the Discover stage's run-discovery button.
- **"Transcript-checked" pill in the topic map**: in `renderDiscoveryEpisodeItem` (both renderers), when an episode's assignment row has `assignment_source === 'refine'` (carried in the payload since slice B2), render a small pill next to the confidence indicator. The episode's `reason` (now transcript-grounded) renders as-is.
- Reuse existing patterns: the suggest/review/accept card pattern, the mark-wrong endpoint, the topic-map episode renderers, existing CSS. Bump `UI_REVISION`.
- **Tests**: proposals render grouped, newest run first; **Accept** creates the subtopic/topic (via the db helper) and marks the proposal `accepted`; **Reject** marks it `rejected`; accepting a proposal whose node already exists is a no-op mark-accepted; accepting one with a deleted parent reports rejection; the before→after panel shows added/dropped/corrected for a seeded refine run; a `refine`-source episode card renders the "transcript-checked" pill (HTML assertion). Keep the `review_ui.py` diff under ~300 lines.
- WORKLOG entry. `docs/operator-workflow.md` Phase B section finalized (the full walk: fresh `discover` → Refine stage → sample → fetch → cost confirm → proposal review → re-`discover`). Cheatsheet note on the Refine stage. Create the operator runbook `.scratch/phase-b-refinement/SMOKE.md` (real `fetch-transcripts` on a handful of DOAC episodes, real `refine --real` ~$0.40 with the cost-confirm, UI eyeball of proposals + before→after + the pill, then `discover --real` to confirm spread).

## Acceptance criteria

- [ ] After a refinement run completes, the Refine stage shows a proposal-review screen listing all `pending` `taxonomy_proposals` grouped (subtopics under parents, then topics), newest run first, each with name / parent / evidence / source episode.
- [ ] **Accept** creates the real `topic`/`subtopic` (parent resolved through renames; idempotent; deleted-parent → rejected with a clear report) and marks the proposal `accepted`; **Reject** marks it `rejected`; the card updates in place.
- [ ] A before→after panel per sampled episode shows topics added/dropped and subtopics corrected, with a mark-wrong control reusing the existing endpoint.
- [ ] A re-run-discovery nudge links the Discover stage's run button.
- [ ] Episodes whose assignment is `assignment_source='refine'` show a "transcript-checked" pill in the topic map (both episode renderers).
- [ ] `review_ui.py` net diff stays under the 300-line HITL-pause threshold; `UI_REVISION` bumped. `.scratch/phase-b-refinement/SMOKE.md` exists. Verify gate green; `test_transcripts.py` untouched.

## Blocked by

- Slice 05 (`feat/issue-05-refine-ui-setup`) — needs the Refine stage shell, `/api/refine`, and the status endpoint.
- Slice 03 (`feat/issue-03-refinement-core-and-cli`) — needs `taxonomy_proposals` populated by real runs.
- Slice 02 (`feat/issue-02-refinement-schema`) — needs the `assignment_source` carried in the topic-map payload (for the pill) and the proposal accept/reject db helpers.
