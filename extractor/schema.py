"""Minimal JSON-schema-style validator.

Supports the subset of JSON Schema this project uses for structured outputs:
- type: object | array | string | number | integer | boolean | null
- required (object), properties, additionalProperties (bool)
- items (array), minItems, maxItems
- minLength (string), minimum / maximum (number, integer)
- enum
"""
from __future__ import annotations

from typing import Any

from yt_channel_analyzer.extractor.errors import SchemaValidationError


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def validate(data: Any, schema: dict, path: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type:
        types = _TYPE_MAP.get(expected_type)
        if types is None:
            raise SchemaValidationError(f"unknown schema type: {expected_type}")
        # bool is a subclass of int — exclude when type is integer/number.
        if expected_type in {"integer", "number"} and isinstance(data, bool):
            raise SchemaValidationError(f"{path}: expected {expected_type}, got bool")
        if not isinstance(data, types):
            raise SchemaValidationError(
                f"{path}: expected {expected_type}, got {type(data).__name__}"
            )

    if "enum" in schema and data not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value {data!r} not in enum")

    if expected_type in {"integer", "number"}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and data < minimum:
            raise SchemaValidationError(f"{path}: {data} < minimum {minimum}")
        if maximum is not None and data > maximum:
            raise SchemaValidationError(f"{path}: {data} > maximum {maximum}")

    if expected_type == "string":
        min_length = schema.get("minLength")
        if min_length is not None and len(data) < min_length:
            raise SchemaValidationError(
                f"{path}: string shorter than minLength {min_length}"
            )

    if expected_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                raise SchemaValidationError(f"{path}: missing required key {key!r}")
        additional = schema.get("additionalProperties", True)
        for key, value in data.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")
            elif additional is False:
                raise SchemaValidationError(f"{path}: unexpected key {key!r}")
            elif isinstance(additional, dict):
                validate(value, additional, f"{path}.{key}")

    if expected_type == "array":
        items = schema.get("items")
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if min_items is not None and len(data) < min_items:
            raise SchemaValidationError(f"{path}: fewer than {min_items} items")
        if max_items is not None and len(data) > max_items:
            raise SchemaValidationError(f"{path}: more than {max_items} items")
        if items is not None:
            for i, item in enumerate(data):
                validate(item, items, f"{path}[{i}]")
