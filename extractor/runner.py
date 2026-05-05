from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from yt_channel_analyzer.extractor.errors import ExtractorError, SchemaValidationError
from yt_channel_analyzer.extractor.registry import Prompt, get_prompt
from yt_channel_analyzer.extractor.schema import validate


Job = tuple[str, str, dict, Optional[int]]
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class ParsedResult:
    data: dict
    raw_text: str
    parse_status: str  # "ok" | "retry"


def _content_hash(prompt: Prompt, rendered: str) -> str:
    payload = json.dumps(
        {
            "rendered": rendered,
            "schema": prompt.schema,
            "system": prompt.system,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse(raw_text: str, schema: dict) -> dict:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"response was not valid JSON: {exc}") from exc
    validate(data, schema)
    return data


def _insert_audit_row(
    connection: sqlite3.Connection,
    *,
    prompt: Prompt,
    rendered: str,
    runner: Any,
    is_batch: bool,
    batch_size: int,
    parse_status: str,
    correlation_id: Optional[int],
) -> None:
    connection.execute(
        """
        INSERT INTO llm_calls(
            prompt_name, prompt_version, content_hash, model, provider,
            is_batch, batch_size, parse_status, tokens_in, tokens_out,
            cost_estimate_usd, correlation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prompt.name,
            prompt.version,
            _content_hash(prompt, rendered),
            getattr(runner, "model", "unknown"),
            getattr(runner, "provider", "unknown"),
            1 if is_batch else 0,
            batch_size,
            parse_status,
            None,
            None,
            None,
            correlation_id,
        ),
    )
    connection.commit()


class Extractor:
    """Deep Module owning LLM-call mechanics."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        runner: Any,
        batch_threshold: int = 10,
    ) -> None:
        self._connection = connection
        self._runner = runner
        self._batch_threshold = batch_threshold

    def run_one(
        self,
        name: str,
        version: str,
        context: dict,
        *,
        correlation_id: Optional[int] = None,
    ) -> ParsedResult:
        prompt = get_prompt(name, version)
        return self._run_single_with_retry(
            prompt, context, correlation_id=correlation_id, is_batch=False, batch_size=1
        )

    def run_batch(
        self,
        jobs: Sequence[Job],
        *,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[ParsedResult]:
        if not jobs:
            return []
        prompts = [get_prompt(name, version) for (name, version, _ctx, _cid) in jobs]
        first = prompts[0]
        if any(
            (p.name, p.version) != (first.name, first.version) for p in prompts
        ):
            raise ExtractorError("run_batch requires a single prompt per call")

        use_batch = (
            getattr(self._runner, "supports_batch", lambda: False)()
            and len(jobs) >= self._batch_threshold
        )

        if use_batch:
            return self._run_via_batch_api(first, jobs, progress_callback)

        results: list[ParsedResult] = []
        total = len(jobs)
        for i, (name, version, context, correlation_id) in enumerate(jobs, start=1):
            prompt = get_prompt(name, version)
            result = self._run_single_with_retry(
                prompt,
                context,
                correlation_id=correlation_id,
                is_batch=False,
                batch_size=1,
            )
            results.append(result)
            if progress_callback is not None:
                progress_callback(i, total)
        return results

    # --- internals ---

    def _run_single_with_retry(
        self,
        prompt: Prompt,
        context: dict,
        *,
        correlation_id: Optional[int],
        is_batch: bool,
        batch_size: int,
    ) -> ParsedResult:
        rendered = prompt.render(context)
        raw_text = self._runner.run_single(prompt=prompt, rendered=rendered)
        try:
            data = _parse(raw_text, prompt.schema)
        except SchemaValidationError:
            _insert_audit_row(
                self._connection,
                prompt=prompt,
                rendered=rendered,
                runner=self._runner,
                is_batch=is_batch,
                batch_size=batch_size,
                parse_status="retry",
                correlation_id=correlation_id,
            )
            raw_text = self._runner.run_single(prompt=prompt, rendered=rendered)
            try:
                data = _parse(raw_text, prompt.schema)
            except SchemaValidationError:
                _insert_audit_row(
                    self._connection,
                    prompt=prompt,
                    rendered=rendered,
                    runner=self._runner,
                    is_batch=is_batch,
                    batch_size=batch_size,
                    parse_status="failed",
                    correlation_id=correlation_id,
                )
                raise
            _insert_audit_row(
                self._connection,
                prompt=prompt,
                rendered=rendered,
                runner=self._runner,
                is_batch=is_batch,
                batch_size=batch_size,
                parse_status="ok",
                correlation_id=correlation_id,
            )
            return ParsedResult(data=data, raw_text=raw_text, parse_status="retry")

        _insert_audit_row(
            self._connection,
            prompt=prompt,
            rendered=rendered,
            runner=self._runner,
            is_batch=is_batch,
            batch_size=batch_size,
            parse_status="ok",
            correlation_id=correlation_id,
        )
        return ParsedResult(data=data, raw_text=raw_text, parse_status="ok")

    def _run_via_batch_api(
        self,
        prompt: Prompt,
        jobs: Sequence[Job],
        progress_callback: Optional[ProgressCallback],
    ) -> list[ParsedResult]:
        rendered_prompts = [prompt.render(ctx) for (_n, _v, ctx, _c) in jobs]
        raw_texts = self._runner.run_batch_submission(
            prompt=prompt, rendered_prompts=rendered_prompts
        )
        if len(raw_texts) != len(jobs):
            raise ExtractorError("batch runner returned wrong number of responses")

        results: list[ParsedResult] = []
        total = len(jobs)
        batch_size = total
        for i, ((_n, _v, _ctx, correlation_id), rendered, raw_text) in enumerate(
            zip(jobs, rendered_prompts, raw_texts), start=1
        ):
            try:
                data = _parse(raw_text, prompt.schema)
                parse_status = "ok"
            except SchemaValidationError:
                _insert_audit_row(
                    self._connection,
                    prompt=prompt,
                    rendered=rendered,
                    runner=self._runner,
                    is_batch=True,
                    batch_size=batch_size,
                    parse_status="failed",
                    correlation_id=correlation_id,
                )
                raise
            _insert_audit_row(
                self._connection,
                prompt=prompt,
                rendered=rendered,
                runner=self._runner,
                is_batch=True,
                batch_size=batch_size,
                parse_status=parse_status,
                correlation_id=correlation_id,
            )
            results.append(
                ParsedResult(data=data, raw_text=raw_text, parse_status=parse_status)
            )
            if progress_callback is not None:
                progress_callback(i, total)
        return results
