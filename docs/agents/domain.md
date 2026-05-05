# Domain Docs

How the engineering skills should consume this project's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the project root (created lazily as domain terms get resolved)
- **`docs/adr/`** — architectural decision records that touch the area you're about to work in (created lazily as decisions get made)

If any of these don't exist yet, **proceed silently**. Don't flag absence; don't suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## Layout

This is a **single-context** project. One `CONTEXT.md` and one `docs/adr/` at the project root.

```
yt_channel_analyzer/
├── CONTEXT.md              ← created lazily
├── docs/
│   ├── adr/                ← created lazily
│   │   └── 0001-*.md
│   └── agents/
│       ├── issue-tracker.md
│       ├── triage-labels.md
│       └── domain.md
└── *.py
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (claim-as-unit) — but worth reopening because…_
