# 09 — Sort options + low-confidence visual styling

Status: needs-triage
Type: AFK
User stories covered: 14, 15
Roadmap sections: §A3

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

The episode list within a subtopic (or topic, when no subtopic is selected) gains sort options: by recency, by view count, by confidence. Default sort is recency.

Episode cards with low-confidence assignments (below a configurable threshold, e.g. 0.5) are visually de-emphasised — faded styling, muted colours, or a "low confidence" badge. The user can spot which assignments to review first.

## Acceptance criteria

- [x] Sort dropdown on episode lists with options: recency, confidence (high → low). View-count option deferred — see Decisions.
- [x] Default sort resets to recency on reload — see Decisions.
- [x] Low-confidence assignments (confidence < threshold) render visually distinct (faded opacity 0.55 + bad-coloured confidence text on `.discovery-episode.low`).
- [x] Threshold is configurable via env var `YTA_LOW_CONFIDENCE_THRESHOLD` (default 0.5; values outside [0,1] / non-numeric / blank fall back to default).
- [x] Test: `DiscoveryLowConfidenceThresholdTests` in `test_discovery.py` seeds a 0.2/0.5/0.9 mixed-confidence fixture and asserts `_low_confidence_class` returns `low` for sub-threshold rows; HTML test guards against regressing to the prior dual-threshold (`0.33`/`0.66`, `very-low`) styling.

## Decisions

### Sort persistence

Per-topic sort selection lives in an in-memory JS `Map` (`discoveryEpisodeSortByTopic`) keyed by topic name. It is **not** persisted to `localStorage` or to the server, so a page reload resets every topic's dropdown to the default (`recency`).

Why this side of the implementer's-choice fork:

- Cheapest to ship and reversible — adding `localStorage` later is a strict superset, no schema or API churn.
- Phase A is a single-user personal-use app on one machine; cross-session persistence has low value relative to the work.
- Topic names are the persistence key, but topic names mutate via rename/merge/split. The in-memory map already handles rename (re-keys) and merge/split (drops the source). Persisting through those events would need extra care, and that complexity is unwarranted before the user actually asks for cross-session memory.

### View-count sort option

The acceptance criterion called for a third sort option (view count). Deferred: `videos.view_count` is not currently populated by ingestion (`youtube.py` does not fetch it). Adding the option now would render as a no-op (every value tied at 0). The dropdown ships with two real options — recency and confidence — and the third returns when ingestion learns to fetch view counts.

## Blocked by

- Slice 04 (need confidence values to sort by and to drive styling)
