# YouTube Channel Analyzer — GUI UX Plan

## Why this exists

The current GUI exposes too much of the internal suggestion-run machinery.

Run IDs are useful for audit/history, but they should not be the main thing the user has to manage. The user wants to explore a channel, discover broad topics, choose what is interesting, then drill into subtopics and comparison groups.

The GUI should guide that research flow instead of making the user wrangle implementation details.

---

## Current UX problem

### Run IDs are leaking into the main workflow

Observed behaviour:
- clicking **generate topic suggestions** creates a run ID
- approving topics works, but it is not obvious that the suggested video assignments must also be applied
- clicking **generate subtopics** creates a separate subtopic run ID
- the subtopic dropdown can look incomplete unless the user selects a previous run ID
- after many runs, it will become hard to remember which run ID belongs to which parent topic or review moment

### Why this is bad

The user is trying to answer research questions:
- what broad topics exist in this channel?
- which topics are worth digging into?
- what subtopics exist under a selected topic?
- is there enough material to create useful comparison groups?

Instead, the UI currently asks them to think like the database:
- which suggestion run ID am I looking at?
- did I approve the label but forget to apply the videos?
- do I need to jump back to a previous run to see the right options?

That is clunky and will get worse as more runs accumulate.

---

## Desired UX model

The GUI should feel like a staged research workbench:

1. **Ingest channel**
2. **Discover broad topics**
3. **Review topic map**
4. **Choose a topic to explore**
5. **Discover/review subtopics for that topic**
6. **Choose a subtopic to explore**
7. **Decide whether there is enough data for comparison groups**
8. **Fetch transcripts and analyse selected groups**

The user should not need to manually manage run IDs for routine work.

---

## Design principle: hide run IDs by default

Run IDs should become an advanced/history/audit concept.

Default UI should show:
- latest active suggestion set
- parent topic/subtopic context
- status counts
- clear next actions
- what has been approved but not applied
- what is ready to explore next

Run IDs can still be available under:
- History
- Advanced
- Audit details
- Compare previous runs

---

## Proposed top-level GUI sections

### 1. Channel Overview

Purpose: orient the user.

Show:
- channel name
- video count
- transcript count
- processed video count
- topic count
- subtopic count
- comparison group count
- latest suggestion activity

Primary actions:
- ingest/fetch videos
- generate topic discovery
- go to Topic Map

---

### 2. Topic Discovery / Topic Map

Purpose: show broad-topic discovery results in a way that piques interest.

Show broad topics as cards/table rows:
- topic name
- number of suggested videos
- number of approved/applied videos
- pending suggestions count
- confidence/reasoning summary if available
- whether topic has subtopics
- whether topic is ready to explore

Primary actions:
- approve topic label
- approve and apply topic label to suggested videos
- reject/rename topic label
- open topic detail

Important UX fix:
Approving a topic label and applying videos should not feel like disconnected hidden steps.

Possible options:
- button: **Approve label + apply suggested videos**
- after approving: show banner **Approved but not applied — apply to N videos?**
- status column: **approved label, 0 videos applied** warning

---

### 3. Topic Detail

Purpose: let the user decide whether a broad topic is worth digging into.

Show:
- topic name
- applied videos under this topic
- candidate videos from pending suggestions
- existing subtopics
- coverage summary
- whether there is enough data to generate useful subtopics

Primary actions:
- generate subtopic suggestions for this topic
- review subtopic suggestions
- manually create subtopic
- open videos in this topic

Important UX fix:
Subtopic generation should be started from a selected parent topic, not from a free-floating run ID mental model.

---

### 4. Subtopic Discovery

Purpose: review subtopics under one parent topic.

Show:
- parent broad topic clearly
- latest relevant subtopic suggestion set for that topic
- approved subtopics
- pending subtopic labels
- video counts per subtopic
- unassigned videos under parent topic

Primary actions:
- approve/apply subtopic suggestion
- reject/rename subtopic suggestion
- create manual subtopic
- open subtopic detail

Run selector should be hidden or secondary:
- default to latest relevant run for the selected parent topic
- show history only if user opens advanced/history

---

### 5. Subtopic Detail / Comparison Readiness

Purpose: decide whether there is enough material for comparison groups.

Show:
- videos assigned to subtopic
- how many have transcripts
- how many are processed
- possible comparison angles
- whether data is currently too thin

Primary actions:
- generate comparison-group suggestions when enough videos exist
- manually create comparison group
- fetch transcripts for selected group
- analyse group

---

## Specific UX changes to build first

### Priority 1 — Topic approval/apply clarity

Problem:
User can approve a topic label without realising videos still need to be applied.

Build:
- make approved-but-unapplied state visible
- add combined action: **Approve + apply to videos**
- after label approval, show next-step prompt to apply

### Priority 2 — Replace run-ID-first navigation

Problem:
User has to remember run IDs to see relevant suggestions.

Build:
- default each view to the latest relevant run for the current context
- for subtopics, select by parent topic first, then latest run
- move run ID selector into an Advanced/History area

### Priority 3 — Topic map as the exploration hub

Problem:
User wants broad-topic discovery to help decide where to dig in.

Build:
- topic cards/table with counts and status
- clear indication of which topics have enough videos to explore
- action to open topic detail and generate subtopics

### Priority 4 — Comparison readiness guardrails

Problem:
User is unsure if there is enough data for comparison groups.

Build:
- show video count per subtopic/group
- show transcript/processed coverage
- label states like:
  - too few videos
  - enough videos, no transcripts
  - ready for comparison

---

## Working rule for future GUI development

If a normal user has to understand run IDs to proceed, the GUI is wrong.

Run IDs should support auditability, not drive the main workflow.
