from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass

STOPWORDS = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "have",
    "your",
    "about",
    "into",
    "they",
    "them",
    "their",
    "there",
    "then",
    "than",
    "just",
    "like",
    "what",
    "when",
    "will",
    "would",
    "could",
    "should",
    "where",
    "which",
    "while",
    "because",
    "been",
    "being",
    "were",
    "also",
    "much",
    "many",
    "more",
    "most",
    "some",
    "such",
    "only",
    "very",
    "over",
    "after",
    "before",
    "under",
    "again",
    "still",
    "here",
    "each",
    "through",
    "these",
    "those",
    "does",
    "did",
    "done",
    "dont",
    "cant",
    "wont",
    "youre",
    "its",
    "im",
    "ive",
    "weve",
    "our",
    "ours",
    "out",
    "off",
    "for",
    "are",
    "was",
    "but",
    "not",
    "all",
    "any",
    "can",
    "how",
    "why",
    "who",
    "had",
    "has",
    "his",
    "her",
    "she",
    "him",
    "too",
    "via",
    "per",
    "get",
    "got",
    "use",
    "used",
    "using",
    "make",
    "made",
    "need",
    "needs",
    "want",
    "wants",
    "say",
    "says",
    "said",
    "video",
    "videos",
    "summary",
    "transcript",
    "channel",
    "really",
    "pretty",
    "even",
    "less",
    "first",
    "second",
    "third",
    "one",
    "two",
    "three",
    "new",
    "old",
    "best",
    "better",
    "viewer",
    "viewers",
}

THEME_BLACKLIST = {
    "improve retention",
    "improves retention",
    "clear promise gives",
    "gives viewers",
    "reason stay",
}

RECOMMENDATION_PREFIXES = (
    "use ",
    "try ",
    "focus on ",
    "avoid ",
    "start with ",
    "keep ",
    "make sure ",
    "remember to ",
    "you should ",
    "dont ",
    "don't ",
    "do not ",
    "always ",
    "never ",
)

CLAIM_MARKERS = (
    " is ",
    " are ",
    " means ",
    " shows ",
    " leads to ",
    " helps ",
    " matters ",
    " works ",
    " improve ",
    " improves ",
    " gives ",
)


@dataclass(frozen=True)
class GroupAnalysisInput:
    youtube_video_id: str
    video_title: str
    processing_status: str | None
    summary_text: str | None


@dataclass(frozen=True)
class GroupAnalysisArtifact:
    analysis_version: str
    processed_video_count: int
    skipped_video_count: int
    shared_themes_json: str
    repeated_recommendations_json: str
    notable_differences_json: str
    analysis_detail: str | None = None


ANALYSIS_VERSION = "deterministic_v1"


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _canonical_sentence(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = re.sub(r"[!?]+$", ".", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower()


def _clean_theme_phrase(tokens: list[str]) -> str | None:
    filtered = [token for token in tokens if len(token) >= 4 and token not in STOPWORDS]
    if len(filtered) < 2:
        return None
    phrase = " ".join(filtered[:2])
    if phrase in THEME_BLACKLIST:
        return None
    return phrase


def _extract_theme_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for sentence in _split_sentences(text):
        tokens = _tokenize(sentence)
        if len(tokens) < 2:
            continue
        for left, right in zip(tokens, tokens[1:]):
            phrase = _clean_theme_phrase([left, right])
            if phrase:
                phrases.append(phrase)
    return phrases


def _extract_recommendations_or_claims(text: str) -> list[str]:
    findings: list[str] = []
    for sentence in _split_sentences(text):
        lowered = sentence.lower()
        if any(lowered.startswith(prefix) for prefix in RECOMMENDATION_PREFIXES):
            findings.append(_canonical_sentence(sentence))
            continue
        if any(marker in lowered for marker in CLAIM_MARKERS) and len(lowered.split()) <= 18:
            findings.append(_canonical_sentence(sentence))
    return findings


def build_group_analysis(rows: list[GroupAnalysisInput]) -> GroupAnalysisArtifact:
    shared_theme_counter: Counter[str] = Counter()
    repeated_statement_counter: Counter[str] = Counter()
    per_video_themes: dict[str, set[str]] = {}
    per_video_findings: dict[str, list[str]] = {}
    processed_video_ids: list[str] = []
    skipped_videos: list[dict[str, str]] = []

    for row in rows:
        if row.processing_status != "processed" or not row.summary_text:
            skipped_videos.append(
                {
                    "youtube_video_id": row.youtube_video_id,
                    "video_title": row.video_title,
                    "status": row.processing_status or "unprocessed",
                }
            )
            continue

        processed_video_ids.append(row.youtube_video_id)
        themes = set(_extract_theme_phrases(row.summary_text))
        findings = _extract_recommendations_or_claims(row.summary_text)
        per_video_themes[row.youtube_video_id] = themes
        per_video_findings[row.youtube_video_id] = findings
        shared_theme_counter.update(themes)
        repeated_statement_counter.update(findings)

    shared_themes = [
        {"theme": theme, "video_count": count}
        for theme, count in shared_theme_counter.most_common()
        if count >= 2
    ][:8]

    repeated_recommendations = [
        {"text": text, "video_count": count}
        for text, count in repeated_statement_counter.most_common()
        if count >= 2
    ][:8]

    notable_differences = []
    for video_id in processed_video_ids:
        unique_themes = [theme for theme in sorted(per_video_themes[video_id]) if shared_theme_counter[theme] == 1][:5]
        findings = per_video_findings.get(video_id, [])[:3]
        if unique_themes or findings:
            notable_differences.append(
                {
                    "youtube_video_id": video_id,
                    "unique_themes": unique_themes,
                    "recommendations_or_claims": findings,
                }
            )

    detail: str | None = None
    if not processed_video_ids:
        detail = "No processed videos with summaries were available for this group."
    elif skipped_videos:
        detail = f"Analyzed {len(processed_video_ids)} processed videos; skipped {len(skipped_videos)} unprocessed or summary-less videos."

    return GroupAnalysisArtifact(
        analysis_version=ANALYSIS_VERSION,
        processed_video_count=len(processed_video_ids),
        skipped_video_count=len(skipped_videos),
        shared_themes_json=json.dumps(shared_themes),
        repeated_recommendations_json=json.dumps(repeated_recommendations),
        notable_differences_json=json.dumps(
            {
                "videos": notable_differences,
                "skipped_videos": skipped_videos,
            }
        ),
        analysis_detail=detail,
    )
