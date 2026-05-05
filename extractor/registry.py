from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from yt_channel_analyzer.extractor.errors import ExtractorError


RenderFn = Callable[[dict], str]


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    render: RenderFn
    schema: dict
    system: str


_PROMPTS: dict[tuple[str, str], Prompt] = {}


def register_prompt(
    *,
    name: str,
    version: str,
    render: RenderFn,
    schema: dict,
    system: str,
) -> Prompt:
    key = (name, version)
    if key in _PROMPTS:
        raise ExtractorError(f"prompt already registered: {name}@{version}")
    prompt = Prompt(
        name=name, version=version, render=render, schema=schema, system=system
    )
    _PROMPTS[key] = prompt
    return prompt


def get_prompt(name: str, version: str) -> Prompt:
    try:
        return _PROMPTS[(name, version)]
    except KeyError as exc:
        raise ExtractorError(f"prompt not registered: {name}@{version}") from exc
