from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol


class SubtopicSuggestionAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class SuggestedSubtopicLabel:
    label: str
    assignment_type: str
    reuse_existing: bool
    rationale: str


@dataclass(frozen=True)
class VideoSubtopicSuggestion:
    youtube_video_id: str
    video_title: str
    broad_topic: str
    primary_subtopic: SuggestedSubtopicLabel
    raw_response_json: str


class SubtopicSuggestionRow(Protocol):
    youtube_video_id: str
    title: str
    description: str | None


def _get_openai_client() -> Any:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SubtopicSuggestionAIError("OPENAI_API_KEY is required for AI subtopic suggestions")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SubtopicSuggestionAIError("openai package is required for AI subtopic suggestions") from exc
    return OpenAI(api_key=api_key)


def _build_prompt(
    *,
    project_name: str,
    broad_topic_name: str,
    approved_subtopics: list[dict[str, str | None]],
    video_title: str,
    video_description: str | None,
) -> str:
    approved_text = (
        "\n".join(
            f"- {row['name']}" + (f": {row['description']}" if row.get('description') else "")
            for row in approved_subtopics
        )
        if approved_subtopics
        else "- (none yet)"
    )
    description_text = (video_description or "").strip() or "(no description)"
    return (
        "You are suggesting a single review-only subtopic for one YouTube video within an already approved broad topic.\n"
        "Use only the provided video title and description metadata. Do not infer from transcripts, channel-wide context, comments, or outside knowledge.\n"
        "Stay inside the given broad topic. Do not suggest a different broad topic.\n"
        "Return exactly one primary subtopic and no secondary subtopics.\n"
        "Prefer reusing an existing approved subtopic when it is a strong, natural fit for this video's subject matter.\n"
        "If no approved subtopic is a strong fit, propose one new concrete subtopic only when it is likely to group at least 5 videos in this broad topic.\n"
        "Do not create one-off niche subtopics for a single episode or 1-2 videos; prefer a broader reusable label instead.\n"
        "Avoid vague catch-all labels, episode-specific phrasing, format labels, audience labels, and tags.\n"
        "When you reuse an existing label, set reuse_existing to true and copy the label exactly.\n"
        "When you create a new label, set reuse_existing to false.\n"
        "Do not output empty strings. Keep rationale brief and specific.\n\n"
        f"Project: {project_name}\n"
        f"Approved broad topic: {broad_topic_name}\n"
        f"Approved subtopics within this broad topic:\n{approved_text}\n\n"
        f"Video title: {video_title}\n"
        f"Video description: {description_text}\n"
    )


def _response_schema() -> dict[str, object]:
    label_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {"type": "string"},
            "assignment_type": {"type": "string", "enum": ["primary"]},
            "reuse_existing": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["label", "assignment_type", "reuse_existing", "rationale"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"primary_subtopic": label_schema},
        "required": ["primary_subtopic"],
    }


def _normalize_label(raw: dict[str, object], *, expected_assignment_type: str) -> SuggestedSubtopicLabel:
    label = " ".join(str(raw.get("label", "")).split()).strip()
    rationale = " ".join(str(raw.get("rationale", "")).split()).strip()
    assignment_type = str(raw.get("assignment_type", "")).strip()
    reuse_existing = bool(raw.get("reuse_existing", False))
    if not label:
        raise SubtopicSuggestionAIError("AI returned an empty subtopic label")
    if assignment_type != expected_assignment_type:
        raise SubtopicSuggestionAIError(
            f"AI returned invalid assignment_type {assignment_type!r} for {expected_assignment_type} subtopic"
        )
    if not rationale:
        raise SubtopicSuggestionAIError("AI returned an empty rationale")
    return SuggestedSubtopicLabel(
        label=label,
        assignment_type=assignment_type,
        reuse_existing=reuse_existing,
        rationale=rationale,
    )


def suggest_subtopics_for_video(
    *,
    project_name: str,
    broad_topic_name: str,
    approved_subtopics: list[dict[str, str | None]],
    youtube_video_id: str,
    video_title: str,
    video_description: str | None,
    model: str = "gpt-4.1-mini",
) -> VideoSubtopicSuggestion:
    client = _get_openai_client()
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Be conservative and precise. Subtopics are research clusters, not tags. Reuse an approved subtopic when it is a strong fit; only propose a new subtopic when it is broad enough to plausibly contain at least 5 videos in the given broad topic.",
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
                            approved_subtopics=approved_subtopics,
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
                "name": "video_subtopic_suggestion",
                "schema": _response_schema(),
                "strict": True,
            }
        },
    )

    raw_text = getattr(response, "output_text", "") or ""
    if not raw_text.strip():
        raise SubtopicSuggestionAIError("AI returned an empty structured response")
    payload = json.loads(raw_text)
    primary_subtopic = _normalize_label(payload["primary_subtopic"], expected_assignment_type="primary")
    return VideoSubtopicSuggestion(
        youtube_video_id=youtube_video_id,
        video_title=video_title,
        broad_topic=broad_topic_name,
        primary_subtopic=primary_subtopic,
        raw_response_json=json.dumps(payload, indent=2, sort_keys=True),
    )
