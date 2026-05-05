# YouTube Channel Analyzer — Project Summary

## Purpose

`yt_channel_analyzer` turns a long-running podcast-style YouTube channel (canonical example: *Diary of a CEO*) into a structured, browseable, queryable knowledge asset.

It exists because:
- It is hard to remember the great advice that comes out of a long backlog of podcast episodes.
- It is hard to know which episodes are worth your time.
- The interesting content lives **across** guests — the consensus, the disagreements, the standout advice — and a single linear watch history cannot surface that.

The app is a **knowledge extractor**, not a transcript collector and not a manual curation workbench.

---

## Product vision

The app should help answer questions like:
- What broad topics does this channel cover, and what subtopics sit underneath?
- Which episodes belong to a topic I care about right now?
- Across all guests who spoke about a given topic, what did they **agree on**, what did they **disagree on**, and what was the **most useful advice**?
- Long-term: given a free-form question ("I want better career direction", "how do I improve my gut health"), what does the channel's collective body of advice say?

The user-facing product is the **answer to those questions**. The taxonomy, claim store, and embeddings are internal scaffolding the app uses — they should not be the thing the user manages.

---

## Operating model

- One channel per project (MVP). Multi-channel is a later concern.
- SQLite is the source of truth for everything: ingested metadata, topic taxonomy, claim store, embeddings.
- `sqlite-vec` extension keeps vectors in the same file as the rest of the data — no separate vector DB to operate.
- AI suggests, the human curates. AI proposals are reviewable; nothing the AI does is authoritative until approved.
- GUI-led for routine use; CLI underneath for setup, automation, and power-user work.

---

## Three-phase product

The product grows in phases. Each phase is independently useful and can be lived with before the next is built.

### Phase A — Topic map (MVP)

What it does:
- Ingests channel + every episode's metadata + chapter markers.
- Uses an LLM (one batched call, ~$0.10 for a full channel) to propose a topic/subtopic taxonomy from titles, descriptions, and chapters alone — **no transcripts**.
- Assigns each episode to one or more topics with a visible confidence score and a short reason.
- Presents a browseable topic map: pick a topic, see the episodes in it.
- User can merge, rename, split, and reassign topics; the human curates the auto-discovered map.

Solves: *"Which episodes are worth my time?"*

### Phase B — Sample-based refinement

What it does:
- Processes transcripts of ~15–20 representative episodes.
- Surfaces topics or subtopics that the metadata pass missed.
- User updates the taxonomy.

Solves: *"The map is approximately right but it's missing things that only come up in conversation."*

### Phase C — Claim extraction and synthesis

What it does:
- Fetches every transcript on the channel.
- Mines each transcript for atomic **claims** (advice, opinions, factual claims, anecdotes), each carrying topic, subtopic, speaker, episode, timestamp, and claim type.
- Embeds claims and clusters them within each topic.
- Surfaces per topic: **consensus** (clusters many guests independently land in), **conflict** (contradictory clusters within a topic), and **most useful advice** (high-density advice clusters).
- Cost: ~$8 one-time for a full DOAC-sized channel using batch APIs.

Solves: *"I can't remember the great advice."*

### Phase D — Natural-language Q&A

What it does:
- Free-form questions ("I want career direction", "how to improve gut health") map to retrieval over claim clusters, then synthesis.
- Answers cite source episodes and timestamps so the user can verify.

Solves: *"I want to ask the channel a question."*

---

## Unit of analysis

The first-class object is the **claim** (from Phase C onward), not the episode. An episode is a container of claims with metadata.

Reasoning: every output the user wants — consensus across guests, contradiction detection, ranked advice, Q&A retrieval — is a claim-level operation. Storing and reasoning at the episode level collapses the granularity those features need.

In Phase A, the only first-class object is the **episode-with-tags**, because no transcripts have been processed yet. The schema is designed so that claims can be added later without rework.

---

## LLM strategy

- **Tiered models.** Cheap fast model (Claude Haiku, GPT-4o-mini) for structured extraction work. Stronger model (Claude Sonnet) only for user-facing Q&A synthesis where nuance matters.
- **Batch APIs** for backlog processing — ~50% cheaper, no realtime requirement.
- **Local embeddings.** `sentence-transformers` (`all-MiniLM-L6-v2` or `bge-small-en`) on CPU. Free, private, fast enough.
- **Process once, store forever.** Each transcript is processed once into claims + embeddings. Subsequent queries are pure retrieval plus a tiny synthesis call.
- **Pre-filter** ad reads, intros, sponsor segments before sending transcript chunks to the LLM. Cheap heuristics save real money.

Realistic costs for the canonical case (Diary of a CEO, ~450 episodes):
- Phase A discovery: ~$0.10 one-time
- Phase B sampled refinement: ~$0.50
- Phase C full claim extraction: ~$8 one-time (batch API)
- Phase D per-query synthesis: ~$0.001–0.01 per question

---

## What this project is not

It is not:
- A transcript downloader.
- A general video search tool.
- A manual taxonomy workbench (this was an earlier framing; it has been retired — see `WORKLOG.md` 2026-05-04 entry).
- A standalone chatbot.
- A multi-channel discovery tool (for now).

The mental model is: **a personal knowledge crystalliser for a single podcast channel you care about.**

---

## What survives from the earlier vision

The earlier framing of this project as a "research workbench with manual taxonomy + comparison groups" produced ~600KB of working code. Most of it survives:

- Channel and video ingestion (`youtube.py`, `db.py`) — reused as-is.
- Schema (`channels`, `videos`, `topics`, `subtopics`) — reused; topics become auto-discovered then curated rather than manually authored.
- Review UI (`review_ui.py`) — repurposed: same suggest/review/approve/apply patterns, now reviewing the auto-discovered topic map and per-episode assignments rather than manual tag suggestions.
- CLI setup, ingestion, taxonomy commands (`cli.py`) — reused; manual taxonomy commands remain as a power-user surface.
- Topic and subtopic suggestion machinery (`topic_suggestions.py`, `subtopic_suggestions.py`) — repurposed to feed from metadata-derived discovery.

The casualties (moved to `legacy/`, not deleted):
- `comparison_group_suggestions.py`, `group_analysis.py`, group markdown export — the new product does not need user-curated comparison groups; consensus and conflict emerge from claim clustering within a topic in Phase C.
- `processing.py`, full-transcript pipeline — dormant until Phase C.
- `markdown_export.py` — dormant; later concern.

The strategy is **retrofit in place**, not greenfield. The plumbing is fine; the conceptual layer above it shifted.

---

## Success criteria

A good version of this project should let you:

1. Point the app at a single podcast channel.
2. Within minutes and for cents, get a reviewable topic map of that channel — what it is about, organised into broad topics and subtopics.
3. Browse "show me episodes about X" and get a useful, ranked list with multi-topic episodes correctly appearing under each relevant topic.
4. Curate the auto-discovered map (merge, rename, split, reassign) without leaving the GUI.
5. Later, opt in to full-transcript ingestion and get per-topic consensus / conflict / standout-advice browsing.
6. Eventually, ask the channel free-form questions and get answers with citations.

Phase A delivers (1)–(4). Phases B–D deliver (5)–(6).
