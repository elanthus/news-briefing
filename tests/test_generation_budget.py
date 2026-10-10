"""Offline spending/deadline regression coverage across calls, models and days."""
import copy
import json
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import daily_publish
from agent_runner.budget import GenerationBudget, GenerationBudgetExceeded
from agent_runner.models import ProviderError
from agent_runner.runner import RunnerSettings, run_workflow
from run_daily_briefing import run_fallback_chain
from tests.test_briefing_output import ROOT, fixture_contract
from tests.test_run_briefing import FakeProvider


class BilledProvider(FakeProvider):
    def __init__(self, output, cost=None, fail=None, advance=None):
        super().__init__([output])
        self.cost = cost
        self.fail = fail
        self.advance = advance

    def generate(self, request):
        if self.fail:
            self.requests.append(request)
            raise self.fail
        response = super().generate(request)
        if self.advance:
            self.advance()
        return replace(response, cost_usd=self.cost)


def settings(root):
    corpus, _config, _projection, _output = fixture_contract()
    source = root / "input.json"
    source.write_text(json.dumps(corpus))
    return RunnerSettings(ROOT / "fixtures/briefing-config-2026-08-11.json",
                          ROOT / "briefing-runner-prompt.md", root / "briefing.md", source)


class GenerationBudgetTests(unittest.TestCase):
    def test_reservation_accounts_for_input_and_full_output_allowance(self):
        budget = GenerationBudget(limit_usd=0.07)
        # Output alone fits ($0.06), but input adds $0.02 and prevents IO.
        with self.assertRaises(GenerationBudgetExceeded):
            budget.reserve("x" * 1000, {}, 1000, 600)
        self.assertEqual(budget.calls, 0)
        self.assertEqual(budget.stop_reason, "spending_allowance")

    def test_unknown_cost_keeps_reservation_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            budget = GenerationBudget.open(path, limit_usd=0.1)
            budget.reserve("prompt", {}, 1000, 600)
            budget.settle(None)
            restored = GenerationBudget.open(path)
            self.assertEqual(restored.unknown_cost_calls, 1)
            self.assertGreater(restored.unknown_reserved_usd, 0.06)
            with self.assertRaises(GenerationBudgetExceeded):
                restored.reserve("prompt", {}, 1000, 600)
            self.assertEqual(restored.calls, 1)

    def test_interrupted_reservation_never_grants_a_new_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            budget = GenerationBudget.open(path)
            budget.reserve("prompt", {}, 1000, 600)
            restored = GenerationBudget.open(path)
            self.assertFalse(restored.available())
            self.assertEqual(restored.stop_reason, "ambiguous_completion")
            self.assertGreater(restored.in_flight_usd, 0)

    def test_spend_exhaustion_stops_the_next_correction_and_fallback(self):
        _corpus, _config, _projection, output = fixture_contract()
        broken = copy.deepcopy(output)
        topic = next(iter(broken["sections"].values()))["topics"][0]
        topic["citation_refs"] = ["unknown"]
        provider = BilledProvider(broken, cost=25)
        with tempfile.TemporaryDirectory() as directory, patch(
            "run_daily_briefing.provider_for", return_value=provider
        ) as factory:
            root = Path(directory)
            result = run_fallback_chain(settings(root), root / "chain", max_tokens=100_000)
            log = json.loads((root / "chain/fallback-log.json").read_text())
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(log["generation_budget"]["known_spend_usd"], 25)
        self.assertEqual(log["generation_budget"]["stop_reason"], "spending_allowance")
        self.assertEqual(len(log["attempts"]), 1)

    def test_timeout_billing_uncertainty_stops_fallback(self):
        _corpus, _config, _projection, output = fixture_contract()
        provider = BilledProvider(output, fail=ProviderError("deadline", transient=True,
                                                            ambiguous_completion=True))
        with tempfile.TemporaryDirectory() as directory, patch(
            "run_daily_briefing.provider_for", return_value=provider
        ) as factory:
            root = Path(directory)
            run_fallback_chain(settings(root), root / "chain", max_tokens=100_000)
            log = json.loads((root / "chain/fallback-log.json").read_text())
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(log["generation_budget"]["unknown_cost_calls"], 1)
        self.assertEqual(log["generation_budget"]["stop_reason"], "ambiguous_completion")
        self.assertGreater(log["generation_budget"]["unknown_reserved_usd"], 0)

    def test_output_capture_overflow_stops_fallback_without_refunding_unknown_cost(self):
        _corpus, _config, _projection, output = fixture_contract()
        provider = BilledProvider(output, fail=ProviderError("capture overflow", transient=False,
            output_truncated=True, ambiguous_completion=True))
        with tempfile.TemporaryDirectory() as directory, patch(
            "run_daily_briefing.provider_for", return_value=provider
        ) as factory:
            root = Path(directory)
            run_fallback_chain(settings(root), root / "chain", max_tokens=100_000)
            log = json.loads((root / "chain/fallback-log.json").read_text())
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(log["generation_budget"]["stop_reason"], "ambiguous_completion")
        self.assertGreater(log["generation_budget"]["unknown_reserved_usd"], 0)
        self.assertTrue(log["attempts"][0]["failure"]["output_truncated"])

    def test_ready_result_survives_spend_exhaustion_on_final_call(self):
        _corpus, _config, _projection, output = fixture_contract()
        provider = BilledProvider(output, cost=13)
        with tempfile.TemporaryDirectory() as directory, patch(
            "run_daily_briefing.provider_for", return_value=provider
        ) as factory:
            root = Path(directory)
            result = run_fallback_chain(settings(root), root / "chain", max_tokens=100_000)
            log = json.loads((root / "chain/fallback-log.json").read_text())
            self.assertTrue((root / "briefing.md").is_file())
        self.assertEqual(result.status, "ready")
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(log["generation_budget"]["known_spend_usd"], 26)
        self.assertEqual(log["generation_budget"]["stop_reason"], "spending_allowance")

    def test_elapsed_time_stops_prose_and_clamps_call_deadline(self):
        _corpus, _config, _projection, output = fixture_contract()
        now = [time.time()]
        budget = GenerationBudget(deadline_epoch=now[0] + 5)
        provider = BilledProvider(output, cost=0.1, advance=lambda: now.__setitem__(0, now[0] + 6))
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.budget.time.time", side_effect=lambda: now[0]
        ):
            root = Path(directory)
            with self.assertRaises(GenerationBudgetExceeded):
                run_workflow(provider, settings(root), root / "run", budget=budget)
            manifest = json.loads((root / "run/manifest.json").read_text())
        self.assertEqual(len(provider.requests), 1)
        self.assertLessEqual(provider.requests[0].timeout_seconds, 5)
        self.assertEqual(manifest["generation_budget"]["stop_reason"], "elapsed_time")

    def test_exhausted_backfill_preserves_existing_ready_and_failure_logs(self):
        for status in ("ready", "failed"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run_dir = root / "runs/2026-10-10"
                run_dir.mkdir(parents=True)
                log = run_dir / "fallback-log.json"
                prior = json.dumps({"status": status, "selected_run_dir": "01-model",
                                    "attempts": [{"status": status}]}).encode()
                log.write_bytes(prior)
                budget = GenerationBudget.open(root / "runs/generation-budget.json")
                budget.known_spend_usd = budget.limit_usd
                budget._persist()
                commands = []
                def run(command, commands=commands):
                    commands.append(command)
                    return subprocess.CompletedProcess(command, 0, "", "")
                result = daily_publish.generate_reports(today=date(2026, 10, 10),
                    window_start="start", window_end="end", event_name="schedule", manual_mode="",
                    manual_report_date="", root=root, runner=run)
                self.assertEqual(result, 1)
                self.assertFalse(any(command[1] == "run_daily_briefing.py" for command in commands))
                self.assertTrue(any(command[1] == "prepare_publication.py" for command in commands))
                self.assertEqual(log.read_bytes(), prior)

    def test_backfill_never_resets_spend_time_or_unknown_allowance_per_day(self):
        for exhausted in ("spend", "time", "unknown"):
            with self.subTest(exhausted=exhausted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "corpora").mkdir()
                for day in range(4, 11):
                    (root / "corpora" / f"2026-10-{day:02d}.json").write_text("{}")
                generated = []
                def run(command, generated=generated, exhausted=exhausted):
                    if command[1] == "run_daily_briefing.py":
                        generated.append(command)
                        path = Path(command[command.index("--generation-budget") + 1])
                        budget = GenerationBudget.open(path)
                        if exhausted == "spend":
                            budget.known_spend_usd = budget.limit_usd
                        elif exhausted == "time":
                            budget.deadline_epoch = 0
                        else:
                            budget.unknown_cost_calls = 1
                            budget.unknown_reserved_usd = budget.limit_usd
                        budget._persist()
                        report = Path(command[command.index("--output") + 1])
                        report.parent.mkdir(parents=True, exist_ok=True)
                        report.write_text("ready result")
                    return subprocess.CompletedProcess(command, 0, "", "")
                status = daily_publish.generate_reports(today=date(2026, 10, 10), window_start="start",
                    window_end="end", event_name="workflow_dispatch", manual_mode="backfill-7-days",
                    manual_report_date="", root=root, runner=run)
                self.assertEqual(status, 1)
                self.assertEqual(len(generated), 1)
                self.assertEqual((root / "reports/2026-10-04.md").read_text(), "ready result")
                stopped = json.loads((root / "runs/2026-10-05/fallback-log.json").read_text())
                self.assertTrue(stopped["generation_budget"]["stop_reason"])
                self.assertEqual(stopped["attempts"], [])
