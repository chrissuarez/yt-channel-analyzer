# AGENTS.md — yt_channel_analyzer

Configuration for AI coding agents working in this project.

## Project at a glance

`yt_channel_analyzer` is a personal-use podcast knowledge extractor. It ingests a single YouTube channel (canonical case: *Diary of a CEO*), auto-discovers a topic map from episode metadata, and (in later phases) extracts claims from transcripts to surface consensus, conflict, and standout advice across guests.

For the current vision, phased plan, and architectural decisions, read in this order:

1. `PROJECT_SUMMARY.md`
2. `CURRENT_STATE.md`
3. `ROADMAP.md`
4. `PRD_PHASE_A_TOPIC_MAP.md`
5. `WORKLOG.md` (most recent entries)

## Agent skills

### Issue tracker

Issues live as markdown files under `.scratch/<feature-slug>/issues/`. PRDs live at `.scratch/<feature-slug>/PRD.md` (or at the repo root for cross-cutting PRDs like the Phase A one). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the project root. Both created lazily as terms and decisions get resolved. See `docs/agents/domain.md`.
