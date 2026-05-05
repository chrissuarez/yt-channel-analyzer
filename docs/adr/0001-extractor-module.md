# 0001 — Extractor as a deep Module owning LLM-call mechanics

Status: accepted (2026-05-04)

## Decision

LLM-call mechanics for this project live in a single deep Module called **Extractor** (Python package `extractor/`). Callers — currently `discovery.py`, later `claim_extraction.py` in Phase C and `query_synthesis.py` in Phase D — register typed prompts with a semver version, then invoke `run_one(...)` or `run_batch(...)` against the Extractor. The Extractor hides provider client lifecycle, structured-output validation, retry-once-on-malformed, prompt versioning, the synchronous-batch facade over the provider's async batch API, and audit logging to a new `llm_calls` table.

## Why

Three LLM-calling modules already exist (`topic_suggestions.py`, `subtopic_suggestions.py`, `comparison_group_suggestions.py`) and each duplicates client retrieval, prompt construction, schema, validation, and error handling. None has retry or prompt versioning, both of which the Phase A PRD requires. Building `discovery.py` as a fourth duplicate would compound the problem; building it on top of an Extractor pays back across every future LLM consumer in the project.

## Considered alternatives

- **(i) Single-call only, caller batches** — rejected: leaks the provider's batch API into every caller; defeats the purpose.
- **(iii) Async batch lifecycle (submit/poll/fetch exposed)** — rejected: needed flexibility (queue management, fire-and-forget) is theoretical for a single-user single-channel tool. *One adapter is hypothetical.* Add it when a real second use case appears.
- **Whole extraction-job orchestration behind the Seam** — rejected: discovery batches across episodes, claim extraction across transcript chunks; orchestration shapes are too different to share. Each consumer keeps orchestration; only the call mechanic and prompt registry are shared.
- **File-based or DB-backed prompt registry** — rejected: single-user project, no need for non-developer prompt editing; pure Python prompts are type-checkable and evolve in git.

## Consequences

- A new `extractor/` package and a new `llm_calls` SQLite table are introduced before slice 02 of Phase A can begin. A new Issue 00 captures this prerequisite.
- The legacy `topic_suggestions.py` and `subtopic_suggestions.py` remain on disk during the transition (still called by the old review surfaces) but new work goes through the Extractor. They join `legacy/` once the new topic-map UI replaces their callers.
- Multi-provider support is **not** designed in. One real adapter (Anthropic) plus one fake adapter (`FakeLLMRunner` for tests) is the entire production surface. Adding a second provider later is an interface evolution, not a precondition.
- All LLM cost and behaviour for this project becomes queryable from a single table (`llm_calls`) from day one.
