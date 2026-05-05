from __future__ import annotations

import json
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
    reject_comparison_group_suggestion_label,
    reject_subtopic_suggestion_label,
    reject_topic_suggestion_label,
    rename_comparison_group_suggestion_label,
    rename_subtopic_suggestion_label,
    rename_topic_suggestion_label,
    store_video_comparison_group_suggestion,
    store_video_subtopic_suggestion,
    store_video_topic_suggestion,
    summarize_comparison_group_suggestion_labels,
    summarize_subtopic_suggestion_labels,
    summarize_topic_suggestion_labels,
)
from yt_channel_analyzer.comparison_group_suggestions import suggest_comparison_groups_for_video
from yt_channel_analyzer.subtopic_suggestions import suggest_subtopics_for_video
from yt_channel_analyzer.topic_suggestions import suggest_topics_for_video


DEFAULT_SUGGESTION_MODEL = "gpt-4.1-mini"
UI_REVISION = "2026-04-25.10-subtopic-readiness"
MIN_NEW_SUBTOPIC_CLUSTER_SIZE = 5

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
    .readiness.thin { color: #fbbf24; background: rgba(251,191,36,0.10); border-color: rgba(251,191,36,0.24); }
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
          Suggestion run
          <select id="run-select"></select>
        </label>
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
          <button id="generate-comparison-groups-btn">Generate comparison-group suggestions</button>
        </div>
      </div>
      <div class="status" id="status-box">Loading channel data… If this does not change, the page hit a client-side render error.</div>
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

    function topicInventoryHtml(inventory) {
      if (!inventory) return '';
      const buckets = inventory.subtopics || [];
      const unassigned = inventory.unassigned_videos || [];
      const assignedHtml = buckets.length
        ? buckets.map((bucket) => `
            <div class="subtopic-bucket">
              <strong>${escapeHtml(bucket.name)}</strong>
              <span class="pill">${escapeHtml(bucket.videos.length)} video(s)</span>
              <span class="readiness ${bucket.comparison_ready ? 'ready' : 'thin'}">${bucket.readiness_label}</span>
              <div class="muted">${escapeHtml(bucket.next_step || '')}</div>
              ${bucket.comparison_ready ? `<div class="subtopic-actions"><button class="primary-action" onclick='generateComparisonGroupsForSubtopic(${JSON.stringify(bucket.name)})'>Generate comparison groups</button></div>` : ''}
              <div class="video-list">
                ${bucket.videos.length ? bucket.videos.map(videoChipHtml).join('') : '<div class="muted">No videos assigned yet.</div>'}
              </div>
            </div>
          `).join('')
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
    document.getElementById('topic-select').addEventListener('change', () => fetchState({ topic: selectedTopicName(), subtopic: null }).catch((error) => setStatus(error.message, true)));
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
                videos.youtube_video_id,
                videos.title
            FROM subtopics
            JOIN topics ON topics.id = subtopics.topic_id
            LEFT JOIN video_subtopics ON video_subtopics.subtopic_id = subtopics.id
            LEFT JOIN videos ON videos.id = video_subtopics.video_id
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
            {"name": row["subtopic_name"], "videos": []},
        )
        if row["youtube_video_id"] is not None:
            bucket["videos"].append(
                {"youtube_video_id": row["youtube_video_id"], "title": row["title"]}
            )
    subtopic_buckets = list(buckets.values())
    for bucket in subtopic_buckets:
        video_count = len(bucket["videos"])
        bucket["video_count"] = video_count
        bucket["comparison_ready"] = video_count >= MIN_NEW_SUBTOPIC_CLUSTER_SIZE
        if bucket["comparison_ready"]:
            bucket["readiness_label"] = "Ready for comparison"
            bucket["next_step"] = "Enough videos to generate comparison-group suggestions."
        else:
            needed = MIN_NEW_SUBTOPIC_CLUSTER_SIZE - video_count
            bucket["readiness_label"] = "Too thin to compare"
            bucket["next_step"] = f"Needs {needed} more video(s) before comparison groups are useful."
    return {
        "topic": topic_name,
        "subtopics": subtopic_buckets,
        "unassigned_videos": [
            {"youtube_video_id": row["youtube_video_id"], "title": row["title"]}
            for row in unassigned_rows
        ],
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
    primary_channel = get_primary_channel(db_path)
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

    return {
        "db_path": str(db_path),
        "dataset_name": db_path.name,
        "dataset_video_count": len(topic_generation_candidates),
        "channel_title": primary_channel.title,
        "channel_id": primary_channel.youtube_channel_id,
        "run_id": resolved_run_id,
        "latest_run_id": latest_run_id,
        "runs": runs,
        "current_run": current_run,
        "topic_map": topic_map,
        "topic_inventory": topic_inventory,
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
