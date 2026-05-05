# 00 — Build the Extractor Module

Status: needs-triage
Type: AFK
User stories covered: foundational (no PRD story directly; enables all LLM work in Phase A and beyond)

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)
[`docs/adr/0001-extractor-module.md`](../../../docs/adr/0001-extractor-module.md)
[`CONTEXT.md`](../../../CONTEXT.md) — see **Extractor**

## What to build

A new Python package `extractor/` that owns LLM-call mechanics for the entire project. This is a prerequisite for slice 02 (real LLM discovery) and for every future LLM consumer (Phase C `claim_extraction.py`, Phase D `query_synthesis.py`).

End-to-end behaviour: a caller registers a typed prompt at import time (template render function + JSON schema + system message + semver version), then calls the Extractor's `run_one(name, version, context)` or `run_batch(jobs, progress_callback=None)`. The Extractor handles provider invocation (Anthropic only for now), structured-output validation, retry once on malformed response, the synchronous batch facade over the provider's async batch API (submit → poll → return), automatic fallback to sequential calls below a small batch threshold, and an audit row in the new `llm_calls` table per call.

This issue does NOT consume the Extractor — it just builds it. Slice 02 is the first real consumer.

## Acceptance criteria

- [ ] `extractor/` package exists with the deep Module type and the prompt registry mechanism (decorator-based registration at import time)
- [ ] `run_one(name, version, context) → ParsedResult` works: builds prompt, calls provider, validates response against schema, retries once on parse failure, returns the validated parsed result
- [ ] `run_batch(jobs, progress_callback=None) → list[ParsedResult]` works: above a threshold, submits to the provider's batch API and polls to completion; below threshold, falls back to sequential `run_one` calls; calls `progress_callback(done, total)` when supplied
- [ ] Both entry points enforce: prompt must be registered, version must be pinned, schema validation is strict, retry happens once
- [ ] Each call writes a row to a new `llm_calls` table with: prompt name, semver version, content hash of (rendered prompt + schema + system message), model, provider, is_batch, batch_size, parse_status (`ok` / `retry` / `failed`), tokens in/out, cost_estimate_usd, correlation_id (nullable foreign key)
- [ ] `FakeLLMRunner` adapter ships in the package: tests register canned `(name, version) → response_dict` mappings; the fake validates each canned response against the schema (so tests can't drift from the schema); records calls for assertion
- [ ] Schema migration adds the `llm_calls` table; idempotent
- [ ] Tests cover: happy path single-call; happy path batch above threshold (mocked batch API); batch below threshold falls back to sequential; malformed response retries once then errors; FakeLLMRunner round-trip; audit row written for every call type
- [ ] No real provider API calls in CI

## Out of scope (deliberately)

- Multi-provider abstraction. One real adapter (Anthropic) plus the fake. Adding a second provider later is a new issue.
- Async batch lifecycle exposed to callers (submit / poll / fetch). The synchronous facade is the only public batch entry point.
- Job-level orchestration (deciding what to run, how to chunk inputs). That stays in consumers like `discovery.py`.
- Prompts themselves. This issue builds the registry mechanism; actual discovery prompts ship in slice 02.

## Blocked by

None — can start immediately.
