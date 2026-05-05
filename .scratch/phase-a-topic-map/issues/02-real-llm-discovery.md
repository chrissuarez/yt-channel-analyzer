# 02 — Real LLM discovery replaces the stub

Status: needs-triage
Type: AFK
User stories covered: 2, 19

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

Replace the hardcoded LLM stub from slice 01 with a real batched LLM call against a cheap model (Claude Haiku 4.5 or GPT-4o-mini). The discovery module assembles a single batched prompt from all episode titles + descriptions + chapter markers for the channel, calls the model, validates the JSON response shape, retries once on malformed response, and persists topics + per-episode topic assignments.

The prompt template carries a version string written into `discovery_runs.prompt_version` so old runs are traceable.

Subtopics, confidence, multi-topic, and reason fields stay out — they ship in slices 03, 04, 05. This slice produces broad topics + single topic per episode only.

## Acceptance criteria

- [ ] `discovery.py` makes a real batched LLM call (provider configured via env var; model defaulted to a cheap one)
- [ ] Prompt is assembled from titles + descriptions + chapter markers; basic boilerplate (sponsor reads, common CTAs) pre-filtered from descriptions
- [ ] Response is validated against an expected JSON schema; one retry on parse failure; on second failure the run is marked errored and no partial state is persisted
- [ ] `discovery_runs.prompt_version` is populated
- [ ] Smoke test on a small real channel (10–20 episodes) produces a credible topic list
- [ ] Cost-tracking note recorded somewhere (run cost, token count) for later reference
- [ ] Unit tests for the discovery module use canned LLM responses (don't hit the real API in CI)

## Blocked by

- Slice 00 (the Extractor Module — discovery is the first real consumer)
- Slice 01 (the wire and schema this replaces the stub in)

## Note

With the Extractor in place (slice 00), this slice no longer has to invent prompt construction, response validation, retry, prompt versioning, or audit logging. It registers a `discovery` prompt with the Extractor's registry and calls `run_batch(...)`. The slice's work is the prompt itself, the typed context dataclass it consumes, and the persistence of results into `discovery_runs` / `video_topics` / `video_subtopics`.
