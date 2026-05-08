# Slice 08 overlay — sticky curation across discovery re-runs

Roadmap sections: §A2

PRD reference: `.scratch/phase-a-topic-map/issues/08-sticky-curation-across-reruns.md`

## Scope

Make user curation stick when the user re-runs discovery on a channel.
Narrowed to the 3 explicit acceptance tests — auto-replay only, no
diff-approval GUI.

The PRD's "replay events from slices 06+07" framing assumes an event
log that only partially exists (only `wrong_assignments` ships an
event row; rename/merge/split/move are applied directly to the live
tables). This slice adds the missing **rename** event log and replays
two event types — renames and wrong-assignments — into every
subsequent discovery run. Merge/split/move event logging is
**out of scope** here and is deferred to a potential follow-up
(issue 08b) if a real second DOAC run shows it matters in practice.

## Acceptance criteria (mapped to ROADMAP boxes)

The two unchecked §A2 sub-bullets added to ROADMAP for this slice
are the iteration units:

1. **§A2 box 1 — rename event log + replay machinery.**
   - New `topic_renames(id, project_id, topic_id, old_name, new_name,
     created_at)` table in `db.py`'s `TABLE_STATEMENTS`. FK
     `topic_id → topics(id) ON DELETE CASCADE`.
     `topic_id` is the row's id at rename time (kept for forensics
     even though replay matches by name).
   - `/api/discovery/topic/rename` (in `review_ui.py`) inserts a
     `topic_renames` row after the existing `UPDATE topics SET name`
     succeeds. Use the same connection/transaction as the rename
     itself so a failed rename does not record a phantom event.
   - In `discovery.py`, add `_apply_renames_to_payload(connection,
     project_id, payload) -> DiscoveryPayload`. Loads all
     `topic_renames` rows for the project, builds a `{old_name:
     latest_new_name}` map (collapsing multi-hop chains: walk each
     name forward through the map until it stabilizes — A→B then
     B→C means A→C). Returns a new `DiscoveryPayload` with rewritten
     `topics` (deduped after rewrite — preserve first-seen order),
     `subtopics[i].parent_topic`, and `assignments[i].topic_name`.
     Pure function; no DB mutation.
   - Add `_suppress_wrong_assignments_in_run(connection, channel_id,
     run_id)` running *after* the existing assignment inserts. SQL:
     `DELETE FROM video_topics WHERE discovery_run_id = :run_id AND
     (video_id, topic_id) IN (SELECT wa.video_id, t.id FROM
     wrong_assignments wa JOIN topics t ON t.id = wa.topic_id JOIN
     videos v ON v.id = wa.video_id WHERE v.channel_id = :channel_id
     AND wa.subtopic_id IS NULL)`. Then a parallel DELETE for
     `video_subtopics` covering rows where `wa.subtopic_id IS NOT
     NULL`. (Note: `wrong_assignments.topic_id` is a stable id, so
     name-rewriting via the rename map is irrelevant here — the
     curated topic id is what we suppress.)
   - Wire both into `run_discovery`: `_apply_renames_to_payload`
     called immediately after `payload = llm(videos)` parses the
     response (so the rest of `run_discovery` sees the rewritten
     payload); `_suppress_wrong_assignments_in_run` called after the
     final assignment loop completes, before commit.
   - Tests:
     - `test_rename_then_rerun_keeps_curated_name_with_episodes`:
       run discovery once with stub → rename topic to a new name →
       run discovery again with same stub → assert exactly one topic
       row exists with the new name, all original `video_topics`
       rows still attach to it, and no orphan row for the old name.
     - `test_mark_wrong_then_rerun_suppresses_assignment`: run
       discovery → call `mark_wrong` for one (video, topic) pair →
       run discovery again → assert no `video_topics` row exists
       for that pair under the new run's `discovery_run_id`.
     - `test_apply_renames_to_payload_collapses_multi_hop_chain`:
       seed `topic_renames` with A→B and B→C → assert payload
       containing topic "A" gets rewritten to "C" (not stuck at "B").

2. **§A2 box 2 — surface new topics introduced by re-runs.**
   - Add `_topics_introduced_in_run(connection, channel_id, run_id)
     -> list[str]` helper. Returns topic names whose earliest
     `video_topics.discovery_run_id` (across all `discovery_runs` for
     this channel) equals the given `run_id`. Ordered by topic name
     for stable test assertions. **Empty list when this is the
     channel's first successful discovery run** — otherwise every
     topic is "new" on first discovery and the badge becomes
     meaningless.
   - Extend `_build_discovery_topic_map` (in `review_ui.py`) payload
     with a top-level `new_topic_names: [<name>, ...]` list. Empty
     list, never null.
   - JS `renderDiscoveryTopicCard` adds a small `<span class=
     "discovery-topic-new-badge">New</span>` next to the topic name
     when its name is in `new_topic_names`. Reuse the existing
     `.discovery-topic-meta` inline-pill pattern (see slice 05's
     `.discovery-episode-also-in` for the precedent).
   - Tests:
     - `test_topics_introduced_in_run_returns_only_new_names`: run
       discovery once with stub topics {Health, Business} → run
       discovery again with stub patched to {Health, Business,
       Tech} → `_topics_introduced_in_run(...)` returns `["Tech"]`.
     - `test_topics_introduced_in_run_empty_on_first_run`: single
       discovery run → returns `[]`.
     - `test_state_payload_carries_new_topic_names`: after the
       second-run fixture above, `discovery_topic_map.new_topic_names`
       in `/api/state` payload equals `["Tech"]`.
     - `test_html_page_renders_new_topic_badge`: HTML page contains
       the `discovery-topic-new-badge` class string when the run's
       topic map includes a new topic.

3. **Loose-end tests + COMPLETE** (no separate ROADMAP box, but
   needed before COMPLETE):
   - Round-trip integration test
     `test_curation_survives_full_rerun_round_trip`: rename Health →
     Wellbeing, mark wrong on (vid_X, Business), then run discovery
     again with same stub. Assert simultaneously: (a) topic still
     "Wellbeing" with all original episodes, (b) (vid_X, Business)
     not in new run's `video_topics`, (c) `_topics_introduced_in_run`
     reports any new stub topics.

## Out of scope (deferred to potential issue 08b)

- Event logging for **merge / split / move-subtopic**. Not in any of
  the 3 acceptance tests. Defer until a real second DOAC run
  exposes the gap. If you find yourself writing one, stop and pause
  — it's scope creep.
- **Diff-approval GUI** with per-change accept/reject from the PRD
  (lines 21–22). Auto-replay + new-topic badge is the minimum that
  satisfies the 3 tests; full diff GUI is a separate slice.
- **Real-LLM smoke**. The 3 acceptance tests are stub-LLM-based.
  Validation against a real second DOAC run is a §A5 / issue-10
  follow-up if needed.

## Agent notes (gotchas)

- **Topic id stability across runs.** `topics` is unique on
  `(project_id, name)` and uses `INSERT ... ON CONFLICT DO UPDATE`,
  so the same topic_id is reused across runs when the LLM produces
  the same name. After a rename, the existing row's name changes;
  a re-run emitting the *old* name will create a NEW row (different
  topic_id, old name back). The rename rewrite step prevents that
  from ever happening.

- **Rename map collapse must terminate.** Multi-hop chains (A→B then
  B→C) need a fixed-point walk, not a single lookup. Watch out for
  cycles (A→B then B→A) — extremely unlikely but a `seen` set guard
  is cheap insurance.

- **`run_discovery` errored-run path.** The slice 02 `try/except`
  around `llm(videos)` records a `status='error'` row with no
  topics/assignments persisted. `_apply_renames_to_payload` runs
  *after* the parse succeeds and *before* persistence; if the parse
  raises, the rename rewrite never runs and that's fine.

- **`stub_llm` first-run-empty topic_renames.** On the very first
  discovery run for a project, `topic_renames` is empty. The rewrite
  is a no-op. Don't add a "skip if empty" early-return — the empty
  map already short-circuits the loop, and the no-op-via-empty path
  needs to stay covered by tests.

- **Tests using the existing `_make_db` helper.** `test_discovery.py`
  has a `_make_db` / `_seed_videos` pattern. New tests should use
  the same fixture path; don't invent a new helper. The
  multi-stub-runs pattern is novel — easiest is to call
  `run_discovery(..., llm=stub_llm)` twice on the same connection
  with the same channel_id.

- **`/api/discovery/topic/rename` test fixtures** in
  `test_review_ui.py` already exercise the happy path; extend the
  existing test (or add an adjacent one) to assert
  `topic_renames` row count goes up by 1 after a successful rename.
