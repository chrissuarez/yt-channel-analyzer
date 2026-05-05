from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from yt_channel_analyzer.extractor.errors import ExtractorError, SchemaValidationError
from yt_channel_analyzer.extractor.registry import get_prompt
from yt_channel_analyzer.extractor.schema import validate


@dataclass
class FakeCall:
    prompt_name: str
    version: str
    rendered_prompt: str
    is_batch: bool


class FakeLLMRunner:
    """Test adapter that returns canned responses validated against the registered schema.

    - `add_response(name, version, payload)` validates and queues a single response.
    - `queue_responses(name, version, [payloads])` queues several (returned in order).
    - `queue_batch_responses(name, version, [payloads])` queues responses to be returned
      from a single batch submission.
    - `calls` records every observed call (for assertions).
    """

    provider = "fake"
    model = "fake-model"

    def __init__(self, *, batch_supported: bool = False) -> None:
        self._responses: dict[tuple[str, str], deque[dict]] = defaultdict(deque)
        self._batch_responses: dict[tuple[str, str], deque[dict]] = defaultdict(deque)
        self.calls: list[FakeCall] = []
        self.batch_submissions = 0
        self.batch_supported = batch_supported

    def add_response(self, name: str, version: str, payload: dict) -> None:
        prompt = get_prompt(name, version)
        validate(payload, prompt.schema)
        self._responses[(name, version)].append(payload)

    def queue_responses(self, name: str, version: str, payloads: list[dict]) -> None:
        # No schema validation here — tests want to enqueue malformed payloads too.
        self._responses[(name, version)].extend(payloads)

    def queue_batch_responses(self, name: str, version: str, payloads: list[dict]) -> None:
        self._batch_responses[(name, version)].extend(payloads)

    # --- runner protocol ---

    def run_single(self, *, prompt, rendered: str) -> str:
        self.calls.append(FakeCall(prompt.name, prompt.version, rendered, is_batch=False))
        queue = self._responses[(prompt.name, prompt.version)]
        if not queue:
            raise ExtractorError(
                f"FakeLLMRunner: no canned response for {prompt.name}@{prompt.version}"
            )
        return json.dumps(queue.popleft())

    def supports_batch(self) -> bool:
        return self.batch_supported

    def run_batch_submission(self, *, prompt, rendered_prompts: list[str]) -> list[str]:
        self.batch_submissions += 1
        for rendered in rendered_prompts:
            self.calls.append(
                FakeCall(prompt.name, prompt.version, rendered, is_batch=True)
            )
        queue = self._batch_responses[(prompt.name, prompt.version)]
        if len(queue) < len(rendered_prompts):
            raise ExtractorError("FakeLLMRunner: not enough batch responses queued")
        return [json.dumps(queue.popleft()) for _ in rendered_prompts]
