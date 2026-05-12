from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

from yt_channel_analyzer.db import (
    accept_taxonomy_proposal,
    apply_subtopic_suggestion_to_video,
    apply_topic_suggestion_to_video,
    approve_comparison_group_suggestion_label,
    approve_subtopic_suggestion_label,
    approve_topic_suggestion_label,
    bulk_apply_topic_suggestion_label,
    connect,
    create_comparison_group_suggestion_run,
    create_subtopic_suggestion_run,
    create_topic_suggestion_run,
    get_latest_topic_suggestion_run_id,
    get_comparison_group_suggestion_review_rows,
    get_primary_channel,
    get_subtopic_suggestion_review_rows,
    get_topic_suggestion_review_rows,
    list_pending_taxonomy_proposals,
    list_refinement_episode_changes,
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
    reject_taxonomy_proposal,
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
    update_channel_fields,
    upsert_channel_metadata,
    upsert_videos_for_primary_channel,
)
from yt_channel_analyzer.legacy.comparison_group_suggestions import suggest_comparison_groups_for_video
from yt_channel_analyzer.subtopic_suggestions import suggest_subtopics_for_video
from yt_channel_analyzer.topic_suggestions import suggest_topics_for_video
from yt_channel_analyzer.youtube import (
    ChannelMetadata,
    VideoMetadata,
    YouTubeAPIError,
    fetch_channel_metadata as _real_fetch_channel_metadata,
    fetch_channel_videos as _real_fetch_channel_videos,
)


DEFAULT_SUGGESTION_MODEL = "gpt-4.1-mini"
UI_REVISION = "2026-05-10.12-discover-streaming-poll-supply-pagination-edit-channel-form-run-discovery-button-wired-reingest-button-wired-discover-row-selects-run-discover-cost-comparison-readiness-run-history-advanced-channel-overview-discovery-panel-shorts-filter-badge-episode-duration-refine-stage-sample-setup-proposal-review-before-after"
MIN_NEW_SUBTOPIC_CLUSTER_SIZE = 5
REINGEST_DEFAULT_LIMIT = 50
SUPPLY_DEFAULT_LIMIT = 50
SUPPLY_MAX_LIMIT = 500
DISCOVER_MODES = ("stub", "real")
REFINE_MODES = ("stub", "real")

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
  <title>YouTube Analyser</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Source+Serif+4:ital,opsz,wght@0,8..60,400..600;1,8..60,400..600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      color-scheme: light;
      /* Design tokens — YT_Analyzer_Design_Spec */
      --paper: #FAF8F2;
      --surface: #ffffff;
      --ink: #2C2C2A;
      --ink-soft: #888780;
      --ink-mute: #B5B2A6;
      --rule: #D3D1C7;
      --rule-soft: #E5E2D8;
      --tag-bg: #F1EFE8;
      --teal: #0F6E56;
      --teal-tint: #E1F5EE;
      --blue: #185FA5;
      --blue-tint: #E5EEF7;
      --coral: #D85A30;
      --coral-tint: #FAECE7;

      --display: "Source Serif 4", "Source Serif Pro", Georgia, serif;
      --body: "Poppins", system-ui, -apple-system, sans-serif;
      --mono: "JetBrains Mono", ui-monospace, "SF Mono", monospace;
      --hairline: 0.5px solid var(--rule);

      /* Compatibility aliases for legacy class definitions */
      --bg: var(--paper);
      --panel: var(--surface);
      --panel-2: var(--tag-bg);
      --text: var(--ink);
      --muted: var(--ink-soft);
      --accent: var(--blue);     /* primary action / links */
      --good: var(--teal);
      --warn: var(--coral);      /* active emphasis */
      --bad: #991b1b;
      --border: var(--rule);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--body);
      font-weight: 400;
      font-size: 16px;
      line-height: 1.5;
      background: var(--paper);
      color: var(--ink);
      -webkit-font-smoothing: antialiased;
    }
    h1, h2, h3, h4 {
      margin: 0;
      font-family: var(--display);
      font-weight: 500;
      letter-spacing: -0.005em;
      line-height: 1.2;
    }
    h1 { font-size: 28px; }
    h2 { font-size: 22px; }
    h3 { font-size: 18px; }
    em, i { font-style: italic; }
    code, .mono {
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.02em;
    }
    .wrap {
      max-width: 1400px;
      margin: 0 auto;
      padding: 0;
    }

    /* Top bar — wordmark + channel pill */
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 48px;
      border-bottom: var(--hairline);
      background: var(--paper);
    }
    .wordmark {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-family: var(--body);
      font-size: 16px;
      font-weight: 500;
      letter-spacing: -0.005em;
    }
    .wordmark-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      background: var(--teal);
      border-radius: 999px;
    }
    .topbar-left { display: flex; align-items: center; gap: 24px; }
    .topbar-right { display: flex; align-items: center; gap: 14px; }
    .topbar .version {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-mute);
      letter-spacing: 0.06em;
    }
    .channel-pill {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 6px 14px 6px 6px;
      border: 0.5px solid var(--rule);
      border-radius: 999px;
      font-size: 13px;
      background: var(--surface);
    }
    .channel-pill .av {
      width: 24px;
      height: 24px;
      border-radius: 999px;
      background: var(--teal);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      font-family: var(--display);
      font-size: 12px;
      font-weight: 500;
    }
    .channel-pill .div {
      width: 1px;
      height: 12px;
      background: var(--rule);
    }
    .channel-pill .yt-id {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-mute);
    }

    /* Stepper — 4 funnel stages */
    .stepper {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0;
      padding: 24px 48px 28px;
      border-bottom: var(--hairline);
      background: var(--paper);
      position: relative;
    }
    .step {
      position: relative;
      padding-right: 32px;
      text-align: left;
      cursor: default;
      display: flex;
      align-items: center;
      background: none;
      border: 0;
    }
    .step:not(:last-child)::after {
      content: "";
      position: absolute;
      top: 22px;
      left: 22px;
      right: -22px;
      height: 0.5px;
      background: var(--rule);
      z-index: 0;
    }
    .step .marker {
      position: relative;
      width: 44px;
      height: 44px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      z-index: 1;
      background: var(--tag-bg);
      color: var(--ink-soft);
      font-family: var(--body);
      font-size: 18px;
      font-weight: 500;
      flex-shrink: 0;
    }
    .step.done .marker { background: var(--teal-tint); color: var(--teal); }
    .step.done .marker::after {
      content: "";
      width: 12px; height: 6px;
      border-left: 2px solid var(--teal);
      border-bottom: 2px solid var(--teal);
      transform: rotate(-45deg) translate(0, -2px);
    }
    .step.act .marker { background: var(--coral-tint); color: var(--coral); }
    .step.act .label, .step.act .sub { color: var(--coral); }
    .step.act .marker::after {
      content: "";
      width: 10px; height: 10px;
      background: var(--coral);
      border-radius: 999px;
    }
    .step.idle .marker::after {
      content: "";
      width: 12px; height: 12px;
      border: 2px solid var(--ink-soft);
      border-radius: 999px;
    }
    .step.idle .label, .step.idle .sub { color: var(--ink-soft); }
    .step .step-text {
      display: inline-flex;
      flex-direction: column;
      margin-left: 12px;
      position: relative;
      z-index: 1;
      background: var(--paper);
      padding: 0 8px 0 4px;
    }
    .step .label {
      font-family: var(--body);
      font-size: 15px;
      font-weight: 500;
      letter-spacing: -0.005em;
      color: var(--ink);
    }
    .step .sub {
      margin-top: 2px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.02em;
      color: var(--ink-soft);
    }

    /* Stage frame */
    .stage-inner {
      max-width: 1400px;
      margin: 0 auto;
      padding: 40px 48px 96px;
    }

    /* Eyebrow / micro */
    .eyebrow {
      font-family: var(--body);
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--ink-soft);
      margin-bottom: 8px;
    }
    .small { font-size: 13px; }
    .soft { color: var(--ink-soft); }
    .mute { color: var(--ink-mute); }
    .accent { color: var(--coral); }
    .good { color: var(--teal); }
    .bad { color: var(--bad); }

    .title {
      margin: 0 0 8px;
      font-size: 28px;
    }
    .revision-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin-left: 10px;
      padding: 3px 9px;
      border-radius: 8px;
      background: var(--tag-bg);
      color: var(--ink-soft);
      font-family: var(--mono);
      font-size: 11px;
      vertical-align: middle;
      letter-spacing: 0.04em;
    }
    .muted { color: var(--ink-soft); }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }
    .row.stretch { align-items: stretch; }
    .controls { margin-top: 16px; }
    .context-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }
    .context-card {
      padding: 14px 16px;
      border-radius: 12px;
      border: 0.5px solid var(--rule);
      background: var(--surface);
    }
    .context-card .k {
      display: block;
      font-family: var(--body);
      font-size: 11px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--ink-soft);
      margin-bottom: 6px;
    }
    .context-card strong {
      display: block;
      margin-bottom: 4px;
      font-family: var(--display);
      font-weight: 500;
      font-size: 18px;
      color: var(--ink);
    }
    .generator {
      margin-top: 20px;
      padding-top: 20px;
      border-top: var(--hairline);
    }
    .run-history-advanced {
      margin-top: 16px;
      padding-top: 12px;
      border-top: var(--hairline);
    }
    .run-history-advanced > summary {
      cursor: pointer;
      color: var(--ink-soft);
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.04em;
    }
    .run-history-advanced .run-history-hint { margin-top: 8px; color: var(--ink-soft); }
    .run-history-advanced > label { margin-top: 8px; max-width: 320px; }
    label {
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    select, input {
      font-family: var(--body);
      font-size: 14px;
      font-weight: 400;
      border-radius: 8px;
      border: 0.5px solid var(--rule);
      background: var(--surface);
      color: var(--ink);
      padding: 9px 12px;
      text-transform: none;
      letter-spacing: 0;
    }
    select:focus, input:focus {
      outline: none;
      border-color: var(--blue);
      box-shadow: 0 0 0 3px var(--blue-tint);
    }
    button {
      font-family: var(--body);
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      border: 0.5px solid var(--rule);
      background: transparent;
      color: var(--ink);
      border-radius: 8px;
      white-space: nowrap;
      transition: background 0.15s, border-color 0.15s;
    }
    button:hover { background: var(--tag-bg); border-color: var(--ink-soft); }
    button:active { transform: translateY(1px); }
    button.primary-action,
    button.btn-primary {
      background: var(--blue);
      color: #fff;
      border-color: var(--blue);
      font-weight: 500;
    }
    button.primary-action:hover,
    button.btn-primary:hover { background: #144D87; border-color: #144D87; }
    button.good { color: var(--teal); border-color: var(--teal-tint); background: var(--teal-tint); }
    button.bad { color: var(--bad); border-color: rgba(153,27,27,0.20); background: rgba(153,27,27,0.06); }
    button.warn { color: var(--coral); border-color: var(--coral-tint); background: var(--coral-tint); }
    button.secondary {
      background: transparent;
      color: var(--ink-soft);
      border-color: transparent;
    }
    button.secondary:hover { color: var(--ink); background: var(--tag-bg); border-color: transparent; }
    /* Section panels — flat surfaces with hairlines */
    .panel,
    .topic-map,
    .channel-overview {
      background: var(--surface);
      border: 0.5px solid var(--rule);
      border-radius: 12px;
      box-shadow: none;
      padding: 24px;
      margin: 0 0 20px;
    }
    .panel h2, .panel h3 { margin-top: 0; }

    .topic-map.discovery-topic-map { border-color: var(--rule); }

    .channel-overview { margin-bottom: 24px; }
    .channel-overview-stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin: 16px 0 0;
    }
    .channel-overview-latest { margin-top: 12px; color: var(--ink-soft); }

    /* Discovery topic map — pillar-style cards */
    .discovery-topic-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
    }
    .discovery-topic-header h3 {
      margin: 0;
      font-family: var(--display);
      font-size: 22px;
      font-weight: 500;
      letter-spacing: -0.01em;
    }
    .discovery-topic-actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .discovery-topic-rename,
    .discovery-topic-merge,
    .discovery-topic-split {
      font-family: var(--body);
      font-size: 12px;
      font-weight: 500;
      padding: 5px 10px;
      border-radius: 8px;
      border: 0.5px solid var(--rule);
      background: var(--surface);
      color: var(--ink-soft);
      cursor: pointer;
    }
    .discovery-topic-rename:hover,
    .discovery-topic-merge:hover,
    .discovery-topic-split:hover {
      background: var(--tag-bg);
      color: var(--ink);
      border-color: var(--ink-soft);
    }
    .subtopic-video-move {
      margin-left: 6px;
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 8px;
      border: 0.5px solid var(--rule);
      background: var(--tag-bg);
      color: var(--ink-soft);
      cursor: pointer;
    }
    .subtopic-video-move:hover { background: var(--blue-tint); color: var(--blue); border-color: var(--blue); }

    /* Confidence — soft hairline track w/ teal fill, coral when low */
    .confidence-bar {
      position: relative;
      height: 4px;
      border-radius: 999px;
      background: var(--rule-soft);
      margin-top: 6px;
      overflow: hidden;
      max-width: 180px;
    }
    .confidence-bar > span {
      position: absolute;
      top: 0; left: 0; bottom: 0;
      background: var(--teal);
      border-radius: 999px;
    }
    .confidence-bar.low > span { background: var(--coral); }

    /* Episode rows — design's pillar/episode pattern, never truncated */
    .discovery-episode-list {
      list-style: none;
      padding: 0;
      margin: 16px 0 0;
      display: grid;
      gap: 0;
    }
    .discovery-episode {
      display: grid;
      grid-template-columns: 152px minmax(0, 1fr);
      gap: 24px;
      padding: 20px 0;
      border-top: var(--hairline);
      background: transparent;
      border-radius: 0;
      border-bottom: 0;
      align-items: start;
    }
    .discovery-episode:first-child { border-top: 0; }
    .discovery-episode.low { opacity: 1; }
    .discovery-episode.low .discovery-episode-title { color: var(--ink-soft); }
    .discovery-episode-thumb {
      width: 152px;
      height: 86px;
      object-fit: cover;
      border-radius: 8px;
      background:
        repeating-linear-gradient(135deg, #ECE8DE 0 8px, #F3EFE6 8px 16px);
      border: 0.5px solid var(--rule);
    }
    .discovery-episode-thumb.placeholder {
      background:
        repeating-linear-gradient(135deg, #ECE8DE 0 8px, #F3EFE6 8px 16px);
    }
    .discovery-episode-body { min-width: 0; }
    .discovery-episode-title {
      font-family: var(--display);
      font-weight: 500;
      font-size: 18px;
      line-height: 1.35;
      letter-spacing: -0.005em;
      color: var(--ink);
      text-wrap: pretty;
      word-break: break-word;
      /* Design rule: never truncate video titles */
      overflow: visible;
      display: block;
      -webkit-line-clamp: unset;
    }
    .discovery-episode-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      align-items: center;
      margin-top: 8px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.02em;
      color: var(--ink-soft);
    }
    .discovery-episode-confidence {
      font-family: var(--mono);
      font-weight: 500;
      color: var(--teal);
    }
    .discovery-episode.low .discovery-episode-confidence { color: var(--coral); }
    .discovery-episode-also-in {
      font-family: var(--body);
      font-size: 11px;
      color: var(--ink-soft);
      background: var(--tag-bg);
      border-radius: 8px;
      padding: 3px 10px;
      margin-left: 4px;
    }
    .discovery-topic-new-badge {
      display: inline-block;
      margin-left: 8px;
      font-family: var(--body);
      font-size: 11px;
      font-weight: 500;
      color: var(--teal);
      background: var(--teal-tint);
      border-radius: 8px;
      padding: 2px 9px;
      vertical-align: middle;
    }
    .discovery-episode-reason {
      margin-top: 12px;
      font-family: var(--display);
      font-style: italic;
      font-size: 15px;
      font-weight: 400;
      line-height: 1.55;
      color: var(--ink-soft);
      border-left: 1px solid var(--coral);
      padding-left: 14px;
      max-width: 64ch;
    }
    .discovery-episode-empty { margin-top: 10px; font-size: 13px; color: var(--ink-soft); }
    .discovery-episode-wrong,
    .subtopic-video-wrong {
      margin-top: 8px;
      background: transparent;
      color: var(--bad);
      border: 0.5px solid rgba(153,27,27,0.30);
      border-radius: 8px;
      padding: 4px 10px;
      font-size: 11px;
      font-family: var(--body);
      font-weight: 500;
      cursor: pointer;
    }
    .subtopic-video-wrong { margin-left: 6px; margin-top: 0; }
    .discovery-episode-wrong:hover,
    .subtopic-video-wrong:hover {
      background: rgba(153,27,27,0.06);
    }

    /* Subtopic accordion — design's caret + serif heading */
    .discovery-subtopic-list {
      display: grid;
      gap: 4px;
      margin-top: 16px;
    }
    .discovery-subtopic-bucket {
      border: 0;
      border-top: var(--hairline);
      border-radius: 0;
      background: transparent;
      padding: 14px 0 0;
    }
    .discovery-subtopic-bucket:first-of-type { border-top: 0; padding-top: 8px; }
    .discovery-subtopic-bucket > summary {
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      padding: 6px 0 12px;
      font-family: var(--display);
      font-weight: 500;
      font-size: 18px;
      letter-spacing: -0.005em;
      color: var(--ink);
    }
    .discovery-subtopic-bucket > summary::-webkit-details-marker { display: none; }
    .discovery-subtopic-bucket > summary::before {
      content: "";
      width: 7px; height: 7px;
      margin-right: 10px;
      border-right: 1.5px solid var(--ink);
      border-bottom: 1.5px solid var(--ink);
      transform: rotate(-45deg);
      display: inline-block;
      flex-shrink: 0;
    }
    .discovery-subtopic-bucket[open] > summary::before {
      transform: rotate(45deg);
      margin-bottom: 2px;
    }
    .discovery-subtopic-bucket .discovery-episode-list { margin-top: 4px; }
    .discovery-subtopic-unassigned > summary {
      color: var(--ink-soft);
      font-style: italic;
    }
    .discovery-episode-sort-row {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 16px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.04em;
      color: var(--ink-soft);
      text-transform: uppercase;
    }
    .discovery-episode-sort {
      background: var(--surface);
      color: var(--ink);
      border: 0.5px solid var(--rule);
      border-radius: 8px;
      padding: 6px 10px;
      font-family: var(--body);
      font-size: 12px;
      letter-spacing: 0;
      text-transform: none;
    }

    /* Topic map (pillars) */
    .topic-map-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: end;
      margin-bottom: 18px;
      padding-bottom: 14px;
      border-bottom: var(--hairline);
    }
    .topic-map-head h2 { margin: 0 0 4px; font-size: 22px; }
    .topic-map-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 20px;
    }
    .topic-card {
      border: 0.5px solid var(--rule);
      border-radius: 12px;
      padding: 18px;
      background: var(--surface);
      position: relative;
      transition: border-color 0.15s, transform 0.15s;
    }
    .topic-card:hover {
      border-color: var(--ink-soft);
    }
    .topic-card.selected {
      border-color: var(--coral);
    }
    .topic-card.selected::before {
      content: "";
      position: absolute;
      left: -1px; top: -1px; bottom: -1px;
      width: 3px;
      background: var(--coral);
      border-radius: 12px 0 0 12px;
    }
    .topic-card h3 {
      margin: 0 0 10px;
      font-family: var(--display);
      font-weight: 500;
      font-size: 22px;
      letter-spacing: -0.01em;
    }
    .topic-card .topic-stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 10px 0;
    }
    .topic-stat {
      padding: 0;
      border-radius: 0;
      background: transparent;
      border: 0;
    }
    .topic-stat .k {
      display: block;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.04em;
      color: var(--ink-mute);
      text-transform: uppercase;
    }
    .topic-stat strong {
      display: block;
      font-family: var(--display);
      font-weight: 500;
      font-size: 22px;
      color: var(--ink);
      margin-top: 2px;
    }
    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 8px;
      padding: 4px 10px;
      font-family: var(--body);
      font-size: 11px;
      font-weight: 400;
      border: 0;
      color: var(--ink-soft);
      background: var(--tag-bg);
    }
    .status-chip.warn { color: var(--coral); background: var(--coral-tint); }
    .status-chip.good { color: var(--teal); background: var(--teal-tint); }
    .status-chip.accent { color: var(--blue); background: var(--blue-tint); }

    /* Topic detail panel — focused pillar */
    .topic-detail {
      margin: 0 0 24px;
      border-radius: 12px;
      border: 0.5px solid var(--rule);
      background: var(--surface);
      box-shadow: none;
      padding: 24px;
    }
    .topic-detail.empty {
      border-style: dashed;
      background: var(--paper);
    }
    .topic-detail-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
      margin-bottom: 16px;
    }
    .topic-detail h2 {
      margin: 4px 0 4px;
      font-family: var(--display);
      font-weight: 500;
      font-size: clamp(28px, 4vw, 44px);
      letter-spacing: -0.01em;
    }
    .topic-detail .eyebrow {
      color: var(--ink-soft);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: 14px;
      font-weight: 600;
    }
    .workflow-rail {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }
    .workflow-step {
      border: 0.5px solid var(--rule);
      border-radius: 12px;
      padding: 14px;
      background: var(--surface);
    }
    .workflow-step strong {
      display: block;
      margin-bottom: 4px;
      font-family: var(--display);
      font-size: 16px;
    }
    .workflow-step.current {
      border-color: var(--coral);
      background: var(--coral-tint);
    }
    .topic-inventory {
      margin-top: 20px;
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.65fr);
      gap: 20px;
    }
    .inventory-panel {
      border: 0.5px solid var(--rule);
      border-radius: 12px;
      padding: 18px;
      background: var(--surface);
    }
    .inventory-panel h3 { margin: 0 0 10px; font-size: 18px; }
    .subtopic-bucket {
      border-top: var(--hairline);
      padding-top: 14px;
      margin-top: 14px;
    }
    .subtopic-bucket:first-of-type { border-top: 0; padding-top: 0; }
    .video-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .video-chip {
      border: 0.5px solid var(--rule);
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--surface);
      color: var(--ink);
      font-size: 14px;
    }
    .video-chip .meta {
      color: var(--ink-soft);
      font-family: var(--mono);
      font-size: 11px;
      margin-top: 4px;
    }
    .readiness {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 8px;
      padding: 3px 9px;
      margin-left: 6px;
      font-family: var(--body);
      font-size: 11px;
      font-weight: 500;
      border: 0;
    }
    .readiness.ready { color: var(--teal); background: var(--teal-tint); }
    .readiness.needs-transcripts { color: var(--coral); background: var(--coral-tint); }
    .readiness.thin { color: var(--bad); background: rgba(153,27,27,0.08); }
    .transcript-coverage {
      color: var(--ink-soft);
      font-family: var(--mono);
      font-size: 11px;
      margin-top: 6px;
    }
    .subtopic-actions { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
    @media (max-width: 900px) { .topic-inventory { grid-template-columns: 1fr; } }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 20px;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 18px;
      padding-bottom: 14px;
      border-bottom: var(--hairline);
    }
    .section-head h2 { font-size: 22px; }
    .metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 18px;
    }
    .metric {
      min-width: 130px;
      padding: 14px 16px;
      background: var(--surface);
      border: 0.5px solid var(--rule);
      border-radius: 12px;
    }
    .metric .k {
      display: block;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--ink-soft);
      margin-bottom: 6px;
    }
    .metric strong {
      font-family: var(--display);
      font-weight: 500;
      font-size: 22px;
    }
    .cards {
      display: grid;
      gap: 14px;
    }
    .card {
      border: 0.5px solid var(--rule);
      border-radius: 12px;
      padding: 18px;
      background: var(--surface);
    }
    .card h4 {
      margin: 0 0 10px;
      font-family: var(--display);
      font-weight: 500;
      font-size: 18px;
      letter-spacing: -0.005em;
    }
    .next-step {
      margin: 12px 0;
      padding: 12px 14px;
      border: 0.5px solid var(--coral);
      background: var(--coral-tint);
      border-radius: 8px;
      color: var(--coral);
      font-size: 13px;
    }
    .next-step.good {
      border-color: var(--teal);
      background: var(--teal-tint);
      color: var(--teal);
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 8px;
      padding: 4px 10px;
      border: 0;
      color: var(--ink-soft);
      background: var(--tag-bg);
      font-family: var(--body);
      font-size: 11px;
      font-weight: 400;
      margin-right: 6px;
      margin-bottom: 6px;
    }
    ul.samples {
      padding-left: 18px;
      margin: 10px 0;
    }
    ul.samples li {
      margin-bottom: 6px;
      color: var(--ink-soft);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    .inline-field { width: min(320px, 100%); }
    .status {
      margin-top: 14px;
      padding: 12px 14px;
      border-radius: 8px;
      border: 0.5px solid var(--rule);
      background: var(--tag-bg);
      color: var(--ink);
      min-height: 44px;
      white-space: pre-wrap;
      font-family: var(--mono);
      font-size: 12px;
    }
    .list { display: grid; gap: 10px; }
    .list-item,
    .application-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 12px 14px;
      border-radius: 8px;
      border: 0.5px solid var(--rule);
      background: var(--surface);
    }
    .label-applications {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }
    .application-row .meta {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-soft);
      margin-top: 4px;
    }
    .empty {
      color: var(--ink-soft);
      border: 0.5px dashed var(--rule);
      border-radius: 12px;
      padding: 20px;
      background: var(--paper);
      text-align: center;
    }
    code {
      color: var(--ink);
      font-family: var(--mono);
      font-size: 12px;
      background: var(--tag-bg);
      padding: 1px 6px;
      border-radius: 4px;
      word-break: break-all;
    }
    @media (max-width: 800px) {
      .topbar { padding: 14px 20px; }
      .stepper { padding: 16px 20px 20px; grid-template-columns: 1fr 1fr; gap: 16px 8px; }
      .stage-inner { padding: 24px 20px 80px; }
      .grid { grid-template-columns: 1fr; }
      .topic-map-head { flex-direction: column; align-items: stretch; }
    }

    /* ─── Design-faithful Review canvas ─── */

    /* Hide legacy panels (kept in DOM so existing tests pass). */
    .panel.channel-overview,
    section.topic-map:not(.discovery-topic-map),
    #selected-topic-detail,
    .stage-inner > .wrap > .grid,
    .run-history-advanced,
    .generator { display: none !important; }
    /* The settings panel (controls row + selects) is collapsed away too.
       The discovery-topic-map IS the Review surface. */
    .panel.review-settings { display: none !important; }

    /* Review toolbar — eyebrow + serif H1 + sort/action row */
    .review-toolbar {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 24px;
      margin-bottom: 28px;
      padding-bottom: 16px;
      border-bottom: var(--hairline);
    }
    .review-toolbar h1 { font-size: 28px; }
    .review-toolbar .lede {
      margin-top: 8px;
      max-width: 60ch;
      color: var(--ink-soft);
      font-size: 15px;
    }
    .review-toolbar .lede .accent { color: var(--coral); }
    .review-toolbar .toolbar-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    .review-toolbar .toolbar-actions button.active {
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
    }
    .review-toolbar .toolbar-actions button.active:hover {
      background: var(--ink);
      color: #fff;
    }
    .review-toolbar .toolbar-actions .sort-label {
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.04em;
      color: var(--ink-soft);
      align-self: center;
      margin-right: 4px;
      text-transform: uppercase;
    }

    /* Review canvas — overview vs focused */
    .review-canvas { display: block; }
    .review-canvas.is-focused {
      display: grid;
      grid-template-columns: 240px 1fr;
      gap: 0;
      align-items: stretch;
      min-height: 720px;
      border: 0.5px solid var(--rule);
      border-radius: 12px;
      background: var(--paper);
      overflow: hidden;
    }
    .review-canvas:not(.is-focused) #minimap { display: none; }
    .review-canvas.is-focused #review-overview { display: none; }
    .review-canvas:not(.is-focused) #review-focused { display: none; }

    /* Pillar overview grid */
    #review-overview .topic-map-grid,
    #discovery-topic-map-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 20px;
    }
    @media (max-width: 1100px) {
      #review-overview .topic-map-grid,
      #discovery-topic-map-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 720px) {
      #review-overview .topic-map-grid,
      #discovery-topic-map-grid { grid-template-columns: 1fr; }
    }
    /* Compact pillar — no inline episodes in overview state */
    .pillar {
      background: var(--surface);
      border: 0.5px solid var(--rule);
      border-radius: 12px;
      padding: 20px 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      min-height: 200px;
      cursor: pointer;
      transition: border-color 0.15s, transform 0.15s;
      position: relative;
    }
    .pillar:hover {
      border-color: var(--ink-soft);
      transform: translateY(-1px);
    }
    .pillar.is-active {
      border-color: var(--coral);
    }
    .pillar.is-active::before {
      content: "";
      position: absolute;
      left: -1px; top: -1px; bottom: -1px;
      width: 3px;
      background: var(--coral);
      border-radius: 12px 0 0 12px;
    }
    .pillar-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
    }
    .pillar-head h3 {
      font-family: var(--display);
      font-weight: 500;
      font-size: 22px;
      letter-spacing: -0.01em;
      max-width: 18ch;
      margin: 0;
    }
    .pillar-head .count {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-soft);
      letter-spacing: 0.04em;
      flex-shrink: 0;
    }
    .pillar .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .pillar .chip {
      font-family: var(--body);
      font-size: 11px;
      font-weight: 400;
      padding: 4px 10px;
      border-radius: 8px;
      color: var(--ink-soft);
      background: var(--tag-bg);
    }
    .pillar-foot {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      margin-top: auto;
      padding-top: 8px;
      gap: 12px;
    }
    .pillar-foot .pct {
      font-family: var(--mono);
      font-size: 10.5px;
      color: var(--ink-mute);
      letter-spacing: 0.04em;
    }
    /* Dot grid — confidence sparkline */
    .dotgrid {
      display: inline-grid;
      grid-auto-flow: column;
      gap: 2px;
    }
    .dotgrid .d {
      width: 4px; height: 14px; border-radius: 2px;
      background: var(--rule);
    }
    .dotgrid .d.s4 { background: var(--coral); }
    .dotgrid .d.s3 { background: #E48462; }
    .dotgrid .d.s2 { background: #ECAA94; }
    .dotgrid .d.s1 { background: #F1CFC0; }

    .overview-hint {
      margin: 36px 0 0;
      text-align: center;
      font-family: var(--display);
      font-style: italic;
      font-size: 14px;
      color: var(--ink-soft);
    }

    /* Minimap (focused state) */
    #minimap {
      border-right: var(--hairline);
      background: var(--paper);
      display: flex;
      flex-direction: column;
    }
    .minimap-head {
      padding: 20px 24px 16px 32px;
      border-bottom: var(--hairline);
    }
    .minimap-head .eyebrow { margin-bottom: 6px; font-size: 12px; }
    .minimap-head .mono { font-size: 11px; color: var(--ink-soft); }
    .mm-row {
      padding: 16px 24px 16px 32px;
      border-bottom: var(--hairline);
      cursor: pointer;
      position: relative;
    }
    .mm-row:hover { background: rgba(216, 90, 48, 0.04); }
    .mm-row .mm-name {
      display: block;
      font-family: var(--display);
      font-weight: 500;
      font-size: 16px;
      letter-spacing: -0.005em;
      line-height: 1.25;
      color: var(--ink-soft);
    }
    .mm-row .mm-count {
      display: block;
      margin-top: 4px;
      font-family: var(--mono);
      font-size: 10.5px;
      color: var(--ink-mute);
      letter-spacing: 0.04em;
    }
    .mm-row.is-focus { background: var(--surface); }
    .mm-row.is-focus .mm-name { color: var(--ink); }
    .mm-row.is-focus::before {
      content: "";
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 3px;
      background: var(--coral);
    }
    .minimap-back {
      padding: 18px 24px 24px 32px;
      margin-top: auto;
    }
    .minimap-back button {
      padding: 0;
      font-size: 12px;
      color: var(--ink-soft);
      border: 0;
      background: transparent;
    }
    .minimap-back button:hover { color: var(--ink); background: transparent; }

    /* Focused content */
    .focused-content {
      padding: 40px 56px 64px;
      max-width: 1000px;
      background: var(--surface);
    }
    .focused-content .focus-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      margin-bottom: 24px;
    }
    .focused-content .focus-head h1 {
      font-size: 44px;
      margin-bottom: 8px;
      letter-spacing: -0.01em;
    }
    .focused-content .focus-head .lede {
      color: var(--ink-soft);
      font-size: 15px;
      max-width: 52ch;
    }
    .focused-content .focus-head .focus-actions {
      display: flex;
      gap: 8px;
      flex-shrink: 0;
    }

    /* Subtopic tab strip */
    .subtopic-tabs {
      display: flex;
      gap: 28px;
      margin-bottom: 20px;
      flex-wrap: wrap;
      border-bottom: var(--hairline);
    }
    .subtopic-tab {
      font-family: var(--display);
      font-size: 16px;
      color: var(--ink-soft);
      border-bottom: 1px solid transparent;
      padding-bottom: 6px;
      cursor: pointer;
      background: none;
      border-top: 0; border-left: 0; border-right: 0;
      border-radius: 0;
      transition: color 0.12s, border-color 0.12s;
    }
    .subtopic-tab .count {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-mute);
      margin-left: 4px;
    }
    .subtopic-tab:hover { color: var(--ink); background: none; border-color: transparent; }
    .subtopic-tab.is-active {
      color: var(--ink);
      border-bottom-color: var(--coral);
    }

    /* In focused mode, the panel chrome on the discovery-topic-map wrapper goes away */
    .review-canvas.is-focused .topic-map.discovery-topic-map {
      border: 0;
      padding: 0;
      margin: 0;
      background: transparent;
    }
    .review-canvas .topic-map.discovery-topic-map { padding: 0; border: 0; background: transparent; margin: 0; }
    .review-canvas .topic-map-head { display: none; }

    /* Episode action column — design's "Watch / Rename / Wrong topic" stack */
    .ep-actions-col {
      display: flex;
      flex-direction: column;
      gap: 6px;
      align-items: flex-end;
    }
    .ep-actions-col button {
      font-size: 12px;
      padding: 6px 10px;
    }

    /* Override episode list when inside focused content — give it 3-col layout */
    .focused-content .discovery-episode {
      grid-template-columns: 152px minmax(0, 1fr) auto;
      gap: 24px;
    }
    .focused-content .discovery-episode-actions {
      display: flex;
      flex-direction: column;
      gap: 6px;
      align-items: flex-end;
    }
    .focused-content .discovery-episode-actions button {
      font-size: 12px;
      padding: 6px 10px;
      width: max-content;
    }

    /* ---------- Stage routing ---------- */
    .stage-panel[hidden] { display: none !important; }
    .step { cursor: pointer; }
    .step:disabled { cursor: default; }
    .step:not(:disabled):hover .label { color: var(--ink); }

    /* ---------- Supply stage ---------- */
    .supply-wrap {
      max-width: 1200px;
      margin: 0 auto;
      padding: 48px 48px 96px;
    }
    .channel-header {
      display: flex;
      gap: 24px;
      align-items: flex-start;
      margin-bottom: 48px;
    }
    .channel-header .ch-avatar {
      width: 88px;
      height: 88px;
      border-radius: 999px;
      background: var(--teal);
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: var(--display);
      font-weight: 500;
      font-size: 36px;
      color: #fff;
      flex-shrink: 0;
    }
    .channel-header .ch-body {
      flex: 1;
      padding-top: 6px;
    }
    .channel-header .ch-body h1 {
      margin: 4px 0 6px;
      font-family: var(--display);
      font-size: 40px;
      font-weight: 500;
      letter-spacing: -0.01em;
      line-height: 1.1;
    }
    .channel-header .ch-description {
      color: var(--ink-soft);
      font-size: 15px;
      max-width: 620px;
      margin: 0;
    }
    .channel-header .ch-meta {
      display: flex;
      gap: 12px;
      margin-top: 16px;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      flex-wrap: wrap;
    }
    .channel-header .ch-meta span.sep { color: var(--ink-mute); }
    .channel-header .ch-actions {
      display: flex;
      flex-direction: column;
      gap: 8px;
      align-items: flex-end;
    }
    .channel-header .ch-hint {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-mute);
      margin-top: 4px;
    }

    .supply-toolbar {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin-bottom: 8px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--rule);
    }
    .supply-h2 {
      font-family: var(--display);
      font-size: 28px;
      font-weight: 500;
      letter-spacing: -0.005em;
      margin: 4px 0 0;
    }
    .supply-toolbar-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .supply-toolbar-actions .sort-label {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-soft);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-right: 6px;
    }
    .supply-toolbar-actions button {
      font-family: var(--body);
      font-size: 13px;
      padding: 6px 12px;
      border: 1px solid var(--rule);
      border-radius: 999px;
      background: var(--paper);
      color: var(--ink-soft);
      cursor: pointer;
    }
    .supply-toolbar-actions button.active {
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
    }

    .supply-video-list { margin-top: 18px; }
    .supply-row {
      display: grid;
      grid-template-columns: 176px 1fr 200px;
      gap: 24px;
      padding: 20px 0;
      border-bottom: 1px solid var(--rule);
      align-items: flex-start;
    }
    .supply-row .sv-thumb {
      width: 160px;
      height: 90px;
      background: var(--tag-bg) repeating-linear-gradient(
        135deg,
        rgba(0,0,0,0.04) 0, rgba(0,0,0,0.04) 1px,
        transparent 1px, transparent 6px);
      border-radius: 4px;
      background-size: cover;
      background-position: center;
    }
    .supply-row .sv-title {
      font-family: var(--body);
      font-weight: 500;
      font-size: 17px;
      line-height: 1.4;
      letter-spacing: -0.005em;
      max-width: 62ch;
      margin: 2px 0 0;
      color: var(--ink);
    }
    .supply-row .sv-meta {
      margin-top: 8px;
      display: flex;
      gap: 14px;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      flex-wrap: wrap;
    }
    .supply-row .sv-meta .sep { color: var(--ink-mute); }
    .supply-row .sv-actions {
      display: flex;
      flex-direction: column;
      gap: 6px;
      align-items: flex-end;
      padding-top: 4px;
    }
    .supply-row .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.02em;
      padding: 4px 10px;
      border-radius: 999px;
    }
    .supply-row .pill .pill-dot {
      width: 6px;
      height: 6px;
      border-radius: 999px;
      display: inline-block;
    }
    .supply-row .pill-good { background: var(--teal-tint); color: var(--teal); }
    .supply-row .pill-good .pill-dot { background: var(--teal); }
    .supply-row .pill-bad { background: var(--coral-tint); color: var(--coral); }
    .supply-row .pill-bad .pill-dot { background: var(--coral); }
    .supply-row .pill-neutral { background: var(--tag-bg); color: var(--ink-soft); }
    .supply-row .pill-neutral .pill-dot { background: var(--ink-soft); }
    .supply-row .sv-hint {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-mute);
    }

    .supply-video-footer {
      margin-top: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
    }
    .supply-load-more {
      font-family: var(--mono);
      font-size: 12px;
      padding: 6px 14px;
      border: 1px solid var(--rule);
      border-radius: 4px;
      background: var(--paper);
      color: var(--ink);
      cursor: pointer;
    }
    .supply-load-more:hover { background: var(--tag-bg); }
    .supply-load-more:disabled { opacity: 0.6; cursor: progress; }

    /* ---------- Discover stage ---------- */
    .discover-h1 {
      font-family: var(--display);
      font-size: 40px;
      font-weight: 500;
      letter-spacing: -0.01em;
      line-height: 1.1;
      margin: 4px 0 18px;
    }
    .discover-lede {
      color: var(--ink-soft);
      font-size: 16px;
      max-width: 62ch;
      margin: 0 0 36px;
    }
    .discover-run-panel {
      border: 1px solid var(--rule);
      border-radius: 6px;
      background: var(--surface, var(--panel));
      padding: 24px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 24px;
      align-items: center;
    }
    .discover-run-panel h3 {
      font-family: var(--display);
      font-weight: 500;
      font-size: 22px;
      margin: 0;
      display: inline-block;
    }
    .discover-run-headline {
      display: flex;
      gap: 16px;
      align-items: center;
      flex-wrap: wrap;
    }
    .discover-run-meta {
      margin-top: 16px;
      display: flex;
      gap: 24px;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      flex-wrap: wrap;
    }
    .discover-run-meta strong {
      color: var(--ink);
      font-weight: 500;
    }
    .discover-mode-toggle {
      margin-top: 18px;
      display: inline-flex;
      border: 1px solid var(--rule);
      border-radius: 999px;
      padding: 3px;
      font-size: 12px;
      font-family: var(--mono);
    }
    .discover-mode-toggle .opt {
      padding: 5px 14px;
      border-radius: 999px;
      color: var(--ink-soft);
    }
    .discover-mode-toggle .opt.on {
      background: var(--ink);
      color: var(--paper);
    }
    .discover-run-action {
      padding: 14px 22px;
      font-size: 15px;
      font-family: var(--body);
      font-weight: 500;
      border: 1px solid var(--ink);
      background: var(--ink);
      color: var(--paper);
      border-radius: 6px;
      cursor: pointer;
    }
    .pill-warn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.02em;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(216, 90, 48, 0.12);
      color: var(--coral);
    }

    .discover-history { margin-top: 64px; }
    .discover-history-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--rule);
    }
    .discover-history-head h2 {
      font-family: var(--display);
      font-size: 28px;
      font-weight: 500;
      margin: 0;
    }
    .discover-run-row {
      display: grid;
      grid-template-columns: 70px 1.4fr 1fr 0.8fr 0.9fr 0.55fr 28px;
      gap: 18px;
      padding: 20px 12px;
      margin: 0 -12px;
      border-bottom: 1px solid var(--rule);
      align-items: flex-start;
      cursor: pointer;
      border-radius: 6px;
      transition: background-color 0.12s ease;
    }
    .discover-run-row:hover {
      background: rgba(15, 110, 86, 0.04);
    }
    .discover-run-row.is-active {
      background: rgba(15, 110, 86, 0.08);
    }
    .discover-run-row.is-active .dr-chevron {
      color: var(--teal);
    }
    .discover-run-row .dr-cost {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      padding-top: 4px;
      text-align: right;
    }
    .discover-run-row .dr-cost.dr-cost-empty {
      color: var(--ink-mute);
    }
    .discover-run-row .dr-num {
      font-family: var(--display);
      font-size: 26px;
      font-weight: 500;
      color: var(--ink);
    }
    .discover-run-row .dr-model {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink);
    }
    .discover-run-row .dr-prompt {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
    }
    .discover-run-row .dr-error {
      margin-top: 6px;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--coral);
    }
    .discover-run-row .dr-when,
    .discover-run-row .dr-counts {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      padding-top: 4px;
    }
    .discover-run-row .dr-status {
      padding-top: 2px;
    }
    .discover-run-row .dr-chevron {
      color: var(--ink-mute);
      font-size: 16px;
      padding-top: 4px;
    }

    /* ---------- Refine stage ---------- */
    #refine-setup { display: block; }
    .refine-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      margin-bottom: 18px;
    }
    .refine-sample-table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--body);
      font-size: 14px;
    }
    .refine-sample-table th {
      text-align: left;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--ink-mute);
      padding: 6px 10px;
      border-bottom: 1px solid var(--rule);
    }
    .refine-sample-table td {
      padding: 10px;
      border-bottom: 1px solid var(--rule);
      vertical-align: top;
    }
    .refine-sample-table tr.is-dropped td { opacity: 0.45; }
    .refine-slot {
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(15, 110, 86, 0.10);
      color: var(--teal);
    }
    .refine-slot.blind_spot { background: rgba(216, 90, 48, 0.12); color: var(--coral); }
    .refine-slot.added { background: var(--rule); color: var(--ink-soft); }
    .refine-tstatus { font-family: var(--mono); font-size: 12px; }
    .refine-tstatus.available { color: var(--teal); }
    .refine-tstatus.missing { color: var(--ink-mute); }
    .refine-row-rm {
      border: none;
      background: none;
      cursor: pointer;
      color: var(--ink-mute);
      font-size: 16px;
      line-height: 1;
    }
    .refine-row-rm:hover { color: var(--coral); }
    .refine-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
      margin-top: 20px;
    }
    .refine-actions input[type="text"] {
      font-family: var(--mono);
      font-size: 13px;
      padding: 8px 10px;
      border: 1px solid var(--rule);
      border-radius: 6px;
      min-width: 280px;
    }
    .refine-actions .secondary {
      padding: 9px 16px;
      font-size: 13px;
      border-radius: 6px;
    }
    .refine-note {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      margin-top: 12px;
    }
    .refine-note.warn { color: var(--coral); }
    .refine-empty { color: var(--ink-soft); padding: 24px 0; }
    .refine-proposals { margin-top: 32px; border-top: 1px solid var(--rule); padding-top: 20px; }
    .refine-proposals > h2 { font-size: 18px; margin: 0 0 4px; }
    .refine-prop-run > h3 {
      font-family: var(--mono); font-size: 12px; color: var(--ink-mute);
      text-transform: uppercase; letter-spacing: 0.08em; margin: 18px 0 8px;
    }
    .refine-prop-card {
      border: 1px solid var(--rule); border-radius: 8px;
      padding: 12px 14px; margin-bottom: 10px;
    }
    .refine-prop-head { font-size: 14px; }
    .refine-prop-kind {
      font-family: var(--mono); font-size: 11px; padding: 1px 6px;
      border-radius: 4px; background: var(--rule); color: var(--ink-soft);
      text-transform: uppercase; letter-spacing: 0.06em; margin-right: 6px;
    }
    .refine-prop-kind.subtopic { background: rgba(15, 118, 110, 0.12); color: var(--teal); }
    .refine-prop-ev {
      font-size: 13px; color: var(--ink-soft); margin: 6px 0;
      border-left: 2px solid var(--rule); padding-left: 10px;
    }
    .refine-prop-src { font-size: 12px; color: var(--ink-mute); margin-bottom: 8px; }
    .refine-prop-actions { display: flex; gap: 8px; }
    .refine-nudge {
      margin-top: 16px; padding: 12px 14px; border-radius: 8px;
      background: rgba(15, 118, 110, 0.08); font-size: 13px; color: var(--ink-soft);
    }
    .refine-review { margin-top: 28px; }
    .refine-diff-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; font-size: 12px; }
    .refine-diff-chips span {
      font-family: var(--mono); padding: 1px 6px; border-radius: 4px; background: var(--rule);
    }
    .refine-diff-add { background: rgba(15, 118, 110, 0.14) !important; color: var(--teal); }
    .refine-diff-drop { background: rgba(216, 90, 48, 0.14) !important; color: var(--coral); }
    .refine-after-list { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }
    .refine-after-list li { margin: 2px 0; }
    .refine-after-list .discovery-episode-wrong { margin-left: 6px; }
    .discovery-episode-checked {
      font-family: var(--mono); font-size: 10px; padding: 1px 5px;
      border-radius: 4px; background: rgba(15, 118, 110, 0.14); color: var(--teal);
      text-transform: uppercase; letter-spacing: 0.05em;
    }

    /* ---------- Consume stage ---------- */
    .consume-wrap {
      max-width: 1200px;
      margin: 0 auto;
      padding: 48px 48px 96px;
      display: grid;
      grid-template-columns: 240px 1fr;
      gap: 56px;
      align-items: start;
    }
    .consume-filters .consume-filter-group {
      margin-top: 24px;
    }
    .consume-filters .consume-filter-label {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--ink-soft);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-bottom: 10px;
    }
    .consume-filters .consume-topic-filters {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .consume-filters .consume-topic-filters label {
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      color: var(--ink-soft);
      gap: 8px;
    }
    .consume-filters .consume-topic-filters label .swatch {
      width: 12px;
      height: 12px;
      border: 1px solid var(--rule);
      border-radius: 2px;
      display: inline-block;
      margin-top: 3px;
      flex-shrink: 0;
    }
    .consume-filters .consume-topic-filters label .name {
      flex: 1;
      display: flex;
      gap: 8px;
    }
    .consume-filters .consume-topic-filters label .count {
      font-family: var(--mono);
      font-size: 11px;
    }
    .consume-filters .consume-filter-empty {
      font-family: var(--display);
      font-style: italic;
      font-size: 13px;
      color: var(--ink-mute);
    }
    .consume-h1 {
      font-family: var(--display);
      font-size: 40px;
      font-weight: 500;
      letter-spacing: -0.01em;
      line-height: 1.1;
      margin: 4px 0 14px;
    }
    .consume-lede {
      color: var(--ink-soft);
      font-size: 16px;
      max-width: 60ch;
      margin: 0 0 40px;
    }
    .consume-empty {
      border: 1px dashed var(--rule);
      border-radius: 6px;
      padding: 56px 32px;
      text-align: center;
    }
    .consume-empty .consume-empty-mark {
      width: 40px;
      height: 40px;
      border-radius: 999px;
      background: var(--tag-bg);
      margin: 0 auto 18px;
    }
    .consume-empty h3 {
      font-family: var(--display);
      font-size: 22px;
      font-weight: 500;
      margin: 0 0 10px;
    }
    .consume-empty p {
      color: var(--ink-soft);
      max-width: 44ch;
      margin: 0 auto;
    }

    .consume-sketch-wrap { margin-top: 56px; }
    .consume-sketch {
      border: 1px solid var(--rule);
      background: var(--surface, var(--panel));
      border-radius: 6px;
      padding: 32px 36px;
      margin-top: 14px;
      position: relative;
    }
    .consume-sketch-tag {
      position: absolute;
      top: 14px;
      right: 18px;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--ink-mute);
    }
    .consume-sketch-quote {
      font-family: var(--display);
      font-style: italic;
      font-size: 22px;
      line-height: 1.45;
      max-width: 56ch;
      color: var(--ink);
      margin: 0;
    }
    .consume-sketch-foot {
      margin-top: 18px;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
    }
    .consume-sketch-speaker {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink);
    }
    .consume-sketch-source {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      margin-top: 4px;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(44, 44, 42, 0.45);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .modal-backdrop[data-open="true"] { display: flex; }
    .modal-card {
      background: var(--paper);
      border: 1px solid var(--rule);
      border-radius: 8px;
      padding: 24px 26px;
      width: min(440px, calc(100vw - 48px));
      box-shadow: 0 18px 48px rgba(44, 44, 42, 0.18);
    }
    .modal-card h3 {
      margin: 0 0 8px;
      font-family: var(--display);
      font-size: 22px;
      color: var(--ink);
    }
    .modal-card p {
      margin: 0 0 6px;
      color: var(--ink-soft);
      line-height: 1.5;
    }
    .modal-card .modal-meta {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      background: var(--tag-bg);
      border-radius: 6px;
      padding: 8px 12px;
      margin: 14px 0 18px;
    }
    .modal-card .modal-actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      margin-top: 18px;
    }
    .modal-card .modal-actions button {
      padding: 8px 18px;
      font-family: var(--body);
      font-size: 14px;
      font-weight: 500;
      border-radius: 6px;
      cursor: pointer;
    }
    .modal-card .modal-actions .modal-cancel {
      background: transparent;
      border: 1px solid var(--rule);
      color: var(--ink);
    }
    .modal-card .modal-actions .modal-confirm {
      background: var(--ink);
      border: 1px solid var(--ink);
      color: var(--paper);
    }
    .modal-card .modal-actions .modal-confirm[disabled] {
      opacity: 0.55;
      cursor: not-allowed;
    }
    .modal-card .modal-form { display: flex; flex-direction: column; gap: 12px; margin: 14px 0 4px; }
    .modal-card .modal-form label {
      display: flex;
      flex-direction: column;
      gap: 4px;
      font-family: var(--body);
      font-size: 12px;
      font-weight: 500;
      color: var(--ink-soft);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .modal-card .modal-form input,
    .modal-card .modal-form textarea {
      font-family: var(--body);
      font-size: 14px;
      font-weight: 400;
      color: var(--ink);
      background: var(--paper);
      border: 1px solid var(--rule);
      border-radius: 6px;
      padding: 8px 10px;
      text-transform: none;
      letter-spacing: 0;
    }
    .modal-card .modal-form textarea { resize: vertical; min-height: 84px; }
    .modal-card .modal-form input:focus,
    .modal-card .modal-form textarea:focus { outline: 2px solid var(--teal); outline-offset: -1px; }
    .modal-card .modal-hint { font-size: 12px; color: var(--ink-soft); margin-top: 6px; }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-left">
      <span class="wordmark"><span class="wordmark-dot"></span>YouTube Analyser</span>
      <span class="version">UI rev {{UI_REVISION}}</span>
    </div>
    <div class="topbar-right">
      <span class="channel-pill" id="channel-pill" hidden>
        <span class="av" id="channel-pill-av">·</span>
        <span id="channel-pill-name">—</span>
        <span class="div"></span>
        <span class="yt-id" id="channel-pill-id">—</span>
      </span>
      <button class="secondary" id="refresh-btn">Refresh</button>
    </div>
  </header>

  <nav class="stepper" aria-label="Pipeline stages">
    <button class="step" type="button" data-stage="supply">
      <span class="marker" aria-hidden="true"></span>
      <span class="step-text">
        <span class="label">Supply</span>
        <span class="sub" id="step-supply-sub">videos &amp; transcripts</span>
      </span>
    </button>
    <button class="step" type="button" data-stage="discover">
      <span class="marker" aria-hidden="true"></span>
      <span class="step-text">
        <span class="label">Discover</span>
        <span class="sub" id="step-discover-sub">topic discovery runs</span>
      </span>
    </button>
    <button class="step" type="button" data-stage="review">
      <span class="marker" aria-hidden="true"></span>
      <span class="step-text">
        <span class="label">Review</span>
        <span class="sub" id="step-review-sub">curate the topic map</span>
      </span>
    </button>
    <button class="step" type="button" data-stage="refine">
      <span class="marker" aria-hidden="true"></span>
      <span class="step-text">
        <span class="label">Refine</span>
        <span class="sub">transcript-grade sample</span>
      </span>
    </button>
    <button class="step" type="button" data-stage="consume">
      <span class="marker" aria-hidden="true"></span>
      <span class="step-text">
        <span class="label">Consume</span>
        <span class="sub">Phase C — sketch only</span>
      </span>
    </button>
  </nav>

  <main class="stage-inner stage-panel" data-stage="review" id="stage-review">
  <div class="wrap">
    <section class="review-toolbar">
      <div>
        <div class="eyebrow">Review · stage 3 of 5</div>
        <h1 class="title">The map of <em id="channel-display-name">your channel</em></h1>
        <p class="lede" id="review-lede">Loading channel data…</p>
      </div>
      <div class="toolbar-actions">
        <span class="sort-label">Sort</span>
        <button id="overview-sort-eps" class="active">Episode count ↓</button>
        <button id="overview-sort-az" class="secondary">Topic A–Z</button>
      </div>
    </section>

    <div id="status-box" class="status">Loading channel data… If this does not change, the page hit a client-side render error.</div>

    <!-- Legacy settings + controls — kept in DOM (tests inspect them) but hidden visually. -->
    <section class="panel review-settings" hidden>
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
      </div>
      <div class="generator">
        <div class="soft small">Generate a fresh run from this dataset, then review it below.</div>
        <div class="controls row stretch">
          <label>
            Model
            <input id="model-input" value="gpt-4.1-mini" placeholder="gpt-4.1-mini">
          </label>
          <label>
            Limit
            <input id="limit-input" type="number" min="1" step="1" placeholder="All eligible videos">
          </label>
          <button id="generate-topics-btn" class="primary-action">Generate topic suggestions</button>
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

    <div id="review-canvas" class="review-canvas">
      <aside id="minimap"></aside>
      <section class="topic-map discovery-topic-map">
        <div class="topic-map-head">
          <div>
            <h2>Auto-Discovered Topics</h2>
            <div class="muted">Latest discovery run. Episode counts and confidence come straight from the model — curate from here.</div>
          </div>
          <div id="discovery-topic-map-meta" class="muted"></div>
        </div>
        <div id="discovery-shorts-badge" class="muted" hidden></div>
        <div id="review-overview">
          <div id="discovery-topic-map-grid" class="topic-map-grid"></div>
          <p class="overview-hint">Click a pillar to focus it.</p>
        </div>
        <div id="review-focused" class="focused-content"></div>
      </section>
    </div>

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
  </main>

  <main class="stage-inner stage-panel" data-stage="supply" id="stage-supply" hidden>
    <div class="supply-wrap">
      <section class="channel-header" id="supply-channel-header"></section>

      <section class="supply-toolbar">
        <div>
          <div class="eyebrow">Supply · stage 1 of 5</div>
          <h2 class="supply-h2">Videos</h2>
        </div>
        <div class="supply-toolbar-actions">
          <span class="sort-label">Sort</span>
          <button id="supply-sort-newest" class="active">Newest first ↓</button>
          <button id="supply-sort-oldest" class="secondary">Oldest first ↑</button>
        </div>
      </section>

      <div id="supply-video-list" class="supply-video-list"></div>
      <div id="supply-video-footer" class="supply-video-footer"></div>
    </div>
  </main>

  <main class="stage-inner stage-panel" data-stage="discover" id="stage-discover" hidden>
    <div class="wrap">
      <div class="eyebrow">Discover · stage 2 of 5</div>
      <h1 class="discover-h1">Topic discovery</h1>
      <p class="discover-lede">
        Ask a model to read every transcript and propose a topology of topics
        and subtopics for the channel. Runs are cheap and re-runnable; your
        curation in Review persists across them.
      </p>

      <section id="discover-run-panel" class="discover-run-panel"></section>

      <section class="discover-history">
        <div class="discover-history-head">
          <h2>Run history</h2>
          <span id="discover-history-summary" class="mono small soft"></span>
        </div>
        <div id="discover-run-list"></div>
      </section>
    </div>
  </main>

  <main class="stage-inner stage-panel" data-stage="consume" id="stage-consume" hidden>
    <div class="consume-wrap">
      <aside class="consume-filters">
        <div class="eyebrow">Filter</div>
        <div class="consume-filter-group">
          <div class="consume-filter-label">By topic</div>
          <div id="consume-topic-filters" class="consume-topic-filters"></div>
        </div>
        <div class="consume-filter-group">
          <div class="consume-filter-label">By speaker</div>
          <div class="consume-filter-empty">Available once guest extraction lands.</div>
        </div>
        <div class="consume-filter-group">
          <div class="consume-filter-label">By claim type</div>
          <div class="consume-filter-empty">Available once Phase C ships.</div>
        </div>
      </aside>

      <section class="consume-main">
        <div class="eyebrow">Consume · stage 5 of 5</div>
        <h1 class="consume-h1">Claims</h1>
        <p class="consume-lede">
          Specific assertions the model has lifted from transcripts, with a
          quotation, the source video, and a timestamp. Click <em>watch source</em>
          to open the moment in YouTube.
        </p>

        <div class="consume-empty">
          <div class="consume-empty-mark"></div>
          <h3>No claims yet</h3>
          <p>
            Claim extraction runs after Review is caught up. The Phase C
            pipeline isn't wired in this build — when it ships, claims will
            appear here grouped by the topics you've curated.
          </p>
        </div>

        <div class="consume-sketch-wrap">
          <div class="eyebrow">Preview · single claim card</div>
          <div class="consume-sketch">
            <span class="consume-sketch-tag">not yet — sketch</span>
            <p class="consume-sketch-quote">
              "The cost of decarbonization is not in the energy itself — it's
              in the rate at which a society can rebuild its infrastructure.
              Every previous transition took fifty to seventy years."
            </p>
            <div class="consume-sketch-foot">
              <div>
                <div class="consume-sketch-speaker">Vaclav Smil</div>
                <div class="consume-sketch-source">
                  Climate change is a problem of governance, not technology — 2026-04-15 · 41:08
                </div>
              </div>
              <button class="btn" disabled>▶ Watch source</button>
            </div>
          </div>
        </div>
      </section>
    </div>
  </main>

  <main class="stage-inner stage-panel" data-stage="refine" id="stage-refine" hidden>
    <div class="wrap">
      <div class="eyebrow">Refine · stage 4 of 5</div>
      <h1 class="discover-h1">Refine the map from transcripts</h1>
      <p class="discover-lede">
        Pick a representative sample of episodes, fetch their transcripts, and
        ask a model to re-judge each one from the full transcript. It returns
        transcript-grade assignments plus proposals for new subtopics (and,
        rarely, topics). Accept the proposals on the next screen, then re-run
        Discover to spread them channel-wide.
      </p>
      <section id="refine-setup" class="discover-run-panel"></section>
    </div>
  </main>

  <div class="modal-backdrop" id="discover-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="discover-confirm-title" hidden>
    <div class="modal-card">
      <h3 id="discover-confirm-title">Run discovery</h3>
      <p id="discover-confirm-body">Confirm to run discovery.</p>
      <div class="modal-meta" id="discover-confirm-meta"></div>
      <div class="modal-actions">
        <button type="button" class="modal-cancel" id="discover-confirm-cancel">Cancel</button>
        <button type="button" class="modal-confirm" id="discover-confirm-go">Run</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="channel-edit-modal" role="dialog" aria-modal="true" aria-labelledby="channel-edit-title" hidden>
    <div class="modal-card">
      <h3 id="channel-edit-title">Edit channel</h3>
      <p>Override the display values for this channel. YouTube ID, thumbnail, and published date stay locked to the source.</p>
      <form class="modal-form" id="channel-edit-form" autocomplete="off">
        <label>Title
          <input type="text" id="channel-edit-title-input" required maxlength="200" />
        </label>
        <label>Handle
          <input type="text" id="channel-edit-handle-input" maxlength="100" placeholder="@channel" />
        </label>
        <label>Description
          <textarea id="channel-edit-description-input" rows="4" maxlength="2000"></textarea>
        </label>
      </form>
      <p class="modal-hint">Re-ingest will overwrite these with the latest YouTube values.</p>
      <div class="modal-actions">
        <button type="button" class="modal-cancel" id="channel-edit-cancel">Cancel</button>
        <button type="button" class="modal-confirm" id="channel-edit-go">Save</button>
      </div>
    </div>
  </div>

  <script>
    const STAGE_ORDER = ['supply', 'discover', 'review', 'refine', 'consume'];

    const state = {
      payload: null,
      activeTopicName: null,
      focusedTopic: null,
      activeSubtopic: null,
      overviewSort: 'episodes',
      activeStage: 'review',
      supplySort: 'newest',
      supplyLimit: 50,
      activeDiscoveryRunId: null,
      discoverMode: 'real',
      refine: {
        loaded: false,
        loading: false,
        error: null,
        discoveryRunId: null,
        poolSize: null,
        episodes: [],          // working set: {youtube_video_id, title, topic, confidence, transcript_status, slot_kind, available}
        estimate: null,        // {n_available, estimated_cost_usd}
        note: null,            // {text, warn}
        mode: 'real',
        running: false,
        runResult: null,       // {refinement_run_id, status, n_proposals, error}
        acceptedThisSession: 0, // proposals accepted since page load (drives the re-run-discovery nudge)
      },
    };

    function setActiveStage(stage) {
      if (!STAGE_ORDER.includes(stage)) return;
      state.activeStage = stage;
      STAGE_ORDER.forEach((s) => {
        const panel = document.getElementById('stage-' + s);
        if (panel) panel.hidden = (s !== stage);
      });
      renderStepper();
      if (stage === 'refine' && !state.refine.loaded && !state.refine.loading) {
        loadRefineSample().catch((error) => setStatus(error.message, true));
      }
    }

    function renderStepper() {
      const activeIdx = STAGE_ORDER.indexOf(state.activeStage);
      document.querySelectorAll('.stepper .step').forEach((btn) => {
        const stage = btn.getAttribute('data-stage');
        const idx = STAGE_ORDER.indexOf(stage);
        btn.classList.remove('done', 'act', 'idle');
        if (idx < activeIdx) btn.classList.add('done');
        else if (idx === activeIdx) btn.classList.add('act');
        else btn.classList.add('idle');
      });
    }

    function setSupplySort(mode) {
      state.supplySort = mode === 'oldest' ? 'oldest' : 'newest';
      const newestBtn = document.getElementById('supply-sort-newest');
      const oldestBtn = document.getElementById('supply-sort-oldest');
      if (newestBtn && oldestBtn) {
        newestBtn.classList.toggle('active', state.supplySort === 'newest');
        newestBtn.classList.toggle('secondary', state.supplySort !== 'newest');
        oldestBtn.classList.toggle('active', state.supplySort === 'oldest');
        oldestBtn.classList.toggle('secondary', state.supplySort !== 'oldest');
      }
      renderSupply(state.payload);
    }

    function focusTopic(name) {
      state.focusedTopic = name || null;
      state.activeSubtopic = null;
      renderDiscoveryTopicMap(lastDiscoveryTopicMap);
    }
    function setActiveSubtopic(name) {
      state.activeSubtopic = name || null;
      renderDiscoveryTopicMap(lastDiscoveryTopicMap);
    }
    function setOverviewSort(mode) {
      state.overviewSort = mode || 'episodes';
      const epsBtn = document.getElementById('overview-sort-eps');
      const azBtn = document.getElementById('overview-sort-az');
      if (epsBtn && azBtn) {
        epsBtn.classList.toggle('active', state.overviewSort === 'episodes');
        epsBtn.classList.toggle('secondary', state.overviewSort !== 'episodes');
        azBtn.classList.toggle('active', state.overviewSort === 'az');
        azBtn.classList.toggle('secondary', state.overviewSort !== 'az');
      }
      renderDiscoveryTopicMap(lastDiscoveryTopicMap);
    }

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
      if (state.activeDiscoveryRunId != null) {
        params.set('discovery_run_id', String(state.activeDiscoveryRunId));
      }
      if (state.supplyLimit) {
        params.set('supply_limit', String(state.supplyLimit));
      }
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
      const pillEl = document.getElementById('channel-pill');
      const pillName = document.getElementById('channel-pill-name');
      const pillId = document.getElementById('channel-pill-id');
      const pillAv = document.getElementById('channel-pill-av');
      const displayName = document.getElementById('channel-display-name');
      const supplySub = document.getElementById('step-supply-sub');
      const discoverSub = document.getElementById('step-discover-sub');
      const reviewSub = document.getElementById('step-review-sub');
      if (!overview) {
        titleEl.textContent = 'Channel Overview';
        subtitleEl.textContent = 'No primary channel set';
        statsEl.innerHTML = '';
        latestEl.innerHTML = '';
        if (pillEl) pillEl.hidden = true;
        if (displayName) displayName.textContent = 'your channel';
        return;
      }
      const channelTitle = overview.channel_title || 'Channel Overview';
      titleEl.textContent = channelTitle;
      subtitleEl.textContent = overview.channel_id ? `Channel ID: ${overview.channel_id}` : '';
      if (displayName) displayName.textContent = channelTitle;
      if (pillEl) {
        pillEl.hidden = false;
        if (pillName) pillName.textContent = channelTitle;
        if (pillAv) pillAv.textContent = (channelTitle || '·').trim().charAt(0).toUpperCase() || '·';
        if (pillId) {
          const cid = overview.channel_id || '';
          pillId.textContent = cid.length > 12 ? cid.slice(0, 4) + '…' + cid.slice(-4) : (cid || '—');
        }
      }
      if (supplySub) {
        const v = overview.video_count ?? 0;
        const t = overview.transcript_count ?? 0;
        supplySub.textContent = `${v} videos · ${t} with transcripts`;
      }
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
        if (discoverSub) discoverSub.textContent = 'no runs yet';
        if (reviewSub) reviewSub.textContent = '—';
        return;
      }
      latestEl.innerHTML = `<div class="muted"><strong>Latest discovery</strong> · run #${escapeHtml(latest.id)} · ${escapeHtml(latest.status)} · ${escapeHtml(latest.started_at)} · ${escapeHtml(latest.model)} · ${escapeHtml(latest.prompt_version)}</div>`;
      if (discoverSub) discoverSub.textContent = `run #${latest.id} · ${latest.started_at || ''}`.trim();
      if (reviewSub) reviewSub.textContent = `${overview.topic_count ?? 0} topics · ${overview.subtopic_count ?? 0} subtopics`;
    }

    function dotGridHtml(episodes, lowThreshold) {
      const eps = (episodes || []).slice(0, 16);
      if (!eps.length) return '<span class="dotgrid"></span>';
      const cells = eps.map((ep) => {
        const c = ep.confidence;
        if (c == null) return '<span class="d"></span>';
        if (c >= 0.85) return '<span class="d s4"></span>';
        if (c >= 0.7)  return '<span class="d s3"></span>';
        if (c >= lowThreshold) return '<span class="d s2"></span>';
        return '<span class="d s1"></span>';
      }).join('');
      return `<span class="dotgrid">${cells}</span>`;
    }

    function highConfidencePct(episodes, lowThreshold) {
      const eps = episodes || [];
      if (!eps.length) return null;
      const high = eps.filter((ep) => ep.confidence != null && ep.confidence >= 0.7).length;
      return Math.round((high / eps.length) * 100);
    }

    function renderDiscoveryTopicMap(map) {
      lastDiscoveryTopicMap = map;
      const canvas = document.getElementById('review-canvas');
      const grid = document.getElementById('discovery-topic-map-grid');
      const overviewWrap = document.getElementById('review-overview');
      const focusedWrap = document.getElementById('review-focused');
      const minimap = document.getElementById('minimap');
      const meta = document.getElementById('discovery-topic-map-meta');
      const lede = document.getElementById('review-lede');
      const shortsBadge = document.getElementById('discovery-shorts-badge');

      function renderShortsBadge(text) {
        if (!shortsBadge) return;
        if (text) {
          shortsBadge.textContent = text;
          shortsBadge.hidden = false;
        } else {
          shortsBadge.textContent = '';
          shortsBadge.hidden = true;
        }
      }

      if (!map) {
        canvas.classList.remove('is-focused');
        state.focusedTopic = null;
        grid.innerHTML = '<div class="empty">No discovery run yet. Run <code>analyze --stub</code> or <code>discover --stub</code> to populate this panel.</div>';
        meta.textContent = '';
        renderShortsBadge(null);
        if (lede) lede.textContent = 'No discovery run yet — run discover to populate the topic map.';
        return;
      }
      meta.textContent = `Run #${map.run_id} · ${map.model} · ${map.prompt_version} · ${map.status} · ${map.created_at}`;
      renderShortsBadge(map.shorts_filter_badge);

      const topics = (map.topics || []).slice();
      if (!topics.length) {
        canvas.classList.remove('is-focused');
        state.focusedTopic = null;
        grid.innerHTML = '<div class="empty">Latest discovery run produced no topic assignments.</div>';
        if (lede) lede.textContent = '0 topics in this run.';
        return;
      }

      const lowThreshold = (typeof map.low_confidence_threshold === 'number') ? map.low_confidence_threshold : 0.5;
      const newTopicNames = new Set(Array.isArray(map.new_topic_names) ? map.new_topic_names : []);
      const totalEpisodes = topics.reduce((acc, t) => acc + (t.episode_count || 0), 0);
      const subtopicTotal = topics.reduce((acc, t) => acc + (t.subtopic_count || (t.subtopics ? t.subtopics.length : 0)), 0);

      if (lede) {
        lede.innerHTML = `${topics.length} topics · ${totalEpisodes} episodes assigned · ${subtopicTotal} subtopics — sticky renames you make here persist across re-runs.`;
      }

      // Sort
      if (state.overviewSort === 'az') {
        topics.sort((a, b) => a.name.localeCompare(b.name));
      } else {
        topics.sort((a, b) => (b.episode_count || 0) - (a.episode_count || 0));
      }

      // Resolve focus
      const focusedName = state.focusedTopic;
      const focusedTopic = focusedName ? topics.find((t) => t.name === focusedName) : null;
      const isFocused = !!focusedTopic;
      canvas.classList.toggle('is-focused', isFocused);

      // Minimap (always render, hidden via CSS in overview state)
      minimap.innerHTML = `
        <div class="minimap-head">
          <div class="eyebrow">Topology</div>
          <span class="mono">${topics.length} pillars · ${totalEpisodes} videos</span>
        </div>
        ${topics.map((t) => {
          const subCount = t.subtopic_count || (t.subtopics ? t.subtopics.length : 0);
          const isActive = focusedName && t.name === focusedName;
          return `
            <div class="mm-row${isActive ? ' is-focus' : ''}" data-topic-name="${escapeHtml(t.name)}" onclick="focusTopic(this.dataset.topicName)">
              <span class="mm-name">${escapeHtml(t.name)}</span>
              <span class="mm-count">${escapeHtml(t.episode_count || 0)} videos · ${escapeHtml(subCount)} sub</span>
            </div>
          `;
        }).join('')}
        <div class="minimap-back">
          <button onclick="focusTopic(null)">← back to overview</button>
        </div>
      `;

      // Overview pillar grid
      grid.innerHTML = topics.map((topic) => {
        const subs = (topic.subtopics || []).map((s) => s.name);
        const subCount = topic.subtopic_count || subs.length;
        const newBadgeHtml = newTopicNames.has(topic.name)
          ? '<span class="discovery-topic-new-badge">New</span>'
          : '';
        const dgrid = dotGridHtml(topic.episodes, lowThreshold);
        const pct = highConfidencePct(topic.episodes, lowThreshold);
        const pctLabel = (pct == null) ? '—' : `${pct}% high-confidence`;
        const chipsHtml = subs.length
          ? `<div class="chips">${subs.slice(0, 6).map((s) => `<span class="chip">${escapeHtml(s)}</span>`).join('')}${subs.length > 6 ? `<span class="chip">+${subs.length - 6}</span>` : ''}</div>`
          : `<div class="chips"><span class="chip soft">${escapeHtml(subCount)} subtopics</span></div>`;
        const isActive = focusedName && topic.name === focusedName;
        return `
          <article class="pillar topic-card discovery-topic-card${isActive ? ' is-active' : ''}"
                   data-topic-name="${escapeHtml(topic.name)}"
                   onclick="focusTopic(this.dataset.topicName)">
            <div class="pillar-head discovery-topic-header">
              <h3>${escapeHtml(topic.name)}${newBadgeHtml}</h3>
              <span class="count">${escapeHtml(topic.episode_count || 0)} videos</span>
            </div>
            ${chipsHtml}
            <div class="pillar-foot">
              ${dgrid}
              <span class="pct">${escapeHtml(pctLabel)}</span>
            </div>
          </article>
        `;
      }).join('');

      // Focused state
      if (isFocused) {
        focusedWrap.innerHTML = renderFocusedTopic(focusedTopic, lowThreshold, newTopicNames);
      } else {
        focusedWrap.innerHTML = '';
      }
    }

    function renderFocusedTopic(topic, lowThreshold, newTopicNames) {
      const subs = topic.subtopics || [];
      const subCount = topic.subtopic_count || subs.length;
      const sortMode = discoveryEpisodeSortByTopic.get(topic.name) || DEFAULT_DISCOVERY_SORT;

      // Subtopic tab strip — "All" + each subtopic + "Unassigned"
      const unassigned = topic.unassigned_within_topic || [];
      const tabs = [{ name: '__all', label: 'All', count: topic.episode_count || 0 }];
      subs.forEach((s) => tabs.push({ name: s.name, label: s.name, count: s.episode_count || 0 }));
      if (unassigned.length) tabs.push({ name: '__unassigned', label: 'Unassigned', count: unassigned.length });
      const activeSub = state.activeSubtopic || '__all';
      const tabsHtml = `
        <div class="subtopic-tabs">
          ${tabs.map((t) => `
            <button type="button"
                    class="subtopic-tab${t.name === activeSub ? ' is-active' : ''}"
                    data-sub="${escapeHtml(t.name)}"
                    onclick="setActiveSubtopic(this.dataset.sub === '__all' ? null : this.dataset.sub)">
              ${escapeHtml(t.label)}<span class="count">· ${escapeHtml(t.count)}</span>
            </button>
          `).join('')}
        </div>
      `;

      // Episode list — filter by active subtopic if set
      let episodes = [];
      let bucketsHtml = '';
      if (activeSub === '__unassigned') {
        episodes = unassigned;
      } else if (activeSub === '__all') {
        // Show subtopic accordions (legacy structure expected by tests) + flat list
        bucketsHtml = renderDiscoverySubtopicBuckets(topic, sortMode, lowThreshold);
        episodes = topic.episodes || [];
      } else {
        const bucket = subs.find((s) => s.name === activeSub);
        episodes = bucket ? (bucket.episodes || []) : [];
      }
      const sortedEpisodes = sortDiscoveryEpisodes(episodes, sortMode);
      const showAlsoIn = activeSub === '__all';
      const episodeListHtml = sortedEpisodes.length
        ? `<ol class="discovery-episode-list">${sortedEpisodes.map((ep) => renderDiscoveryEpisodeItemFocused(ep, topic.name, activeSub, lowThreshold, showAlsoIn)).join('')}</ol>`
        : '<div class="muted discovery-episode-empty">No episodes in this view.</div>';

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

      const newBadgeHtml = newTopicNames.has(topic.name)
        ? '<span class="discovery-topic-new-badge">New</span>'
        : '';

      return `
        <div class="focus-head">
          <div>
            <div class="eyebrow">Focus</div>
            <h1>${escapeHtml(topic.name)}${newBadgeHtml}</h1>
            <p class="lede">${escapeHtml(topic.episode_count || 0)} videos across ${escapeHtml(subCount)} subtopics. Sticky renames you make here persist across re-runs of discovery.</p>
          </div>
          <div class="focus-actions discovery-topic-actions">
            <button class="discovery-topic-rename" type="button"
                    data-topic-name="${escapeHtml(topic.name)}"
                    onclick="renameDiscoveryTopic(this.dataset.topicName)">✎ Rename</button>
            <button class="discovery-topic-merge" type="button"
                    data-topic-name="${escapeHtml(topic.name)}"
                    onclick="mergeDiscoveryTopic(this.dataset.topicName)">⇆ Merge</button>
            <button class="discovery-topic-split" type="button"
                    data-topic-name="${escapeHtml(topic.name)}"
                    onclick="splitDiscoveryTopic(this.dataset.topicName)">＋ Split</button>
          </div>
        </div>
        ${tabsHtml}
        ${sortRowHtml}
        ${activeSub === '__all' ? bucketsHtml : ''}
        ${activeSub === '__all' ? '<h3 class="eyebrow" style="margin-top:24px;">All episodes</h3>' : ''}
        ${episodeListHtml}
      `;
    }

    // Episode row used in focused state — adds the design's action column.
    function renderDiscoveryEpisodeItemFocused(episode, topicName, activeSub, lowThreshold, showAlsoIn) {
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
      const alsoIn = (showAlsoIn && Array.isArray(episode.also_in)) ? episode.also_in : [];
      const alsoInHtml = alsoIn.length
        ? `<span class="discovery-episode-also-in">also in: ${alsoIn.map((name) => escapeHtml(name)).join(', ')}</span>`
        : '';
      const checkedHtml = episode.assignment_source === 'refine'
        ? '<span class="discovery-episode-checked" title="Re-judged from the full transcript">transcript-checked</span>'
        : '';
      const watchUrl = episode.youtube_video_id
        ? `https://www.youtube.com/watch?v=${encodeURIComponent(episode.youtube_video_id)}`
        : null;
      const watchBtn = watchUrl
        ? `<a class="primary-action" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px;padding:6px 10px;font-size:12px;border-radius:8px;" href="${escapeHtml(watchUrl)}" target="_blank" rel="noopener">▶ Watch</a>`
        : '';
      const subForButton = (activeSub && activeSub !== '__all' && activeSub !== '__unassigned') ? activeSub : null;
      const wrongTopicBtn = `<button class="discovery-episode-wrong" type="button"
                onclick='markEpisodeWrong(${JSON.stringify(topicName)}, ${JSON.stringify(episode.youtube_video_id || '')}, null)'>✗ Wrong topic</button>`;
      const wrongSubBtn = subForButton
        ? `<button class="subtopic-video-wrong" type="button" style="margin-left:0;"
                onclick='markEpisodeWrong(${JSON.stringify(topicName)}, ${JSON.stringify(episode.youtube_video_id || '')}, ${JSON.stringify(subForButton)})'>✗ Wrong subtopic</button>`
        : '';
      const publishedHtml = episode.published_at
        ? `<span>${escapeHtml(formatDate(episode.published_at))}</span>`
        : '';
      const durationStr = formatDuration(episode.duration_seconds);
      const durationHtml = durationStr ? `<span class="muted">${escapeHtml(durationStr)}</span>` : '';
      return `
        <li class="discovery-episode${lowClass}">
          ${thumbHtml}
          <div class="discovery-episode-body">
            <div class="discovery-episode-title">${escapeHtml(episode.title || '(untitled)')}</div>
            <div class="discovery-episode-meta">
              <span class="discovery-episode-confidence">conf ${escapeHtml(pct)}</span>
              ${checkedHtml}
              ${publishedHtml}
              ${durationHtml}
              <span>${escapeHtml(episode.youtube_video_id || '')}</span>
              ${alsoInHtml}
            </div>
            ${reasonHtml}
          </div>
          <div class="discovery-episode-actions">
            ${watchBtn}
            ${wrongTopicBtn}
            ${wrongSubBtn}
          </div>
        </li>
      `;
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
      const watchUrl = episode.youtube_video_id
        ? `https://www.youtube.com/watch?v=${encodeURIComponent(episode.youtube_video_id)}`
        : null;
      const watchBtn = watchUrl
        ? `<a class="primary-action" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px;padding:4px 8px;font-size:11px;border-radius:8px;margin-right:6px;" href="${escapeHtml(watchUrl)}" target="_blank" rel="noopener">▶ Watch</a>`
        : '';
      const wrongButton = topicName
        ? `<button class="discovery-episode-wrong"
                   type="button"
                   onclick='markEpisodeWrong(${JSON.stringify(topicName)}, ${JSON.stringify(episode.youtube_video_id || '')}, null)'>Wrong topic?</button>`
        : '';
      const alsoIn = (showAlsoIn && Array.isArray(episode.also_in)) ? episode.also_in : [];
      const alsoInHtml = alsoIn.length
        ? `<span class="discovery-episode-also-in">also in: ${alsoIn.map((name) => escapeHtml(name)).join(', ')}</span>`
        : '';
      const checkedHtml = episode.assignment_source === 'refine'
        ? '<span class="discovery-episode-checked" title="Re-judged from the full transcript">transcript-checked</span>'
        : '';
      const publishedHtml = episode.published_at
        ? `<span class="muted">${escapeHtml(formatDate(episode.published_at))}</span>`
        : '';
      const durationStr = formatDuration(episode.duration_seconds);
      const durationHtml = durationStr ? `<span class="muted">${escapeHtml(durationStr)}</span>` : '';
      return `
        <li class="discovery-episode${lowClass}">
          ${thumbHtml}
          <div class="discovery-episode-body">
            <div class="discovery-episode-title">${escapeHtml(episode.title || '(untitled)')}</div>
            <div class="discovery-episode-meta">
              <span class="discovery-episode-confidence">${escapeHtml(pct)}</span>
              ${checkedHtml}
              ${publishedHtml}
              ${durationHtml}
              <span class="muted">${escapeHtml(episode.youtube_video_id || '')}</span>
              ${alsoInHtml}
            </div>
            ${reasonHtml}
            <div style="margin-top:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
              ${watchBtn}
              ${wrongButton}
            </div>
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

    function videoChipWatchHtml(video) {
      if (!video.youtube_video_id) return '';
      const url = `https://www.youtube.com/watch?v=${encodeURIComponent(video.youtube_video_id)}`;
      return `<a class="primary-action" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;padding:3px 8px;font-size:11px;border-radius:8px;margin-top:6px;margin-right:6px;" href="${escapeHtml(url)}" target="_blank" rel="noopener">▶ Watch</a>`;
    }

    function videoChipMetaHtml(video) {
      const parts = [];
      if (video.published_at) parts.push(escapeHtml(formatDate(video.published_at)));
      if (video.youtube_video_id) parts.push(escapeHtml(video.youtube_video_id));
      return parts.length
        ? `<div class="meta">${parts.join(' · ')}</div>`
        : '';
    }

    function videoChipHtml(video) {
      return `<div class="video-chip">${escapeHtml(video.title || '(untitled)')}${videoChipMetaHtml(video)}${videoChipWatchHtml(video)}</div>`;
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
      return `<div class="video-chip">${escapeHtml(video.title || '(untitled)')}${videoChipMetaHtml(video)}${videoChipWatchHtml(video)}${moveButton}${wrongButton}</div>`;
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
            bucket = { ...bucket, transcript_count: bucket.transcript_count ?? 0, video_count: bucket.video_count ?? 0 };
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

    function formatDate(value) {
      if (!value) return '—';
      const s = String(value);
      // Trim time portion if SQLite "YYYY-MM-DD HH:MM:SS" — keep date only.
      const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
      return m ? m[1] : s;
    }

    function formatDateTime(value) {
      if (!value) return '—';
      return String(value).replace('T', ' ').slice(0, 16);
    }

    function formatDuration(seconds) {
      const n = Number(seconds);
      if (!Number.isFinite(n) || n <= 0) return null;
      const s = Math.round(n);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      const pad = (x) => String(x).padStart(2, '0');
      return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
    }

    function channelInitial(title) {
      const t = String(title || '').trim();
      if (!t) return '·';
      return t.slice(0, 1).toUpperCase();
    }

    function renderChannelHeader(supply) {
      const host = document.getElementById('supply-channel-header');
      if (!host) return;
      if (!supply) {
        host.innerHTML = '<p class="muted">No primary channel — initialize one first.</p>';
        return;
      }
      const newSinceRun = state.payload?.channel_overview?.latest_discovery
        ? null
        : null;
      const meta = [];
      if (supply.handle) meta.push(escapeHtml(supply.handle));
      if (supply.last_refreshed_at) meta.push('Ingested ' + escapeHtml(formatDateTime(supply.last_refreshed_at)));
      const metaJoined = meta.length
        ? meta.map((m, i) => (i ? '<span class="sep">·</span>' : '') + '<span>' + m + '</span>').join('')
        : '<span class="sep">No ingestion timestamp recorded</span>';
      const desc = supply.description
        ? '<p class="ch-description">' + escapeHtml(supply.description) + '</p>'
        : '';
      host.innerHTML = `
        <div class="ch-avatar">${escapeHtml(channelInitial(supply.title))}</div>
        <div class="ch-body">
          <div class="eyebrow">Channel</div>
          <h1>${escapeHtml(supply.title || 'Unknown channel')}</h1>
          ${desc}
          <div class="ch-meta">${metaJoined}</div>
        </div>
        <div class="ch-actions">
          <button class="primary-action" id="supply-reingest-btn">Re-ingest</button>
          <button class="secondary" id="supply-edit-btn">Edit channel</button>
        </div>
      `;
      const reingest = document.getElementById('supply-reingest-btn');
      if (reingest) {
        reingest.addEventListener('click', async () => {
          if (reingest.disabled) return;
          const originalLabel = reingest.textContent;
          reingest.disabled = true;
          reingest.textContent = 'Re-ingesting…';
          setStatus('Re-ingesting channel metadata and videos…');
          try {
            const payload = await postJson('/api/reingest', {});
            setStatus(payload.message || 'Re-ingest complete.');
            await fetchState({
              runId: state.payload?.run_id,
              topic: state.payload?.subtopic_reviews?.selected_topic || null,
              subtopic: state.payload?.comparison_reviews?.selected_subtopic || null,
            });
          } catch (error) {
            setStatus(error.message || 'Re-ingest failed.', true);
          } finally {
            reingest.disabled = false;
            reingest.textContent = originalLabel;
          }
        });
      }
      const edit = document.getElementById('supply-edit-btn');
      if (edit) edit.addEventListener('click', () => openChannelEdit());
    }

    function openChannelEdit() {
      const supply = state.payload?.supply_channel;
      const modal = document.getElementById('channel-edit-modal');
      const titleInput = document.getElementById('channel-edit-title-input');
      const handleInput = document.getElementById('channel-edit-handle-input');
      const descInput = document.getElementById('channel-edit-description-input');
      const goBtn = document.getElementById('channel-edit-go');
      if (!modal || !titleInput || !handleInput || !descInput || !goBtn) return;
      if (!supply) {
        setStatus('No channel loaded yet.', true);
        return;
      }
      titleInput.value = supply.title || '';
      handleInput.value = supply.handle || '';
      descInput.value = supply.description || '';
      goBtn.disabled = false;
      goBtn.textContent = 'Save';
      modal.hidden = false;
      modal.setAttribute('data-open', 'true');
      titleInput.focus();
    }

    function closeChannelEdit() {
      const modal = document.getElementById('channel-edit-modal');
      if (!modal) return;
      modal.hidden = true;
      modal.removeAttribute('data-open');
    }

    async function submitChannelEdit() {
      const goBtn = document.getElementById('channel-edit-go');
      const cancelBtn = document.getElementById('channel-edit-cancel');
      const titleInput = document.getElementById('channel-edit-title-input');
      const handleInput = document.getElementById('channel-edit-handle-input');
      const descInput = document.getElementById('channel-edit-description-input');
      if (!goBtn || !titleInput) return;
      const title = (titleInput.value || '').trim();
      if (!title) {
        setStatus('Channel title is required.', true);
        titleInput.focus();
        return;
      }
      goBtn.disabled = true;
      goBtn.textContent = 'Saving…';
      if (cancelBtn) cancelBtn.disabled = true;
      setStatus('Saving channel edits…');
      try {
        const payload = await postJson('/api/channel/edit', {
          title,
          handle: (handleInput.value || '').trim(),
          description: (descInput.value || '').trim(),
        });
        setStatus(payload.message || 'Channel updated.');
        closeChannelEdit();
        await fetchState({
          runId: state.payload?.run_id,
          topic: state.payload?.subtopic_reviews?.selected_topic || null,
          subtopic: state.payload?.comparison_reviews?.selected_subtopic || null,
        });
      } catch (error) {
        setStatus(error.message || 'Channel edit failed.', true);
      } finally {
        goBtn.disabled = false;
        goBtn.textContent = 'Save';
        if (cancelBtn) cancelBtn.disabled = false;
      }
    }

    function transcriptPill(status) {
      if (status === 'available') {
        return '<span class="pill pill-good"><span class="pill-dot"></span>transcript ready</span>';
      }
      if (status === 'disabled' || status === 'unavailable' || status === 'not_found') {
        return '<span class="pill pill-bad"><span class="pill-dot"></span>no transcript</span>';
      }
      if (!status) {
        return '<span class="pill pill-neutral"><span class="pill-dot"></span>not fetched</span>';
      }
      return '<span class="pill pill-neutral"><span class="pill-dot"></span>' + escapeHtml(status) + '</span>';
    }

    function transcriptHint(video) {
      if (video.transcript_status === 'available' && video.transcript_fetched_at) {
        return 'fetched ' + escapeHtml(formatDate(video.transcript_fetched_at));
      }
      if (video.transcript_status === 'disabled') return 'captions disabled';
      if (video.transcript_status === 'not_found') return 'no captions found';
      if (!video.transcript_status) return 'not yet attempted';
      return escapeHtml(video.transcript_status);
    }

    function renderSupply(payload) {
      if (!payload) return;
      renderChannelHeader(payload.supply_channel);
      const list = document.getElementById('supply-video-list');
      const footer = document.getElementById('supply-video-footer');
      if (!list || !footer) return;

      const videos = (payload.supply_videos || []).slice();
      if (state.supplySort === 'oldest') videos.reverse();

      if (!videos.length) {
        list.innerHTML = '<p class="muted" style="padding:24px 0;">No videos yet — re-ingest from the CLI to fetch.</p>';
        footer.innerHTML = '';
        return;
      }

      list.innerHTML = videos.map((v) => {
        const thumb = v.thumbnail_url
          ? `style="background-image:url('${escapeHtml(v.thumbnail_url)}');"`
          : '';
        const durationStr = formatDuration(v.duration_seconds);
        const meta = [
          formatDate(v.published_at),
          ...(durationStr ? [durationStr] : []),
          'YT-' + escapeHtml(v.youtube_video_id || ''),
        ];
        const metaHtml = meta.map((m, i) =>
          (i ? '<span class="sep">·</span>' : '') + '<span>' + escapeHtml(m) + '</span>'
        ).join('');
        const watchUrl = v.youtube_video_id
          ? `https://www.youtube.com/watch?v=${encodeURIComponent(v.youtube_video_id)}`
          : null;
        const titleHtml = watchUrl
          ? `<a href="${escapeHtml(watchUrl)}" target="_blank" rel="noopener" class="sv-title-link">${escapeHtml(v.title)}</a>`
          : escapeHtml(v.title);
        const watchBtn = watchUrl
          ? `<a class="primary-action" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;padding:4px 10px;font-size:12px;border-radius:8px;" href="${escapeHtml(watchUrl)}" target="_blank" rel="noopener">▶ Watch</a>`
          : '';
        return `
          <div class="supply-row">
            <div class="sv-thumb" ${thumb}></div>
            <div>
              <p class="sv-title">${titleHtml}</p>
              <div class="sv-meta">${metaHtml}</div>
            </div>
            <div class="sv-actions">
              ${watchBtn}
              ${transcriptPill(v.transcript_status)}
              <span class="sv-hint">${transcriptHint(v)}</span>
            </div>
          </div>
        `;
      }).join('');

      const totalShown = videos.length;
      const totalAll = payload.channel_overview?.video_count ?? totalShown;
      const maxLimit = payload.supply_max_limit ?? 500;
      const reachedCap = totalShown >= maxLimit;
      const hasMore = totalAll > totalShown && !reachedCap;
      let footerHtml = totalAll > totalShown
        ? `<span>Showing ${totalShown} of ${totalAll} videos</span>`
        : `<span>Showing all ${totalShown} ${totalShown === 1 ? 'video' : 'videos'}</span>`;
      if (hasMore) {
        footerHtml += `<button type="button" class="supply-load-more" id="supply-load-more">Load more</button>`;
      } else if (reachedCap && totalAll > totalShown) {
        footerHtml += `<span class="sep">cap of ${maxLimit} reached</span>`;
      }
      footer.innerHTML = footerHtml;
      const loadMoreBtn = document.getElementById('supply-load-more');
      if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', () => loadMoreSupply());
      }
    }

    async function loadMoreSupply() {
      const payload = state.payload;
      if (!payload) return;
      const totalAll = payload.channel_overview?.video_count ?? 0;
      const maxLimit = payload.supply_max_limit ?? 500;
      const currentLimit = payload.supply_limit ?? state.supplyLimit ?? 50;
      const next = Math.min(currentLimit + 50, totalAll || (currentLimit + 50), maxLimit);
      if (next <= currentLimit) return;
      state.supplyLimit = next;
      const btn = document.getElementById('supply-load-more');
      if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
      try {
        await fetchState();
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function renderDiscoverRunPanel(payload) {
      const host = document.getElementById('discover-run-panel');
      if (!host) return;
      const overview = payload.channel_overview || {};
      const latest = overview.latest_discovery;
      const videoCount = overview.video_count ?? 0;
      const model = latest?.model || 'claude-haiku-4-5-20251001';
      const promptVersion = latest?.prompt_version || '—';
      const warnPill = videoCount > 0 && !latest
        ? `<span class="pill-warn">${videoCount} videos · no discovery run yet</span>`
        : '';
      const realOn = state.discoverMode === 'real';
      host.innerHTML = `
        <div>
          <div class="discover-run-headline">
            <h3>Run discovery</h3>
            ${warnPill}
          </div>
          <div class="discover-run-meta">
            <span>Model <strong>${escapeHtml(model)}</strong></span>
            <span>Prompt <strong>${escapeHtml(promptVersion)}</strong></span>
            <span>Estimate <strong>$0.019 ± 0.005</strong></span>
            <span>~17s</span>
          </div>
          <div class="discover-mode-toggle" role="tablist" aria-label="Discovery mode">
            <button type="button" class="opt ${realOn ? 'on' : ''}" data-mode="real" role="tab" aria-selected="${realOn}">--real</button>
            <button type="button" class="opt ${realOn ? '' : 'on'}" data-mode="stub" role="tab" aria-selected="${!realOn}">--stub</button>
          </div>
        </div>
        <button class="discover-run-action" id="discover-run-btn">Run discovery →</button>
      `;
      host.querySelectorAll('.discover-mode-toggle .opt').forEach((btn) => {
        btn.addEventListener('click', () => setDiscoverMode(btn.getAttribute('data-mode')));
      });
      const runBtn = document.getElementById('discover-run-btn');
      if (runBtn) runBtn.addEventListener('click', () => openDiscoverConfirm());
    }

    function setDiscoverMode(mode) {
      state.discoverMode = (mode === 'stub') ? 'stub' : 'real';
      renderDiscoverRunPanel(state.payload || {});
    }

    function openDiscoverConfirm() {
      const modal = document.getElementById('discover-confirm-modal');
      const titleEl = document.getElementById('discover-confirm-title');
      const bodyEl = document.getElementById('discover-confirm-body');
      const metaEl = document.getElementById('discover-confirm-meta');
      const goBtn = document.getElementById('discover-confirm-go');
      if (!modal || !titleEl || !bodyEl || !metaEl || !goBtn) return;
      const real = state.discoverMode === 'real';
      titleEl.textContent = real ? 'Run discovery (--real)' : 'Run discovery (--stub)';
      bodyEl.textContent = real
        ? 'This will call the Anthropic API and bill tokens against your account. Proceed?'
        : 'Run a free, deterministic stub discovery. No tokens are spent — useful for wiring sanity checks.';
      metaEl.textContent = real
        ? 'Estimate: ~$0.019 · ~17s · requires RALPH_ALLOW_REAL_LLM=1 server-side.'
        : 'Stub assignments only — does not reflect channel content.';
      goBtn.textContent = real ? 'Run --real' : 'Run --stub';
      goBtn.disabled = false;
      modal.hidden = false;
      modal.setAttribute('data-open', 'true');
    }

    function closeDiscoverConfirm() {
      const modal = document.getElementById('discover-confirm-modal');
      if (!modal) return;
      modal.hidden = true;
      modal.removeAttribute('data-open');
    }

    async function pollDiscoveryRunStatus(runId) {
      // Poll /api/discovery_runs/<id> until status is terminal ('success' or
      // 'error') or we hit the safety cap. Returns the final status payload
      // or throws on timeout. Cap at ~120s — a healthy DOAC run is ~17s, but
      // larger channels could be slower; we'd rather surface a "still running"
      // hint than auto-cancel.
      const intervalMs = 1500;
      const capMs = 120000;
      const start = Date.now();
      while (true) {
        const res = await fetch(`/api/discovery_runs/${runId}`);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || `status check failed (HTTP ${res.status})`);
        }
        const status = await res.json();
        if (status.status === 'success' || status.status === 'error') {
          return status;
        }
        if (Date.now() - start > capMs) {
          throw new Error(`discovery still running after ${capMs / 1000}s — check the server log`);
        }
        await new Promise((r) => setTimeout(r, intervalMs));
      }
    }

    async function runDiscoverFromModal() {
      const goBtn = document.getElementById('discover-confirm-go');
      const cancelBtn = document.getElementById('discover-confirm-cancel');
      const runBtn = document.getElementById('discover-run-btn');
      const mode = state.discoverMode === 'stub' ? 'stub' : 'real';
      if (goBtn) { goBtn.disabled = true; goBtn.textContent = 'Running…'; }
      if (cancelBtn) cancelBtn.disabled = true;
      if (runBtn) { runBtn.disabled = true; runBtn.textContent = 'Running…'; }
      setStatus(`Running ${mode} discovery — this may take a few seconds…`);
      try {
        const startResp = await postJson('/api/discover', { mode });
        const runId = startResp.run_id;
        setStatus(`Discovery run ${runId} started — polling for completion…`);
        const finalStatus = await pollDiscoveryRunStatus(runId);
        if (finalStatus.status === 'error') {
          throw new Error(finalStatus.error_message || `Discovery run ${runId} errored.`);
        }
        setStatus(`Discovery run ${runId} complete.`);
        state.activeDiscoveryRunId = runId;
        state.refine.loaded = false;
        state.activeStage = 'review';
        state.focusedTopic = null;
        state.activeSubtopic = null;
        await fetchState();
        closeDiscoverConfirm();
      } catch (error) {
        setStatus(error.message || 'Discovery failed.', true);
        // Leave the modal open on error so the user can read the message
        // and click Cancel; reset the Run button label so they can retry.
        if (goBtn) { goBtn.disabled = false; goBtn.textContent = state.discoverMode === 'real' ? 'Run --real' : 'Run --stub'; }
      } finally {
        if (cancelBtn) cancelBtn.disabled = false;
        if (runBtn) { runBtn.disabled = false; runBtn.textContent = 'Run discovery →'; }
      }
    }

    function renderDiscoverHistory(payload) {
      const list = document.getElementById('discover-run-list');
      const summary = document.getElementById('discover-history-summary');
      if (!list || !summary) return;
      const runs = payload.discover_runs || [];
      if (!runs.length) {
        list.innerHTML = '<p class="muted" style="padding:24px 0;">No discovery runs yet — run one from the CLI.</p>';
        summary.textContent = '';
        return;
      }
      const successCount = runs.filter((r) => r.status === 'success').length;
      const errorCount = runs.filter((r) => r.status === 'error').length;
      summary.textContent = `${successCount} successful${errorCount ? ' · ' + errorCount + ' errored' : ''}`;
      const formatCost = (c) => {
        if (c == null) return { text: '—', empty: true };
        if (c < 0.001) return { text: '<$0.001', empty: false };
        return { text: '$' + c.toFixed(c < 0.01 ? 4 : 3), empty: false };
      };
      const loadedRunId = payload.discovery_topic_map?.run_id ?? null;
      list.innerHTML = runs.map((r) => {
        const statusPill = r.status === 'success'
          ? '<span class="pill pill-good"><span class="pill-dot"></span>success</span>'
          : '<span class="pill pill-bad"><span class="pill-dot"></span>errored</span>';
        const errLine = r.error_message
          ? `<div class="dr-error">${escapeHtml(r.error_message).slice(0, 120)}</div>`
          : '';
        const cost = formatCost(r.cost_estimate_usd);
        const costClass = cost.empty ? 'dr-cost dr-cost-empty' : 'dr-cost';
        const isActive = loadedRunId != null && Number(loadedRunId) === Number(r.id);
        const rowClass = isActive ? 'discover-run-row is-active' : 'discover-run-row';
        const disabled = r.status !== 'success';
        return `
          <div class="${rowClass}" data-discovery-run-id="${r.id}" data-disabled="${disabled ? '1' : '0'}" role="button" tabindex="0" aria-current="${isActive ? 'true' : 'false'}">
            <span class="dr-num">#${r.id}</span>
            <div>
              <div><span class="dr-model">${escapeHtml(r.model)}</span> <span class="dr-prompt">· prompt ${escapeHtml(r.prompt_version)}</span></div>
              ${errLine}
            </div>
            <span class="dr-when">${escapeHtml(formatDateTime(r.created_at))}</span>
            <span class="dr-status">${statusPill}</span>
            <span class="dr-counts">${r.topic_count} topics · ${r.episode_count} eps</span>
            <span class="${costClass}">${cost.text}</span>
            <span class="dr-chevron">›</span>
          </div>
        `;
      }).join('');
      list.querySelectorAll('.discover-run-row').forEach((row) => {
        const idAttr = row.getAttribute('data-discovery-run-id');
        const disabled = row.getAttribute('data-disabled') === '1';
        if (!idAttr) return;
        const handler = () => {
          if (disabled) {
            setStatus(`Run #${idAttr} errored — no topic map to review.`, true);
            return;
          }
          selectDiscoveryRun(Number(idAttr));
        };
        row.addEventListener('click', handler);
        row.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            handler();
          }
        });
      });
    }

    async function selectDiscoveryRun(runId) {
      state.activeDiscoveryRunId = runId;
      state.refine.loaded = false;
      state.focusedTopic = null;
      state.activeSubtopic = null;
      setActiveStage('review');
      try {
        await fetchState();
      } catch (err) {
        setStatus(err.message || 'Failed to load discovery run', true);
      }
    }

    function renderDiscover(payload) {
      if (!payload) return;
      renderDiscoverRunPanel(payload);
      renderDiscoverHistory(payload);
    }

    // ---------- Refine stage ----------
    function parseYoutubeId(raw) {
      const s = String(raw || '').trim();
      const m = s.match(/(?:v=|youtu\.be\/|\/shorts\/|\/embed\/)([A-Za-z0-9_-]{11})/);
      if (m) return m[1];
      if (/^[A-Za-z0-9_-]{11}$/.test(s)) return s;
      return null;
    }

    async function loadRefineSample() {
      state.refine.loading = true;
      state.refine.error = null;
      renderRefine();
      try {
        const qs = state.activeDiscoveryRunId != null ? `?discovery_run_id=${encodeURIComponent(state.activeDiscoveryRunId)}` : '';
        const res = await fetch('/api/refine/sample' + qs);
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || 'Failed to load sample');
        state.refine.discoveryRunId = payload.discovery_run_id;
        state.refine.poolSize = payload.pool_size;
        state.refine.episodes = (payload.episodes || []).map((e) => ({
          youtube_video_id: e.youtube_video_id,
          title: e.title || '(untitled)',
          topic: e.topic || null,
          confidence: e.confidence,
          transcript_status: e.transcript_status || null,
          slot_kind: e.slot_kind || 'coverage',
          available: e.transcript_status === 'available',
        }));
        state.refine.estimate = null;
        state.refine.note = null;
        state.refine.runResult = null;
        state.refine.loaded = true;
      } catch (error) {
        state.refine.error = error.message || 'Failed to load sample';
      } finally {
        state.refine.loading = false;
        renderRefine();
      }
    }

    function refineRemoveEpisode(ytId) {
      state.refine.episodes = state.refine.episodes.filter((e) => e.youtube_video_id !== ytId);
      state.refine.estimate = null;
      renderRefine();
    }

    function refineAddEpisode() {
      const input = document.getElementById('refine-add-input');
      if (!input) return;
      const ytId = parseYoutubeId(input.value);
      if (!ytId) { setStatus('Could not parse a YouTube video ID from that.', true); return; }
      if (state.refine.episodes.some((e) => e.youtube_video_id === ytId)) {
        setStatus('Already in the sample.', true);
        return;
      }
      state.refine.episodes.push({
        youtube_video_id: ytId, title: '(added — fetch to confirm)', topic: null,
        confidence: null, transcript_status: null, slot_kind: 'added', available: false,
      });
      input.value = '';
      state.refine.estimate = null;
      renderRefine();
    }

    async function refineFetchAndEstimate() {
      const ids = state.refine.episodes.map((e) => e.youtube_video_id);
      if (!ids.length) { setStatus('Sample is empty.', true); return; }
      setStatus(`Fetching ${ids.length} transcript(s)…`);
      state.refine.running = true; renderRefine();
      try {
        const resp = await postJson('/api/refine/fetch-transcripts', { video_ids: ids });
        const byId = new Map((resp.episodes || []).map((e) => [e.youtube_video_id, e]));
        const kept = [], dropped = [];
        state.refine.episodes.forEach((e) => {
          const upd = byId.get(e.youtube_video_id);
          if (upd) { e.transcript_status = upd.transcript_status; e.available = !!upd.available; }
          if (e.available) kept.push(e); else dropped.push(e.youtube_video_id);
        });
        state.refine.episodes = kept;
        state.refine.estimate = { n_available: resp.n_available, estimated_cost_usd: resp.estimated_cost_usd };
        state.refine.note = dropped.length
          ? { text: `Dropped ${dropped.length} episode(s) with no available transcript: ${dropped.join(', ')}`, warn: true }
          : { text: `${resp.n_available} transcript(s) available.`, warn: false };
        setStatus(`${resp.n_available} transcript(s) available · est. $${Number(resp.estimated_cost_usd || 0).toFixed(4)}`);
      } catch (error) {
        setStatus(error.message || 'Fetch failed.', true);
      } finally {
        state.refine.running = false; renderRefine();
      }
    }

    async function pollRefineStatus(runId) {
      const intervalMs = 1500, capMs = 600000, start = Date.now();
      while (true) {
        const res = await fetch(`/api/refine/status/${runId}`);
        const status = await res.json();
        if (!res.ok) throw new Error(status.error || `status check failed (HTTP ${res.status})`);
        if (status.status === 'success' || status.status === 'error') return status;
        if (Date.now() - start > capMs) throw new Error(`refinement still running after ${capMs / 1000}s — check the server log`);
        await new Promise((r) => setTimeout(r, intervalMs));
      }
    }

    async function refineRun() {
      const ids = state.refine.episodes.map((e) => e.youtube_video_id);
      if (!ids.length) { setStatus('Sample is empty.', true); return; }
      const mode = state.refine.mode === 'stub' ? 'stub' : 'real';
      if (mode === 'real' && state.refine.estimate
          && !window.confirm(`Run --real refinement over ${ids.length} episode(s)? Estimated cost $${Number(state.refine.estimate.estimated_cost_usd || 0).toFixed(4)}. This bills the Anthropic API.`)) {
        return;
      }
      state.refine.running = true; state.refine.runResult = null; renderRefine();
      setStatus(`Starting ${mode} refinement run over ${ids.length} episode(s)…`);
      try {
        const startResp = await postJson('/api/refine', { mode, video_ids: ids, discovery_run_id: state.refine.discoveryRunId });
        const runId = startResp.refinement_run_id;
        setStatus(`Refinement run ${runId} started — polling…`);
        const finalStatus = await pollRefineStatus(runId);
        state.refine.runResult = {
          refinement_run_id: runId,
          status: finalStatus.status,
          n_proposals: finalStatus.n_proposals,
          error: finalStatus.error || null,
        };
        if (finalStatus.status === 'error') setStatus(finalStatus.error || `Refinement run ${runId} errored.`, true);
        else setStatus(`Refinement run ${runId} complete — ${finalStatus.n_proposals} proposal(s).`);
      } catch (error) {
        setStatus(error.message || 'Refinement failed.', true);
      } finally {
        state.refine.running = false; renderRefine();
      }
    }

    function setRefineMode(mode) {
      state.refine.mode = mode === 'stub' ? 'stub' : 'real';
      renderRefine();
    }

    function refineStatusHtml(ep) {
      const st = ep.transcript_status;
      if (st === 'available') return '<span class="refine-tstatus available">transcript ready</span>';
      if (!st) return '<span class="refine-tstatus missing">not fetched</span>';
      return `<span class="refine-tstatus missing">${escapeHtml(st)}</span>`;
    }

    function renderRefine() {
      const host = document.getElementById('refine-setup');
      if (!host) return;
      const r = state.refine;
      const extrasHtml = renderRefineProposals() + renderRefineReview();
      if (r.loading) { host.innerHTML = '<p class="refine-empty">Loading the auto-picked sample…</p>' + extrasHtml; return; }
      if (r.error) {
        const hint = /discover/i.test(r.error)
          ? ' <button class="secondary" onclick="setActiveStage(\\'discover\\')">Go to Discover →</button>'
          : '';
        host.innerHTML = `<p class="refine-note warn">${escapeHtml(r.error)}</p><p>${hint}</p>` + extrasHtml;
        return;
      }
      if (!r.loaded) { host.innerHTML = '<p class="refine-empty">Open this stage to load the sample.</p>' + extrasHtml; return; }
      const eps = r.episodes;
      const realOn = r.mode === 'real';
      const estLabel = r.estimate ? `$${Number(r.estimate.estimated_cost_usd || 0).toFixed(4)}` : '—';
      const canRun = !!r.estimate && r.estimate.n_available > 0 && !r.running;
      const rows = eps.length ? eps.map((ep) => `
        <tr>
          <td>${escapeHtml(ep.title)}<br><span class="mono small soft">${escapeHtml(ep.youtube_video_id)}</span></td>
          <td>${ep.topic ? escapeHtml(ep.topic) : '<span class="soft">— blind spot</span>'}</td>
          <td class="mono">${ep.confidence == null ? '—' : Number(ep.confidence).toFixed(2)}</td>
          <td>${refineStatusHtml(ep)}</td>
          <td><span class="refine-slot ${escapeHtml(ep.slot_kind)}">${escapeHtml(String(ep.slot_kind || '').replace('_', ' '))}</span></td>
          <td><button class="refine-row-rm" title="Remove" onclick="refineRemoveEpisode('${escapeHtml(ep.youtube_video_id)}')">✕</button></td>
        </tr>`).join('') : '<tr><td colspan="6" class="refine-empty">Sample is empty — add episodes by ID below.</td></tr>';
      const noteHtml = r.note ? `<p class="refine-note ${r.note.warn ? 'warn' : ''}">${escapeHtml(r.note.text)}</p>` : '';
      const runResultHtml = r.runResult ? (
        r.runResult.status === 'error'
          ? `<p class="refine-note warn">Run #${r.runResult.refinement_run_id} errored: ${escapeHtml(r.runResult.error || '')}</p>`
          : `<p class="refine-note">Run #${r.runResult.refinement_run_id} complete — ${escapeHtml(r.runResult.n_proposals)} taxonomy proposal(s). Review them below.</p>`
      ) : '';
      host.innerHTML = `
        <div class="refine-meta">
          <span>Discovery run <strong>#${escapeHtml(r.discoveryRunId)}</strong></span>
          <span>Candidate pool <strong>${escapeHtml(r.poolSize)}</strong></span>
          <span>Sample <strong>${eps.length}</strong> episode(s)</span>
          <span>Estimate <strong>${estLabel}</strong></span>
        </div>
        <table class="refine-sample-table">
          <thead><tr><th>Episode</th><th>Covers topic</th><th>Conf.</th><th>Transcript</th><th>Slot</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${noteHtml}
        <div class="refine-actions">
          <input type="text" id="refine-add-input" placeholder="add by video ID or URL" />
          <button class="secondary" onclick="refineAddEpisode()">Add</button>
          <button class="secondary" onclick="refineFetchAndEstimate()" ${r.running ? 'disabled' : ''}>Fetch transcripts &amp; estimate</button>
          <span class="div"></span>
          <div class="discover-mode-toggle" role="tablist" aria-label="Refinement mode">
            <button type="button" class="opt ${realOn ? 'on' : ''}" data-mode="real" role="tab" aria-selected="${realOn}" onclick="setRefineMode('real')">--real</button>
            <button type="button" class="opt ${realOn ? '' : 'on'}" data-mode="stub" role="tab" aria-selected="${!realOn}" onclick="setRefineMode('stub')">--stub</button>
          </div>
          <button class="discover-run-action" onclick="refineRun()" ${canRun ? '' : 'disabled'}>Run refinement (${estLabel})</button>
        </div>
        ${runResultHtml}
        ${extrasHtml}
      `;
    }

    function renderRefineProposals() {
      const proposals = (state.payload && state.payload.refine_proposals) || [];
      const accepted = state.refine.acceptedThisSession || 0;
      if (!proposals.length && !accepted) return '';
      const byRun = new Map();
      proposals.forEach((p) => {
        if (!byRun.has(p.refinement_run_id)) byRun.set(p.refinement_run_id, []);
        byRun.get(p.refinement_run_id).push(p);
      });
      const runIds = Array.from(byRun.keys()).sort((a, b) => b - a);
      const groupsHtml = runIds.map((rid) => {
        const items = byRun.get(rid);
        const subs = items.filter((p) => p.kind === 'subtopic');
        const tops = items.filter((p) => p.kind !== 'subtopic');
        const cards = subs.concat(tops).map(renderProposalCard).join('');
        return `<div class="refine-prop-run"><h3>Refinement run #${escapeHtml(rid)} · ${items.length} proposal(s)</h3>${cards}</div>`;
      }).join('');
      const bodyHtml = proposals.length ? groupsHtml : '<p class="refine-empty">No pending proposals — all reviewed.</p>';
      const nudgeHtml = accepted
        ? `<div class="refine-nudge">Accepted ${accepted} change(s) this session. <button class="secondary" onclick="setActiveStage(\\'discover\\')">Re-run Discover →</button> to spread them across the channel.</div>`
        : '';
      return `<div class="refine-proposals"><h2>Taxonomy proposals</h2><p class="muted small">Accept to create the real topic/subtopic node (parent resolved through renames). Then re-run Discover to spread it channel-wide.</p>${bodyHtml}${nudgeHtml}</div>`;
    }

    function renderProposalCard(p) {
      const watchUrl = p.source_youtube_video_id
        ? `https://www.youtube.com/watch?v=${encodeURIComponent(p.source_youtube_video_id)}`
        : null;
      const srcHtml = watchUrl
        ? `<a href="${escapeHtml(watchUrl)}" target="_blank" rel="noopener">${escapeHtml(p.source_title || p.source_youtube_video_id)}</a>`
        : '<span class="soft">(source episode unknown)</span>';
      const parentHtml = p.kind === 'subtopic'
        ? ` <span class="soft">under</span> <strong>${escapeHtml(p.parent_topic_name || '?')}</strong>`
        : '';
      const evHtml = p.evidence ? `<div class="refine-prop-ev">${escapeHtml(p.evidence)}</div>` : '';
      return `<div class="refine-prop-card">
        <div class="refine-prop-head"><span class="refine-prop-kind ${escapeHtml(p.kind)}">${escapeHtml(p.kind)}</span><strong>${escapeHtml(p.name)}</strong>${parentHtml}</div>
        ${evHtml}
        <div class="refine-prop-src">from ${srcHtml}</div>
        <div class="refine-prop-actions">
          <button class="discover-run-action" onclick="acceptProposal(${p.proposal_id})">Accept</button>
          <button class="secondary" onclick="rejectProposal(${p.proposal_id})">Reject</button>
        </div>
      </div>`;
    }

    async function acceptProposal(proposalId) {
      try {
        const res = await postJson('/api/refine/proposal/accept', { proposal_id: proposalId });
        if (res.result && res.result.status === 'rejected') {
          setStatus(res.message || `Proposal ${proposalId} could not be accepted — marked rejected.`, true);
        } else {
          state.refine.acceptedThisSession = (state.refine.acceptedThisSession || 0) + 1;
          setStatus(res.message || `Accepted proposal ${proposalId}.`);
        }
        await fetchState();
      } catch (error) {
        setStatus(error.message || 'Accept failed.', true);
      }
    }

    async function rejectProposal(proposalId) {
      try {
        const res = await postJson('/api/refine/proposal/reject', { proposal_id: proposalId });
        setStatus(res.message || `Rejected proposal ${proposalId}.`);
        await fetchState();
      } catch (error) {
        setStatus(error.message || 'Reject failed.', true);
      }
    }

    function renderRefineReview() {
      const runs = (state.payload && state.payload.refine_review) || [];
      if (!runs.length) return '';
      const groups = runs.map((run) => {
        const eps = (run.episodes || []).map(renderRefineReviewEpisode).join('');
        return `<div class="refine-prop-run"><h3>Refinement run #${escapeHtml(run.refinement_run_id)} · ${(run.episodes || []).length} episode(s) sampled</h3>${eps || '<p class="refine-empty">No episodes recorded.</p>'}</div>`;
      }).join('');
      return `<div class="refine-proposals refine-review"><h2>Before → after (sampled episodes)</h2><p class="muted small">Transcript-grade reassignments per sampled episode. Mark one wrong if the transcript re-judgement still missed.</p>${groups}</div>`;
    }

    function renderRefineReviewEpisode(ep) {
      const before = ep.before || [];
      const after = ep.after || [];
      const beforeByTopic = new Map(before.map((a) => [a.topic, a]));
      const afterByTopic = new Map(after.map((a) => [a.topic, a]));
      const added = after.filter((a) => !beforeByTopic.has(a.topic));
      const dropped = before.filter((a) => !afterByTopic.has(a.topic));
      const corrected = after.filter((a) => beforeByTopic.has(a.topic) && (beforeByTopic.get(a.topic).subtopic || '') !== (a.subtopic || ''));
      const watchUrl = ep.youtube_video_id ? `https://www.youtube.com/watch?v=${encodeURIComponent(ep.youtube_video_id)}` : null;
      const titleHtml = watchUrl
        ? `<a href="${escapeHtml(watchUrl)}" target="_blank" rel="noopener">${escapeHtml(ep.title || ep.youtube_video_id)}</a>`
        : escapeHtml(ep.title || '(untitled)');
      const chips = [];
      added.forEach((a) => chips.push(`<span class="refine-diff-add">+ ${escapeHtml(a.topic)}${a.subtopic ? ' / ' + escapeHtml(a.subtopic) : ''}</span>`));
      dropped.forEach((a) => chips.push(`<span class="refine-diff-drop">− ${escapeHtml(a.topic)}${a.subtopic ? ' / ' + escapeHtml(a.subtopic) : ''}</span>`));
      corrected.forEach((a) => chips.push(`<span class="refine-diff-fix">${escapeHtml(a.topic)}: <s>${escapeHtml(beforeByTopic.get(a.topic).subtopic || '—')}</s> → ${escapeHtml(a.subtopic || '—')}</span>`));
      const diffHtml = chips.length ? `<div class="refine-diff-chips">${chips.join('')}</div>` : '<div class="refine-diff-chips muted small">No change vs. the metadata pass.</div>';
      const afterRows = after.length ? after.map((a) => {
        const conf = (a.confidence == null) ? '' : ` <span class="mono soft">${Number(a.confidence).toFixed(2)}</span>`;
        const src = a.assignment_source === 'manual' ? ' <span class="mono soft">(manual)</span>' : '';
        const wrongBtn = `<button class="discovery-episode-wrong" type="button" title="Mark this topic wrong for this episode" onclick='markEpisodeWrong(${JSON.stringify(a.topic)}, ${JSON.stringify(ep.youtube_video_id || '')}, null)'>✗ wrong</button>`;
        return `<li>${escapeHtml(a.topic)}${a.subtopic ? ' / ' + escapeHtml(a.subtopic) : ''}${conf}${src} ${wrongBtn}</li>`;
      }).join('') : '<li class="muted small">No assignments after the run.</li>';
      return `<div class="refine-prop-card">
        <div class="refine-prop-head">📄 ${titleHtml} <span class="mono soft">${escapeHtml(ep.youtube_video_id || '')}</span></div>
        ${diffHtml}
        <ul class="refine-after-list">${afterRows}</ul>
      </div>`;
    }

    function renderConsume(payload) {
      const host = document.getElementById('consume-topic-filters');
      if (!host) return;
      const map = payload?.discovery_topic_map;
      const topics = (map?.topics || []).slice(0, 8);
      if (!topics.length) {
        host.innerHTML = '<div class="consume-filter-empty">No topics yet — run discovery first.</div>';
        return;
      }
      host.innerHTML = topics.map((t) => `
        <label>
          <span class="name"><span class="swatch"></span>${escapeHtml(t.name)}</span>
          <span class="count">${t.episode_count ?? 0}</span>
        </label>
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
      renderSupply(payload);
      renderDiscover(payload);
      renderRefine();
      renderConsume(payload);
      renderStepper();
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
    document.getElementById('overview-sort-eps').addEventListener('click', () => setOverviewSort('episodes'));
    document.getElementById('overview-sort-az').addEventListener('click', () => setOverviewSort('az'));
    document.querySelectorAll('.stepper .step').forEach((btn) => {
      btn.addEventListener('click', () => {
        const stage = btn.getAttribute('data-stage');
        if (stage) setActiveStage(stage);
      });
    });
    document.getElementById('supply-sort-newest').addEventListener('click', () => setSupplySort('newest'));
    document.getElementById('supply-sort-oldest').addEventListener('click', () => setSupplySort('oldest'));
    setActiveStage(state.activeStage);
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

    document.getElementById('discover-confirm-cancel').addEventListener('click', () => closeDiscoverConfirm());
    document.getElementById('discover-confirm-go').addEventListener('click', () => runDiscoverFromModal());
    document.getElementById('discover-confirm-modal').addEventListener('click', (event) => {
      if (event.target.id === 'discover-confirm-modal') closeDiscoverConfirm();
    });
    document.getElementById('channel-edit-cancel').addEventListener('click', () => closeChannelEdit());
    document.getElementById('channel-edit-go').addEventListener('click', () => submitChannelEdit());
    document.getElementById('channel-edit-modal').addEventListener('click', (event) => {
      if (event.target.id === 'channel-edit-modal') closeChannelEdit();
    });
    document.getElementById('channel-edit-form').addEventListener('submit', (event) => {
      event.preventDefault();
      submitChannelEdit();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const discoverModal = document.getElementById('discover-confirm-modal');
      if (discoverModal && discoverModal.getAttribute('data-open') === 'true') closeDiscoverConfirm();
      const editModal = document.getElementById('channel-edit-modal');
      if (editModal && editModal.getAttribute('data-open') === 'true') closeChannelEdit();
    });

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
                videos.published_at,
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
            SELECT DISTINCT videos.youtube_video_id, videos.title, videos.published_at
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
            {
                "youtube_video_id": row["youtube_video_id"],
                "title": row["title"],
                "published_at": row["published_at"],
            }
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
            {
                "youtube_video_id": row["youtube_video_id"],
                "title": row["title"],
                "published_at": row["published_at"],
            }
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


def _build_supply_channel(
    db_path: Path, channel_id: int
) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT youtube_channel_id, title, handle, description,
                   thumbnail_url, last_refreshed_at, created_at
            FROM channels
            WHERE id = ?
            """,
            (channel_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "youtube_channel_id": row["youtube_channel_id"],
        "title": row["title"],
        "handle": row["handle"],
        "description": row["description"],
        "thumbnail_url": row["thumbnail_url"],
        "last_refreshed_at": row["last_refreshed_at"],
        "created_at": row["created_at"],
    }


def _build_supply_videos(
    db_path: Path, channel_id: int, *, limit: int = SUPPLY_DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT v.id, v.youtube_video_id, v.title, v.published_at,
                   v.thumbnail_url, v.duration_seconds, t.transcript_status, t.fetched_at
            FROM videos v
            LEFT JOIN video_transcripts t ON t.video_id = v.id
            WHERE v.channel_id = ?
            ORDER BY COALESCE(v.published_at, v.created_at) DESC
            LIMIT ?
            """,
            (channel_id, limit),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "youtube_video_id": row["youtube_video_id"],
            "title": row["title"],
            "published_at": row["published_at"],
            "thumbnail_url": row["thumbnail_url"],
            "duration_seconds": (
                int(row["duration_seconds"])
                if row["duration_seconds"] is not None
                else None
            ),
            "transcript_status": row["transcript_status"],
            "transcript_fetched_at": row["fetched_at"],
        }
        for row in rows
    ]


def _build_discover_runs(
    db_path: Path, channel_id: int, *, limit: int = 25
) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, model, prompt_version, status, error_message, created_at
            FROM discovery_runs
            WHERE channel_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (channel_id, limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            run_id = int(row["id"])
            counts = connection.execute(
                """
                SELECT COUNT(DISTINCT topic_id) AS topic_count,
                       COUNT(DISTINCT video_id) AS episode_count
                FROM video_topics
                WHERE discovery_run_id = ?
                """,
                (run_id,),
            ).fetchone()
            cost_row = connection.execute(
                """
                SELECT SUM(cost_estimate_usd) AS total_cost
                FROM llm_calls
                WHERE correlation_id = ?
                """,
                (run_id,),
            ).fetchone()
            total_cost = cost_row["total_cost"] if cost_row else None
            out.append(
                {
                    "id": run_id,
                    "model": row["model"],
                    "prompt_version": row["prompt_version"],
                    "status": row["status"],
                    "error_message": row["error_message"],
                    "created_at": row["created_at"],
                    "topic_count": int(counts["topic_count"] or 0),
                    "episode_count": int(counts["episode_count"] or 0),
                    "cost_estimate_usd": (
                        float(total_cost) if total_cost is not None else None
                    ),
                }
            )
        return out


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


def _build_discovery_topic_map(
    db_path: Path, *, run_id: int | None = None
) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _run_cols = (
            "id, channel_id, model, prompt_version, status, created_at, "
            "n_shorts_excluded, n_orphaned_wrong_marks, n_orphaned_renames"
        )
        if run_id is None:
            run_row = connection.execute(
                f"""
                SELECT {_run_cols}
                FROM discovery_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            run_row = connection.execute(
                f"""
                SELECT {_run_cols}
                FROM discovery_runs
                WHERE id = ?
                """,
                (int(run_id),),
            ).fetchone()
        if run_row is None:
            return None
        new_topic_names = _topics_introduced_in_run(
            connection, int(run_row["channel_id"]), int(run_row["id"])
        )

        # Rows from this discovery run, plus transcript-grade ('refine') rows
        # the operator's Refine stage wrote against any of this run's topics
        # (those carry discovery_run_id NULL — see db.write_refine_assignments).
        run_id_int = run_row["id"]
        topic_rows = connection.execute(
            """
            SELECT topics.id AS topic_id,
                   topics.name AS topic_name,
                   COUNT(DISTINCT video_topics.video_id) AS episode_count,
                   AVG(video_topics.confidence) AS avg_confidence
            FROM video_topics
            JOIN topics ON topics.id = video_topics.topic_id
            WHERE video_topics.discovery_run_id = ?
               OR (video_topics.assignment_source = 'refine'
                   AND video_topics.topic_id IN (
                       SELECT topic_id FROM video_topics WHERE discovery_run_id = ?
                   ))
            GROUP BY topics.id, topics.name
            ORDER BY episode_count DESC, topics.name COLLATE NOCASE
            """,
            (run_id_int, run_id_int),
        ).fetchall()

        episode_rows = connection.execute(
            """
            SELECT video_topics.topic_id AS topic_id,
                   videos.youtube_video_id AS youtube_video_id,
                   videos.title AS title,
                   videos.thumbnail_url AS thumbnail_url,
                   videos.published_at AS published_at,
                   videos.duration_seconds AS duration_seconds,
                   video_topics.confidence AS confidence,
                   video_topics.reason AS reason,
                   video_topics.assignment_source AS assignment_source
            FROM video_topics
            JOIN videos ON videos.id = video_topics.video_id
            WHERE video_topics.discovery_run_id = ?
               OR (video_topics.assignment_source = 'refine'
                   AND video_topics.topic_id IN (
                       SELECT topic_id FROM video_topics WHERE discovery_run_id = ?
                   ))
            ORDER BY video_topics.confidence DESC, videos.title COLLATE NOCASE
            """,
            (run_id_int, run_id_int),
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
               OR (video_subtopics.assignment_source = 'refine'
                   AND video_subtopics.subtopic_id IN (
                       SELECT id FROM subtopics WHERE topic_id IN (
                           SELECT topic_id FROM video_topics WHERE discovery_run_id = ?
                       )
                   ))
            ORDER BY subtopics.name COLLATE NOCASE
            """,
            (run_id_int, run_id_int),
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
                "duration_seconds": (
                    int(row["duration_seconds"])
                    if row["duration_seconds"] is not None
                    else None
                ),
                "confidence": (
                    float(row["confidence"]) if row["confidence"] is not None else None
                ),
                "reason": row["reason"],
                "assignment_source": row["assignment_source"],
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

    # Read-only shorts-filter badge: hidden entirely when this run excluded no
    # Shorts and orphaned no curation actions (no noise on channels with no
    # Shorts). NULL audit values (filter was off) read as 0. Render-side only.
    n_shorts_excluded = run_row["n_shorts_excluded"] or 0
    n_orphaned_wrong_marks = run_row["n_orphaned_wrong_marks"] or 0
    n_orphaned_renames = run_row["n_orphaned_renames"] or 0
    inert_curation_actions = n_orphaned_wrong_marks + n_orphaned_renames
    if n_shorts_excluded == 0 and inert_curation_actions == 0:
        shorts_filter_badge = None
    else:
        shorts_filter_badge = (
            f"{n_shorts_excluded} shorts excluded · "
            f"{inert_curation_actions} curation actions inert "
            "(target episodes filtered)"
        )

    return {
        "run_id": int(run_row["id"]),
        "model": run_row["model"],
        "prompt_version": run_row["prompt_version"],
        "status": run_row["status"],
        "created_at": run_row["created_at"],
        "low_confidence_threshold": _load_low_confidence_threshold(),
        "topics": [_topic_payload(row) for row in topic_rows],
        "new_topic_names": new_topic_names,
        "n_shorts_excluded": run_row["n_shorts_excluded"],
        "n_orphaned_wrong_marks": run_row["n_orphaned_wrong_marks"],
        "n_orphaned_renames": run_row["n_orphaned_renames"],
        "shorts_filter_badge": shorts_filter_badge,
    }


def _build_refine_proposals(db_path: Path, project_id: int) -> list[dict[str, Any]]:
    """All pending ``taxonomy_proposals`` for the project's refinement runs,
    shaped for the Refine-stage proposal-review screen."""
    with connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return list_pending_taxonomy_proposals(connection, project_id)


def _build_refine_review(db_path: Path, project_id: int) -> list[dict[str, Any]]:
    """Per ``success`` refinement run, the sampled episodes' before→after
    assignments — the Refine-stage sanity panel."""
    with connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return list_refinement_episode_changes(connection, project_id)


def build_state_payload(
    db_path: str | Path,
    *,
    run_id: int | None = None,
    topic_name: str | None = None,
    subtopic_name: str | None = None,
    sample_limit: int = 3,
    discovery_run_id: int | None = None,
    supply_limit: int | None = None,
) -> dict[str, Any]:
    effective_supply_limit = SUPPLY_DEFAULT_LIMIT if supply_limit is None else supply_limit
    effective_supply_limit = max(1, min(int(effective_supply_limit), SUPPLY_MAX_LIMIT))
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
    discovery_topic_map = _build_discovery_topic_map(db_path, run_id=discovery_run_id)
    latest_subtopic_run_id_by_topic = _latest_subtopic_run_ids_by_topic(db_path)
    if primary_channel is None:
        channel_overview = None
        supply_channel: dict[str, Any] | None = None
        supply_videos: list[dict[str, Any]] = []
        discover_runs: list[dict[str, Any]] = []
        refine_proposals: list[dict[str, Any]] = []
        refine_review: list[dict[str, Any]] = []
    else:
        channel_overview = _build_channel_overview(
            db_path,
            project_id=primary_channel.project_id,
            channel_id=primary_channel.channel_id,
        )
        refine_proposals = _build_refine_proposals(
            db_path, primary_channel.project_id
        )
        refine_review = _build_refine_review(db_path, primary_channel.project_id)
        supply_channel = _build_supply_channel(
            db_path, channel_id=primary_channel.channel_id
        )
        supply_videos = _build_supply_videos(
            db_path, channel_id=primary_channel.channel_id, limit=effective_supply_limit
        )
        discover_runs = _build_discover_runs(
            db_path, channel_id=primary_channel.channel_id
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
        "supply_channel": supply_channel,
        "supply_videos": supply_videos,
        "supply_limit": effective_supply_limit,
        "supply_max_limit": SUPPLY_MAX_LIMIT,
        "discover_runs": discover_runs,
        "refine_proposals": refine_proposals,
        "refine_review": refine_review,
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


def _discover_mode_config(mode: str) -> tuple[str, str]:
    """Return ``(model, prompt_version)`` for a ``POST /api/discover`` mode.

    Hoisted out of the runner so the request handler can pre-allocate the
    ``discovery_runs`` row (which needs both fields) before spawning the
    background thread that calls the runner.
    """
    from yt_channel_analyzer.discovery import (
        DISCOVERY_PROMPT_VERSION,
        STUB_MODEL,
        STUB_PROMPT_VERSION,
    )

    if mode == "stub":
        return (STUB_MODEL, STUB_PROMPT_VERSION)
    if mode == "real":
        from yt_channel_analyzer.extractor.anthropic_runner import DEFAULT_MODEL

        return (DEFAULT_MODEL, DISCOVERY_PROMPT_VERSION)
    raise ReviewUIError(f"unknown discover mode: {mode!r}")


def _default_discover_runner(
    db_path: Path, *, mode: str, run_id: int
) -> dict[str, Any]:
    """Default runner for ``POST /api/discover``.

    Picks ``stub_llm`` or the real Anthropic-backed callable per ``mode`` and
    drives ``run_discovery`` against a pre-allocated run row. The real path
    opens a sqlite connection that the Extractor uses to log ``llm_calls``
    rows; we close it after the run. The ``RALPH_ALLOW_REAL_LLM=1`` gate is
    enforced inside ``make_real_llm_callable`` and surfaces here as
    ``RuntimeError`` — caller (request handler) catches and stamps the row.
    """
    from yt_channel_analyzer.discovery import (
        run_discovery,
        stub_llm,
    )

    project_name = _resolve_primary_project_name(db_path)
    model, prompt_version = _discover_mode_config(mode)
    if mode == "stub":
        run_discovery(
            db_path,
            project_name=project_name,
            llm=stub_llm,
            model=model,
            prompt_version=prompt_version,
            run_id=run_id,
        )
        return {"run_id": run_id, "model": model, "prompt_version": prompt_version}
    if mode == "real":
        from yt_channel_analyzer.discovery import make_real_llm_callable

        connection = connect(db_path)
        try:
            llm = make_real_llm_callable(connection)
            run_discovery(
                db_path,
                project_name=project_name,
                llm=llm,
                model=model,
                prompt_version=prompt_version,
                run_id=run_id,
            )
        finally:
            connection.close()
        return {"run_id": run_id, "model": model, "prompt_version": prompt_version}
    raise ReviewUIError(f"unknown discover mode: {mode!r}")


def _default_refine_runner(
    db_path: Path,
    *,
    mode: str,
    run_id: int,
    video_ids: list[str] | None,
    discovery_run_id: int | None,
    transcript_fetcher: Any,
) -> None:
    """Default runner for ``POST /api/refine``: drives ``run_refinement``
    against the pre-allocated ``refinement_runs`` row.

    ``stub`` mode uses the deterministic stub LLM (free, offline); ``real``
    mode opens a sqlite connection for the Extractor's ``llm_calls`` audit and
    builds the Anthropic-backed callable — the ``RALPH_ALLOW_REAL_LLM=1`` gate
    is enforced inside ``make_real_refinement_llm_callable``. ``run_refinement``
    already flips the run row to ``error`` on any failure.
    """
    from yt_channel_analyzer import refinement

    project_name = _resolve_primary_project_name(db_path)
    common = dict(
        project_name=project_name,
        discovery_run_id=discovery_run_id,
        sample=video_ids,
        transcript_fetcher=transcript_fetcher,
        run_id=run_id,
        out=lambda *_a, **_k: None,
    )
    if mode == "stub":
        refinement.run_refinement(
            db_path,
            llm=refinement.stub_refinement_llm,
            model=refinement.STUB_MODEL,
            **common,
        )
        return
    if mode == "real":
        connection = connect(db_path)
        try:
            llm = refinement.make_real_refinement_llm_callable(connection)
            refinement.run_refinement(
                db_path,
                llm=llm,
                model=refinement.DEFAULT_REAL_MODEL,
                **common,
            )
        finally:
            connection.close()
        return
    raise ReviewUIError(f"unknown refine mode: {mode!r}")


class ReviewUIApp:
    def __init__(
        self,
        db_path: str | Path,
        *,
        sample_limit: int = 3,
        channel_metadata_fetcher: Any = None,
        channel_videos_fetcher: Any = None,
        discover_runner: Any = None,
        refine_runner: Any = None,
        transcript_fetcher: Any = None,
        transcript_fetch_request_interval: float = 1.0,
        run_in_background: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.sample_limit = sample_limit
        self._channel_metadata_fetcher = channel_metadata_fetcher or _real_fetch_channel_metadata
        self._channel_videos_fetcher = channel_videos_fetcher or _real_fetch_channel_videos
        self._discover_runner = discover_runner or _default_discover_runner
        self._refine_runner = refine_runner or _default_refine_runner
        # Injectable transcript fetcher for the Refine-stage fetch endpoint
        # (None → the real youtube-transcript-api fetcher inside
        # ``run_fetch_transcripts``); tests pass ``stub_transcript_fetcher``.
        self._transcript_fetcher = transcript_fetcher
        self._transcript_fetch_request_interval = transcript_fetch_request_interval
        # ``run_in_background=True`` spawns a daemon thread per /api/discover
        # call so the request returns immediately with the pre-allocated
        # run id; the JS client polls /api/discovery_runs/<id> until terminal.
        # Tests pass ``False`` to keep the call synchronous + deterministic.
        self._run_in_background = run_in_background

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
                discovery_run_id_raw = _normalize_text(
                    (query.get("discovery_run_id") or [None])[0]
                )
                supply_limit_raw = _normalize_text(
                    (query.get("supply_limit") or [None])[0]
                )
                run_id = int(run_id_raw) if run_id_raw is not None else None
                discovery_run_id = (
                    int(discovery_run_id_raw)
                    if discovery_run_id_raw is not None
                    else None
                )
                supply_limit = (
                    int(supply_limit_raw) if supply_limit_raw is not None else None
                )
                payload = build_state_payload(
                    self.db_path,
                    run_id=run_id,
                    topic_name=topic_name,
                    subtopic_name=subtopic_name,
                    sample_limit=self.sample_limit,
                    discovery_run_id=discovery_run_id,
                    supply_limit=supply_limit,
                )
                return self._json_response(start_response, payload)
            if method == "GET" and path.startswith("/api/discovery_runs/"):
                tail = path[len("/api/discovery_runs/"):]
                try:
                    run_id_int = int(tail)
                except ValueError as exc:
                    raise ReviewUIError(
                        f"invalid discovery_run id: {tail!r}"
                    ) from exc
                return self._json_response(
                    start_response, self._discovery_run_status(run_id_int)
                )
            if method == "GET" and path == "/api/refine/sample":
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=False)
                discovery_run_id_raw = _normalize_text(
                    (query.get("discovery_run_id") or [None])[0]
                )
                discovery_run_id = (
                    int(discovery_run_id_raw) if discovery_run_id_raw is not None else None
                )
                return self._json_response(
                    start_response, self._refine_sample(discovery_run_id)
                )
            if method == "GET" and path.startswith("/api/refine/status/"):
                tail = path[len("/api/refine/status/"):]
                try:
                    run_id_int = int(tail)
                except ValueError as exc:
                    raise ReviewUIError(
                        f"invalid refinement_run id: {tail!r}"
                    ) from exc
                return self._json_response(
                    start_response, self._refine_run_status(run_id_int)
                )
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
        if path == "/api/reingest":
            return self._reingest(body)
        if path == "/api/discover":
            return self._discover(body)
        if path == "/api/refine/fetch-transcripts":
            return self._refine_fetch_transcripts(body)
        if path == "/api/refine":
            return self._refine_run(body)
        if path == "/api/refine/proposal/accept":
            return self._refine_proposal_resolve(body, accept=True)
        if path == "/api/refine/proposal/reject":
            return self._refine_proposal_resolve(body, accept=False)
        if path == "/api/channel/edit":
            return self._channel_edit(body)
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

    def _reingest(self, body: dict[str, Any]) -> dict[str, Any]:
        limit_raw = body.get("limit") if isinstance(body, dict) else None
        if limit_raw in (None, ""):
            limit = REINGEST_DEFAULT_LIMIT
        else:
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError) as exc:
                raise ReviewUIError(f"invalid limit: {limit_raw!r}") from exc
            if limit < 1:
                raise ReviewUIError("limit must be >= 1")
            limit = min(limit, REINGEST_DEFAULT_LIMIT)

        primary_channel = get_primary_channel(self.db_path)
        project_name = _resolve_primary_project_name(self.db_path)
        try:
            metadata = self._channel_metadata_fetcher(primary_channel.youtube_channel_id)
            upsert_channel_metadata(
                self.db_path,
                project_name=project_name,
                metadata=metadata,
            )
            videos = self._channel_videos_fetcher(
                primary_channel.youtube_channel_id, limit=limit
            )
            stored_count = upsert_videos_for_primary_channel(self.db_path, videos=videos)
        except YouTubeAPIError as exc:
            raise ReviewUIError(f"Re-ingest failed: {exc}") from exc

        supply = _build_supply_channel(self.db_path, channel_id=primary_channel.channel_id)
        last_refreshed_at = supply["last_refreshed_at"] if supply else None
        channel_title = (supply["title"] if supply else None) or metadata.title
        return {
            "ok": True,
            "channel_title": channel_title,
            "youtube_channel_id": primary_channel.youtube_channel_id,
            "video_count": stored_count,
            "last_refreshed_at": last_refreshed_at,
            "message": f"Re-ingested '{channel_title}': stored {stored_count} video(s).",
        }

    def _channel_edit(self, body: dict[str, Any]) -> dict[str, Any]:
        title = _normalize_text(body.get("title")) if isinstance(body, dict) else None
        if title is None:
            raise ReviewUIError("missing required field: title")
        handle = _normalize_text(body.get("handle")) if isinstance(body, dict) else None
        description = (
            _normalize_text(body.get("description"))
            if isinstance(body, dict)
            else None
        )

        primary_channel = get_primary_channel(self.db_path)
        update_channel_fields(
            self.db_path,
            channel_id=primary_channel.channel_id,
            title=title,
            handle=handle,
            description=description,
        )

        supply = _build_supply_channel(
            self.db_path, channel_id=primary_channel.channel_id
        )
        return {
            "ok": True,
            "channel_title": (supply["title"] if supply else None) or title,
            "youtube_channel_id": primary_channel.youtube_channel_id,
            "handle": supply["handle"] if supply else handle,
            "description": supply["description"] if supply else description,
            "message": f"Updated channel '{title}'.",
        }

    def _discover(self, body: dict[str, Any]) -> dict[str, Any]:
        mode_raw = body.get("mode") if isinstance(body, dict) else None
        mode = _normalize_text(mode_raw)
        if mode is None:
            raise ReviewUIError("missing required field: mode (must be 'stub' or 'real')")
        if mode not in DISCOVER_MODES:
            raise ReviewUIError(
                f"invalid mode: {mode!r} (must be one of {', '.join(DISCOVER_MODES)})"
            )

        # Real-mode env-gate check before allocation, so a missing
        # RALPH_ALLOW_REAL_LLM doesn't leave a stale 'running' row behind.
        # The canonical check still runs in `make_real_llm_callable` inside
        # the runner; this is a UX optimization, not a security boundary.
        if mode == "real" and os.environ.get("RALPH_ALLOW_REAL_LLM") != "1":
            raise ReviewUIError(
                "Real LLM calls are gated behind RALPH_ALLOW_REAL_LLM=1. "
                "Set it before retrying."
            )

        from yt_channel_analyzer.discovery import allocate_discovery_run

        model, prompt_version = _discover_mode_config(mode)
        project_name = _resolve_primary_project_name(self.db_path)
        run_id = allocate_discovery_run(
            self.db_path,
            project_name=project_name,
            model=model,
            prompt_version=prompt_version,
        )

        if self._run_in_background:
            thread = threading.Thread(
                target=self._discover_runner_safe,
                args=(mode, run_id),
                daemon=True,
                name=f"discover-run-{run_id}",
            )
            thread.start()
        else:
            # Synchronous path for tests. Errors still flip the row to 'error'
            # via run_discovery's failure handlers; surface them as 400 so
            # tests can assert on them like the prior synchronous behavior.
            try:
                self._discover_runner(self.db_path, mode=mode, run_id=run_id)
            except RuntimeError as exc:
                raise ReviewUIError(str(exc)) from exc

        return {
            "ok": True,
            "run_id": run_id,
            "mode": mode,
            "model": model,
            "message": f"Discovery run {run_id} started (mode={mode}, model={model}).",
        }

    def _discover_runner_safe(self, mode: str, run_id: int) -> None:
        """Background-thread entrypoint: drives the runner and swallows
        exceptions. ``run_discovery`` already flips the row to 'error' on
        any failure, so the polling client sees the terminal state via the
        DB. We log to stderr for the dev-server log; we don't have a structured
        logger here.
        """
        try:
            self._discover_runner(self.db_path, mode=mode, run_id=run_id)
        except Exception as exc:  # pragma: no cover - defensive thread guard
            import sys
            print(
                f"[discover-run-{run_id}] background run failed: {exc}",
                file=sys.stderr,
                flush=True,
            )

    def _refine_run(self, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/refine`` — body ``{mode?, video_ids?, discovery_run_id?}``.

        ``mode`` is ``'stub'`` or ``'real'`` (default ``'real'``). ``video_ids``
        (YouTube ids of primary-channel episodes) overrides the auto-picker when
        given; ``discovery_run_id`` pins the discovery run (else the latest).
        Allocates a ``refinement_runs`` row, kicks ``run_refinement`` off on a
        daemon thread (same async pattern as ``POST /api/discover``), and returns
        the run id immediately — the client polls ``GET /api/refine/status/<id>``
        until terminal.
        """
        mode = _normalize_text(body.get("mode")) or "real"
        if mode not in REFINE_MODES:
            raise ReviewUIError(
                f"invalid mode: {mode!r} (must be one of {', '.join(REFINE_MODES)})"
            )
        # UX-only pre-check (the canonical gate is in
        # ``make_real_refinement_llm_callable``) so a missing env var doesn't
        # leave a stale 'pending' row behind.
        if mode == "real" and os.environ.get("RALPH_ALLOW_REAL_LLM") != "1":
            raise ReviewUIError(
                "Real LLM calls are gated behind RALPH_ALLOW_REAL_LLM=1. "
                "Set it before retrying."
            )

        raw_ids = body.get("video_ids")
        video_ids: list[str] | None = None
        if raw_ids is not None:
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ReviewUIError(
                    "video_ids must be a non-empty list of YouTube video ids"
                )
            video_ids = []
            for item in raw_ids:
                text = _normalize_text(item) if isinstance(item, str) else None
                if text is None:
                    raise ReviewUIError("video_ids entries must be non-empty strings")
                if text not in video_ids:
                    video_ids.append(text)

        discovery_run_id_raw = body.get("discovery_run_id")
        discovery_run_id = (
            int(discovery_run_id_raw)
            if discovery_run_id_raw not in (None, "")
            else None
        )

        from yt_channel_analyzer.refinement import allocate_refinement_run

        project_name = _resolve_primary_project_name(self.db_path)
        run_id = allocate_refinement_run(self.db_path, project_name=project_name)

        if self._run_in_background:
            thread = threading.Thread(
                target=self._refine_runner_safe,
                args=(mode, run_id, video_ids, discovery_run_id),
                daemon=True,
                name=f"refine-run-{run_id}",
            )
            thread.start()
        else:
            # Synchronous path for tests. ``run_refinement`` already flips the
            # run row to 'error' on failure; surface gate RuntimeErrors as 400.
            try:
                self._refine_runner(
                    self.db_path,
                    mode=mode,
                    run_id=run_id,
                    video_ids=video_ids,
                    discovery_run_id=discovery_run_id,
                    transcript_fetcher=self._transcript_fetcher,
                )
            except RuntimeError as exc:
                raise ReviewUIError(str(exc)) from exc

        return {
            "ok": True,
            "refinement_run_id": run_id,
            "mode": mode,
            "message": f"Refinement run {run_id} started (mode={mode}).",
        }

    def _refine_runner_safe(
        self,
        mode: str,
        run_id: int,
        video_ids: list[str] | None,
        discovery_run_id: int | None,
    ) -> None:
        """Background-thread entrypoint for ``POST /api/refine``. ``run_refinement``
        flips the run row to 'error' on any failure, so the polling client always
        sees a terminal state; we just log to stderr for the dev-server log."""
        try:
            self._refine_runner(
                self.db_path,
                mode=mode,
                run_id=run_id,
                video_ids=video_ids,
                discovery_run_id=discovery_run_id,
                transcript_fetcher=self._transcript_fetcher,
            )
        except Exception as exc:  # pragma: no cover - defensive thread guard
            import sys
            print(
                f"[refine-run-{run_id}] background run failed: {exc}",
                file=sys.stderr,
                flush=True,
            )

    def _refine_sample(self, discovery_run_id: int | None) -> dict[str, Any]:
        """``GET /api/refine/sample`` — the slice-B3 auto-picked sample for the
        active channel's latest (or the given) discovery run. Read-only."""
        from yt_channel_analyzer import refinement

        project_name = _resolve_primary_project_name(self.db_path)
        try:
            return refinement.describe_refinement_sample(
                self.db_path,
                project_name=project_name,
                discovery_run_id=discovery_run_id,
            )
        except ValueError as exc:
            raise ReviewUIError(str(exc)) from exc

    def _refine_fetch_transcripts(self, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/refine/fetch-transcripts`` — body ``{video_ids: [...]}``
        (YouTube video ids, each must belong to the primary channel). Runs the
        slice-B1 fetch path for each id, then returns the per-episode transcript
        statuses, the ids that came back non-``available`` (the Refine screen
        drops these from the sample), and the ``refine --real`` cost estimate
        over the episodes whose transcript is now available."""
        from yt_channel_analyzer import db as _db
        from yt_channel_analyzer import refinement
        from yt_channel_analyzer.cli import run_fetch_transcripts

        raw_ids = body.get("video_ids") if isinstance(body, dict) else None
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ReviewUIError(
                "missing required field: video_ids (a non-empty list of YouTube video ids)"
            )
        requested: list[str] = []
        for item in raw_ids:
            text = _normalize_text(item) if isinstance(item, str) else None
            if text is None:
                raise ReviewUIError("video_ids entries must be non-empty strings")
            if text not in requested:
                requested.append(text)

        channel_video_ids = {
            row["youtube_video_id"]
            for row in _db.list_primary_channel_transcript_status(self.db_path)
        }
        unknown = [vid for vid in requested if vid not in channel_video_ids]
        if unknown:
            raise ReviewUIError(
                f"not videos of the primary channel: {', '.join(unknown)}"
            )

        run_fetch_transcripts(
            self.db_path,
            requested,
            transcript_fetcher=self._transcript_fetcher,
            request_interval=self._transcript_fetch_request_interval,
            out=lambda *_args, **_kwargs: None,
        )

        status_by_id = {
            row["youtube_video_id"]: row["transcript_status"]
            for row in _db.list_primary_channel_transcript_status(self.db_path)
        }
        episodes: list[dict[str, Any]] = []
        available_ids: list[str] = []
        dropped: list[str] = []
        for vid in requested:
            status = status_by_id.get(vid)
            available = status == "available"
            episodes.append(
                {
                    "youtube_video_id": vid,
                    "transcript_status": status,
                    "available": available,
                }
            )
            (available_ids if available else dropped).append(vid)

        estimated_cost_usd = refinement.estimate_refinement_cost_usd(
            self.db_path, youtube_video_ids=available_ids
        )
        return {
            "ok": True,
            "episodes": episodes,
            "dropped": dropped,
            "n_available": len(available_ids),
            "estimated_cost_usd": estimated_cost_usd,
        }

    def _discovery_run_status(self, run_id: int) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT id, status, error_message, model, prompt_version, created_at
                FROM discovery_runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ReviewUIError(f"discovery run not found: {run_id}")
        return {
            "id": int(row["id"]),
            "status": row["status"],
            "error_message": row["error_message"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "created_at": row["created_at"],
        }

    def _refine_run_status(self, run_id: int) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT id, status, error_message, n_sample, discovery_run_id, created_at
                FROM refinement_runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ReviewUIError(f"refinement run not found: {run_id}")
            n_proposals = connection.execute(
                "SELECT COUNT(*) AS c FROM taxonomy_proposals WHERE refinement_run_id = ?",
                (run_id,),
            ).fetchone()["c"]
        result: dict[str, Any] = {
            "id": int(row["id"]),
            "status": row["status"],
            "n_sample": row["n_sample"],
            "n_proposals": int(n_proposals),
            "discovery_run_id": row["discovery_run_id"],
            "created_at": row["created_at"],
        }
        if row["error_message"] is not None:
            result["error"] = row["error_message"]
        return result

    def _refine_proposal_resolve(
        self, body: dict[str, Any], *, accept: bool
    ) -> dict[str, Any]:
        """``POST /api/refine/proposal/{accept,reject}`` — body ``{proposal_id}``.

        Accept creates the real ``topics``/``subtopics`` node (parent resolved
        through the rename log; idempotent); a missing parent is reported back as
        a rejection. Reject just marks the proposal row.
        """
        raw = body.get("proposal_id")
        try:
            proposal_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ReviewUIError("missing or invalid field: proposal_id") from exc
        with connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            if accept:
                result = accept_taxonomy_proposal(connection, proposal_id)
            else:
                result = reject_taxonomy_proposal(connection, proposal_id)
            connection.commit()
        if not accept:
            msg = f"Rejected proposal {proposal_id}."
        elif result.get("status") == "rejected":
            parent = result.get("parent_topic_name")
            msg = (
                f"Proposal {proposal_id} ({result.get('kind')} "
                f"'{result.get('name')}') could not be accepted — parent topic "
                f"'{parent}' no longer exists. Marked rejected."
            )
        elif result.get("kind") == "subtopic":
            msg = (
                f"Accepted subtopic '{result.get('name')}' under "
                f"'{result.get('parent_topic_name')}'. Re-run Discover to spread it."
            )
        else:
            msg = f"Accepted topic '{result.get('name')}'. Re-run Discover to spread it."
        return {"ok": True, "result": result, "message": msg}

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
    with make_server(host, port, app, server_class=_ThreadingWSGIServer) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nReview UI stopped.")
