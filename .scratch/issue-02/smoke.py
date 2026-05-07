"""Issue 02 / §A5 smoke: ingest a real channel, run discovery (real or stub),
print topics + subtopics + per-episode confidence/reason + cost.

Ad-hoc, not part of the test suite. Run from `~/.openclaw/workspace` with the
venv active.

    # Stub mode (free, deterministic — sanity-checks wiring on a /tmp DB)
    PYTHONPATH=. python3 yt_channel_analyzer/.scratch/issue-02/smoke.py --stub

    # Real-LLM mode (HITL boundary; sets RALPH_ALLOW_REAL_LLM=1 internally —
    # the verify gate must never set it)
    PYTHONPATH=. python3 yt_channel_analyzer/.scratch/issue-02/smoke.py

Reads ANTHROPIC_API_KEY and YOUTUBE_API_KEY from
~/.openclaw/workspace/.env (simple KEY=VALUE lines).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

USE_STUB = "--stub" in sys.argv[1:]

if not USE_STUB:
    os.environ["RALPH_ALLOW_REAL_LLM"] = "1"

ENV_FILE = Path("/home/chris/.openclaw/workspace/.env")
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from yt_channel_analyzer.db import (
    connect,
    init_db,
    upsert_channel_metadata,
    upsert_videos_for_primary_channel,
)
from yt_channel_analyzer.discovery import (
    DISCOVERY_PROMPT_VERSION,
    make_real_llm_callable,
    run_discovery,
    stub_llm,
)
from yt_channel_analyzer.extractor import anthropic_runner as ar
from yt_channel_analyzer.youtube import (
    fetch_channel_metadata,
    fetch_channel_videos,
    resolve_canonical_channel_id,
)

CHANNEL_INPUT = "@TheDiaryOfACEO"
PROJECT = "DOAC-smoke"
LIMIT = 15
HAIKU_INPUT_PER_M = 1.0
HAIKU_OUTPUT_PER_M = 5.0

USAGE: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

_orig_run_single = ar.AnthropicRunner.run_single


def _patched_run_single(self, *, prompt, rendered):
    client = self._ensure_client()
    message = client.messages.create(
        model=self.model,
        max_tokens=4096,
        system=prompt.system,
        messages=[{"role": "user", "content": rendered}],
    )
    usage = getattr(message, "usage", None)
    if usage is not None:
        USAGE["input_tokens"] += getattr(usage, "input_tokens", 0)
        USAGE["output_tokens"] += getattr(usage, "output_tokens", 0)
        USAGE["calls"] += 1
    return ar._extract_text(message)


ar.AnthropicRunner.run_single = _patched_run_single


def main() -> int:
    mode = "stub" if USE_STUB else "real"
    ts = time.strftime("%Y%m%d-%H%M%S")
    db_path = Path(f"/tmp/doac-smoke-{mode}-{ts}.db")
    print(f"[smoke] mode={mode} db={db_path}")

    canonical = resolve_canonical_channel_id(CHANNEL_INPUT)
    metadata = fetch_channel_metadata(canonical)
    print(f"[smoke] resolved channel: {metadata.youtube_channel_id} ({metadata.title})")

    init_db(
        db_path,
        project_name=PROJECT,
        channel_id=metadata.youtube_channel_id,
        channel_title=metadata.title,
        channel_handle=metadata.handle,
    )
    upsert_channel_metadata(db_path, project_name=PROJECT, metadata=metadata)
    videos = fetch_channel_videos(metadata.youtube_channel_id, limit=LIMIT)
    upsert_videos_for_primary_channel(db_path, videos=videos)
    print(f"[smoke] ingested {len(videos)} videos")

    started = time.time()
    if USE_STUB:
        llm = stub_llm
    else:
        with connect(db_path) as conn:
            llm = make_real_llm_callable(conn)
    run_id = run_discovery(
        db_path,
        project_name=PROJECT,
        llm=llm,
        model=ar.DEFAULT_MODEL,
        prompt_version=DISCOVERY_PROMPT_VERSION,
    )
    elapsed = time.time() - started
    print(f"[smoke] discovery run {run_id} complete ({elapsed:.1f}s)")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        topics = conn.execute(
            """
            SELECT t.id, t.name, COUNT(vt.video_id) AS episodes,
                   ROUND(AVG(vt.confidence), 2) AS avg_conf
            FROM video_topics vt
            JOIN topics t ON t.id = vt.topic_id
            WHERE vt.discovery_run_id = ?
            GROUP BY t.id
            ORDER BY episodes DESC
            """,
            (run_id,),
        ).fetchall()
        # Subtopic density: distinct subtopics-with-assignments per topic.
        subtopic_density = conn.execute(
            """
            SELECT t.id AS topic_id, t.name AS topic,
                   COUNT(DISTINCT s.id) AS subtopic_count
            FROM video_subtopics vs
            JOIN subtopics s ON s.id = vs.subtopic_id
            JOIN topics t ON t.id = s.topic_id
            WHERE vs.discovery_run_id = ?
            GROUP BY t.id
            ORDER BY t.name COLLATE NOCASE
            """,
            (run_id,),
        ).fetchall()
        # Per-subtopic assignment counts within this run.
        subtopics = conn.execute(
            """
            SELECT t.name AS topic, s.name AS subtopic,
                   COUNT(vs.video_id) AS episodes,
                   ROUND(AVG(vs.confidence), 2) AS avg_conf
            FROM video_subtopics vs
            JOIN subtopics s ON s.id = vs.subtopic_id
            JOIN topics t ON t.id = s.topic_id
            WHERE vs.discovery_run_id = ?
            GROUP BY s.id
            ORDER BY t.name COLLATE NOCASE, episodes DESC
            """,
            (run_id,),
        ).fetchall()
        per_episode = conn.execute(
            """
            SELECT v.id AS vid, v.title, t.name AS topic,
                   vt.confidence, vt.reason
            FROM video_topics vt
            JOIN topics t ON t.id = vt.topic_id
            JOIN videos v ON v.id = vt.video_id
            WHERE vt.discovery_run_id = ?
            ORDER BY v.id
            """,
            (run_id,),
        ).fetchall()
        # Subtopic assignments keyed by (video_id, topic_name).
        per_episode_subs = conn.execute(
            """
            SELECT vs.video_id AS vid, t.name AS topic, s.name AS subtopic
            FROM video_subtopics vs
            JOIN subtopics s ON s.id = vs.subtopic_id
            JOIN topics t ON t.id = s.topic_id
            WHERE vs.discovery_run_id = ?
            """,
            (run_id,),
        ).fetchall()

    sub_lookup: dict[tuple[int, str], list[str]] = {}
    for row in per_episode_subs:
        sub_lookup.setdefault((row["vid"], row["topic"]), []).append(row["subtopic"])

    print()
    print("=== Discovered topics ===")
    for row in topics:
        print(f"  {row['episodes']:>3}  conf={row['avg_conf']}  {row['name']}")

    total_topics = len(subtopic_density) or len(topics) or 1
    total_subs = sum(r["subtopic_count"] for r in subtopic_density)
    avg_density = total_subs / total_topics if total_topics else 0.0
    print()
    print("=== Subtopic density per topic ===")
    for row in subtopic_density:
        print(f"  {row['subtopic_count']:>3}  {row['topic']}")
    print(f"  -- avg subtopics/topic: {avg_density:.2f} (target ≥2.0 — issue 03 crit 6)")

    print()
    print("=== Subtopics (within run) ===")
    last_topic = None
    for row in subtopics:
        if row["topic"] != last_topic:
            print(f"  [{row['topic']}]")
            last_topic = row["topic"]
        print(f"    {row['episodes']:>3}  conf={row['avg_conf']}  {row['subtopic']}")

    print()
    print("=== Per-episode ===")
    confs: list[float] = []
    for row in per_episode:
        conf = row["confidence"]
        if conf is not None:
            confs.append(conf)
        title = (row["title"] or "")[:55]
        reason = (row["reason"] or "")[:60]
        subs = sub_lookup.get((row["vid"], row["topic"]), [])
        sub_str = ", ".join(subs) if subs else "—"
        conf_str = f"{conf:.2f}" if conf is not None else "  - "
        print(f"  [{conf_str}] {title!r:57}  -> {row['topic']} / {sub_str}")
        if reason:
            print(f"         reason: {reason}")
    if confs:
        cmin, cmax = min(confs), max(confs)
        cavg = sum(confs) / len(confs)
        print(
            f"  -- confidence: min={cmin:.2f} max={cmax:.2f} avg={cavg:.2f} "
            f"spread={cmax - cmin:.2f} (issue 04 crit 5: expect variation)"
        )

    if not USE_STUB:
        cost = (
            USAGE["input_tokens"] * HAIKU_INPUT_PER_M / 1_000_000
            + USAGE["output_tokens"] * HAIKU_OUTPUT_PER_M / 1_000_000
        )
        print()
        print("=== Cost ===")
        print(f"  model         : {ar.DEFAULT_MODEL}")
        print(f"  api calls     : {USAGE['calls']}")
        print(f"  input tokens  : {USAGE['input_tokens']:,}")
        print(f"  output tokens : {USAGE['output_tokens']:,}")
        print(f"  est. cost USD : ${cost:.4f}  (Haiku 4.5: ${HAIKU_INPUT_PER_M}/M in, ${HAIKU_OUTPUT_PER_M}/M out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
