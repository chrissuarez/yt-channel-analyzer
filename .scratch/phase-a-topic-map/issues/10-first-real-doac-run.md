# 10 — First real run on Diary of a CEO + cheatsheet update

Status: needs-triage
Type: HITL
User stories covered: 20, 21 (judgement call on map quality)
Roadmap sections: §A5

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

The validation event for Phase A. Run the full pipeline end-to-end on Diary of a CEO and judge whether the resulting topic map feels right.

Steps:
1. Run `analyze diary-of-a-ceo` from a clean state.
2. Wait for ingestion + discovery to complete.
3. Open the GUI and browse the resulting topic map.
4. Do at least one rename, one merge, and one move-episode action.
5. Re-run discovery and confirm the curation survives.
6. Update `YT_ANALYZER_CHEATSHEET.md` with the new primary commands and the operator workflow.
7. Update `WORKLOG.md` with the run results, the user's subjective verdict, and any rough edges discovered.

## Acceptance criteria

- [ ] DOAC fully ingested and discovery run completed successfully
- [ ] User has spent at least 30 minutes browsing the resulting map and exercising curation actions
- [ ] User records a verdict: does the map feel right? What's missing? What's wrong?
- [ ] `YT_ANALYZER_CHEATSHEET.md` updated for the new primary commands
- [ ] `WORKLOG.md` entry written with results and verdict
- [ ] If the verdict is "needs Phase B sooner" or "needs claim extraction sooner," that becomes the next roadmap update

## Why HITL

Only the user can answer "does this feel right for DOAC?" Phase A's definition of done is subjective by design — formal accuracy metrics are out of scope until a later phase creates a real need.

## Blocked by

- All previous slices (01–09)
