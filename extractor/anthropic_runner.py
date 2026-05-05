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


class AnthropicRunner:
    provider = "anthropic"

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client: Any | None = None

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
        message = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=prompt.system,
            messages=[{"role": "user", "content": rendered}],
        )
        return _extract_text(message)

    def run_batch_submission(self, *, prompt: Prompt, rendered_prompts: list[str]) -> list[str]:
        client = self._ensure_client()
        requests = [
            {
                "custom_id": f"req-{i}",
                "params": {
                    "model": self.model,
                    "max_tokens": 4096,
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
        for entry in client.messages.batches.results(batch_id):
            results[entry.custom_id] = _extract_text(entry.result.message)
        return [results[f"req-{i}"] for i in range(len(rendered_prompts))]


def _extract_text(message: Any) -> str:
    parts = getattr(message, "content", None) or []
    texts = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            texts.append(text)
    return "".join(texts)
