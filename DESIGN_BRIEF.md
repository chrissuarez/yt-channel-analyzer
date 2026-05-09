# Design Brief — yt-channel-analyzer

> Input to the design conversation. Not a PRD, not a spec. Read this *before* sketching.
> Companion to `GUI_UX_PLAN.md` (functional layout) and `CONTEXT.md` (domain glossary).

## What this tool is

A **single-operator workspace** for turning a YouTube channel into a curated, navigable map of its ideas. One person (the operator) ingests a channel's videos, asks an LLM to discover topics, curates the result, then reads / re-watches the channel through that lens. There's no multi-tenancy, no permissions, no onboarding. Be opinionated.

## What it should *feel* like

**A serious nonfiction book that has a workspace tucked inside it.** Editorial calm on the outside (Supply → Discover → Consume reading), workshop affordances on the inside (Review canvas where you move things around).

Reference points (in order of how strongly they apply):
- **Are.na** — restrained type, generous whitespace, content-respectful.
- **Substack reader / Readwise** — typography-led; the writing matters.
- **tldraw / Figma FigJam** — only for the Review canvas: spatial topic pillars you click to focus.
- **Linear** — only for the funnel stepper's tight status semantics. *Not* for the overall feel.

Anti-references (what we are *not*):
- Generic SaaS admin panel (Vercel/PostHog/Mixpanel — what the current UI looks like).
- Glassmorphism, gradients, large border radii, drop shadows on default state.
- Emoji icons, animated illustrations, "wow factor" hero sections.
- Notion-light blandness.

## The user, and what they're doing

Solo operator (one person, on their own laptop). They:
1. Pick a channel.
2. Ingest its videos.
3. Run topic discovery (LLM call, ~$0.02 per run, takes ~17s).
4. **Triage the result** — accept, rename, mark-wrong, drill into topics. Their corrections persist across re-runs (sticky curation).
5. **Read & watch** through the curated lens — read claims the LLM extracted from transcripts, watch the source video where each claim was made.

They are *not* dashboard-watching. They are *not* getting notifications. They open the tool with intent, do a session of work, close it.

## Information architecture: 4-stage funnel

A horizontal stepper at the top of every page makes the pipeline explicit. Each stage is its own page. Stages are linear but the operator can jump between them.

```
●────────●────────○────────○
Supply   Discover  Review   Consume
✓ done   ✓ done    · 4 left · not yet
```

### Stage status semantics (concrete, not hand-wavy)

| Stage    | Status sub-line                                          | State labels                                |
|----------|----------------------------------------------------------|---------------------------------------------|
| Supply   | `N videos · M with transcripts`                          | `empty` / `ingesting` / `ready`             |
| Discover | `K runs · last: 2026-05-09`                              | `never run` / `outdated*` / `current`       |
| Review   | `X / Y assignments curated`                              | `unstarted` / `in progress` / `caught up`   |
| Consume  | `P claims · Q topics covered`                            | `empty` / `partial` / `full` (Phase C)      |

*`outdated` = new videos ingested since the last discovery run.

The stepper is the operator's primary "what should I do next" surface. If everything is `current/caught up/full`, the stepper shows all green checks and there's nothing to do. If a stage is `outdated` or `unstarted`, that stage's circle is filled terra-cotta, signalling action.

### What each page is for

**Supply Channel.** A scrollable list of ingested videos (full titles, never truncated). Each row: title, publish date, transcript-availability indicator. Top of page: channel header, "Add channel" affordance, "Re-ingest" button. Empty state: a single large CTA "Add a channel" with a paste-URL field below.

**Topic Discovery.** History of discovery runs as a vertical list (most recent first). Each row shows: run #, model, prompt version, timestamp, status (success/error), topic count, episode count, **cost** (now that `cost_estimate_usd` is wired). Click a row → see that run's full payload (topics, subtopics, error message + raw response if it errored). Top of page: prominent "Run discovery" button with `--stub` / `--real` toggle and current cost estimate.

**Review.** The canvas. See "Review canvas" below — this is the page that needs the most design love.

**Consume.** Claims (Phase C — not built yet, but reserve the space). Layout: scrollable column of claim cards, each card is a quote in Fraunces italic with attribution to source video + timestamp. Filter sidebar at left: filter by topic, by speaker (when guest data lands), by claim type. Click "watch source" on a claim → opens YouTube embed in a slide-over panel, auto-seeked to the timestamp.

## Review canvas — the central interaction

The Review page is where the canvas/topology metaphor lives.

**Default (overview) state:** topic pillars laid out as cards in a responsive grid (think Pinterest masonry, not strict columns). Each pillar shows:
- Topic name (Fraunces, large)
- Episode count badge
- Subtopic chips (small pills, inline)
- A confidence sparkline / dot grid showing assignment confidence distribution

That's it. No episode titles in overview state — the pillar is a *summary affordance*. Pillar size scales lightly with episode count (visual hierarchy: bigger pillars = more videos).

**Focused state (click a pillar):** the clicked pillar expands inline to ~80% page width, others collapse to a thin strip showing only name + count badge, stacked vertically along the left edge as a "minimap." The expanded pillar reveals:
- Subtopic accordions (click subtopic → expand its episodes)
- Episode rows under each subtopic, **with full titles wrapping freely** (the no-truncation constraint).
- Each episode row: thumbnail, title (Inter Medium), confidence pill, "also in [Topic X]" pills, and the LLM's `reason` rendered as a small italic Fraunces pull-quote.
- Curation actions per row: ✎ Rename topic · ✗ Mark wrong (topic) · ✗ Mark wrong (subtopic) · ▶ Watch (opens YouTube).

Click a minimap pillar → swap focus. Click empty space → return to overview.

**Why this resolves the user's constraints:**
- "Click a pillar, others minimize" → focused/minimap states.
- "See full video titles" → only the focused pillar shows titles, so the no-truncation rule is affordable (the canvas isn't trying to display 200 wrapping titles at once).
- "See big topic pillars at a glance" → overview state shows pillar size + episode count, sortable by either.
- "Click a video to view it" → ▶ Watch button per episode row.

## Visual system

### Color (light theme — only theme for now)

| Token        | Value     | Use                                    |
|--------------|-----------|----------------------------------------|
| `--paper`    | `#fafaf7` | Page background. Warm off-white.       |
| `--surface`  | `#ffffff` | Cards, pillars.                        |
| `--ink`      | `#1a1a17` | Primary text. Warm near-black.         |
| `--ink-soft` | `#5a5a52` | Secondary text, metadata.              |
| `--rule`     | `#e6e2d8` | Borders, dividers, hairlines.          |
| `--accent`   | `#c2410c` | Single confident accent. Terra-cotta.  |
| `--good`     | `#15803d` | Done / curated / current.              |
| `--warn`     | `#c2410c` | Action needed (intentionally = accent).|
| `--bad`      | `#991b1b` | Errored / wrong-marked.                |
| `--mute`     | `#9ca3af` | Disabled, not-yet-applicable.          |

No dark mode in v1. (Add later if it becomes a real ask.)

### Typography

- **Display** (page titles, topic names, claim quotes): **Fraunces** variable serif, "soft" optical setting. Falls back to Source Serif Pro → Georgia.
- **Body** (everything else): **Inter** variable. Falls back to system-ui.
- **Mono** (run IDs, timestamps, model names, JSON): **JetBrains Mono**. Falls back to ui-monospace.

Title scales: H1 40 / H2 28 / H3 20. Body 16 / small 14 / micro 12. Line-height 1.5 for body, 1.2 for display.

### Spacing & shape

- Spacing scale: `4 8 12 16 24 32 48 64 96` px. Use the upper end generously — editorial breathes.
- Radii: `6px` for cards, `999px` for pills, `0` for hairlines/rules. **No 12-16px rounded-card SaaS look.**
- Borders: 1px solid `--rule`, used liberally instead of shadows.
- Shadows: none on default state. On hover: `0 1px 0 var(--accent)` (a single hairline at top of card) — interactivity signal, not depth.

### Iconography

Stroke-only icons (1.5px), monochrome `--ink`. Use Lucide or Phosphor. **No emoji.** Icons are sparse — only where labels would be ambiguous.

## Component vocabulary

Components Claude Design should sketch (in this priority order):

1. **TopFunnelStepper** — horizontal, 4 stages, with status sub-lines + click-to-navigate.
2. **PillarCard** — overview state + focused state + minimap state (3 variants).
3. **EpisodeRow** — thumbnail, full-title-wrapping, confidence, reason pull-quote, also-in, action buttons.
4. **StageEmptyState** — paper-y "nothing here yet" with single CTA. Used by Supply (no channel), Discover (no runs), Review (no curation needed), Consume (Phase C placeholder).
5. **SubtopicChip** — inline pill, click to filter focused pillar.
6. **DiscoveryRunRow** — model, version, timestamp, status, cost, click → detail.
7. **ClaimCard** (Phase C — sketch, don't build) — quote + source video + timestamp + watch-source link.
8. **WatchSlideOver** (Phase C — sketch, don't build) — embedded YouTube player at right edge, seekable.

## Hard constraints

- **Never truncate a video title.** Anywhere it appears, it wraps.
- **Stage status must be glanceable.** A user opening the tool cold should know within 2 seconds what stage needs work.
- **Phase C-ready shell.** Consume's page should exist and have an empty state before claims/transcripts ship; don't build a UI that needs restructuring when Phase C lands.
- **Terra-cotta is the only accent color.** Don't add a second brand color "for variety."
- **Built on plain HTML/CSS/JS** (the current stack is `http.server` + vanilla — design choices need to be implementable without a JS framework).

## What I'm explicitly *not* deciding here

- Animation language (only specified: pillar focus/minimap transition should be a smooth ~200ms cubic-bezier).
- Mobile / responsive — operator is on a laptop. Sketch desktop-first; mobile is post-v1.
- Empty-channel onboarding (single-CTA suffices).
- Multi-channel switching UI — not needed yet (one channel per DB by design).

## How to use this brief

Paste the GitHub URL (https://github.com/chrissuarez/yt-channel-analyzer) into Claude Design plus this brief. Ask for:
1. Mockups of the 4 stages (Supply / Discover / Review / Consume) at desktop width.
2. Three states of the Review canvas (overview, focused, minimap-only).
3. The component vocabulary as a small style-guide page.

Don't ask for: dark mode, mobile, marketing-site landing pages, or icon design.
