from __future__ import annotations

import json
import os
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


class ComparisonGroupSuggestionAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class SuggestedComparisonGroupLabel:
    label: str
    reuse_existing: bool
    rationale: str


@dataclass(frozen=True)
class VideoComparisonGroupSuggestion:
    youtube_video_id: str
    video_title: str
    broad_topic: str
    subtopic: str
    primary_comparison_group: SuggestedComparisonGroupLabel
    raw_response_json: str


def _get_openai_client() -> Any:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ComparisonGroupSuggestionAIError("OPENAI_API_KEY is required for AI comparison-group suggestions")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ComparisonGroupSuggestionAIError("openai package is required for AI comparison-group suggestions") from exc
    return OpenAI(api_key=api_key)


def _build_prompt(
    *,
    project_name: str,
    broad_topic_name: str,
    subtopic_name: str,
    approved_comparison_groups: list[dict[str, str | int | None]],
    video_title: str,
    video_description: str | None,
) -> str:
    approved_text = (
        "\n".join(
            f"- {row['name']}"
            + (f": {row['description']}" if row.get("description") else "")
            + (f" (members={row['member_count']})" if row.get("member_count") is not None else "")
            for row in approved_comparison_groups
        )
        if approved_comparison_groups
        else "- (none yet)"
    )
    description_text = (video_description or "").strip() or "(no description)"
    return (
        "You are suggesting a single review-only comparison group for one YouTube video within an already approved subtopic.\n"
        "Use only the provided video title and description metadata. Do not infer from transcripts, channel-wide context, comments, or outside knowledge.\n"
        "Stay inside the given broad topic and approved subtopic. Do not suggest a different broad topic or subtopic.\n"
        "Return exactly one primary comparison group and no secondary groups.\n"
        "Prefer reusing an existing approved comparison group when it is a strong, natural fit for this video's subject matter within the chosen subtopic.\n"
        "If no approved comparison group is a strong fit, propose one new concrete comparison group that is specific enough to compare multiple similar videos inside the subtopic.\n"
        "Avoid vague catch-all labels, episode-specific phrasing, dates, guest names, format labels, and audience labels.\n"
        "When you reuse an existing label, set reuse_existing to true and copy the label exactly.\n"
        "When you create a new label, set reuse_existing to false.\n"
        "Do not output empty strings. Keep rationale brief and specific.\n\n"
        f"Project: {project_name}\n"
        f"Approved broad topic: {broad_topic_name}\n"
        f"Approved subtopic: {subtopic_name}\n"
        f"Approved comparison groups within this subtopic:\n{approved_text}\n\n"
        f"Video title: {video_title}\n"
        f"Video description: {description_text}\n"
    )


def _response_schema() -> dict[str, object]:
    label_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {"type": "string"},
            "reuse_existing": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["label", "reuse_existing", "rationale"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"primary_comparison_group": label_schema},
        "required": ["primary_comparison_group"],
    }


def _canonicalize_label(label: str) -> str:
    words = [word for word in label.casefold().replace("&", " and ").replace("/", " ").split() if word]
    return " ".join(words)


def _resolve_reusable_label(label: str, approved_group_names: list[str]) -> tuple[str, bool]:
    cleaned_label = " ".join(label.split()).strip()
    canonical_label = _canonicalize_label(cleaned_label)

    approved_by_canonical = {
        _canonicalize_label(name): name
        for name in approved_group_names
        if str(name).strip()
    }
    if canonical_label in approved_by_canonical:
        return approved_by_canonical[canonical_label], True

    best_match: str | None = None
    best_score = 0.0
    for approved_name in approved_group_names:
        candidate = " ".join(str(approved_name).split()).strip()
        if not candidate:
            continue
        score = SequenceMatcher(None, canonical_label, _canonicalize_label(candidate)).ratio()
        if score > best_score:
            best_match = candidate
            best_score = score
    if best_match and best_score >= 0.9:
        return best_match, True
    return cleaned_label, False


def _normalize_label(raw: dict[str, object], *, approved_group_names: list[str]) -> SuggestedComparisonGroupLabel:
    label = " ".join(str(raw.get("label", "")).split()).strip()
    rationale = " ".join(str(raw.get("rationale", "")).split()).strip()
    reuse_existing = bool(raw.get("reuse_existing", False))
    if not label:
        raise ComparisonGroupSuggestionAIError("AI returned an empty comparison-group label")
    if not rationale:
        raise ComparisonGroupSuggestionAIError("AI returned an empty rationale")
    resolved_label, inferred_reuse_existing = _resolve_reusable_label(label, approved_group_names)
    return SuggestedComparisonGroupLabel(
        label=resolved_label,
        reuse_existing=reuse_existing or inferred_reuse_existing,
        rationale=rationale,
    )


def suggest_comparison_groups_for_video(
    *,
    project_name: str,
    broad_topic_name: str,
    subtopic_name: str,
    approved_comparison_groups: list[dict[str, str | int | None]],
    youtube_video_id: str,
    video_title: str,
    video_description: str | None,
    model: str = "gpt-4.1-mini",
) -> VideoComparisonGroupSuggestion:
    client = _get_openai_client()
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Be conservative and precise. Reuse an approved comparison group only when it is a strong fit within the chosen subtopic, otherwise propose one concrete reusable comparison group.",
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": _build_prompt(
                            project_name=project_name,
                            broad_topic_name=broad_topic_name,
                            subtopic_name=subtopic_name,
                            approved_comparison_groups=approved_comparison_groups,
                            video_title=video_title,
                            video_description=video_description,
                        ),
                    }
                ],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "video_comparison_group_suggestion",
                "schema": _response_schema(),
                "strict": True,
            }
        },
    )

    raw_text = getattr(response, "output_text", "") or ""
    if not raw_text.strip():
        raise ComparisonGroupSuggestionAIError("AI returned an empty structured response")
    payload = json.loads(raw_text)
    approved_group_names = [str(row.get("name", "")).strip() for row in approved_comparison_groups if str(row.get("name", "")).strip()]
    primary_comparison_group = _normalize_label(
        payload["primary_comparison_group"],
        approved_group_names=approved_group_names,
    )
    return VideoComparisonGroupSuggestion(
        youtube_video_id=youtube_video_id,
        video_title=video_title,
        broad_topic=broad_topic_name,
        subtopic=subtopic_name,
        primary_comparison_group=primary_comparison_group,
        raw_response_json=json.dumps(payload, indent=2, sort_keys=True),
    )
