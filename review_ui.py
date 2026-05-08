from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from yt_channel_analyzer.db import (
    apply_subtopic_suggestion_to_video,
    apply_topic_suggestion_to_video,
    approve_comparison_group_suggestion_label,
    approve_subtopic_suggestion_label,
    approve_topic_suggestion_label,
    bulk_apply_topic_suggestion_label,
    create_comparison_group_suggestion_run,
    create_subtopic_suggestion_run,
    create_topic_suggestion_run,
    get_latest_topic_suggestion_run_id,
    get_comparison_group_suggestion_review_rows,
    get_primary_channel,
    get_subtopic_suggestion_review_rows,
    get_topic_suggestion_review_rows,
    list_subtopic_suggestion_application_rows,
    list_approved_comparison_groups_for_subtopic,
    list_approved_subtopics_for_topic,
    list_approved_topic_names,
    list_topic_suggestion_application_rows,
    list_topic_suggestion_runs,
    list_topics,
    list_videos_for_comparison_group_suggestions,
    list_videos_for_subtopic_suggestions,
    list_videos_for_topic_suggestions,
    mark_assignment_wrong,
    move_episode_subtopic,
    reject_comparison_group_suggestion_label,
    reject_subtopic_suggestion_label,
    reject_topic_suggestion_label,
    rename_comparison_group_suggestion_label,
    rename_subtopic_suggestion_label,
    merge_topics,
    rename_topic,
    split_topic,
    rename_topic_suggestion_label,
    store_video_comparison_group_suggestion,
    store_video_subtopic_suggestion,
    store_video_topic_suggestion,
    summarize_comparison_group_suggestion_labels,
    summarize_subtopic_suggestion_labels,
    summarize_topic_suggestion_labels,
)
from yt_channel_analyzer.legacy.comparison_group_suggestions import suggest_comparison_groups_for_video
from yt_channel_analyzer.subtopic_suggestions import suggest_subtopics_for_video
from yt_channel_analyzer.topic_suggestions import suggest_topics_for_video


DEFAULT_SUGGESTION_MODEL = "gpt-4.1-mini"
UI_REVISION = "2026-05-08.5-comparison-readiness-run-history-advanced-channel-overview-discovery-panel"
MIN_NEW_SUBTOPIC_CLUSTER_SIZE = 5

DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.5
LOW_CONFIDENCE_THRESHOLD_ENV_VAR = "YTA_LOW_CONFIDENCE_THRESHOLD"


def _load_low_confidence_threshold() -> float:
    raw = os.environ.get(LOW_CONFIDENCE_THRESHOLD_ENV_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_LOW_CONFIDENCE_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LOW_CONFIDENCE_THRESHOLD
    if not 0.0 <= value <= 1.0:
        return DEFAULT_LOW_CONFIDENCE_THRESHOLD
    return value


def _low_confidence_class(confidence: float | None, threshold: float) -> str:
    if confidence is None:
        return ""
    return "low" if confidence < threshold else ""

HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YT Channel Analyzer Review UI</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b1020;
      --panel: #141b2d;
      --panel-2: #1a2338;
      --text: #eef2ff;
      --muted: #a8b3cf;
      --accent: #7dd3fc;
      --good: #86efac;
      --warn: #fbbf24;
      --bad: #fca5a5;
      --border: #2b3550;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      background: linear-gradient(180deg, #09101f 0%, #0b1020 100%);
      color: var(--text);
    }
    .wrap {
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px;
    }
    .topbar, .panel {
      background: rgba(20, 27, 45, 0.92);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.22);
    }
    .topbar {
      padding: 20px;
      margin-bottom: 20px;
    }
    .title {
      margin: 0 0 10px;
      font-size: 28px;
    }
    .revision-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin-left: 10px;
      padding: 4px 8px;
      border-radius: 999px;
      border: 1px solid rgba(125, 211, 252, 0.35);
      background: rgba(125, 211, 252, 0.08);
      color: var(--accent);
      font-size: 12px;
      vertical-align: middle;
    }
    .muted { color: var(--muted); }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }
    .row.stretch {
      align-items: stretch;
    }
    .controls {
      margin-top: 16px;
    }
    .context-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }
    .context-card {
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.03);
    }
    .context-card .k {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .context-card strong {
      display: block;
      margin-bottom: 4px;
    }
    .generator {
      margin-top: 16px;
      padding-top: 16px;
      border-top: 1px solid rgba(255,255,255,0.06);
    }
    .run-history-advanced {
      margin-top: 16px;
      padding-top: 12px;
      border-top: 1px solid rgba(255,255,255,0.06);
    }
    .run-history-advanced > summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 14px;
    }
    .run-history-advanced .run-history-hint {
      margin-top: 8px;
    }
    .run-history-advanced > label {
      margin-top: 8px;
      max-width: 320px;
    }
    label {
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 14px;
      color: var(--muted);
    }
    select, input, button {
      font: inherit;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--panel-2);
      color: var(--text);
      padding: 10px 12px;
    }
    button {
      cursor: pointer;
      transition: transform 0.05s ease, border-color 0.15s ease;
    }
    button:hover { border-color: var(--accent); }
    button:active { transform: translateY(1px); }
    button.good { border-color: rgba(134, 239, 172, 0.35); }
    button.bad { border-color: rgba(252, 165, 165, 0.35); }
    button.warn { border-color: rgba(251, 191, 36, 0.35); }
    button.primary-action {
      background: rgba(134, 239, 172, 0.12);
      border-color: rgba(134, 239, 172, 0.62);
      color: var(--good);
      font-weight: 700;
    }
    button.secondary { background: transparent; }
    .topic-map {
      margin: 20px 0;
      padding: 18px;
      border: 1px solid rgba(125, 211, 252, 0.18);
      border-radius: 18px;
      background:
        radial-gradient(circle at top left, rgba(125, 211, 252, 0.12), transparent 32%),
        rgba(20, 27, 45, 0.78);
      box-shadow: 0 18px 45px rgba(0, 0, 0, 0.22);
    }
    .topic-map.discovery-topic-map {
      border-color: rgba(134, 239, 172, 0.22);
      background:
        radial-gradient(circle at top left, rgba(134, 239, 172, 0.10), transparent 32%),
        rgba(20, 27, 45, 0.78);
    }
    .channel-overview { margin-bottom: 20px; }
    .channel-overview-stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 8px;
      margin: 12px 0;
    }
    .channel-overview-latest { margin-top: 8px; }
    .discovery-topic-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
    }
    .discovery-topic-header h3 {
      margin: 0;
    }
    .discovery-topic-actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .discovery-topic-rename,
    .discovery-topic-merge,
    .discovery-topic-split {
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: rgba(125, 211, 252, 0.06);
      color: var(--accent);
      cursor: pointer;
    }
    .discovery-topic-rename:hover,
    .discovery-topic-merge:hover,
    .discovery-topic-split:hover {
      background: rgba(125, 211, 252, 0.16);
    }
    .subtopic-video-move {
      margin-left: 6px;
      font-size: 11px;
      padding: 2px 6px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: rgba(125, 211, 252, 0.06);
      color: var(--accent);
      cursor: pointer;
    }
    .subtopic-video-move:hover {
      background: rgba(125, 211, 252, 0.16);
    }
    .confidence-bar {
      position: relative;
      height: 6px;
      border-radius: 999px;
      background: rgba(255,255,255,0.08);
      margin-top: 6px;
      overflow: hidden;
    }
    .confidence-bar > span {
      position: absolute;
      top: 0;
      left: 0;
      bottom: 0;
      background: linear-gradient(90deg, rgba(134, 239, 172, 0.6), rgba(125, 211, 252, 0.7));
      border-radius: 999px;
    }
    .confidence-bar.low > span { background: rgba(251, 191, 36, 0.6); }
    .discovery-episode-list {
      list-style: none;
      padding: 0;
      margin: 12px 0 0;
      display: grid;
      gap: 8px;
    }
    .discovery-episode {
      display: grid;
      grid-template-columns: 64px minmax(0, 1fr);
      gap: 10px;
      padding: 8px;
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 12px;
      background: rgba(11,16,32,0.42);
    }
    .discovery-episode.low { opacity: 0.55; }
    .discovery-episode-thumb {
      width: 64px;
      height: 36px;
      object-fit: cover;
      border-radius: 8px;
      background: rgba(255,255,255,0.05);
    }
    .discovery-episode-thumb.placeholder {
      background: linear-gradient(135deg, rgba(125,211,252,0.18), rgba(134,239,172,0.10));
    }
    .discovery-episode-body { min-width: 0; }
    .discovery-episode-title {
      font-size: 13px;
      font-weight: 600;
      line-height: 1.25;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .discovery-episode-meta {
      display: flex;
      gap: 10px;
      align-items: center;
      margin-top: 3px;
      font-size: 11px;
    }
    .discovery-episode-confidence {
      font-weight: 700;
      color: var(--good);
    }
    .discovery-episode.low .discovery-episode-confidence { color: var(--bad); }
    .discovery-episode-also-in {
      font-size: 11px;
      color: var(--muted);
      background: rgba(148, 163, 184, 0.12);
      border-radius: 999px;
      padding: 1px 8px;
    }
    .discovery-topic-new-badge {
      display: inline-block;
      margin-left: 8px;
      font-size: 11px;
      font-weight: 600;
      color: var(--good);
      background: rgba(74, 222, 128, 0.15);
      border-radius: 999px;
      padding: 1px 8px;
      vertical-align: middle;
    }
    .discovery-episode-reason {
      margin-top: 4px;
      font-size: 12px;
      color: var(--muted);
      font-style: italic;
    }
    .discovery-episode-empty {
      margin-top: 10px;
      font-size: 12px;
    }
    .discovery-episode-wrong {
      margin-top: 6px;
      background: rgba(252, 165, 165, 0.10);
      color: var(--bad);
      border: 1px solid rgba(252, 165, 165, 0.40);
      border-radius: 999px;
      padding: 2px 10px;
      font-size: 11px;
      cursor: pointer;
    }
    .discovery-episode-wrong:hover {
      background: rgba(252, 165, 165, 0.22);
    }
    .subtopic-video-wrong {
      margin-left: 6px;
      background: rgba(252, 165, 165, 0.10);
      color: var(--bad);
      border: 1px solid rgba(252, 165, 165, 0.40);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      cursor: pointer;
    }
    .subtopic-video-wrong:hover {
      background: rgba(252, 165, 165, 0.22);
    }
    .discovery-subtopic-list {
      display: grid;
      gap: 6px;
      margin-top: 12px;
    }
    .discovery-subtopic-bucket {
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 10px;
      background: rgba(11,16,32,0.32);
      padding: 6px 10px;
    }
    .discovery-subtopic-bucket > summary {
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .discovery-subtopic-bucket > summary::-webkit-details-marker { display: none; }
    .discovery-subtopic-bucket .discovery-episode-list { margin-top: 8px; }
    .discovery-subtopic-unassigned > summary { color: var(--muted); font-style: italic; }
    .discovery-episode-sort-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      font-size: 12px;
      color: var(--muted);
    }
    .discovery-episode-sort {
      background: rgba(11,16,32,0.6);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 4px 6px;
      font-size: 12px;
    }
    .topic-map-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: end;
      margin-bottom: 14px;
    }
    .topic-map-head h2 { margin: 0 0 4px; }
    .topic-map-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }
    .topic-card {
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.025);
    }
    .topic-card.selected {
      border-color: rgba(125, 211, 252, 0.5);
      box-shadow: inset 0 0 0 1px rgba(125, 211, 252, 0.18);
    }
    .topic-card {
      position: relative;
      overflow: hidden;
      transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease;
    }
    .topic-card::after {
      content: "";
      position: absolute;
      inset: auto 12px 12px auto;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(125, 211, 252, 0.14), transparent 68%);
      pointer-events: none;
    }
    .topic-card:hover {
      transform: translateY(-2px);
      border-color: rgba(125, 211, 252, 0.42);
      background: rgba(255,255,255,0.04);
    }
    .topic-card.selected {
      background:
        linear-gradient(135deg, rgba(125, 211, 252, 0.10), rgba(134, 239, 172, 0.035)),
        rgba(255,255,255,0.035);
    }
    .topic-card h3 {
      margin: 0 0 8px;
      font-size: 18px;
    }
    .topic-card .topic-stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin: 10px 0;
    }
    .topic-stat {
      padding: 8px;
      border-radius: 10px;
      background: rgba(255,255,255,0.035);
      border: 1px solid rgba(255,255,255,0.055);
    }
    .topic-stat .k { display: block; color: var(--muted); font-size: 11px; }
    .topic-stat strong { font-size: 18px; }
    .status-chip {
      display: inline-flex;
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 12px;
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .status-chip.warn { color: #fde68a; border-color: rgba(251, 191, 36, 0.4); background: rgba(251, 191, 36, 0.08); }
    .status-chip.good { color: var(--good); border-color: rgba(134, 239, 172, 0.35); background: rgba(134, 239, 172, 0.08); }
    .status-chip.accent { color: var(--accent); border-color: rgba(125, 211, 252, 0.35); background: rgba(125, 211, 252, 0.08); }
    .topic-detail {
      margin: 0 0 20px;
      border-radius: 20px;
      border: 1px solid rgba(134, 239, 172, 0.18);
      background:
        linear-gradient(135deg, rgba(134, 239, 172, 0.08), transparent 36%),
        linear-gradient(225deg, rgba(125, 211, 252, 0.10), transparent 42%),
        rgba(11, 16, 32, 0.72);
      box-shadow: 0 20px 54px rgba(0,0,0,0.26);
      padding: 18px;
    }
    .topic-detail.empty { border-style: dashed; }
    .topic-detail-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
      margin-bottom: 14px;
    }
    .topic-detail h2 {
      margin: 4px 0 4px;
      font-size: clamp(24px, 4vw, 40px);
      letter-spacing: -0.04em;
    }
    .topic-detail .eyebrow {
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 11px;
      font-weight: 700;
    }
    .workflow-rail {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }
    .workflow-step {
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 14px;
      padding: 12px;
      background: rgba(255,255,255,0.03);
    }
    .workflow-step strong { display: block; margin-bottom: 4px; }
    .workflow-step.current {
      border-color: rgba(125, 211, 252, 0.38);
      background: rgba(125, 211, 252, 0.08);
    }
    .topic-inventory {
      margin-top: 16px;
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.65fr);
      gap: 14px;
    }
    .inventory-panel {
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.028);
    }
    .inventory-panel h3 { margin: 0 0 8px; }
    .subtopic-bucket {
      border-top: 1px solid rgba(255,255,255,0.07);
      padding-top: 10px;
      margin-top: 10px;
    }
    .subtopic-bucket:first-of-type { border-top: 0; padding-top: 0; }
    .video-list {
      display: grid;
      gap: 6px;
      margin-top: 8px;
    }
    .video-chip {
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 10px;
      padding: 8px 10px;
      background: rgba(11,16,32,0.48);
      color: var(--text);
      font-size: 13px;
    }
    .video-chip .meta { color: var(--muted); font-size: 11px; margin-top: 3px; }
    .readiness {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 9px;
      margin-left: 6px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid rgba(255,255,255,0.08);
    }
    .readiness.ready { color: #86efac; background: rgba(34,197,94,0.10); border-color: rgba(34,197,94,0.24); }
    .readiness.needs-transcripts { color: #fbbf24; background: rgba(251,191,36,0.10); border-color: rgba(251,191,36,0.24); }
    .readiness.thin { color: #fca5a5; background: rgba(248,113,113,0.10); border-color: rgba(248,113,113,0.24); }
    .transcript-coverage { color: var(--muted); font-size: 11px; margin-top: 4px; }
    .subtopic-actions { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
    @media (max-width: 900px) { .topic-inventory { grid-template-columns: 1fr; } }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 20px;
    }
    .panel {
      padding: 18px;
    }
    .panel h2, .panel h3 {
      margin-top: 0;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
    }
    .metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric {
      min-width: 110px;
      padding: 10px 12px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border);
      border-radius: 12px;
    }
    .metric .k {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 4px;
    }
    .metric strong {
      font-size: 20px;
    }
    .cards {
      display: grid;
      gap: 12px;
    }
    .card {
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      background: rgba(255,255,255,0.02);
    }
    .card h4 {
      margin: 0 0 8px;
      font-size: 18px;
    }
    .next-step {
      margin: 10px 0;
      padding: 10px 12px;
      border: 1px solid rgba(251, 191, 36, 0.4);
      background: rgba(251, 191, 36, 0.09);
      border-radius: 12px;
      color: #fde68a;
      font-size: 13px;
    }
    .next-step.good {
      border-color: rgba(134, 239, 172, 0.35);
      background: rgba(134, 239, 172, 0.08);
      color: var(--good);
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 10px;
      border: 1px solid var(--border);
      color: var(--muted);
      font-size: 12px;
      margin-right: 6px;
      margin-bottom: 6px;
    }
    ul.samples {
      padding-left: 18px;
      margin: 10px 0;
    }
    ul.samples li {
      margin-bottom: 6px;
      color: var(--muted);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .inline-field {
      width: min(320px, 100%);
    }
    .status {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(125, 211, 252, 0.08);
      color: var(--text);
      min-height: 44px;
      white-space: pre-wrap;
    }
    .list {
      display: grid;
      gap: 10px;
    }
    .list-item {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.02);
    }
    .label-applications {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .application-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.02);
    }
    .application-row .meta {
      font-size: 12px;
      color: var(--muted);
      margin-top: 4px;
    }
    .empty {
      color: var(--muted);
      border: 1px dashed var(--border);
      border-radius: 12px;
      padding: 16px;
    }
    code {
      color: var(--accent);
      word-break: break-all;
    }
    @media (max-width: 800px) {
      .wrap { padding: 14px; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="topbar">
      <h1 class="title">YT Channel Analyzer Review UI <span class="revision-badge">UI rev {{UI_REVISION}}</span></h1>
      <div class="muted">Local-only review surface for topic, subtopic, and comparison-group suggestion labels.</div>
      <div id="context-grid" class="context-grid"></div>
      <div class="controls row">
        <label>
          Topic for subtopic review
          <select id="topic-select"></select>
        </label>
        <label>
          Subtopic for comparison-group review
          <select id="subtopic-select"></select>
        </label>
        <button id="refresh-btn" class="secondary">Refresh</button>
      </div>
      <div class="generator">
        <div class="muted">Generate a fresh run from this dataset, then review it below.</div>
        <div class="controls row stretch">
          <label>
            Model
            <input id="model-input" value="gpt-4.1-mini" placeholder="gpt-4.1-mini">
          </label>
          <label>
            Limit
            <input id="limit-input" type="number" min="1" step="1" placeholder="All eligible videos">
          </label>
          <button id="generate-topics-btn">Generate topic suggestions</button>
          <button id="generate-subtopics-btn">Generate subtopic suggestions</button>
        </div>
      </div>
      <details class="run-history-advanced">
        <summary>Run history (advanced)</summary>
        <div class="muted run-history-hint">Pick an older run to inspect its labels. Routine review uses the latest run automatically.</div>
        <label>
          Suggestion run
          <select id="run-select"></select>
        </label>
      </details>
      <div class="status" id="status-box">Loading channel data… If this does not change, the page hit a client-side render error.</div>
    </section>

    <section class="panel channel-overview">
      <div class="section-head">
        <div>
          <h2 id="channel-overview-title">Channel Overview</h2>
          <div class="muted" id="channel-overview-subtitle"></div>
        </div>
      </div>
      <div class="channel-overview-stats" id="channel-overview-stats"></div>
      <div id="channel-overview-latest" class="channel-overview-latest"></div>
    </section>

    <section class="topic-map discovery-topic-map">
      <div class="topic-map-head">
        <div>
          <h2>Auto-Discovered Topics</h2>
          <div class="muted">Latest discovery run. Episode counts and confidence come straight from the model — curate from here.</div>
        </div>
        <div id="discovery-topic-map-meta" class="muted"></div>
      </div>
      <div id="discovery-topic-map-grid" class="topic-map-grid"></div>
    </section>

    <section class="topic-map">
      <div class="topic-map-head">
        <div>
          <h2>Topic Map</h2>
          <div class="muted">Start here: choose the broad topic that looks worth exploring next.</div>
        </div>
        <button class="secondary" onclick="generateTopics()">Discover broad topics</button>
      </div>
      <div id="topic-map-grid" class="topic-map-grid"></div>
    </section>

    <section id="selected-topic-detail" class="topic-detail empty">
      <div class="muted">Choose a topic from the Topic Map to inspect what is ready to explore next.</div>
    </section>

    <div class="grid">
      <section class="panel">
        <div class="section-head">
          <div>
            <h2>Broad Topics</h2>
            <div class="muted">Review discovered topics and assign videos before drilling deeper.</div>
          </div>
        </div>
        <div id="topic-metrics" class="metrics"></div>
        <h3>Pending review</h3>
        <div id="topic-pending" class="cards"></div>
        <h3>Approved labels</h3>
        <div id="topic-approved" class="list"></div>
      </section>

      <section class="panel">
        <div class="section-head">
          <div>
            <h2>Subtopics</h2>
            <div class="muted">Choose a broad topic, then discover reusable clusters. Subtopic suggestions need 5+ related videos; smaller one-off labels are suppressed.</div>
          </div>
        </div>
        <div id="subtopic-metrics" class="metrics"></div>
        <h3>Pending review</h3>
        <div id="subtopic-pending" class="cards"></div>
        <h3>Approved labels</h3>
        <div id="subtopic-approved" class="list"></div>
      </section>

      <section class="panel">
        <div class="section-head">
          <div>
            <h2>Comparison Readiness</h2>
            <div class="muted">Check whether a subtopic has enough videos and transcripts to compare.</div>
          </div>
        </div>
        <div id="comparison-metrics" class="metrics"></div>
        <h3>Pending review</h3>
        <div id="comparison-pending" class="cards"></div>
        <h3>Approved groups in this subtopic</h3>
        <div id="comparison-approved" class="list"></div>
      </section>
    </div>
  </div>

  <script>
    const state = { payload: null, activeTopicName: null };

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function setStatus(message, isError = false) {
      const box = document.getElementById('status-box');
      box.textContent = message;
      box.style.borderColor = isError ? 'rgba(252, 165, 165, 0.35)' : 'var(--border)';
      box.style.background = isError ? 'rgba(252, 165, 165, 0.08)' : 'rgba(125, 211, 252, 0.08)';
    }

    function selectedRunId(overrideValue = undefined) {
      const value = overrideValue ?? document.getElementById('run-select').value;
      return value ? Number(value) : null;
    }

    function selectedTopicName(overrideValue = undefined) {
      const value = overrideValue ?? document.getElementById('topic-select').value;
      return value || null;
    }

    function selectedSubtopicName(overrideValue = undefined) {
      const value = overrideValue ?? document.getElementById('subtopic-select').value;
      return value || null;
    }

    function selectedModelName() {
      return document.getElementById('model-input').value.trim() || 'gpt-4.1-mini';
    }

    function selectedLimit() {
      const raw = document.getElementById('limit-input').value.trim();
      return raw ? Number(raw) : null;
    }

    async function fetchState(options = {}) {
      const params = new URLSearchParams();
      const runId = selectedRunId(options.runId);
      const topic = selectedTopicName(options.topic);
      const subtopic = selectedSubtopicName(options.subtopic);
      if (runId) params.set('run_id', String(runId));
      if (topic) params.set('topic', topic);
      if (subtopic) params.set('subtopic', subtopic);
      const response = await fetch(`/api/state?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Failed to load state');
      state.payload = payload;
      render();
      const topicSummary = payload.topic_reviews.summary;
      const subtopicSummary = payload.subtopic_reviews.summary;
      const comparisonSummary = payload.comparison_reviews.summary;
      const dataset = payload.dataset_name || payload.db_path;
      const runScope = payload.current_run?.scope_label || 'no run selected';
      const selectedTopic = payload.subtopic_reviews.selected_topic ? ` · topic ${payload.subtopic_reviews.selected_topic}` : '';
      const selectedSubtopic = payload.comparison_reviews.selected_subtopic ? ` · subtopic ${payload.comparison_reviews.selected_subtopic}` : '';
      setStatus(`Dataset ${dataset} · run ${payload.run_id ?? 'none'} (${runScope})${selectedTopic}${selectedSubtopic} · pending topics ${topicSummary.pending} · pending subtopics ${subtopicSummary.pending} · pending comparison groups ${comparisonSummary.pending}.`);
    }

    function renderSelect(selectId, options, selected, getValue, getLabel) {
      const select = document.getElementById(selectId);
      select.innerHTML = '';
      for (const option of options) {
        const node = document.createElement('option');
        node.value = getValue(option);
        node.textContent = getLabel(option);
        if (String(node.value) === String(selected ?? '')) node.selected = true;
        select.appendChild(node);
      }
    }

    function metricHtml(label, value) {
      return `<div class="metric"><span class="k">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }

    function renderSummary(containerId, summary) {
      document.getElementById(containerId).innerHTML = [
        metricHtml('Pending', summary.pending),
        metricHtml('Approved', summary.approved),
        metricHtml('Rejected', summary.rejected),
        metricHtml('Superseded', summary.superseded)
      ].join('');
    }

    function renderContext(context) {
      const container = document.getElementById('context-grid');
      const run = context.current_run;
      const currentRunText = run
        ? [
            `run ${escapeHtml(run.id)} · ${escapeHtml(run.scope_label)}`,
            `<span class="muted">${escapeHtml(run.created_at || 'created time unknown')} · model ${escapeHtml(run.model_name)}</span>`,
            `<span class="muted">topic labels ${escapeHtml(run.label_count)}, subtopic labels ${escapeHtml(run.subtopic_label_count || 0)}, comparison labels ${escapeHtml(run.comparison_label_count || 0)}, suggestions ${escapeHtml(run.suggestion_row_count || 0)}</span>`
          ].join('<br>')
        : '<span class="muted">No suggestion run selected yet.</span>';
      const topicScopeText = context.subtopic_reviews.selected_topic
        ? `${escapeHtml(context.subtopic_reviews.selected_topic)}<br><span class="muted">${escapeHtml(context.subtopic_reviews.eligible_video_count || 0)} eligible video(s) for new subtopic suggestions in this topic.</span>`
        : '<span class="muted">Choose an approved topic to review or generate subtopics.</span>';
      const comparisonScopeText = context.comparison_reviews.selected_subtopic
        ? `${escapeHtml(context.comparison_reviews.selected_subtopic)}<br><span class="muted">${escapeHtml(context.comparison_reviews.eligible_video_count || 0)} eligible video(s) for new comparison-group suggestions in this subtopic.</span>`
        : '<span class="muted">Choose an approved subtopic to review or generate comparison groups.</span>';
      container.innerHTML = `
        <div class="context-card">
          <span class="k">Dataset</span>
          <strong>${escapeHtml(context.dataset_name || '(unknown)')}</strong>
          <div class="muted"><code>${escapeHtml(context.db_path)}</code></div>
          <div class="muted">${escapeHtml(context.dataset_video_count || 0)} stored video(s) in this dataset.</div>
        </div>
        <div class="context-card">
          <span class="k">Primary channel</span>
          <strong>${escapeHtml(context.channel_title || '(unknown)')}</strong>
          <div class="muted">${escapeHtml(context.channel_id || '')}</div>
        </div>
        <div class="context-card">
          <span class="k">Selected run</span>
          <strong>${currentRunText}</strong>
        </div>
        <div class="context-card">
          <span class="k">Subtopic scope</span>
          <strong>${topicScopeText}</strong>
        </div>
        <div class="context-card">
          <span class="k">Comparison-group scope</span>
          <strong>${comparisonScopeText}</strong>
        </div>
      `;
    }

    function statusChipHtml(item) {
      const status = item.status || 'empty';
      if (status === 'needs_review') return '<span class="status-chip warn">Needs review</span>';
      if (status === 'approved_not_applied') return '<span class="status-chip warn">Approved, not applied</span>';
      if (status === 'ready_to_explore') return '<span class="status-chip good">Ready to explore</span>';
      if (status === 'suggested') return '<span class="status-chip accent">Suggested</span>';
      return '<span class="status-chip">No current suggestions</span>';
    }

    const DEFAULT_DISCOVERY_SORT = 'recency';
    const discoveryEpisodeSortByTopic = new Map();
    let lastDiscoveryTopicMap = null;

    function sortDiscoveryEpisodes(episodes, mode) {
      const list = (episodes || []).slice();
      const m = mode || DEFAULT_DISCOVERY_SORT;
      if (m === 'confidence') {
        list.sort((a, b) => {
          const ac = (a.confidence == null) ? -Infinity : a.confidence;
          const bc = (b.confidence == null) ? -Infinity : b.confidence;
          if (bc !== ac) return bc - ac;
          return (a.title || '').localeCompare(b.title || '', undefined, { sensitivity: 'base' });
        });
      } else {
        list.sort((a, b) => {
          const ad = a.published_at || '';
          const bd = b.published_at || '';
          if (ad && !bd) return -1;
          if (!ad && bd) return 1;
          if (bd !== ad) return bd < ad ? -1 : 1;
          return (a.title || '').localeCompare(b.title || '', undefined, { sensitivity: 'base' });
        });
      }
      return list;
    }

    function setDiscoveryEpisodeSort(topicName, mode) {
      discoveryEpisodeSortByTopic.set(topicName, mode);
      renderDiscoveryTopicMap(lastDiscoveryTopicMap);
    }

    async function splitDiscoveryTopic(sourceName) {
      const topic = (lastDiscoveryTopicMap?.topics || []).find((t) => t.name === sourceName);
      if (!topic || !topic.episodes || !topic.episodes.length) {
        window.alert(`No episodes available to split off from "${sourceName}".`);
        return;
      }
      const existingNames = new Set((lastDiscoveryTopicMap?.topics || []).map((t) => t.name));
      const proposedName = window.prompt(
        `Split episodes off "${sourceName}" into a NEW topic. New topic name:`,
      );
      if (proposedName == null) return;
      const newName = proposedName.trim();
      if (!newName || newName === sourceName) return;
      if (existingNames.has(newName)) {
        window.alert(`"${newName}" already exists. Use Merge to combine, or pick a different name.`);
        return;
      }
      const list = topic.episodes
        .map((e, i) => `${i + 1}. ${e.title || '(untitled)'} [${e.youtube_video_id}]`)
        .join('\\n');
      const selectionRaw = window.prompt(
        `Which episodes go to "${newName}"? Enter indices comma-separated (e.g. 1,3,5).\n\n${list}`,
      );
      if (selectionRaw == null) return;
      const indices = selectionRaw
        .split(',')
        .map((s) => parseInt(s.trim(), 10) - 1)
        .filter((i) => Number.isInteger(i) && i >= 0 && i < topic.episodes.length);
      const uniqueIndices = Array.from(new Set(indices));
      if (!uniqueIndices.length) {
        window.alert('No valid episode indices selected.');
        return;
      }
      if (uniqueIndices.length === topic.episodes.length) {
        window.alert(`Selecting every episode would empty "${sourceName}". Use Rename instead.`);
        return;
      }
      const ids = uniqueIndices.map((i) => topic.episodes[i].youtube_video_id);
      if (!window.confirm(
        `Move ${ids.length} episode(s) from "${sourceName}" to new topic "${newName}"?`,
      )) return;
      await mutate(
        '/api/discovery/topic/split',
        { source_name: sourceName, new_name: newName, youtube_video_ids: ids },
        `Split ${ids.length} episode(s) from "${sourceName}" into "${newName}".`,
      );
    }

    async function mergeDiscoveryTopic(sourceName) {
      const otherTopics = (lastDiscoveryTopicMap?.topics || [])
        .map((t) => t.name)
        .filter((n) => n !== sourceName);
      if (!otherTopics.length) {
        window.alert(`No other discovery topic to merge "${sourceName}" into.`);
        return;
      }
      const proposed = window.prompt(
        `Merge "${sourceName}" into which topic? Options: ${otherTopics.join(', ')}`,
      );
      if (proposed == null) return;
      const targetName = proposed.trim();
      if (!targetName || targetName === sourceName) return;
      if (!otherTopics.includes(targetName)) {
        window.alert(`"${targetName}" is not an existing discovery topic.`);
        return;
      }
      if (!window.confirm(
        `Merging "${sourceName}" into "${targetName}" will reassign its episodes and delete "${sourceName}". Continue?`,
      )) return;
      discoveryEpisodeSortByTopic.delete(sourceName);
      await mutate(
        '/api/discovery/topic/merge',
        { source_name: sourceName, target_name: targetName },
        `Merged discovery topic "${sourceName}" into "${targetName}".`,
      );
    }

    async function renameDiscoveryTopic(currentName) {
      const proposed = window.prompt(`Rename discovery topic "${currentName}" to:`, currentName);
      if (proposed == null) return;
      const newName = proposed.trim();
      if (!newName || newName === currentName) return;
      const previousMode = discoveryEpisodeSortByTopic.get(currentName);
      if (previousMode !== undefined) {
        discoveryEpisodeSortByTopic.delete(currentName);
        discoveryEpisodeSortByTopic.set(newName, previousMode);
      }
      await mutate(
        '/api/discovery/topic/rename',
        { current_name: currentName, new_name: newName },
        `Renamed discovery topic "${currentName}" to "${newName}".`,
      );
    }

    function renderChannelOverview(overview) {
      const titleEl = document.getElementById('channel-overview-title');
      const subtitleEl = document.getElementById('channel-overview-subtitle');
      const statsEl = document.getElementById('channel-overview-stats');
      const latestEl = document.getElementById('channel-overview-latest');
      if (!overview) {
        titleEl.textContent = 'Channel Overview';
        subtitleEl.textContent = 'No primary channel set';
        statsEl.innerHTML = '';
        latestEl.innerHTML = '';
        return;
      }
      titleEl.textContent = overview.channel_title || 'Channel Overview';
      subtitleEl.textContent = overview.channel_id ? `Channel ID: ${overview.channel_id}` : '';
      const tiles = [
        ['Videos', overview.video_count],
        ['Transcripts', overview.transcript_count],
        ['Topics', overview.topic_count],
        ['Subtopics', overview.subtopic_count],
        ['Comparison groups', overview.comparison_group_count],
      ];
      statsEl.innerHTML = tiles.map(([label, value]) => `
        <div class="topic-stat"><span class="k">${escapeHtml(label)}</span><strong>${escapeHtml(value == null ? 0 : value)}</strong></div>
      `).join('');
      const latest = overview.latest_discovery;
      if (!latest) {
        latestEl.innerHTML = '<div class="muted"><strong>Latest discovery</strong> · <em>No discovery yet — run <code>analyze</code> or <code>discover</code> to start.</em></div>';
        return;
      }
      latestEl.innerHTML = `<div class="muted"><strong>Latest discovery</strong> · run #${escapeHtml(latest.id)} · ${escapeHtml(latest.status)} · ${escapeHtml(latest.started_at)} · ${escapeHtml(latest.model)} · ${escapeHtml(latest.prompt_version)}</div>`;
    }

    function renderDiscoveryTopicMap(map) {
      lastDiscoveryTopicMap = map;
      const grid = document.getElementById('discovery-topic-map-grid');
      const meta = document.getElementById('discovery-topic-map-meta');
      if (!map) {
        grid.innerHTML = '<div class="empty">No discovery run yet. Run <code>analyze --stub</code> or <code>discover --stub</code> to populate this panel.</div>';
        meta.textContent = '';
        return;
      }
      meta.textContent = `Run #${map.run_id} · ${map.model} · ${map.prompt_version} · ${map.status} · ${map.created_at}`;
      const topics = map.topics || [];
      if (!topics.length) {
        grid.innerHTML = '<div class="empty">Latest discovery run produced no topic assignments.</div>';
        return;
      }
      const lowThreshold = (typeof map.low_confidence_threshold === 'number') ? map.low_confidence_threshold : 0.5;
      const newTopicNames = new Set(Array.isArray(map.new_topic_names) ? map.new_topic_names : []);
      grid.innerHTML = topics.map((topic) => {
        const confidence = (topic.avg_confidence == null) ? null : Math.max(0, Math.min(1, topic.avg_confidence));
        const pct = (confidence == null) ? '—' : `${Math.round(confidence * 100)}%`;
        const barClass = (confidence != null && confidence < lowThreshold) ? 'low' : '';
        const barWidth = (confidence == null) ? 0 : Math.round(confidence * 100);
        const sortMode = discoveryEpisodeSortByTopic.get(topic.name) || DEFAULT_DISCOVERY_SORT;
        const sortedEpisodes = sortDiscoveryEpisodes(topic.episodes, sortMode);
        const episodeListHtml = sortedEpisodes.length
          ? `<ol class="discovery-episode-list">${sortedEpisodes.map((ep) => renderDiscoveryEpisodeItem(ep, topic.name, lowThreshold, true)).join('')}</ol>`
          : '<div class="muted discovery-episode-empty">No episodes assigned in this run.</div>';
        const sortRowHtml = (topic.episodes && topic.episodes.length)
          ? `<div class="discovery-episode-sort-row">
              <label>Sort
                <select class="discovery-episode-sort"
                        data-discovery-sort
                        data-topic-name="${escapeHtml(topic.name)}"
                        onchange="setDiscoveryEpisodeSort(this.dataset.topicName, this.value)">
                  <option value="recency" ${sortMode === 'recency' ? 'selected' : ''}>Recency</option>
                  <option value="confidence" ${sortMode === 'confidence' ? 'selected' : ''}>Confidence</option>
                </select>
              </label>
            </div>`
          : '';
        const subtopicBucketsHtml = renderDiscoverySubtopicBuckets(topic, sortMode, lowThreshold);
        const subtopicCount = topic.subtopic_count || (topic.subtopics ? topic.subtopics.length : 0);
        const newBadgeHtml = newTopicNames.has(topic.name)
          ? '<span class="discovery-topic-new-badge">New</span>'
          : '';
        return `
          <article class="topic-card discovery-topic-card">
            <div class="discovery-topic-header">
              <h3>${escapeHtml(topic.name)}${newBadgeHtml}</h3>
              <div class="discovery-topic-actions">
                <button class="discovery-topic-rename"
                        type="button"
                        data-topic-name="${escapeHtml(topic.name)}"
                        onclick="renameDiscoveryTopic(this.dataset.topicName)">Rename</button>
                <button class="discovery-topic-merge"
                        type="button"
                        data-topic-name="${escapeHtml(topic.name)}"
                        onclick="mergeDiscoveryTopic(this.dataset.topicName)">Merge</button>
                <button class="discovery-topic-split"
                        type="button"
                        data-topic-name="${escapeHtml(topic.name)}"
                        onclick="splitDiscoveryTopic(this.dataset.topicName)">Split</button>
              </div>
            </div>
            <div class="topic-stats">
              <div class="topic-stat"><span class="k">Episodes</span><strong>${escapeHtml(topic.episode_count)}</strong></div>
              <div class="topic-stat"><span class="k">Subtopics</span><strong>${escapeHtml(subtopicCount)}</strong></div>
              <div class="topic-stat"><span class="k">Avg confidence</span><strong>${escapeHtml(pct)}</strong></div>
            </div>
            <div class="confidence-bar ${barClass}"><span style="width:${barWidth}%"></span></div>
            ${subtopicBucketsHtml}
            ${sortRowHtml}
            ${episodeListHtml}
          </article>
        `;
      }).join('');
    }

    function renderDiscoverySubtopicBuckets(topic, sortMode, lowThreshold) {
      const subtopics = topic.subtopics || [];
      const unassigned = topic.unassigned_within_topic || [];
      if (!subtopics.length && !unassigned.length) return '';
      const bucketHtml = subtopics.map((bucket) => {
        const eps = sortDiscoveryEpisodes(bucket.episodes || [], sortMode);
        const items = eps.length
          ? `<ol class="discovery-episode-list">${eps.map((ep) => renderDiscoveryEpisodeItem(ep, topic.name, lowThreshold)).join('')}</ol>`
          : '<div class="muted discovery-episode-empty">No episodes assigned in this run.</div>';
        return `
          <details class="discovery-subtopic-bucket">
            <summary>${escapeHtml(bucket.name)} <span class="pill">${escapeHtml(bucket.episode_count)}</span></summary>
            ${items}
          </details>`;
      }).join('');
      const unassignedHtml = unassigned.length
        ? (() => {
            const eps = sortDiscoveryEpisodes(unassigned, sortMode);
            const items = `<ol class="discovery-episode-list">${eps.map((ep) => renderDiscoveryEpisodeItem(ep, topic.name, lowThreshold)).join('')}</ol>`;
            return `
              <details class="discovery-subtopic-bucket discovery-subtopic-unassigned">
                <summary>Unassigned within topic <span class="pill">${escapeHtml(unassigned.length)}</span></summary>
                ${items}
              </details>`;
          })()
        : '';
      return `<div class="discovery-subtopic-list">${bucketHtml}${unassignedHtml}</div>`;
    }

    function renderDiscoveryEpisodeItem(episode, topicName, lowThreshold, showAlsoIn) {
      const threshold = (typeof lowThreshold === 'number') ? lowThreshold : 0.5;
      const c = (episode.confidence == null) ? null : Math.max(0, Math.min(1, episode.confidence));
      const pct = (c == null) ? '—' : `${Math.round(c * 100)}%`;
      const lowClass = (c != null && c < threshold) ? ' low' : '';
      const reasonHtml = episode.reason
        ? `<div class="discovery-episode-reason">${escapeHtml(episode.reason)}</div>`
        : '';
      const thumbHtml = episode.thumbnail_url
        ? `<img class="discovery-episode-thumb" alt="" loading="lazy" src="${escapeHtml(episode.thumbnail_url)}">`
        : '<div class="discovery-episode-thumb placeholder" aria-hidden="true"></div>';
      const wrongButton = topicName
        ? `<button class="discovery-episode-wrong"
                   type="button"
                   onclick='markEpisodeWrong(${JSON.stringify(topicName)}, ${JSON.stringify(episode.youtube_video_id || '')}, null)'>Wrong topic?</button>`
        : '';
      const alsoIn = (showAlsoIn && Array.isArray(episode.also_in)) ? episode.also_in : [];
      const alsoInHtml = alsoIn.length
        ? `<span class="discovery-episode-also-in">also in: ${alsoIn.map((name) => escapeHtml(name)).join(', ')}</span>`
        : '';
      return `
        <li class="discovery-episode${lowClass}">
          ${thumbHtml}
          <div class="discovery-episode-body">
            <div class="discovery-episode-title">${escapeHtml(episode.title || '(untitled)')}</div>
            <div class="discovery-episode-meta">
              <span class="discovery-episode-confidence">${escapeHtml(pct)}</span>
              <span class="muted">${escapeHtml(episode.youtube_video_id || '')}</span>
              ${alsoInHtml}
            </div>
            ${reasonHtml}
            ${wrongButton}
          </div>
        </li>
      `;
    }

    async function markEpisodeWrong(topicName, youtubeVideoId, subtopicName) {
      const target = subtopicName
        ? `subtopic '${subtopicName}' under '${topicName}'`
        : `topic '${topicName}'`;
      if (!window.confirm(`Mark '${youtubeVideoId}' as wrong on ${target}? It will be removed from this assignment.`)) return;
      const result = await postJson('/api/discovery/episode/mark-wrong', {
        topic_name: topicName,
        youtube_video_id: youtubeVideoId,
        subtopic_name: subtopicName,
      });
      if (!result) return;
      setStatus(result.message || 'Marked wrong.');
      await fetchState();
    }

    function renderTopicMap(items) {
      const container = document.getElementById('topic-map-grid');
      if (!items || !items.length) {
        container.innerHTML = '<div class="empty">No topics yet. Generate topic suggestions or create topics first.</div>';
        return;
      }
      container.innerHTML = items.map((item, index) => `
        <article class="topic-card ${item.selected ? 'selected' : ''}">
          <div>${statusChipHtml(item)}</div>
          <h3>${escapeHtml(item.name)}</h3>
          <div class="topic-stats">
            <div class="topic-stat"><span class="k">Applied videos</span><strong>${escapeHtml(item.assignment_count || 0)}</strong></div>
            <div class="topic-stat"><span class="k">Pending review</span><strong>${escapeHtml(item.pending_count || 0)}</strong></div>
            <div class="topic-stat"><span class="k">Ready to apply</span><strong>${escapeHtml(item.apply_ready_count || 0)}</strong></div>
            <div class="topic-stat"><span class="k">Subtopics</span><strong>${escapeHtml((item.subtopic_count || 0) + (item.selected ? (state.payload?.subtopic_reviews?.summary?.pending || 0) : 0))}</strong></div>
          </div>
          <div class="muted">Primary ${escapeHtml(item.primary_count || 0)} · secondary ${escapeHtml(item.secondary_count || 0)}</div>
          <div class="actions">
            <button class="primary-action" onclick="selectTopicFromMap(${index})">Explore topic</button>
          </div>
        </article>
      `).join('');
    }

    async function selectTopicFromMap(index) {
      const item = state.payload?.topic_map?.[index];
      if (!item) {
        setStatus('Topic map selection failed. Refresh the page and try again.', true);
        return;
      }
      const topicName = item.name;
      state.activeTopicName = topicName;
      setStatus(`Opening topic lane: ${topicName}…`);
      const select = document.getElementById('topic-select');
      select.value = topicName;
      await fetchState({ topic: topicName, subtopic: null });
      document.getElementById('selected-topic-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function videoChipHtml(video) {
      return `<div class="video-chip">${escapeHtml(video.title || '(untitled)')}<div class="meta">${escapeHtml(video.youtube_video_id || '')}</div></div>`;
    }

    function subtopicVideoChipHtml(video, topicName, currentSubtopic, candidateSubtopics) {
      const moveButton = candidateSubtopics.length
        ? `<button class="subtopic-video-move"
                   type="button"
                   onclick='moveEpisodeSubtopic(${JSON.stringify(topicName)}, ${JSON.stringify(currentSubtopic)}, ${JSON.stringify(video.youtube_video_id || '')}, ${JSON.stringify(candidateSubtopics)})'>Move</button>`
        : '';
      const wrongButton = `<button class="subtopic-video-wrong"
                                   type="button"
                                   onclick='markEpisodeWrong(${JSON.stringify(topicName)}, ${JSON.stringify(video.youtube_video_id || '')}, ${JSON.stringify(currentSubtopic)})'>Wrong subtopic?</button>`;
      return `<div class="video-chip">${escapeHtml(video.title || '(untitled)')}<div class="meta">${escapeHtml(video.youtube_video_id || '')}</div>${moveButton}${wrongButton}</div>`;
    }

    async function moveEpisodeSubtopic(topicName, currentSubtopic, youtubeVideoId, candidates) {
      if (!candidates || !candidates.length) {
        setStatus(`No other subtopics under '${topicName}' to move to.`, true);
        return;
      }
      const numbered = candidates.map((name, i) => `${i + 1}. ${name}`).join('\\n');
      const raw = window.prompt(
        `Move '${youtubeVideoId}' from '${currentSubtopic}' to which subtopic under '${topicName}'?\n\n${numbered}\n\nEnter the number:`
      );
      if (raw == null) return;
      const idx = parseInt(raw.trim(), 10);
      if (!Number.isInteger(idx) || idx < 1 || idx > candidates.length) {
        setStatus('Move cancelled: invalid selection.', true);
        return;
      }
      const targetSubtopic = candidates[idx - 1];
      if (!window.confirm(`Move '${youtubeVideoId}' from '${currentSubtopic}' to '${targetSubtopic}'?`)) return;
      const result = await postJson('/api/discovery/episode/move-subtopic', {
        topic_name: topicName,
        youtube_video_id: youtubeVideoId,
        target_subtopic_name: targetSubtopic,
      });
      if (!result) return;
      setStatus(result.message || 'Moved.');
      await fetchState();
    }

    function topicInventoryHtml(inventory) {
      if (!inventory) return '';
      const buckets = inventory.subtopics || [];
      const topicName = inventory.topic || '';
      const allSubtopicNames = buckets.map((b) => b.name);
      const unassigned = inventory.unassigned_videos || [];
      const assignedHtml = buckets.length
        ? buckets.map((bucket) => {
            const candidates = allSubtopicNames.filter((n) => n !== bucket.name);
            const chips = bucket.videos.length
              ? bucket.videos.map((v) => subtopicVideoChipHtml(v, topicName, bucket.name, candidates)).join('')
              : '<div class="muted">No videos assigned yet.</div>';
            const readinessClass = ({
              too_few: 'readiness thin',
              needs_transcripts: 'readiness needs-transcripts',
              ready: 'readiness ready',
            })[bucket.readiness_state] || 'readiness thin';
            return `
            <div class="subtopic-bucket">
              <strong>${escapeHtml(bucket.name)}</strong>
              <span class="pill">${escapeHtml(bucket.videos.length)} video(s)</span>
              <span class="${readinessClass}">${bucket.readiness_label}</span>
              <div class="transcript-coverage">${bucket.transcript_count}/${bucket.video_count} transcripts</div>
              <div class="muted">${escapeHtml(bucket.next_step || '')}</div>
              <div class="video-list">
                ${chips}
              </div>
            </div>
          `;
          }).join('')
        : '<div class="empty">No approved subtopics exist for this topic yet.</div>';
      const unassignedHtml = unassigned.length
        ? `<div class="video-list">${unassigned.map(videoChipHtml).join('')}</div>`
        : '<div class="next-step good">All broad-topic videos are assigned to subtopics.</div>';
      return `
        <div class="topic-inventory">
          <div class="inventory-panel">
            <h3>Assigned subtopics</h3>
            <div class="muted">Videos already organised inside this broad topic.</div>
            ${assignedHtml}
          </div>
          <div class="inventory-panel">
            <h3>Unassigned videos</h3>
            <div class="muted">Broad-topic videos not yet assigned to any subtopic.</div>
            ${unassignedHtml}
          </div>
        </div>
      `;
    }

    function renderSelectedTopicDetail(payload) {
      const container = document.getElementById('selected-topic-detail');
      const selectedName = payload.subtopic_reviews?.selected_topic;
      const item = (payload.topic_map || []).find((topic) => topic.name === selectedName) || (payload.topic_map || []).find((topic) => topic.selected);
      if (!item) {
        container.className = 'topic-detail empty';
        container.innerHTML = '<div class="muted">Choose a topic from the Topic Map to inspect what is ready to explore next.</div>';
        return;
      }
      container.className = 'topic-detail';
      state.activeTopicName = item.name;
      const applied = item.assignment_count || item.applied_count || 0;
      const approvedSubtopics = item.subtopic_count || 0;
      const pendingSubtopics = payload.subtopic_reviews?.summary?.pending || 0;
      const suppressedSubtopics = payload.subtopic_reviews?.summary?.suppressed_low_support || 0;
      const subtopics = approvedSubtopics + pendingSubtopics;
      const eligible = payload.subtopic_reviews?.eligible_video_count || 0;
      const nextText = item.apply_ready_count > 0
        ? `Apply ${escapeHtml(item.apply_ready_count)} approved video suggestion(s) before drilling deeper.`
        : subtopics > 0
          ? 'This topic already has subtopics. Review or expand them next.'
          : applied > 0
            ? 'Ready to look for subtopics inside this topic.'
            : 'Review and apply topic suggestions before exploring subtopics.';
      container.innerHTML = `
        <div class="topic-detail-head">
          <div>
            <div class="eyebrow">Selected research lane</div>
            <h2>${escapeHtml(item.name)}</h2>
            <div>${statusChipHtml(item)}</div>
          </div>
          <div class="actions">
            <button class="primary-action" onclick="generateSubtopics()">Discover subtopics</button>
            <button class="secondary" onclick="document.getElementById('subtopic-pending').scrollIntoView({ behavior: 'smooth', block: 'start' })">Review subtopics</button>
          </div>
        </div>
        <div class="topic-stats">
          <div class="topic-stat"><span class="k">Applied videos</span><strong>${escapeHtml(applied)}</strong></div>
          <div class="topic-stat"><span class="k">Eligible for subtopics</span><strong>${escapeHtml(eligible)}</strong></div>
          <div class="topic-stat"><span class="k">Subtopics</span><strong>${escapeHtml(approvedSubtopics)}</strong></div>
          <div class="topic-stat"><span class="k">Pending subtopics</span><strong>${escapeHtml(pendingSubtopics)}</strong></div>
          <div class="topic-stat"><span class="k">Suppressed tiny labels</span><strong>${escapeHtml(suppressedSubtopics)}</strong></div>
        </div>
        <div class="workflow-rail">
          <div class="workflow-step"><strong>1. Broad topic</strong><span class="muted">${escapeHtml(applied)} video(s) assigned.</span></div>
          <div class="workflow-step current"><strong>2. Subtopics</strong><span class="muted">${escapeHtml(nextText)}</span></div>
          <div class="workflow-step"><strong>3. Compare</strong><span class="muted">Create groups once a subtopic has enough videos.</span></div>
        </div>
        ${topicInventoryHtml(payload.topic_inventory)}
      `;
    }

    function renderTopicCards(items) {
      const container = document.getElementById('topic-pending');
      if (!items.length) {
        container.innerHTML = '<div class="empty">No pending topic labels for this run.</div>';
        return;
      }
      container.innerHTML = items.map((item, index) => `
        <article class="card">
          <h4>${escapeHtml(item.name)}</h4>
          <div>
            <span class="pill">videos ${escapeHtml(item.video_count)}</span>
            <span class="pill">primary ${escapeHtml(item.primary_count)}</span>
            <span class="pill">secondary ${escapeHtml(item.secondary_count)}</span>
            <span class="pill">${item.approved_topic_exists ? 'reuses approved topic' : 'new topic label'}</span>
          </div>
          <ul class="samples">
            ${item.samples.map((sample) => `<li>[${escapeHtml(sample.assignment_type)}] ${escapeHtml(sample.video_title)} (${escapeHtml(sample.youtube_video_id)})</li>`).join('') || '<li>No sample videos.</li>'}
          </ul>
          <div class="next-step">Approving only accepts this label. To actually assign these videos to the topic, use <strong>Approve + apply to videos</strong>.</div>
          <input class="inline-field" id="topic-input-${index}" placeholder="Optional new approved name">
          <div class="actions">
            <button class="primary-action" onclick="approveAndApplyTopic(${index})">Approve + apply to videos</button>
            <button class="good" onclick="approveTopic(${index})">Approve label only</button>
            <button class="warn" onclick="renameTopic(${index})">Rename only</button>
            <button class="bad" onclick="rejectTopic(${index})">Reject</button>
          </div>
        </article>
      `).join('');
    }

    function renderApprovedTopics(items) {
      const container = document.getElementById('topic-approved');
      if (!items.length) {
        container.innerHTML = '<div class="empty">No approved topic labels yet.</div>';
        return;
      }
      container.innerHTML = items.map((item, index) => `
        <div class="card">
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <div class="muted">${escapeHtml(item.suggestion_count)} suggestions, primary ${escapeHtml(item.primary_count)}, secondary ${escapeHtml(item.secondary_count)}</div>
            <div class="muted">Ready to apply ${escapeHtml(item.apply_ready_count || 0)} · already applied ${escapeHtml(item.applied_count || 0)} · blocked ${escapeHtml(item.blocked_count || 0)}</div>
            ${item.apply_ready_count > 0
              ? `<div class="next-step">Approved but not applied: ${escapeHtml(item.apply_ready_count)} video(s) still need assigning to this topic.</div>`
              : `<div class="next-step good">No pending video assignments for this approved label.</div>`}
          </div>
          <div class="actions">
            ${item.apply_ready_count > 0
              ? `<button class="primary-action" onclick="bulkApplyTopic(${index})">Apply to ${escapeHtml(item.apply_ready_count)} video(s)</button>`
              : `<button class="secondary" onclick="bulkApplyTopic(${index})">Check/apply suggestions</button>`}
          </div>
          <div class="label-applications">
            ${item.applications.length ? item.applications.map((application, applicationIndex) => `
              <div class="application-row">
                <div>
                  <strong>${escapeHtml(application.video_title)}</strong>
                  <div class="meta">${escapeHtml(application.youtube_video_id)} · ${escapeHtml(application.assignment_type)} · ${escapeHtml(application.status_label)}</div>
                </div>
                ${application.can_apply
                  ? `<button class="good" onclick="applyTopicVideo(${index}, ${applicationIndex})">Apply</button>`
                  : `<span class="pill">${escapeHtml(application.status_label)}</span>`}
              </div>
            `).join('') : '<div class="empty">No approved topic suggestions in this run.</div>'}
          </div>
        </div>
      `).join('');
    }

    function renderSubtopicCards(items) {
      const container = document.getElementById('subtopic-pending');
      if (!items.length) {
        container.innerHTML = '<div class="empty">No pending subtopic labels for this topic and run.</div>';
        return;
      }
      container.innerHTML = items.map((item, index) => `
        <article class="card">
          <h4>${escapeHtml(item.name)}</h4>
          <div>
            <span class="pill">videos ${escapeHtml(item.video_count)}</span>
            <span class="pill">${item.approved_subtopic_exists ? 'reuses approved subtopic' : 'new subtopic label'}</span>
          </div>
          <ul class="samples">
            ${item.samples.map((sample) => `<li>${escapeHtml(sample.video_title)} (${escapeHtml(sample.youtube_video_id)})</li>`).join('') || '<li>No sample videos.</li>'}
          </ul>
          <div class="next-step">Approving only accepts this subtopic label. Use <strong>Approve + apply to videos</strong> to assign the suggested videos at the same time.</div>
          <input class="inline-field" id="subtopic-input-${index}" placeholder="Optional new approved name">
          <div class="actions">
            <button class="primary-action" onclick="approveAndApplySubtopic(${index})">Approve + apply to videos</button>
            <button class="good" onclick="approveSubtopic(${index})">Approve label only</button>
            <button class="warn" onclick="renameSubtopic(${index})">Rename only</button>
            <button class="bad" onclick="rejectSubtopic(${index})">Reject</button>
          </div>
        </article>
      `).join('');
    }

    function renderApprovedSubtopics(items) {
      const container = document.getElementById('subtopic-approved');
      if (!items.length) {
        container.innerHTML = '<div class="empty">No approved subtopic labels for this topic yet.</div>';
        return;
      }
      container.innerHTML = items.map((item, index) => `
        <div class="card">
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <div class="muted">${escapeHtml(item.suggestion_count)} suggestions, reused ${escapeHtml(item.reuse_existing_count)}</div>
            <div class="muted">Ready to apply ${escapeHtml(item.apply_ready_count || 0)} · already applied ${escapeHtml(item.applied_count || 0)}</div>
            ${item.apply_ready_count > 0
              ? `<div class="next-step">Approved but not applied: ${escapeHtml(item.apply_ready_count)} video(s) still need assigning to this subtopic.</div>`
              : `<div class="next-step good">No pending video assignments for this approved subtopic.</div>`}
          </div>
          <div class="actions">
            ${item.apply_ready_count > 0
              ? `<button class="primary-action" onclick="bulkApplySubtopic(${index})">Apply to ${escapeHtml(item.apply_ready_count)} video(s)</button>`
              : `<button class="secondary" onclick="bulkApplySubtopic(${index})">Check/apply suggestions</button>`}
          </div>
          <div class="label-applications">
            ${item.applications.length ? item.applications.map((application, applicationIndex) => `
              <div class="application-row">
                <div>
                  <strong>${escapeHtml(application.video_title)}</strong>
                  <div class="meta">${escapeHtml(application.youtube_video_id)} · ${escapeHtml(application.status_label)}</div>
                </div>
                ${application.can_apply
                  ? `<button class="good" onclick="applySubtopicVideo(${index}, ${applicationIndex})">Apply</button>`
                  : `<span class="pill">${escapeHtml(application.status_label)}</span>`}
              </div>
            `).join('') : '<div class="empty">No approved subtopic suggestions in this run.</div>'}
          </div>
        </div>
      `).join('');
    }

    function renderComparisonCards(items, approvedGroups) {
      const container = document.getElementById('comparison-pending');
      const mergeOptions = approvedGroups.map((group) => `<option value="${escapeHtml(group.name)}"></option>`).join('');
      const mergeHint = approvedGroups.length
        ? `<div class="muted">Merge target options: ${approvedGroups.map((group) => escapeHtml(group.name)).join(', ')}</div>`
        : '<div class="muted">No approved comparison groups yet in this subtopic.</div>';
      if (!items.length) {
        container.innerHTML = '<div class="empty">No pending comparison-group labels for this subtopic and run.</div>';
        return;
      }
      container.innerHTML = `${approvedGroups.length ? `<datalist id="comparison-approved-groups-options">${mergeOptions}</datalist>` : ''}` + items.map((item, index) => `
        <article class="card">
          <h4>${escapeHtml(item.name)}</h4>
          <div>
            <span class="pill">videos ${escapeHtml(item.video_count)}</span>
            <span class="pill">reused ${escapeHtml(item.reuse_existing_count || 0)}</span>
            <span class="pill">${item.approved_group_exists ? 'matches approved group' : 'new comparison-group label'}</span>
          </div>
          <ul class="samples">
            ${item.samples.map((sample) => `<li>${escapeHtml(sample.video_title)} (${escapeHtml(sample.youtube_video_id)})</li>`).join('') || '<li>No sample videos.</li>'}
          </ul>
          ${mergeHint}
          <input class="inline-field" id="comparison-input-${index}" placeholder="Optional approved group name" ${approvedGroups.length ? 'list="comparison-approved-groups-options"' : ''}>
          <div class="actions">
            <button class="good" onclick="approveComparisonGroup(${index})">Approve</button>
            <button class="warn" onclick="renameComparisonGroup(${index})">Rename only</button>
            <button class="bad" onclick="rejectComparisonGroup(${index})">Reject</button>
          </div>
        </article>
      `).join('');
    }

    function renderApprovedComparisonGroups(items) {
      const container = document.getElementById('comparison-approved');
      if (!items.length) {
        container.innerHTML = '<div class="empty">No approved comparison groups for this subtopic yet.</div>';
        return;
      }
      container.innerHTML = items.map((item) => `
        <div class="list-item">
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <div class="muted">members ${escapeHtml(item.member_count || 0)}${item.description ? ` · ${escapeHtml(item.description)}` : ''}</div>
          </div>
        </div>
      `).join('');
    }

    function render() {
      const payload = state.payload;
      renderSelect('run-select', payload.runs, payload.run_id, (item) => item.id, (item) => `run ${item.id} · ${item.scope_label} · topics ${item.pending_label_count} pending · subtopics ${item.subtopic_pending_label_count || 0} pending · comparison groups ${item.comparison_pending_label_count || 0} pending`);
      renderSelect('topic-select', payload.subtopic_reviews.available_topics.map((name) => ({ name })), payload.subtopic_reviews.selected_topic, (item) => item.name, (item) => item.name);
      renderSelect('subtopic-select', payload.comparison_reviews.available_subtopics.map((name) => ({ name })), payload.comparison_reviews.selected_subtopic, (item) => item.name, (item) => item.name);
      renderContext(payload);
      renderChannelOverview(payload.channel_overview);
      renderDiscoveryTopicMap(payload.discovery_topic_map);
      renderTopicMap(payload.topic_map);
      renderSelectedTopicDetail(payload);
      renderSummary('topic-metrics', payload.topic_reviews.summary);
      renderSummary('subtopic-metrics', payload.subtopic_reviews.summary);
      renderSummary('comparison-metrics', payload.comparison_reviews.summary);
      renderTopicCards(payload.topic_reviews.pending);
      renderApprovedTopics(payload.topic_reviews.approved);
      renderSubtopicCards(payload.subtopic_reviews.pending);
      renderApprovedSubtopics(payload.subtopic_reviews.approved);
      renderComparisonCards(payload.comparison_reviews.pending, payload.comparison_reviews.approved_groups);
      renderApprovedComparisonGroups(payload.comparison_reviews.approved_groups);
    }

    async function postJson(path, body) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Request failed');
      return payload;
    }

    async function mutate(path, body, successMessage) {
      try {
        const payload = await postJson(path, body);
        setStatus(payload.message || successMessage);
        await fetchState({
          runId: state.payload?.run_id,
          topic: state.payload?.subtopic_reviews?.selected_topic || null,
          subtopic: state.payload?.comparison_reviews?.selected_subtopic || null,
        });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function generateSuggestions(path, body, scopeLabel) {
      try {
        setStatus(`Generating ${scopeLabel} suggestions…`);
        const payload = await postJson(path, body);
        setStatus(payload.message || `Generated ${scopeLabel} suggestions.`);
        await fetchState({
          runId: payload.run_id,
          topic: payload.topic || body.topic || state.activeTopicName || state.payload?.subtopic_reviews?.selected_topic || null,
          subtopic: payload.subtopic || body.subtopic || state.payload?.comparison_reviews?.selected_subtopic || null,
        });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function currentTopicReviewItem(index) {
      return state.payload.topic_reviews.pending[index];
    }

    function currentApprovedTopicItem(index) {
      return state.payload.topic_reviews.approved[index];
    }

    function currentSubtopicReviewItem(index) {
      return state.payload.subtopic_reviews.pending[index];
    }

    function currentApprovedSubtopicItem(index) {
      return state.payload.subtopic_reviews.approved[index];
    }

    function currentComparisonReviewItem(index) {
      return state.payload.comparison_reviews.pending[index];
    }

    async function approveTopic(index) {
      const item = currentTopicReviewItem(index);
      const approvedName = document.getElementById(`topic-input-${index}`).value.trim();
      await mutate('/api/topic/approve', { run_id: state.payload.run_id, label: item.name, approved_name: approvedName || null }, `Approved ${item.name}. Next step: apply videos to this topic.`);
    }

    async function approveAndApplyTopic(index) {
      const item = currentTopicReviewItem(index);
      const approvedName = document.getElementById(`topic-input-${index}`).value.trim();
      await mutate('/api/topic/approve-and-apply', { run_id: state.payload.run_id, label: item.name, approved_name: approvedName || null }, `Approved and applied ${item.name}`);
    }

    async function rejectTopic(index) {
      const item = currentTopicReviewItem(index);
      await mutate('/api/topic/reject', { run_id: state.payload.run_id, label: item.name }, `Rejected ${item.name}`);
    }

    async function renameTopic(index) {
      const item = currentTopicReviewItem(index);
      const newName = document.getElementById(`topic-input-${index}`).value.trim();
      if (!newName) {
        setStatus('Enter a new topic label name first.', true);
        return;
      }
      await mutate('/api/topic/rename', { run_id: state.payload.run_id, current_name: item.name, new_name: newName }, `Renamed ${item.name}`);
    }

    async function bulkApplyTopic(index) {
      const item = currentApprovedTopicItem(index);
      await mutate('/api/topic/bulk-apply', { run_id: state.payload.run_id, label: item.name }, `Applied ${item.name} to ready videos`);
    }

    async function applyTopicVideo(index, applicationIndex) {
      const item = currentApprovedTopicItem(index);
      const application = item.applications[applicationIndex];
      await mutate('/api/topic/apply-video', { run_id: state.payload.run_id, label: item.name, video_id: application.youtube_video_id }, `Applied ${item.name} to ${application.youtube_video_id}`);
    }

    async function approveSubtopic(index) {
      const item = currentSubtopicReviewItem(index);
      const approvedName = document.getElementById(`subtopic-input-${index}`).value.trim();
      await mutate('/api/subtopic/approve', { run_id: state.payload.run_id, topic: state.payload.subtopic_reviews.selected_topic, label: item.name, approved_name: approvedName || null }, `Approved ${item.name}. Next step: apply videos to this subtopic.`);
    }

    async function approveAndApplySubtopic(index) {
      const item = currentSubtopicReviewItem(index);
      const approvedName = document.getElementById(`subtopic-input-${index}`).value.trim();
      await mutate('/api/subtopic/approve-and-apply', { run_id: state.payload.run_id, topic: state.payload.subtopic_reviews.selected_topic, label: item.name, approved_name: approvedName || null }, `Approved and applied ${item.name}`);
    }

    async function rejectSubtopic(index) {
      const item = currentSubtopicReviewItem(index);
      await mutate('/api/subtopic/reject', { run_id: state.payload.run_id, topic: state.payload.subtopic_reviews.selected_topic, label: item.name }, `Rejected ${item.name}`);
    }

    async function renameSubtopic(index) {
      const item = currentSubtopicReviewItem(index);
      const newName = document.getElementById(`subtopic-input-${index}`).value.trim();
      if (!newName) {
        setStatus('Enter a new subtopic label name first.', true);
        return;
      }
      await mutate('/api/subtopic/rename', { run_id: state.payload.run_id, topic: state.payload.subtopic_reviews.selected_topic, current_name: item.name, new_name: newName }, `Renamed ${item.name}`);
    }

    async function applySubtopicVideo(index, applicationIndex) {
      const item = currentApprovedSubtopicItem(index);
      const application = item.applications[applicationIndex];
      await mutate('/api/subtopic/apply-video', { run_id: state.payload.run_id, topic: state.payload.subtopic_reviews.selected_topic, label: item.name, video_id: application.youtube_video_id }, `Applied ${item.name} to ${application.youtube_video_id}`);
    }

    async function bulkApplySubtopic(index) {
      const item = currentApprovedSubtopicItem(index);
      await mutate('/api/subtopic/bulk-apply', { run_id: state.payload.run_id, topic: state.payload.subtopic_reviews.selected_topic, label: item.name }, `Applied ${item.name} to ready videos`);
    }

    async function approveComparisonGroup(index) {
      const item = currentComparisonReviewItem(index);
      const approvedName = document.getElementById(`comparison-input-${index}`).value.trim();
      await mutate('/api/comparison-group/approve', { run_id: state.payload.run_id, subtopic: state.payload.comparison_reviews.selected_subtopic, label: item.name, approved_name: approvedName || null }, `Approved ${item.name}`);
    }

    async function rejectComparisonGroup(index) {
      const item = currentComparisonReviewItem(index);
      await mutate('/api/comparison-group/reject', { run_id: state.payload.run_id, subtopic: state.payload.comparison_reviews.selected_subtopic, label: item.name }, `Rejected ${item.name}`);
    }

    async function renameComparisonGroup(index) {
      const item = currentComparisonReviewItem(index);
      const newName = document.getElementById(`comparison-input-${index}`).value.trim();
      if (!newName) {
        setStatus('Enter a new comparison-group label name first.', true);
        return;
      }
      await mutate('/api/comparison-group/rename', { run_id: state.payload.run_id, subtopic: state.payload.comparison_reviews.selected_subtopic, current_name: item.name, new_name: newName }, `Renamed ${item.name}`);
    }

    async function generateTopics() {
      await generateSuggestions('/api/generate/topics', { model: selectedModelName(), limit: selectedLimit() }, 'topic');
    }

    async function generateSubtopics() {
      const topic = state.activeTopicName || state.payload?.subtopic_reviews?.selected_topic || selectedTopicName();
      if (!topic) {
        setStatus('Choose an approved topic before generating subtopic suggestions.', true);
        return;
      }
      state.activeTopicName = topic;
      const select = document.getElementById('topic-select');
      select.value = topic;
      await generateSuggestions('/api/generate/subtopics', { topic, model: selectedModelName(), limit: selectedLimit() }, 'subtopic');
    }

    async function generateComparisonGroups(subtopicOverride = null) {
      const topic = state.activeTopicName || selectedTopicName();
      const subtopic = subtopicOverride || selectedSubtopicName();
      if (!topic) {
        setStatus('Choose an approved topic before generating comparison-group suggestions.', true);
        return;
      }
      if (!subtopic) {
        setStatus('Choose an approved subtopic before generating comparison-group suggestions.', true);
        return;
      }
      const subtopicSelect = document.getElementById('subtopic-select');
      if (subtopicSelect) subtopicSelect.value = subtopic;
      await generateSuggestions('/api/generate/comparison-groups', { topic, subtopic, model: selectedModelName(), limit: selectedLimit() }, 'comparison-group');
    }

    async function generateComparisonGroupsForSubtopic(subtopicName) {
      await generateComparisonGroups(subtopicName);
    }

    document.getElementById('refresh-btn').addEventListener('click', () => fetchState().catch((error) => setStatus(error.message, true)));
    document.getElementById('run-select').addEventListener('change', () => fetchState().catch((error) => setStatus(error.message, true)));
    document.getElementById('topic-select').addEventListener('change', () => {
      const newTopic = selectedTopicName();
      const map = state.payload?.latest_subtopic_run_id_by_topic || {};
      if (newTopic && Object.prototype.hasOwnProperty.call(map, newTopic)) {
        const targetRunId = String(map[newTopic]);
        const runSelect = document.getElementById('run-select');
        if (runSelect && String(runSelect.value) !== targetRunId) {
          runSelect.value = targetRunId;
        }
      }
      fetchState({ topic: newTopic, subtopic: null }).catch((error) => setStatus(error.message, true));
    });
    document.getElementById('subtopic-select').addEventListener('change', () => fetchState().catch((error) => setStatus(error.message, true)));
    document.getElementById('generate-topics-btn').addEventListener('click', () => generateTopics());
    document.getElementById('generate-subtopics-btn').addEventListener('click', () => generateSubtopics());
    document.getElementById('generate-comparison-groups-btn').addEventListener('click', () => generateComparisonGroups());

    fetchState().catch((error) => setStatus(error.message, true));
  </script>
</body>
</html>
"""


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class ReviewUIError(ValueError):
    pass


def _group_topic_review_rows(rows: list[Any]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        if current is None or current["name"] != row["name"]:
            current = {
                "name": row["name"],
                "video_count": int(row["video_count"] or 0),
                "primary_count": int(row["primary_count"] or 0),
                "secondary_count": int(row["secondary_count"] or 0),
                "approved_topic_exists": bool(row["approved_topic_exists"]),
                "samples": [],
            }
            grouped.append(current)
        if row["youtube_video_id"] is not None:
            current["samples"].append(
                {
                    "youtube_video_id": row["youtube_video_id"],
                    "video_title": row["video_title"],
                    "assignment_type": row["assignment_type"],
                }
            )
    return grouped


def _group_subtopic_review_rows(rows: list[Any]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        if current is None or current["name"] != row["name"]:
            current = {
                "name": row["name"],
                "video_count": int(row["video_count"] or 0),
                "approved_subtopic_exists": bool(row["approved_subtopic_exists"]),
                "samples": [],
            }
            grouped.append(current)
        if row["youtube_video_id"] is not None:
            current["samples"].append(
                {
                    "youtube_video_id": row["youtube_video_id"],
                    "video_title": row["video_title"],
                }
            )
    return grouped


def _group_comparison_review_rows(rows: list[Any]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        if current is None or current["name"] != row["name"]:
            reuse_existing_count = int(row["reuse_existing_count"] or 0) if "reuse_existing_count" in row.keys() else 0
            current = {
                "name": row["name"],
                "video_count": int(row["video_count"] or 0),
                "reuse_existing_count": reuse_existing_count,
                "approved_group_exists": bool(row["approved_group_exists"]),
                "samples": [],
            }
            grouped.append(current)
        if row["youtube_video_id"] is not None:
            current["samples"].append(
                {
                    "youtube_video_id": row["youtube_video_id"],
                    "video_title": row["video_title"],
                }
            )
    return grouped


def _summary_counts(rows: list[Any]) -> dict[str, int]:
    counts = {"pending": 0, "approved": 0, "rejected": 0, "superseded": 0}
    for row in rows:
        status = str(row["status"])
        if status in counts:
            counts[status] += 1
    return counts


def _group_topic_application_rows(rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        already_applied = bool(row["already_applied"])
        conflicting_primary = bool(row["conflicting_primary"])
        status_label = "Already applied" if already_applied else "Blocked by existing primary topic" if conflicting_primary else "Ready to apply"
        grouped.setdefault(str(row["suggested_label"]), []).append(
            {
                "youtube_video_id": row["youtube_video_id"],
                "video_title": row["video_title"],
                "assignment_type": row["assignment_type"],
                "already_applied": already_applied,
                "conflicting_primary": conflicting_primary,
                "can_apply": not already_applied and not conflicting_primary,
                "status_label": status_label,
            }
        )
    return grouped


def _group_subtopic_application_rows(rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        already_applied = bool(row["already_applied"])
        status_label = "Already applied" if already_applied else "Ready to apply"
        grouped.setdefault(str(row["suggested_label"]), []).append(
            {
                "youtube_video_id": row["youtube_video_id"],
                "video_title": row["video_title"],
                "already_applied": already_applied,
                "can_apply": not already_applied,
                "status_label": status_label,
            }
        )
    return grouped


def _parse_limit(value: object) -> int | None:
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    limit = int(normalized)
    if limit <= 0:
        raise ReviewUIError("limit must be a positive integer")
    return limit


def _enrich_runs_with_subtopic_counts(db_path: Path, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in runs:
        run = dict(row)
        subtopic_rows = [dict(item) for item in summarize_subtopic_suggestion_labels(db_path, run_id=int(run["id"]))]
        subtopic_counts = _summary_counts(subtopic_rows)
        subtopic_topics = sorted({str(item["topic_name"]) for item in subtopic_rows if item.get("topic_name")}, key=str.casefold)
        comparison_rows = [dict(item) for item in summarize_comparison_group_suggestion_labels(db_path, run_id=int(run["id"]))]
        comparison_counts = _summary_counts(comparison_rows)
        comparison_topics = sorted({str(item["topic_name"]) for item in comparison_rows if item.get("topic_name")}, key=str.casefold)
        comparison_subtopics = sorted({str(item["subtopic_name"]) for item in comparison_rows if item.get("subtopic_name")}, key=str.casefold)
        scope_label = "Topic suggestions"
        if comparison_rows and not run.get("label_count") and not subtopic_rows:
            if len(comparison_subtopics) == 1:
                scope_label = f"Comparison groups · {comparison_subtopics[0]}"
            else:
                scope_label = "Comparison groups"
        elif subtopic_rows and not run.get("label_count"):
            if len(subtopic_topics) == 1:
                scope_label = f"Subtopic suggestions · {subtopic_topics[0]}"
            else:
                scope_label = "Subtopic suggestions"
        elif subtopic_rows or comparison_rows:
            scope_label = "Mixed topic + subtopic suggestions"
        run.update(
            {
                "scope_label": scope_label,
                "subtopic_label_count": len(subtopic_rows),
                "subtopic_pending_label_count": subtopic_counts["pending"],
                "subtopic_approved_label_count": subtopic_counts["approved"],
                "subtopic_rejected_label_count": subtopic_counts["rejected"],
                "subtopic_superseded_label_count": subtopic_counts["superseded"],
                "subtopic_topic_count": len(subtopic_topics),
                "subtopic_topics": subtopic_topics,
                "comparison_label_count": len(comparison_rows),
                "comparison_pending_label_count": comparison_counts["pending"],
                "comparison_approved_label_count": comparison_counts["approved"],
                "comparison_rejected_label_count": comparison_counts["rejected"],
                "comparison_superseded_label_count": comparison_counts["superseded"],
                "comparison_topic_count": len(comparison_topics),
                "comparison_topics": comparison_topics,
                "comparison_subtopic_count": len(comparison_subtopics),
                "comparison_subtopics": comparison_subtopics,
            }
        )
        enriched.append(run)
    return enriched


def _latest_subtopic_run_id_for_topic(
    db_path: str | Path, topic_name: str
) -> int | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT MAX(subtopic_suggestion_labels.suggestion_run_id) AS run_id
              FROM subtopic_suggestion_labels
              JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
             WHERE topics.name = ?
            """,
            (topic_name,),
        ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _latest_subtopic_run_ids_by_topic(db_path: str | Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT topics.name AS topic_name,
                   MAX(subtopic_suggestion_labels.suggestion_run_id) AS run_id
              FROM subtopic_suggestion_labels
              JOIN topics ON topics.id = subtopic_suggestion_labels.topic_id
          GROUP BY topics.name
            """
        ).fetchall()
    return {
        str(row[0]): int(row[1])
        for row in rows
        if row[0] is not None and row[1] is not None
    }


def _build_topic_inventory(db_path: Path, *, topic_name: str | None) -> dict[str, Any] | None:
    if topic_name is None:
        return None
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        subtopic_rows = connection.execute(
            """
            SELECT
                subtopics.id AS subtopic_id,
                subtopics.name AS subtopic_name,
                videos.id AS video_id,
                videos.youtube_video_id,
                videos.title,
                CASE WHEN video_transcripts.video_id IS NOT NULL THEN 1 ELSE 0 END
                    AS transcript_available,
                CASE WHEN processed_videos.video_id IS NOT NULL THEN 1 ELSE 0 END
                    AS processed_ok
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            LEFT JOIN video_subtopics ON video_subtopics.subtopic_id = subtopics.id
            LEFT JOIN videos ON videos.id = video_subtopics.video_id
            LEFT JOIN video_transcripts
                ON video_transcripts.video_id = videos.id
               AND video_transcripts.transcript_status = 'available'
            LEFT JOIN processed_videos
                ON processed_videos.video_id = videos.id
               AND processed_videos.processing_status = 'processed'
            WHERE topics.name = ?
            ORDER BY subtopics.name COLLATE NOCASE, videos.published_at DESC, videos.id DESC
            """,
            (topic_name,),
        ).fetchall()
        unassigned_rows = connection.execute(
            """
            SELECT DISTINCT videos.youtube_video_id, videos.title
            FROM videos
            JOIN video_topics ON video_topics.video_id = videos.id
            JOIN topics ON topics.id = video_topics.topic_id
            WHERE topics.name = ?
              AND NOT EXISTS (
                SELECT 1
                FROM video_subtopics
                JOIN subtopics ON subtopics.id = video_subtopics.subtopic_id
                WHERE video_subtopics.video_id = videos.id
                  AND subtopics.topic_id = topics.id
              )
            ORDER BY videos.published_at DESC, videos.id DESC
            """,
            (topic_name,),
        ).fetchall()

    buckets: dict[int, dict[str, Any]] = {}
    for row in subtopic_rows:
        bucket = buckets.setdefault(
            int(row["subtopic_id"]),
            {
                "name": row["subtopic_name"],
                "videos": [],
                "_seen_video_ids": set(),
                "transcript_count": 0,
                "processed_count": 0,
            },
        )
        if row["youtube_video_id"] is None:
            continue
        video_id = int(row["video_id"])
        if video_id in bucket["_seen_video_ids"]:
            continue
        bucket["_seen_video_ids"].add(video_id)
        bucket["videos"].append(
            {"youtube_video_id": row["youtube_video_id"], "title": row["title"]}
        )
        if int(row["transcript_available"] or 0):
            bucket["transcript_count"] += 1
        if int(row["processed_ok"] or 0):
            bucket["processed_count"] += 1
    subtopic_buckets = list(buckets.values())
    for bucket in subtopic_buckets:
        bucket.pop("_seen_video_ids", None)
        video_count = len(bucket["videos"])
        bucket["video_count"] = video_count
        if video_count < MIN_NEW_SUBTOPIC_CLUSTER_SIZE:
            bucket["readiness_state"] = "too_few"
            needed = MIN_NEW_SUBTOPIC_CLUSTER_SIZE - video_count
            bucket["readiness_label"] = "Too thin to compare"
            bucket["next_step"] = (
                f"Needs {needed} more video(s) before comparison groups are useful."
            )
        elif bucket["transcript_count"] == 0:
            bucket["readiness_state"] = "needs_transcripts"
            bucket["readiness_label"] = "Enough videos, no transcripts"
            bucket["next_step"] = (
                "Fetch transcripts for these videos before generating comparison groups."
            )
        else:
            bucket["readiness_state"] = "ready"
            bucket["readiness_label"] = "Ready for comparison"
            bucket["next_step"] = "Enough videos to generate comparison-group suggestions."
        bucket["comparison_ready"] = bucket["readiness_state"] == "ready"
    return {
        "topic": topic_name,
        "subtopics": subtopic_buckets,
        "unassigned_videos": [
            {"youtube_video_id": row["youtube_video_id"], "title": row["title"]}
            for row in unassigned_rows
        ],
    }


def _resolve_primary_project_name(db_path: Path) -> str:
    primary_channel = get_primary_channel(db_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT name FROM projects WHERE id = ?",
            (primary_channel.project_id,),
        ).fetchone()
    if row is None:
        raise ReviewUIError(
            f"project not found for primary channel (project_id={primary_channel.project_id})"
        )
    return row[0]


def _build_channel_overview(
    db_path: Path, project_id: int, channel_id: int
) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        channel_row = connection.execute(
            "SELECT youtube_channel_id, title FROM channels WHERE id = ?",
            (channel_id,),
        ).fetchone()
        video_count = connection.execute(
            "SELECT COUNT(*) FROM videos WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()[0]
        transcript_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM video_transcripts
            JOIN videos ON videos.id = video_transcripts.video_id
            WHERE videos.channel_id = ?
            """,
            (channel_id,),
        ).fetchone()[0]
        topic_count = connection.execute(
            """
            SELECT COUNT(DISTINCT video_topics.topic_id)
            FROM video_topics
            JOIN videos ON videos.id = video_topics.video_id
            WHERE videos.channel_id = ?
            """,
            (channel_id,),
        ).fetchone()[0]
        subtopic_count = connection.execute(
            """
            SELECT COUNT(DISTINCT video_subtopics.subtopic_id)
            FROM video_subtopics
            JOIN videos ON videos.id = video_subtopics.video_id
            WHERE videos.channel_id = ?
            """,
            (channel_id,),
        ).fetchone()[0]
        comparison_group_count = connection.execute(
            """
            SELECT COUNT(DISTINCT comparison_group_videos.comparison_group_id)
            FROM comparison_group_videos
            JOIN videos ON videos.id = comparison_group_videos.video_id
            WHERE videos.channel_id = ?
            """,
            (channel_id,),
        ).fetchone()[0]
        latest_row = connection.execute(
            """
            SELECT id, status, created_at, model, prompt_version
            FROM discovery_runs
            WHERE channel_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (channel_id,),
        ).fetchone()

    if latest_row is None:
        latest_discovery: dict[str, Any] | None = None
    else:
        latest_discovery = {
            "id": int(latest_row["id"]),
            "status": latest_row["status"],
            "started_at": latest_row["created_at"],
            "model": latest_row["model"],
            "prompt_version": latest_row["prompt_version"],
        }

    return {
        "channel_title": channel_row["title"] if channel_row is not None else None,
        "channel_id": channel_row["youtube_channel_id"] if channel_row is not None else None,
        "video_count": int(video_count),
        "transcript_count": int(transcript_count),
        "topic_count": int(topic_count),
        "subtopic_count": int(subtopic_count),
        "comparison_group_count": int(comparison_group_count),
        "latest_discovery": latest_discovery,
    }


def _topics_introduced_in_run(
    connection: sqlite3.Connection, channel_id: int, run_id: int
) -> list[str]:
    has_earlier_run = connection.execute(
        "SELECT 1 FROM discovery_runs WHERE channel_id = ? AND id < ? LIMIT 1",
        (channel_id, run_id),
    ).fetchone()
    if has_earlier_run is None:
        return []
    rows = connection.execute(
        """
        SELECT DISTINCT t.name AS name
        FROM topics t
        JOIN video_topics vt ON vt.topic_id = t.id
        WHERE vt.discovery_run_id = ?
          AND t.first_discovery_run_id = ?
        ORDER BY t.name COLLATE NOCASE
        """,
        (run_id, run_id),
    ).fetchall()
    return [row[0] for row in rows]


def _build_discovery_topic_map(db_path: Path) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        run_row = connection.execute(
            """
            SELECT id, channel_id, model, prompt_version, status, created_at
            FROM discovery_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if run_row is None:
            return None
        new_topic_names = _topics_introduced_in_run(
            connection, int(run_row["channel_id"]), int(run_row["id"])
        )

        topic_rows = connection.execute(
            """
            SELECT topics.id AS topic_id,
                   topics.name AS topic_name,
                   COUNT(DISTINCT video_topics.video_id) AS episode_count,
                   AVG(video_topics.confidence) AS avg_confidence
            FROM video_topics
            JOIN topics ON topics.id = video_topics.topic_id
            WHERE video_topics.discovery_run_id = ?
            GROUP BY topics.id, topics.name
            ORDER BY episode_count DESC, topics.name COLLATE NOCASE
            """,
            (run_row["id"],),
        ).fetchall()

        episode_rows = connection.execute(
            """
            SELECT video_topics.topic_id AS topic_id,
                   videos.youtube_video_id AS youtube_video_id,
                   videos.title AS title,
                   videos.thumbnail_url AS thumbnail_url,
                   videos.published_at AS published_at,
                   video_topics.confidence AS confidence,
                   video_topics.reason AS reason
            FROM video_topics
            JOIN videos ON videos.id = video_topics.video_id
            WHERE video_topics.discovery_run_id = ?
            ORDER BY video_topics.confidence DESC, videos.title COLLATE NOCASE
            """,
            (run_row["id"],),
        ).fetchall()

        subtopic_rows = connection.execute(
            """
            SELECT subtopics.topic_id AS topic_id,
                   subtopics.name AS subtopic_name,
                   videos.youtube_video_id AS youtube_video_id
            FROM video_subtopics
            JOIN subtopics ON subtopics.id = video_subtopics.subtopic_id
            JOIN videos ON videos.id = video_subtopics.video_id
            WHERE video_subtopics.discovery_run_id = ?
            ORDER BY subtopics.name COLLATE NOCASE
            """,
            (run_row["id"],),
        ).fetchall()

    topic_id_to_name: dict[int, str] = {
        int(row["topic_id"]): row["topic_name"] for row in topic_rows
    }
    topics_by_video: dict[str, list[str]] = {}
    for row in episode_rows:
        topic_name = topic_id_to_name.get(int(row["topic_id"]))
        if topic_name is None:
            continue
        names = topics_by_video.setdefault(row["youtube_video_id"], [])
        if topic_name not in names:
            names.append(topic_name)

    episodes_by_topic: dict[int, list[dict[str, Any]]] = {}
    for row in episode_rows:
        topic_id = int(row["topic_id"])
        current_topic_name = topic_id_to_name.get(topic_id)
        also_in = [
            name
            for name in topics_by_video.get(row["youtube_video_id"], [])
            if name != current_topic_name
        ]
        episodes_by_topic.setdefault(topic_id, []).append(
            {
                "youtube_video_id": row["youtube_video_id"],
                "title": row["title"],
                "thumbnail_url": row["thumbnail_url"],
                "published_at": row["published_at"],
                "confidence": (
                    float(row["confidence"]) if row["confidence"] is not None else None
                ),
                "reason": row["reason"],
                "also_in": also_in,
            }
        )

    subtopic_assignment: dict[tuple[int, str], str] = {}
    subtopic_names_by_topic: dict[int, list[str]] = {}
    for row in subtopic_rows:
        topic_id = int(row["topic_id"])
        sub_name = row["subtopic_name"]
        names = subtopic_names_by_topic.setdefault(topic_id, [])
        if sub_name not in names:
            names.append(sub_name)
        subtopic_assignment[(topic_id, row["youtube_video_id"])] = sub_name

    def _bucket_topic(topic_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        names = subtopic_names_by_topic.get(topic_id, [])
        bucketed: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
        unassigned: list[dict[str, Any]] = []
        for ep in episodes_by_topic.get(topic_id, []):
            sub = subtopic_assignment.get((topic_id, ep["youtube_video_id"]))
            if sub is not None and sub in bucketed:
                bucketed[sub].append(ep)
            else:
                unassigned.append(ep)
        subtopic_payload = [
            {"name": name, "episode_count": len(bucketed[name]), "episodes": bucketed[name]}
            for name in names
        ]
        return subtopic_payload, unassigned

    def _topic_payload(row: sqlite3.Row) -> dict[str, Any]:
        topic_id = int(row["topic_id"])
        subtopic_payload, unassigned = _bucket_topic(topic_id)
        return {
            "name": row["topic_name"],
            "episode_count": int(row["episode_count"]),
            "avg_confidence": (
                float(row["avg_confidence"])
                if row["avg_confidence"] is not None
                else None
            ),
            "episodes": episodes_by_topic.get(topic_id, []),
            "subtopics": subtopic_payload,
            "subtopic_count": len(subtopic_payload),
            "unassigned_within_topic": unassigned,
        }

    return {
        "run_id": int(run_row["id"]),
        "model": run_row["model"],
        "prompt_version": run_row["prompt_version"],
        "status": run_row["status"],
        "created_at": run_row["created_at"],
        "low_confidence_threshold": _load_low_confidence_threshold(),
        "topics": [_topic_payload(row) for row in topic_rows],
        "new_topic_names": new_topic_names,
    }


def build_state_payload(
    db_path: str | Path,
    *,
    run_id: int | None = None,
    topic_name: str | None = None,
    subtopic_name: str | None = None,
    sample_limit: int = 3,
) -> dict[str, Any]:
    db_path = Path(db_path)
    try:
        primary_channel = get_primary_channel(db_path)
    except ValueError:
        primary_channel = None
    topic_generation_candidates = [dict(row) for row in list_videos_for_topic_suggestions(db_path)]
    runs = _enrich_runs_with_subtopic_counts(db_path, [dict(row) for row in list_topic_suggestion_runs(db_path)])
    latest_run_id = get_latest_topic_suggestion_run_id(db_path)
    resolved_run_id = run_id or latest_run_id
    current_run = next((row for row in runs if int(row["id"]) == int(resolved_run_id)), None) if resolved_run_id is not None else None

    approved_topic_rows: list[dict[str, Any]] = []
    pending_topic_rows: list[dict[str, Any]] = []
    topic_summary = {"pending": 0, "approved": 0, "rejected": 0, "superseded": 0}

    subtopic_approved_rows: list[dict[str, Any]] = []
    pending_subtopic_rows: list[dict[str, Any]] = []
    subtopic_summary = {"pending": 0, "approved": 0, "rejected": 0, "superseded": 0}
    suppressed_low_support_subtopic_count = 0

    approved_comparison_groups: list[dict[str, Any]] = []
    pending_comparison_rows: list[dict[str, Any]] = []
    comparison_summary = {"pending": 0, "approved": 0, "rejected": 0, "superseded": 0}
    available_subtopics: list[str] = []
    selected_subtopic = subtopic_name
    eligible_comparison_video_count = 0

    available_topics = sorted({row["name"] for row in list_topics(db_path)}, key=str.casefold)
    selected_topic = str(topic_name) if topic_name is not None else (available_topics[0] if available_topics else None)
    eligible_subtopic_video_count = 0

    if selected_topic is not None:
        eligible_subtopic_video_count = len(list_videos_for_subtopic_suggestions(db_path, topic_name=selected_topic))
        available_subtopics = [str(row["name"]) for row in list_approved_subtopics_for_topic(db_path, topic_name=selected_topic)]
        selected_subtopic = str(subtopic_name) if subtopic_name is not None else (available_subtopics[0] if available_subtopics else None)
        if selected_subtopic is not None:
            approved_comparison_groups = [
                dict(row)
                for row in list_approved_comparison_groups_for_subtopic(db_path, subtopic_name=selected_subtopic)
            ]
            eligible_comparison_video_count = len(
                list_videos_for_comparison_group_suggestions(db_path, subtopic_name=selected_subtopic)
            )

    if resolved_run_id is not None:
        topic_label_rows = [dict(row) for row in summarize_topic_suggestion_labels(db_path, run_id=resolved_run_id)]
        topic_summary = _summary_counts(topic_label_rows)
        approved_topic_rows = [row for row in topic_label_rows if row["status"] == "approved"]
        topic_application_rows = _group_topic_application_rows(list_topic_suggestion_application_rows(db_path, run_id=resolved_run_id))
        for row in approved_topic_rows:
            applications = topic_application_rows.get(str(row["name"]), [])
            row["applications"] = applications
            row["apply_ready_count"] = sum(1 for item in applications if item["can_apply"])
            row["applied_count"] = sum(1 for item in applications if item["already_applied"])
            row["blocked_count"] = sum(1 for item in applications if item["conflicting_primary"])
        pending_topic_rows = _group_topic_review_rows(
            get_topic_suggestion_review_rows(db_path, run_id=resolved_run_id, status="pending", sample_limit=sample_limit)
        )

        subtopic_all_rows = [dict(row) for row in summarize_subtopic_suggestion_labels(db_path, run_id=resolved_run_id)]
        subtopic_topics = sorted({str(row["topic_name"]) for row in subtopic_all_rows if row.get("topic_name")}, key=str.casefold)
        available_topics = subtopic_topics or available_topics
        if topic_name is None and available_topics:
            selected_topic = available_topics[0]
            eligible_subtopic_video_count = len(list_videos_for_subtopic_suggestions(db_path, topic_name=selected_topic))
            available_subtopics = [str(row["name"]) for row in list_approved_subtopics_for_topic(db_path, topic_name=selected_topic)]
            if subtopic_name is None:
                selected_subtopic = available_subtopics[0] if available_subtopics else None
                if selected_subtopic is not None:
                    approved_comparison_groups = [
                        dict(row)
                        for row in list_approved_comparison_groups_for_subtopic(db_path, subtopic_name=selected_subtopic)
                    ]
                    eligible_comparison_video_count = len(
                        list_videos_for_comparison_group_suggestions(db_path, subtopic_name=selected_subtopic)
                    )
        if selected_topic is not None:
            subtopic_topic_rows = [row for row in subtopic_all_rows if row.get("topic_name") == selected_topic]
            subtopic_summary = _summary_counts(subtopic_topic_rows)
            subtopic_approved_rows = [row for row in subtopic_topic_rows if row["status"] == "approved"]
            subtopic_application_rows = _group_subtopic_application_rows(
                list_subtopic_suggestion_application_rows(db_path, topic_name=selected_topic, run_id=resolved_run_id)
            )
            for row in subtopic_approved_rows:
                applications = subtopic_application_rows.get(str(row["name"]), [])
                row["applications"] = applications
                row["apply_ready_count"] = sum(1 for item in applications if item["can_apply"])
                row["applied_count"] = sum(1 for item in applications if item["already_applied"])
            if selected_subtopic is not None:
                comparison_all_rows = [
                    dict(row)
                    for row in summarize_comparison_group_suggestion_labels(
                        db_path,
                        subtopic_name=selected_subtopic,
                        run_id=resolved_run_id,
                    )
                ]
                comparison_summary = _summary_counts(comparison_all_rows)
                pending_comparison_rows = _group_comparison_review_rows(
                    get_comparison_group_suggestion_review_rows(
                        db_path,
                        subtopic_name=selected_subtopic,
                        run_id=resolved_run_id,
                        status="pending",
                        sample_limit=sample_limit,
                    )
                )
            raw_pending_subtopic_rows = _group_subtopic_review_rows(
                get_subtopic_suggestion_review_rows(
                    db_path,
                    topic_name=selected_topic,
                    run_id=resolved_run_id,
                    status="pending",
                    sample_limit=sample_limit,
                )
            )
            suppressed_low_support_subtopic_count = sum(
                1 for row in raw_pending_subtopic_rows if int(row.get("video_count") or 0) < MIN_NEW_SUBTOPIC_CLUSTER_SIZE
            )
            pending_subtopic_rows = [
                row for row in raw_pending_subtopic_rows if int(row.get("video_count") or 0) >= MIN_NEW_SUBTOPIC_CLUSTER_SIZE
            ]
            subtopic_summary["pending"] = len(pending_subtopic_rows)
            subtopic_summary["suppressed_low_support"] = suppressed_low_support_subtopic_count

    topic_lookup: dict[str, dict[str, Any]] = {}
    for topic in list_topics(db_path):
        name = str(topic["name"])
        topic_lookup[name] = {
            "name": name,
            "assignment_count": int(topic["assignment_count"] or 0),
            "primary_count": int(topic["primary_count"] or 0),
            "secondary_count": int(topic["secondary_count"] or 0),
            "subtopic_count": len(list_approved_subtopics_for_topic(db_path, topic_name=name)),
            "pending_count": 0,
            "apply_ready_count": 0,
            "applied_count": 0,
            "blocked_count": 0,
            "status": "empty",
            "selected": name == selected_topic,
        }
    for row in pending_topic_rows:
        name = str(row["name"])
        item = topic_lookup.setdefault(
            name,
            {
                "name": name,
                "assignment_count": 0,
                "primary_count": 0,
                "secondary_count": 0,
                "subtopic_count": 0,
                "pending_count": 0,
                "apply_ready_count": 0,
                "applied_count": 0,
                "blocked_count": 0,
                "status": "suggested",
                "selected": name == selected_topic,
            },
        )
        item["pending_count"] += int(row.get("video_count") or 0)
        item["status"] = "needs_review"
    for row in approved_topic_rows:
        name = str(row["name"])
        item = topic_lookup.setdefault(
            name,
            {
                "name": name,
                "assignment_count": 0,
                "primary_count": 0,
                "secondary_count": 0,
                "subtopic_count": 0,
                "pending_count": 0,
                "apply_ready_count": 0,
                "applied_count": 0,
                "blocked_count": 0,
                "status": "suggested",
                "selected": name == selected_topic,
            },
        )
        item["apply_ready_count"] += int(row.get("apply_ready_count") or 0)
        item["applied_count"] += int(row.get("applied_count") or 0)
        item["blocked_count"] += int(row.get("blocked_count") or 0)
        if item["apply_ready_count"]:
            item["status"] = "approved_not_applied"
        elif item["assignment_count"] or item["applied_count"]:
            item["status"] = "ready_to_explore"
    for item in topic_lookup.values():
        if item["status"] == "empty" and item["assignment_count"]:
            item["status"] = "ready_to_explore"
    topic_map = sorted(
        topic_lookup.values(),
        key=lambda item: (
            0 if item["status"] in {"needs_review", "approved_not_applied"} else 1,
            -int(item["assignment_count"] or 0),
            str(item["name"]).casefold(),
        ),
    )
    topic_inventory = _build_topic_inventory(db_path, topic_name=selected_topic)
    discovery_topic_map = _build_discovery_topic_map(db_path)
    latest_subtopic_run_id_by_topic = _latest_subtopic_run_ids_by_topic(db_path)
    if primary_channel is None:
        channel_overview = None
    else:
        channel_overview = _build_channel_overview(
            db_path,
            project_id=primary_channel.project_id,
            channel_id=primary_channel.channel_id,
        )

    return {
        "db_path": str(db_path),
        "dataset_name": db_path.name,
        "dataset_video_count": len(topic_generation_candidates),
        "channel_title": primary_channel.title if primary_channel is not None else None,
        "channel_id": primary_channel.youtube_channel_id if primary_channel is not None else None,
        "run_id": resolved_run_id,
        "latest_run_id": latest_run_id,
        "runs": runs,
        "current_run": current_run,
        "topic_map": topic_map,
        "topic_inventory": topic_inventory,
        "discovery_topic_map": discovery_topic_map,
        "channel_overview": channel_overview,
        "latest_subtopic_run_id_by_topic": latest_subtopic_run_id_by_topic,
        "topic_reviews": {
            "eligible_video_count": len(topic_generation_candidates),
            "summary": topic_summary,
            "pending": pending_topic_rows,
            "approved": approved_topic_rows,
        },
        "subtopic_reviews": {
            "available_topics": available_topics,
            "selected_topic": selected_topic,
            "eligible_video_count": eligible_subtopic_video_count,
            "summary": subtopic_summary,
            "suppressed_low_support_count": suppressed_low_support_subtopic_count,
            "pending": pending_subtopic_rows,
            "approved": subtopic_approved_rows,
        },
        "comparison_reviews": {
            "available_subtopics": available_subtopics,
            "selected_subtopic": selected_subtopic,
            "eligible_video_count": eligible_comparison_video_count,
            "summary": comparison_summary,
            "pending": pending_comparison_rows,
            "approved_groups": approved_comparison_groups,
        },
    }


class ReviewUIApp:
    def __init__(self, db_path: str | Path, *, sample_limit: int = 3) -> None:
        self.db_path = Path(db_path)
        self.sample_limit = sample_limit

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/":
                return self._html_response(start_response, self._render_html_page())
            if method == "GET" and path == "/api/state":
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=False)
                run_id_raw = _normalize_text((query.get("run_id") or [None])[0])
                topic_name = _normalize_text((query.get("topic") or [None])[0])
                subtopic_name = _normalize_text((query.get("subtopic") or [None])[0])
                run_id = int(run_id_raw) if run_id_raw is not None else None
                payload = build_state_payload(
                    self.db_path,
                    run_id=run_id,
                    topic_name=topic_name,
                    subtopic_name=subtopic_name,
                    sample_limit=self.sample_limit,
                )
                return self._json_response(start_response, payload)
            if method == "POST" and path.startswith("/api/"):
                body = self._read_json_body(environ)
                payload = self._handle_post(path, body)
                return self._json_response(start_response, payload)
            return self._json_response(start_response, {"error": f"Not found: {path}"}, status="404 Not Found")
        except (ReviewUIError, ValueError) as exc:
            return self._json_response(start_response, {"error": str(exc)}, status="400 Bad Request")
        except Exception as exc:  # pragma: no cover - defensive fallback for live server use
            return self._json_response(start_response, {"error": f"Internal server error: {exc}"}, status="500 Internal Server Error")

    def _handle_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        run_id_raw = body.get("run_id")
        run_id = int(run_id_raw) if run_id_raw not in (None, "") else None
        if path == "/api/generate/topics":
            return self._generate_topic_suggestions(body)
        if path == "/api/generate/subtopics":
            return self._generate_subtopic_suggestions(body)
        if path == "/api/generate/comparison-groups":
            return self._generate_comparison_group_suggestions(body)
        if path == "/api/topic/approve":
            label = self._require_text(body, "label")
            approved_name = _normalize_text(body.get("approved_name"))
            topic_id = approve_topic_suggestion_label(self.db_path, suggested_label=label, approved_name=approved_name, run_id=run_id)
            final_name = approved_name or label
            return {"ok": True, "message": f"Approved topic label '{label}' as '{final_name}' (row {topic_id}). Next step: apply the suggested videos to the topic."}
        if path == "/api/topic/approve-and-apply":
            label = self._require_text(body, "label")
            approved_name = _normalize_text(body.get("approved_name"))
            topic_id = approve_topic_suggestion_label(self.db_path, suggested_label=label, approved_name=approved_name, run_id=run_id)
            final_name = approved_name or label
            matched, applied, skipped = bulk_apply_topic_suggestion_label(self.db_path, suggested_label=final_name, run_id=run_id)
            return {
                "ok": True,
                "message": (
                    f"Approved topic label '{label}' as '{final_name}' (row {topic_id}) and applied ready video suggestions. "
                    f"Matched {matched}, applied {applied}, skipped {skipped}."
                ),
            }
        if path == "/api/topic/reject":
            label = self._require_text(body, "label")
            updated = reject_topic_suggestion_label(self.db_path, suggested_label=label, run_id=run_id)
            return {"ok": True, "message": f"Rejected topic label '{label}' across {updated} row(s)."}
        if path == "/api/topic/rename":
            current_name = self._require_text(body, "current_name")
            new_name = self._require_text(body, "new_name")
            label_id = rename_topic_suggestion_label(self.db_path, current_name=current_name, new_name=new_name, run_id=run_id)
            return {"ok": True, "message": f"Renamed topic label '{current_name}' to '{new_name}' (row {label_id})."}
        if path == "/api/topic/bulk-apply":
            label = self._require_text(body, "label")
            matched, applied, skipped = bulk_apply_topic_suggestion_label(self.db_path, suggested_label=label, run_id=run_id)
            return {"ok": True, "message": f"Applied approved topic label '{label}' to ready videos. Matched {matched}, applied {applied}, skipped {skipped}."}
        if path == "/api/topic/apply-video":
            label = self._require_text(body, "label")
            video_id = self._require_text(body, "video_id")
            apply_topic_suggestion_to_video(self.db_path, video_id=video_id, suggested_label=label, run_id=run_id)
            return {"ok": True, "message": f"Applied topic label '{label}' to video '{video_id}'."}
        if path == "/api/subtopic/approve":
            topic_name = self._require_text(body, "topic")
            label = self._require_text(body, "label")
            approved_name = _normalize_text(body.get("approved_name"))
            subtopic_id = approve_subtopic_suggestion_label(
                self.db_path,
                topic_name=topic_name,
                suggested_label=label,
                approved_name=approved_name,
                run_id=run_id,
            )
            final_name = approved_name or label
            return {"ok": True, "message": f"Approved subtopic label '{label}' as '{final_name}' under '{topic_name}' (row {subtopic_id}). Next step: apply the suggested videos to the subtopic."}
        if path == "/api/subtopic/approve-and-apply":
            topic_name = self._require_text(body, "topic")
            label = self._require_text(body, "label")
            approved_name = _normalize_text(body.get("approved_name"))
            subtopic_id = approve_subtopic_suggestion_label(
                self.db_path,
                topic_name=topic_name,
                suggested_label=label,
                approved_name=approved_name,
                run_id=run_id,
            )
            final_name = approved_name or label
            matched = applied = skipped = 0
            applications = _group_subtopic_application_rows(
                list_subtopic_suggestion_application_rows(self.db_path, topic_name=topic_name, run_id=run_id)
            ).get(final_name, [])
            for application in applications:
                matched += 1
                if application["can_apply"]:
                    apply_subtopic_suggestion_to_video(
                        self.db_path,
                        video_id=application["youtube_video_id"],
                        topic_name=topic_name,
                        suggested_label=final_name,
                        run_id=run_id,
                    )
                    applied += 1
                else:
                    skipped += 1
            return {
                "ok": True,
                "message": (
                    f"Approved subtopic label '{label}' as '{final_name}' under '{topic_name}' (row {subtopic_id}) and applied ready video suggestions. "
                    f"Matched {matched}, applied {applied}, skipped {skipped}."
                ),
            }
        if path == "/api/subtopic/bulk-apply":
            topic_name = self._require_text(body, "topic")
            label = self._require_text(body, "label")
            matched = applied = skipped = 0
            applications = _group_subtopic_application_rows(
                list_subtopic_suggestion_application_rows(self.db_path, topic_name=topic_name, run_id=run_id)
            ).get(label, [])
            for application in applications:
                matched += 1
                if application["can_apply"]:
                    apply_subtopic_suggestion_to_video(
                        self.db_path,
                        video_id=application["youtube_video_id"],
                        topic_name=topic_name,
                        suggested_label=label,
                        run_id=run_id,
                    )
                    applied += 1
                else:
                    skipped += 1
            return {"ok": True, "message": f"Applied approved subtopic label '{label}' under '{topic_name}' to ready videos. Matched {matched}, applied {applied}, skipped {skipped}."}
        if path == "/api/subtopic/reject":
            topic_name = self._require_text(body, "topic")
            label = self._require_text(body, "label")
            updated = reject_subtopic_suggestion_label(self.db_path, topic_name=topic_name, suggested_label=label, run_id=run_id)
            return {"ok": True, "message": f"Rejected subtopic label '{label}' across {updated} row(s)."}
        if path == "/api/subtopic/rename":
            topic_name = self._require_text(body, "topic")
            current_name = self._require_text(body, "current_name")
            new_name = self._require_text(body, "new_name")
            label_id = rename_subtopic_suggestion_label(
                self.db_path,
                topic_name=topic_name,
                current_name=current_name,
                new_name=new_name,
                run_id=run_id,
            )
            return {"ok": True, "message": f"Renamed subtopic label '{current_name}' to '{new_name}' under '{topic_name}' (row {label_id})."}
        if path == "/api/subtopic/apply-video":
            topic_name = self._require_text(body, "topic")
            label = self._require_text(body, "label")
            video_id = self._require_text(body, "video_id")
            apply_subtopic_suggestion_to_video(
                self.db_path,
                video_id=video_id,
                topic_name=topic_name,
                suggested_label=label,
                run_id=run_id,
            )
            return {"ok": True, "message": f"Applied subtopic label '{label}' to video '{video_id}' under '{topic_name}'."}
        if path == "/api/comparison-group/approve":
            subtopic_name = self._require_text(body, "subtopic")
            label = self._require_text(body, "label")
            approved_name = _normalize_text(body.get("approved_name"))
            group_id = approve_comparison_group_suggestion_label(
                self.db_path,
                subtopic_name=subtopic_name,
                suggested_label=label,
                approved_name=approved_name,
                run_id=run_id,
            )
            final_name = approved_name or label
            return {"ok": True, "message": f"Approved comparison-group label '{label}' as '{final_name}' under '{subtopic_name}' (row {group_id})."}
        if path == "/api/comparison-group/reject":
            subtopic_name = self._require_text(body, "subtopic")
            label = self._require_text(body, "label")
            updated = reject_comparison_group_suggestion_label(
                self.db_path,
                subtopic_name=subtopic_name,
                suggested_label=label,
                run_id=run_id,
            )
            return {"ok": True, "message": f"Rejected comparison-group label '{label}' across {updated} row(s)."}
        if path == "/api/comparison-group/rename":
            subtopic_name = self._require_text(body, "subtopic")
            current_name = self._require_text(body, "current_name")
            new_name = self._require_text(body, "new_name")
            label_id = rename_comparison_group_suggestion_label(
                self.db_path,
                subtopic_name=subtopic_name,
                current_name=current_name,
                new_name=new_name,
                run_id=run_id,
            )
            return {
                "ok": True,
                "message": f"Renamed comparison-group label '{current_name}' to '{new_name}' under '{subtopic_name}' (row {label_id}).",
            }
        if path == "/api/discovery/topic/rename":
            current_name = self._require_text(body, "current_name")
            new_name = self._require_text(body, "new_name")
            project_name = _resolve_primary_project_name(self.db_path)
            topic_id = rename_topic(
                self.db_path,
                project_name=project_name,
                current_name=current_name,
                new_name=new_name,
            )
            return {
                "ok": True,
                "message": f"Renamed discovery topic '{current_name}' to '{new_name}' (row {topic_id}).",
            }
        if path == "/api/discovery/topic/merge":
            source_name = self._require_text(body, "source_name")
            target_name = self._require_text(body, "target_name")
            project_name = _resolve_primary_project_name(self.db_path)
            stats = merge_topics(
                self.db_path,
                project_name=project_name,
                source_name=source_name,
                target_name=target_name,
            )
            return {
                "ok": True,
                "message": (
                    f"Merged discovery topic '{source_name}' into '{target_name}'. "
                    f"Moved {stats['moved_episode_assignments']} episode assignment(s); "
                    f"dropped {stats['dropped_episode_collisions']} duplicate(s); "
                    f"moved {stats['moved_subtopics']} subtopic(s); "
                    f"merged {stats['merged_subtopic_collisions']} colliding subtopic(s)."
                ),
                "stats": stats,
            }
        if path == "/api/discovery/topic/split":
            source_name = self._require_text(body, "source_name")
            new_name = self._require_text(body, "new_name")
            youtube_video_ids = body.get("youtube_video_ids")
            if not isinstance(youtube_video_ids, list) or not all(
                isinstance(v, str) and v.strip() for v in youtube_video_ids
            ):
                raise ReviewUIError(
                    "youtube_video_ids must be a non-empty list of strings."
                )
            project_name = _resolve_primary_project_name(self.db_path)
            stats = split_topic(
                self.db_path,
                project_name=project_name,
                source_name=source_name,
                new_name=new_name,
                youtube_video_ids=[v.strip() for v in youtube_video_ids],
            )
            skipped = stats.get("skipped_video_ids") or []
            skipped_note = (
                f" Skipped {len(skipped)} video id(s) not on '{source_name}'."
                if skipped
                else ""
            )
            return {
                "ok": True,
                "message": (
                    f"Split discovery topic '{source_name}' into '{new_name}'. "
                    f"Moved {stats['moved_episode_assignments']} episode assignment(s); "
                    f"dropped {stats['dropped_subtopic_assignments']} subtopic assignment(s)."
                    f"{skipped_note}"
                ),
                "stats": stats,
            }
        if path == "/api/discovery/episode/move-subtopic":
            topic_name = self._require_text(body, "topic_name")
            youtube_video_id = self._require_text(body, "youtube_video_id")
            target_subtopic_name = self._require_text(body, "target_subtopic_name")
            project_name = _resolve_primary_project_name(self.db_path)
            stats = move_episode_subtopic(
                self.db_path,
                project_name=project_name,
                topic_name=topic_name,
                youtube_video_id=youtube_video_id,
                target_subtopic_name=target_subtopic_name,
            )
            if stats["moved"]:
                msg = (
                    f"Moved '{youtube_video_id}' from '{stats['previous_subtopic_name']}' "
                    f"to '{target_subtopic_name}' under '{topic_name}'."
                )
            elif stats["inserted"]:
                msg = (
                    f"Attached '{youtube_video_id}' to subtopic '{target_subtopic_name}' "
                    f"under '{topic_name}'."
                )
            else:
                msg = (
                    f"'{youtube_video_id}' is already on subtopic "
                    f"'{target_subtopic_name}' under '{topic_name}'."
                )
            return {"ok": True, "message": msg, "stats": stats}
        if path == "/api/discovery/episode/mark-wrong":
            topic_name = self._require_text(body, "topic_name")
            youtube_video_id = self._require_text(body, "youtube_video_id")
            raw_subtopic = body.get("subtopic_name")
            subtopic_name = (
                raw_subtopic.strip()
                if isinstance(raw_subtopic, str) and raw_subtopic.strip()
                else None
            )
            raw_reason = body.get("reason")
            reason = (
                raw_reason.strip()
                if isinstance(raw_reason, str) and raw_reason.strip()
                else None
            )
            project_name = _resolve_primary_project_name(self.db_path)
            stats = mark_assignment_wrong(
                self.db_path,
                project_name=project_name,
                topic_name=topic_name,
                youtube_video_id=youtube_video_id,
                subtopic_name=subtopic_name,
                reason=reason,
            )
            if subtopic_name is None:
                msg = (
                    f"Marked '{youtube_video_id}' as wrong on topic "
                    f"'{topic_name}'. Removed from this topic."
                )
            else:
                msg = (
                    f"Marked '{youtube_video_id}' as wrong on subtopic "
                    f"'{subtopic_name}' under '{topic_name}'. Removed from "
                    f"this subtopic."
                )
            return {"ok": True, "message": msg, "stats": stats}
        raise ReviewUIError(f"Unsupported API route: {path}")

    def _generate_topic_suggestions(self, body: dict[str, Any]) -> dict[str, Any]:
        model_name = _normalize_text(body.get("model")) or DEFAULT_SUGGESTION_MODEL
        limit = _parse_limit(body.get("limit"))
        primary_channel = get_primary_channel(self.db_path)
        approved_topic_names = list_approved_topic_names(self.db_path)
        rows = list_videos_for_topic_suggestions(self.db_path, limit=limit)
        if not rows:
            raise ReviewUIError("No stored videos are available for topic suggestion generation.")
        run_id = create_topic_suggestion_run(self.db_path, model_name=model_name, status="success")
        stored_count = 0
        for row in rows:
            suggestion = suggest_topics_for_video(
                project_name=primary_channel.title,
                approved_topic_names=approved_topic_names,
                youtube_video_id=row["youtube_video_id"],
                video_title=row["title"],
                video_description=row["description"],
                model=model_name,
            )
            stored_count += store_video_topic_suggestion(self.db_path, run_id=run_id, suggestion=suggestion)
        return {
            "ok": True,
            "run_id": run_id,
            "message": f"Generated topic suggestions for {len(rows)} video(s) in run {run_id} using {model_name}. Stored {stored_count} suggestion row(s).",
        }

    def _generate_subtopic_suggestions(self, body: dict[str, Any]) -> dict[str, Any]:
        topic_name = self._require_text(body, "topic")
        model_name = _normalize_text(body.get("model")) or DEFAULT_SUGGESTION_MODEL
        limit = _parse_limit(body.get("limit"))
        primary_channel = get_primary_channel(self.db_path)
        approved_subtopics = [
            {"name": row["name"], "description": row["description"]}
            for row in list_approved_subtopics_for_topic(self.db_path, topic_name=topic_name)
        ]
        rows = list_videos_for_subtopic_suggestions(self.db_path, topic_name=topic_name, limit=limit)
        if not rows:
            raise ReviewUIError(f"No stored videos are available for subtopic suggestion generation under '{topic_name}'.")
        run_id = create_subtopic_suggestion_run(self.db_path, topic_name=topic_name, model_name=model_name, status="success")
        stored_count = 0
        for row in rows:
            suggestion = suggest_subtopics_for_video(
                project_name=primary_channel.title,
                broad_topic_name=topic_name,
                approved_subtopics=approved_subtopics,
                youtube_video_id=row["youtube_video_id"],
                video_title=row["title"],
                video_description=row["description"],
                model=model_name,
            )
            stored_count += store_video_subtopic_suggestion(
                self.db_path,
                run_id=run_id,
                topic_name=topic_name,
                suggestion=suggestion,
            )

        suppressed_labels: list[str] = []
        pending_labels = get_subtopic_suggestion_review_rows(
            self.db_path,
            topic_name=topic_name,
            run_id=run_id,
            status="pending",
            sample_limit=1,
        )
        for label_row in pending_labels:
            video_count = int(label_row["video_count"] or 0)
            if video_count < MIN_NEW_SUBTOPIC_CLUSTER_SIZE:
                label_name = str(label_row["name"])
                reject_subtopic_suggestion_label(
                    self.db_path,
                    topic_name=topic_name,
                    suggested_label=label_name,
                    run_id=run_id,
                )
                suppressed_labels.append(label_name)

        suppressed_count = len(suppressed_labels)
        return {
            "ok": True,
            "run_id": run_id,
            "topic": topic_name,
            "message": (
                f"Generated subtopic suggestions for {len(rows)} video(s) under '{topic_name}' in run {run_id} using {model_name}. "
                f"Stored {stored_count} suggestion row(s). Suppressed {suppressed_count} low-support subtopic label(s) below the {MIN_NEW_SUBTOPIC_CLUSTER_SIZE}-video threshold."
            ),
        }

    def _generate_comparison_group_suggestions(self, body: dict[str, Any]) -> dict[str, Any]:
        topic_name = self._require_text(body, "topic")
        subtopic_name = self._require_text(body, "subtopic")
        model_name = _normalize_text(body.get("model")) or DEFAULT_SUGGESTION_MODEL
        limit = _parse_limit(body.get("limit"))
        primary_channel = get_primary_channel(self.db_path)
        approved_groups = [
            {"name": row["name"], "description": row["description"], "member_count": row["member_count"]}
            for row in list_approved_comparison_groups_for_subtopic(self.db_path, subtopic_name=subtopic_name)
        ]
        rows = list_videos_for_comparison_group_suggestions(self.db_path, subtopic_name=subtopic_name, limit=limit)
        if not rows:
            raise ReviewUIError(
                f"No stored videos are available for comparison-group suggestion generation under '{subtopic_name}'."
            )
        run_id = create_comparison_group_suggestion_run(
            self.db_path,
            subtopic_name=subtopic_name,
            model_name=model_name,
            status="success",
        )
        stored_count = 0
        for row in rows:
            suggestion = suggest_comparison_groups_for_video(
                project_name=primary_channel.title,
                broad_topic_name=topic_name,
                subtopic_name=subtopic_name,
                approved_comparison_groups=approved_groups,
                youtube_video_id=row["youtube_video_id"],
                video_title=row["title"],
                video_description=row["description"],
                model=model_name,
            )
            stored_count += store_video_comparison_group_suggestion(
                self.db_path,
                run_id=run_id,
                subtopic_name=subtopic_name,
                suggestion=suggestion,
            )
        return {
            "ok": True,
            "run_id": run_id,
            "topic": topic_name,
            "subtopic": subtopic_name,
            "message": f"Generated comparison-group suggestions for {len(rows)} video(s) under '{topic_name} / {subtopic_name}' in run {run_id} using {model_name}. Stored {stored_count} suggestion row(s).",
        }

    def _read_json_body(self, environ: dict[str, Any]) -> dict[str, Any]:
        length_raw = environ.get("CONTENT_LENGTH") or "0"
        try:
            length = int(length_raw)
        except ValueError as exc:
            raise ReviewUIError(f"invalid Content-Length: {length_raw}") from exc
        raw = environ["wsgi.input"].read(length) if length > 0 else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            raise ReviewUIError("request body must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ReviewUIError("request body must be a JSON object")
        return parsed

    @staticmethod
    def _require_text(body: dict[str, Any], key: str) -> str:
        value = _normalize_text(body.get(key))
        if value is None:
            raise ReviewUIError(f"missing required field: {key}")
        return value

    @staticmethod
    def _render_html_page() -> str:
        return HTML_PAGE.replace("{{UI_REVISION}}", UI_REVISION)

    @staticmethod
    def _json_response(start_response: Any, payload: dict[str, Any], *, status: str = "200 OK") -> list[bytes]:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        headers = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body)))]
        start_response(status, headers)
        return [body]

    @staticmethod
    def _html_response(start_response: Any, html: str, *, status: str = "200 OK") -> list[bytes]:
        body = html.encode("utf-8")
        headers = [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))]
        start_response(status, headers)
        return [body]


def build_review_app(db_path: str | Path, *, sample_limit: int = 3) -> ReviewUIApp:
    return ReviewUIApp(db_path, sample_limit=sample_limit)


def serve_review_ui(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 8765, sample_limit: int = 3) -> None:
    app = build_review_app(db_path, sample_limit=sample_limit)
    print(f"Serving review UI for {Path(db_path)}")
    print(f"Open http://{host}:{port}")
    with make_server(host, port, app) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nReview UI stopped.")
