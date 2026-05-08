# Issue 12 — GUI plan finish: Run-ID demote (Priority 2)

Roadmap sections: §A7

User stories covered:
- As a routine reviewer, I should not have to pick a run ID before doing anything — the latest relevant run should be selected for me.
- As an auditor, I still need to be able to inspect a previous run's labels — the run picker should remain reachable, just demoted from primary nav.

## Scope

Implements Priority 2 of `GUI_UX_PLAN.md` ("Replace run-ID-first navigation"). Two concrete changes:

1. The legacy suggestion-run dropdown (`<select id="run-select">`) is currently in the topbar's primary `.controls.row` between the channel context and the topic/subtopic selectors. Move it into a collapsed `<details class="run-history-advanced">` block placed below the `.generator` div in the topbar. The select itself, its event listener, and its data flow stay otherwise unchanged — this is a markup relocation, not a behavior rewrite. Default state: collapsed (no `open` attribute).

2. Today the subtopic-review pane is keyed off `state.payload.run_id`, which defaults to the latest topic-suggestion run overall. That latest run may not have subtopic labels for the parent topic the user just selected. For Priority 2's "for subtopics, select by parent topic first, then latest run" rule: when the user changes the parent-topic selector, the active run should switch to the latest run that has subtopic labels for that specific topic (if any). Implementation:
   - New helper `_latest_subtopic_run_id_for_topic(db_path, topic_name) -> int | None` in `review_ui.py`. Returns the max `run_id` in `subtopic_suggestions` for that topic name, or `None`.
   - `build_state_payload` adds `latest_subtopic_run_id_by_topic: dict[str, int]` (only entries for topics that have any subtopic-suggestion run). Empty dict when nothing's been generated yet.
   - JS topic-select change handler: reads `state.payload.latest_subtopic_run_id_by_topic[topicName]`; if present and different from the currently-selected `run-select.value`, sets it before the next `fetchState`.

## Acceptance criteria

1. `<select id="run-select">` no longer appears inside the topbar primary `.controls.row` block. It now lives inside a `<details class="run-history-advanced">` whose summary text is "Run history (advanced)". HTML test asserts both: (a) the `details` element is rendered with class `run-history-advanced`, (b) the `run-select` is a descendant of it, (c) the markup between `<div class="controls row">` and its closing `</div>` no longer contains `id="run-select"`.
2. The collapsed block contains a one-line muted hint immediately above the select: "Pick an older run to inspect its labels. Routine review uses the latest run automatically."
3. Topic-select and subtopic-select remain inside the topbar primary `.controls.row`. They are not moved.
4. `UI_REVISION` is bumped. The new value preserves both `channel-overview` and `discovery` substrings so the existing `test_ui_revision_advances_for_*` assertions (9 of them) keep passing.
5. New helper `_latest_subtopic_run_id_for_topic(db_path: str, topic_name: str) -> int | None` exists in `review_ui.py`. Returns `None` when the topic has no subtopic-suggestion rows; otherwise the max `run_id`.
6. `build_state_payload` exposes `latest_subtopic_run_id_by_topic: dict[str, int]` populated from every topic in `topic_reviews`. Topics with no subtopic suggestion rows are omitted (so the dict can be empty).
7. JS: when the topic-select dropdown changes, if `state.payload.latest_subtopic_run_id_by_topic[newValue]` exists and differs from the currently-selected run-select value, set `run-select.value = ...` before the existing `fetchState()` call. Existing topic-select change behavior is otherwise preserved.
8. Verify gate stays green throughout (`.ralph/verify.sh`).

## Out of scope

- Real-LLM smoke for this slice (no LLM call site changes).
- Touching the `discovery_runs`-based discovery topic map (that's a separate run system; this slice is purely about the legacy topic/subtopic suggestion runs).
- Removing the run-select entirely. Auditability is the reason it stays — only the visual prominence changes.
- Topic-select behaviour beyond the one new "if a subtopic run exists for this topic, switch the run picker to it" tweak.
- CSS polish beyond what's needed for the `<details>` element to render acceptably alongside the existing topbar.

## Agent notes

- The existing `topic-select` change handler is at `review_ui.py:~1929` (event listener registration block). The change-handler chain currently calls `fetchState`. Add the run-select adjustment *before* that fetch so the next state pull uses the new run id.
- `state.payload.runs` already includes `pending_label_count` / `subtopic_pending_label_count` per run (see line 1716). The new `latest_subtopic_run_id_by_topic` payload is a separate map keyed by topic name — do not try to derive it from the existing `runs` array, because that array is already filtered/enriched and won't tell you which run had labels for which specific topic.
- For the helper SQL: query `subtopic_suggestions` (or whichever table holds raw subtopic suggestion rows — confirm by reading `db.py`) filtered by topic name, returning `MAX(run_id)`. If the table joins through `topic_id`, resolve the topic name to id first; otherwise the topic-name column is direct.
- The current `<select id="run-select">` is at `review_ui.py:781`. The `.generator` div ends around line 807 (`</div>` after `Generate subtopic suggestions` button). Place the new `<details>` between the `.generator` close and the `status-box` div, still inside `<section class="topbar">`.
- Watch the `test_ui_revision_advances_for_*` test family (~9 cases) — they look for substrings in the `UI_REVISION` constant. Pick a new value like `2026-05-08.4-run-history-advanced-channel-overview-discovery-panel` to keep prior substrings present.
- HITL_PAUSE if you find that subtopic suggestion runs are stored differently than expected (e.g., schema mismatch with how the spec assumes the table looks). Better to surface the mismatch than guess.
