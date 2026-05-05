# Issue tracker: Local Markdown

Issues and PRDs for `yt_channel_analyzer` live as markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The PRD is `.scratch/<feature-slug>/PRD.md` (cross-cutting PRDs may live at the project root, e.g. `PRD_PHASE_A_TOPIC_MAP.md` — link to them from the feature directory)
- Implementation issues are `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Future

If this project is later pushed to GitHub, re-running `/setup-matt-pocock-skills` will reconfigure to GitHub Issues. Existing `.scratch/` issues can be migrated mechanically (`gh issue create` per file).
