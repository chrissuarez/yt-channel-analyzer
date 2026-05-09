# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first when picking up cold

The project's vision and current focus live in markdown — these are not duplicated below:

1. `PROJECT_SUMMARY.md` — what the product is and the four-phase plan.
2. `CURRENT_STATE.md` — what's built, what's next, current tensions.
3. `ROADMAP.md` — phased plan with checkboxes that drive Ralph iteration units.
4. `PRD_PHASE_A_TOPIC_MAP.md` — Phase A spec.
5. `WORKLOG.md` — recent terse iteration entries (skim last ~10).
6. `CONTEXT.md` — **binding domain glossary**. Use these terms exactly in code, prompts, and PRDs (`Channel`, `Episode`, `Topic`, `Subtopic`, `Assignment`, `DiscoveryRun`, `Curation`, `TopicMap`, `Extractor`, `Claim`). The `_Avoid_` list is enforced — e.g., say `Episode` in domain talk even though the underlying schema uses the historical `videos` table.
7. `docs/adr/0001-extractor-module.md` — the one architectural decision currently captured.

## Package layout — non-obvious bit

This directory **is** the `yt_channel_analyzer` Python package. Code imports itself as `yt_channel_analyzer.X`, so the package's parent (`/home/chris/.openclaw/workspace`) must be on `PYTHONPATH` and is the working directory for tests and the CLI. Hence the `cd ..` and `PYTHONPATH=.` patterns below — they are not optional.

A `.venv` lives at the parent (`~/.openclaw/workspace/.venv`), not inside this dir. `Anthropic` and `google-api-python-client` are installed there but are not pinned in any requirements file (none exists).

## Verify gate (the test command)

The canonical test command, used by both humans and the Ralph harness:

```bash
.ralph/verify.sh
```

That script `cd`s to the parent dir and runs `python3 -m unittest -q yt_channel_analyzer.test_discovery yt_channel_analyzer.test_extractor` (~200 tests, ~20s, currently green).

`test_transcripts.py` is **deliberately excluded** from the default gate — it has 2 pre-existing failures that cover the soon-to-be-`legacy/` Phase C surface. Don't try to "fix" by widening the gate without addressing those failures first.

Override targets via positional args or env var:

```bash
.ralph/verify.sh yt_channel_analyzer.test_discovery
RALPH_VERIFY_TARGETS="yt_channel_analyzer.test_extractor" .ralph/verify.sh
```

Run a single test:

```bash
cd .. && python3 -m unittest yt_channel_analyzer.test_discovery.DiscoveryRunTests.test_foo
```

## CLI

Activate the venv and load `.env` (for `ANTHROPIC_API_KEY` / `YOUTUBE_API_KEY`) before running anything:

```bash
cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a
```

The Phase A primary path is `discover` + `serve-review-ui`. The full per-command reference is `YT_ANALYZER_CHEATSHEET.md` (§1 is current; §§4, 6, 7, 8, 9 are legacy/Phase C and print `[legacy]` warnings). The end-to-end recipe is `docs/operator-workflow.md`.

Two non-obvious CLI rules:

- **`discover` and `analyze` require `--stub|--real` as a mutex.** Stub is free + deterministic (no API call); use it for wiring sanity checks. Real mode additionally requires `RALPH_ALLOW_REAL_LLM=1` in the environment — `make_real_llm_callable()` enforces this and fails fast before any API call. A real DOAC run is ~$0.019 / 15 episodes / Haiku 4.5.
- **Default port is 8765**, not 8000 (a few docs still say 8000 — pre-existing doc bug).

When serving the UI to another machine on the LAN, pass `--host 0.0.0.0`; the default `127.0.0.1` is local-only.

## Architecture — the bits that span files

- **SQLite is the source of truth** for everything (schema, taxonomy, audit log, eventually vectors via `sqlite-vec`). One DB per channel/project. `db.py` (~5K lines) owns schema + queries; `db.connect()` + `db.ensure_schema()` are the entry points. New tables added by recent slices: `discovery_runs`, `topic_renames` (curation event log), `wrong_assignments`, `llm_calls`.
- **`extractor/` is the only LLM-call module.** All callers (currently `discovery.py`; later `claim_extraction.py` in Phase C) register typed prompts and call `runner.run_one`/`run_batch`. Provider lifecycle, structured-output validation, retry-once, and audit logging to `llm_calls` live here. Don't add a second LLM-calling module — extend the registry instead. See ADR 0001.
- **`discovery.py` is the Phase A pipeline.** `run_discovery(db_path, llm)` takes an injected `LLMCallable` (so tests pass `stub_llm` directly — see below), produces a `DiscoveryRun` with topics + subtopics + per-episode multi-topic `Assignments` carrying confidence + reason. Sticky-curation fixed-point chain (`_apply_renames_to_payload`) replays user renames + suppresses wrong-marked assignments before persisting, so the user's curation survives re-runs.
- **`discovery.stub_llm` matches the `LLMCallable` signature directly.** Pass it to `run_discovery` for any free wiring sanity check — no need to mock `AnthropicRunner`. Use this pattern for any future smoke needing cheap pre-flight validation.
- **`review_ui.py` is the curation GUI** (~3K lines, single file, stdlib `http.server`). `build_state_payload()` is the JSON envelope every page render hangs off; the discovery-topic-map block is the primary view (channel overview → topics → subtopics → episodes with confidence/reason/`also_in` pills). `_DISCOVERY_*` and `_TOPIC_*` helpers shape the payload.
- **`legacy/` is Phase C dormant code** (`comparison_group_suggestions.py`, `group_analysis.py`, `markdown_export.py`, `processing.py`). CLI commands that touch it print `[legacy]` warnings via `_warn_legacy()`. **Do not introduce comparison-group concepts in new code.** That framing was retired by the 2026-05-04 vision pivot.
- **`youtube.py`, `topic_suggestions.py`, `subtopic_suggestions.py`** are pre-pivot ingestion + suggestion machinery. Reused as-is or repurposed; don't rebuild.

## Conventions

- **Branch naming:** `feat/issue-NN-<slug>`. The Ralph harness regex-parses the issue number from the branch — no env var or CLI arg. New iterations should branch off `main`.
- **Issue files:** `.scratch/<feature-slug>/issues/NN-<slug>.md`. PRDs at `.scratch/<feature-slug>/PRD.md` (or repo root for cross-cutting). Per-issue Ralph overlays at `.ralph/issues/NN-<slug>.md`. Stale overlays should be deleted post-merge.
- **WORKLOG entries:** terse, sacrifice grammar for concision; future iterations skim them to skip exploration.
- **Comments:** when extending `extractor/schema.py`, additive validators must be no-op when their key is absent so existing schemas stay green.
- **JSON parse failures:** Haiku tends to wrap JSON in code fences despite instructions otherwise — `runner._strip_code_fence` handles this. If you hit a parse failure on a paid call, check raw text for fences first.

## Ralph harness (`.ralph/`)

Per-issue branches drive a Ralph loop. Two drivers:

- `.ralph/ralph-once.sh` — one HITL iteration.
- `.ralph/afk-ralph.sh` — up to `MAX_ITER` (default 8) unattended iterations, with clean-tree pre-flight, verify gate per iteration, per-iteration backup tag `ralph/iter-N-pre-<ts>` (local, never pushed), stream-json + jq filtering, logs in `.ralph/logs/` (gitignored).

Both refuse to run unless `HEAD` matches `^feat/issue-([0-9]+[a-z]?)-`.

The iteration contract is `.ralph/PROMPT.md` — including the `<ralph>...</ralph>` sigil protocol (`CONTINUE` / `COMPLETE` / `HITL_PAUSE: <reason>` / `BLOCKED: <reason>`) and HITL pause triggers (real LLM, real YouTube fetch, destructive migration, >5 file moves, governance-doc edits, >300-line edits to `review_ui.py`, test deletion).

Sandbox-blocked operations are unblocked via `--allowedTools` flags duplicated in both drivers (search for `INNER_ALLOWED_TOOLS`). If you add a sandbox-blocked command, edit both. If the list grows past ~6 entries, that's the signal to refactor sandbox handling wrapper-side.
