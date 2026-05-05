from __future__ import annotations


class ExtractorError(RuntimeError):
    pass


class SchemaValidationError(ExtractorError):
    pass
