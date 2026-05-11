# 04 — Discovery prompt taxonomy awareness + never-downgrade refine rows

Status: needs-triage
Type: AFK
Branch: `feat/issue-04-discover-taxonomy-aware`
Spec: `PRD_PHASE_B.md` (module 4 — "Discovery prompt taxonomy awareness"; the six slices §B4; the §Q5 decision); `ROADMAP.md` §B
User stories covered: 10, 12 (and improves sticky curation generally)

## Context

When the operator accepts a Phase B taxonomy proposal it becomes a real `topics`/`subtopics` row, but only the ~15 sampled episodes have transcript-grade assignments. The other ~435 stay unassigned to the new node until a `discover` run assigns them. Today the discovery prompt isn't told the existing taxonomy at all, so a re-run doesn't reliably reuse curated names. This slice closes that loop: feed the curated taxonomy into the discovery prompt so a post-Phase-B `discover` re-run reuses the accepted names and spreads them channel-wide, and protect transcript-grade rows from being downgraded by that metadata re-run.

Small, independent — can land anytime after `main` (doesn't depend on B1–B3, though it's only *useful* once proposals can be accepted).

## What to build

- **`run_discovery` feeds the current curated taxonomy into the prompt.** Before the LLM call, load the project's current topic names with their subtopic names; render them into the discovery prompt as a "taxonomy so far" block — instruction along the lines of: *"Here is the taxonomy already curated for this channel. Reuse these exact names where an episode fits one; you may also propose new topics/subtopics."* Pass through the same rename map (`_apply_renames_to_payload`) so the names shown are the current curated ones, not stale.
- **Bump `DISCOVERY_PROMPT_VERSION`** (e.g. `discovery-v5`) — old runs stay traceable to their prompt; the registered prompt name/version pair updates.
- **Never downgrade `refine`/`manual` rows.** `run_discovery`'s `INSERT ... ON CONFLICT(video_id, topic_id) DO UPDATE` on `video_topics` (and the `video_subtopics` equivalent) currently overwrites `assignment_source`, `confidence`, `reason`, `discovery_run_id` unconditionally. Change it so that when the existing row's `assignment_source` is `'refine'` or `'manual'`, the update keeps that `assignment_source` and the existing `confidence`/`reason` (transcript-grade data survives a metadata re-run); it may still update `discovery_run_id` to reflect that this run also saw the pair, or leave it — implementer's call, but the source + confidence + reason of a refine/manual row must not regress to `'auto'`. A metadata re-run can still INSERT brand-new `'auto'` rows for new (video, topic) pairs, and the existing `wrong_assignments` suppression still applies to all sources.
- **Tests** (in `test_discovery`): a second stub `discover` run is given the topics created by the first run and the prompt context includes them (assert the rendered prompt / context carries the curated names); a `video_topics` row written with `assignment_source='refine'` (and a chosen confidence/reason) is still `'refine'` with the same confidence/reason after a subsequent `discover` run that re-proposes that (video, topic) pair; a `'manual'` row likewise survives; a genuinely new (video, topic) pair from the new run is inserted as `'auto'`; `wrong_assignments` suppression still removes a wrong-marked pair regardless of source.
- WORKLOG entry. (No schema change; no cheatsheet change.)

## Acceptance criteria

- [ ] The discovery prompt/context includes the project's current curated topic + subtopic names (rename-resolved), with an instruction to reuse them; `DISCOVERY_PROMPT_VERSION` is bumped and written into new `discovery_runs` rows.
- [ ] A `discover` re-run after Phase B accepts a new subtopic reuses that subtopic's exact name when assigning episodes to it (verified via stub: the run is told the name, the stub honoring it produces `video_subtopics` rows under the existing node, not a near-duplicate).
- [ ] A `refine`-source or `manual`-source `video_topics`/`video_subtopics` row keeps its `assignment_source`, `confidence`, and `reason` after a subsequent `discover` run touches the same (video, topic/subtopic) pair.
- [ ] A new `discover` run still adds `'auto'` rows for new pairs, and `wrong_assignments` suppression still works for all sources.
- [ ] Verify gate green; `test_transcripts.py` untouched.

## Blocked by

None — can start immediately (branch off `main`). Best landed after slice 03 so the never-downgrade behavior has `refine` rows to protect, but not a hard dependency.
