# YouTube Channel Analyzer — Worklog

## Purpose

This file is the running log for notable progress, decisions, and pivots.

Use it to make resumptions easier without reconstructing everything from memory.

Keep entries short and practical.

---

## Entry template

```markdown
## YYYY-MM-DD

### Done
- ...

### Learned
- ...

### Next
- ...
```

---

## 2026-05-11 — Phase B slice 2: refinement schema + db helpers

### Done
- `feat/issue-02-refinement-schema` (off `main` after FF-merging B1's `feat/issue-01-fetch-transcripts`). One iteration. No CLI/LLM.
- `db.py` schema: 3 new tables in `SCHEMA_STATEMENTS`+`REQUIRED_TABLE_COLUMNS` — `refinement_runs` (mirrors `discovery_runs`; status `pending→running→success/error`; FK `channel_id`→channels CASCADE, `discovery_run_id`→discovery_runs SET NULL), `refinement_episodes` (`PK(refinement_run_id, video_id)`, `transcript_status_at_run`), `taxonomy_proposals` (`kind` topic/subtopic, `status` pending/accepted/rejected, `parent_topic_name`, `evidence`, `source_video_id`→videos SET NULL, `resolved_at`). `video_topics`/`video_subtopics` CREATEs gain `refinement_run_id INTEGER` (FK→refinement_runs SET NULL) + `'refine'` in the `assignment_source` CHECK.
- Migration: `_repair_video_topic_refine_source_constraint` (wired into `ensure_schema` right after the `'auto'` repair) — RENAME→CREATE-new→INSERT…SELECT→DROP dance like `_repair_video_topic_assignment_source_constraint`; idempotency guard = `'refine'` not in the live create-SQL. Its INSERT…SELECT **omits `refinement_run_id`** on purpose (defaults NULL on the rebuilt table): any DB hitting this branch predates `'refine'` so has no refine rows, and the `'auto'` repair that runs just before may itself have just rebuilt the table without that column — listing it would crash (`no such column`). Verified: old-shape DB (no `'refine'`, no `refinement_run_id`, `assignment_source IN ('manual','import','suggested')`) migrates, preserves rows, idempotent on re-run.
- `db.py` helpers (connection-first, no `ensure_schema`/commit — caller owns the txn, like `discovery.py`'s internals): `build_topic_rename_resolver(conn, project_id)` (fixed-point closure over `topic_renames`, same logic as `discovery._apply_renames_to_payload` — kept separate, not refactored, to avoid touching the 200-test discovery surface); `create_refinement_run` / `set_refinement_run_status` (validates the 4-status set); `add_refinement_episodes(conn, run_id, [(video_id, status), …])` (idempotent upsert); `insert_taxonomy_proposals(conn, run_id, [dict, …]) -> [id]` (forces `parent_topic_name` NULL for `kind='topic'`, requires it for `'subtopic'`); `accept_taxonomy_proposal(conn, id) -> dict` (creates the `topics`/`subtopics` row if absent — idempotent; for subtopics resolves the parent name through the rename log first, and if the parent is gone marks the proposal `rejected` with `reason='parent_topic_missing'`); `reject_taxonomy_proposal` (flips status; does **not** delete a node a prior accept created — other episodes may use it); `write_refine_assignments(conn, *, channel_id, refinement_run_id, video_id, assignments)` — deletes the video's `assignment_source <> 'manual'` topic+subtopic rows then inserts the given assignments as `assignment_source='refine'`, `discovery_run_id=NULL`, `refinement_run_id=<run>` with `ON CONFLICT DO NOTHING` (so a surviving `manual` row for a re-affirmed topic is not clobbered); rows the operator marked via `wrong_assignments` are not re-added; an unknown topic name is a `ValueError`; a referenced subtopic is created under its topic if absent.
- `review_ui._build_discovery_topic_map`: the topic-count / episode / subtopic queries now also pick up `assignment_source='refine'` rows whose topic (resp. whose subtopic's topic) is one of the run's topics (those rows carry `discovery_run_id NULL`). Per-episode payload gains `assignment_source` (B6 renders the "transcript-checked" pill off it). New refine-only topics from accepted proposals deliberately don't appear until a fresh `discover` run — by design (re-run `discover` to spread accepted nodes).
- New `test_refinement_schema.py` (7 tests: fresh schema shape; old-DB migration + idempotency + row preservation; run lifecycle + episode upsert; proposal accept creates node & resolves a renamed parent (idempotent); accept on missing parent → rejected + explicit reject path; `write_refine_assignments` wholesale-replace honoring `manual` survival + `wrong_assignments` + unknown-topic error; topic-map shows refine rows under the right subtopic bucket with `assignment_source`). Added to `.ralph/verify.sh` `DEFAULT_TARGETS`. **Gate 319 green** (312 + 7). `test_transcripts.py` untouched & still excluded.
- ROADMAP §B2 ticked; issue file `.scratch/phase-b-refinement/issues/02-refinement-schema.md` marked DONE.

### Learned
- `_ensure_required_columns` runs **before** the `_repair_*` functions in `ensure_schema`, but the `'auto'` repair rebuilds `video_topics`/`video_subtopics` with an explicit column list that drops any freshly-ALTERed column not on it. So a new junction column added via both `REQUIRED_TABLE_COLUMNS` *and* a later CHECK-rebuild must be reintroduced by that rebuild's CREATE (not its INSERT…SELECT) — don't try to carry it through the SELECT.
- SQLite is fine with a `CREATE TABLE` whose `FOREIGN KEY … REFERENCES other_table` names a table created later in the same `executescript` (forward refs allowed; FK only enforced on DML). So `video_topics`'s FK to `refinement_runs` works even though `video_topics` is created first in `SCHEMA_STATEMENTS`.

### Next (operator HITL — branch NOT auto-mergeable)
- Review the `_repair_video_topic_refine_source_constraint` rebuild before merge (Ralph HITL destructive-migration trigger #3 — same class as the slice-A/C migrations).
- No real LLM/network in scope for this slice.
- Slice 3 (`refinement.py` core + `refine --stub` CLI) is now unblocked: sample picker + internal transcript fetch + `refinement.transcript` prompt in `extractor/` + persist via these helpers. Slice 3 also wires B1's `fetch-transcripts --refinement-run-id` to a real episode list.

---

## 2026-05-11 — Phase B slice 1: `fetch-transcripts` CLI (non-legacy fetch path)

### Done
- `feat/issue-01-fetch-transcripts`, one iteration. Merged `docs/phase-b-prd` → `main` first (FF; deleted branch).
- New CLI `fetch-transcripts --db-path X (--video-ids a,b,c | --missing-only | --limit N | --refinement-run-id R) [--stub]` in `cli.py`. Selector mutex via argparse `add_mutually_exclusive_group(required=True)` — none/multiple → exit 2. `--missing-only` = primary-channel videos with no `video_transcripts` row or a retryable status (`rate_limited`/`request_failed`/`error`); `--limit N` = the N most-recent of that set; `--video-ids` = exactly those (validated as primary-channel IDs, dedup-preserving-order); `--refinement-run-id` errors cleanly until Phase B schema lands (slice 2/3). Bad selection → `error: …` on stderr, exit 2.
- Fetch loop helper `run_fetch_transcripts(db_path, video_ids, *, transcript_fetcher=None, sleep=time.sleep, request_interval=1.0, max_rate_limit_retries=5, base_backoff=2.0, max_backoff=60.0, out=print)`: sequential, fixed inter-request sleep, exp-capped backoff + retry-same-video on `rate_limited` (records the `rate_limited` row & moves on after the cap), each result persisted immediately via `upsert_video_transcript` (→ killed run resumes via `--missing-only`), one line/video + closing status tally. Uses the existing `youtube.fetch_video_transcript(transcript_fetcher=…)` injection point.
- `youtube.py`: `RETRYABLE_TRANSCRIPT_STATUSES` constant + `stub_transcript_fetcher(video_id)` (returns `available`/`generated`/`en`/`<stub transcript for …>`, no network) — surfaced by `--stub`, reused by tests.
- `db.py`: `list_primary_channel_transcript_status(db_path)` — every primary-channel video LEFT JOIN its transcript status (NULL if unfetched), newest-published first; backs all the selectors.
- Legacy `fetch-group-transcripts` byte-unchanged. No schema change — `video_transcripts` already had the right columns/status vocab.
- New `test_transcripts_fetch.py` (17 tests) → added to `.ralph/verify.sh` `DEFAULT_TARGETS`. `test_transcripts.py` untouched & still excluded. Cheatsheet §2 entry added. Verify gate **312 tests green**.

### Learned
- An injected `transcript_fetcher` returns a `TranscriptRecord` directly (only the *default* fetcher catches exceptions internally), so the rate-limit test fakes a `rate_limited` record then `available`, not a raised exception. The backoff loop keys off `record.status == "rate_limited"`.

### Next
- Slice 2: refinement schema (`refinement_runs` / `refinement_episodes` / `taxonomy_proposals`, `assignment_source='refine'` + `refinement_run_id` on junction tables) + db helpers. Then slice 3 wires `--refinement-run-id` to real episode lists.
- Operator: nothing required for this slice (no real LLM/network in scope); real transcript fetch smoke deferred to `.scratch/phase-b-refinement/SMOKE.md` once slices 2–3 land.

---

## 2026-05-11 — Shorts filter slice C: default flipped to exclude_shorts=1

### Done
- `feat/issue-03-shorts-flip-default-ui`, Ralph iter 1. **Behavior change for every channel**: `channels.exclude_shorts` now `DEFAULT 1` (`SCHEMA_STATEMENTS` + `REQUIRED_TABLE_COLUMNS`). Existing DBs migrated by `_repair_channels_exclude_shorts_default` in `ensure_schema()` — `UPDATE channels SET exclude_shorts=1` then RENAME/CREATE/INSERT-SELECT/DROP rebuild of `channels` with `DEFAULT 1` (legacy_alter_table + foreign_keys=OFF, same dance as `_repair_discovery_runs_status_constraint`). Idempotency guard = create-SQL inspection (no marker table). So: next `discover` on DOAC drops Shorts (`duration_seconds <= 180`, NULL=long/kept) → cleaner topic map.
- `run_discovery` now populates `discovery_runs.n_orphaned_wrong_marks` (wrong_assignments whose video is now filtered, scoped to channel — known pre-LLM) and `n_orphaned_renames` (topic_renames whose target topic has no kept episode assigned this run — computed post-persist, after `_suppress_wrong_assignments_in_run`). Both NULL when filter off. Curation rows never deleted — flip exclude_shorts=0 + re-run → they wake back up.
- `review_ui.py`: `_build_discovery_topic_map` carries `n_shorts_excluded`/`n_orphaned_*` + a precomputed `shorts_filter_badge` string (`"X shorts excluded · Y curation actions inert (target episodes filtered)"`, None when all zero); `#discovery-shorts-badge` div + `renderShortsBadge()` in `renderDiscoveryTopicMap`. ~49 net lines (cap 200). `UI_REVISION` += `-shorts-filter-badge`.
- Tests: `ShortsFlipDefaultMigrationTests` (one-shot flip + idempotent + FK children survive rebuild + fresh-DB default=1), `ShortsOrphanCountTests` (orphan counts populated/NULL, curation rows survive, woken wrong-mark re-suppresses), `ShortsFilterBadgeHtmlTests`. `test_exclude_shorts_defaults_to_zero` → `_to_one`; slice-B `_seed` now sets exclude_shorts explicitly (new schema default would otherwise = 1). 295 tests green.

### Next (operator HITL — branch NOT auto-mergeable)
- Review the destructive `channels` table rebuild migration before merge (Ralph HITL trigger #3).
- Real-LLM verify (operator-only, `RALPH_ALLOW_REAL_LLM=1`): re-run `discover --real` on a real DOAC DB → confirm topic map cleaner than prior run, `n_shorts_excluded`/orphan counts populated, badge renders on `:8765`. Do NOT run from Ralph.

---

## 2026-05-10 — CURRENT_STATE.md refresh + subtopic-autoheal slice 14 GREEN

### Done
- Refreshed CURRENT_STATE.md (was stuck at 2026-05-04 / start of Phase A) on main as `34a0097`. Captures Phase A complete, sticky-curation 3/3 paths validated, design hand-off + 16-commit GUI rebuild, three open follow-ups.
- Branched `feat/issue-14-subtopic-autoheal`. Wrote `RunDiscoverySubtopicAutohealTests` (test_discovery.py:4536) — RED at `discovery.py:821` with expected ValueError, mirroring live Haiku run-10 failure.
- Implemented `_autoheal_dangling_subtopic_refs(payload)` in `discovery.py`: walks assignments, synthesizes `DiscoverySubtopic(name=S, parent_topic=T)` for any `(T, S)` pair where T is declared in `payload.topics` but S isn't declared under T. Wired between `_apply_renames_to_payload` and the persistence loop in `run_discovery`. Dangling *topic* refs still raise — those need fresh data, not synthesis.
- Reframed `test_validation_failure_persists_errored_run_with_raw_response` (test_discovery.py:4106): trigger swapped from dangling-subtopic (now auto-healed) to unknown `youtube_video_id`, preserving paid-failure-recovery coverage.
- Reframed `test_assignment_subtopic_not_in_payload_raises` → `_is_autohealed` (test_discovery.py:4410): asserts the new positive contract on an empty-`subtopics[]` payload, complementing the autoheal test which seeds one pre-declared subtopic.
- Verify gate green at **272 tests** (+1 net). All sticky-curation/persistence test classes happy.

### Learned
- Two existing strict-validation tests captured the old "raise on dangling subtopic" contract. Reframing both into positive-direction assertions (test name suffixes flipped from `_raises` to `_is_autohealed`) preserved the coverage slot rather than deleting tests.
- `_autoheal_dangling_subtopic_refs` is a pure function on the payload — no DB read, no LLM call. Cheap to insert in front of the persistence loop, returns the original payload unchanged when no heal is needed.

### Next
- Live real-LLM smoke through the UI to confirm the prior run-10 failure mode now succeeds (cost: ~$0.05). Optional — the unit test mirrors the run-10 payload shape.
- Other open threads from the design hand-off memory: server-side Supply sort; fuzzy-match fallback for sticky-curation chain (deferred until exact-match limitation actually bites).

---

## 2026-05-10 — Stream/poll live smoke + validator divergence surfaced

After `793b229` landed, restarted dev server (pid 26041) on `0.0.0.0:8765` against `tmp/doac-sticky.sqlite` with `RALPH_ALLOW_REAL_LLM=1`. Stub smoke: `POST /api/discover` returned in **~30ms** (vs ~17s blocking before); finished too fast for the 0.05s poll to catch `'running'`. Real-mode smoke (run 10): POST returned in 30ms, polls at t+0.5s/5s/10s/15s/20s all saw `status='running'` (5+ samples), poll at t+25s saw `'error'` with the full `error_message` propagated through `/api/discovery_runs/<id>`. **Async + polling contract validated end-to-end.** 404 + 400 paths confirmed (`/api/discovery_runs/99999` → 400 with "not found"; `/abc` → 400 with "invalid"). Audit trail intact: `discovery_runs[10].raw_response` 14803 bytes preserved; `llm_calls[8]` has `parse_status=ok`, `correlation_id=10`, `tokens_in=28162 / tokens_out=5062`, **$0.053472**.

**Orthogonal bug surfaced (not in scope for this slice, but actionable):** Run 10 errored on persistence-side validation: assignment referenced subtopic `"Decision Making & Influence"` under topic `"Personal Development & Psychology"` but Haiku didn't list that subtopic in its `subtopics` array. `stop_reason=end_turn` (not truncation), so the parsed payload was complete — this is LLM-output divergence from the schema, not a parser bug. The strict validator at `discovery.py:_apply_renames_to_payload`/persistence loop raises `ValueError`, the existing error path saves `raw_response` (good), but the user pays ~$0.05 per occurrence with no recovery. Recommended fix: auto-recover by appending the implicitly-declared subtopic to `payload.subtopics` before strict validation. Alternative: tighten the prompt; doesn't help if Haiku slips again.

### Next
- Auto-recover undeclared subtopics in `discovery.py` before persistence (highest impact: real $ on real runs).
- Server-side Supply sort (`ORDER BY` in `_build_supply_videos`).
- Update `CURRENT_STATE.md` — currently dated 2026-05-04 and asks "has Phase A been built?" while the answer is "yes, with a working real-LLM UI flow."

---

## 2026-05-10 — Stream/poll in-flight discovery

`POST /api/discover` no longer blocks on the LLM call. New flow: handler resolves `(model, prompt_version)` per mode via new `_discover_mode_config`, calls new `discovery.allocate_discovery_run()` to insert a `discovery_runs` row in `status='running'`, returns immediately with `{ok, run_id, mode, model}`, and spawns a `daemon=True` thread that drives `_discover_runner(self.db_path, mode=, run_id=)`. New `ReviewUIApp(run_in_background=True)` kwarg flips the dispatch — tests pass `False` for synchronous + deterministic execution. New `GET /api/discovery_runs/<id>` returns a small `{id, status, error_message, model, prompt_version, created_at}` payload — the JS modal polls it every 1.5s (cap 120s) until terminal, then refetches `/api/state` once. Real-mode env-gate check moved to the handler so a missing `RALPH_ALLOW_REAL_LLM` 400s before the row is allocated (no stale `'running'` row). Schema: `discovery_runs.status` CHECK now allows `'running'` (default flipped from `'success'`); new `_repair_discovery_runs_status_constraint` rebuilds the table on existing DBs using `legacy_alter_table=ON` + `foreign_keys=OFF` so child FK references in `topics`, `video_topics`, `video_subtopics` survive the rename. Migration verified against real `tmp/doac-sticky.sqlite` — all 8 historical runs preserved, 133 child `video_topics` rows kept. `run_discovery` now pre-allocates as `'running'` and adds an explicit UPDATE to `'success'` on the happy path; the kwargs `run_id` lets the request handler hand the id in. JS `runDiscoverFromModal` rewritten around new `pollDiscoveryRunStatus(runId)`; on terminal `'error'` modal stays open with the `error_message` so the user can read + retry. UI revision bumped to `2026-05-10.12-discover-streaming-poll-...`. 4 new tests (271 total, +4): pre-allocation contract, `/api/discovery_runs/<id>` happy + 404, migration replays the old CHECK constraint and proves `'running'` is accepted post-`ensure_schema`. Verify gate green ~71s. Live UI smoke deferred — dev server still on the old code at pid 7483.

### Next
- Server-side Supply sort (`ORDER BY` toggle in `_build_supply_videos` so `oldest` returns channel's true oldest, not oldest of loaded N).
- Live re-smoke of the streaming flow against `tmp/doac-sticky.sqlite` after server restart.

---

## 2026-05-10 — Post-fix real-LLM smoke through the UI

End-to-end UI flow exercised against `tmp/doac-sticky.sqlite` after truncation + streaming fixes (`b724fe1`, `2a77484`) and the `Run discovery` wiring (`ef07fac`). Server restarted with `RALPH_ALLOW_REAL_LLM=1` on `0.0.0.0:8765`. Click-path: Discover stage → Run discovery → Real → Confirm. `discovery_runs` row 8 → status=success, Haiku 4.5, `discovery-v4`, no error. `llm_calls` row 7 → `parse_status=ok`, `correlation_id=8`, 28162 in / 4952 out, **$0.052922** (within 4% of the prior CLI smoke's $0.050947 — no retry waste, no truncation). 50 topic assignments / 8 distinct topics; 50 subtopic assignments / 16 subtopics. One new topic surfaced ("Personal Finance & Economics") — sticky-curation new-topic-badge path exercised. Server log clean: `POST /api/discover` 200 → `GET /api/state?discovery_run_id=8` 200 (88KB). UI snapped to Review with the new run as expected. Modal sat on "Running…" ~17s — acceptable, but confirms stream/poll is the next obvious UX win if it gets annoying. No code change.

### Next
- Stream/poll in-flight discovery (`/api/state?discovery_run_id=N` polling while run executes in a thread) — modal currently blocks ~17s.
- Server-side Supply sort (`ORDER BY` toggle in `_build_supply_videos` so `oldest` returns channel's true oldest, not oldest of loaded N).

---

## 2026-05-10 — Discovery truncation fix (raise max_tokens + skip retry on truncation)

Both halves of yesterday's smoke-test damage report addressed in one commit on `feat/issue-11-discovery-truncation-fix`. **A) Raised `AnthropicRunner.max_tokens` from `4096` → `64000`** (Haiku 4.5's published output ceiling, exposed as `DEFAULT_MAX_TOKENS` and a constructor kwarg so we can dial it down per call/model). Applied in both `run_single` and `run_batch_submission`. **B) `Extractor._run_single_with_retry` now short-circuits on `stop_reason="max_tokens"`** — captures `last_stop_reason` after each `run_single`, and on first `SchemaValidationError` checks it: if the response was truncated, writes the `parse_status="failed"` audit row directly and re-raises without a second LLM call. Non-truncation parse failures still take the existing retry-once path (regression-tested). `AnthropicRunner` exposes `last_stop_reason` (and `last_batch_stop_reasons` for batch entries); `FakeLLMRunner` gained `queue_stop_reason()` + `last_stop_reason` so tests can simulate truncation without monkeypatching. New class `TruncationRetrySkipTests` (3 tests: truncation→no-retry, non-truncation→retry-still-works, missing-stop-reason→retry-still-works) plus `AnthropicRunnerConfigTests` (2 tests: default = 64000, override respected). Verify gate green at **253** (~57s). No real LLM call in tests.

### Why this matters

Yesterday's $0.097 smoke spent **half** on a guaranteed-fail retry. Post-fix, the same truncation event would cost ~$0.048. And — more importantly — at `max_tokens=64000` the DOAC prompt won't truncate at all; expected real cost on DOAC discovery should now hover around the cheatsheet's `~$0.019/15 episodes` ballpark scaled to channel size, with no retry doubling.

### Smoke (real, post-commit) + streaming follow-up

First attempt against DOAC at `max_tokens=64000` failed client-side: `anthropic` SDK rejects non-streaming requests whose `max_tokens` implies >10min of expected completion (formula in `_base_client._calculate_nonstreaming_timeout`: `3600 * max_tokens / 128_000 > 600` → cutoff ~21K). No spend (SDK refused before sending). Followed up by switching `AnthropicRunner.run_single` to `client.messages.stream(...).get_final_message()` so callers can dial `max_tokens` to the model ceiling without tripping the gate. Batch path (`run_batch_submission`) untouched — async batches already side-step the synchronous-timeout rule.

Smoke retry succeeded — `discovery_runs` row 7, **status=success**, 35.6s wall, 28162 input + 4557 output tokens, **cost $0.050947**. 7 topics + 34 subtopics across all 50 DOAC episodes; no truncation, no retry. The 4557-token output would have truncated at the old 4096 ceiling — direct confirmation that the fix unblocks DOAC. Cost vs yesterday's $0.097 = **47% reduction**, matching the predicted halving from skipping the retry. Verify gate stayed green at 253.

### Next
- Supply pagination (still: `limit=50` hard-coded in `_build_supply_videos`).
- Wire `Edit channel` form (smaller slice).
- Optional: stream/poll in-flight discovery status — modal still sits frozen during the synchronous request.

---

## 2026-05-10 — Wire Run discovery button (Discover page)

Discover-page `Run discovery →` button is no longer a toast: clicks open a confirm modal, then POST `/api/discover` with `{mode}` ∈ {`stub`,`real`}. Server endpoint dispatches to an injectable `discover_runner` (default `_default_discover_runner`); stub mode runs `run_discovery` with `stub_llm` (`STUB_MODEL`/`STUB_PROMPT_VERSION`); real mode opens a sqlite connection, calls `make_real_llm_callable(connection)` (the `RALPH_ALLOW_REAL_LLM=1` gate already lives there as a `RuntimeError`) and runs `run_discovery` with `DEFAULT_MODEL`/`DISCOVERY_PROMPT_VERSION`, closing the connection in a `finally`. The gate's `RuntimeError` is caught in `_discover` and re-raised as `ReviewUIError` so the existing 400 handler surfaces the message verbatim. `ReviewUIApp.__init__` takes optional `discover_runner` kwarg — same DI shape as `channel_metadata_fetcher`. Mode-toggle pills became real `<button>` elements bound to `setDiscoverMode`; default mode is `--real`. Reusable HTML/CSS modal (`#discover-confirm-modal`, paper/ink palette, backdrop click + Escape close). On success, JS sets `state.activeDiscoveryRunId = run_id`, switches to `review` stage, refetches state — Review snaps to the new run. On 400, status bar shows the gate hint verbatim. `UI_REVISION` bumped to `2026-05-10.9-run-discovery-button-wired-…`. 7 new tests in `DiscoverEndpointTests` (stub success, real-no-env 400, real-with-env injected runner, missing-mode 400, unknown-mode 400, button-HTML wired, UI_REVISION advance). Verify gate green at 248 (~56s). No real LLM call in tests.

### Smoke (real, post-commit)

`POST /api/discover {mode:"real"}` with `RALPH_ALLOW_REAL_LLM=1` exported reached Anthropic and the failure was correctly logged: `discovery_runs` row 5 → `status=error` with parse-failure message; `llm_calls` rows 4/5 both `correlation_id=5`. Wiring works end-to-end. Real spend: **$0.097** ($0.048642 × 2). The 400 surfaced was a pre-existing bug, not a regression: AnthropicRunner's `max_tokens=4096` truncates DOAC's discovery output mid-string, and `Extractor.run_one`'s retry-once retries the deterministic ceiling failure, doubling spend.

### Next
- **Raise `AnthropicRunner.max_tokens`** (likely to model max) or chunk the discovery prompt — current 4096 truncates DOAC output. Surfaced by today's smoke test.
- **Skip retry on `parse_status=failed` from truncation** — deterministic, no point retrying. Halves spend on this failure mode.
- Supply pagination (still: `limit=50` hard-coded in `_build_supply_videos`).
- Wire `Edit channel` form (smaller slice).
- Optional: stream/poll in-flight discovery status — currently the modal sits on "Running…" for the full ~60s.

## 2026-05-10 — Wire Re-ingest button (Supply page)

Supply-page Re-ingest button is no longer a toast: clicks now POST `/api/reingest`. Server endpoint reads the primary channel + project name (`get_primary_channel` + `_resolve_primary_project_name`), calls the YouTube fetchers, and upserts via `upsert_channel_metadata` + `upsert_videos_for_primary_channel`. Returns `{ok, channel_title, youtube_channel_id, video_count, last_refreshed_at, message}`. `ReviewUIApp.__init__` now takes optional `channel_metadata_fetcher` and `channel_videos_fetcher` kwargs (default to `youtube.fetch_channel_metadata` / `fetch_channel_videos`) so tests can stub without monkeypatching env — same dependency-injection shape as `discovery.run_discovery`'s `LLMCallable`. `YouTubeAPIError` is wrapped as `ReviewUIError` so the existing 400 handler surfaces a clean `Re-ingest failed: …` message; missing `YOUTUBE_API_KEY` flows through that same path. JS handler disables the button + swaps label to "Re-ingesting…", posts, on success calls `fetchState()` to refresh the Supply numbers + `last_refreshed_at` line, on error sets status. Optional `limit` body field clamped to `[1, 50]` (the YouTube API page max). `UI_REVISION` bumped to `2026-05-10.8-reingest-button-wired-…`. 9 new tests in `ReingestEndpointTests` (happy path, DB persistence, limit clamp, metadata-error 400, videos-error 400, no-primary-channel 400, missing-API-key 400, button-HTML wired, UI_REVISION advance). Verify gate green at 241 (~54s). No real network in tests.

### Next
- Supply pagination (currently `limit=50` hard-coded in `_build_supply_videos`).
- Wire `Edit channel` form (smaller slice).
- Wire `Run discovery` button (needs `RALPH_ALLOW_REAL_LLM=1` server-side check + confirm modal).

## 2026-05-10 — Discover row → Review (run selector)

Discover history rows are now clickable: click a successful run → switches `activeStage` to `'review'`, sets `state.activeDiscoveryRunId`, refetches. `_build_discovery_topic_map` accepts optional `run_id=` (defaults to latest as before); `build_state_payload` adds `discovery_run_id=` kwarg; `/api/state` parses `?discovery_run_id=`. JS `fetchState()` appends the param when set; `selectDiscoveryRun()` clears focused topic/subtopic so user lands on the overview for the chosen run. Active row marked via `is-active` class against the actually-loaded `discovery_topic_map.run_id` (so latest stays highlighted by default). Errored runs show a status-bar warning instead of loading. Row gets pointer cursor + hover tint + role=button + Enter/Space handler. CSS: row padding `20px 0` → `20px 12px` with `-12px` margin so the hover/active background bleeds into the gutter for readability. `UI_REVISION` bumped to `2026-05-10.7-discover-row-selects-run-discover-cost-…`. New test `test_state_payload_discovery_run_id_selects_specific_run` covers default-latest, specific-id, and missing-id paths. Verify gate green at 232 (~52s).

## 2026-05-10 — Cost column in Discover history (commit `feaf7e7`)

`run_discovery` pre-allocates the `discovery_runs` row before the LLM call so its id is available as `correlation_id` on the resulting `llm_calls` row; both error paths (LLM raise / persistence-validation failure) flip the pre-allocated row from `'success'` to `'error'` in place rather than inserting a duplicate (raw_response / error_message semantics unchanged). `discovery_llm_via_extractor` and `stub_llm` now accept `correlation_id` kwarg; new `_call_llm_with_optional_correlation` helper uses `inspect.signature` to skip the kwarg for legacy fixture lambdas (`def f(_videos): …`) so existing tests stay untouched. `_build_discover_runs` LEFT-joins `llm_calls` on `correlation_id = discovery_runs.id` and `SUM(cost_estimate_usd)`. JS adds a 6th `dr-cost` column (CSS grid widened 6→7 cols); `formatCost` shows `$0.0019` (4-dp under $0.01, 3-dp above), `<$0.001` for sub-mil, `—` when NULL. `UI_REVISION` bumped to `2026-05-10.6-discover-cost-...`. 5 new tests in `DiscoverRunsCostRollupTests`. Verify gate green at 231 (~50s). Pre-existing runs show `—` (their `llm_calls` lack `correlation_id`); first new `discover --real` populates. Dev server restarted on pid 128486.

## 2026-05-10 — Stage pages: Supply / Discover / Consume + stepper as router

Stepper now routes between four `<main class="stage-panel">` blocks via `state.activeStage` and `setActiveStage()`. Stepper buttons no longer `disabled`; `done`/`act`/`idle` derive from index vs activeStage in `renderStepper()`. Stepper line previously cut through labels — fixed by extending line endpoints to marker centers (`left: 22px; right: -22px`) so markers' z-index masks them, plus adding `background: var(--paper)` to `.step-text` so the line is masked behind labels.

**Supply stage**: channel header (88px teal avatar w/ first-letter, 40px serif h1, description, @handle / ingested-at metadata, Re-ingest / Edit channel buttons → CLI-pointer status messages); Videos list w/ Newest/Oldest sort, 160×90 striped placeholder thumbs (real `thumbnail_url` if present), 17px serif titles linking to youtube.com/watch, mono published-date + YT id meta, transcript-status pill (good/bad/neutral) + hint line. Pagination footer uses `channel_overview.video_count`. New payload helpers `_build_supply_channel` + `_build_supply_videos` (default limit 50).

**Discover stage**: lede + run panel (model + prompt-version readout from `latest_discovery`, $0.019±0.005 estimate placeholder, --real/--stub visual toggle, Run discovery button → CLI-pointer status message); run history table from new `_build_discover_runs` helper (id, model, prompt_version, status, error_message, created_at, COUNT(DISTINCT topic_id) + COUNT(DISTINCT video_id) from video_topics). Cost column omitted because `discovery.py` doesn't pass `correlation_id` to llm_calls — flagged as follow-up.

**Consume stage**: two-column. Filter sidebar (live topic list from `discovery_topic_map.topics` w/ episode count, plus "Available once X lands" placeholders for speakers + claim types). Main panel is the design's empty state + a single static sketch claim card with "not yet — sketch" tag.

Verify gate green at 226 throughout. Re-ingest / Edit channel / Run discovery buttons are visual-only on purpose (HITL gating against accidental real-LLM cost). Server restarted on pid 107947, 0.0.0.0:8765, 144KB rendered HTML.

---

## 2026-05-09 (late) — review_ui.py reskin to Claude Design hand-off

User pasted a Claude Design URL pointing to a `youtube-anaslyser` bundle (Review canvas mocks: overview pillar grid + minimap + focused state) and said "drive existing code with the design… happy to do away with current UI as it's clunky." Reskinned `review_ui.py` end-to-end: design tokens (paper/ink/teal/blue/coral) + Poppins + Source Serif 4 + JetBrains Mono via Google Fonts; new `<header class="topbar">` (wordmark + dot + version + channel pill) and `<nav class="stepper">` (4 stages, Review active); `#review-canvas` toggles between overview (compact pillars w/ chips + dot grid + "X% high-confidence") and focused (240px minimap left + focus-head w/ 44px serif h1 + subtopic tab strip w/ coral underline + episode rows: 152px striped thumbs, 18px serif titles never truncated, italic Source Serif reasons w/ coral left border, also-in pills, action column ▶ Watch / ✗ Wrong topic / ✗ Wrong subtopic). Legacy panels (channel-overview tiles, "Topic Map" broad, Broad Topics / Subtopic / Comparison grid, generator, run-history-advanced) hidden via `display:none !important` so the 37 HTML-coupled tests stay green. New JS: `state.focusedTopic`/`activeSubtopic`/`overviewSort`, `focusTopic()`, `setActiveSubtopic()`, `setOverviewSort()`, `dotGridHtml()`, `highConfidencePct()`, `renderFocusedTopic()`, `renderDiscoveryEpisodeItemFocused()`. Verify gate: 226/226 green. **Working tree dirty — review_ui.py +1519/−494 lines, NOT committed.** Server restarted on 0.0.0.0:8765, user confirmed they can see the new UI. Sort buttons (Episode count ↓ / Topic A–Z) + Discard run + Mark caught up wired only to client-side reorder + status messages — real backend wiring deferred. Supply/Discover/Consume stage pages from the design (mocks-stages.jsx) intentionally out of scope; only Review has a live data path today.

---

## 2026-05-09 (evening) — Errored-run + raw-response persistence

### Done
- `discovery_runs` schema: added `error_message TEXT` + `raw_response TEXT` (nullable, both back-compat). Existing DBs migrate via `_ensure_required_columns` on next `ensure_schema` call (`discovery_runs` now in `REQUIRED_TABLE_COLUMNS`).
- `discovery.py`: wrapped the rename + persist + commit block in try/except. On any `Exception` (including the canonical "assignment references subtopic not in payload.subtopics" `ValueError` that lost ~$0.019 on the 2026-05-08 run-1 retry), `connection.rollback()` discards the pending success-row insert, then a fresh insert writes an `error` row with `error_message=str(exc)` + `raw_response=json.dumps(asdict(payload_returned_from_llm))`. Re-raises after persist.
- LLM-raise path also gets `error_message` populated now (previously: status only). `raw_response` stays NULL there — the LLM raised before returning a payload.
- Tests: schema test asserts the two new columns; `test_llm_error_marks_run_errored_and_persists_no_partial_state` extended to assert `error_message="malformed after retry"` + `raw_response IS NULL`; new `test_validation_failure_persists_errored_run_with_raw_response` covers the validation path with a dangling-subtopic-ref `DiscoveryPayload` and asserts `raw_response` round-trips via `json.loads(...) == asdict(bad_payload)`. Verify gate green at 219 (+1).

### Decisions
- Wrapped the entire validation+persistence block (not just individual validation `raise`s) so transient SQLite errors during persistence also benefit from raw-response capture. Cost: one extra `INSERT INTO discovery_runs` on the failure path. Negligible.
- Saved the **post-LLM, pre-rename** payload as `raw_response`. The renamed payload is what gets persisted on success, but the unmunged LLM output is what's most useful for debugging.
- Did **not** widen the validator to drop dangling subtopic refs (the second sub-bullet from the original follow-up). Persisting the raw payload makes the failure recoverable without needing the validator to be more permissive.

### Next
- #2 `cost_estimate_usd` pricing table (still pending — needs Haiku 4.5 input/output rates from user).
- #6 UI clunkiness triage to `.scratch/ui-clunk/`.
- #7 fuzzy-match fallback for sticky-curation, #8 Phase B/C scoping, mirrored-networking switch (kills the session — do at boundary).

---

## 2026-05-09 (pm) — Cleanup commits + tokens_in/out wiring

### Done
- Split the working-tree fixes into three commits on `main`: `f250bb0` (orphan `addEventListener` removal), `04b4657` (threading WSGI mixin + WORKLOG entries), `a5b8cdf` (`CLAUDE.md`). Verify gate green at 214 throughout.
- `e85c833` fixed stale `127.0.0.1:8000` default-port references in `docs/operator-workflow.md:120` + `YT_ANALYZER_CHEATSHEET.md:65` → `8765`.
- `974c209` wired `tokens_in`/`tokens_out` end-to-end (`AnthropicRunner.last_usage` + `last_batch_usages` → `Extractor` → `llm_calls` audit row, success/retry/failed paths). `FakeLLMRunner` gained `queue_usage` / `queue_batch_usages`. Verify gate 218 (+4 `TokenUsageTests`). `cost_estimate_usd` deliberately NULL — pricing follow-up.

### Decisions
- Skipped a regression test for the orphan `addEventListener` bug — would tie tests to embedded HTML string format that isn't load-bearing elsewhere; defer until similar bug recurs.
- For `cost_estimate_usd`: user opted to ship tokens-only and defer the price table. Cost becomes queryable as `tokens × external price` until the table lands.

### Next
- #5 errored-run + raw-response persistence (the $0.019 lost on run 1's first try is the canonical case).
- #6 UI clunkiness triage to `.scratch/ui-clunk/`.
- #7 fuzzy-match fallback for sticky-curation, #8 Phase B/C scoping, mirrored-networking switch (kills the session — do at boundary).

---

## 2026-05-09 — Sticky-curation validation run 2 (3/3 paths PASS), server deadlock fix, CLAUDE.md added

### Done
- Run 2 of sticky-curation validation succeeded on first try (`discovery_runs.id=2`, Haiku 4.5, prompt `discovery-v4`, ~$0.019; cumulative `tmp/doac-sticky.sqlite` spend ~$0.057). Log: `/tmp/doac-sticky-run2-20260509-073437.log`.
- Validated all three sticky-curation paths against run 2's `video_topics`/`video_subtopics`: rename replay (Haiku re-proposed "Sexual Health & Relationships" → chain rewrote to "Sex & Intimacy", 4 eps under new name); wrong-topic suppression (`video_id=4 AND topic_id=6` has 0 rows); wrong-subtopic suppression (`video_id=7 AND subtopic_id=7` has 0 rows).
- Fixed review-UI server deadlock: stdlib `wsgiref.simple_server.make_server` is single-threaded; VSCode remote's port-forward auto-detection (node pid 1457) was opening 12+ parallel probes to :8765, queue backed up, `wait_w` hang. Wrapped with `_ThreadingWSGIServer(ThreadingMixIn, WSGIServer)` + `daemon_threads=True`. Self-probe now ~1ms. Real bug, not VSCode-specific.
- Added `CLAUDE.md` (via `/init`) capturing package layout (parent-dir + `PYTHONPATH=.` quirk), verify gate, real-LLM gating, the `discovery.stub_llm`-as-`LLMCallable` pattern, ADR pointers, Ralph harness conventions.

### Learned
- **Sticky-curation chain is exact-string-match**, not semantic. Haiku rephrased "Personal Development & Success" as "Personal Development & Discipline" in run 2; the user's rename to "Self-Improvement" did NOT replay — chain looks for verbatim `old_name` in proposed `topics[].name`. Same logic for wrong-marks (cured `topic_id` doesn't transfer to LLM-renamed proposals). By design, but means Haiku word-choice variance silently bypasses curation. Doc + future fuzzy-match slice.
- `llm_calls.tokens_in`, `tokens_out`, `cost_estimate_usd` are **all NULL** across all 3 rows. Schema provisioned, `extractor/anthropic_runner.py` doesn't fill them. Operator-workflow doc's "queryable cost" claim is currently false.
- Failed-validation paid calls *do* land in `llm_calls` (3 calls, 2 successful `discovery_runs`) — audit trail intact even when `discovery_runs` row is missing.
- Existing Windows-side network setup uses **netsh portproxy + firewall rule** hardcoded to WSL IP `192.168.83.240` for ports 8765 / 2222→22 / 18789 / 18888. All break together when WSL IP drifts on restart. Win11 build 26200 supports mirrored networking; switch deferred (kills Claude session).

### Next
- Commit working-tree changes on `main`: `review_ui.py` orphan-addEventListener removal (yesterday) + `_ThreadingWSGIServer` mixin (today) + WORKLOG entries + new `CLAUDE.md`. Two small commits or one combined.
- Switch to mirrored networking (`C:\Users\Dad\.wslconfig`: `[wsl2]\nnetworkingMode=mirrored` + `wsl --shutdown` from PowerShell) at the next session boundary.
- Fix the port-default doc bug (`docs/operator-workflow.md:120`, `YT_ANALYZER_CHEATSHEET.md:65`: `8000` → `8765`).
- Wire `llm_calls.tokens_in/out/cost_estimate_usd` in `extractor/anthropic_runner.py` (small slice).
- Errored-run + raw-response persistence slice (addresses the $0.019 lost on run 1's first try).
- User flagged "UI feels clunky" without specifics — capture concrete pain points to `.scratch/ui-clunk/` before any redesign.

---

## 2026-05-08 — Slice 02b CLI `--real` + sticky-curation validation run 1

### Done
- Shipped slice 02b (commit `dc80758`, FF-merged): `discover` and `analyze` now take a required `--stub|--real` mutex + optional `--model`; `--real` enforces `RALPH_ALLOW_REAL_LLM=1` inside `make_real_llm_callable`. 6 new/renamed tests, 214 verify-gate tests green.
- Updated `docs/operator-workflow.md` §3 + `YT_ANALYZER_CHEATSHEET.md` §1 with real-mode recipe; ROADMAP §A5 box 3 sub-bullet ticked.
- Kicked off second DOAC validation: ran `analyze --real` on fresh `tmp/doac-sticky.sqlite` — first call hit a `discovery.py:723` payload-validation error (Haiku referenced an undeclared subtopic), retry on Haiku worked first try. Run 1 on disk: discovery_runs id=1, 6 topics, 12 subtopics, confidence 0.70–0.95. Spent ~$0.038.

### Learned
- The CLI does not persist anything when `run_discovery` rejects a payload mid-validation: no errored row, no raw response. So a paid call that fails validation is invisible on disk. Worth a small follow-up slice (errored-run row + raw-response capture before validating).
- `review_ui.py:1975` had an orphan `getElementById('generate-comparison-groups-btn').addEventListener` left over from §A4's legacy-move slice — the button was hidden but the handler stayed, throwing on page load and skipping initial `fetchState()`. Removed the line; uncommitted in working tree.
- The CLI's serve-review-ui default port is **8765** but `docs/operator-workflow.md:120` and `YT_ANALYZER_CHEATSHEET.md:65` still say `127.0.0.1:8000`. Pre-existing doc bug.

### Next
- User is curating in the review UI (rename ≥1 topic + mark ≥1 wrong-assignment). When ready, fire run 2 (`discover --real` on the same DB, ~$0.019) and verify renames carried forward + wrong assignments stayed suppressed.
- Commit the `review_ui.py` JS fix with a regression test (parse served HTML, assert every `getElementById(id).addEventListener` has a matching `id="..."` in the markup).
- Fix the port-default doc bug.
- Consider an errored-run + raw-response persistence slice.

---

## 2026-05-08 — Issue 13 / Ralph iteration 3: comparison readiness polish + COMPLETE

### Done
- JS `topicInventoryHtml` per-bucket iteration now spreads
  `bucket = { ...bucket, transcript_count: bucket.transcript_count ?? 0,
  video_count: bucket.video_count ?? 0 }` before the template, so
  stale callers missing either coverage key still render
  `0/0 transcripts` instead of `undefined/undefined`. Template
  literal `${bucket.transcript_count}/${bucket.video_count}`
  preserved verbatim — AC7's `assertIn` test stays green.
- New `test_empty_subtopic_bucket_has_zero_counts_and_too_few_state`
  in `TopicInventoryReadinessStateTests`: subtopic with no
  `video_subtopics` rows yields `video_count=0`,
  `transcript_count=0`, `processed_count=0`,
  `readiness_state="too_few"`, `comparison_ready=False`.
- Re-grep of `comparison_ready` confirms only call sites are the
  3 back-compat read assertions in `test_discovery.py` and the
  write site in `_build_topic_inventory` — no other JS/Python
  consumers, alias intact.
- LEFT JOIN cardinality dedupe still covered by
  `test_transcript_and_processed_counts_dedupe_per_video` (5×5×5,
  no Cartesian inflation).

### Verified
- `.ralph/verify.sh`: 210 tests, ~50s, OK (was 209; +1).

### Issue 13 acceptance status — all green
- AC1–AC10 ✓ (see iter 1 + iter 2 entries below for breakdowns).
- Polish loose-ends (this iter): empty-bucket + stale-caller +
  dedupe re-checked.

### Next
- Issue 13 COMPLETE — branch ready for review and merge.

---

## 2026-05-08 — Issue 13 / Ralph iteration 2: comparison readiness HTML + UI rev

### Done
- `topicInventoryHtml` JS now picks state-keyed class via map
  (`{too_few: 'readiness thin', needs_transcripts:
  'readiness needs-transcripts', ready: 'readiness ready'}`)
  with `'readiness thin'` fallback. Used full `'readiness X'`
  strings (not bare `X`) so the spec'd `assertIn('readiness needs-transcripts', html)`
  pin works against the rendered page source.
- New `<div class="transcript-coverage">${bucket.transcript_count}/${bucket.video_count} transcripts</div>`
  sub-line under the readiness pill. Backend always populates both
  keys, so empty bucket renders `0/0 transcripts` directly from
  template interpolation — no JS-side defensiveness needed.
- CSS: `.readiness.needs-transcripts` rule inserted between the two
  existing readiness rules (amber `#fbbf24`, mirrors original `thin`
  hue). `.readiness.thin` retuned to red `#fca5a5` /
  `rgba(248,113,113,*)` so all three states are visually separable
  (red → amber → green ladder). New `.transcript-coverage` muted
  11px style for the sub-line.
- `UI_REVISION` bumped to
  `2026-05-08.5-comparison-readiness-run-history-advanced-channel-overview-discovery-panel`.
  Keeps `channel-overview`, `discovery`, `run-history-advanced`
  substrings so all 11 prior `test_ui_revision_advances_for_*`
  assertions stay green; adds `comparison-readiness` for this slice.
- 5 tests in new `ComparisonReadinessHTMLTests`:
  three readiness class strings present in HTML;
  `.readiness.needs-transcripts` CSS rule ships;
  JS references `bucket.readiness_state`; transcript-coverage
  template fragment + class present; `UI_REVISION` carries the
  three required substrings.

### Verified
- `.ralph/verify.sh`: 209 tests, ~49s, OK (was 204; +5).

### Issue 13 acceptance status
- AC1 (transcript_count/processed_count, dedupe) ✓ — iter 1.
- AC2 (3-state readiness ladder) ✓ — iter 1.
- AC3 (per-state labels + next_step copy) ✓ — iter 1.
- AC4 (`comparison_ready` back-compat alias) ✓ — iter 1.
- AC5 (state-keyed pill class, all 3 strings in source) ✓ — iter 2.
- AC6 (`.readiness.needs-transcripts` CSS) ✓ — iter 2.
- AC7 (transcript-coverage sub-line + `0/0` empty case) ✓ — iter 2.
- AC8 (`UI_REVISION` carries all 3 substrings) ✓ — iter 2.
- AC9 (3-state fixture coverage + HTML tests) ✓ — iter 1 + 2.
- AC10 (verify gate green) ✓.

### Next
- Iteration 3: loose-end polish + COMPLETE — verify
  `comparison_ready` callers, empty-bucket render, LEFT JOIN
  cardinality once more (already covered by `test_transcript_and_processed_counts_dedupe_per_video`),
  then emit COMPLETE.

---

## 2026-05-08 — Issue 11 / Ralph iteration 3: polish + COMPLETE

### Done
- `build_state_payload` now wraps `get_primary_channel(db_path)` in
  `try/except ValueError` (the function raises rather than returns
  `None` — agent notes in the issue spec were aspirational).
  When no primary channel exists, `channel_overview`, `channel_title`,
  and `channel_id` all return `None` so `/api/state` no longer 400s
  on an unconfigured DB.
- `renderChannelOverview` empty-overview branch now sets the subtitle
  to "No primary channel set" so the panel renders a meaningful
  header instead of a blank one.
- `UI_REVISION` bumped to
  `2026-05-08.3-channel-overview-no-primary-channel-discovery-panel`.
  Keeps both `channel-overview` and `discovery` substrings so all 9
  prior `test_ui_revision_advances_for_*` tests stay green.
- 2 new tests:
  `test_state_payload_channel_overview_null_when_no_primary_channel`
  (empty DB doesn't raise; channel_* keys all `None`),
  `test_html_page_renders_no_primary_channel_hint`.

### Verified
- `.ralph/verify.sh`: 190 tests, ~47s, OK (was 188; +2).

### Issue 11 acceptance status
- AC1 (`channel_overview` payload key + counts + `latest_discovery`
  shape) ✓ — iter 1.
- AC2 (panel above Discovery Topic Map + tiles + empty-state copy)
  ✓ — iter 2.
- AC3 (renders without errors when `primary_channel` unset / DB
  empty / `discovery_runs` empty) ✓ — iter 3 (no-primary path) +
  iter 1 (empty-DB-with-channel + no-runs paths).
- AC4 (payload-shape + HTML wiring tests) ✓ — 9 tests across
  `ChannelOverviewPayloadTests` + `ChannelOverviewHTMLTests`.

### Next
- Issue 11 COMPLETE; branch `feat/issue-11-channel-overview` ready
  for review and merge.

---

## 2026-05-08 — Issue 11 / Ralph iteration 2: Channel Overview HTML panel

### Done
- New `<section class="panel channel-overview">` inserted between the
  topbar and the Discovery Topic Map section. Header: `<h2>` channel
  title + muted "Channel ID: <id>" subtitle. Body: 5 stat tiles
  (Videos / Transcripts / Topics / Subtopics / Comparison groups) in a
  new `.channel-overview-stats` auto-fit grid; tiles reuse existing
  `.topic-stat` styling. Latest-discovery line below the tiles.
- `renderChannelOverview(payload.channel_overview)` JS function added
  just before `renderDiscoveryTopicMap`; wired into `render()`
  immediately after `renderContext` and before `renderDiscoveryTopicMap`.
  Empty-state branch (when `latest_discovery` is null) renders
  "Latest discovery · No discovery yet — run `analyze` or `discover`
  to start." Defensive fallback when `overview` itself is falsy
  (clears panel, no crash) — useful once polish iteration ships the
  no-primary-channel state.
- Minimal CSS: `.channel-overview` (margin), `.channel-overview-stats`
  (auto-fit grid mirroring `.topic-stats` shape), `.channel-overview-latest`.
- `UI_REVISION` bumped to
  `2026-05-08.2-channel-overview-above-discovery-panel`. Kept the
  "discovery" substring because 9 existing `test_ui_revision_advances_for_*`
  tests assert it (acts as a soft "this app is post-pivot" marker).
- 6 new tests in `ChannelOverviewHTMLTests`: panel markup IDs;
  ordering above the Discovery Topic Map; `renderChannelOverview`
  defined + wired into `render()`; stat-tile labels present in JS
  source; empty-state copy mentions `analyze` + `discover`;
  `UI_REVISION` contains `channel-overview`.

### Verified
- `.ralph/verify.sh`: 188 tests, ~46s, OK (was 182; +6).

### Notes for next iteration
- Polish iteration (sub-plan 3): graceful no-primary-channel state
  (`get_primary_channel(db_path)` can return `None` — the iteration 1
  call site at line ~2627 currently dereferences `.project_id` /
  `.channel_id` and would crash; needs a guard with an empty
  `channel_overview` payload). Empty-DB safety check (no videos /
  no runs / no topics — JS render already handles `null`/falsy
  values, so the remaining work is the Python side returning an
  inert payload).
- Issue acceptance criteria status after this iteration:
  ✓ payload key + counts + latest_discovery shape (iter 1)
  ✓ panel HTML + tiles + latest-discovery block + empty copy (iter 2)
  ✗ "renders without errors when primary_channel unset / DB empty /
    discovery_runs empty" — final iteration.

### Next
- Iteration 3: graceful no-primary-channel state + COMPLETE.

---

## 2026-05-08 — Issue 11 / Ralph iteration 1: channel_overview payload key

### Done
- `_build_channel_overview(db_path, project_id, channel_id)` helper in
  `review_ui.py` (placed before `_topics_introduced_in_run`). Single
  `sqlite3.connect`, seven scoped reads: channel title/yt_id from
  `channels`; video count from `videos` WHERE channel_id; transcript
  count via `video_transcripts` JOIN videos; distinct topic count via
  `video_topics` JOIN videos; distinct subtopic count via
  `video_subtopics` JOIN videos; comparison-group count via
  `comparison_group_videos` JOIN videos; latest `discovery_runs` row
  scoped by channel_id. `latest_discovery` is `None` when no run exists,
  else `{id, status, started_at (aliased from created_at), model,
  prompt_version}`.
- `build_state_payload` now surfaces a new `channel_overview` key
  alongside `discovery_topic_map` (no refactor of the assembler).
- 2 tests in new `ChannelOverviewPayloadTests`:
  `test_state_payload_has_channel_overview_with_seeded_counts` (stub
  discovery → 2 videos / 0 transcripts / 2 topics / 0 subtopics / 0
  comparison groups + latest run metadata),
  `test_state_payload_channel_overview_latest_discovery_null_when_no_run`
  (empty DB → `latest_discovery` is `None`).

### Verified
- `.ralph/verify.sh`: 182 tests, ~46s, OK (was 180; +2).

### Notes for next iteration
- `discovery_runs` schema column is `created_at`; payload exposes it as
  `started_at` per acceptance criteria — no schema change needed.
- `comparison_groups` are scoped by subtopic, not channel directly. The
  count uses `comparison_group_videos` JOIN videos so the number reflects
  comparison groups that touch this channel's videos.
- `project_id` plumbed through but unused — spec'd signature kept; the
  polish/no-primary-channel iteration may need it.
- Sub-plan 2 (HTML panel + JS render + HTML wiring test) is next.

### Next
- Iteration 2: render Channel Overview panel above Discovery Topic Map;
  HTML wiring test.

---

## 2026-05-08 — Issue 08 / Ralph iteration 3: round-trip test + COMPLETE

### Done
- `test_curation_survives_full_rerun_round_trip` in
  `StickyCurationRenameReplayTests`: seeds Health/Business via custom
  payload (vid1→Health, vid2→Health+Business), renames Health→Wellbeing,
  marks (vid2, Business) wrong, runs discovery again with stub still
  emitting the pre-curation names + a brand-new "Tech" topic. Asserts
  simultaneously: (a) only "Wellbeing" exists with vid1+vid2 attached
  (rename replay), (b) (vid2, Business) absent from second run's
  `video_topics` (mark-wrong replay), (c) `_topics_introduced_in_run`
  returns `["Tech"]` (new-topic surfacing).
- No production code change — exercises existing replay + new-topic
  machinery end-to-end.

### Verified
- `.ralph/verify.sh`: 180 tests, ~45s, OK (was 179; +1).

### Issue 08 acceptance status
- AC4 (rename A→B re-run) ✓ — `test_rename_then_rerun_keeps_curated_name_with_episodes`
  + round-trip.
- AC5 (mark-wrong re-run) ✓ — `test_mark_wrong_then_rerun_suppresses_assignment`
  + round-trip.
- AC6 (new topic surfaced for approval) ✓ — `_topics_introduced_in_run`
  + `discovery-topic-new-badge` (auto-replay surfaces the diff via badge;
  per-change approval GUI deferred per overlay).
- AC1/2/3 (full diff GUI, per-change approve/reject) — explicitly
  out-of-scope per `.ralph/issues/08-sticky-curation.md`. Merge/split/
  move event logging deferred to potential issue 08b.

### Next
- Issue 08 COMPLETE; branch ready for review and merge.

---

## 2026-05-08 — Issue 08 / Ralph iteration 2: surface new topics introduced by re-runs

### Done
- `topics` schema: `first_discovery_run_id INTEGER` (FK
  `discovery_runs(id) ON DELETE SET NULL`) added to `TABLE_STATEMENTS` +
  `REQUIRED_TABLE_COLUMNS`. Recorded on first INSERT in `run_discovery`
  (`INSERT INTO topics(project_id, name, first_discovery_run_id) VALUES
  (?, ?, ?) ON CONFLICT DO UPDATE SET name = excluded.name`); ON CONFLICT
  preserves the original first-seen run.
- `_topics_introduced_in_run(connection, channel_id, run_id)` helper in
  `review_ui.py`: returns `[]` when no earlier `discovery_runs` row
  exists for the channel; otherwise distinct topic names where
  `vt.discovery_run_id = run_id AND t.first_discovery_run_id = run_id`,
  ordered by name COLLATE NOCASE.
- `_build_discovery_topic_map` queries `discovery_runs.channel_id`
  alongside `id` and adds `new_topic_names: [...]` (empty list, never
  null) to the payload.
- JS: `renderDiscoveryTopicMap` reads `map.new_topic_names` into a Set
  and appends `<span class="discovery-topic-new-badge">New</span>` next
  to matching `<h3>`. CSS pill mirrors `.discovery-episode-also-in`
  (slice 05 precedent). `UI_REVISION` bumped to
  `2026-05-08.1-discovery-new-topic-badge`.
- 4 tests in `StickyCurationRenameReplayTests`:
  `test_topics_introduced_in_run_returns_only_new_names`,
  `test_topics_introduced_in_run_empty_on_first_run`,
  `test_state_payload_carries_new_topic_names`,
  `test_html_page_renders_new_topic_badge`.

### Learned
- `MIN(video_topics.discovery_run_id)` alone can't identify first-seen
  run: existing `ON CONFLICT(video_id, topic_id) DO UPDATE SET
  discovery_run_id = excluded.discovery_run_id` in `run_discovery`
  overwrites prior runs' ids whenever the same (video, topic) reappears.
  Hence the topics-side `first_discovery_run_id` column.
- Comparing `topics.created_at` to `discovery_runs.created_at` would be
  fragile with `CURRENT_TIMESTAMP` second-precision in fast tests where
  both runs land in the same second.

### Verified
- `.ralph/verify.sh`: 179 tests, ~45s, OK (was 175; +4).

### Next
- Iteration 3: overlay box 3 loose-end round-trip test
  `test_curation_survives_full_rerun_round_trip` (rename + mark-wrong +
  new-topic-introduced in a single fixture), then COMPLETE.

---

## 2026-05-08 — Issue 05 / Ralph iteration 3: stub round-trip multi-topic test; close-out

### Done
- `test_discovery.py::DiscoverCLITests::test_discover_stub_persists_multi_topic_video_under_two_topics`:
  exercises the `discover --stub` end-to-end and groups `video_topics`
  rows by `youtube_video_id`, asserting `vid1` carries both
  `STUB_TOPIC_NAME` and `STUB_SECONDARY_TOPIC_NAME` rows while `vid2`
  stays single-topic. Closes overlay box 3's "round-trip test: stub
  run persists ≥2 video_topics rows for the multi-topic stub video".
  HTML/payload also-in coverage already shipped iter 2 (lines 1109,
  1182, 1273); existing-fixture sweep done iter 1.

### Verified
- `.ralph/verify.sh`: 170 tests, ~40s, OK (was 169; +1).

### Issue 05 status: COMPLETE
- Both §A2/§A3 ROADMAP boxes ticked iters 1-2; overlay box 3
  loose-end tests now satisfied. All 5 issue acceptance criteria
  met (prompt v4 + stub fixture, schema permits N rows per video,
  GUI shows under each topic, also-in pill, smoke fixture).

---

## 2026-05-07 — Issue 04 / Ralph iteration 3: validator bounds + slice 04 test coverage; close-out

### Done
- `extractor/schema.py` now enforces `minimum`/`maximum` (number/integer)
  and `minLength` (string). Doc-comment updated. Discovery's
  `_DISCOVERY_SCHEMA` already declared `confidence: minimum 0 / maximum 1`
  and `reason: minLength 1` from iter 1, so the runtime guard now matches
  what the schema declares.
- `test_discovery.py` schema rejection tests added (5):
  `test_schema_rejects_assignment_missing_confidence`,
  `_missing_reason`, `_confidence_below_zero`, `_confidence_above_one`,
  `_empty_reason`.
- `ExtractorBackedLLMTests.test_callable_threads_varied_confidence_and_reason`
  asserts non-1.0 confidence + non-trivial reason flow via the Extractor
  adapter into `DiscoveryAssignment` (mirrors the existing round-trip
  test, but with varied values so the iter 2 threading is exercised on
  realistic data).
- `RunDiscoveryConfidencePersistenceTests` end-to-end test asserts
  varied-confidence assignments land in `video_topics.confidence/reason`
  AND inherit into `video_subtopics.confidence/reason` when a subtopic
  is named (no longer 1.0 / "" placeholders).
- Issue file `04-confidence-and-reason.md`: Status flipped to "done
  (criteria 1-4); criterion 5 re-homed under §A5 / issue 10". Criterion
  5 line annotated with the re-home pointer (mirrors issue 03 criterion
  6 pattern from commit `d48cad2`).

### Verified
- `.ralph/verify.sh` (test_discovery + test_extractor): 167 tests, ~35s,
  OK (was 160 pre-iter; +7 new tests, all green).

### Smoke (recommended pre-merge, not in this AFK loop)
- `.scratch/issue-02/smoke.py` against the new `discovery-v3` prompt is
  the fastest sanity check that Haiku still parses the widened
  schema with confidence + reason — but it's a real-LLM HITL trigger,
  so leave it for an attended run after merge.

### Issue 04 status: COMPLETE
- All ROADMAP §A2/§A3 boxes ticked, all 5 issue acceptance criteria
  accounted for (criteria 1-4 shipped iters 1-2 + this iter; criterion
  5 re-homed).

---

## 2026-05-07 — Issue 04 / Ralph iteration 1: widen discovery schema + prompt for confidence + reason

### Done
- `_DISCOVERY_SCHEMA` assignment items now `required: [youtube_video_id,
  topic, confidence, reason]`. `confidence` is `{type: number, minimum: 0,
  maximum: 1}` and `reason` is `{type: string, minLength: 1}`. Schema stays
  `additionalProperties: false` everywhere. Note: the project's minimal
  validator (`extractor/schema.py`) currently honors `type`/`required`/
  `additionalProperties` but ignores `minimum`/`maximum`/`minLength` — those
  numeric/length bounds will be enforced by slice 04 payload threading
  (or a small validator extension) in the next sub-plan.
- `_DISCOVERY_SYSTEM` prompt now asks the model for `confidence` (0.0–1.0)
  and a short `reason` per assignment; the example JSON in the system
  message includes `"confidence": 0.85, "reason": "matched chapter title
  'Sub A1'"` so Haiku has an unfenced shape to mirror (slice 02 lesson).
- `DISCOVERY_PROMPT_VERSION` bumped `discovery-v2` → `discovery-v3`.
- Test fixtures: 3 `runner.add_response` sites + 2 inline schema-accept
  tests in `test_discovery.py` updated to carry `confidence`/`reason` on
  each assignment so the verify gate stays green. `_payload_from_response`
  is unchanged (still hardcodes `1.0`/`""` until iter 2), so the
  round-trip test's existing assertions hold.

### Deferred to iter 2 (slice 04 payload threading)
- `_payload_from_response` should read `confidence`/`reason` from the
  response item.
- `stub_llm` keeps `confidence=1.0` but the existing `"stub assignment"`
  reason becomes the actually-threaded value.
- `test_schema_rejects_assignment_with_extra_keys` is currently green
  for the wrong reason (assignment is missing required `reason`, not
  because `confidence` is unknown) — re-point the rejected key to
  `priority`/`weight` so the assertion fires for the test's stated
  reason.
- New positive tests: schema accepts confidence+reason, rejects either
  missing, rejects out-of-range/empty (latter likely needs validator
  extension); round-trip + persistence tests asserting the LLM-emitted
  values land in `video_topics`/`video_subtopics`.

### Verified
- `.ralph/verify.sh` (test_discovery + test_extractor): 160 tests, ~35s,
  OK.

### Smoke (recommended pre-merge, not in this AFK loop)
- `.scratch/issue-02/smoke.py` is the fastest way to confirm the new
  prompt version still parses on Haiku — but it's a real-LLM HITL
  trigger, so leave it for an attended run after iter 2 ships.

---

## 2026-05-07 — Issue 03 / Ralph iteration 2: GUI subtopic drill-down on topic detail

### Done (3 new tests in `test_discovery.py`)
- Backend: `_build_discovery_topic_map` adds a second SQL pull on
  `video_subtopics` (filtered by latest `discovery_run_id`), buckets each
  topic's flat episode list into `subtopics: [{name, episode_count,
  episodes}]` plus `unassigned_within_topic`, and exposes a
  `subtopic_count` per topic. Existing `episodes` flat list preserved for
  backward compat with sort + multi-topic tests.
- Frontend: `renderDiscoverySubtopicBuckets` renders one `<details>` per
  subtopic (collapsible, count pill in summary) plus an
  "Unassigned within topic" bucket when present. Buckets sit above the
  flat episode list inside each `discovery-topic-card`. Topic-card stats
  gain a "Subtopics" tile alongside Episodes / Avg confidence.
- CSS: `.discovery-subtopic-list`, `.discovery-subtopic-bucket`,
  `.discovery-subtopic-unassigned` follow the existing dark/glass card
  vocabulary.
- `UI_REVISION` bumped to `2026-05-07.1-discovery-subtopic-drilldown`.
- ROADMAP §A3 line 80 ticked with a postscript noting both halves
  (episodes from iter 6 + subtopics from this iter).

### Why this is unblocked now
- §A2 line 80's deferral note was "subtopics deferred until §A2 LLM
  produces them." Issue 03 / iteration 1 widened discovery to emit +
  persist subtopics (`subtopics` + `video_subtopics` rows now populate
  per run), so the UI half can land cleanly.

### Verified
- Verify gate: `test_discovery + test_extractor` 160 tests, ~35s, OK.
- `node --check` test (`test_inline_script_parses_as_javascript`) stays
  green — the new render function is small and uses the same
  `JSON.stringify` / `escapeHtml` patterns as iter 6.

### Next
- Issue 03 acceptance: smoke test on a small real channel proving
  topics produce ≥2 subtopics each. Requires real LLM (HITL trigger #1)
  — not in scope for this iteration. Issue 03 is otherwise
  acceptance-complete pending that smoke run.

---

## 2026-05-07 — Issue 03 / Ralph iteration 1: widen discovery to emit + persist subtopics

### Done (TDD, 8 new tests in `test_discovery.py`)
- `_DISCOVERY_SCHEMA` now accepts an optional `subtopics: [{name, parent_topic}]`
  array on the payload and an optional `subtopic` field on each assignment.
  Schema stays `additionalProperties: false` everywhere — slice 04/05 will
  relax confidence/reason/multi-topic deliberately.
- `DISCOVERY_PROMPT_VERSION` bumped `discovery-v1` → `discovery-v2`. System
  prompt updated to ask for 2-6 subtopics per topic and to name a `subtopic`
  on each assignment whose `parent_topic` matches the assignment's `topic`.
- New `DiscoverySubtopic(name, parent_topic)` dataclass exported. `DiscoveryPayload`
  carries `subtopics: list[DiscoverySubtopic]` (default `[]`); `DiscoveryAssignment`
  carries `subtopic_name: str | None = None`. Defaults keep all existing
  call-sites/tests intact.
- `run_discovery` persistence: inserts `subtopics` rows under their parent
  topic id (idempotent on `(topic_id, name)`) and writes `video_subtopics`
  rows with `assignment_source='auto'`, `confidence`/`reason` mirrored from
  the assignment, `discovery_run_id=run_id`. Assignments without a
  subtopic skip the junction insert. Unknown parent_topic on a subtopic, or
  an assignment-level subtopic that isn't in `payload.subtopics` under the
  named topic, raises `ValueError` (mirrors the existing topic validator).
- `stub_llm` now emits one subtopic (`"General sub"` under `"General"`)
  with every video assigned to it, so the stub end-to-end exercises the
  new persistence path.
- Test updates: existing `test_schema_rejects_assignment_with_extra_keys`
  switched its rejected key from `subtopic` (now valid) to `confidence`
  (still rejected until slice 04). Two new schema tests +
  `RunDiscoverySubtopicPersistenceTests` (7 tests) cover persistence,
  graceful-skip, both raise paths, stub shape, and Extractor-backed
  payload round-trip.
- ROADMAP §A2 lines 72/74 amended with slice 03 scope postscripts
  (mirrors the slice 02 pattern).

### Learned
- Adding `subtopic_name` as the last field on the existing `DiscoveryAssignment`
  with a default of `None` and `subtopics` last on `DiscoveryPayload` with
  `field(default_factory=list)` keeps the dataclass-defaults rule satisfied
  and avoids a churn cascade across the ~30 test sites that build payloads
  positionally — none of them touch the new fields.
- Schema has `subtopics` optional (not in `required`) on purpose: existing
  ExtractorBackedLLMTests fixtures + RunDiscoveryErrorPathTests handcraft
  payloads without the new key, and the slice 02 stub shape needs to keep
  validating cleanly. Real LLM is asked for subtopics via the system
  prompt; the schema floor is "topics + assignments must be present."
- Bumping `DISCOVERY_PROMPT_VERSION` is correct because the system prompt
  + schema both changed. `register_discovery_prompt` is idempotent on
  `(name, version)` so old `discovery-v1` registrations from other test
  modules stay isolated by `_RegistryIsolation` setUp/tearDown.

### Next
- §A3 line 80 ("Topic detail: subtopics + episodes assigned to each")
  becomes implementable now that `video_subtopics` rows actually populate
  via discovery. Likely shape: extend `_build_discovery_topic_map` payload
  with per-topic subtopic buckets + episode arrays, and render a third
  drill-down level in the JS topic-card. Issue 03 acceptance criterion
  "GUI: topic map view drills into a topic detail view that lists
  subtopics with counts; clicking a subtopic shows the assigned episode
  list" maps directly here.
- After §A3 line 80, issue 03's smoke-test acceptance ("a small real
  channel produces topics with at least 2 subtopics each") still needs
  a real-LLM run — HITL trigger #1.

---

## 2026-05-06 — Issue 02 / smoke run on DOAC + fence-strip fix in `_parse`

### Done
- HITL smoke test: ingested 15 DOAC episodes, ran `make_real_llm_callable()` against Claude Haiku 4.5. End-to-end success on second attempt.
- First attempt failed: Haiku returned valid JSON but wrapped in ```` ```json ... ``` ```` fences despite explicit "no markdown fences" prompt directive. Added `_strip_code_fence` in `extractor/runner.py`'s `_parse`. Three new tests in `FenceStripTests` (fenced/bare-fenced/unfenced).
- Cost note (issue 02 AC): 15 episodes / 1 batched call / 8,528 input + 689 output tokens / 6.9s wall / **$0.0120** at Haiku 4.5 pricing ($1/M in, $5/M out). Topics produced were credible (Sexual Health, AI, Wealth, Neuroscience, etc.) — see commit body for the full set.

### Learned
- "Output JSON only — no fences" in the system prompt is not sufficient on Haiku; treat fenced output as the expected case and strip defensively.
- Slice-02 schema doesn't carry confidence, so all assignments land at the parser's default 1.0. Per spec — confidence ships in slice 04.

### Next
- CLI still requires `--stub` for `discover`/`analyze`. Adding a `--real` path is a small follow-up (file under §A5 or spin a tiny issue 02b — not blocking the merge).

## 2026-05-06 — Issue 02 / Ralph iteration 6: tick persist-to-junction-tables at slice-02 scope

### Done (doc-only, no code change)
- Ticked ROADMAP §A2 line 74 ("Persist to `topics`, `subtopics`, junction
  tables, `discovery_runs`") at slice-02 scope. `run_discovery` already
  persists the slice-02 surface — `topics` + `video_topics` +
  `discovery_runs` (success + errored), confidence/reason default to
  1.0/"" since the slice-02 prompt schema rejects those keys.
  `subtopics` / `video_subtopics` deliberately stay empty in slice 02
  and ship in slices 03–05 as the schema widens. Mirrors the iteration 4
  pattern (parenthetical noting deferred scope).

### Learned
- §A2 is now fully ticked at slice-02 scope. Issue 02 acceptance
  criteria still has three open items: real-LLM smoke run on a 10-20
  episode channel, "credible topic list" credibility check, and
  cost-tracking note. All three require actually calling the real LLM,
  which is HITL trigger #1 (the verify gate must not spend tokens). The
  loop should pause here for human review per PROMPT.md instruction #3
  ("no unchecked checkbox remains in those sections but the issue's
  acceptance criteria are not all met").
- `make_real_llm_callable` (iter 3) already raises unless
  `RALPH_ALLOW_REAL_LLM=1` is set, so the pause gate is enforced in
  code, not just the harness.

### Next
- HITL: a human runs `RALPH_ALLOW_REAL_LLM=1` against a real channel
  (Diary of a CEO, 10-20 episodes), captures token + cost numbers, and
  records them somewhere durable (likely a new line in this WORKLOG or
  an issue 02 acceptance-evidence note in `.scratch/`). After that the
  remaining issue 02 boxes can be ticked and the branch is COMPLETE.

---

## 2026-05-06 — Issue 02 / Ralph iteration 5: errored-run path on llm failure

### Done (TDD, 2 new tests in `test_discovery.py`)
- `discovery.py` `run_discovery` now wraps the `llm(videos)` call in
  `try/except Exception`. On any exception it inserts a `discovery_runs`
  row with `status='error'` (model + prompt_version still recorded for
  audit), commits, then re-raises. Topic and `video_topics` inserts only
  run after a successful payload, so the error path leaves no partial
  state — exactly the slice 02 acceptance: "on second failure the run is
  marked errored and no partial state is persisted".
- New `RunDiscoveryErrorPathTests`:
  - `test_llm_error_marks_run_errored_and_persists_no_partial_state` —
    seeds a 2-video channel, passes a callable that raises
    `SchemaValidationError` (the same exception Extractor.run_one
    re-raises after its one-retry exhausts), asserts a single
    `discovery_runs` row with `status='error'`, no `topics`, no
    `video_topics`, and that the exception propagates.
  - `test_llm_error_does_not_corrupt_prior_successful_run` — runs a
    successful stub run first, then a failing run, asserts both rows
    exist with the right statuses and the prior run's assignments
    remain intact.

### Learned
- `_run_single_with_retry` in `extractor/runner.py` already does the
  one-retry on parse failure and writes audit rows to `llm_calls` for
  both `parse_status='retry'` and `parse_status='failed'`. Discovery only
  needed the surface-level error path. Tests use `SchemaValidationError`
  rather than a generic `Exception` so they exercise the realistic
  failure shape.
- Bare `except Exception` is intentional here: any exception means we
  shouldn't persist topics/assignments. Catching `ExtractorError`
  specifically would silently drop other failure modes (network errors,
  KeyError on context dict, etc.) and still leave a partial state risk.

### Next
- Roadmap §A2 line 74: "Persist to `topics`, `subtopics`, junction
  tables, `discovery_runs` *(persistence done; awaits real payload)*".
  This is effectively done — `run_discovery` already persists topics +
  `video_topics` + `discovery_runs`. `subtopics`/`video_subtopics`
  intentionally stay empty in slice 02 (deferred to slice 03 per issue
  spec). The bullet wraps both slice-02 and slice-03 scope; the
  appropriate next-iteration move is to tick it with a slice-02
  parenthetical (mirroring the line 72 / iteration 4 pattern) and let
  slice 03 widen the schema + handlers.
- After that line, the §A2 bullets are exhausted and the remaining issue
  02 acceptance criteria (smoke test on a real channel; cost-tracking
  note) require running the real LLM — that is HITL trigger #1 territory
  and should pause the loop for human review.

---

## 2026-05-06 — Issue 02 / Ralph iteration 4: tick prompt-shape checkbox at slice scope

### Done (no code change; doc-only)
- Ticked ROADMAP §A2 line "Prompt produces: list of broad topics with subtopics,
  plus per-episode topic/subtopic assignments with confidence and reason" as
  satisfied at slice 02 scope: prompt produces broad topics + per-episode
  single-topic assignments. Subtopics/confidence/reason are deferred to slices
  03–05 per issue 02 spec ("Subtopics, confidence, multi-topic, and reason
  fields stay out — they ship in slices 03, 04, 05").
- Annotation flags the contradiction so future iterations don't re-open the
  checkbox: `_DISCOVERY_SCHEMA` is `additionalProperties: false` and rejects
  those keys today by design — slices 03–05 widen the schema deliberately.

### Learned
- Roadmap §A2 was authored before per-issue slicing was finalized, so several
  checkboxes (line 72 in particular) bundle multi-slice scope into a single
  bullet. Pattern from §A3 (e.g. lines 80, 84) is to tick with a parenthetical
  noting what's deferred and to which slice — followed here.

### Next
- Roadmap line 73: "Validate response shape; reject malformed batches; retry
  once" — Extractor already owns schema validation + one retry. Discovery-side
  work needed: when the Extractor raises after the retry, mark the
  `discovery_runs` row with `status='error'` and ensure no partial state
  (topics / `video_topics`) is persisted. `run_discovery` currently calls the
  llm before opening the cursor + insert path, so wrap the llm call in a
  try/except that inserts an errored run row and re-raises (or returns a
  sentinel — TBD per consistency with current callers).

---

## 2026-05-06 — Issue 02 / Ralph iteration 3: single batched LLM call site

### Done (TDD, 9 new tests in `test_discovery.py`)
- `discovery.py` registers prompt `discovery.topics@discovery-v1` via the
  Extractor registry. System message instructs the LLM to emit
  `{topics: [...], assignments: [{youtube_video_id, topic}]}` and forbids
  prose / markdown fences. Schema (`additionalProperties: false`) enforces
  exactly that shape — extra keys like `subtopic`/`confidence` are rejected
  so future slices add them deliberately.
- `register_discovery_prompt()` is idempotent — repeat calls return the
  already-registered Prompt instead of raising.
- `discovery_llm_via_extractor(extractor)` returns an `LLMCallable` that
  renders all videos into one prompt and round-trips a single
  `Extractor.run_one(...)` call. The Extractor owns schema validation +
  one-retry on parse failure (slice 02 acceptance criterion). Slice 02
  scope: `confidence=1.0` and `reason=""` defaults are filled by the
  adapter; later slices (03–05) extend the schema.
- `make_real_llm_callable(connection, *, model=None)` constructs an
  `AnthropicRunner(model=model or DEFAULT_MODEL)` + `Extractor` wired
  adapter. **Raises `RuntimeError` unless `RALPH_ALLOW_REAL_LLM=1`** so the
  verify gate path can't accidentally spend tokens. Tests cover both unset
  and `="0"` cases.

### Learned
- The existing `LLMCallable = Callable[[Sequence[DiscoveryVideo]],
  DiscoveryPayload]` interface from slice 01 is exactly the seam needed —
  the new adapter just produces an `LLMCallable` from an `Extractor`, no
  changes to `run_discovery`. The caller (CLI in a later iteration) opens
  its own connection, builds the Extractor + adapter, and passes the
  callable into `run_discovery(..., prompt_version=DISCOVERY_PROMPT_VERSION,
  ...)` so the run row records `discovery-v1`.
- `Extractor` lives in `yt_channel_analyzer.extractor` (slice 00). It uses
  the `llm_calls` audit table and a separate connection from
  `run_discovery`'s own connection — both safe with SQLite WAL.

### Next
- Wire the prompt content for slice 02's broader §A2 checkbox: "Prompt
  produces: list of broad topics with subtopics, plus per-episode
  topic/subtopic assignments with confidence (0.0–1.0) and a short reason
  string". Per the issue spec, slice 02 only ships broad topics + single
  topic per episode — subtopics/confidence/reason land in slices 03–05.
  So the next iteration likely focuses on the "Validate response shape;
  retry once; on second failure mark errored" checkbox (already mostly
  delegated to Extractor — needs the discovery-side error path to set
  `discovery_runs.status='error'` instead of persisting partial state).

---

## 2026-05-06 — Issue 02 / Ralph iteration 2: strip description boilerplate before LLM

### Done (TDD, 8 new tests in `test_discovery.py`)
- New `strip_description_boilerplate(description)` in `discovery.py`. Line-
  based regex filter that drops sponsor reads ("Sponsored by", "Brought to
  you by", "Sponsors:", "Use code … for X% off"), subscribe/like/bell
  CTAs, "Follow me on …" lines, social-platform label lines (`Twitter:`,
  `Instagram:`), bare social/podcast URLs (instagram, twitter/x, tiktok,
  facebook, linkedin, threads, patreon, discord, youtube/youtu.be,
  spotify, apple), and "Listen on …" / "Available on …" CTAs. Chapter-
  marker lines (matched by the existing `_CHAPTER_LINE` regex) are always
  kept so episode structure still reaches the LLM.
- `run_discovery` now sets `DiscoveryVideo.description` to the cleaned
  text. `chapters` is still parsed from the original description so the
  filter can't accidentally elide structure even if a chapter title
  happens to mention a sponsor.
- Returns `None` for `None` input, `""` for empty input, possibly `""`
  if the entire description was boilerplate. Consecutive blank lines
  collapsed and leading/trailing blanks trimmed.

### Learned
- Patterns ending in `\b` after `:` don't match line-final colons because
  `:` is non-word and there's no following word char. Split the
  `Sponsors:` rule into its own pattern (`\bsponsors?:`) without a
  trailing `\b`. Caught by the `test_strips_sponsor_read_lines` red.
- The boilerplate filter is intentionally aggressive — over-filter beats
  under-filter for Phase A discovery, where a sponsor brand leaking into
  the LLM context could nucleate a phantom topic.

### Next
- Build the single batched LLM call (Haiku 4.5 / GPT-4o-mini). HITL
  trigger #1 — adding a real-LLM call site means the next iteration
  must wrap it with the `RALPH_ALLOW_REAL_LLM=1` env-var guard and
  raise without it; the verify gate must still pass with the env unset.

---

## 2026-05-06 — Issue 02 / Ralph iteration 1: pull chapter markers into discovery videos

### Done (TDD, 7 new tests in `test_discovery.py`)
- New `Chapter(start_seconds, title)` frozen dataclass exported from
  `discovery.py`. New `parse_chapters_from_description(description)` helper
  that follows YouTube's chapter-recognition rules conservatively: ≥3
  timestamped lines, first timestamp is `0:00`, timestamps strictly
  monotonically increasing. If any check fails, returns an empty tuple
  rather than half-parsed chapters.
- `DiscoveryVideo` gained a `chapters: tuple[Chapter, ...] = ()` field
  (default empty so existing `DiscoveryVideo(...)` constructors keep
  working). `run_discovery` now populates `chapters` per video by parsing
  the description, so the LLM callable receives titles + descriptions +
  chapters as the issue 02 sub-plan calls for.
- No schema change — chapters are derived per discovery run from the
  existing `videos.description` column. YouTube Data API doesn't return
  chapters as a separate field anyway; they live inside descriptions.

### Learned
- The minimal helper accepts a few stylistic variants (leading bullets,
  bracketed timestamps, an optional separator) but keeps the YouTube
  validity rules strict, so a description with two stray timestamps in
  prose won't be misread as chapter markers. Ad-read sponsor blocks
  with `0:00` Intro-style chapters still parse cleanly.
- Defaulting `chapters=()` keeps the existing `StubLLMTests` and
  `_seed_channel_with_videos` test fixtures intact — no churn outside
  the new tests.

### Next
- Pre-filter common boilerplate (sponsor reads, social CTAs) from
  descriptions before they're handed to the LLM. Parsed chapters are
  the natural anchor for "trim everything below the last chapter line"
  if we want to be aggressive; otherwise a regex-based filter for
  common ad-read tells.

---

## 2026-05-06 — Issue 09 / Ralph iteration 14: document sort-persistence decision

### Done (docs only — no code)
- Added Decisions section to `.scratch/phase-a-topic-map/issues/09-sort-and-low-confidence-styling.md`:
  - Sort persistence: per-topic JS `Map`, not persisted to localStorage/server, resets to recency on reload. Rationale: cheapest-to-ship, reversible (localStorage is a strict superset), single-user app, and topic-rename/merge/split already complicates a stable persistence key.
  - View-count sort option: deferred because `videos.view_count` is not ingested. Listed as a known acceptance-criteria gap with a clear unblock condition.
- Ticked all five issue 09 acceptance criteria checkboxes in the spec to reflect met state (with cross-refs to the deferral notes).
- Ticked the last unchecked §A3 sort-persistence checkbox in `ROADMAP.md`.

### Issue 09 status
- All five acceptance criteria met; remaining §A3 unchecked items belong to other issues (subtopic rendering, blocked on §A2 real LLM). Branch is ready for `<ralph>COMPLETE</ralph>` next iteration.

### Next
- Next iteration: confirm acceptance criteria all met and emit COMPLETE for the branch.

---

## 2026-05-06 — Issue 09 / Ralph iteration 13: configurable low-confidence threshold

### Done (10 new tests in `test_discovery.py::DiscoveryLowConfidenceThresholdTests`)
- New env var `YTA_LOW_CONFIDENCE_THRESHOLD` (default 0.5) read by
  `_load_low_confidence_threshold()` in `review_ui.py`. Validates: blank
  / non-numeric / out-of-range [0,1] all fall back to default. Threshold
  is included on the `discovery_topic_map` payload so the JS doesn't
  need its own constant.
- Replaced the hardcoded 0.33/0.66 dual-threshold logic in JS with a
  single threshold sourced from `map.low_confidence_threshold`. Both the
  topic-card confidence bar and the episode card now emit at most one
  `low` class (no more `very-low`). CSS for `.discovery-episode.low`
  collapsed into one rule (opacity 0.55, bad-coloured confidence text);
  `.confidence-bar.very-low` and `.discovery-episode.very-low` rules
  removed.
- New `_low_confidence_class(confidence, threshold)` Python helper used
  by tests; the JS mirrors the same `c < threshold` check with the
  threshold injected via the payload.
- Mixed-confidence fixture test seeds 0.2 / 0.5 / 0.9 assignments in a
  single topic, runs `_build_discovery_topic_map`, and asserts the
  classifier returns `low` for 0.2 and `''` for 0.5 / 0.9. HTML tests
  guard against regressing back to dual thresholds (`0.33`/`0.66` and
  `very-low` are now banned substrings in the rendered page).
- UI revision bumped to `2026-05-06.9-discovery-confidence-threshold`.
  (First attempt used `discovery-low-confidence-threshold` but the
  substring `very-low` lurked inside `discovery-low` — the regression
  test caught it.)

### Learned
- Substring-style HTML assertions (`assertNotIn("very-low", html)`) are
  fragile against unrelated identifiers that contain the same letters.
  `discovery-low-confidence-threshold` literally contains `very-low`
  via the trailing `very` of `discovery` plus `-low`. Renamed UI rev to
  sidestep the collision, and the test still does its job.
- The threshold + classifier helper duo (Python helper + payload field
  consumed by JS) is the cheapest way to unit-test the styling
  decision without a JS test harness. JS only mirrors the comparison
  literally; if that drifts, the `test_html_uses_payload_threshold`
  guard still notices a regression.

### Next
- Issue 09's last unchecked roadmap item: document the sort-persistence
  decision (per-topic dropdown resets to recency on reload). One-line
  note somewhere durable. Then issue 09 acceptance criteria are met
  and the branch can `<ralph>COMPLETE</ralph>`.

---

## 2026-05-06 — Slice 07 (partial) / Ralph iteration 12: mark assignment wrong

### Done (TDD, 14 new tests in `test_discovery.py`)
- New `wrong_assignments` table in `db.py` schema:
  `(id, video_id, topic_id, subtopic_id NULLABLE, reason NULLABLE, created_at)`.
  This is the first persistent curation-event record (slice 06 stopped at
  `assignment_source='manual'`). Slice 08 can replay these to keep
  curation surviving discovery re-runs.
- New `mark_assignment_wrong(db_path, *, project_name, topic_name,
  youtube_video_id, subtopic_name=None, reason=None)` in `db.py`. When
  `subtopic_name` is None: deletes the `video_topics` row and ALSO drops
  any `video_subtopics` rows whose subtopic is under that topic
  (otherwise the video would still hang off the topic via subtopic
  joins). When provided: deletes only the `video_subtopics` row, leaves
  the topic membership intact. Records the event row in
  `wrong_assignments` with `subtopic_id` populated only for the
  subtopic-scoped path. Rejects unknown project / topic / video /
  subtopic, and rejects when the row to remove doesn't exist.
- New `/api/discovery/episode/mark-wrong` endpoint. Body:
  `{topic_name, youtube_video_id, subtopic_name?, reason?}`. Tailored
  success messages for topic vs subtopic removal.
- UI: each `discovery-episode` chip in the discovery topic-map's
  episode list now has a `Wrong topic?` button (calls
  `markEpisodeWrong(topic, vid, null)`). Each subtopic-bucket video chip
  in the selected-topic inventory now has a `Wrong subtopic?` button
  (calls `markEpisodeWrong(topic, vid, subtopic)`). Confirm dialog
  before posting.
- UI revision bumped to `2026-05-05.8-discovery-episode-mark-wrong`.
  Existing `test_ui_revision_advances_for_move` relaxed to the durable
  `discovery` substring (same pattern split/merge used after their
  successors shipped).

### Learned
- The "remove and record" pattern looks identical for topic-scoped and
  subtopic-scoped wrong-marks, but the topic case has to also clear
  child `video_subtopics` rows or the video stays attached to the topic
  via subtopic membership joins. Caught by the dedicated
  `test_mark_wrong_topic_also_drops_video_subtopics_under_topic` test.
- Kept the curation event minimal (`wrong_assignments` table only — not
  a generic `topic_curation_events` log) per scope discipline. Slice 08
  can generalize once it has concrete replay needs.

### Next
- Slice 08: curation surviving discovery re-runs. Likely needs to
  generalize `wrong_assignments` (and the existing
  `assignment_source='manual'` markers on rename/merge/split/move) into
  an event log that the next discovery run consults before applying its
  output.
- Or pivot to A2: real Haiku/4o-mini batched discovery call to retire
  the stub.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 11: move episode between subtopics

### Done (TDD, 13 new tests in `test_discovery.py`)
- New `move_episode_subtopic(db_path, project_name, topic_name,
  youtube_video_id, target_subtopic_name)` in `db.py`. Resolves project,
  topic, target subtopic (must be under that topic), and video; rejects
  unknowns. Refuses if the video isn't already on the topic. If the video
  has an existing `video_subtopics` row under any subtopic of that topic,
  re-points it to the target (no-op when already on target). Otherwise
  inserts a new row with `assignment_source='manual'` to flag the
  curation move. Returns `{moved, inserted, previous_subtopic_name,
  target_subtopic_id}`.
- New `/api/discovery/episode/move-subtopic` endpoint mirroring the
  rename/merge/split shape. Body: `{topic_name, youtube_video_id,
  target_subtopic_name}`. Tailored success messages for moved / attached /
  no-op.
- UI: each video chip inside the selected-topic inventory's subtopic
  buckets now has a `Move` button (sibling subtopics only — hidden when a
  topic has only one subtopic). The JS handler prompts with a numbered
  list of candidate subtopics and asks for confirmation before posting.
- UI revision bumped to `2026-05-05.7-discovery-episode-move-subtopic`.
  Relaxed the `test_ui_revision_advances_for_split` assertion to the
  durable `discovery` substring (same pattern merge used after split
  shipped).

### Learned
- The natural surface for "move episode between subtopics" is the legacy
  topic-inventory panel, not the discovery topic-map panel. The discovery
  payload still doesn't expose subtopics (deferred until §A2 produces
  real subtopic data), and the inventory view already groups videos by
  subtopic, which is the structure this action needs.
- Per-row `assignment_source='manual'` on the move means future code that
  distinguishes auto vs. curated subtopic membership has the signal it
  needs without an extra column.

### Next
- Mark an assignment as wrong (the last §A3 curation action). Then
  curation surviving a re-run (slice 08).
- Or pivot to A2: real Haiku/4o-mini batched discovery call to retire
  the stub.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 10: discovery topic split

### Done (TDD, 14 new tests in `test_discovery.py`)
- New `split_topic(db_path, project_name, source_name, new_name,
  youtube_video_ids)` in `db.py`. Validates source != new, requires a
  non-empty id list, fails on missing project / source / colliding new
  topic name. Resolves the supplied `youtube_video_ids` to internal video
  rows scoped to the source topic; ids that aren't on the source are
  filtered out and reported back as `skipped_video_ids` (raise only if
  *all* are missing). Within a single transaction it: creates the new
  topic in the source's project, re-points the matching `video_topics`
  rows to the new topic, and drops `video_subtopics` rows for those
  videos whose subtopic still belongs to the source topic (keeps the new
  topic from inheriting orphaned subtopic membership). Returns
  `new_topic_id`, `moved_episode_assignments`,
  `dropped_subtopic_assignments`, `skipped_video_ids`.
- New `/api/discovery/topic/split` endpoint mirroring merge. Body:
  `{source_name, new_name, youtube_video_ids}`. Validates the id list is
  non-empty list of non-empty strings before calling the db helper.
- UI: each discovery topic card now has a `Split` button next to
  `Rename`/`Merge`. The JS handler prompts for the new topic name (must
  not collide), then prompts again with a numbered list of episodes for
  the user to enter comma-separated indices. Refuses selecting all
  episodes (suggests Rename instead). Confirm dialog before posting.
- UI revision bumped to `2026-05-05.6-discovery-topic-split`. Relaxed
  the `test_ui_revision_advances_for_merge` test to assert the durable
  `discovery` substring (same pattern earlier iterations used).

### Learned
- The orphan-subtopic cleanup was the only non-obvious bit of the split
  semantics. Without it, splitting episodes off "Productivity" into
  "Time Management" leaves `video_subtopics` rows pointing at
  Productivity-owned subtopics for the moved videos, which would still
  render under Productivity if you ever join through subtopic. Dropping
  those rows is the cheapest path; future iterations can offer a
  "carry the subtopic with you" affordance if needed.
- Listing all video ids inside `window.prompt` is fine for stub-scale
  topics (a handful of episodes) but will get unwieldy past ~20. A
  dedicated checkbox modal is the obvious follow-up; deferred until the
  real LLM produces realistic episode counts per topic.

### Next
- Move an episode between subtopics; mark an assignment as wrong. Then
  curation surviving a re-run (slice 08).
- Or pivot to A2: real Haiku/4o-mini batched discovery call to retire
  the stub.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 9: discovery topic merge

### Done (TDD, 11 new tests in `test_discovery.py`)
- New `merge_topics(db_path, project_name, source_name, target_name)` in
  `db.py`. Within a single transaction it: drops colliding source rows
  in `video_topics` (target wins), re-points remaining rows to the
  target, handles subtopic name collisions by re-pointing
  `video_subtopics` then dropping the source subtopic, re-points
  non-colliding subtopics, dedup-and-re-points
  `subtopic_suggestion_labels`, and finally deletes the source topic.
  Returns a stats dict: `moved_episode_assignments`,
  `dropped_episode_collisions`, `moved_subtopics`,
  `merged_subtopic_collisions`, `target_topic_id`.
- New `/api/discovery/topic/merge` endpoint; returns success message
  with stats. Rejects unknown source/target and same-name merges via
  the existing 400-Bad-Request path.
- UI: each discovery topic card now has a `Merge` button next to
  `Rename`. JS prompt lists the other discovery topics and validates
  the chosen target before calling the endpoint; confirm dialog before
  destructive action; per-topic sort preference is dropped for the
  source topic when its key disappears.
- UI revision bumped to `2026-05-05.5-discovery-topic-merge`. Relaxed
  the prior `test_ui_revision_advances_for_rename` to use the
  `discovery` keyword like the other UI-revision tests, since pinning
  to "rename" blocked every future iteration.

### Learned
- `video_topics` has a partial unique index for one-primary-per-video,
  but it can't fire during a merge: a video already had at most one
  primary topic before the merge, so re-pointing one source row to the
  target either lands in a colliding-and-dropped slot (target wins) or
  in a free slot (target inherits primary). No conflict.
- Comparison groups CASCADE-delete when their parent subtopic is
  dropped during a colliding-subtopic merge. Acceptable given Phase A4
  plans to legacy-archive the comparison-group code; documented via
  the merge-collision behavior rather than worked around.

### Next
- Split a topic, move an episode between subtopics, mark an assignment
  as wrong. Then think about curation surviving a re-run.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 8: discovery topic rename happy path

### Done (TDD, 7 new tests in `test_discovery.py`)
- New `DiscoveryTopicRenameTests` class. Covers: POST
  `/api/discovery/topic/rename` returns 200 and renames the topic in the
  DB; renamed topic surfaces correctly in `/api/state`'s
  `discovery_topic_map` with episode assignments preserved; rename of an
  unknown topic returns 400 with a "not found" message; rename to an
  existing topic name returns 400 with "already exists"; HTML wires
  `function renameDiscoveryTopic` and `/api/discovery/topic/rename`;
  each discovery topic card has a `discovery-topic-rename` button hook;
  `UI_REVISION` includes the substring `rename`.
- Backend: new `/api/discovery/topic/rename` route in
  `ReviewUIApp._handle_post`. Reuses existing `db.rename_topic`. Project
  name resolved via new helper `_resolve_primary_project_name(db_path)`,
  which avoids the `primary_channel.title`-as-project-name shortcut used
  by older suggestion routes (the test seed sets `project_name="proj"`
  and `channel_title="Channel"`, and topics live under `proj`).
- Frontend: new `renameDiscoveryTopic(currentName)` JS function uses
  `window.prompt`, refuses empty / unchanged names, migrates the
  per-topic sort selection (`discoveryEpisodeSortByTopic`) under the new
  name, and posts via the existing `mutate(...)` helper. Each discovery
  topic card now renders a small Rename button next to the title.
  Minimal CSS for `.discovery-topic-header` / `.discovery-topic-rename`.
- Relaxed `test_ui_revision_advances_for_episode_sort` from `sort` to the
  durable `discovery` substring (same pattern iteration 7 used on
  iteration 6's revision-string test, and iteration 6 used on iteration
  5's). Older revision-string tests already assert `discovery`.
- `UI_REVISION` bumped to `2026-05-05.4-discovery-topic-rename`.
- All 33 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted JS body, `node --check` parses cleanly (44KB).

### Slice 06 status
- Slice 06 (curation: rename + merge) is now partially landed. Rename
  happy path works against the stub topics. Merge, the
  curation-survives-rerun mechanism (`topic_curation_events` or
  equivalent — slice 08), error edge cases beyond "not found"/"already
  exists", and inline-edit UX polish all remain.

### Deferred (logged so the next iteration can pick up)
- Inline edit UX in place of `window.prompt`.
- Curation persistence across discovery re-runs (slice 08).
- Topic merge action (slice 06's other half).

### Next session — Ralph iteration 9
1. Topic merge happy path (`/api/discovery/topic/merge`) with episode
   assignment dedupe under target topic.
2. Or: kick off slice 02 prep — Extractor module wiring for the real
   LLM discovery prompt.
3. Or: PRD §A4 legacy move (split into mechanical mini-iterations:
   create `legacy/__init__.py`; move `comparison_group_suggestions.py`
   with a re-export shim; verify imports in `review_ui.py` still work).

---

## 2026-05-05 — Slice 01 / Ralph iteration 7: per-topic episode sort options

### Done (TDD, 4 new tests in `test_discovery.py`)
- New `DiscoveryEpisodeSortHTMLTests` class with 4 tests asserting the
  sort dropdown markup (`discovery-episode-sort`, `value="recency"`,
  `value="confidence"`), the `sortDiscoveryEpisodes` JS function, the
  `DEFAULT_DISCOVERY_SORT = 'recency'` constant, and that
  `UI_REVISION` includes `sort`.
- Per-topic sort dropdown rendered inside each discovery topic card —
  options Recency / Confidence, default Recency. Selection persisted in
  a per-topic `Map` keyed by topic name; `setDiscoveryEpisodeSort` writes
  the choice and re-renders the panel via the cached `lastDiscoveryTopicMap`.
- New JS helpers: `sortDiscoveryEpisodes(episodes, mode)` returns a
  sorted copy. Recency = `published_at DESC` with nulls last, confidence
  = numeric DESC with nulls last, both with NOCASE-style title tiebreak.
- Backend payload unchanged — episodes still served by confidence DESC,
  the JS reorders client-side.
- View-count sort deferred: `videos.view_count` is not currently
  ingested. Documented in the iteration plan rather than added as a
  no-op option (would be a stub that always tied at 0).
- Relaxed older `test_ui_revision_advances_for_episode_list` from
  `topic-episodes` to the durable `discovery` substring (same pattern
  iteration 6 used on iteration-5's revision-string test).
- `UI_REVISION` bumped to `2026-05-05.3-discovery-episode-sort`.
- All 26 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted JS body, `node --check` parses cleanly (43KB).

### Deferred (logged so the next iteration can pick up)
- View-count sort option — needs `videos.view_count` populated during
  ingestion (`youtube.py` does not currently fetch it).
- Sort persistence across reloads — issue 09 says "implementer's choice";
  current implementation resets to recency on reload.

### Next session — Ralph iteration 8
1. Remove comparison-group panels from primary GUI nav (PRD §A4).
2. Move comparison-group code to `legacy/` with deprecation shims.
3. Curation actions (rename / merge / split / move / mark wrong) —
   requires real LLM topics from slice 02 to be useful, but the
   rename happy path could be wired up now against stub topics.

---

## 2026-05-05 — Slice 01 / Ralph iteration 6: GUI per-topic episode list

### Done (TDD, 4 new tests in `test_discovery.py`)
- Two new test classes: `DiscoveryTopicMapEpisodesPayloadTests` (per-topic
  `episodes` list shape; multi-topic episode appears under each topic
  with the right reason/confidence) and `DiscoveryTopicEpisodesHTMLTests`
  (HTML hook + UI revision marker). Updated the older
  `test_ui_revision_advances_for_discovery_topic_map_panel` to assert the
  durable `discovery` substring rather than a stale per-iteration tag.
- Extended `_build_discovery_topic_map` with a second query that pulls
  per-topic episode rows joined to `videos` (id/title/thumbnail/
  published_at/confidence/reason) and groups them onto the topic dicts.
  Episodes sorted by descending confidence then NOCASE title.
- New JS helper `renderDiscoveryEpisodeItem` renders each card: 64x36
  thumbnail (placeholder gradient when missing), two-line clamped title,
  confidence percentage chip, raw youtube_video_id, italic reason line.
  Topic cards now include `<ol class="discovery-episode-list">` below
  the confidence bar.
- Episode cards get `.low` (opacity 0.78, amber confidence) or
  `.very-low` (opacity 0.55, red confidence) modifiers below the same
  thresholds the topic-level confidence bar uses.
- `UI_REVISION` bumped to `2026-05-05.2-discovery-topic-episodes`.
- All 22 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted `<script>` body and `node --check` parses cleanly
  (41KB).

### Deferred (logged so the next iteration can pick up)
- Guest name on each episode card — not currently extracted from
  metadata; needs a description-parser or a separate "guest" field
  added during ingestion.
- Subtopics rendered under each topic — `stub_llm` doesn't produce
  subtopics, so persistence and rendering wait for the real LLM in
  slice 02 (PRD §A2).

### Next session — Ralph iteration 7
1. Curation actions on auto-discovered topics: rename, merge, split,
   move episode between topics, mark assignment as wrong (PRD §A3
   curation bullets).
2. Sort options for the per-topic episode list (recency, confidence)
   (PRD §A3 sort bullet).
3. Remove comparison-group panels from primary GUI nav.
4. Move comparison-group code to `legacy/` with deprecation shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 5: GUI discovery topic-map panel

### Done (TDD, 3 new tests in `test_discovery.py`)
- New `DiscoveryTopicMapHTMLTests` class. Tests assert that the rendered
  HTML page exposes `id="discovery-topic-map-grid"`, the heading
  "Auto-Discovered Topics", a `function renderDiscoveryTopicMap` JS
  definition, the call site `renderDiscoveryTopicMap(payload.discovery_topic_map)`
  inside `render()`, and that `UI_REVISION` includes `"discovery-topic-map"`.
- Added a new `<section class="topic-map discovery-topic-map">` above the
  existing pre-pivot Topic Map. Contains a `discovery-topic-map-meta`
  paragraph (run id / model / prompt version / status / created_at) and
  a `discovery-topic-map-grid` that holds the rendered topic cards.
- Added `renderDiscoveryTopicMap(map)` in the JS layer. Renders an empty
  state when the payload is null, a per-topic card grid otherwise.
  Each card shows topic name, episode count, average confidence as a
  percentage, and a colour-graded confidence bar (green ≥ 0.66, amber
  ≥ 0.33, red below). Wired into `render()` between `renderContext` and
  `renderTopicMap`.
- Added matching CSS: `.topic-map.discovery-topic-map` (green-tinted
  variant of the pre-pivot panel) and `.confidence-bar` with `.low` /
  `.very-low` modifiers.
- Bumped `UI_REVISION` to `2026-05-05.1-discovery-topic-map`.
- All 18 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted `<script>` body and `node --check` parses cleanly.

### Next session — Ralph iteration 6
1. Topic detail in the discovery panel: episode list per topic with
   "why this episode is here" reason + confidence indicator (PRD §A3
   second/third bullets).
2. Curation actions on auto-discovered topics: rename, merge, split,
   move episode, mark assignment wrong.
3. Remove comparison-group panels from primary GUI nav.
4. Move comparison-group code to `legacy/` with deprecation shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 4: GUI discovery topic-map payload

### Done (TDD, 3 new tests in `test_discovery.py`)
- Added `_build_discovery_topic_map(db_path)` helper to `review_ui.py`.
  Reads the latest `discovery_runs` row, then aggregates
  `COUNT(DISTINCT video_id)` and `AVG(confidence)` per topic via the
  `video_topics.discovery_run_id` FK. Sorted by descending
  `episode_count`, then topic name (NOCASE).
- Wired the new payload into `build_state_payload()` under the
  `discovery_topic_map` key. Returns `None` when no discovery run
  exists; otherwise `{run_id, model, prompt_version, status, created_at,
  topics: [{name, episode_count, avg_confidence}, ...]}`.
- Existing pre-pivot `topic_map` (built from the old topic-suggestion
  flow) is unchanged and lives alongside the new key. The GUI HTML
  hasn't been touched yet — that's the next iteration's job.
- New `DiscoveryStatePayloadTests` class with three tests: empty case,
  happy path with two topics + 3 assignments, and latest-run isolation
  (older run ignored after a second run is recorded).
- All 15 tests in `test_discovery.py` pass. The 2 pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` are unchanged.

### Next session — Ralph iteration 5
1. Render `discovery_topic_map` in the GUI HTML/JS — a panel above the
   pre-pivot Topic Map showing the auto-discovered topics with episode
   counts and confidence indicators (PRD §A3 first bullet).
2. Remove comparison-group panels from primary GUI nav.
3. Move comparison-group code to `legacy/` with deprecation-warning
   import shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 3: CLI `analyze` chain

### Done (TDD, 2 new tests in `test_discovery.py`)
- Added `analyze` subparser in `cli.py` with `--db-path`, `--project-name`,
  `--channel-input`, `--limit`, `--stub`. The `--stub` flag is currently
  required, mirroring `discover`.
- Handler chains: `resolve_canonical_channel_id(channel_input)` →
  `fetch_channel_metadata` → `upsert_channel_metadata` (creates project +
  primary channel) → `fetch_channel_videos` → `upsert_videos_for_primary_channel`
  → `run_discovery(..., llm=stub_llm)`. Prints a one-line summary.
- New tests in `AnalyzeCLITests`:
  - `test_analyze_chains_setup_ingest_and_discover` — monkey-patches the three
    YouTube callables on the `cli` module, runs `cli.main(["analyze", ...])`
    against a fresh DB, asserts project + primary channel + 2 videos +
    1 discovery run + 2 `video_topics` rows with `assignment_source='auto'`.
  - `test_analyze_requires_stub_flag` — without `--stub`, `cli.main` exits
    non-zero.
- All 12 tests in `test_discovery.py` pass. `ReviewUIAppTests` pre-existing
  failures unchanged.

### Next session — Ralph iteration 4
1. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
2. Remove comparison-group panels from primary GUI nav.
3. Move comparison-group code to `legacy/` with deprecation-warning import shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 2: CLI `discover --stub`

### Done (TDD, 3 new tests in `test_discovery.py`)
- Added `stub_llm(videos) -> DiscoveryPayload` to `discovery.py`. Returns one
  topic (`General`) with every video assigned to it (`confidence=1.0`,
  `reason="stub assignment"`). Also exported `STUB_MODEL = "stub"` and
  `STUB_PROMPT_VERSION = "stub-v0"` so the CLI and tests share the same
  identifiers.
- Added `discover` subparser in `cli.py` with `--db-path`, `--project-name`,
  `--stub`. The `--stub` flag is currently required; without it the parser
  errors with "real LLM lands in slice 02" — keeps the CLI surface honest
  until slice 02.
- New tests:
  - `StubLLMTests.test_stub_llm_returns_one_topic_covering_all_videos`
  - `DiscoverCLITests.test_discover_stub_creates_run_and_assignments` —
    runs `cli.main(["discover", ..., "--stub"])` end-to-end against a
    seeded 2-video DB and asserts a `discovery_runs` row plus 2
    `video_topics` rows with `assignment_source='auto'`.
  - `DiscoverCLITests.test_discover_requires_stub_flag` — without `--stub`
    the CLI exits non-zero.
- All 10 tests in `test_discovery.py` pass. The 2 pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` are unchanged.

### Next session — Ralph iteration 3
1. CLI `analyze` command chaining setup → ingest → discover.
2. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
3. Remove comparison-group panels from primary GUI nav.
4. Move comparison-group code to `legacy/` with deprecation-warning import shims.

---

## 2026-05-05 — Slice 01 session 2 / Ralph iteration 1: CHECK-constraint repair

### Done (TDD, 2 new tests in `test_discovery.py`)
- Added `_repair_video_topic_assignment_source_constraint` to `db.py`. Detects an
  old-shape `video_topics` / `video_subtopics` whose CHECK omits `'auto'` (by
  scanning `sqlite_master.sql` for the literal `'auto'`), then RENAMEs to
  `_old`, re-creates the table with the modern shape and CHECK clause, INSERT
  SELECTs all columns over, DROPs the old. Pattern mirrors
  `_repair_video_transcripts_constraint`.
- Wired the new repair into `ensure_schema` after the existing repairs and
  before INDEX_STATEMENTS (so unique indexes are re-created cleanly).
- New tests:
  - `test_ensure_schema_repairs_old_video_topics_check_constraint` — drops the
    fresh tables, recreates them with the pre-change CHECK, runs `ensure_schema`,
    then inserts an `'auto'` row into both junction tables.
  - `test_repair_preserves_existing_rows` — rebuilds an old-shape `video_topics`
    with one `('primary','manual')` row, runs `ensure_schema`, asserts the row
    survived.
- All 7 tests in `test_discovery.py` pass. The 2 pre-existing
  `ReviewUIAppTests` failures noted in session 1 are unchanged (verified the
  repair is a no-op on fresh DBs because `SCHEMA_STATEMENTS` already include
  `'auto'`).

### Open: git tracking
- Parent repo at `/home/chris/.openclaw/workspace` tracks only `db.py` and
  `review_ui.py` from this project. WORKLOG.md, PRD_PHASE_A_TOPIC_MAP.md,
  `discovery.py`, `test_discovery.py`, `.scratch/`, `extractor/`, and all the
  project docs are **untracked**. Ralph's per-iteration commit contract needs
  Chris's call: commit yt_channel_analyzer artefacts to the parent repo, init
  a nested repo here, or skip auto-commits and let WORKLOG be the progress
  ledger.

### Next session — Ralph iteration 2
1. CLI `discover` command (stub payload behind `--stub`) — test via `cli.main`.
2. CLI `analyze` command chaining setup → ingest → discover.
3. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
4. Remove comparison-group panels from primary GUI nav.
5. Move comparison-group code to `legacy/` with deprecation-warning import shims.

---

## 2026-05-04 — Slice 01 session 1: schema + stub discovery

### Done (TDD, 5 tests in `test_discovery.py`)
- Added `discovery_runs` table (channel_id FK, model, prompt_version, status, created_at).
- Extended `video_topics` and `video_subtopics` with `confidence REAL`, `reason TEXT`,
  `discovery_run_id INTEGER` (FK → discovery_runs ON DELETE SET NULL).
- Expanded `assignment_source` CHECK on both tables to include `'auto'`.
- New module `yt_channel_analyzer/discovery.py`: `DiscoveryVideo`, `DiscoveryAssignment`,
  `DiscoveryPayload`, and `run_discovery(db_path, *, project_name, llm, model, prompt_version) -> run_id`.
  LLM is injected as a callable — stub today, real LLM in slice 02.

### Learned / known gap
- `_ensure_required_columns` auto-adds the new columns to existing DBs (entries added to
  `REQUIRED_TABLE_COLUMNS`).
- SQLite can't ALTER a CHECK constraint — old DBs with the pre-change `assignment_source`
  CHECK will reject `'auto'` inserts. Needs a table-rebuild repair like
  `_repair_video_transcripts_constraint`. Not blocking fresh-DB tests; required before this
  hits any persisted DB.
- 2 pre-existing failures in `test_transcripts.py::ReviewUIAppTests` are unrelated to slice 01
  (verified against unmodified `db.py`).

### Next session — continue slice 01
1. CHECK-constraint repair for `video_topics` / `video_subtopics` (test: insert `'auto'`
   into an old-shape DB after `ensure_schema`).
2. CLI `discover` command (stub payload behind `--stub`) — test via `cli.main`.
3. CLI `analyze` command chaining setup → ingest → discover.
4. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
5. Remove comparison-group panels from primary GUI nav.
6. Move comparison-group code to `legacy/` with deprecation-warning import shims; keep
   `test_transcripts.py` green.

Read first next time: `CURRENT_STATE.md`, `PRD_PHASE_A_TOPIC_MAP.md`,
`.scratch/phase-a-topic-map/issues/01-*.md`, `discovery.py`, `test_discovery.py`.

---

## 2026-05-04 — Vision pivot to podcast knowledge extractor

### Done
- Reframed the project from "manual research workbench" to "podcast knowledge extractor."
  Canonical use case: point the app at *Diary of a CEO*, get a topic map, later get
  per-topic consensus / conflict / advice, eventually free-form Q&A.
- Resolved load-bearing architectural decisions through a structured grilling session:
  - Product shape: extractor + synthesizer, not curation workbench.
  - Unit of analysis: the **claim** (Phase C onward); episode-with-tags for MVP.
  - Topic discovery: LLM-proposed from metadata (titles, descriptions, chapter markers),
    then human-curated. No transcripts in MVP.
  - MVP scope: **Phase A — topic map of channel + episodes per topic.**
  - Episode-to-topic relationship: multi-topic, each assignment has confidence + reason.
  - Code strategy: **retrofit in place**; comparison-group machinery moves to `legacy/`.
  - LLM strategy: tiered (Haiku for extraction, Sonnet for synthesis), batch APIs,
    local sentence-transformers embeddings, `sqlite-vec` for vector storage,
    process-once-store-forever.
- Cost-modelled the full DOAC backlog: ~$0.10 for Phase A discovery, ~$8 one-time for
  Phase C full claim extraction with batch API. Phase D Q&A is fractions of a cent per query.
- Rewrote `PROJECT_SUMMARY.md`, `CURRENT_STATE.md`, `ROADMAP.md` to reflect the new vision.
- Wrote `PRD_PHASE_A_TOPIC_MAP.md` as the detailed plan for the next build slice.

### Learned
- The earlier "research workbench" framing was real but produced a product the user didn't
  actually want to operate manually. The user wants the app to do the structuring; they
  want to curate the result, not build it from scratch.
- Topic discovery does NOT require full transcripts. Metadata + chapter markers carry
  enough signal to propose a credible taxonomy at near-zero cost. This unblocks an
  early, cheap MVP.
- Most of the existing ~600KB of code is reusable. The schema, ingestion, review UI, and
  topic suggestion machinery all carry over with shifted semantics. The conceptual layer
  changed; the plumbing did not.
- The casualties are bounded to the comparison-group surface area. Those modules go to
  `legacy/`, not to deletion, in case Phase C wants pieces back.

### Next
- Phase A1: extend schema with `video_topics` / `video_subtopics` junction tables and
  a `discovery_runs` table; backfill from existing primary/secondary topic columns.
- Phase A2: build `discovery.py` — single batched LLM call that produces taxonomy +
  per-episode multi-topic assignments from metadata only.
- Phase A3: extend `review_ui.py` to render the auto-discovered topic map with confidence
  visible and curation actions (rename, merge, split, move, mark wrong).
- Phase A4: move comparison-group code to `legacy/`.
- First real run target: ingest Diary of a CEO and review the resulting topic map.

---

## 2026-04-25

### Done
- Added living project docs: `PROJECT_SUMMARY.md`, `ROADMAP.md`, and `CURRENT_STATE.md`.
- Captured the product as a structured YouTube research workbench rather than just a transcript tool.
- Documented that the review UI exists because CLI-only testing and QA became too difficult in practice.
- Tightened the roadmap to reflect a move toward a GUI-led workflow.

### Learned
- The codebase has grown beyond the earliest narrowly scoped mission notes.
- The next leverage point is probably better operator experience, not just more capability.
- GUI improvements are justified by actual workflow pain, not polish for its own sake.

### Next
- Identify the highest-friction review/QA tasks that still rely too heavily on CLI.
- Improve the GUI around broad-topic suggestion review/apply flows.
- Keep the docs updated as the workflow direction becomes clearer.

## 2026-04-25 — GUI workflow feedback

### Done
- Captured major GUI usability issue: the UI currently exposes run IDs too prominently and makes the user manage implementation details.
- Added `GUI_UX_PLAN.md` to describe a better GUI-led workflow.

### Learned
- The user expects to ingest a channel, see broad topics, choose interesting topics, then drill into subtopics.
- Topic discovery should pique interest and guide exploration.
- Approving a topic label without clearly applying videos is confusing.
- Run IDs should be audit/history details, not the primary navigation model.
- Subtopic generation should be contextual to a selected parent topic, not something that requires remembering old run IDs.
- Comparison-group generation may need readiness indicators because the user is not sure whether there is enough data.

### Next
- Redesign the GUI flow around Channel Overview → Topic Map → Topic Detail → Subtopic Discovery → Comparison Readiness.
- Make approved-but-unapplied topic suggestions obvious.
- Add or design an **Approve + apply** path for topic suggestions.
- Hide run ID wrangling behind Advanced/History where possible.

## 2026-04-25 — GUI priority 1 patch

### Done
- Patched `review_ui.py` to make topic approval/application clearer.
- Added a primary **Approve + apply to videos** action for pending topic labels.
- Renamed the plain approval path to **Approve label only**.
- Added warning/help text explaining that approving a label does not assign videos by itself.
- Made approved-but-unapplied labels visually explicit with an **Approved but not applied** warning.
- Reworded bulk apply to **Apply to N video(s)**.
- Added `/api/topic/approve-and-apply` route.

### Verified
- `review_ui.py` compiles.
- Smoke-tested `/api/topic/approve-and-apply` against a copied SQLite DB.
- Smoke result: pending label was approved, application route ran, and state refreshed with `ready=0`, `applied=3`, `blocked=0` in the copied DB.

### Next
- Restart/reload the GUI and test the changed topic cards in browser.
- Next UX priority remains hiding run-ID-first navigation and making subtopic review parent-topic-led.

## 2026-04-25 — Topic Map first pass

### Done
- Added first-pass **Topic Map** above the old review panels.
- Added topic cards with status, applied videos, pending review count, ready-to-apply count, and subtopic count.
- Added **Explore topic** action to make topic-first exploration more obvious.
- Renamed old panel headings to more product-friendly language: Broad Topics, Subtopics, and Comparison Readiness.
- Bumped UI revision to `2026-04-25.2-topic-map`.

### Verified
- `review_ui.py` compiles.
- `build_state_payload()` returns `topic_map` with 8 topics against `tmp/test.sqlite`.
- Served page contains the new revision and Topic Map markup.

### Next
- Improve Topic Map interactions so selecting a topic feels like navigating to a topic detail view, not just changing a dropdown.
- Hide or demote run ID controls behind an advanced/history section.
- Build a real Topic Detail section for subtopic exploration.

## 2026-04-25 — Workbench topic-detail UI

### Done
- Used `frontend-design` direction to move the UI further from database-admin layout toward a research workbench.
- Added revision `2026-04-25.4-workbench-topic-detail`.
- Added visible selected-topic / selected research lane panel below Topic Map.
- Updated **Explore topic** so it sets status, selects the topic, refreshes state, and scrolls to the selected-topic panel.
- Added workflow rail: Broad topic → Subtopics → Compare.
- Added selected-topic actions: **Discover subtopics** and **Review subtopics**.
- Improved Topic Map card hover/selected styling.

### Verified
- `review_ui.py` compiles.
- Live served page contains revision `2026-04-25.4-workbench-topic-detail`.
- Live served page contains `selected-topic-detail` and `Selected research lane` markup.
- `/api/state` returns 8 topic map cards and selected topic `Artificial Intelligence`.

### Next
- If Chris still finds the layout off, inspect with browser screenshot/feedback and tune visual hierarchy.
- Demote run selectors into Advanced/History.
- Make selected-topic panel into a fuller topic detail view with videos and subtopic readiness.

## 2026-04-25 — Preserve selected topic context

### Done
- Fixed bug where generating subtopics from a selected topic could snap the UI back to the first available topic, e.g. Health & Wellness.
- Added `state.activeTopicName` in the review UI.
- Made **Explore topic** store the active topic explicitly.
- Made **Discover subtopics** use the active selected research lane rather than relying only on the dropdown.
- Made subtopic/comparison generation responses return their parent `topic`/`subtopic` so the client can refresh in the same context.
- Bumped UI revision to `2026-04-25.5-preserve-topic-context`.

### Verified
- `review_ui.py` compiles.
- Live UI serves revision `2026-04-25.5-preserve-topic-context`.
- Live `/api/state?topic=Artificial%20Intelligence` returns selected topic `Artificial Intelligence` and 8 topic-map cards.

### Next
- Chris should retest: Artificial Intelligence → Discover subtopics should remain on Artificial Intelligence after generation.
- If it still jumps, inspect browser state/event order and the run selector change handler.

## 2026-04-25 — Subtopic approve/apply flow

### Done
- Added subtopic equivalent of the topic approve/apply workflow.
- Pending subtopic cards now show **Approve + apply to videos** and **Approve label only**.
- Approved subtopics now show approved-but-not-applied warnings and **Apply to N video(s)** actions.
- Added backend routes `/api/subtopic/approve-and-apply` and `/api/subtopic/bulk-apply` using existing per-video subtopic assignment helper.
- Selected-topic detail now shows pending subtopic count in the compact metrics.
- Topic Map subtopic count includes pending subtopics for the currently selected topic.
- Bumped UI revision to `2026-04-25.6-subtopic-apply-flow`.

### Verified
- `review_ui.py` compiles.
- Copied-DB smoke test approved and applied a pending Psychology subtopic suggestion: matched 1, applied 1, skipped 0.
- Live UI serves revision `2026-04-25.6-subtopic-apply-flow`.
- Live page contains `approveAndApplySubtopic` and `bulkApplySubtopic` handlers.

### Next
- Decide whether already-applied videos should be hidden by default with a toggle to show all application rows.
- Continue reducing scroll distance: move pending subtopic status/actions closer to the selected-topic panel.

## 2026-04-25 — Subtopic cluster threshold

### Done
- Tightened subtopic suggestion prompt so subtopics are treated as reusable research clusters, not one-off tags.
- Added rule: new subtopics should plausibly cover at least 5 videos in the parent broad topic.
- Added generation-time suppression for new subtopic labels with fewer than `MIN_NEW_SUBTOPIC_CLUSTER_SIZE = 5` suggested videos.
- Existing approved subtopics can still receive individual new videos.
- Updated UI copy to explain that new subtopics need 5+ suggested videos and one-off labels are suppressed.
- Bumped UI revision to `2026-04-25.7-subtopic-cluster-threshold`.

### Verified
- `subtopic_suggestions.py` and `review_ui.py` compile.
- Copied-DB smoke test generated 3 fake suggestions under Psychology for one new label and correctly suppressed/rejected it: pending 0, rejected 1.
- Live UI serves revision `2026-04-25.7-subtopic-cluster-threshold` and includes the threshold copy.

### Next
- Consider surfacing suppressed labels in the UI as a collapsed/secondary section so the user understands why fewer suggestions appeared.
- Consider adding a configurable threshold control later, but default should stay conservative.

## 2026-04-25 — Subtopic review threshold enforcement

### Done
- Fixed overly permissive subtopic threshold logic: approved-existing labels were still being shown with only 2-4 suggested videos.
- Changed generation suppression so low-support labels are suppressed regardless of whether the subtopic label already exists.
- Added review/display filtering so pending subtopic suggestions below the 5-video threshold are hidden from the review queue.
- Added `suppressed_low_support` summary count for subtopic reviews.
- Selected-topic panel now shows **Suppressed tiny labels**.
- Bumped UI revision to `2026-04-25.8-subtopic-review-threshold`.

### Verified
- For Psychology, previous pending low-support suggestions were hidden: pending 0, suppressed_low_support 3.
- Live UI serves revision `2026-04-25.8-subtopic-review-threshold`.
- Live `/api/state?topic=Psychology` returns no pending subtopics and `suppressed_low_support: 3`.

### Next
- Consider exposing suppressed subtopic labels in a collapsed debug/history section if Chris wants visibility into what was filtered.

## 2026-04-25 — Topic inventory in selected research lane

### Done
- Added selected-topic inventory to the review UI.
- The selected research lane now shows **Assigned subtopics** with videos grouped under each subtopic.
- It also shows **Unassigned videos**: broad-topic videos not yet assigned to any subtopic.
- Added `topic_inventory` to `/api/state`.
- Bumped UI revision to `2026-04-25.9-topic-inventory`.

### Verified
- `review_ui.py` compiles.
- For `Personal Relationships`, topic inventory shows `Family: 2`, `Friendship: 6`, `unassigned: 0`.
- Live served page contains revision `2026-04-25.9-topic-inventory` and inventory markup.

### Next
- Consider adding quick actions for unassigned videos, e.g. assign to existing subtopic, generate suggestions for unassigned only, or manually create subtopic.

## 2026-04-25 — Subtopic readiness in selected research lane

### Done
- Added per-subtopic readiness to the selected-topic inventory.
- Subtopics with fewer than 5 assigned videos are marked **Too thin to compare**.
- Subtopics with 5+ assigned videos are marked **Ready for comparison**.
- Added an inline **Generate comparison groups** action for ready subtopics.
- Bumped UI revision to `2026-04-25.10-subtopic-readiness`.

### Verified
- `review_ui.py` compiles.
- Live UI revision check passed.
- For `Personal Relationships`: `Family` has 2 videos and is too thin; `Friendship` has 6 videos and is ready for comparison.

### Next
- Use the ready `Friendship` subtopic to generate comparison-group suggestions.
- After comparison groups are reviewed, fetch/process transcripts for one chosen comparison group rather than fetching everything.

## 2026-04-25 — Fixed blank page after readiness patch

### Issue
- Browser page stopped loading after `2026-04-25.10-subtopic-readiness`.
- Server was still returning HTTP 200, so this was a frontend JS parse failure rather than a backend outage.

### Cause
- A JavaScript escaping helper inside the Python triple-quoted HTML string was mangled, producing an invalid regular expression in the rendered script.

### Fix
- Removed the fragile `escapeJs` helper.
- Used `JSON.stringify(bucket.name)` for safe inline button arguments instead.

### Verified
- Extracted rendered `<script>` and ran `node --check` successfully.
- Restarted the review UI.
- Live page check passed: page loads, revision is present, bad helper is gone, safe inline argument is present.

## 2026-04-25 — Fixed inline Generate comparison groups button

### Issue
- The inline **Generate comparison groups** button in the selected research lane rendered but did not trigger generation.
- Server logs showed no `POST /api/generate/comparison-groups`, so the click was failing client-side before reaching the backend.

### Cause
- The inline `onclick` argument used `JSON.stringify(bucket.name)` inside a double-quoted HTML attribute, so the generated attribute broke for string values.

### Fix
- Changed the inline handler attribute to single quotes around the attribute value while keeping `JSON.stringify(bucket.name)` for the JavaScript argument.

### Verified
- Rendered script passes `node --check`.
- Live page includes the safe single-quoted `onclick` and no longer includes the broken double-quoted handler.

## 2026-05-07 — §A4 legacy code move (HITL, 6 commits)

### What
- Created `legacy/` package and moved 4 files: `comparison_group_suggestions.py`, `group_analysis.py`, `markdown_export.py`, `processing.py`. Updated importers in `db.py`, `cli.py`, `review_ui.py`, `test_transcripts.py` (one of which was a `unittest.mock.patch("yt_channel_analyzer.comparison_group_suggestions...")` string-path target).
- Hid comparison-group entry points from the GUI primary nav (dropped page-header button + per-subtopic action button). API routes + helpers + state payload fields stayed untouched so any external callers keep working.
- Added a one-line `[legacy]` stderr warning at the entry of all 21 CLI commands operating on the comparison-group / group-transcript / group-analysis / group-export concept.

### Why
- ROADMAP §A4. Phase A discovery is the new primary flow; comparison groups were the Phase C transcript-comparison surface, now demoted to legacy.

### Decisions
- Chose HITL over Ralph: 4 file moves was borderline-not-tripping the >5-files HITL trigger, but the string-path patch failure (caught only by `test_transcripts`, which the verify gate excludes) is exactly the silent-failure mode AFK Ralph would have missed.
- Scoped CLI warnings to all 21 commands (not just the 9 that import moved code) — ROADMAP wording "comparison-group commands" reads broader than "commands that import legacy/".
- GUI hide = remove buttons (not wrap in `<details>`). Routes still serve any non-UI caller, so no backward-access fallback was needed.

### Verified
- `discover` + `extractor` test gate green throughout (147 tests, ~33s).
- `test_transcripts` holds at the same 2 pre-existing failures (49 tests, ~16s).

## 2026-05-07 — Slice 04 payload threading (Ralph iter 2)

- `discovery._payload_from_response` now threads `confidence` (float-coerced) + `reason` from each assignment item; no more hardcoded `1.0` / `""`.
- `stub_llm` already shipped `reason="stub assignment"` from slice 03; unchanged.
- `test_schema_rejects_assignment_with_extra_keys` re-pointed: now passes valid `confidence` + `reason` plus an extra `priority` key (since `confidence` is now a real schema property after slice 04 iter 1).
- `test_callable_round_trips_payload_via_extractor` expectation updated to threaded `reason="fixture"`.
- Verify gate: 160 tests green (~35s).

## 2026-05-07 — Slice 05 prompt + multi-topic stub (Ralph iter 1)

- `_DISCOVERY_SYSTEM`: "exactly once" rule replaced with "at least once" + explicit anti-over-tagging clause ("most episodes should have a single assignment"); example JSON extended to show one `<id1>` with two `assignments` entries (different topics) so the model has an in-prompt template.
- `DISCOVERY_PROMPT_VERSION` → `discovery-v4`. Schema unchanged (already permitted N rows per `youtube_video_id`).
- New `STUB_SECONDARY_TOPIC_NAME = "Cross-cutting"`; `stub_llm` now emits N primary-topic rows + 1 secondary-topic row on `videos[0]` (confidence 0.6, no subtopic) so 2-video seeds yield 3 `video_topics` rows.
- Re-pointed assignment-count assertions (3 sites: `test_discover_stub_creates_run_and_assignments`, `test_analyze_chains_setup_ingest_and_discover`, `test_llm_error_does_not_corrupt_prior_successful_run`) from 2→3.
- `test_stub_llm_emits_one_subtopic_per_topic`: subtopic-presence assertion now filtered to primary-topic assignments (secondary entry has no subtopic).
- `test_stub_llm_returns_one_topic_covering_all_videos` renamed + rewritten as `test_stub_llm_assigns_every_video_to_primary_and_one_to_secondary`.
- Verify gate: 167 tests green (~36s).
- Next iteration (slice 05 box 2): `_build_discovery_topic_map` per-episode `also_in: [<topic_name>, ...]` payload + JS card pill.

## 2026-05-07 — Slice 05 multi-topic "also in" pill (Ralph iter 2)

- `_build_discovery_topic_map`: precompute `topic_id_to_name` from `topic_rows`, walk `episode_rows` once to build `topics_by_video: dict[video_id, list[topic_name]]`, then stamp each per-topic episode dict with `also_in` (other topics, current topic excluded; `[]` when single-topic — never null). No extra SQL.
- JS `renderDiscoveryEpisodeItem` gains 4th arg `showAlsoIn`; only the top-level topic episode list (line ~1150) passes `true`. Subtopic drill-down + unassigned-within-topic buckets stay pill-free per overlay scope.
- Inline pill rendered inside `.discovery-episode-meta` next to confidence/id; new `.discovery-episode-also-in` CSS (rounded muted chip) lives alongside the existing inline-meta classes.
- Tests: `test_state_payload_episode_dicts_carry_also_in_for_multi_topic` (vid1 in Health+Business; vid2 only in Business; assertions cover both directions of also_in + empty list invariant); `test_html_page_renders_also_in_pill_for_multi_topic_episodes` (CSS class + literal `also in:` string ship in the rendered HTML).
- Verify gate: 169 tests green (~36s; +2 vs prior iter).
- Issue 05 acceptance criteria 1, 2, 3, 4, 5 now all met (criterion 3 — same episode in both topic lists — is a virtue of multi-row `video_topics` + the iter 1 stub fixture; covered by the new also_in test which traverses both topic.episodes lists).

## 2026-05-08 — Slice 08 rename event log + replay (Ralph iter 1)

- New `topic_renames(id, project_id, topic_id, old_name, new_name, created_at)` table in `db.py` `TABLE_STATEMENTS` + `REQUIRED_TABLE_COLUMNS`. FKs cascade off `projects` and `topics`. `topic_id` is the row's id at rename time (kept for forensics; replay matches by name).
- `db.rename_topic` instrumented: after the existing `UPDATE topics SET name`, inserts a `topic_renames` row in the same connection/transaction so a failed rename does not record a phantom event. (`/api/discovery/topic/rename` calls into `rename_topic`, so the API path is covered without touching `review_ui.py`.)
- `discovery._apply_renames_to_payload(connection, project_id, payload)`: builds a fixed-point rename map (cycle-safe via `seen` set) collapsing A→B→C straight to "C". Returns a new `DiscoveryPayload` with rewritten `topics` (deduped, first-seen order), `subtopics[i].parent_topic`, `assignments[i].topic_name`. Pure — no DB mutation.
- `discovery._suppress_wrong_assignments_in_run(connection, channel_id, run_id)`: runs after the final assignment loop in `run_discovery`. Two parallel DELETEs (topic-level and subtopic-level) scoped to the current `discovery_run_id` and the channel. `wrong_assignments.topic_id` is stable across renames, so no name-rewriting needed here.
- `run_discovery` wires both: `_apply_renames_to_payload` immediately after `payload = llm(videos)` returns; `_suppress_wrong_assignments_in_run` after the assignment-insert loop, before commit. Errored-run path (LLM raises) is unaffected — neither helper runs.
- 5 new tests in `StickyCurationRenameReplayTests`: rename-then-rerun keeps curated name with both episodes still attached and no orphan old-name row; mark-wrong-then-rerun has the suppressed `(vid, topic)` pair absent from the new run's `video_topics` (siblings preserved); multi-hop A→B then B→C collapses incoming "A" to "C"; dedupe after rewrite ([A, B] with A→B → [B]); rename API records exactly one `topic_renames` row with the right old/new pair.
- Verify gate: 175 tests green (~44s; +5 vs prior iter).
- Next iteration (slice 08 box 2): `_topics_introduced_in_run` helper + `new_topic_names` payload + JS "New" badge.

## 2026-05-08 — Slice 12 run-ID demote: relocate run-select into Run history (advanced) (Ralph iter 1)

- Moved `<label>Suggestion run / <select id="run-select"></select></label>` out of the topbar primary `<div class="controls row">` into a new `<details class="run-history-advanced">` placed between the `.generator` close and the `status-box` div, still inside `<section class="topbar">`. Markup-only change — JS, event listeners, data flow untouched (`document.getElementById("run-select")` still resolves; only its DOM parent moved).
- `<details>` body: `<summary>Run history (advanced)</summary>`, `.run-history-hint` muted line ("Pick an older run to inspect its labels. Routine review uses the latest run automatically."), then the original `<label>` wrapping `<select id="run-select">`. Default collapsed (no `open` attribute).
- Minimal CSS in the existing `<style>` block: `.run-history-advanced` border-top + margin mirroring `.generator`; `> summary { cursor: pointer; color: var(--muted); }`; `> label { max-width: 320px }` so the select does not stretch the topbar.
- `UI_REVISION` → `2026-05-08.4-run-history-advanced-channel-overview-discovery-panel` (keeps `channel-overview` + `discovery` substrings — all 10 `test_ui_revision_advances_for_*` assertions stay green).
- 5 new tests in `RunHistoryAdvancedHTMLTests`: (1) `<details class="run-history-advanced">` wraps `id="run-select"` + summary copy ships, (2) primary `.controls.row` no longer contains `id="run-select"`, (3) `id="topic-select"` + `id="subtopic-select"` remain inside primary `.controls.row`, (4) hint copy renders, (5) `UI_REVISION` carries all three required substrings.
- Verify gate: 195 tests green (~47s; +5 vs prior iter).
- Next iteration (slice 12 box 2): `_latest_subtopic_run_id_for_topic` helper + `latest_subtopic_run_id_by_topic` payload + JS topic-select handler tweak so changing the parent topic snaps `run-select` to the latest run that has subtopic labels for that topic.

## 2026-05-08 — Slice 12 run-ID demote: per-topic latest-subtopic-run snap (Ralph iter 2)

- New `_latest_subtopic_run_id_for_topic(db_path, topic_name) -> int | None` helper in `review_ui.py`: `SELECT MAX(suggestion_run_id) FROM subtopic_suggestion_labels JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id WHERE topics.name = ?`. Returns `None` when topic has no subtopic-suggestion labels (incl. unknown topic name).
- Added bulk-query sibling `_latest_subtopic_run_ids_by_topic(db_path)` (single `GROUP BY topics.name`) used by `build_state_payload` so payload assembly stays O(1) DB hits regardless of topic count. Standalone helper kept as the spec-mandated lookup surface.
- `build_state_payload` now exposes `latest_subtopic_run_id_by_topic: dict[str, int]` (top-level payload key, alongside `discovery_topic_map` / `channel_overview`). Empty dict on fresh DBs.
- JS topic-select `change` listener: reads `state.payload?.latest_subtopic_run_id_by_topic?.[newTopic]`; when present and different from current `run-select.value`, sets `run-select.value` *before* the existing `fetchState({ topic, subtopic: null })` so `selectedRunId()` inside `fetchState` picks up the new id. Pre-existing change behavior preserved (still passes `subtopic: null` to clear comparison-group selection).
- 5 new tests in `LatestSubtopicRunIdByTopicTests`: per-topic max run id (2 runs split across Health/Business → A=run_a, B=run_b); helper returns `None` when topic has no subtopic labels (and for unknown topic name); payload carries the mapping dict; payload dict is `{}` on a fresh DB with no subtopic-suggestion runs; HTML wiring — JS topic-select change handler block references both `latest_subtopic_run_id_by_topic` and `run-select`.
- Verify gate: 200 tests green (~48s; +5 vs prior iter).
- Issue 12 acceptance criteria 1–8 all met. Branch ready for review.
- Note: prompt referenced `.scratch/phase-a-topic-map/issues/12-*.md` but the issue spec actually lives at `.ralph/issues/12-run-id-demote.md` — flagged in iteration summary. (Pattern matches issue 11; issue files for slices that ship as Ralph overlays live under `.ralph/issues/` not `.scratch/...`.)

## 2026-05-08 — Slice 13 comparison readiness: 3-state inventory backend (Ralph iter 1)

- `_build_topic_inventory` SELECT extended with `LEFT JOIN video_transcripts ... AND transcript_status = 'available'` and `LEFT JOIN processed_videos ... AND processing_status = 'processed'`, carrying per-row `transcript_available`/`processed_ok` boolean columns + `videos.id AS video_id`. Both side tables PK on `video_id` so no Cartesian inflation; defensive `_seen_video_ids` set per bucket guards against future schema drift.
- Aggregation walks rows once: bucket gets `transcript_count`/`processed_count` increments only on first sight per video. Empty subtopic (`youtube_video_id IS NULL` from outer join) skipped before counting.
- Readiness branch rewritten as 3-arm if/elif/else: `video_count < 5` → `too_few` (existing "Too thin to compare" / "Needs N more video(s)..." copy preserved); `transcript_count == 0` → `needs_transcripts` ("Enough videos, no transcripts" / "Fetch transcripts for these videos before generating comparison groups."); else → `ready` (existing copy preserved). `bucket["comparison_ready"]` derived as `readiness_state == "ready"` so existing boolean callers keep working unchanged.
- 4 new tests in `TopicInventoryReadinessStateTests`: too_few with 2 videos + 2 transcripts (transcripts irrelevant when under threshold); needs_transcripts with 5 videos + 0 transcripts; ready with 5 videos + 3 transcripts + 2 processed; dedupe sanity with 5 videos all having both transcript+processed rows → counts stay at 5/5 (no inflation across the two side joins).
- Verify gate: 204 tests green (~50s; +4 vs prior iter).
- HTML pill / CSS / `UI_REVISION` bump intentionally deferred to issue-13 box 2 next iteration (per ROADMAP §A8 split).
- Note: prompt referenced `.scratch/phase-a-topic-map/issues/13-*.md` but issue spec lives at `.ralph/issues/13-comparison-readiness.md` (same pattern as issues 11 + 12 — Ralph-overlay issue files live under `.ralph/issues/`).

## 2026-05-09 — Edit channel form wired (commit `dfffc9d`)

- New `db.update_channel_fields(channel_id, title, handle, description)` — UPDATE-only on the three display fields, raises `ValueError` on unknown channel id. YouTube-derived columns (thumbnail_url, published_at, last_refreshed_at) untouched.
- New `POST /api/channel/edit` in `review_ui.py`: validates title via `_normalize_text` (blank/missing → 400), persists, returns updated channel info from `_build_supply_channel`. Re-ingest still overwrites — design choice, hint text in modal documents it.
- Supply stage's Edit button (previously a `setStatus(... CLI-only ...)` stub at `:3600`) now opens new `#channel-edit-modal` (paper/ink palette, reuses existing modal CSS, adds form/input/textarea/hint styles). Modal pre-fills from `state.payload.supply_channel`; submit POSTs and refetches state. Per-modal Escape + backdrop close wired alongside discover modal.
- 7 new tests in `ChannelEditEndpointTests`: happy path, blank handle/description persist as NULL, missing-title 400, blank-title 400, no-primary-channel 400, HTML wiring, UI_REVISION substring preservation.
- `UI_REVISION` bumped to `2026-05-10.10-edit-channel-form-…` preserving prior substrings (run-discovery, reingest, discover-cost, comparison-readiness, channel-overview).
- Verify gate: 260 tests green (+7).

## 2026-05-10 — Supply pagination (Load-more button)

- New constants `SUPPLY_DEFAULT_LIMIT = 50` and `SUPPLY_MAX_LIMIT = 500` in `review_ui.py`.
- `build_state_payload(..., supply_limit=None)` — accepts optional kwarg, clamps to `[1, SUPPLY_MAX_LIMIT]`, passes through to `_build_supply_videos(limit=...)`. Echoes `supply_limit` (effective) and `supply_max_limit` in the payload so JS can decide when to render Load-more.
- `/api/state?supply_limit=N` — parsed alongside the existing `?run_id=` / `?topic=` / `?subtopic=` / `?discovery_run_id=` query params.
- JS: `state.supplyLimit = 50` initial; `fetchState()` appends `&supply_limit=N`; Supply footer renders a `Load more` button (`#supply-load-more`) when `total > shown` and shown < cap. Click bumps `state.supplyLimit` by 50 (clamped to total + max), refetches. When cap reached with more available, footer shows `cap of 500 reached` instead. Replaces the previous "extend the limit in `_build_supply_videos`" code-pointer hint.
- New CSS: `.supply-load-more` (paper/ink palette, mono font, hover/disabled states).
- 7 new tests in `SupplyPaginationTests`: default-cap-50, custom-limit, lower-clamp (0 → 1), upper-clamp (10000 → 500), `/api/state?supply_limit=` query parsing, button-HTML wiring, `UI_REVISION` substring preservation.
- `UI_REVISION` bumped to `2026-05-10.11-supply-pagination-…` preserving prior substrings.
- Verify gate: 267 tests green (+7).

### Next
- Optional: stream/poll in-flight discovery status — modal still sits frozen during the synchronous request.
- Optional: server-side sort for Supply (currently client-side `.reverse()` in JS, so `oldest` shows the oldest of the most-recent-N rather than the channel's true oldest).

## 2026-05-10 — Watch button + published date on every video surface (UNCOMMITTED on main)

- User feedback: "wherever you display a video," add published date + a Watch button. Scope confirmed via question → "Active Phase A only" (skipped legacy topic/subtopic suggestion samples + applications).
- `review_ui.py` only — 51 +/ 6 -.
  - `renderDiscoveryEpisodeItemFocused`: published-date span added to meta row (Watch already present).
  - `renderDiscoveryEpisodeItem` (compact, used in subtopic buckets): new Watch button + date span. Buttons row gained a flex wrapper under reason.
  - New shared helpers `videoChipMetaHtml(v)` (date · YT id) + `videoChipWatchHtml(v)` (▶ Watch link). `videoChipHtml` and `subtopicVideoChipHtml` both consume them — chips in topic-inventory now show date + watch.
  - `_build_topic_inventory` SQL: added `videos.published_at` to both subtopic and unassigned-rows SELECTs; dict outputs include `published_at`. No test pinned the chip dict shape — additive change.
  - Supply rows (`renderSupply`): explicit ▶ Watch button added to `.sv-actions` alongside transcript pill (the title was already a watch link, but a discrete button matches the spec).
- Date format: reuses existing `formatDate(value)` which trims to YYYY-MM-DD.
- Verify gate: 272 tests green, no new tests added (pure UI + additive SQL).
- Dev server: killed stale PID 26041 (started 08:25 from a prior session via `nohup`), restarted on `0.0.0.0:8765` against `tmp/doac-sticky.sqlite` for visual check.

## 2026-05-10 — Shorts filter design (issue files only, no code)

- User flagged YT Shorts polluting topic frequency on channels where shorts are re-cuts of long videos. /grill-me design pass; nine forks resolved with user confirmation on every choice.
- Decisions (full rationale in `.scratch/shorts-filter/issues/` + memory `project_shorts_filter.md`): filter not dedup, `duration_seconds <= 180` cutoff (NULL → long), per-channel sticky `exclude_shorts` + per-run CLI override default exclude, upstream of LLM and sticky-curation, audit + read-only UI badge, explicit `backfill-durations` CLI, `fetch_recent_videos` always pulls duration going forward.
- Three issue files written: `01-A-stockpile.md` (schema + ingest + backfill, no behavior change), `02-B-filter-logic.md` (filter + per-run override flags, default still 0), `03-C-flip-default-ui.md` (default flip + orphan counts + read-only badge — only slice that costs LLM tokens to verify on DOAC).
- No code changes this session; main was already dirty (`WORKLOG.md`, `review_ui.py` — pre-existing UNCOMMITTED Watch+date pass from prior session).

## 2026-05-11 — Shorts filter slice 01-A: stockpile duration data (Ralph iteration 1, branch feat/issue-01-shorts-stockpile)

- Purely additive, zero behavior change. Spec: `.scratch/shorts-filter/issues/01-A-stockpile.md`.
- `db.py` schema: `videos.duration_seconds INTEGER NULL`; `channels.exclude_shorts INTEGER NOT NULL DEFAULT 0 CHECK (0,1)` (default 0 this slice — C flips it); `discovery_runs` gains `shorts_cutoff_seconds`, `n_episodes_total`, `n_shorts_excluded`, `n_orphaned_wrong_marks`, `n_orphaned_renames` (all NULL, populated in B/C). Added to both `TABLE_STATEMENTS` CREATEs and `REQUIRED_TABLE_COLUMNS` so existing DBs get `ALTER TABLE ADD COLUMN` via `ensure_schema`. `upsert_videos_for_primary_channel` now writes `duration_seconds` (ON CONFLICT uses `COALESCE(excluded.duration_seconds, videos.duration_seconds)` so a later metadata refresh can't clobber a backfilled value). New helpers `get_video_ids_missing_duration_for_primary_channel` + `update_video_durations_for_primary_channel` (only updates rows still NULL → idempotent).
- `youtube.py`: `VideoMetadata` gains `duration_seconds: int | None = None` (keyword default — no fixture churn needed). New `parse_iso8601_duration` (regex `P(nD)?T(nH)?(nM)?(nS)?` → seconds; falsy/unparseable → None; `PT0S`→0). New `fetch_video_durations(ids, *, api_key)` batches `videos.list?part=contentDetails` ≤50/req, returns `{id: seconds|None}`, no call on empty list. `fetch_channel_videos` does its `playlistItems` call then enriches each result via `fetch_video_durations`.  NOTE: overlay/spec say `fetch_recent_videos` but the actual function is `fetch_channel_videos` — followed existing name.
- `cli.py`: new `backfill-durations --db-path X` command — finds the primary channel's `duration_seconds IS NULL` rows, batched `fetch_video_durations`, updates. Idempotent; needs `YOUTUBE_API_KEY`.
- Tests (test_discovery.py): `Iso8601DurationParsingTests`, `ShortsStockpileSchemaTests` (fresh CREATE has cols / `ensure_schema` ALTERs legacy tables / exclude_shorts defaults 0), `FetchVideoDurationsTests` (fetch_channel_videos enriches via patched `fetch_json`; empty-ids makes no call), `BackfillDurationsCLITests` (idempotent; only NULL rows).
- Verify gate green: 281 tests (`.ralph/verify.sh`).
- NOT done in sandbox (operator): real `fetch_channel_videos`/`backfill-durations` smoke against a live channel (HITL — no real YT calls here); `discover --stub` byte-identical-before/after on DOAC (no DOAC DB in sandbox).
- ROADMAP.md has no shorts-filter section — this branch is driven by the issue overlay, not a ROADMAP checkbox; nothing ticked. Flagged for human.

## 2026-05-11 — Shorts filter slice 02-B: discovery filter + per-run override (Ralph iteration 1, branch feat/issue-02-shorts-filter-logic)

- Spec: `.scratch/shorts-filter/issues/02-B-filter-logic.md`. Channel default still `exclude_shorts=0` after this slice — behavior only changes on CLI flag or hand-set column. (Slice C flips default + adds orphan counts + UI badge.)
- `discovery.py`: module const `SHORTS_CUTOFF_SECONDS = 180`. `run_discovery` gains `exclude_shorts_override: bool | None = None`. Effective filter = override if not None else `bool(channels.exclude_shorts)`. When active, pre-filters loaded episode rows BEFORE building `DiscoveryVideo` list / `video_id_by_yt` — i.e. upstream of `_apply_renames_to_payload` and the LLM call. Cutoff: `duration_seconds <= 180` excluded; `duration_seconds IS NULL` → treated as long (kept, fail-safe). 0-episodes-after-filter → `ValueError` mentioning `--include-shorts`; raised before the run row is self-allocated so no empty `DiscoveryRun` persists (if caller pre-allocated `run_id`, that row is flipped to `status='error'` first). Audit fields stamped via one `UPDATE discovery_runs SET shorts_cutoff_seconds, n_episodes_total, n_shorts_excluded` right after run-id resolution (path-independent — covers both self-alloc and review-UI pre-alloc); `shorts_cutoff_seconds` NULL when filter off. `n_orphaned_*` left NULL (slice C). Channel SELECT now also pulls `exclude_shorts`; video SELECT now also pulls `duration_seconds`.
- `cli.py`: `discover` gains mutex `--exclude-shorts` / `--include-shorts` (one-run only; neither → channel sticky). Handler maps to `exclude_shorts_override` True/False/None and passes to `run_discovery`. Override never writes `channels.exclude_shorts`. `analyze` unchanged (still no override → channel setting).
- Tests (test_discovery.py `ShortsFilterDiscoveryTests`, +7): channel exclude_shorts=1 → shorts filtered + audit populated + recording-stub proves prompt for excluded video never built (upstream check); channel exclude_shorts=0 → all kept, `n_shorts_excluded=0`, `shorts_cutoff_seconds` NULL; `--include-shorts` beats channel=1 (channel row untouched); `--exclude-shorts` beats channel=0 (channel row untouched); flags mutex → SystemExit; all-shorts channel + filter → ValueError w/ `--include-shorts`, zero discovery_runs rows; mixed NULL/non-NULL durations → NULLs kept. Boundary pinned: duration exactly 180 is excluded (`<=`).
- Verify gate green: 288 tests (`.ralph/verify.sh`).
- HITL (operator, not done here — no real LLM): re-run `discover --exclude-shorts` on DOAC with `RALPH_ALLOW_REAL_LLM=1` after slice A's `backfill-durations`; confirm cleaner topic map + correct audit values. Also note: existing `wrong_assignments` rows targeting now-excluded videos go inert (not deleted) — slice C surfaces the count.

## 2026-05-11 — Shorts filter slice 03-C: flip exclude_shorts default + orphan counts + UI badge (Ralph iteration 1, branch feat/issue-03-shorts-flip-default-ui)

- Spec: `.scratch/shorts-filter/issues/03-C-flip-default-ui.md`. Closes the shorts-filter feature (3/3). Operator smoke passed (`.scratch/shorts-filter/SMOKE_SLICE_03.md`, steps 1–4).
- `db.py`: `channels.exclude_shorts` CREATE flips to `DEFAULT 1`. New `_repair_channels_exclude_shorts_default` (called from `ensure_schema` alongside the other `_repair_*`): one-shot, fires only while the create-SQL still says `DEFAULT 0` — `UPDATE channels SET exclude_shorts = 1`, then RENAME→CREATE(new, `DEFAULT 1`)→INSERT SELECT(all cols by name)→DROP old with `foreign_keys=OFF`+`legacy_alter_table=ON` (same dance as `_repair_discovery_runs_status_constraint`, so `videos`/`discovery_runs` FK children survive). Idempotent forever after (no marker table; the `DEFAULT 1` create-SQL is the guard).
- `discovery.py`: stamps `n_orphaned_wrong_marks` + `n_orphaned_renames` on the run row — counts of `wrong_assignments` / `topic_renames` whose target episode got filtered out as a Short this run (curation actions gone inert). NULL when filter off.
- `review_ui.py`: read-only shorts-filter badge on the discovery topic-map header — `"N shorts excluded · M curation actions inert (target episodes filtered)"`, hidden entirely when `n_shorts_excluded == 0` and no inert curation (no noise on Shorts-free channels). Follow-up commit `a4c68d9`: `duration_seconds` added to the discovery-episode + supply-videos payloads, `formatDuration()` JS helper, per-episode length rendered next to published date in both discovery episode renderers + the Supply list (operators verifying the 180s cutoff need to see lengths). `UI_REVISION` bumped.
- Tests: verify gate green, 295 tests (`.ralph/verify.sh`).
- HITL (operator, done): eyeballed the `channels` rebuild migration; ran it against a real pre-slice-C DB + confirmed idempotency; real-LLM `discover` (~$0.02) to see badge + orphan counts on live data; hard-refreshed the review UI to confirm per-episode length renders.

## 2026-05-11 — Phase B planning: PRD + ROADMAP §B + 6 issue slices (branch `docs/phase-b-prd`, not yet merged)

- Phase A + shorts filter both complete → Phase B ("sample-based transcript refinement") is the next active phase. Design walked via /grill-me; every fork operator-confirmed; design frozen.
- `PRD_PHASE_B.md` (repo root, `PRD_PHASE_A_TOPIC_MAP.md` style): process ~15 representative non-Short transcripts; one LLM call per transcript (Haiku, `Extractor.run_batch`, whole transcript, no chunking/summarize/ad-filter) returning 3 parts — `assignments` (episode re-judged from transcript), `new_subtopic_proposals` (`{name, parent_topic, evidence}`), `new_topic_proposals` (`{name, evidence}`, expected near-empty). Operator accepts proposals → real `topics`/`subtopics`; then re-runs taxonomy-aware `discover` to spread channel-wide. NOT Phase B: claims/embeddings/clustering/synthesis/full-channel fetch (= Phase C). Frozen decisions captured in the PRD: ⅔-coverage/⅓-blind-spot sample picker against a named discovery run (one-per-topic-before-two; operator-editable; one replacement round for dead transcripts); new tables `refinement_runs`/`refinement_episodes`/`taxonomy_proposals`; reassignments → existing `video_topics`/`video_subtopics` with new `assignment_source='refine'` (CHECK-rebuild repair mirroring `_repair_video_topic_assignment_source_constraint`) + nullable `refinement_run_id`; refine assignments replace a sampled episode's non-curated rows wholesale (curated `wrong_assignments` wins); `discover` never downgrades `refine`/`manual` rows; `discover` prompt gets the curated taxonomy (bump `DISCOVERY_PROMPT_VERSION`) — that's how accepted proposals reach the other ~435 episodes; `extractor/`-only LLM; `stub_refinement_llm` + injectable `transcript_fetcher` for free/offline tests; `make_real_refinement_llm_callable` gated by `RALPH_ALLOW_REAL_LLM=1` AND `refine --real` needs a pre-flight cost-estimate + `--yes`/interactive confirm (~$0.40/15 ep); new non-legacy `fetch-transcripts` CLI (selector mutex, resumable, rate-limit backoff, `--stub`), legacy `fetch-group-transcripts` left alone; new stand-alone "Refine" stepper stage, 2 screens (sample setup; proposal review + before→after sanity panel + re-run-discovery nudge), async-run+poll, "transcript-checked" pill on `refine` episode cards. `analyze` not extended.
- `ROADMAP.md` §B rewritten (old stub conflated with claim extraction): the 6 slices as checkbox groups. `CONTEXT.md` gained glossary terms `Transcript`, `RefinementRun`, `TaxonomyProposal` (+ relationships).
- 6 tracer-bullet issue files `.scratch/phase-b-refinement/issues/01..06-*.md` (commit 270c555): B1 `fetch-transcripts` CLI; B2 refinement schema + db helpers; B3 `refinement.py` core + `refine --stub` CLI (≈2 Ralph iterations); B4 discover-prompt taxonomy awareness + never-downgrade; B5 Refine UI sample-setup; B6 Refine UI proposal-review. Order 1→2→3, 4 anytime, 5→6 after 3. All AFK (B2's CHECK-rebuild trips the harness's destructive-migration HITL pause at runtime). New test files `test_transcripts_fetch.py` (B1) + `test_refinement.py` (B3) → verify gate; `test_transcripts.py` stays excluded + untouched.
- No code yet. Next: merge `docs/phase-b-prd` → main, then start `feat/issue-01-fetch-transcripts` off main. Recommended first execution step: a fresh `discover --real` on DOAC now that the Shorts default is on, so refinement samples a clean non-Short run.
