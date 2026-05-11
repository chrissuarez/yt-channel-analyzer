# yt_channel_analyzer

The single-context glossary for this project. Use these terms exactly in code, issue titles, prompts, and PRDs. When tempted by a synonym, check the `_Avoid_` list first.

## Language

**Channel**:
The single YouTube source the project ingests from. One channel per project.
_Avoid_: Account, source.

**Episode**:
A single video on the **Channel**. The project is for podcast-style channels, so we say **Episode**, not "Video," even where the underlying YouTube API uses "video."
_Avoid_: Video (in domain talk), entry, item.

**Topic**:
A broad subject bucket auto-discovered by the **Extractor** and curated by the user. Examples: Health, Money, Politics. Coarse and reusable across many **Episodes**.
_Avoid_: Category, tag, label.

**Subtopic**:
A finer bucket nested under a **Topic**. Examples: Gut Health, Sleep (under Health). One **Topic** has many **Subtopics**.
_Avoid_: Sub-category, sub-tag.

**Assignment**:
The link between an **Episode** and a **Topic** (or **Subtopic**). Carries a confidence score (0.0–1.0), a short reason string, and a source (`auto` from a **DiscoveryRun**, or `manual` from **Curation**). One **Episode** can have many **Assignments**.
_Avoid_: Tag, label, classification.

**DiscoveryRun**:
One execution of the **Extractor** against a **Channel**'s **Episode** metadata that produces a proposed set of **Topics**, **Subtopics**, and **Assignments**. Re-running discovery produces a new **DiscoveryRun** without overwriting prior **Curation**.
_Avoid_: Suggestion run, analysis pass.

**Curation**:
The user's edits to the auto-discovered map: rename, merge, split, move-Episode-between-Subtopics, mark-Assignment-wrong. Curation events are sticky across **DiscoveryRuns**.
_Avoid_: Review, manual override, correction.

**TopicMap**:
The user-facing artifact: the set of curated **Topics** + **Subtopics** + **Assignments** for a **Channel** at a given moment. The MVP product is a browseable **TopicMap**.
_Avoid_: Taxonomy (too generic), categorisation.

**Extractor**:
The deep Module that owns LLM-call mechanics: provider invocation, structured-output validation, retry, prompt versioning, batch facade, and audit logging. Callers (currently `discovery.py` and — Phase B — `refinement.py`; later `claim_extraction.py` in Phase C) supply a registered prompt + typed context and receive a validated `ParsedResult`. The Module hides the LLM provider behind its **Seam** so callers don't know or care which model ran the call.
_Avoid_: LLM client, prompt runner, AI helper.

**Transcript** _(Phase B onward)_:
The fetched-and-stored text of an **Episode** (from `youtube-transcript-api`), with a status (`available`/`disabled`/`not_found`/…) and a source (`manual`/`generated`). Lives in the `video_transcripts` table. Phase B fetches transcripts for a refinement sample; Phase C fetches them channel-wide.
_Avoid_: caption file, subtitles, VTT.

**RefinementRun** _(Phase B onward)_:
One sample-based refinement pass: a chosen subset of an **Episode** set is transcribed, each **Transcript** is sent to the **Extractor**, and the run produces transcript-grade **Assignments** (`source = "refine"`) for the sampled **Episodes** plus a set of **TaxonomyProposals**. Recorded in `refinement_runs` (mirrors **DiscoveryRun**). A new run never overwrites an old one.
_Avoid_: refresh, re-scan, sample run.

**TaxonomyProposal** _(Phase B onward)_:
A proposed new **Topic** or **Subtopic** surfaced by a **RefinementRun** from transcript evidence, carrying the parent **Topic** (for subtopic proposals), an evidence snippet, the source **Episode**, and a status (`pending`/`accepted`/`rejected`). Accepting one creates the real **Topic**/**Subtopic**; it is then subject to the same **Curation** stickiness as any other taxonomy node.
_Avoid_: suggestion, candidate topic, tag proposal.

**Claim** _(Phase C onward)_:
An atomic piece of advice, opinion, factual statement, or anecdote extracted from an **Episode** transcript, carrying its **Topic**/**Subtopic**, the speaking guest, the source **Episode**, and a timestamp. Phase C will introduce a `claims` table; Phase D queries operate over **Claim** clusters.
_Avoid_: Insight, snippet, quote (too narrow), statement (too vague).

## Relationships

- A **Channel** has many **Episodes**.
- An **Episode** has many **Assignments** (multi-topic membership is the default).
- An **Assignment** links an **Episode** to one **Topic** or **Subtopic**.
- A **Topic** has many **Subtopics**.
- A **DiscoveryRun** produces many **Assignments** (`source = "auto"`).
- **Curation** events overlay **Assignments** and survive subsequent **DiscoveryRuns**.
- The **Extractor** is invoked by `discovery.py` to produce a **DiscoveryRun**'s contents, by `refinement.py` (Phase B) to produce a **RefinementRun**'s contents, and (later) by `claim_extraction.py` to produce **Claims**.
- An **Episode** has at most one **Transcript**. A **RefinementRun** consumes the **Transcripts** of its sampled **Episodes** and emits **TaxonomyProposals** plus `source = "refine"` **Assignments**. Accepting a **TaxonomyProposal** adds a **Topic**/**Subtopic** that a later **DiscoveryRun** then populates channel-wide.

## Example dialogue

> **Chris:** "When I rename a **Topic** the **Extractor** proposed, does that survive the next **DiscoveryRun**?"
> **Designer:** "Yes — rename is a **Curation** event recorded against the **TopicMap**. The next **DiscoveryRun** is diffed against the current curated state; it never silently overwrites."
> **Chris:** "And if a new **Episode** drops, will the **Extractor** assign it to the renamed **Topic**?"
> **Designer:** "It assigns to the *underlying* **Topic** identity. The user-visible name is whatever the latest **Curation** set it to."

## Flagged ambiguities

- **"Video" vs "Episode"** — the existing schema and code use "video" / `videos` table. New domain talk should say **Episode**. Schema rename is deferred (would touch ~600KB of code) but new modules and UI copy use **Episode**. Resolved: **Episode** is the domain term; `videos` is the historical implementation name.
- **"Tag" vs "Label" vs "Topic"** — early code uses "label" for the textual name of a **Topic**. New work uses **Topic** (the entity) and "topic name" (the string). Resolved: **Topic** is the entity; "label" is retired in new code.
- **"Comparison Group"** — pre-pivot concept; user-curated grouping of **Episodes** for transcript comparison. Retired by the 2026-05-04 vision pivot. Code lives in `legacy/`. Do not introduce it in new modules.
