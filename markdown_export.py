from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "item"


@dataclass(frozen=True)
class ExportedMarkdownFile:
    export_kind: str
    relative_path: str
    content: str
    source_updated_at: str | None


@dataclass(frozen=True)
class GroupMarkdownExport:
    files: list[ExportedMarkdownFile]


def _render_metadata(items: list[tuple[str, str | None]]) -> list[str]:
    lines = ["## Metadata", ""]
    for key, value in items:
        lines.append(f"- **{key}:** {value if value not in (None, '') else 'n/a'}")
    lines.append("")
    return lines


def build_group_markdown_export(*, group: dict, processed_rows: list[dict], analysis_row: dict | None) -> GroupMarkdownExport:
    group_slug = _slugify(group["name"])
    files: list[ExportedMarkdownFile] = []

    for row in processed_rows:
        video_slug = _slugify(row["video_title"])
        relative_path = f"{group_slug}/videos/{video_slug}--{row['youtube_video_id']}.md"
        lines = [
            f"# {row['video_title']}",
            "",
            *_render_metadata(
                [
                    ("comparison_group", group["name"]),
                    ("group_id", str(group["id"])),
                    ("youtube_video_id", row["youtube_video_id"]),
                    ("published_at", row.get("published_at")),
                    ("processing_status", row.get("processing_status") or "unprocessed"),
                    ("chunk_count", str(row.get("chunk_count") or 0)),
                    ("transcript_char_count", str(row.get("transcript_char_count") or 0)),
                    ("processed_at", row.get("processed_at")),
                ]
            ),
            "## Summary",
            "",
            (row.get("summary_text") or "No processed summary is stored for this video yet."),
            "",
        ]
        if row.get("processing_detail"):
            lines.extend(["## Processing detail", "", row["processing_detail"], ""])
        files.append(
            ExportedMarkdownFile(
                export_kind="video",
                relative_path=relative_path,
                content="\n".join(lines).rstrip() + "\n",
                source_updated_at=row.get("processed_at"),
            )
        )

    if analysis_row is not None and analysis_row.get("analysis_version") is not None:
        details = json.loads(analysis_row["notable_differences_json"])
        lines = [
            f"# {group['name']} group summary",
            "",
            *_render_metadata(
                [
                    ("comparison_group", group["name"]),
                    ("group_id", str(group["id"])),
                    ("analysis_version", analysis_row.get("analysis_version")),
                    ("processed_video_count", str(analysis_row.get("processed_video_count") or 0)),
                    ("skipped_video_count", str(analysis_row.get("skipped_video_count") or 0)),
                    ("analyzed_at", analysis_row.get("analyzed_at")),
                ]
            ),
        ]
        if analysis_row.get("analysis_detail"):
            lines.extend(["## Analysis detail", "", analysis_row["analysis_detail"], ""])

        lines.extend(["## Shared themes", ""])
        for item in json.loads(analysis_row["shared_themes_json"]):
            lines.append(f"- {item['theme']} ({item['video_count']} videos)")
        if lines[-1] == "":
            pass
        elif lines[-1].startswith("##"):
            lines.append("- None")
        lines.append("")

        lines.extend(["## Repeated recommendations or claims", ""])
        repeated = json.loads(analysis_row["repeated_recommendations_json"])
        if repeated:
            for item in repeated:
                lines.append(f"- {item['text']} ({item['video_count']} videos)")
        else:
            lines.append("- None")
        lines.append("")

        lines.extend(["## Notable differences", ""])
        videos = details.get("videos", [])
        if videos:
            for item in videos:
                lines.append(f"### {item['youtube_video_id']}")
                lines.append("")
                lines.append(f"- Unique themes: {', '.join(item.get('unique_themes', [])) or 'None'}")
                lines.append(
                    f"- Recommendations or claims: {'; '.join(item.get('recommendations_or_claims', [])) or 'None'}"
                )
                lines.append("")
        else:
            lines.extend(["- None", ""])

        skipped = details.get("skipped_videos", [])
        lines.extend(["## Skipped videos", ""])
        if skipped:
            for item in skipped:
                lines.append(f"- {item['youtube_video_id']} | {item['status']} | {item['video_title']}")
        else:
            lines.append("- None")
        lines.append("")

        files.append(
            ExportedMarkdownFile(
                export_kind="group_summary",
                relative_path=f"{group_slug}/{group_slug}--group-summary.md",
                content="\n".join(lines).rstrip() + "\n",
                source_updated_at=analysis_row.get("analyzed_at"),
            )
        )

    return GroupMarkdownExport(files=files)


def write_group_markdown_export(*, output_dir: Path, export: GroupMarkdownExport) -> list[Path]:
    written: list[Path] = []
    for item in export.files:
        destination = output_dir / item.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(item.content, encoding="utf-8")
        written.append(destination)
    return written
