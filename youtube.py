from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# YouTube returns the max 50 IDs per videos.list request.
YOUTUBE_VIDEOS_LIST_BATCH = 50

_ISO8601_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?"
    r")?$"
)


def parse_iso8601_duration(value: str | None) -> int | None:
    """Parse an ISO 8601 duration like ``PT2M30S`` into whole seconds.

    Returns ``None`` for a falsy or unparseable value (e.g. live streams that
    report ``P0D`` parse to ``0``; anything outside the supported grammar yields
    ``None`` rather than raising)."""
    if not value:
        return None
    match = _ISO8601_DURATION_RE.match(value.strip())
    if match is None:
        return None
    parts = match.groupdict()
    days = int(parts["days"] or 0)
    hours = int(parts["hours"] or 0)
    minutes = int(parts["minutes"] or 0)
    seconds = int(parts["seconds"] or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


@dataclass(frozen=True)
class ChannelMetadata:
    youtube_channel_id: str
    title: str
    description: str | None
    custom_url: str | None
    published_at: str | None
    thumbnail_url: str | None

    @property
    def handle(self) -> str | None:
        if not self.custom_url:
            return None
        return self.custom_url if self.custom_url.startswith("@") else f"@{self.custom_url}"


@dataclass(frozen=True)
class VideoMetadata:
    youtube_video_id: str
    title: str
    description: str | None
    published_at: str | None
    thumbnail_url: str | None
    duration_seconds: int | None = None


class YouTubeResolverError(ValueError):
    pass


class YouTubeAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptRecord:
    status: str
    source: str | None
    language_code: str | None
    text: str | None
    detail: str | None = None


def get_api_key() -> str:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise YouTubeAPIError("YOUTUBE_API_KEY is required")
    return api_key


def resolve_channel_input(channel_input: str) -> dict[str, str]:
    raw_value = channel_input.strip()
    if not raw_value:
        raise YouTubeResolverError("channel input cannot be empty")

    parsed = urllib.parse.urlparse(raw_value)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        parts = [part for part in path.split("/") if part]
        if parsed.netloc.lower().endswith("youtube.com"):
            if len(parts) >= 2 and parts[0] == "channel":
                return {"kind": "id", "value": parts[1]}
            if len(parts) >= 2 and parts[0] == "@":
                return {"kind": "handle", "value": f"@{parts[1]}"}
            if parts and parts[0].startswith("@"):
                return {"kind": "handle", "value": parts[0]}
        raise YouTubeResolverError(f"unsupported YouTube channel URL: {channel_input}")

    if raw_value.startswith("@"):
        return {"kind": "handle", "value": raw_value}
    if raw_value.startswith("UC"):
        return {"kind": "id", "value": raw_value}
    raise YouTubeResolverError("channel input must be a channel ID, handle, or supported YouTube URL")


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def build_api_url(path: str, **params: str) -> str:
    query = urllib.parse.urlencode(params)
    return f"{YOUTUBE_API_BASE}/{path}?{query}"


def resolve_canonical_channel_id(channel_input: str, *, api_key: str | None = None) -> str:
    resolved = resolve_channel_input(channel_input)
    if resolved["kind"] == "id":
        return resolved["value"]

    key = api_key or get_api_key()
    url = build_api_url(
        "search",
        part="snippet",
        q=resolved["value"],
        type="channel",
        maxResults="1",
        key=key,
    )
    payload = fetch_json(url)
    items = payload.get("items", [])
    if not items:
        raise YouTubeAPIError(f"no channel found for {channel_input}")

    channel_id = items[0].get("snippet", {}).get("channelId") or items[0].get("id", {}).get("channelId")
    if not channel_id:
        raise YouTubeAPIError(f"no canonical channel ID returned for {channel_input}")
    return channel_id


def fetch_channel_metadata(channel_id: str, *, api_key: str | None = None) -> ChannelMetadata:
    key = api_key or get_api_key()
    url = build_api_url(
        "channels",
        part="snippet",
        id=channel_id,
        key=key,
    )
    payload = fetch_json(url)
    items = payload.get("items", [])
    if not items:
        raise YouTubeAPIError(f"channel not found: {channel_id}")

    snippet = items[0].get("snippet", {})
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}

    return ChannelMetadata(
        youtube_channel_id=items[0]["id"],
        title=snippet.get("title", ""),
        description=snippet.get("description"),
        custom_url=snippet.get("customUrl"),
        published_at=snippet.get("publishedAt"),
        thumbnail_url=thumbnail.get("url"),
    )


def fetch_channel_videos(
    channel_id: str,
    *,
    api_key: str | None = None,
    limit: int = 25,
) -> list[VideoMetadata]:
    key = api_key or get_api_key()
    safe_limit = max(1, min(limit, 50))

    channel_url = build_api_url(
        "channels",
        part="contentDetails",
        id=channel_id,
        key=key,
    )
    channel_payload = fetch_json(channel_url)
    channel_items = channel_payload.get("items", [])
    if not channel_items:
        raise YouTubeAPIError(f"channel not found: {channel_id}")

    uploads_playlist_id = (
        channel_items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )
    if not uploads_playlist_id:
        raise YouTubeAPIError(f"uploads playlist not found for channel: {channel_id}")

    playlist_url = build_api_url(
        "playlistItems",
        part="snippet,contentDetails",
        playlistId=uploads_playlist_id,
        maxResults=str(safe_limit),
        key=key,
    )
    playlist_payload = fetch_json(playlist_url)
    items = playlist_payload.get("items", [])

    videos: list[VideoMetadata] = []
    for item in items:
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})
        video_id = content_details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
        if not video_id:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
        videos.append(
            VideoMetadata(
                youtube_video_id=video_id,
                title=snippet.get("title", ""),
                description=snippet.get("description"),
                published_at=content_details.get("videoPublishedAt") or snippet.get("publishedAt"),
                thumbnail_url=thumbnail.get("url"),
            )
        )

    durations = fetch_video_durations(
        [video.youtube_video_id for video in videos], api_key=key
    )
    return [
        VideoMetadata(
            youtube_video_id=video.youtube_video_id,
            title=video.title,
            description=video.description,
            published_at=video.published_at,
            thumbnail_url=video.thumbnail_url,
            duration_seconds=durations.get(video.youtube_video_id),
        )
        for video in videos
    ]


def _batched(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def fetch_video_durations(
    video_ids: list[str],
    *,
    api_key: str | None = None,
) -> dict[str, int | None]:
    """Look up ``contentDetails.duration`` for each video ID via ``videos.list``
    (batched ≤50 per request) and return ``{video_id: seconds | None}``.

    Missing IDs (deleted/private videos) and durations the API omits map to
    ``None``. An empty input list makes no API call."""
    unique_ids = list(dict.fromkeys(vid for vid in video_ids if vid))
    if not unique_ids:
        return {}
    key = api_key or get_api_key()
    durations: dict[str, int | None] = {vid: None for vid in unique_ids}
    for batch in _batched(unique_ids, YOUTUBE_VIDEOS_LIST_BATCH):
        url = build_api_url(
            "videos",
            part="contentDetails",
            id=",".join(batch),
            maxResults=str(YOUTUBE_VIDEOS_LIST_BATCH),
            key=key,
        )
        payload = fetch_json(url)
        for item in payload.get("items", []):
            video_id = item.get("id")
            if not video_id:
                continue
            duration_text = item.get("contentDetails", {}).get("duration")
            durations[video_id] = parse_iso8601_duration(duration_text)
    return durations


def _safe_exception_detail(exc: Exception) -> str | None:
    message = " ".join(str(exc).split()).strip()
    if not message:
        return None
    return message[:300]


def _classify_transcript_exception(exc: Exception, known_errors: dict[str, type[Exception]]) -> str:
    if isinstance(exc, known_errors["TranscriptsDisabled"]):
        return "disabled"
    if isinstance(exc, known_errors["NoTranscriptFound"]):
        return "not_found"
    if isinstance(exc, known_errors["VideoUnavailable"]):
        return "unavailable"

    name = exc.__class__.__name__.lower()
    detail = (str(exc) or "").lower()
    combined = f"{name} {detail}"

    if "too many requests" in combined or "rate limit" in combined or "ratelimit" in combined:
        return "rate_limited"
    if any(token in combined for token in ("request", "http", "connection", "timeout", "timed out", "proxy")):
        return "request_failed"
    return "error"


def _transcript_segment_text(segment: Any) -> str:
    if isinstance(segment, dict):
        value = segment.get("text", "")
    else:
        value = getattr(segment, "text", "")
    return value.strip() if isinstance(value, str) else ""


def _default_transcript_fetcher() -> Callable[[str], TranscriptRecord]:
    try:
        from youtube_transcript_api import (
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
            YouTubeTranscriptApi,
        )
    except ImportError as exc:
        raise RuntimeError(
            "youtube-transcript-api is required for transcript fetching"
        ) from exc

    known_errors: dict[str, type[Exception]] = {
        "NoTranscriptFound": NoTranscriptFound,
        "TranscriptsDisabled": TranscriptsDisabled,
        "VideoUnavailable": VideoUnavailable,
    }

    def fetch(video_id: str) -> TranscriptRecord:
        try:
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id)
            preferred = None
            try:
                preferred = transcript_list.find_manually_created_transcript(["en"])
            except NoTranscriptFound:
                preferred = None
            if preferred is None:
                try:
                    preferred = transcript_list.find_generated_transcript(["en"])
                except NoTranscriptFound:
                    available = list(transcript_list)
                    if available:
                        preferred = available[0]
                    else:
                        return TranscriptRecord(status="not_found", source=None, language_code=None, text=None)
            segments = preferred.fetch()
            text = " ".join(segment_text for segment in segments if (segment_text := _transcript_segment_text(segment)))
            source = "generated" if getattr(preferred, "is_generated", False) else "manual"
            return TranscriptRecord(
                status="available",
                source=source,
                language_code=getattr(preferred, "language_code", None),
                text=text or None,
            )
        except Exception as exc:
            return TranscriptRecord(
                status=_classify_transcript_exception(exc, known_errors),
                source=None,
                language_code=None,
                text=None,
                detail=_safe_exception_detail(exc),
            )

    return fetch


def fetch_video_transcript(
    video_id: str,
    *,
    transcript_fetcher: Callable[[str], TranscriptRecord] | None = None,
) -> TranscriptRecord:
    fetcher = transcript_fetcher or _default_transcript_fetcher()
    return fetcher(video_id)
