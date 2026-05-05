from __future__ import annotations

import re
from dataclasses import dataclass

from yt_channel_analyzer.youtube import TranscriptRecord


@dataclass(frozen=True)
class ProcessedVideoArtifact:
    processing_status: str
    summary_text: str | None
    chunk_count: int
    transcript_char_count: int
    detail: str | None = None


@dataclass(frozen=True)
class TranscriptChunk:
    chunk_index: int
    chunk_text: str
    start_char: int
    end_char: int


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]


def build_transcript_chunks(transcript_text: str, *, chunk_size: int = 1200) -> list[TranscriptChunk]:
    normalized = _normalize_text(transcript_text)
    if not normalized:
        return []

    chunks: list[TranscriptChunk] = []
    start = 0
    chunk_index = 0
    length = len(normalized)

    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            split_at = normalized.rfind(" ", start, end)
            if split_at > start:
                end = split_at
        chunk_text = normalized[start:end].strip()
        if chunk_text:
            chunks.append(
                TranscriptChunk(
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    start_char=start,
                    end_char=end,
                )
            )
            chunk_index += 1
        start = end
        while start < length and normalized[start] == " ":
            start += 1

    return chunks


def build_summary_text(transcript_text: str, *, max_chars: int = 280) -> str | None:
    normalized = _normalize_text(transcript_text)
    if not normalized:
        return None
    if len(normalized) <= max_chars:
        return normalized

    sentences = _split_sentences(transcript_text)
    chosen: list[str] = []
    total = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        addition = len(sentence) if not chosen else len(sentence) + 1
        if chosen and total + addition > max_chars:
            break
        if not chosen and len(sentence) > max_chars:
            break
        chosen.append(sentence)
        total += addition

    if chosen:
        return " ".join(chosen)

    clipped = normalized[:max_chars].rstrip()
    split_at = clipped.rfind(" ")
    if split_at > 80:
        clipped = clipped[:split_at]
    return f"{clipped}…"


def process_transcript_record(transcript: TranscriptRecord | None) -> tuple[ProcessedVideoArtifact, list[TranscriptChunk]]:
    if transcript is None:
        return (
            ProcessedVideoArtifact(
                processing_status="transcript_missing",
                summary_text=None,
                chunk_count=0,
                transcript_char_count=0,
                detail="Transcript has not been fetched.",
            ),
            [],
        )

    if transcript.status != "available" or not transcript.text:
        detail = transcript.detail or f"Transcript status: {transcript.status}"
        return (
            ProcessedVideoArtifact(
                processing_status=f"transcript_{transcript.status}",
                summary_text=None,
                chunk_count=0,
                transcript_char_count=0,
                detail=detail,
            ),
            [],
        )

    chunks = build_transcript_chunks(transcript.text)
    summary = build_summary_text(transcript.text)
    normalized_length = len(_normalize_text(transcript.text))
    return (
        ProcessedVideoArtifact(
            processing_status="processed",
            summary_text=summary,
            chunk_count=len(chunks),
            transcript_char_count=normalized_length,
            detail=None,
        ),
        chunks,
    )
