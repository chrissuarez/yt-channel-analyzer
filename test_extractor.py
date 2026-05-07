from __future__ import annotations

import sqlite3
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from yt_channel_analyzer.db import connect, ensure_schema
from yt_channel_analyzer.extractor import (
    Extractor,
    ExtractorError,
    FakeLLMRunner,
    ParsedResult,
    Prompt,
    SchemaValidationError,
    register_prompt,
    registry as _registry_module,
)


SIMPLE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topic": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["topic", "confidence"],
}


def _render(context: dict) -> str:
    return f"Title: {context['title']}"


class _RegistryIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(_registry_module._PROMPTS)
        _registry_module._PROMPTS.clear()

    def tearDown(self) -> None:
        _registry_module._PROMPTS.clear()
        _registry_module._PROMPTS.update(self._saved)


def _open_db(tmp: str) -> sqlite3.Connection:
    path = Path(tmp) / "test.sqlite"
    connection = connect(path)
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


class RegistryTests(_RegistryIsolation):
    def test_register_and_lookup_pinned_version(self) -> None:
        register_prompt(
            name="discovery.topics",
            version="1.0.0",
            render=_render,
            schema=SIMPLE_SCHEMA,
            system="You are an extractor.",
        )
        prompt = _registry_module.get_prompt("discovery.topics", "1.0.0")
        self.assertEqual(prompt.name, "discovery.topics")
        self.assertEqual(prompt.version, "1.0.0")
        self.assertEqual(prompt.system, "You are an extractor.")
        self.assertIs(prompt.render, _render)

    def test_unregistered_lookup_raises(self) -> None:
        with self.assertRaises(ExtractorError):
            _registry_module.get_prompt("missing", "1.0.0")

    def test_version_must_be_pinned_exactly(self) -> None:
        register_prompt(
            name="p", version="1.2.3", render=_render, schema=SIMPLE_SCHEMA, system="s"
        )
        with self.assertRaises(ExtractorError):
            _registry_module.get_prompt("p", "1.2.4")

    def test_double_register_same_version_rejected(self) -> None:
        register_prompt(
            name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="s"
        )
        with self.assertRaises(ExtractorError):
            register_prompt(
                name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="s"
            )


class FakeLLMRunnerTests(_RegistryIsolation):
    def setUp(self) -> None:
        super().setUp()
        register_prompt(
            name="discovery.topics",
            version="1.0.0",
            render=_render,
            schema=SIMPLE_SCHEMA,
            system="sys",
        )

    def test_canned_response_validated_against_schema(self) -> None:
        fake = FakeLLMRunner()
        with self.assertRaises(SchemaValidationError):
            fake.add_response(
                "discovery.topics", "1.0.0",
                {"topic": "Health"},  # missing confidence
            )

    def test_records_calls_for_assertion(self) -> None:
        fake = FakeLLMRunner()
        fake.add_response(
            "discovery.topics", "1.0.0",
            {"topic": "Health", "confidence": 0.9},
        )
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            extractor.run_one("discovery.topics", "1.0.0", {"title": "Sleep"})
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call.prompt_name, "discovery.topics")
        self.assertEqual(call.version, "1.0.0")
        self.assertIn("Sleep", call.rendered_prompt)


class _RawRunner:
    """Minimal runner that returns whatever raw text it's given. Used to exercise
    `_parse` against LLM output the FakeLLMRunner can't produce (e.g. fenced JSON)."""

    provider = "raw"
    model = "raw-model"

    def __init__(self, raw_text: str) -> None:
        self._raw = raw_text
        self.calls = 0

    def supports_batch(self) -> bool:
        return False

    def run_single(self, *, prompt, rendered: str) -> str:
        self.calls += 1
        return self._raw


class FenceStripTests(_RegistryIsolation):
    """Haiku-style code-fenced JSON must parse without falling into the retry path."""

    def setUp(self) -> None:
        super().setUp()
        register_prompt(
            name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="sys"
        )

    def _run(self, raw: str) -> ParsedResult:
        runner = _RawRunner(raw)
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=runner)
            result = extractor.run_one("p", "1.0.0", {"title": "x"})
        self.assertEqual(runner.calls, 1, "fence-stripped JSON should parse first try")
        return result

    def test_strips_json_fence(self) -> None:
        result = self._run('```json\n{"topic": "Health", "confidence": 0.9}\n```')
        self.assertEqual(result.parse_status, "ok")
        self.assertEqual(result.data, {"topic": "Health", "confidence": 0.9})

    def test_strips_bare_fence(self) -> None:
        result = self._run('```\n{"topic": "Health", "confidence": 0.5}\n```')
        self.assertEqual(result.data["confidence"], 0.5)

    def test_unfenced_json_unchanged(self) -> None:
        result = self._run('{"topic": "Health", "confidence": 0.7}')
        self.assertEqual(result.data["confidence"], 0.7)


class RunOneTests(_RegistryIsolation):
    def setUp(self) -> None:
        super().setUp()
        register_prompt(
            name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="sys"
        )

    def test_happy_path_returns_parsed_result(self) -> None:
        fake = FakeLLMRunner()
        fake.add_response("p", "1.0.0", {"topic": "Health", "confidence": 0.9})
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            result = extractor.run_one("p", "1.0.0", {"title": "x"})
        self.assertIsInstance(result, ParsedResult)
        self.assertEqual(result.data, {"topic": "Health", "confidence": 0.9})
        self.assertEqual(result.parse_status, "ok")

    def test_unregistered_prompt_rejected(self) -> None:
        fake = FakeLLMRunner()
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            with self.assertRaises(ExtractorError):
                extractor.run_one("does-not-exist", "1.0.0", {"title": "x"})

    def test_malformed_response_retries_once_then_succeeds(self) -> None:
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [
            {"topic": "Health"},  # malformed: missing confidence
            {"topic": "Health", "confidence": 0.5},
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            result = extractor.run_one("p", "1.0.0", {"title": "x"})
        self.assertEqual(result.parse_status, "retry")
        self.assertEqual(result.data["confidence"], 0.5)
        self.assertEqual(len(fake.calls), 2)

    def test_malformed_twice_errors_no_partial_state(self) -> None:
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [
            {"topic": "Health"},
            {"topic": "Health"},
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            with self.assertRaises(SchemaValidationError):
                extractor.run_one("p", "1.0.0", {"title": "x"})
            row = connection.execute(
                "SELECT parse_status FROM llm_calls ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(row["parse_status"], "failed")

    def test_audit_row_written_on_success(self) -> None:
        fake = FakeLLMRunner()
        fake.add_response("p", "1.0.0", {"topic": "Health", "confidence": 0.9})
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            extractor.run_one("p", "1.0.0", {"title": "Sleep"})
            row = connection.execute(
                "SELECT prompt_name, prompt_version, model, provider, is_batch, "
                "batch_size, parse_status, content_hash FROM llm_calls"
            ).fetchone()
        self.assertEqual(row["prompt_name"], "p")
        self.assertEqual(row["prompt_version"], "1.0.0")
        self.assertEqual(row["is_batch"], 0)
        self.assertEqual(row["batch_size"], 1)
        self.assertEqual(row["parse_status"], "ok")
        self.assertTrue(row["content_hash"])

    def test_audit_row_written_on_retry(self) -> None:
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [
            {"topic": "Health"},
            {"topic": "Health", "confidence": 0.5},
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            extractor.run_one("p", "1.0.0", {"title": "x"})
            statuses = [
                r["parse_status"]
                for r in connection.execute(
                    "SELECT parse_status FROM llm_calls ORDER BY id"
                ).fetchall()
            ]
        self.assertEqual(statuses, ["retry", "ok"])


class RunBatchTests(_RegistryIsolation):
    def setUp(self) -> None:
        super().setUp()
        register_prompt(
            name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="sys"
        )

    def test_below_threshold_falls_back_to_sequential(self) -> None:
        fake = FakeLLMRunner(batch_supported=True)
        fake.queue_responses("p", "1.0.0", [
            {"topic": "A", "confidence": 0.1},
            {"topic": "B", "confidence": 0.2},
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake, batch_threshold=5)
            jobs = [
                ("p", "1.0.0", {"title": "1"}, None),
                ("p", "1.0.0", {"title": "2"}, None),
            ]
            results = extractor.run_batch(jobs)
        self.assertEqual([r.data["topic"] for r in results], ["A", "B"])
        self.assertEqual(fake.batch_submissions, 0)
        rows = connection.execute(
            "SELECT is_batch FROM llm_calls"
        ).fetchall()
        self.assertEqual([r["is_batch"] for r in rows], [0, 0])

    def test_above_threshold_uses_batch_api(self) -> None:
        fake = FakeLLMRunner(batch_supported=True)
        fake.queue_batch_responses("p", "1.0.0", [
            {"topic": "A", "confidence": 0.1},
            {"topic": "B", "confidence": 0.2},
            {"topic": "C", "confidence": 0.3},
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake, batch_threshold=2)
            jobs = [
                ("p", "1.0.0", {"title": str(i)}, None) for i in range(3)
            ]
            results = extractor.run_batch(jobs)
        self.assertEqual([r.data["topic"] for r in results], ["A", "B", "C"])
        self.assertEqual(fake.batch_submissions, 1)
        rows = connection.execute(
            "SELECT is_batch, batch_size FROM llm_calls"
        ).fetchall()
        self.assertEqual([r["is_batch"] for r in rows], [1, 1, 1])
        self.assertEqual({r["batch_size"] for r in rows}, {3})

    def test_progress_callback_invoked(self) -> None:
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [
            {"topic": "A", "confidence": 0.1},
            {"topic": "B", "confidence": 0.2},
        ])
        progress: list[tuple[int, int]] = []
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake, batch_threshold=10)
            extractor.run_batch(
                [("p", "1.0.0", {"title": "1"}, None),
                 ("p", "1.0.0", {"title": "2"}, None)],
                progress_callback=lambda done, total: progress.append((done, total)),
            )
        self.assertEqual(progress, [(1, 2), (2, 2)])


class SchemaMigrationTests(unittest.TestCase):
    def test_llm_calls_table_exists(self) -> None:
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_calls'"
            ).fetchone()
            self.assertIsNotNone(row)

    def test_migration_idempotent(self) -> None:
        with TemporaryDirectory() as td:
            path = Path(td) / "x.sqlite"
            connection = connect(path)
            connection.row_factory = sqlite3.Row
            ensure_schema(connection)
            ensure_schema(connection)
            cols = connection.execute("PRAGMA table_info(llm_calls)").fetchall()
            names = {c["name"] for c in cols}
        self.assertTrue(
            {"prompt_name", "prompt_version", "content_hash", "model", "provider",
             "is_batch", "batch_size", "parse_status", "tokens_in", "tokens_out",
             "cost_estimate_usd", "correlation_id"} <= names
        )


if __name__ == "__main__":
    unittest.main()
