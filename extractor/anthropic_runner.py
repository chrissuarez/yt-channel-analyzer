"""Anthropic adapter for the Extractor.

The real provider call is implemented behind a lazy import so test
environments without the `anthropic` package are unaffected.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from yt_channel_analyzer.extractor.errors import ExtractorError
from yt_channel_analyzer.extractor.registry import Prompt


DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 64000


class AnthropicRunner:
    provider = "anthropic"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._max_tokens = max_tokens
        self._client: Any | None = None
        self.last_usage: dict[str, int] | None = None
        self.last_stop_reason: str | None = None
        self.last_batch_usages: list[dict[str, int] | None] = []
        self.last_batch_stop_reasons: list[str | None] = []

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise ExtractorError("ANTHROPIC_API_KEY is required for the Anthropic runner")
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ExtractorError("anthropic package is required") from exc
        self._client = Anthropic(api_key=self._api_key)
        return self._client

    def supports_batch(self) -> bool:
        return True

    def run_single(self, *, prompt: Prompt, rendered: str) -> str:
        client = self._ensure_client()
        # Streaming is required by the SDK whenever max_tokens implies a
        # potential completion >10min (>~21K tokens at the SDK's 128K/hour
        # default). We always stream so the caller can dial max_tokens up
        # to the model ceiling without tripping that gate.
        with client.messages.stream(
            model=self.model,
            max_tokens=self._max_tokens,
            system=prompt.system,
            messages=[{"role": "user", "content": rendered}],
        ) as stream:
            message = stream.get_final_message()
        self.last_usage = _extract_usage(message)
        self.last_stop_reason = _extract_stop_reason(message)
        return _extract_text(message)

    def run_batch_submission(self, *, prompt: Prompt, rendered_prompts: list[str]) -> list[str]:
        client = self._ensure_client()
        requests = [
            {
                "custom_id": f"req-{i}",
                "params": {
                    "model": self.model,
                    "max_tokens": self._max_tokens,
                    "system": prompt.system,
                    "messages": [{"role": "user", "content": rendered}],
                },
            }
            for i, rendered in enumerate(rendered_prompts)
        ]
        batch = client.messages.batches.create(requests=requests)
        batch_id = batch.id
        while True:
            current = client.messages.batches.retrieve(batch_id)
            if current.processing_status == "ended":
                break
            time.sleep(2.0)
        results: dict[str, str] = {}
        usages: dict[str, dict[str, int] | None] = {}
        stop_reasons: dict[str, str | None] = {}
        for entry in client.messages.batches.results(batch_id):
            results[entry.custom_id] = _extract_text(entry.result.message)
            usages[entry.custom_id] = _extract_usage(entry.result.message)
            stop_reasons[entry.custom_id] = _extract_stop_reason(entry.result.message)
        self.last_batch_usages = [
            usages.get(f"req-{i}") for i in range(len(rendered_prompts))
        ]
        self.last_batch_stop_reasons = [
            stop_reasons.get(f"req-{i}") for i in range(len(rendered_prompts))
        ]
        return [results[f"req-{i}"] for i in range(len(rendered_prompts))]


def _extract_text(message: Any) -> str:
    parts = getattr(message, "content", None) or []
    texts = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            texts.append(text)
    return "".join(texts)


def _extract_stop_reason(message: Any) -> str | None:
    reason = getattr(message, "stop_reason", None)
    return str(reason) if reason is not None else None


def _extract_usage(message: Any) -> dict[str, int] | None:
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if input_tokens is None and output_tokens is None:
        return None
    return {
        "input_tokens": int(input_tokens) if input_tokens is not None else 0,
        "output_tokens": int(output_tokens) if output_tokens is not None else 0,
    }
