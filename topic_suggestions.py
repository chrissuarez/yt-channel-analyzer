from __future__ import annotations

import json
import os
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Protocol


class TopicSuggestionAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class SuggestedTopicLabel:
    label: str
    assignment_type: str
    reuse_existing: bool
    rationale: str


@dataclass(frozen=True)
class VideoTopicSuggestion:
    youtube_video_id: str
    video_title: str
    primary_topic: SuggestedTopicLabel
    secondary_topics: tuple[SuggestedTopicLabel, ...]
    raw_response_json: str


class TopicSuggestionRow(Protocol):
    youtube_video_id: str
    title: str
    description: str | None


def _get_openai_client() -> Any:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise TopicSuggestionAIError("OPENAI_API_KEY is required for AI topic suggestions")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise TopicSuggestionAIError("openai package is required for AI topic suggestions") from exc
    return OpenAI(api_key=api_key)


def _build_prompt(*, project_name: str, approved_topic_names: list[str], video_title: str, video_description: str | None) -> str:
    approved_labels = approved_topic_names or []
    approved_text = "\n".join(f"- {name}" for name in approved_labels) if approved_labels else "- (none yet)"
    description_text = (video_description or "").strip() or "(no description)"
    return (
        "You are assigning broad YouTube content topics for a single project.\n"
        "Use only the provided video title and description metadata. Do not infer from transcripts, channel-wide context, comments, or outside knowledge.\n"
        "Suggest broad topics only. No subtopics, tags, tones, formats, or audience labels.\n"
        "Return exactly one primary topic and zero or one secondary topic.\n"
        "The primary topic must be the single best broad subject-matter bucket for the video.\n"
        "A secondary topic is optional and should usually be empty. Only include one when a second broad subject is clearly central to the video and explicitly supported by the title or description.\n"
        "Do not add a secondary topic for loose overlap, background context, general wellbeing framing, or because multiple approved labels are somewhat related.\n"
        "Prefer reusing an existing approved project topic label only when it is a strong, natural fit. Reuse strong existing broad labels aggressively when the new idea is just a close wording variant.\n"
        "If existing approved labels are only a partial fit, too vague, or less precise than the actual subject matter, introduce a new concrete broad-topic label instead.\n"
        "Strongly disfavour reusing vague or catch-all labels such as 'Evergreen' unless the metadata clearly makes that the best available broad topic.\n"
        "Keep labels short, broad, concrete, and reusable across many videos. Avoid niche phrases, episode titles, overly specific formats, and vague catch-all labels when a clearer subject label is available.\n"
        "At the broad-topic level, prefer reusable umbrella subjects like Health & Wellness, Politics, Economics, Psychology, Artificial Intelligence, or Cryptocurrency over narrower constructions.\n"
        "Avoid subtopic-like labels such as Law and Government Surveillance, Health Science, Human Behavior, Personal Development, Biohacking, Mindset, Productivity Systems, or similarly narrow compounds unless the project already uses that exact broad label and it is clearly intended as a broad bucket.\n"
        "If you are tempted to output a narrow health-family label such as Health, Health Science, Health and Fitness, Longevity, Nutrition, Exercise, or Biohacking, prefer the broader reusable label Health & Wellness unless an existing approved label is an even better exact fit.\n"
        "A secondary topic should be rare. Include one only when omitting it would lose a clearly co-equal subject that is central for most of the video, not just a lens, consequence, or adjacent theme.\n"
        "When you reuse an existing label, set reuse_existing to true and copy the label exactly.\n"
        "When you create a new label, set reuse_existing to false.\n"
        "Do not output duplicate labels. Do not output empty strings.\n"
        "Your rationale should briefly explain why the primary fits best and why the secondary is omitted or truly central when present.\n\n"
        f"Project: {project_name}\n"
        f"Approved broad-topic labels:\n{approved_text}\n\n"
        f"Video title: {video_title}\n"
        f"Video description: {description_text}\n"
    )


def _response_schema() -> dict[str, object]:
    label_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {"type": "string"},
            "assignment_type": {"type": "string", "enum": ["primary", "secondary"]},
            "reuse_existing": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["label", "assignment_type", "reuse_existing", "rationale"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "primary_topic": label_schema,
            "secondary_topics": {
                "type": "array",
                "items": label_schema,
                "maxItems": 1,
            },
        },
        "required": ["primary_topic", "secondary_topics"],
    }


def _canonicalize_topic_label(label: str) -> str:
    words = [word for word in label.casefold().replace("&", " and ").replace("/", " ").split() if word]
    return " ".join(words)


_HEALTH_LABEL_ALIASES = {
    "health": "Health & Wellness",
    "health and wellness": "Health & Wellness",
    "health wellness": "Health & Wellness",
    "health and fitness": "Health & Wellness",
    "health fitness": "Health & Wellness",
    "health science": "Health & Wellness",
    "wellness": "Health & Wellness",
    "fitness": "Health & Wellness",
    "longevity": "Health & Wellness",
    "nutrition": "Health & Wellness",
    "exercise": "Health & Wellness",
    "biohacking": "Health & Wellness",
}

_SUBTOPIC_LIKE_LABEL_ALIASES = {
    "human behavior": "Psychology",
    "personal development": "Psychology",
    "mindset": "Psychology",
    "law and government surveillance": "Politics",
    "government surveillance": "Politics",
    "surveillance": "Politics",
}


def _resolve_reusable_label(label: str, approved_topic_names: list[str]) -> tuple[str, bool]:
    cleaned_label = " ".join(label.split()).strip()
    canonical_label = _canonicalize_topic_label(cleaned_label)

    approved_by_canonical = {
        _canonicalize_topic_label(name): name
        for name in approved_topic_names
        if str(name).strip()
    }

    if canonical_label in approved_by_canonical:
        return approved_by_canonical[canonical_label], True

    alias_target = _HEALTH_LABEL_ALIASES.get(canonical_label) or _SUBTOPIC_LIKE_LABEL_ALIASES.get(canonical_label)
    if alias_target:
        target_canonical = _canonicalize_topic_label(alias_target)
        if target_canonical in approved_by_canonical:
            return approved_by_canonical[target_canonical], True
        return alias_target, False

    best_match: str | None = None
    best_score = 0.0
    for approved_name in approved_topic_names:
        candidate = " ".join(str(approved_name).split()).strip()
        if not candidate:
            continue
        score = SequenceMatcher(None, canonical_label, _canonicalize_topic_label(candidate)).ratio()
        if score > best_score:
            best_match = candidate
            best_score = score
    if best_match and best_score >= 0.86:
        return best_match, True
    return cleaned_label, False


def _normalize_label(
    raw: dict[str, object], *, expected_assignment_type: str, approved_topic_names: list[str]
) -> SuggestedTopicLabel:
    label = " ".join(str(raw.get("label", "")).split()).strip()
    rationale = " ".join(str(raw.get("rationale", "")).split()).strip()
    assignment_type = str(raw.get("assignment_type", "")).strip()
    reuse_existing = bool(raw.get("reuse_existing", False))
    if not label:
        raise TopicSuggestionAIError("AI returned an empty topic label")
    if assignment_type != expected_assignment_type:
        raise TopicSuggestionAIError(
            f"AI returned invalid assignment_type {assignment_type!r} for {expected_assignment_type} topic"
        )
    if not rationale:
        raise TopicSuggestionAIError("AI returned an empty rationale")
    resolved_label, inferred_reuse_existing = _resolve_reusable_label(label, approved_topic_names)
    return SuggestedTopicLabel(
        label=resolved_label,
        assignment_type=assignment_type,
        reuse_existing=reuse_existing or inferred_reuse_existing,
        rationale=rationale,
    )


def suggest_topics_for_video(
    *,
    project_name: str,
    approved_topic_names: list[str],
    youtube_video_id: str,
    video_title: str,
    video_description: str | None,
    model: str = "gpt-4.1-mini",
) -> VideoTopicSuggestion:
    client = _get_openai_client()
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Be conservative and precise. Reuse an approved broad-topic label only when it is a strong fit; otherwise propose a more concrete broad topic. Secondary topics should be rare.",
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
                            approved_topic_names=approved_topic_names,
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
                "name": "video_topic_suggestion",
                "schema": _response_schema(),
                "strict": True,
            }
        },
    )

    raw_text = getattr(response, "output_text", "") or ""
    if not raw_text.strip():
        raise TopicSuggestionAIError("AI returned an empty structured response")
    payload = json.loads(raw_text)

    primary_topic = _normalize_label(
        payload["primary_topic"],
        expected_assignment_type="primary",
        approved_topic_names=approved_topic_names,
    )
    secondary_topics_raw = payload.get("secondary_topics", [])
    secondary_topics: list[SuggestedTopicLabel] = []
    seen_labels = {primary_topic.label.casefold()}
    for item in secondary_topics_raw:
        normalized = _normalize_label(
            item,
            expected_assignment_type="secondary",
            approved_topic_names=approved_topic_names,
        )
        key = normalized.label.casefold()
        if key in seen_labels:
            continue
        seen_labels.add(key)
        secondary_topics.append(normalized)
        if len(secondary_topics) >= 1:
            break

    return VideoTopicSuggestion(
        youtube_video_id=youtube_video_id,
        video_title=video_title,
        primary_topic=primary_topic,
        secondary_topics=tuple(secondary_topics),
        raw_response_json=json.dumps(payload, indent=2, sort_keys=True),
    )
