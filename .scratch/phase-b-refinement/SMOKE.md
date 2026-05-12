# Phase B operator smoke — real DOAC refinement pass

End-to-end check that the sample-based transcript refinement loop works on
real data and real spend. Run once after B1–B6 are on `main`. Budget ≈ $0.40
(Haiku, one call per sampled transcript) plus a `discover --real` re-run
(≈ $0.02). Use a throwaway DB copy so a bad run is cheap to discard.

Prereqs: `cd ~/.openclaw/workspace && source .venv/bin/activate && set -a && source .env && set +a`
(needs `ANTHROPIC_API_KEY` + `YOUTUBE_API_KEY`). `RALPH_ALLOW_REAL_LLM=1` is
required for every `--real` step below (the runner fails fast without it).

## 0. Fresh non-Short discovery run

```bash
RALPH_ALLOW_REAL_LLM=1 python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/doac.sqlite --project-name "DOAC" --real
```

Confirm the Shorts filter was on (default): `discover` prints the excluded
count; `sqlite3 ./tmp/doac.sqlite 'SELECT n_shorts_excluded FROM discovery_runs ORDER BY id DESC LIMIT 1'`
should be non-zero on DOAC.

## 1. Inspect the auto-picked sample (UI)

```bash
python3 -m yt_channel_analyzer.cli serve-review-ui --db-path ./tmp/doac.sqlite --port 8765
```

Open `http://127.0.0.1:8765`, go to the **Refine** stage. Expect: ≈⌊⅔·15⌋
coverage slots spread over the busiest topics + the rest as blind-spot slots
(lowest-confidence / unassigned). Optionally remove a couple of rows or add
one by URL.

## 2. Fetch transcripts + cost estimate

Click **Fetch transcripts & estimate**. Episodes with no available
transcript drop out (note the warning). The estimate should land near
$0.40 for ≈15 transcripts. (CLI alternative: `fetch-transcripts
--db-path ./tmp/doac.sqlite --refinement-run-id <id>` after a `--stub`
allocate, or just let `refine --real` fetch them inline.)

## 3. Real refinement run

Toggle **--real**, click **Run refinement ($X.XX)**, confirm the dollar
prompt. Wait for the poll to report `success` + the proposal count. (CLI:
`RALPH_ALLOW_REAL_LLM=1 python3 -m yt_channel_analyzer.cli refine
--db-path ./tmp/doac.sqlite --project-name "DOAC" --real` — `--yes` skips
the prompt.)

Sanity: `sqlite3 ./tmp/doac.sqlite 'SELECT kind, name, parent_topic_name, status FROM taxonomy_proposals ORDER BY id'`
— mostly `subtopic` rows, `topic` rows should be rare; all `pending`.

## 4. Review proposals + before → after (UI)

On the Refine stage below the sample:

- **Taxonomy proposals**: grouped by run, newest first, subtopics under
  parents then topics. Each card has the evidence snippet + source episode
  link. **Accept** a few that look right → status line confirms the node was
  created; the card disappears on refresh. **Reject** an off one. Accepting
  one whose parent topic was meanwhile renamed should still work (resolves
  through renames); a deleted parent reports a rejection.
- **Before → after**: per sampled episode, eyeball the added/dropped/
  corrected chips and the transcript-grade assignment list. The episode's
  `reason` should now read like it came from the transcript, not the title.
  Click "✗ wrong" on any topic the transcript pass still got wrong — it
  drops and the panel refreshes.
- **Re-run Discover** nudge appears once you've accepted ≥1 proposal.

DB check: `sqlite3 ./tmp/doac.sqlite "SELECT assignment_source, COUNT(*) FROM video_topics GROUP BY 1"`
should show a `refine` bucket = the sampled episodes' topic rows.

## 5. Spread the accepted nodes channel-wide

```bash
RALPH_ALLOW_REAL_LLM=1 python3 -m yt_channel_analyzer.cli discover \
  --db-path ./tmp/doac.sqlite --project-name "DOAC" --real
```

Then in the topic map (Review stage): the accepted subtopics/topics should
now have episodes from beyond the sample; the sampled episodes still carry
their `refine` rows (transcript-grade confidence/reason, "transcript-checked"
pill) — the re-run did **not** downgrade them. `sqlite3 ./tmp/doac.sqlite
"SELECT assignment_source, COUNT(*) FROM video_topics GROUP BY 1"` — the
`refine` bucket is unchanged in size; new spread is `auto`.

## Pass criteria

- Sample picker produced a sensible coverage/blind-spot mix.
- Cost estimate within ~2× of actual (check `llm_calls`).
- Proposals rendered grouped; Accept created real nodes; Reject marked rows.
- Before → after showed real transcript-grade changes; mark-wrong worked.
- Post-`discover` re-run spread accepted nodes without downgrading `refine`
  rows; pill visible.
