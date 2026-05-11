# 01 — `fetch-transcripts` CLI + non-legacy transcript fetch path

Status: done (2026-05-11 — see WORKLOG / ROADMAP §B1)
Type: AFK
Branch: `feat/issue-01-fetch-transcripts`
Spec: `PRD_PHASE_B.md` (module 2 — "Transcript fetch"; the six slices §B1); `ROADMAP.md` §B
User stories covered: 13, 16, 18

## Context

Phase B refines the topic map from a sample of transcripts; Phase C will fetch transcripts channel-wide. Both need a transcript fetcher. The only transcript-fetch entry point today is the legacy `fetch-group-transcripts` command, which is scoped to comparison groups — the retired pre-pivot framing CLAUDE.md forbids extending. This slice builds the non-legacy general fetcher. It reuses the existing `video_transcripts` table and `youtube.fetch_video_transcript` (which already takes an injectable `transcript_fetcher`); no schema change, no LLM, no refinement logic.

This is the first vertical slice of Phase B: CLI → `youtube` fetch → DB → stdout. Shippable on its own.

## What to build

- **CLI `fetch-transcripts --db-path X [--video-ids a,b,c | --missing-only | --limit N | --refinement-run-id R] [--stub]`** in `cli.py`. The four selectors are mutually exclusive; no selector → error (don't accidentally fetch hundreds). `--db-path` only (operates on the primary channel, like `show-channels`); no `--project-name`.
  - `--missing-only` = every primary-channel video with no `video_transcripts` row, or a row whose status is retryable (`rate_limited` / `request_failed` / `error`).
  - `--limit N` = the N most-recent primary-channel videos still missing a transcript.
  - `--video-ids` = exactly those YouTube video IDs (must belong to the primary channel).
  - `--refinement-run-id R` = exactly the episodes recorded for that `refinement_runs` row (this selector is a no-op until slice B2/B3 land the table; the flag can be wired but reject with a clear message if the table doesn't exist yet — or land it in B3. Implementer's call; keep it out of B1's acceptance criteria).
- **Fetch loop**: sequential, small fixed inter-request sleep (the `youtube-transcript-api` path is IP-throttled, no API key). On a `rate_limited` classification, exponential capped backoff and continue. Persist each result immediately via `db.upsert_video_transcript` so a killed run resumes cleanly with `--missing-only`. No `--force` re-fetch of `available` rows; no parallelism.
- **Output**: one line per video (`<id> | <status> | <source> | <language>`), closing tally (`available: N, disabled: N, not_found: N, ...`).
- **`--stub`**: uses a built-in fake fetcher that returns `TranscriptRecord(status="available", source="generated", text="<stub transcript for ...>")` for any ID — for wiring sanity with no network.
- **Injectable fetcher**: the command (and the underlying helper) accept a `transcript_fetcher` parameter so tests pass a fake; `--stub` is just the CLI surfacing of that.
- **Leave `fetch-group-transcripts` exactly as-is** — not deleted, not redirected, not extended.
- **No schema change** — `video_transcripts` already has the right columns and status vocab.
- **Tests**: new file `test_transcripts_fetch.py` (must NOT be `test_transcripts.py` — that name is the gate-excluded legacy file). Add it to `.ralph/verify.sh`'s default targets. Cover: each selector resolves the right set; `--missing-only` skips `available` rows and includes retryable ones; results persisted; resume works; rate-limit backoff invoked (with a fake that raises a rate-limit-classified exception once then succeeds); the no-selector error; `--stub` writes `available` rows without touching the network.
- Cheatsheet entry for `fetch-transcripts` (§1, current — not `[legacy]`). WORKLOG entry.

## Acceptance criteria

- [x] `fetch-transcripts --db-path X --missing-only` fetches and persists transcripts for exactly the primary-channel videos missing one (or with a retryable status), prints per-video status + a tally, and is safely re-runnable (already-`available` rows untouched).
- [x] `--video-ids` and `--limit` selectors work; supplying none, or more than one, errors clearly (argparse mutex → exit 2).
- [x] A killed run resumed with `--missing-only` picks up where it left off (no duplicate work, no lost rows) — each result `upsert`ed immediately.
- [x] A `rate_limited` result triggers backoff-and-continue, not a crash (exp-capped backoff, retry same video up to `max_rate_limit_retries`, then record `rate_limited` and move on).
- [x] `--stub` populates `available` rows with placeholder text and makes no network call; `run_fetch_transcripts` accepts an injectable `transcript_fetcher` used by the tests.
- [x] `fetch-group-transcripts` is byte-unchanged.
- [x] No schema change. `test_transcripts_fetch.py` (17 tests) is in the verify gate and green (312 total); `test_transcripts.py` is untouched and still excluded.
- [n/a] `--refinement-run-id` — wired as a flag, errors cleanly until B2/B3 land the `refinement_runs` table (kept out of B1's acceptance per the spec).

## Blocked by

None — can start immediately (branch off `main`).
