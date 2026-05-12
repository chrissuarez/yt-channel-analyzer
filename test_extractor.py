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
from yt_channel_analyzer.extractor.pricing import MODEL_PRICES, estimate_cost


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


class TruncationRetrySkipTests(_RegistryIsolation):
    """When a parse failure follows ``stop_reason=max_tokens``, retrying is a
    deterministic-fail and just doubles spend. Skip the retry."""

    def setUp(self) -> None:
        super().setUp()
        register_prompt(
            name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="sys"
        )

    def test_truncation_skips_retry_and_records_failed(self) -> None:
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [{"topic": "Health"}])  # missing confidence
        fake.queue_stop_reason("max_tokens")
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            with self.assertRaises(SchemaValidationError):
                extractor.run_one("p", "1.0.0", {"title": "x"})
            statuses = [
                r["parse_status"]
                for r in connection.execute(
                    "SELECT parse_status FROM llm_calls ORDER BY id"
                ).fetchall()
            ]
        self.assertEqual(len(fake.calls), 1, "must not retry on truncation")
        self.assertEqual(statuses, ["failed"])

    def test_non_truncation_parse_failure_still_retries(self) -> None:
        """Regression guard: only ``stop_reason=max_tokens`` short-circuits."""
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [
            {"topic": "Health"},  # malformed
            {"topic": "Health", "confidence": 0.5},
        ])
        fake.queue_stop_reason("end_turn")
        fake.queue_stop_reason("end_turn")
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            result = extractor.run_one("p", "1.0.0", {"title": "x"})
            statuses = [
                r["parse_status"]
                for r in connection.execute(
                    "SELECT parse_status FROM llm_calls ORDER BY id"
                ).fetchall()
            ]
        self.assertEqual(result.parse_status, "retry")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(statuses, ["retry", "ok"])

    def test_missing_stop_reason_still_retries(self) -> None:
        """Fixtures that don't queue a stop_reason should fall back to retry-once."""
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [
            {"topic": "Health"},
            {"topic": "Health", "confidence": 0.5},
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            result = extractor.run_one("p", "1.0.0", {"title": "x"})
        self.assertEqual(result.parse_status, "retry")
        self.assertEqual(len(fake.calls), 2)


class AnthropicRunnerConfigTests(unittest.TestCase):
    """Constructor wiring for ``AnthropicRunner`` — no network."""

    def test_default_max_tokens_matches_haiku_4_5_ceiling(self) -> None:
        from yt_channel_analyzer.extractor.anthropic_runner import (
            DEFAULT_MAX_TOKENS,
            AnthropicRunner,
        )
        runner = AnthropicRunner(api_key="sk-test")
        self.assertEqual(DEFAULT_MAX_TOKENS, 64000)
        self.assertEqual(runner._max_tokens, DEFAULT_MAX_TOKENS)
        self.assertIsNone(runner.last_stop_reason)
        self.assertEqual(runner.last_batch_stop_reasons, [])

    def test_max_tokens_override_respected(self) -> None:
        from yt_channel_analyzer.extractor.anthropic_runner import AnthropicRunner
        runner = AnthropicRunner(api_key="sk-test", max_tokens=8192)
        self.assertEqual(runner._max_tokens, 8192)


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


class TokenUsageTests(_RegistryIsolation):
    def setUp(self) -> None:
        super().setUp()
        register_prompt(
            name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="sys"
        )

    def test_audit_row_records_token_usage_when_runner_reports_it(self) -> None:
        fake = FakeLLMRunner()
        fake.add_response("p", "1.0.0", {"topic": "Health", "confidence": 0.9})
        fake.queue_usage(input_tokens=123, output_tokens=45)
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            extractor.run_one("p", "1.0.0", {"title": "x"})
            row = connection.execute(
                "SELECT tokens_in, tokens_out, cost_estimate_usd FROM llm_calls"
            ).fetchone()
        self.assertEqual(row["tokens_in"], 123)
        self.assertEqual(row["tokens_out"], 45)
        self.assertIsNone(row["cost_estimate_usd"])

    def test_audit_row_tokens_null_when_runner_does_not_report_usage(self) -> None:
        fake = FakeLLMRunner()
        fake.add_response("p", "1.0.0", {"topic": "Health", "confidence": 0.9})
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            extractor.run_one("p", "1.0.0", {"title": "x"})
            row = connection.execute(
                "SELECT tokens_in, tokens_out FROM llm_calls"
            ).fetchone()
        self.assertIsNone(row["tokens_in"])
        self.assertIsNone(row["tokens_out"])

    def test_retry_path_records_per_call_usage(self) -> None:
        fake = FakeLLMRunner()
        fake.queue_responses("p", "1.0.0", [
            {"topic": "Health"},
            {"topic": "Health", "confidence": 0.5},
        ])
        fake.queue_usage(input_tokens=100, output_tokens=10)
        fake.queue_usage(input_tokens=110, output_tokens=20)
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            extractor.run_one("p", "1.0.0", {"title": "x"})
            rows = connection.execute(
                "SELECT parse_status, tokens_in, tokens_out FROM llm_calls ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [(r["parse_status"], r["tokens_in"], r["tokens_out"]) for r in rows],
            [("retry", 100, 10), ("ok", 110, 20)],
        )

    def test_batch_audit_row_records_per_result_tokens(self) -> None:
        fake = FakeLLMRunner(batch_supported=True)
        fake.queue_batch_responses("p", "1.0.0", [
            {"topic": "A", "confidence": 0.1},
            {"topic": "B", "confidence": 0.2},
            {"topic": "C", "confidence": 0.3},
        ])
        fake.queue_batch_usages([
            {"input_tokens": 10, "output_tokens": 1},
            {"input_tokens": 20, "output_tokens": 2},
            None,
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake, batch_threshold=2)
            jobs = [
                ("p", "1.0.0", {"title": str(i)}, None) for i in range(3)
            ]
            extractor.run_batch(jobs)
            rows = connection.execute(
                "SELECT tokens_in, tokens_out FROM llm_calls ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [(r["tokens_in"], r["tokens_out"]) for r in rows],
            [(10, 1), (20, 2), (None, None)],
        )


class PricingTests(unittest.TestCase):
    def test_known_model_computes_input_plus_output(self) -> None:
        cost = estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 6.00)

    def test_batch_flag_halves_cost(self) -> None:
        list_cost = estimate_cost("claude-haiku-4-5-20251001", 100, 50)
        batch_cost = estimate_cost("claude-haiku-4-5-20251001", 100, 50, is_batch=True)
        self.assertAlmostEqual(batch_cost, list_cost / 2)

    def test_unknown_model_returns_none(self) -> None:
        self.assertIsNone(estimate_cost("not-a-real-model", 100, 50))

    def test_missing_tokens_returns_none(self) -> None:
        self.assertIsNone(estimate_cost("claude-haiku-4-5-20251001", None, 50))
        self.assertIsNone(estimate_cost("claude-haiku-4-5-20251001", 100, None))

    def test_table_covers_three_claude_4_models(self) -> None:
        self.assertEqual(
            set(MODEL_PRICES),
            {"claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-7"},
        )


class CostEstimateAuditTests(_RegistryIsolation):
    def setUp(self) -> None:
        super().setUp()
        register_prompt(
            name="p", version="1.0.0", render=_render, schema=SIMPLE_SCHEMA, system="sys"
        )

    def test_audit_row_records_cost_when_model_is_priced(self) -> None:
        fake = FakeLLMRunner()
        fake.model = "claude-haiku-4-5-20251001"
        fake.add_response("p", "1.0.0", {"topic": "Health", "confidence": 0.9})
        fake.queue_usage(input_tokens=1000, output_tokens=200)
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake)
            extractor.run_one("p", "1.0.0", {"title": "x"})
            row = connection.execute(
                "SELECT cost_estimate_usd FROM llm_calls"
            ).fetchone()
        self.assertAlmostEqual(row["cost_estimate_usd"], (1000 * 1.0 + 200 * 5.0) / 1_000_000)

    def test_batch_audit_row_records_discounted_cost(self) -> None:
        fake = FakeLLMRunner(batch_supported=True)
        fake.model = "claude-haiku-4-5-20251001"
        fake.queue_batch_responses("p", "1.0.0", [
            {"topic": "A", "confidence": 0.1},
            {"topic": "B", "confidence": 0.2},
        ])
        fake.queue_batch_usages([
            {"input_tokens": 1000, "output_tokens": 200},
            {"input_tokens": 2000, "output_tokens": 400},
        ])
        with TemporaryDirectory() as td:
            connection = _open_db(td)
            extractor = Extractor(connection=connection, runner=fake, batch_threshold=2)
            jobs = [("p", "1.0.0", {"title": str(i)}, None) for i in range(2)]
            extractor.run_batch(jobs)
            costs = [
                r["cost_estimate_usd"]
                for r in connection.execute(
                    "SELECT cost_estimate_usd FROM llm_calls ORDER BY id"
                )
            ]
        self.assertAlmostEqual(costs[0], 0.5 * (1000 * 1.0 + 200 * 5.0) / 1_000_000)
        self.assertAlmostEqual(costs[1], 0.5 * (2000 * 1.0 + 400 * 5.0) / 1_000_000)


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


class SchemaNullOptionalTests(unittest.TestCase):
    """An optional property sent as explicit ``null`` validates like an omitted key."""

    SCHEMA: dict = {
        "type": "object",
        "additionalProperties": False,
        "required": ["topic"],
        "properties": {
            "topic": {"type": "string", "minLength": 1},
            "subtopic": {"type": "string", "minLength": 1},
        },
    }

    def test_null_optional_property_is_accepted(self) -> None:
        from yt_channel_analyzer.extractor.schema import validate
        validate({"topic": "T", "subtopic": None}, self.SCHEMA)  # no raise

    def test_omitted_optional_property_is_accepted(self) -> None:
        from yt_channel_analyzer.extractor.schema import validate
        validate({"topic": "T"}, self.SCHEMA)  # no raise

    def test_null_required_property_still_rejected(self) -> None:
        from yt_channel_analyzer.extractor.schema import validate
        with self.assertRaises(SchemaValidationError):
            validate({"topic": None}, self.SCHEMA)

    def test_present_optional_property_still_validated(self) -> None:
        from yt_channel_analyzer.extractor.schema import validate
        with self.assertRaises(SchemaValidationError):
            validate({"topic": "T", "subtopic": ""}, self.SCHEMA)


if __name__ == "__main__":
    unittest.main()
