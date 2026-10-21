import copy
import http.client
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_runner.checkpoint import sha256_file, write_json_atomic
from agent_runner.decisions import ENDPOINT, MAX_RESPONSE_BYTES, JevClient
from agent_runner.jev_review import (
    MAX_CALLS,
    _payload,
    build_tasks,
    load_citation_urls,
    load_topics,
    load_verified_run,
    review_run,
)
from agent_runner.models import ProviderError
from agent_runner.output import (
    DESTINATION_REDACTION,
    project_corpus,
    project_selected_evidence,
    redecode_redacted_text,
)
from agent_runner.runner import RunnerSettings, run_workflow
from run_daily_briefing import ChainResult
from tests.test_briefing_output import ROOT, fixture_contract, selection_from_output
from tests.test_run_briefing import FakeProvider


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


class DecisionsTests(unittest.TestCase):
    def payload(self):
        return {"model": "typesafe/jev-1.13-20260917", "answers": {
            "question": {"type": "noul", "noul": 0.9}},
            "usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.0000042}}

    def evaluate(self, payload):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.decisions._urlopen", return_value=Response(payload)
        ) as opened:
            result = JevClient().evaluate({"title": "Evidence"}, {
                "question": {"type": "noul", "instructions": "Is this supported?"}})
        return result, opened

    def test_live_api_shape_and_fixed_authenticated_endpoint(self):
        result, opened = self.evaluate(self.payload())
        self.assertEqual(result["probabilities"], {"question": 0.9})
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertNotIn("secret", json.dumps(result))

    def test_malformed_answers_and_tool_events_are_rejected(self):
        variants = []
        for value in (True, float("nan"), float("inf"), -0.01, 1.01, "0.9", 10 ** 400):
            payload = self.payload()
            payload["answers"]["question"]["noul"] = value
            variants.append(payload)
        for answers in ({}, {"other": {"type": "noul", "noul": 0.9}},
                        {"question": {"type": "choice", "choice": "yes"}},
                        {"question": {"type": "noul", "noul": 0.9, "tool_calls": []}}):
            payload = self.payload()
            payload["answers"] = answers
            variants.append(payload)
        payload = self.payload()
        payload["tool_calls"] = [{"name": "browse"}]
        variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                self.evaluate(payload)

    def test_bad_usage_is_rejected_and_unknown_cost_preserved(self):
        for cost in (True, -1, float("nan"), "0.01", 10 ** 400):
            payload = self.payload()
            payload["usage"]["cost"] = cost
            with self.subTest(cost=cost), self.assertRaises(ProviderError):
                self.evaluate(payload)
        payload = self.payload()
        payload["usage"].pop("cost")
        result, _ = self.evaluate(payload)
        self.assertIsNone(result["cost_usd"])

    def test_oversized_response_and_redirect_fail_without_retry_or_body_leak(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.decisions._urlopen", return_value=io.BytesIO(b" " * (MAX_RESPONSE_BYTES + 1))
        ), self.assertRaisesRegex(ProviderError, "output budget"):
            JevClient().evaluate({}, {"q": {"type": "noul"}})
        error = urllib.error.HTTPError(ENDPOINT, 302, "redirect", {}, io.BytesIO(b"secret"))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.decisions._urlopen", side_effect=error
        ) as opened, self.assertRaisesRegex(ProviderError, "Jev HTTP 302") as caught:
            JevClient().evaluate({}, {"q": {"type": "noul"}})
        opened.assert_called_once()
        self.assertNotIn("secret", str(caught.exception))

    def test_http_protocol_errors_are_ambiguous_typed_failures_without_retry_or_body_leak(self):
        for error in (http.client.BadStatusLine("secret remote line"),
                      http.client.IncompleteRead(b"secret partial response", 50)):
            with self.subTest(error=type(error).__name__), patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}):
                if isinstance(error, http.client.IncompleteRead):
                    response = MagicMock()
                    response.__enter__.return_value.read.side_effect = error
                    opener = patch("agent_runner.decisions._urlopen", return_value=response)
                else:
                    opener = patch("agent_runner.decisions._urlopen", side_effect=error)
                with opener as opened, self.assertRaises(ProviderError) as caught:
                    JevClient().evaluate({}, {"q": {"type": "noul"}})
                opened.assert_called_once()
                self.assertTrue(caught.exception.ambiguous_completion)
                self.assertFalse(caught.exception.transient)
                self.assertNotIn("secret", str(caught.exception))

    def test_deeply_nested_json_is_a_typed_provider_failure(self):
        raw = b"[" * 2000 + b"0" + b"]" * 2000
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.decisions._urlopen", return_value=io.BytesIO(raw)
        ), self.assertRaises(ProviderError):
            JevClient().evaluate({}, {"q": {"type": "noul"}})


class FakeJudge(JevClient):
    def __init__(self, *, cost=0.00001, fail_after=None):
        self.calls = []
        self.cost = cost
        self.fail_after = fail_after

    def evaluate(self, state, questions, *, timeout=30):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise ProviderError("secret remote error", transient=False)
        self.calls.append((state, questions))
        return {"probabilities": {key: 0.9 for key in questions}, "model": "typesafe/jev-test",
                "cost_usd": self.cost, "input_tokens": 100, "output_tokens": 20, "latency_ms": 1}


class JevReviewTests(unittest.TestCase):
    def test_raised_call_guard_stays_bounded_and_records_its_limit(self):
        self.assertEqual(MAX_CALLS, 256)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            topics = [{'included': True, 'position': {}, 'headline': 'Short', 'prose': 'Short',
                       'evidence': [{'title': 'Short'}]}]
            task = {'check': 'unsupported_claim', 'positions': [{}], 'state': {'topic': topics[0]}}
            judge = FakeJudge()
            original_payload = _payload

            def one_per_batch(tasks):
                state, questions, size = original_payload(tasks)
                return state, questions, size if len(tasks) == 1 else 100_000

            with patch('agent_runner.jev_review.load_verified_run',
                       return_value=SimpleNamespace(root=(root / 'run').resolve(), topics=topics, hashes={})), patch(
                'agent_runner.jev_review.load_citation_urls', return_value={'{}': [['https://example.com']]}
            ), patch('agent_runner.jev_review.build_tasks', return_value=([task] * (MAX_CALLS + 1), 0)), patch(
                'agent_runner.jev_review._payload', side_effect=one_per_batch
            ):
                report = review_run(root / 'run', root / 'review', client=judge)
            self.assertEqual(report['status'], 'partial')
            self.assertEqual(report['stop_code'], 'budget')
            self.assertEqual(report['max_calls'], MAX_CALLS)
            self.assertEqual(len(judge.calls), MAX_CALLS)
            self.assertEqual(report['cost_ceiling_usd'], 0.10)
            self.assertEqual(report['max_review_seconds'], 120)

    def make_run(self, root, corpus=None):
        corpus, _config, _projected, output = fixture_contract(corpus)
        corpus_path = root / "input.json"
        corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
        settings = RunnerSettings(
            config_path=ROOT / "fixtures/briefing-config-2026-08-11.json",
            prompt_path=ROOT / "briefing-runner-prompt.md",
            output_path=root / "briefing.md", corpus_path=corpus_path)
        result = run_workflow(FakeProvider([output]), settings, root / "run")
        self.assertEqual(result.status, "ready")
        return root / "run"

    # The first corpus item is the first topic of the first section. Its summary
    # carries a destination plus stray CommonMark escapes; the projection masks the
    # destination and, since #291, removes the escapes from that string.
    CURRENT_SUMMARY = f"Read the thread at {DESTINATION_REDACTION} - then the follow-up."
    HISTORICAL_SUMMARY = f"Read the thread at {DESTINATION_REDACTION} \\- then the follow\\-up."

    def escaped_corpus(self):
        corpus = copy.deepcopy(fixture_contract()[0])
        item = next(iter(corpus["categories"].values()))[0]
        item["title"] = "Follow-up thread"
        item["summary"] = "Read the thread at https://example.test/thread \\- then the follow\\-up."
        return corpus

    @staticmethod
    def rewrite_evidence(run, before, after):
        """Replace one recorded evidence string and re-bind its manifest hash."""
        manifest = json.loads((run / "manifest.json").read_text())
        evidence = json.loads((run / "selected-evidence.json").read_text())
        count = 0

        def walk(value):
            nonlocal count
            if isinstance(value, str):
                count += value == before
                return after if value == before else value
            if isinstance(value, list):
                return [walk(item) for item in value]
            if isinstance(value, dict):
                return {key: walk(item) for key, item in value.items()}
            return value

        write_json_atomic(run / "selected-evidence.json", walk(evidence))
        manifest["artifacts"]["selected-evidence.json"] = sha256_file(run / "selected-evidence.json")
        write_json_atomic(run / "manifest.json", manifest)
        return count

    def test_pre_291_escapes_inside_masked_recorded_strings_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(Path(directory), self.escaped_corpus())
            self.assertEqual(self.rewrite_evidence(run, self.CURRENT_SUMMARY, self.HISTORICAL_SUMMARY), 1)
            state = load_verified_run(run)
            corpus = json.loads((run / "corpus.json").read_text())
            frozen = json.loads((run / "frozen-selection.json").read_text())
            self.assertEqual(state.evidence, project_selected_evidence(frozen, project_corpus(corpus)))
            self.assertEqual(state.hashes["selected-evidence.json"], sha256_file(run / "selected-evidence.json"))
            summaries = [source["summary"] for topic in state.topics for source in topic["evidence"]]
            self.assertIn(self.CURRENT_SUMMARY, summaries)
            self.assertNotIn(self.HISTORICAL_SUMMARY, summaries)

    def test_otherwise_altered_recorded_evidence_is_still_rejected(self):
        marker, current = DESTINATION_REDACTION, self.CURRENT_SUMMARY
        alterations = {
            "escape in a marker-free string": ("Follow-up thread", "Follow\\-up thread"),
            "entity in a marker-free string": ("Follow-up thread", "Follow&#45;up thread"),
            "live URL in a masked slot": (current, current.replace(marker, "https://attacker.invalid/x")),
            "escaped URL in a masked slot": (current, current.replace(marker, "https\\://attacker.invalid/x")),
            "entity-encoded URL in a masked slot": (current, current.replace(marker, "https&#58;//attacker.invalid/x")),
            "changed text in a masked string": (current, current + " Extra claim."),
        }
        for name, (before, after) in alterations.items():
            with self.subTest(alteration=name), tempfile.TemporaryDirectory() as directory:
                run = self.make_run(Path(directory), self.escaped_corpus())
                self.assertEqual(self.rewrite_evidence(run, before, after), 1)
                with self.assertRaisesRegex(ValueError, "differs"):
                    load_topics(run)

    def test_current_recorded_evidence_is_accepted_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(Path(directory), self.escaped_corpus())
            recorded = json.loads((run / "selected-evidence.json").read_text())
            self.assertIn(self.CURRENT_SUMMARY, json.dumps(recorded))
            self.assertEqual(redecode_redacted_text(recorded), recorded)
            self.assertEqual(load_verified_run(run).evidence, recorded)

    def test_review_is_advisory_redacted_and_source_artifacts_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root)
            before = {path.name: path.read_bytes() for path in run.iterdir() if path.is_file()}
            judge = FakeJudge()
            report = review_run(run, root / "review", client=judge)
            after = {path.name: path.read_bytes() for path in run.iterdir() if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["flagged_checks"], len(report["results"]))
        serialized = json.dumps(judge.calls)
        self.assertNotRegex(serialized, r"https?://|\b(?:citation|item)_\d+\b")
        for _state, questions in judge.calls:
            for index, question in enumerate(questions.values()):
                self.assertIn(f"checks[{index}]", question["instructions"])

    def test_verified_value_reuses_loaded_evidence_but_a_new_consumer_rejects_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root)
            state = load_verified_run(run)
            urls = load_citation_urls(state)
            (run / "frozen-selection.json").write_text("{}")
            with patch("agent_runner.jev_review._read_json", side_effect=AssertionError("unexpected artifact read")):
                self.assertEqual(load_citation_urls(state), urls)
                report = review_run(run, root / "review", client=FakeJudge(), verified_run=state)
            self.assertEqual(report["status"], "complete")
            with self.assertRaisesRegex(ValueError, "artifact hash"):
                load_topics(run)

    def test_publication_rejects_manifest_changed_during_verification(self):
        from agent_runner.publication import verified_publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root)

            def changed_after_load(path):
                state = load_verified_run(path)
                manifest = json.loads((path / "manifest.json").read_text())
                manifest["attempts"] = []
                write_json_atomic(path / "manifest.json", manifest)
                return state

            with patch("agent_runner.publication.load_verified_run", side_effect=changed_after_load):
                with self.assertRaisesRegex(ValueError, "manifest changed during verification"):
                    verified_publication(run)

    def test_hash_mutation_prevents_any_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root)
            (run / "selected-evidence.json").write_text("{}")
            judge = FakeJudge()
            report = review_run(run, root / "review", client=judge)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(judge.calls)

    def test_reordered_final_topics_are_not_paired_by_counts_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root)
            manifest = json.loads((run / "manifest.json").read_text())
            name = next(row["structured_artifact"] for row in manifest["attempts"]
                        if row["index"] == manifest["final"]["attempt"])
            candidate = json.loads((run / name).read_text())
            topics = next(section["topics"] for section in candidate["sections"].values()
                          if len(section["topics"]) > 1)
            topics[0], topics[1] = topics[1], topics[0]
            write_json_atomic(run / name, candidate)
            manifest["artifacts"][name] = sha256_file(run / name)
            write_json_atomic(run / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "aligns"):
                load_topics(run)

    def test_evidence_is_recomputed_even_if_its_hash_is_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root)
            manifest = json.loads((run / "manifest.json").read_text())
            evidence = json.loads((run / "selected-evidence.json").read_text())
            topic = next(section["topics"][0] for section in evidence["sections"].values()
                         if section["topics"])
            topic["evidence"][0]["title"] = "Invented support"
            write_json_atomic(run / "selected-evidence.json", evidence)
            manifest["artifacts"]["selected-evidence.json"] = sha256_file(run / "selected-evidence.json")
            write_json_atomic(run / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "differs"):
                load_topics(run)

    def test_partial_pair_coverage_unknown_billing_and_provider_failures(self):
        for judge, max_pairs in ((FakeJudge(), 0), (FakeJudge(cost=None), 1000),
                                 (FakeJudge(fail_after=1), 1000)):
            with self.subTest(judge=judge, max_pairs=max_pairs), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                report = review_run(self.make_run(root), root / "review", client=judge, max_pairs=max_pairs)
            self.assertEqual(report["status"], "partial")
            self.assertNotIn("secret remote error", json.dumps(report))
            if judge.cost is None:
                self.assertEqual(len(judge.calls), 1)
                self.assertEqual(report["unknown_cost_calls"], 1)

    def test_duplicate_and_grouping_questions_cover_evidence_only(self):
        _corpus, _config, projected, output = fixture_contract()
        evidence = project_selected_evidence(selection_from_output(output), projected)
        topic = {"included": True, "position": {"index": 0}, "headline": "Same event",
                 "prose": "Faithful", "evidence": next(
                     section["topics"][0]["evidence"] for section in evidence["sections"].values())}
        topic["evidence"] *= 2
        excluded = copy.deepcopy(topic)
        excluded["included"] = False
        tasks, omitted = build_tasks([topic, excluded], 10)
        self.assertEqual(omitted, 0)
        self.assertEqual(sum(task["check"] == "duplicate" for task in tasks), 1)
        self.assertEqual(sum(task["check"] == "unsafe_grouping" for task in tasks), 2)
        self.assertEqual(sum(task["check"] == "unsupported_claim" for task in tasks), 1)
        _state, questions, _size = _payload(tasks)
        for index, question in enumerate(questions.values()):
            self.assertIn(f"checks[{index}]", question["instructions"])

    def test_oversized_checks_are_skipped_explicitly_and_budget_stops_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            topics = [{"included": True, "position": {}, "headline": "Large", "prose": "x" * 30_000,
                       "evidence": [{"title": "x" * 30_000}]}]
            judge = FakeJudge()
            with patch("agent_runner.jev_review.load_verified_run",
                       return_value=SimpleNamespace(root=(root / "run").resolve(), topics=topics, hashes={})), patch(
                "agent_runner.jev_review.load_citation_urls", return_value={"{}": [["https://example.com"]]}
            ):
                report = review_run(root / "run", root / "review", client=judge)
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["skipped_oversized_checks"], 4)
            self.assertFalse(judge.calls)
            topics[0]["headline"] = "Short"
            topics[0]["prose"] = "Short"
            topics[0]["evidence"] = [{"title": "Short"}]
            with patch("agent_runner.jev_review.load_verified_run",
                       return_value=SimpleNamespace(root=(root / "run").resolve(), topics=topics, hashes={})), patch(
                "agent_runner.jev_review.load_citation_urls", return_value={"{}": [["https://example.com"]]}
            ):
                report = review_run(root / "run", root / "budget", client=judge, cost_ceiling=0.000000001)
            self.assertEqual(report["status"], "partial")
            self.assertFalse(judge.calls)

    def test_output_cannot_overwrite_or_nest_in_source_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for output in (root, root / "run", root / "run" / "review"):
                with self.assertRaisesRegex(ValueError, "separate"):
                    review_run(root / "run", output)

    def test_truncated_response_closes_review_with_unknown_billing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root)
            before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
            response = MagicMock()
            response.__enter__.return_value.read.side_effect = http.client.IncompleteRead(
                b"private-http-response-marker-209", 20)
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
                "agent_runner.decisions._urlopen", return_value=response
            ) as opened:
                report = review_run(run, root / "review")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["calls"][0]["status"], "failed_billing_unknown")
            self.assertEqual(report["unknown_cost_calls"], 1)
            self.assertNotIn("private-http-response-marker-209", json.dumps(report))
            self.assertEqual(before, {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()})
            opened.assert_called_once()

    def test_daily_repair_exception_preserves_ready_exit_and_logs_only_type(self):
        import run_daily_briefing

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "chain"
            selected = run_dir / "candidate"
            output = root / "briefing.md"
            output.write_text("Original ready briefing.")
            argv = ["run_daily_briefing.py", "--output", str(output), "--force", "--run-dir", str(run_dir),
                    "--corpus", str(ROOT / "fixtures/current-corpus.json"),
                    "--jev-review-dir", str(run_dir / "jev-review")]
            result = ChainResult("ready", "test", selected, run_dir)
            for exception in (RuntimeError("secret remote error"), ProviderError("secret", transient=False)):
                stderr = io.StringIO()
                with self.subTest(exception=type(exception).__name__), patch("sys.argv", argv), patch(
                    "run_daily_briefing.run_fallback_chain", return_value=result
                ), patch("run_daily_briefing.daily_semantic_review", side_effect=exception), \
                        redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    self.assertEqual(run_daily_briefing.main(), 0)
                self.assertIn(type(exception).__name__, stderr.getvalue())
                self.assertNotIn("secret", stderr.getvalue())
                self.assertEqual(output.read_text(), "Original ready briefing.")

    def test_daily_cli_rejects_removed_mode_flag(self):
        import run_daily_briefing

        argv = ["run_daily_briefing.py", "--output", "unused.md", "--run-dir", "unused-run",
                "--corpus", "unused.json", "--jev-repair-mode", "apply"]
        with patch("sys.argv", argv), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                run_daily_briefing.main()
        self.assertEqual(caught.exception.code, 2)

    def test_daily_cli_reviews_only_selected_ready_candidate(self):
        import run_daily_briefing

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "chain"
            selected = run_dir / "candidate"
            review_dir = run_dir / "jev-review"
            argv = ["run_daily_briefing.py", "--output", str(root / "briefing.md"),
                    "--run-dir", str(run_dir), "--corpus", str(ROOT / "fixtures/current-corpus.json"),
                    "--jev-review-dir", str(review_dir)]
            for status in ("ready", "failed"):
                result = ChainResult(status, "test", selected if status == "ready" else None, run_dir)
                with patch("sys.argv", argv), patch(
                    "run_daily_briefing.run_fallback_chain", return_value=result
                ), patch(
                    "run_daily_briefing.daily_semantic_review", return_value={"status": "complete"}
                ) as review, \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    exit_code = run_daily_briefing.main()
                self.assertEqual(exit_code, 0 if status == "ready" else 1)
                if status == "ready":
                    review.assert_called_once_with(selected, review_dir)
                else:
                    review.assert_not_called()

    def test_advisory_failure_leaves_single_runner_exit_code_unchanged(self):
        import run_briefing
        from agent_runner.runner import RunResult

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            argv = ["run_briefing.py", "--provider", "openrouter", "--model", "test",
                    "--output", str(root / "briefing.md"), "--run-dir", str(run_dir),
                    "--corpus", str(ROOT / "fixtures/current-corpus.json"),
                    "--jev-review-dir", str(root / "review")]
            with patch("sys.argv", argv), patch(
                "run_briefing.run_workflow", return_value=RunResult(0, run_dir, root / "briefing.md", "ready")
            ), patch("agent_runner.jev_review.review_run", side_effect=ValueError("invalid input")), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(run_briefing.main(), 0)


if __name__ == "__main__":
    unittest.main()
