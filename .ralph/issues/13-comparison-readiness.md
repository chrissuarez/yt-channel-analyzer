# Issue 13 — GUI plan finish: Comparison readiness guardrails (Priority 4)

Roadmap sections: §A8

User stories covered:
- As a user evaluating whether a subtopic is worth a comparison-group dive, I should see at a glance whether it has enough videos *and* whether those videos have transcripts — not just one of the two.
- As a user looking at a 5-video subtopic with 0 transcripts, the GUI should tell me to fetch transcripts next, not tell me it's "ready for comparison".

## Scope

Implements Priority 4 of `GUI_UX_PLAN.md` ("Comparison readiness guardrails"). Three concrete changes, all inside `review_ui.py`:

1. **Backend**: extend `_build_topic_inventory` (currently `review_ui.py:2198–2267`) so each per-subtopic bucket carries:
   - `transcript_count: int` — videos in the bucket with `video_transcripts.transcript_status = 'available'`.
   - `processed_count: int` — videos in the bucket with `processed_videos.processing_status = 'processed'`.
   - `readiness_state: str` — one of `"too_few"`, `"needs_transcripts"`, `"ready"`.
     - `"too_few"` when `video_count < MIN_NEW_SUBTOPIC_CLUSTER_SIZE` (= 5).
     - `"needs_transcripts"` when `video_count >= MIN_NEW_SUBTOPIC_CLUSTER_SIZE` and `transcript_count == 0`.
     - `"ready"` when `video_count >= MIN_NEW_SUBTOPIC_CLUSTER_SIZE` and `transcript_count >= 1`.
   - `readiness_label` / `next_step` strings per state — see acceptance criteria.
   - `comparison_ready: bool` — kept as a back-compat alias of `readiness_state == "ready"`.

2. **HTML/CSS**: switch the readiness pill at `review_ui.py:1473` from a 2-class boolean (`ready`/`thin`) to a 3-class state-keyed render driven by `bucket.readiness_state` (CSS classes `ready`/`needs-transcripts`/`thin`). Add a third CSS rule next to the existing two at `review_ui.py:617–629`. Add a muted "X/Y transcripts" sub-line near the video-count pill so coverage is visible even when the state pill is `ready`.

3. **UI_REVISION bump**: `UI_REVISION` (currently `2026-05-08.4-run-history-advanced-channel-overview-discovery-panel`) needs to be advanced. Preserve the `channel-overview` and `discovery` substrings so the existing `test_ui_revision_advances_for_*` family stays green. Adding `comparison-readiness` to the new value lets the new HTML tests pin against it.

## Acceptance criteria

1. `_build_topic_inventory(db_path, topic_name=...)` returns subtopic buckets that each contain integer `transcript_count` and `processed_count` keys. Both default to `0` when no rows match. Both are de-duplicated per video — a video with both an `available` transcript and a `processed` row counts once for transcripts and once for processed (i.e., a LEFT JOIN must not inflate via Cartesian explosion across the two side tables).

2. Each bucket carries `readiness_state`: one of `"too_few"`, `"needs_transcripts"`, `"ready"`. The state ladder is exactly:
   - `video_count < 5` → `"too_few"`.
   - `video_count >= 5` and `transcript_count == 0` → `"needs_transcripts"`.
   - `video_count >= 5` and `transcript_count >= 1` → `"ready"`.

3. `readiness_label` and `next_step` per state:
   - `too_few`: `"Too thin to compare"` / `"Needs N more video(s) before comparison groups are useful."` (existing copy — keep).
   - `needs_transcripts`: `"Enough videos, no transcripts"` / `"Fetch transcripts for these videos before generating comparison groups."`.
   - `ready`: `"Ready for comparison"` / `"Enough videos to generate comparison-group suggestions."` (existing copy — keep).

4. `bucket.comparison_ready` (existing boolean) is preserved and equals `readiness_state == "ready"`. Any caller that previously read `comparison_ready` still works without code change.

5. The HTML pill at `review_ui.py:1473` is re-keyed off `readiness_state`. Rendered output for the three states includes `class="readiness ready"`, `class="readiness needs-transcripts"`, and `class="readiness thin"` respectively. The HTML page source contains all three class strings (so `assertIn('readiness needs-transcripts', html)`-style tests can pin them).

6. CSS: a new rule `.readiness.needs-transcripts { ... }` exists alongside the existing `.readiness.ready` and `.readiness.thin` rules. Pick a hue distinct from both — amber/yellow (e.g. `#fbbf24`) is a natural fit for "almost ready". If amber is too close to the existing `.readiness.thin` color, retune `.readiness.thin` to a redder hue so all three are visually separable.

7. A muted "${bucket.transcript_count}/${bucket.video_count} transcripts" sub-line is rendered in the bucket card next to the existing video-count pill (or just below it). Renders `0/0 transcripts` for an empty bucket (no crash). The HTML page source contains the literal template fragment `${bucket.transcript_count}/${bucket.video_count}` so a test can pin its presence in the JS.

8. `UI_REVISION` is bumped. The new value contains the substrings `channel-overview`, `discovery`, and `comparison-readiness`. (The first two keep the existing `test_ui_revision_advances_for_*` assertions green; the third lets the new tests in this slice pin against it.)

9. New tests cover all three readiness states. Acceptable shapes: three `_build_topic_inventory` fixtures (one per state) asserting `readiness_state`, `readiness_label`, `transcript_count`, `processed_count`, and `comparison_ready`. Plus an HTML test asserting all three CSS class strings + the transcript-coverage template fragment + the new `UI_REVISION` substring all appear in the rendered page.

10. Verify gate (`.ralph/verify.sh`) stays green throughout.

## Out of scope

- Real-LLM smoke for this slice (no LLM call site changes).
- Adding a "fetch transcripts" button / flow in the GUI. The `needs_transcripts` state surfaces the gap; actually fetching transcripts is a separate concern (Phase B / transcript fetching) and out of scope here.
- Surfacing `processed_count` visually in the bucket card. It's added to the bucket dict for future use (Phase B/C will need it), but the HTML in this slice only renders `transcript_count`. Don't add a `processed_count` UI tile — that's premature.
- Touching the comparison-group review pane (the bottom-of-page panel). Priority 4 is about the per-subtopic readiness pill in the Topic Inventory; the comparison-group review pane is a separate surface.
- Changing `MIN_NEW_SUBTOPIC_CLUSTER_SIZE` (= 5). The threshold stays where it is.

## Agent notes

- `_build_topic_inventory` is at `review_ui.py:2198`. The existing query LEFT JOINs `video_subtopics` and `videos`. Add two more LEFT JOINs: `video_transcripts` (filtered to `transcript_status = 'available'` in the JOIN ON clause, not the WHERE — otherwise videos without transcripts get filtered out entirely) and `processed_videos` (filtered to `processing_status = 'processed'` similarly). Then aggregate per subtopic in Python, or rewrite as a `GROUP BY subtopics.id` with `COUNT(DISTINCT video_transcripts.video_id)` and `COUNT(DISTINCT processed_videos.video_id)` — either is acceptable.
- Watch the existing per-row stream pattern: the current loop builds `bucket["videos"]` from per-row `youtube_video_id` + `title`. If you add the JOINs, the same video may appear once per Cartesian combination; use `COUNT(DISTINCT ...)` to dedupe, or carry `transcript_available` / `processed_ok` per row and reduce in Python over a `seen_video_ids` set per bucket.
- The existing readiness branch at `review_ui.py:2252–2259` is what you're replacing. Keep the `MIN_NEW_SUBTOPIC_CLUSTER_SIZE - video_count` "Needs N more video(s)..." computation for the `too_few` branch — it's user-facing copy that's already shipped.
- HTML pill site is `review_ui.py:1473`. The rendering JS function is `topicInventoryHtml(inventory)` starting at line 1457. Replace the ternary `bucket.comparison_ready ? 'ready' : 'thin'` with a state-keyed lookup like `{too_few: 'thin', needs_transcripts: 'needs-transcripts', ready: 'ready'}[bucket.readiness_state]` (or an equivalent inline expression). Default to `'thin'` if `readiness_state` is unexpectedly missing — defensive but cheap.
- CSS rules for `.readiness.*` are at `review_ui.py:617–629`. Add the new rule between the two existing ones for grep-ability.
- `UI_REVISION` is at the top of `review_ui.py` (search for the constant). Current value: `2026-05-08.4-run-history-advanced-channel-overview-discovery-panel`. A natural new value: `2026-05-08.5-comparison-readiness-channel-overview-discovery-panel`.
- The `_build_topic_inventory` tests are in `test_review_ui.py` (or a similar file — confirm via grep). If no test file currently exists for it, drop new tests into the file most adjacent to the existing topic-inventory test surface; create `test_review_ui.py` if needed.
- HITL_PAUSE if you discover that `video_transcripts` or `processed_videos` schemas have changed since the spec was written, or if a video can have multiple `transcript_status = 'available'` rows (the schema says PRIMARY KEY video_id so it can't, but verify before relying on that).
- COMPLETE on the third iteration after the polish pass: confirm `comparison_ready` boolean still flips correctly, JS sub-line doesn't crash for empty buckets, all three CSS classes ship, `UI_REVISION` carries all three substrings.
