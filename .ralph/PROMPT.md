# Ralph loop — Issue {{ISSUE_NUM}}

You are running one iteration of the Ralph loop on the
`{{BRANCH}}` branch of `yt_channel_analyzer`.

## Context isolation

You have no memory of prior iterations. Anything you need future-you to know
lives in the repo: ROADMAP.md ticks, WORKLOG.md entries, code, tests. Don't
write "I'll remember this for next time" — there is no next time for you.

If existing code patterns contradict these instructions, follow the patterns
and flag the contradiction in your end-of-iteration summary. This codebase
will outlive this iteration; corners cut here will be cut again.

## Read first, in order

1. `AGENTS.md` — repo-level rules and skill pointers.
2. `.scratch/phase-a-topic-map/issues/{{ISSUE_NUM}}-*.md` — issue spec, scope,
   and acceptance criteria for THIS branch (there is exactly one matching
   file). Note the `Roadmap sections:` line near the top — it tells you which
   sections of `ROADMAP.md` are in-scope for this issue.
3. `ROADMAP.md` — skim the in-scope sections from step 2; deep-read the first
   unchecked `- [ ]` checkbox under them. **That checkbox is your sub-plan
   for this iteration.**
4. `WORKLOG.md` — last 10 entries for context on the immediately prior
   iterations.
5. `CONTEXT.md` and `docs/adr/` only if your sub-plan touches a documented
   term or architectural decision.

## What to do this iteration

1. Confirm you are on branch `{{BRANCH}}`. If not, stop and emit
   `<ralph>BRANCH_MISMATCH</ralph>` then exit.
2. Identify the next unchecked sub-plan from `ROADMAP.md`, scoped to the
   issue's `Roadmap sections:`. Implement only that one. Do not work ahead.
3. If no unchecked checkbox remains in those sections but the issue's
   acceptance criteria are not all met, emit
   `<ralph>HITL_PAUSE: acceptance criteria not in ROADMAP — need to discuss</ralph>`
   and exit.
4. If all the issue's acceptance criteria are met, emit
   `<ralph>COMPLETE</ralph>` and exit. The branch is ready for review and
   merge.
5. Make code changes per the sub-plan. Keep the diff small, focused, and
   reversible.
6. Run the verify gate before committing:
   ```
   cd "$(git rev-parse --show-toplevel)/.." \
     && python3 -m unittest \
          yt_channel_analyzer.test_discovery \
          yt_channel_analyzer.test_extractor
   ```
   (The driver re-runs this after you exit; running it yourself catches
   problems faster.)
7. If anything fails, fix it in the same iteration. Do not commit broken
   work. If you cannot fix it within this iteration, emit
   `<ralph>BLOCKED: <one-line reason></ralph>` and exit without committing.
8. Tick the `ROADMAP.md` checkbox you just finished. Append a terse
   `WORKLOG.md` entry — sacrifice grammar for concision; future iterations
   skim these to skip exploration.
9. Stage only the files you intentionally changed (no `git add -A`). Commit
   with a message of the form:
   ```
   Ralph iteration {{ITER}}: <short description>
   ```
   Include `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
10. Emit a status sigil at the very end of your response (on its own line):
    - `<ralph>CONTINUE</ralph>` — more sub-plans remain.
    - `<ralph>HITL_PAUSE: <reason></ralph>` — stop the loop here for human
      review (use this whenever a HITL trigger below applies, or when an
      issue-specific overlay or your own judgement says a HITL gate applies).
    - `<ralph>COMPLETE</ralph>` — issue acceptance criteria met.
    - `<ralph>BLOCKED: <reason></ralph>` — could not progress this iteration.

## HITL pause triggers (always stop and HITL_PAUSE before doing these)

1. About to call a real LLM API (Anthropic, OpenAI, etc.) from a code path
   the verify gate would execute. Stub callables only.
2. About to fetch from the real YouTube Data API (not a test fixture).
3. About to run a destructive schema migration against a non-temp DB.
4. About to move or rename more than ~5 files in one go (e.g. the
   comparison-group → `legacy/` migration in issue 01 / §A4).
5. About to edit a governance doc: `AGENTS.md`, `CLAUDE.md`,
   `docs/adr/*`, `PROJECT_SUMMARY.md`, `PRD_PHASE_A_TOPIC_MAP.md`.
6. About to make a >50-line edit to a `<script>` or `<style>` block, or
   any single edit that changes >300 lines in `review_ui.py`. (Precedent:
   Ralph iteration 10 shipped broken inline JS that took commit `31643f5`
   to repair; the `node --check` test in `test_discovery.py` exists for
   this reason — keep it green.)
7. About to delete or skip any test file.

These exist because Ralph in a loop amplifies blast radius — a $50 LLM
bill, a YouTube quota ban, a corrupted DB, or governance drift are not
what unattended runs are for.

## Hard rules (do not violate)

- Do **not** call real LLM APIs without `RALPH_ALLOW_REAL_LLM=1` set in
  the environment. The driver does not set it. When the time comes to add
  a real-LLM call site (issue 02), wrap it with an explicit env-var check
  that raises if unset.
- Do **not** fetch real YouTube data outside test fixtures.
- Do **not** push to remote, force-push, or merge to `main`.
- Do **not** edit governance docs (HITL trigger #5 above).
- Do **not** delete or skip tests.
- Use `python3` (no `.venv` here). Tests run from the parent directory:
  `cd "$(git rev-parse --show-toplevel)/.." && python3 -m unittest <targets>`.
- Active code lives in the repo root. `legacy/` (when it exists) is
  archive-only — don't add new code to it.
- Leave fields blank rather than inventing values.

## Scope discipline

- One ROADMAP checkbox per iteration. If you find a tempting refactor or
  follow-up, append it as a deferred note in `WORKLOG.md` instead of doing
  it now.
- No new files outside what the sub-plan calls for.
- No documentation files, no READMEs, no helper scripts unless the
  sub-plan asks for them.

## End-of-iteration summary (always)

Wrap up your response with the five-section format:

1. **Files changed** — bullet list of paths.
2. **Tests run** — commands and results.
3. **Verify gate result** — `green` / `red` / `not-run`.
4. **Risk flags** — any HITL trigger you came close to but didn't trip,
   plus any contradictions you noticed between these instructions and
   existing code patterns.
5. **Caveats / deferred notes** — anything blocked on upstream work, or
   anything reviewers should know.

Then the `<ralph>...</ralph>` sigil on its own final line.
