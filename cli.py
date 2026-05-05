from __future__ import annotations

import argparse
import json
from pathlib import Path

from yt_channel_analyzer.db import (
    add_video_to_comparison_group,
    assign_subtopic_to_video,
    assign_topic_to_video,
    move_video_between_comparison_groups,
    create_comparison_group,
    create_subtopic,
    create_topic,
    get_comparison_group_details,
    get_group_analysis,
    get_group_processed_video_results,
    get_group_transcript_statuses,
    get_group_transcripts_for_processing,
    get_markdown_exports,
    get_primary_channel,
    get_project_overview,
    get_stored_channels,
    get_video_subtopic_assignments,
    get_video_summary,
    get_video_topic_assignments,
    init_db,
    list_comparison_groups,
    list_group_videos,
    list_subtopics,
    list_topics,
    record_markdown_export,
    remove_subtopic_from_video,
    remove_topic_from_video,
    remove_video_from_comparison_group,
    rename_comparison_group,
    rename_subtopic,
    rename_topic,
    resolve_comparison_group,
    search_library,
    upsert_channel_metadata,
    upsert_group_analysis,
    upsert_processed_video_artifacts,
    upsert_video_transcript,
    upsert_videos_for_primary_channel,
    approve_comparison_group_suggestion_label,
    approve_topic_suggestion_label,
    apply_topic_suggestion_to_video,
    bulk_apply_topic_suggestion_label,
    create_comparison_group_suggestion_run,
    create_topic_suggestion_run,
    get_latest_topic_suggestion_run_id,
    list_topic_suggestion_runs,
    list_approved_topic_names,
    list_approved_comparison_groups_for_subtopic,
    list_approved_subtopics_for_topic,
    list_video_comparison_group_suggestions,
    list_video_topic_suggestions,
    list_video_subtopic_suggestions,
    list_videos_for_comparison_group_suggestions,
    list_videos_for_topic_suggestions,
    list_videos_for_subtopic_suggestions,
    reject_comparison_group_suggestion_label,
    reject_topic_suggestion_label,
    reject_subtopic_suggestion_label,
    rename_comparison_group_suggestion_label,
    rename_topic_suggestion_label,
    rename_subtopic_suggestion_label,
    store_video_comparison_group_suggestion,
    store_video_topic_suggestion,
    create_subtopic_suggestion_run,
    store_video_subtopic_suggestion,
    summarize_comparison_group_suggestion_labels,
    summarize_topic_suggestion_labels,
    summarize_subtopic_suggestion_labels,
    get_comparison_group_suggestion_review_rows,
    get_topic_suggestion_review_rows,
    get_subtopic_suggestion_review_rows,
    approve_subtopic_suggestion_label,
    supersede_stale_topic_suggestions,
)
from yt_channel_analyzer.discovery import (
    STUB_MODEL,
    STUB_PROMPT_VERSION,
    run_discovery,
    stub_llm,
)
from yt_channel_analyzer.comparison_group_suggestions import suggest_comparison_groups_for_video
from yt_channel_analyzer.group_analysis import GroupAnalysisInput, build_group_analysis
from yt_channel_analyzer.markdown_export import build_group_markdown_export, write_group_markdown_export
from yt_channel_analyzer.processing import process_transcript_record
from yt_channel_analyzer.review_ui import serve_review_ui
from yt_channel_analyzer.subtopic_suggestions import suggest_subtopics_for_video
from yt_channel_analyzer.topic_suggestions import suggest_topics_for_video
from yt_channel_analyzer.youtube import (
    TranscriptRecord,
    fetch_channel_metadata,
    fetch_channel_videos,
    fetch_video_transcript,
    resolve_canonical_channel_id,
)


CLI_MODULE_PREFIX = "python3 -m yt_channel_analyzer.cli"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yt-channel-analyzer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init-db",
        help="Create a new project-scoped SQLite database with one primary channel.",
    )
    init_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    init_parser.add_argument("--project-name", required=True, help="Human-readable project name.")
    init_parser.add_argument("--channel-id", required=True, help="YouTube channel ID for the primary channel.")
    init_parser.add_argument("--channel-title", required=True, help="Display title for the primary channel.")
    init_parser.add_argument("--channel-handle", help="Optional YouTube handle for the primary channel.")

    fetch_parser = subparsers.add_parser(
        "fetch-channel",
        help="Resolve one YouTube channel input, fetch channel metadata, and store it in SQLite.",
    )
    fetch_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    fetch_parser.add_argument("--project-name", required=True, help="Human-readable project name.")
    fetch_parser.add_argument(
        "channel_input",
        help="One YouTube channel input: channel ID, handle, or supported URL.",
    )

    fetch_videos_parser = subparsers.add_parser(
        "fetch-videos",
        help="Fetch video metadata for the already-stored primary channel and store it in SQLite.",
    )
    fetch_videos_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    fetch_videos_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Conservative maximum number of uploaded videos to fetch from the primary channel.",
    )

    subparsers.add_parser(
        "show-channels",
        help="Print stored channel metadata rows from SQLite.",
    ).add_argument("--db-path", required=True, help="Path to the SQLite database file.")

    show_videos_parser = subparsers.add_parser(
        "show-videos",
        help="Print a concise stored video count and a few sample videos.",
    )
    show_videos_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    show_videos_parser.add_argument(
        "--sample-limit",
        type=int,
        default=5,
        help="Number of sample videos to print.",
    )

    overview_parser = subparsers.add_parser(
        "show-project-overview",
        help="Print a plain-text overview of the stored project, topics, subtopics, groups, and artefact counts.",
    )
    overview_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")

    create_topic_parser = subparsers.add_parser(
        "create-topic",
        help="Create or update a broad manual topic for a project database.",
    )
    create_topic_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    create_topic_parser.add_argument("--project-name", required=True, help="Human-readable project name.")
    create_topic_parser.add_argument("--name", required=True, help="Broad topic name.")
    create_topic_parser.add_argument("--description", help="Optional topic description.")

    list_topics_parser = subparsers.add_parser(
        "list-topics",
        help="List stored broad topics with simple assignment counts.",
    )
    list_topics_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")

    rename_topic_parser = subparsers.add_parser(
        "rename-topic",
        help="Rename an existing broad topic without changing its relationships.",
    )
    rename_topic_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    rename_topic_parser.add_argument("--project-name", required=True, help="Human-readable project name.")
    rename_topic_parser.add_argument("--current-name", required=True, help="Existing broad topic name.")
    rename_topic_parser.add_argument("--new-name", required=True, help="Replacement broad topic name.")

    assign_topic_parser = subparsers.add_parser(
        "assign-topic",
        help="Assign a stored topic to a stored video as primary or secondary.",
    )
    assign_topic_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    assign_topic_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    assign_topic_parser.add_argument("--topic", required=True, help="Existing topic name.")
    assign_topic_parser.add_argument(
        "--assignment-type",
        required=True,
        choices=["primary", "secondary"],
        help="Whether this topic is the primary or a secondary topic for the video.",
    )

    inspect_video_topics_parser = subparsers.add_parser(
        "show-video-topics",
        help="Inspect topic assignments for one stored video.",
    )
    inspect_video_topics_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    inspect_video_topics_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")

    create_subtopic_parser = subparsers.add_parser(
        "create-subtopic",
        help="Create or update a manual subtopic under an existing broad topic.",
    )
    create_subtopic_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    create_subtopic_parser.add_argument("--topic", required=True, help="Existing broad topic name.")
    create_subtopic_parser.add_argument("--name", required=True, help="Subtopic name.")
    create_subtopic_parser.add_argument("--description", help="Optional subtopic description.")

    list_subtopics_parser = subparsers.add_parser(
        "list-subtopics",
        help="List stored subtopics for one broad topic with simple assignment counts.",
    )
    list_subtopics_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    list_subtopics_parser.add_argument("--topic", required=True, help="Existing broad topic name.")

    rename_subtopic_parser = subparsers.add_parser(
        "rename-subtopic",
        help="Rename an existing subtopic without changing its relationships.",
    )
    rename_subtopic_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    rename_subtopic_parser.add_argument("--topic", required=True, help="Existing broad topic name.")
    rename_subtopic_parser.add_argument("--current-name", required=True, help="Existing subtopic name.")
    rename_subtopic_parser.add_argument("--new-name", required=True, help="Replacement subtopic name.")

    assign_subtopic_parser = subparsers.add_parser(
        "assign-subtopic",
        help="Assign a stored subtopic to a stored video.",
    )
    assign_subtopic_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    assign_subtopic_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    assign_subtopic_parser.add_argument("--subtopic", required=True, help="Existing subtopic name.")

    show_video_subtopics_parser = subparsers.add_parser(
        "show-video-subtopics",
        help="Inspect subtopic assignments for one stored video.",
    )
    show_video_subtopics_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    show_video_subtopics_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")

    create_group_parser = subparsers.add_parser(
        "create-comparison-group",
        help="Create or update a manual comparison group under an existing subtopic.",
    )
    create_group_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    create_group_parser.add_argument("--subtopic", required=True, help="Existing subtopic name.")
    create_group_parser.add_argument("--name", required=True, help="Comparison group name.")
    create_group_parser.add_argument("--description", help="Optional comparison group description.")
    create_group_parser.add_argument("--target-size", type=int, help="Optional target group size for later workflows.")

    list_groups_parser = subparsers.add_parser(
        "list-comparison-groups",
        help="List comparison groups for one subtopic with member counts.",
    )
    list_groups_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    list_groups_parser.add_argument("--subtopic", required=True, help="Existing subtopic name.")

    rename_group_parser = subparsers.add_parser(
        "rename-comparison-group",
        help="Rename an existing comparison group without changing its relationships.",
    )
    rename_group_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    rename_group_parser.add_argument("--subtopic", required=True, help="Existing subtopic name.")
    rename_group_parser.add_argument("--current-name", required=True, help="Existing comparison group name.")
    rename_group_parser.add_argument("--new-name", required=True, help="Replacement comparison group name.")

    add_group_member_parser = subparsers.add_parser(
        "add-video-to-comparison-group",
        help="Add a stored video to a stored comparison group.",
    )
    add_group_member_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    add_group_member_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    add_group_member_parser.add_argument("--group", required=True, help="Existing comparison group name.")

    remove_group_member_parser = subparsers.add_parser(
        "remove-video-from-comparison-group",
        help="Remove a stored video from a stored comparison group without deleting the video.",
    )
    remove_group_member_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    remove_group_member_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    remove_group_member_parser.add_argument("--group", required=True, help="Existing comparison group name.")

    move_group_member_parser = subparsers.add_parser(
        "move-video-between-comparison-groups",
        help="Move a stored video from one comparison group to another without deleting underlying records.",
    )
    move_group_member_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    move_group_member_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    move_group_member_parser.add_argument("--from-group", required=True, help="Existing source comparison group name.")
    move_group_member_parser.add_argument("--to-group", required=True, help="Existing target comparison group name.")

    remove_topic_assignment_parser = subparsers.add_parser(
        "remove-video-topic-assignment",
        help="Remove one topic assignment from a stored video without deleting the video.",
    )
    remove_topic_assignment_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    remove_topic_assignment_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    remove_topic_assignment_parser.add_argument("--topic", required=True, help="Existing topic name.")

    remove_subtopic_assignment_parser = subparsers.add_parser(
        "remove-video-subtopic-assignment",
        help="Remove one subtopic assignment from a stored video without deleting the video.",
    )
    remove_subtopic_assignment_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    remove_subtopic_assignment_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    remove_subtopic_assignment_parser.add_argument("--subtopic", required=True, help="Existing subtopic name.")

    show_group_parser = subparsers.add_parser(
        "show-comparison-group",
        help="Inspect one comparison group and list its member videos.",
    )
    show_group_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    show_group_parser.add_argument("--group", required=True, help="Existing comparison group name.")

    fetch_transcripts_parser = subparsers.add_parser(
        "fetch-group-transcripts",
        help="Fetch transcripts only for videos in one comparison group.",
    )
    fetch_transcripts_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    fetch_target = fetch_transcripts_parser.add_mutually_exclusive_group(required=True)
    fetch_target.add_argument("--group", help="Comparison group name.")
    fetch_target.add_argument("--group-id", type=int, help="Comparison group id.")

    show_transcripts_parser = subparsers.add_parser(
        "show-group-transcripts",
        help="Inspect transcript status/results for one comparison group.",
    )
    show_transcripts_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    show_target = show_transcripts_parser.add_mutually_exclusive_group(required=True)
    show_target.add_argument("--group", help="Comparison group name.")
    show_target.add_argument("--group-id", type=int, help="Comparison group id.")

    process_parser = subparsers.add_parser(
        "process-group-videos",
        help="Process stored transcripts for videos in one comparison group into per-video artefacts.",
    )
    process_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    process_target = process_parser.add_mutually_exclusive_group(required=True)
    process_target.add_argument("--group", help="Comparison group name.")
    process_target.add_argument("--group-id", type=int, help="Comparison group id.")

    show_processed_parser = subparsers.add_parser(
        "show-group-processing",
        help="Inspect per-video processing results for one comparison group.",
    )
    show_processed_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    show_processed_target = show_processed_parser.add_mutually_exclusive_group(required=True)
    show_processed_target.add_argument("--group", help="Comparison group name.")
    show_processed_target.add_argument("--group-id", type=int, help="Comparison group id.")

    analyze_group_parser = subparsers.add_parser(
        "analyze-comparison-group",
        help="Build and store deterministic group-level analysis for one comparison group.",
    )
    analyze_group_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    analyze_group_target = analyze_group_parser.add_mutually_exclusive_group(required=True)
    analyze_group_target.add_argument("--group", help="Comparison group name.")
    analyze_group_target.add_argument("--group-id", type=int, help="Comparison group id.")

    show_group_analysis_parser = subparsers.add_parser(
        "show-group-analysis",
        help="Inspect stored group-level analysis for one comparison group.",
    )
    show_group_analysis_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    show_group_analysis_target = show_group_analysis_parser.add_mutually_exclusive_group(required=True)
    show_group_analysis_target.add_argument("--group", help="Comparison group name.")
    show_group_analysis_target.add_argument("--group-id", type=int, help="Comparison group id.")

    export_markdown_parser = subparsers.add_parser(
        "export-group-markdown",
        help="Export stored per-video and group markdown for one comparison group.",
    )
    export_markdown_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    export_markdown_parser.add_argument("--output-dir", required=True, help="Directory for markdown export files.")
    export_markdown_target = export_markdown_parser.add_mutually_exclusive_group(required=True)
    export_markdown_target.add_argument("--group", help="Comparison group name.")
    export_markdown_target.add_argument("--group-id", type=int, help="Comparison group id.")

    search_parser = subparsers.add_parser(
        "search-library",
        help="Search stored transcript, summary, and group analysis text from SQLite only.",
    )
    search_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    search_parser.add_argument("query", help="Search query text.")
    search_parser.add_argument("--group", help="Optional comparison group name filter.")
    search_parser.add_argument("--topic", help="Optional topic name filter.")
    search_parser.add_argument("--subtopic", help="Optional subtopic name filter.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of ranked matches to print.",
    )

    suggest_topics_parser = subparsers.add_parser(
        "suggest-topics",
        help="Use AI to suggest broad primary/secondary topics from stored video title and description metadata only.",
    )
    suggest_topics_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    suggest_topics_parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of stored videos to suggest topics for.",
    )
    suggest_topics_parser.add_argument(
        "--model",
        default="gpt-4.1-mini",
        help="OpenAI model name for structured topic suggestion output.",
    )

    suggest_subtopics_parser = subparsers.add_parser(
        "suggest-subtopics",
        help="Use AI to suggest one review-only subtopic within an existing approved broad topic from stored video title and description metadata only.",
    )
    suggest_subtopics_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    suggest_subtopics_parser.add_argument("--topic", required=True, help="Existing approved broad topic name.")
    suggest_subtopics_parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of stored videos within the broad topic to inspect.",
    )
    suggest_subtopics_parser.add_argument(
        "--model",
        default="gpt-4.1-mini",
        help="OpenAI model name for structured subtopic suggestion output.",
    )

    suggest_comparison_groups_parser = subparsers.add_parser(
        "suggest-comparison-groups",
        help="Use AI to suggest one review-only comparison group within an existing approved subtopic from stored video title and description metadata only.",
    )
    suggest_comparison_groups_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    suggest_comparison_groups_parser.add_argument("--subtopic", required=True, help="Existing approved subtopic name.")
    suggest_comparison_groups_parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of stored videos within the subtopic to inspect.",
    )
    suggest_comparison_groups_parser.add_argument(
        "--model",
        default="gpt-4.1-mini",
        help="OpenAI model name for structured comparison-group suggestion output.",
    )

    list_topic_suggestions_parser = subparsers.add_parser(
        "list-topic-suggestions",
        help="List stored topic suggestions pending or after review.",
    )
    list_topic_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    list_topic_suggestions_parser.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "superseded"],
        help="Optional suggested-label review status filter.",
    )
    list_topic_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to inspect. Defaults to the latest suggestion run.",
    )

    list_topic_suggestion_runs_parser = subparsers.add_parser(
        "list-topic-suggestion-runs",
        help="List stored topic suggestion runs with at-a-glance review counts.",
    )
    list_topic_suggestion_runs_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")

    list_subtopic_suggestions_parser = subparsers.add_parser(
        "list-subtopic-suggestions",
        help="List stored review-only subtopic suggestions for one approved broad topic.",
    )
    list_subtopic_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    list_subtopic_suggestions_parser.add_argument("--topic", required=True, help="Approved broad topic name.")
    list_subtopic_suggestions_parser.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "superseded"],
        help="Optional suggested-label review status filter.",
    )
    list_subtopic_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to inspect. Defaults to the latest suggestion run.",
    )

    list_comparison_group_suggestions_parser = subparsers.add_parser(
        "list-comparison-group-suggestions",
        help="List stored review-only comparison-group suggestions for one approved subtopic.",
    )
    list_comparison_group_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    list_comparison_group_suggestions_parser.add_argument("--subtopic", required=True, help="Approved subtopic name.")
    list_comparison_group_suggestions_parser.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "superseded"],
        help="Optional suggested-label review status filter.",
    )
    list_comparison_group_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to inspect. Defaults to the latest suggestion run.",
    )

    summarize_topic_suggestions_parser = subparsers.add_parser(
        "summarize-topic-suggestion-labels",
        help="Summarize suggested labels so review can happen label-by-label.",
    )
    summarize_topic_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    summarize_topic_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to summarize. Defaults to the latest suggestion run.",
    )
    summarize_topic_suggestions_parser.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "superseded"],
        help="Optional suggested-label review status filter.",
    )

    summarize_subtopic_suggestions_parser = subparsers.add_parser(
        "summarize-subtopic-suggestion-labels",
        help="Summarize suggested subtopic labels for one approved broad topic so review can happen label-by-label.",
    )
    summarize_subtopic_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    summarize_subtopic_suggestions_parser.add_argument("--topic", required=True, help="Approved broad topic name.")
    summarize_subtopic_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to summarize. Defaults to the latest suggestion run.",
    )
    summarize_subtopic_suggestions_parser.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "superseded"],
        help="Optional suggested-label review status filter.",
    )

    summarize_comparison_group_suggestions_parser = subparsers.add_parser(
        "summarize-comparison-group-suggestion-labels",
        help="Summarize suggested comparison-group labels for one approved subtopic so review can happen label-by-label.",
    )
    summarize_comparison_group_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    summarize_comparison_group_suggestions_parser.add_argument("--subtopic", required=True, help="Approved subtopic name.")
    summarize_comparison_group_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to summarize. Defaults to the latest suggestion run.",
    )
    summarize_comparison_group_suggestions_parser.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "superseded"],
        help="Optional suggested-label review status filter.",
    )

    review_topic_suggestions_parser = subparsers.add_parser(
        "review-topic-suggestions",
        help="Show the latest pending topic suggestion run in a guided, grouped review format.",
    )
    review_topic_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    review_topic_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to review. Defaults to the latest suggestion run.",
    )
    review_topic_suggestions_parser.add_argument(
        "--sample-limit",
        type=int,
        default=3,
        help="How many example video titles to show under each label.",
    )

    review_subtopic_suggestions_parser = subparsers.add_parser(
        "review-subtopic-suggestions",
        help="Show the latest pending subtopic suggestion run for one approved broad topic in a guided review format.",
    )
    review_subtopic_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    review_subtopic_suggestions_parser.add_argument("--topic", required=True, help="Approved broad topic name.")
    review_subtopic_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to review. Defaults to the latest suggestion run.",
    )
    review_subtopic_suggestions_parser.add_argument(
        "--sample-limit",
        type=int,
        default=3,
        help="How many example video titles to show under each subtopic label.",
    )

    review_comparison_group_suggestions_parser = subparsers.add_parser(
        "review-comparison-group-suggestions",
        help="Show the latest pending comparison-group suggestion run for one approved subtopic in a guided review format.",
    )
    review_comparison_group_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    review_comparison_group_suggestions_parser.add_argument("--subtopic", required=True, help="Approved subtopic name.")
    review_comparison_group_suggestions_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run to review. Defaults to the latest suggestion run.",
    )
    review_comparison_group_suggestions_parser.add_argument(
        "--sample-limit",
        type=int,
        default=3,
        help="How many example video titles to show under each comparison-group label.",
    )

    approve_topic_suggestion_parser = subparsers.add_parser(
        "approve-topic-suggestion-label",
        help="Approve one suggested label, optionally renaming it to the final approved topic name.",
    )
    approve_topic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    approve_topic_suggestion_parser.add_argument("--label", required=True, help="Suggested label to approve.")
    approve_topic_suggestion_parser.add_argument(
        "--approved-name",
        help="Optional final approved broad-topic name to use instead of the suggested label.",
    )
    approve_topic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    approve_subtopic_suggestion_parser = subparsers.add_parser(
        "approve-subtopic-suggestion-label",
        help="Approve one suggested subtopic label within a chosen broad topic, optionally renaming it.",
    )
    approve_subtopic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    approve_subtopic_suggestion_parser.add_argument("--topic", required=True, help="Approved broad topic name.")
    approve_subtopic_suggestion_parser.add_argument("--label", required=True, help="Suggested subtopic label to approve.")
    approve_subtopic_suggestion_parser.add_argument(
        "--approved-name",
        help="Optional final approved subtopic name to use instead of the suggested label.",
    )
    approve_subtopic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    approve_comparison_group_suggestion_parser = subparsers.add_parser(
        "approve-comparison-group-suggestion-label",
        help="Approve one suggested comparison-group label within a chosen subtopic, optionally renaming it.",
    )
    approve_comparison_group_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    approve_comparison_group_suggestion_parser.add_argument("--subtopic", required=True, help="Approved subtopic name.")
    approve_comparison_group_suggestion_parser.add_argument("--label", required=True, help="Suggested comparison-group label to approve.")
    approve_comparison_group_suggestion_parser.add_argument(
        "--approved-name",
        help="Optional final approved comparison-group name to use instead of the suggested label.",
    )
    approve_comparison_group_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    reject_topic_suggestion_parser = subparsers.add_parser(
        "reject-topic-suggestion-label",
        help="Reject one suggested label without applying it.",
    )
    reject_topic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    reject_topic_suggestion_parser.add_argument("--label", required=True, help="Suggested label to reject.")
    reject_topic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    reject_subtopic_suggestion_parser = subparsers.add_parser(
        "reject-subtopic-suggestion-label",
        help="Reject one suggested subtopic label without applying it.",
    )
    reject_subtopic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    reject_subtopic_suggestion_parser.add_argument("--topic", required=True, help="Approved broad topic name.")
    reject_subtopic_suggestion_parser.add_argument("--label", required=True, help="Suggested subtopic label to reject.")
    reject_subtopic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    reject_comparison_group_suggestion_parser = subparsers.add_parser(
        "reject-comparison-group-suggestion-label",
        help="Reject one suggested comparison-group label without applying it.",
    )
    reject_comparison_group_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    reject_comparison_group_suggestion_parser.add_argument("--subtopic", required=True, help="Approved subtopic name.")
    reject_comparison_group_suggestion_parser.add_argument("--label", required=True, help="Suggested comparison-group label to reject.")
    reject_comparison_group_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    rename_topic_suggestion_parser = subparsers.add_parser(
        "rename-topic-suggestion-label",
        help="Rename one pending suggested label without approving it yet.",
    )
    rename_topic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    rename_topic_suggestion_parser.add_argument("--current-name", required=True, help="Current suggested label.")
    rename_topic_suggestion_parser.add_argument("--new-name", required=True, help="Replacement suggested label.")
    rename_topic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    rename_subtopic_suggestion_parser = subparsers.add_parser(
        "rename-subtopic-suggestion-label",
        help="Rename one pending suggested subtopic label without approving it yet.",
    )
    rename_subtopic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    rename_subtopic_suggestion_parser.add_argument("--topic", required=True, help="Approved broad topic name.")
    rename_subtopic_suggestion_parser.add_argument("--current-name", required=True, help="Current suggested subtopic label.")
    rename_subtopic_suggestion_parser.add_argument("--new-name", required=True, help="Replacement suggested subtopic label.")
    rename_subtopic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    rename_comparison_group_suggestion_parser = subparsers.add_parser(
        "rename-comparison-group-suggestion-label",
        help="Rename one pending suggested comparison-group label without approving it yet.",
    )
    rename_comparison_group_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    rename_comparison_group_suggestion_parser.add_argument("--subtopic", required=True, help="Approved subtopic name.")
    rename_comparison_group_suggestion_parser.add_argument("--current-name", required=True, help="Current suggested comparison-group label.")
    rename_comparison_group_suggestion_parser.add_argument("--new-name", required=True, help="Replacement suggested comparison-group label.")
    rename_comparison_group_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the label. Defaults to the latest suggestion run.",
    )

    apply_topic_suggestion_parser = subparsers.add_parser(
        "apply-topic-suggestion",
        help="Apply one approved suggestion to one video. Nothing is auto-applied.",
    )
    apply_topic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    apply_topic_suggestion_parser.add_argument("--video-id", required=True, help="Stored YouTube video ID.")
    apply_topic_suggestion_parser.add_argument("--label", required=True, help="Approved suggested label to apply.")
    apply_topic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the approved label. Defaults to the latest suggestion run.",
    )

    bulk_apply_topic_suggestion_parser = subparsers.add_parser(
        "bulk-apply-topic-suggestion-label",
        help="Apply one approved suggestion label to all matching videos in a specific run.",
    )
    bulk_apply_topic_suggestion_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    bulk_apply_topic_suggestion_parser.add_argument("--label", required=True, help="Approved suggested label to apply.")
    bulk_apply_topic_suggestion_parser.add_argument(
        "--run-id",
        type=int,
        help="Optional suggestion run containing the approved label. Defaults to the latest suggestion run.",
    )

    supersede_topic_suggestions_parser = subparsers.add_parser(
        "supersede-stale-topic-suggestions",
        help="Mark stale pending suggestion labels from older runs as superseded.",
    )
    supersede_topic_suggestions_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    supersede_topic_suggestions_parser.add_argument(
        "--keep-run-id",
        type=int,
        required=True,
        help="Keep this run active and supersede pending suggestion labels from older runs.",
    )
    supersede_topic_suggestions_parser.add_argument(
        "--label",
        help="Optional specific suggested label to supersede across older runs.",
    )

    discover_parser = subparsers.add_parser(
        "discover",
        help="Run topic discovery for a project's primary channel.",
    )
    discover_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    discover_parser.add_argument("--project-name", required=True, help="Project whose primary channel to analyze.")
    discover_parser.add_argument(
        "--stub",
        action="store_true",
        help="Use a hardcoded fake LLM payload (no API call). Required until real LLM lands.",
    )

    serve_review_ui_parser = subparsers.add_parser(
        "serve-review-ui",
        help="Run a lightweight local web UI for reviewing and applying suggestion labels.",
    )
    serve_review_ui_parser.add_argument("--db-path", required=True, help="Path to the SQLite database file.")
    serve_review_ui_parser.add_argument("--host", default="127.0.0.1", help="Bind host for the local web server.")
    serve_review_ui_parser.add_argument("--port", type=int, default=8765, help="Bind port for the local web server.")
    serve_review_ui_parser.add_argument(
        "--sample-limit",
        type=int,
        default=3,
        help="Sample videos to show for each pending suggestion label.",
    )

    return parser



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        db_path = init_db(
            Path(args.db_path),
            project_name=args.project_name,
            channel_id=args.channel_id,
            channel_title=args.channel_title,
            channel_handle=args.channel_handle,
        )
        print(f"Initialized project database at {db_path}")
        return 0

    if args.command == "fetch-channel":
        channel_id = resolve_canonical_channel_id(args.channel_input)
        metadata = fetch_channel_metadata(channel_id)
        stored_channel_id = upsert_channel_metadata(
            Path(args.db_path),
            project_name=args.project_name,
            metadata=metadata,
        )
        print(f"Stored channel {metadata.youtube_channel_id} as row {stored_channel_id}")
        return 0

    if args.command == "fetch-videos":
        primary_channel = get_primary_channel(Path(args.db_path))
        videos = fetch_channel_videos(primary_channel.youtube_channel_id, limit=args.limit)
        stored_count = upsert_videos_for_primary_channel(Path(args.db_path), videos=videos)
        print(
            f"Stored {stored_count} video metadata rows for {primary_channel.youtube_channel_id} ({primary_channel.title})"
        )
        return 0

    if args.command == "show-channels":
        for row in get_stored_channels(Path(args.db_path)):
            print(
                " | ".join(
                    [
                        row["youtube_channel_id"],
                        row["title"] or "",
                        row["handle"] or "",
                        row["published_at"] or "",
                        row["last_refreshed_at"] or "",
                    ]
                )
            )
        return 0

    if args.command == "show-videos":
        total, rows = get_video_summary(Path(args.db_path), sample_limit=args.sample_limit)
        print(f"Video count: {total}")
        for row in rows:
            print(f"- {row['youtube_video_id']} | {row['published_at'] or ''} | {row['title']}")
        return 0

    if args.command == "show-project-overview":
        overview = get_project_overview(Path(args.db_path))
        channel = overview["channel"]
        counts = overview["counts"]
        print(f"Project: {overview['project_name']}")
        channel_line = f"Channel: {channel['title']} | {channel['youtube_channel_id']}"
        if channel["handle"]:
            channel_line += f" | {channel['handle']}"
        print(channel_line)
        print(
            "Counts: "
            f"videos={counts['videos']} | transcripts={counts['transcripts']} | "
            f"processed_videos={counts['processed_videos']} | exports={counts['exports']}"
        )
        topics = overview["topics"]
        if not topics:
            print("Topics: (none)")
            return 0
        print("Topics:")
        for topic in topics:
            print(
                f"- {topic['name']} | topic_assignments={topic['topic_assignment_count']} | subtopics={topic['subtopic_count']}"
            )
            if not topic["subtopics"]:
                print("  (no subtopics)")
                continue
            for subtopic in topic["subtopics"]:
                print(
                    f"  - {subtopic['name']} | subtopic_assignments={subtopic['subtopic_assignment_count']} | groups={subtopic['group_count']}"
                )
                if not subtopic["groups"]:
                    print("    (no comparison groups)")
                    continue
                for group in subtopic["groups"]:
                    print(
                        "    - "
                        f"{group['name']} | members={group['member_count']} | transcripts={group['transcript_count']} | "
                        f"processed={group['processed_video_count']} | exports={group['export_count']}"
                    )
        return 0

    if args.command == "create-topic":
        topic_id = create_topic(
            Path(args.db_path),
            project_name=args.project_name,
            topic_name=args.name,
            description=args.description,
        )
        print(f"Stored topic '{args.name}' as row {topic_id}")
        return 0

    if args.command == "list-topics":
        for row in list_topics(Path(args.db_path)):
            print(
                " | ".join(
                    [
                        row["name"],
                        row["description"] or "",
                        f"assignments={row['assignment_count']}",
                        f"primary={row['primary_count'] or 0}",
                        f"secondary={row['secondary_count'] or 0}",
                    ]
                )
            )
        return 0

    if args.command == "rename-topic":
        topic_id = rename_topic(
            Path(args.db_path),
            project_name=args.project_name,
            current_name=args.current_name,
            new_name=args.new_name,
        )
        print(f"Renamed topic '{args.current_name}' to '{args.new_name}' as row {topic_id}")
        return 0

    if args.command == "assign-topic":
        assign_topic_to_video(
            Path(args.db_path),
            video_id=args.video_id,
            topic_name=args.topic,
            assignment_type=args.assignment_type,
            assignment_source="manual",
        )
        print(f"Assigned topic '{args.topic}' to video {args.video_id} as {args.assignment_type}")
        return 0

    if args.command == "show-video-topics":
        rows = get_video_topic_assignments(Path(args.db_path), video_id=args.video_id)
        header = rows[0]
        print(f"Video: {header['youtube_video_id']} | {header['video_title']}")
        assignments = [row for row in rows if row["topic_name"] is not None]
        if not assignments:
            print("(no topic assignments)")
            return 0
        for row in assignments:
            print(f"- {row['assignment_type']} | {row['assignment_source']} | {row['topic_name']}")
        return 0

    if args.command == "create-subtopic":
        subtopic_id = create_subtopic(
            Path(args.db_path),
            topic_name=args.topic,
            subtopic_name=args.name,
            description=args.description,
        )
        print(f"Stored subtopic '{args.name}' under topic '{args.topic}' as row {subtopic_id}")
        return 0

    if args.command == "list-subtopics":
        for row in list_subtopics(Path(args.db_path), topic_name=args.topic):
            print(
                " | ".join(
                    [
                        row["topic_name"],
                        row["name"],
                        row["description"] or "",
                        f"assignments={row['assignment_count']}",
                    ]
                )
            )
        return 0

    if args.command == "rename-subtopic":
        subtopic_id = rename_subtopic(
            Path(args.db_path),
            topic_name=args.topic,
            current_name=args.current_name,
            new_name=args.new_name,
        )
        print(f"Renamed subtopic '{args.current_name}' to '{args.new_name}' as row {subtopic_id}")
        return 0

    if args.command == "assign-subtopic":
        assign_subtopic_to_video(
            Path(args.db_path),
            video_id=args.video_id,
            subtopic_name=args.subtopic,
            assignment_source="manual",
        )
        print(f"Assigned subtopic '{args.subtopic}' to video {args.video_id}")
        return 0

    if args.command == "show-video-subtopics":
        rows = get_video_subtopic_assignments(Path(args.db_path), video_id=args.video_id)
        header = rows[0]
        print(f"Video: {header['youtube_video_id']} | {header['video_title']}")
        assignments = [row for row in rows if row["subtopic_name"] is not None]
        if not assignments:
            print("(no subtopic assignments)")
            return 0
        for row in assignments:
            print(f"- {row['topic_name']} | {row['subtopic_name']} | {row['assignment_source']}")
        return 0

    if args.command == "create-comparison-group":
        group_id = create_comparison_group(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            group_name=args.name,
            description=args.description,
            target_size=args.target_size,
        )
        print(f"Stored comparison group '{args.name}' under subtopic '{args.subtopic}' as row {group_id}")
        return 0

    if args.command == "list-comparison-groups":
        for row in list_comparison_groups(Path(args.db_path), subtopic_name=args.subtopic):
            print(
                " | ".join(
                    [
                        row["topic_name"],
                        row["subtopic_name"],
                        row["name"],
                        row["description"] or "",
                        f"target_size={row['target_size'] if row['target_size'] is not None else ''}",
                        f"members={row['member_count']}",
                    ]
                )
            )
        return 0

    if args.command == "rename-comparison-group":
        group_id = rename_comparison_group(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            current_name=args.current_name,
            new_name=args.new_name,
        )
        print(f"Renamed comparison group '{args.current_name}' to '{args.new_name}' as row {group_id}")
        return 0

    if args.command == "add-video-to-comparison-group":
        add_video_to_comparison_group(
            Path(args.db_path),
            video_id=args.video_id,
            group_name=args.group,
        )
        print(f"Added video {args.video_id} to comparison group '{args.group}'")
        return 0

    if args.command == "remove-video-from-comparison-group":
        remove_video_from_comparison_group(
            Path(args.db_path),
            video_id=args.video_id,
            group_name=args.group,
        )
        print(f"Removed video {args.video_id} from comparison group '{args.group}'")
        return 0

    if args.command == "move-video-between-comparison-groups":
        move_video_between_comparison_groups(
            Path(args.db_path),
            video_id=args.video_id,
            from_group_name=args.from_group,
            to_group_name=args.to_group,
        )
        print(
            f"Moved video {args.video_id} from comparison group '{args.from_group}' to '{args.to_group}'"
        )
        return 0

    if args.command == "remove-video-topic-assignment":
        remove_topic_from_video(
            Path(args.db_path),
            video_id=args.video_id,
            topic_name=args.topic,
        )
        print(f"Removed topic '{args.topic}' from video {args.video_id}")
        return 0

    if args.command == "remove-video-subtopic-assignment":
        remove_subtopic_from_video(
            Path(args.db_path),
            video_id=args.video_id,
            subtopic_name=args.subtopic,
        )
        print(f"Removed subtopic '{args.subtopic}' from video {args.video_id}")
        return 0

    if args.command == "show-comparison-group":
        rows = get_comparison_group_details(Path(args.db_path), group_name=args.group)
        header = rows[0]
        print(
            " | ".join(
                [
                    f"Group: {header['group_name']}",
                    f"Topic: {header['topic_name']}",
                    f"Subtopic: {header['subtopic_name']}",
                    f"Target size: {header['target_size'] if header['target_size'] is not None else ''}",
                ]
            )
        )
        members = [row for row in rows if row["youtube_video_id"] is not None]
        if not members:
            print("(no member videos)")
            return 0
        for row in members:
            print(f"- {row['youtube_video_id']} | {row['published_at'] or ''} | {row['video_title']}")
        return 0

    if args.command == "fetch-group-transcripts":
        group = resolve_comparison_group(
            Path(args.db_path),
            group_name=getattr(args, "group", None),
            group_id=getattr(args, "group_id", None),
        )
        rows = list_group_videos(Path(args.db_path), group_id=group["id"])
        for row in rows:
            transcript = fetch_video_transcript(row["youtube_video_id"])
            upsert_video_transcript(
                Path(args.db_path),
                youtube_video_id=row["youtube_video_id"],
                transcript=transcript,
            )
            print(f"{row['youtube_video_id']} | {transcript.status} | {transcript.source or ''} | {transcript.language_code or ''}")
        if not rows:
            print("(no member videos)")
        return 0

    if args.command == "show-group-transcripts":
        group = resolve_comparison_group(
            Path(args.db_path),
            group_name=getattr(args, "group", None),
            group_id=getattr(args, "group_id", None),
        )
        rows = get_group_transcript_statuses(Path(args.db_path), group_id=group["id"])
        print(f"Group: {group['name']} | id={group['id']}")
        if not rows:
            print("(no member videos)")
            return 0
        for row in rows:
            print(
                f"- {row['youtube_video_id']} | {row['transcript_status'] or 'missing'} | "
                f"{row['transcript_source'] or ''} | {row['language_code'] or ''} | chars={row['transcript_chars'] or 0}"
            )
        return 0

    if args.command == "process-group-videos":
        group = resolve_comparison_group(
            Path(args.db_path),
            group_name=getattr(args, "group", None),
            group_id=getattr(args, "group_id", None),
        )
        rows = get_group_transcripts_for_processing(Path(args.db_path), group_id=group["id"])
        if not rows:
            print("(no member videos)")
            return 0
        for row in rows:
            transcript = None
            if row["transcript_status"] is not None:
                transcript = TranscriptRecord(
                    status=row["transcript_status"],
                    source=row["transcript_source"],
                    language_code=row["language_code"],
                    text=row["transcript_text"],
                    detail=row["transcript_detail"],
                )
            artifact, chunks = process_transcript_record(transcript)
            upsert_processed_video_artifacts(
                Path(args.db_path),
                youtube_video_id=row["youtube_video_id"],
                artifact=artifact,
                chunks=chunks,
            )
            print(
                f"{row['youtube_video_id']} | {artifact.processing_status} | "
                f"chunks={artifact.chunk_count} | summary_chars={len(artifact.summary_text or '')}"
            )
        return 0

    if args.command == "show-group-processing":
        group = resolve_comparison_group(
            Path(args.db_path),
            group_name=getattr(args, "group", None),
            group_id=getattr(args, "group_id", None),
        )
        rows = get_group_processed_video_results(Path(args.db_path), group_id=group["id"])
        print(f"Group: {group['name']} | id={group['id']}")
        if not rows:
            print("(no member videos)")
            return 0
        for row in rows:
            print(
                f"- {row['youtube_video_id']} | {row['processing_status'] or 'unprocessed'} | "
                f"chunks={row['chunk_count'] or 0} | chars={row['transcript_char_count'] or 0} | "
                f"summary={(row['summary_text'] or '')[:80]}"
            )
        return 0

    if args.command == "analyze-comparison-group":
        group = resolve_comparison_group(
            Path(args.db_path),
            group_name=getattr(args, "group", None),
            group_id=getattr(args, "group_id", None),
        )
        rows = get_group_processed_video_results(Path(args.db_path), group_id=group["id"])
        artifact = build_group_analysis(
            [
                GroupAnalysisInput(
                    youtube_video_id=row["youtube_video_id"],
                    video_title=row["video_title"],
                    processing_status=row["processing_status"],
                    summary_text=row["summary_text"],
                )
                for row in rows
            ]
        )
        upsert_group_analysis(Path(args.db_path), group_id=group["id"], artifact=artifact)
        print(
            f"Group: {group['name']} | id={group['id']} | processed={artifact.processed_video_count} | "
            f"skipped={artifact.skipped_video_count}"
        )
        if artifact.analysis_detail:
            print(artifact.analysis_detail)
        return 0

    if args.command == "show-group-analysis":
        group = resolve_comparison_group(
            Path(args.db_path),
            group_name=getattr(args, "group", None),
            group_id=getattr(args, "group_id", None),
        )
        row = get_group_analysis(Path(args.db_path), group_id=group["id"])
        print(f"Group: {group['name']} | id={group['id']}")
        if row is None or row["analysis_version"] is None:
            print("(no stored group analysis)")
            return 0
        print(
            f"version={row['analysis_version']} | processed={row['processed_video_count']} | "
            f"skipped={row['skipped_video_count']} | analyzed_at={row['analyzed_at']}"
        )
        if row["analysis_detail"]:
            print(row["analysis_detail"])
        print("shared_themes=")
        for item in json.loads(row["shared_themes_json"]):
            print(f"- {item['theme']} | videos={item['video_count']}")
        print("repeated_recommendations_or_claims=")
        for item in json.loads(row["repeated_recommendations_json"]):
            print(f"- {item['text']} | videos={item['video_count']}")
        details = json.loads(row["notable_differences_json"])
        print("notable_differences=")
        for item in details.get("videos", []):
            print(
                f"- {item['youtube_video_id']} | unique_themes={', '.join(item['unique_themes'])} | "
                f"findings={'; '.join(item['recommendations_or_claims'])}"
            )
        skipped = details.get("skipped_videos", [])
        if skipped:
            print("skipped_videos=")
            for item in skipped:
                print(f"- {item['youtube_video_id']} | {item['status']} | {item['video_title']}")
        return 0

    if args.command == "export-group-markdown":
        db_path = Path(args.db_path)
        group = resolve_comparison_group(
            db_path,
            group_name=getattr(args, "group", None),
            group_id=getattr(args, "group_id", None),
        )
        processed_rows = [dict(row) for row in get_group_processed_video_results(db_path, group_id=group["id"])]
        analysis_row = get_group_analysis(db_path, group_id=group["id"])
        export = build_group_markdown_export(
            group={"id": group["id"], "name": group["name"]},
            processed_rows=processed_rows,
            analysis_row=dict(analysis_row) if analysis_row is not None else None,
        )
        output_dir = Path(args.output_dir)
        written_paths = write_group_markdown_export(output_dir=output_dir, export=export)
        for item in export.files:
            record_markdown_export(
                db_path,
                group_id=group["id"],
                export_kind=item.export_kind,
                relative_path=item.relative_path,
                source_updated_at=item.source_updated_at,
            )
        print(f"Group: {group['name']} | id={group['id']} | exported_files={len(written_paths)}")
        for item in get_markdown_exports(db_path, group_id=group["id"]):
            print(f"- {item['export_kind']} | {item['relative_path']}")
        return 0

    if args.command == "search-library":
        rows, mode = search_library(
            Path(args.db_path),
            query=args.query,
            group_name=args.group,
            topic_name=args.topic,
            subtopic_name=args.subtopic,
            limit=args.limit,
        )
        print(f"Search mode: {mode} | results={len(rows)}")
        for row in rows:
            label = row.video_title or row.group_name or "(unknown source)"
            scope_parts = [part for part in [row.group_name, row.topic_name, row.subtopic_name] if part]
            scope_text = f" | scope={' > '.join(scope_parts)}" if scope_parts else ""
            print(
                f"- {row.source_type} | {label}{scope_text}\n"
                f"  snippet: {row.snippet}"
            )
        return 0

    if args.command == "suggest-topics":
        db_path = Path(args.db_path)
        primary_channel = get_primary_channel(db_path)
        approved_topic_names = list_approved_topic_names(db_path)
        rows = list_videos_for_topic_suggestions(db_path, limit=args.limit)
        if not rows:
            print("(no stored videos)")
            return 0
        run_id = create_topic_suggestion_run(db_path, model_name=args.model, status="success")
        stored_count = 0
        for row in rows:
            suggestion = suggest_topics_for_video(
                project_name=primary_channel.title,
                approved_topic_names=approved_topic_names,
                youtube_video_id=row["youtube_video_id"],
                video_title=row["title"],
                video_description=row["description"],
                model=args.model,
            )
            stored_count += store_video_topic_suggestion(db_path, run_id=run_id, suggestion=suggestion)
            secondary_text = ", ".join(item.label for item in suggestion.secondary_topics) or "(none)"
            print(
                f"{suggestion.youtube_video_id} | primary={suggestion.primary_topic.label} | secondary={secondary_text}"
            )
        print(f"Stored {stored_count} suggestion rows in run {run_id}")
        return 0

    if args.command == "suggest-subtopics":
        db_path = Path(args.db_path)
        primary_channel = get_primary_channel(db_path)
        approved_subtopics = [
            {"name": row["name"], "description": row["description"]}
            for row in list_approved_subtopics_for_topic(db_path, topic_name=args.topic)
        ]
        rows = list_videos_for_subtopic_suggestions(db_path, topic_name=args.topic, limit=args.limit)
        if not rows:
            print("(no stored videos for topic)")
            return 0
        run_id = create_subtopic_suggestion_run(db_path, topic_name=args.topic, model_name=args.model, status="success")
        stored_count = 0
        for row in rows:
            suggestion = suggest_subtopics_for_video(
                project_name=primary_channel.title,
                broad_topic_name=args.topic,
                approved_subtopics=approved_subtopics,
                youtube_video_id=row["youtube_video_id"],
                video_title=row["title"],
                video_description=row["description"],
                model=args.model,
            )
            stored_count += store_video_subtopic_suggestion(
                db_path,
                run_id=run_id,
                topic_name=args.topic,
                suggestion=suggestion,
            )
            status_text = "reuse-existing" if suggestion.primary_subtopic.reuse_existing else "new-label"
            print(
                f"{suggestion.youtube_video_id} | topic={args.topic} | subtopic={suggestion.primary_subtopic.label} | {status_text}"
            )
        print(
            f"Stored {stored_count} review-only subtopic suggestions in run {run_id}. No subtopic assignments were applied."
        )
        return 0

    if args.command == "suggest-comparison-groups":
        db_path = Path(args.db_path)
        primary_channel = get_primary_channel(db_path)
        approved_groups = [
            {"name": row["name"], "description": row["description"], "member_count": row["member_count"]}
            for row in list_approved_comparison_groups_for_subtopic(db_path, subtopic_name=args.subtopic)
        ]
        rows = list_videos_for_comparison_group_suggestions(db_path, subtopic_name=args.subtopic, limit=args.limit)
        if not rows:
            print("(no stored videos for subtopic)")
            return 0
        run_id = create_comparison_group_suggestion_run(
            db_path,
            subtopic_name=args.subtopic,
            model_name=args.model,
            status="success",
        )
        stored_count = 0
        for row in rows:
            suggestion = suggest_comparison_groups_for_video(
                project_name=primary_channel.title,
                broad_topic_name=row["topic_name"],
                subtopic_name=args.subtopic,
                approved_comparison_groups=approved_groups,
                youtube_video_id=row["youtube_video_id"],
                video_title=row["title"],
                video_description=row["description"],
                model=args.model,
            )
            stored_count += store_video_comparison_group_suggestion(
                db_path,
                run_id=run_id,
                subtopic_name=args.subtopic,
                suggestion=suggestion,
            )
            status_text = "reuse-existing" if suggestion.primary_comparison_group.reuse_existing else "new-label"
            print(
                f"{suggestion.youtube_video_id} | subtopic={args.subtopic} | comparison-group={suggestion.primary_comparison_group.label} | {status_text}"
            )
        print(
            f"Stored {stored_count} review-only comparison-group suggestions in run {run_id}. No comparison-group memberships were applied."
        )
        return 0

    if args.command == "list-topic-suggestions":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        rows = list_video_topic_suggestions(Path(args.db_path), status=args.status, run_id=args.run_id)
        if not rows:
            print("(no topic suggestions)")
            return 0
        print(f"Run: {resolved_run_id}")
        for row in rows:
            reuse_text = "reuse-existing" if row["reuse_existing"] else "new-label"
            print(
                f"{row['youtube_video_id']} | {row['assignment_type']} | {row['suggested_label']} | {row['label_status']} | {reuse_text}\n"
                f"  {row['video_title']}\n"
                f"  rationale: {row['rationale']}"
            )
        return 0

    if args.command == "list-subtopic-suggestions":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        rows = list_video_subtopic_suggestions(
            Path(args.db_path),
            topic_name=args.topic,
            status=args.status,
            run_id=args.run_id,
        )
        if not rows:
            print("(no subtopic suggestions)")
            return 0
        print(f"Run: {resolved_run_id} | Topic: {args.topic}")
        for row in rows:
            reuse_text = "reuse-existing" if row["reuse_existing"] else "new-label"
            print(
                f"{row['youtube_video_id']} | {row['assignment_type']} | {row['suggested_label']} | {row['label_status']} | {reuse_text}\n"
                f"  {row['video_title']}\n"
                f"  rationale: {row['rationale']}"
            )
        return 0

    if args.command == "list-comparison-group-suggestions":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        rows = list_video_comparison_group_suggestions(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            status=args.status,
            run_id=args.run_id,
        )
        if not rows:
            print("(no comparison-group suggestions)")
            return 0
        print(f"Run: {resolved_run_id} | Subtopic: {args.subtopic}")
        for row in rows:
            reuse_text = "reuse-existing" if row["reuse_existing"] else "new-label"
            print(
                f"{row['youtube_video_id']} | {row['suggested_label']} | {row['label_status']} | {reuse_text}\n"
                f"  {row['video_title']}\n"
                f"  rationale: {row['rationale']}"
            )
        return 0

    if args.command == "list-topic-suggestion-runs":
        rows = list_topic_suggestion_runs(Path(args.db_path))
        if not rows:
            print("(no topic suggestion runs)")
            return 0
        for row in rows:
            print(
                f"run={row['id']} | created_at={row['created_at']} | model={row['model_name']} | run_status={row['run_status']} | "
                f"labels={row['label_count']} | suggestions={row['suggestion_row_count']} | "
                f"pending={row['pending_label_count']} | approved={row['approved_label_count']} | "
                f"rejected={row['rejected_label_count']} | superseded={row['superseded_label_count']}"
            )
        return 0

    if args.command == "summarize-topic-suggestion-labels":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        rows = summarize_topic_suggestion_labels(Path(args.db_path), status=args.status, run_id=args.run_id)
        if not rows:
            print("(no suggested labels)")
            return 0
        print(f"Run: {resolved_run_id}")
        for row in rows:
            print(
                f"{row['name']} | status={row['status']} | suggestions={row['suggestion_count']} | "
                f"primary={row['primary_count'] or 0} | secondary={row['secondary_count'] or 0}"
            )
        return 0

    if args.command == "summarize-subtopic-suggestion-labels":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        rows = summarize_subtopic_suggestion_labels(
            Path(args.db_path),
            topic_name=args.topic,
            status=args.status,
            run_id=args.run_id,
        )
        if not rows:
            print("(no suggested subtopic labels)")
            return 0
        print(f"Run: {resolved_run_id} | Topic: {args.topic}")
        for row in rows:
            print(
                f"{row['name']} | status={row['status']} | suggestions={row['suggestion_count']} | "
                f"reused={row['reuse_existing_count'] or 0}"
            )
        return 0

    if args.command == "summarize-comparison-group-suggestion-labels":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        rows = summarize_comparison_group_suggestion_labels(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            status=args.status,
            run_id=args.run_id,
        )
        if not rows:
            print("(no suggested comparison-group labels)")
            return 0
        print(f"Run: {resolved_run_id} | Subtopic: {args.subtopic}")
        for row in rows:
            print(
                f"{row['name']} | status={row['status']} | suggestions={row['suggestion_count']} | "
                f"reused={row['reuse_existing_count'] or 0}"
            )
        return 0

    if args.command == "review-topic-suggestions":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        if resolved_run_id is None:
            print("(no topic suggestion runs)")
            return 0
        rows = get_topic_suggestion_review_rows(
            Path(args.db_path),
            run_id=resolved_run_id,
            status="pending",
            sample_limit=args.sample_limit,
        )
        if not rows:
            print(f"Latest suggestion run {resolved_run_id} contains 0 pending video suggestions across 0 labels")
            print("No pending suggested labels remain for review.")
            print("Approved labels still need to be applied to videos before downstream subtopic suggestions will see them.")
            print(
                f"Suggested next actions: run list-topic-suggestions --db-path {args.db_path} --run-id {resolved_run_id}"
            )
            print(
                f"Then apply approved labels with: {CLI_MODULE_PREFIX} bulk-apply-topic-suggestion-label --db-path {args.db_path} --run-id {resolved_run_id} --label \"<approved label>\""
            )
            return 0

        labels: list[dict[str, object]] = []
        current_label: dict[str, object] | None = None
        total_pending = 0
        for row in rows:
            if current_label is None or current_label["name"] != row["name"]:
                current_label = {
                    "name": row["name"],
                    "video_count": int(row["video_count"] or 0),
                    "primary_count": int(row["primary_count"] or 0),
                    "secondary_count": int(row["secondary_count"] or 0),
                    "approved_topic_exists": bool(row["approved_topic_exists"]),
                    "samples": [],
                }
                labels.append(current_label)
                total_pending += current_label["video_count"]
            if row["youtube_video_id"] is not None:
                current_label["samples"].append(
                    {
                        "youtube_video_id": row["youtube_video_id"],
                        "video_title": row["video_title"],
                        "assignment_type": row["assignment_type"],
                    }
                )

        run_descriptor = f"Run {resolved_run_id}" if args.run_id else f"Latest suggestion run {resolved_run_id}"
        print(f"{run_descriptor} contains {total_pending} pending video suggestions across {len(labels)} labels")
        print("Review each label below before applying anything.")
        for label in labels:
            topic_status = "reuses existing approved topic" if label["approved_topic_exists"] else "new suggested topic"
            print()
            print(
                f"Label: {label['name']} | videos={label['video_count']} | primary={label['primary_count']} | "
                f"secondary={label['secondary_count']} | {topic_status}"
            )
            print("Example videos:")
            for sample in label["samples"]:
                print(f"- [{sample['assignment_type']}] {sample['video_title']} ({sample['youtube_video_id']})")
            print("Suggested next actions:")
            print(
                f"- approve: {CLI_MODULE_PREFIX} approve-topic-suggestion-label --db-path {args.db_path} --label \"{label['name']}\" --run-id {resolved_run_id}"
            )
            print(
                f"- reject: {CLI_MODULE_PREFIX} reject-topic-suggestion-label --db-path {args.db_path} --label \"{label['name']}\" --run-id {resolved_run_id}"
            )
            print(
                f"- rename: {CLI_MODULE_PREFIX} rename-topic-suggestion-label --db-path {args.db_path} --current-name \"{label['name']}\" --new-name \"<new label>\" --run-id {resolved_run_id}"
            )
        return 0

    if args.command == "review-subtopic-suggestions":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        if resolved_run_id is None:
            print("(no topic suggestion runs)")
            return 0
        rows = get_subtopic_suggestion_review_rows(
            Path(args.db_path),
            topic_name=args.topic,
            run_id=resolved_run_id,
            status="pending",
            sample_limit=args.sample_limit,
        )
        if not rows:
            print(f"Latest suggestion run {resolved_run_id} contains 0 pending subtopic suggestions for topic {args.topic}")
            print("No pending suggested subtopic labels remain for review.")
            print(
                f"Suggested next actions: run list-subtopic-suggestions --db-path {args.db_path} --topic \"{args.topic}\" --run-id {resolved_run_id}"
            )
            return 0

        labels: list[dict[str, object]] = []
        current_label: dict[str, object] | None = None
        total_pending = 0
        for row in rows:
            if current_label is None or current_label["name"] != row["name"]:
                current_label = {
                    "name": row["name"],
                    "video_count": int(row["video_count"] or 0),
                    "approved_subtopic_exists": bool(row["approved_subtopic_exists"]),
                    "samples": [],
                }
                labels.append(current_label)
                total_pending += current_label["video_count"]
            if row["youtube_video_id"] is not None:
                current_label["samples"].append(
                    {
                        "youtube_video_id": row["youtube_video_id"],
                        "video_title": row["video_title"],
                    }
                )

        run_descriptor = f"Run {resolved_run_id}" if args.run_id else f"Latest suggestion run {resolved_run_id}"
        print(f"{run_descriptor} contains {total_pending} pending subtopic suggestions across {len(labels)} labels for topic {args.topic}")
        print("Review each suggested subtopic label below before applying anything.")
        for label in labels:
            subtopic_status = "reuses existing approved subtopic" if label["approved_subtopic_exists"] else "new suggested subtopic"
            print()
            print(f"Label: {label['name']} | videos={label['video_count']} | {subtopic_status}")
            print("Example videos:")
            for sample in label["samples"]:
                print(f"- {sample['video_title']} ({sample['youtube_video_id']})")
            print("Suggested next actions:")
            print(
                f"- approve: {CLI_MODULE_PREFIX} approve-subtopic-suggestion-label --db-path {args.db_path} --topic \"{args.topic}\" --label \"{label['name']}\" --run-id {resolved_run_id}"
            )
            print(
                f"- reject: {CLI_MODULE_PREFIX} reject-subtopic-suggestion-label --db-path {args.db_path} --topic \"{args.topic}\" --label \"{label['name']}\" --run-id {resolved_run_id}"
            )
            print(
                f"- rename: {CLI_MODULE_PREFIX} rename-subtopic-suggestion-label --db-path {args.db_path} --topic \"{args.topic}\" --current-name \"{label['name']}\" --new-name \"<new label>\" --run-id {resolved_run_id}"
            )
        return 0

    if args.command == "review-comparison-group-suggestions":
        resolved_run_id = args.run_id or get_latest_topic_suggestion_run_id(Path(args.db_path))
        if resolved_run_id is None:
            print("(no topic suggestion runs)")
            return 0
        rows = get_comparison_group_suggestion_review_rows(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            run_id=resolved_run_id,
            status="pending",
            sample_limit=args.sample_limit,
        )
        if not rows:
            print(f"Latest suggestion run {resolved_run_id} contains 0 pending comparison-group suggestions for subtopic {args.subtopic}")
            print("No pending suggested comparison-group labels remain for review.")
            print(
                f"Suggested next actions: run list-comparison-group-suggestions --db-path {args.db_path} --subtopic \"{args.subtopic}\" --run-id {resolved_run_id}"
            )
            return 0

        labels: list[dict[str, object]] = []
        current_label: dict[str, object] | None = None
        total_pending = 0
        for row in rows:
            if current_label is None or current_label["name"] != row["name"]:
                current_label = {
                    "name": row["name"],
                    "video_count": int(row["video_count"] or 0),
                    "approved_group_exists": bool(row["approved_group_exists"]),
                    "samples": [],
                }
                labels.append(current_label)
                total_pending += current_label["video_count"]
            if row["youtube_video_id"] is not None:
                current_label["samples"].append(
                    {
                        "youtube_video_id": row["youtube_video_id"],
                        "video_title": row["video_title"],
                    }
                )

        run_descriptor = f"Run {resolved_run_id}" if args.run_id else f"Latest suggestion run {resolved_run_id}"
        print(f"{run_descriptor} contains {total_pending} pending comparison-group suggestions across {len(labels)} labels for subtopic {args.subtopic}")
        print("Review each suggested comparison-group label below before applying anything.")
        for label in labels:
            group_status = "reuses existing approved comparison group" if label["approved_group_exists"] else "new suggested comparison group"
            print()
            print(f"Label: {label['name']} | videos={label['video_count']} | {group_status}")
            print("Example videos:")
            for sample in label["samples"]:
                print(f"- {sample['video_title']} ({sample['youtube_video_id']})")
            print("Suggested next actions:")
            print(
                f"- approve: {CLI_MODULE_PREFIX} approve-comparison-group-suggestion-label --db-path {args.db_path} --subtopic \"{args.subtopic}\" --label \"{label['name']}\" --run-id {resolved_run_id}"
            )
            print(
                f"- reject: {CLI_MODULE_PREFIX} reject-comparison-group-suggestion-label --db-path {args.db_path} --subtopic \"{args.subtopic}\" --label \"{label['name']}\" --run-id {resolved_run_id}"
            )
            print(
                f"- rename: {CLI_MODULE_PREFIX} rename-comparison-group-suggestion-label --db-path {args.db_path} --subtopic \"{args.subtopic}\" --current-name \"{label['name']}\" --new-name \"<new label>\" --run-id {resolved_run_id}"
            )
        return 0

    if args.command == "approve-topic-suggestion-label":
        topic_id = approve_topic_suggestion_label(
            Path(args.db_path),
            suggested_label=args.label,
            approved_name=args.approved_name,
            run_id=args.run_id,
        )
        approved_label = args.approved_name or args.label
        print(
            f"Approved suggested label '{args.label}' as topic '{approved_label}' (row {topic_id})"
        )
        print(
            f"Next step: apply it to matching videos with {CLI_MODULE_PREFIX} bulk-apply-topic-suggestion-label --db-path {args.db_path} --run-id {args.run_id or '<latest>'} --label \"{args.label}\""
        )
        return 0

    if args.command == "approve-subtopic-suggestion-label":
        subtopic_id = approve_subtopic_suggestion_label(
            Path(args.db_path),
            topic_name=args.topic,
            suggested_label=args.label,
            approved_name=args.approved_name,
            run_id=args.run_id,
        )
        print(
            f"Approved suggested subtopic label '{args.label}' as subtopic '{args.approved_name or args.label}' under topic '{args.topic}' (row {subtopic_id})"
        )
        return 0

    if args.command == "approve-comparison-group-suggestion-label":
        group_id = approve_comparison_group_suggestion_label(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            suggested_label=args.label,
            approved_name=args.approved_name,
            run_id=args.run_id,
        )
        print(
            f"Approved suggested comparison-group label '{args.label}' as comparison group '{args.approved_name or args.label}' under subtopic '{args.subtopic}' (row {group_id})"
        )
        return 0

    if args.command == "reject-topic-suggestion-label":
        updated = reject_topic_suggestion_label(Path(args.db_path), suggested_label=args.label, run_id=args.run_id)
        print(f"Rejected suggested label '{args.label}' across {updated} label row(s)")
        return 0

    if args.command == "reject-subtopic-suggestion-label":
        updated = reject_subtopic_suggestion_label(
            Path(args.db_path),
            topic_name=args.topic,
            suggested_label=args.label,
            run_id=args.run_id,
        )
        print(f"Rejected suggested subtopic label '{args.label}' across {updated} label row(s)")
        return 0

    if args.command == "reject-comparison-group-suggestion-label":
        updated = reject_comparison_group_suggestion_label(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            suggested_label=args.label,
            run_id=args.run_id,
        )
        print(f"Rejected suggested comparison-group label '{args.label}' across {updated} label row(s)")
        return 0

    if args.command == "rename-topic-suggestion-label":
        label_id = rename_topic_suggestion_label(
            Path(args.db_path),
            current_name=args.current_name,
            new_name=args.new_name,
            run_id=args.run_id,
        )
        print(f"Renamed suggested label '{args.current_name}' to '{args.new_name}' as row {label_id}")
        return 0

    if args.command == "rename-subtopic-suggestion-label":
        label_id = rename_subtopic_suggestion_label(
            Path(args.db_path),
            topic_name=args.topic,
            current_name=args.current_name,
            new_name=args.new_name,
            run_id=args.run_id,
        )
        print(f"Renamed suggested subtopic label '{args.current_name}' to '{args.new_name}' as row {label_id}")
        return 0

    if args.command == "rename-comparison-group-suggestion-label":
        label_id = rename_comparison_group_suggestion_label(
            Path(args.db_path),
            subtopic_name=args.subtopic,
            current_name=args.current_name,
            new_name=args.new_name,
            run_id=args.run_id,
        )
        print(f"Renamed suggested comparison-group label '{args.current_name}' to '{args.new_name}' as row {label_id}")
        return 0

    if args.command == "apply-topic-suggestion":
        apply_topic_suggestion_to_video(
            Path(args.db_path),
            video_id=args.video_id,
            suggested_label=args.label,
            run_id=args.run_id,
        )
        print(f"Applied approved suggestion '{args.label}' to video {args.video_id}")
        return 0

    if args.command == "bulk-apply-topic-suggestion-label":
        matched, applied, skipped = bulk_apply_topic_suggestion_label(
            Path(args.db_path),
            suggested_label=args.label,
            run_id=args.run_id,
        )
        print(f"Matched {matched} suggestions, applied {applied}, skipped {skipped}")
        return 0

    if args.command == "supersede-stale-topic-suggestions":
        summary = supersede_stale_topic_suggestions(
            Path(args.db_path),
            keep_run_id=args.keep_run_id,
            suggested_label=args.label,
        )
        print(
            "Run kept active: {keep_run_id}; older runs affected: {older_runs_affected}; matched: {matched}; superseded: {superseded}; skipped: {skipped}".format(
                **summary,
            )
        )
        return 0

    if args.command == "discover":
        if not args.stub:
            parser.error("discover currently requires --stub (real LLM lands in slice 02)")
        run_id = run_discovery(
            Path(args.db_path),
            project_name=args.project_name,
            llm=stub_llm,
            model=STUB_MODEL,
            prompt_version=STUB_PROMPT_VERSION,
        )
        print(f"Discovery run {run_id} complete (model={STUB_MODEL})")
        return 0

    if args.command == "serve-review-ui":
        serve_review_ui(
            Path(args.db_path),
            host=args.host,
            port=args.port,
            sample_limit=args.sample_limit,
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
