from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_runner.models import ProviderError
from agent_runner.runner import RunnerSettings, RunResult
from run_daily_briefing import PRODUCTION_MODEL_CHAIN, run_fallback_chain


class DailyBriefingFallbackTests(unittest.TestCase):
    @staticmethod
    def _settings(root: Path) -> RunnerSettings:
        return RunnerSettings(
            config_path=root / "config.json",
            sources_path=root / "sources.json",
            prompt_path=root / "prompt.md",
            output_path=root / "briefing.md",
            corpus_path=root / "corpus.json",
        )

    def test_falls_back_in_order_and_preserves_failure_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            seen: list[tuple[str, str | None, int]] = []

            def fake_run(provider, run_settings, run_dir):
                seen.append((provider.model, provider.reasoning_effort, provider.max_tokens))
                run_dir.mkdir(parents=True)
                if provider.model == "tencent/hy3":
                    failure = ProviderError(
                        "openrouter HTTP 404: model not found",
                        transient=False,
                        status_code=404,
                        openrouter_model_404=True,
                    )
                    (run_dir / "manifest.json").write_text(
                        json.dumps({"status": "failed", "error": failure.record()}),
                        encoding="utf-8",
                    )
                    raise failure
                if provider.model == "deepseek/deepseek-v4-flash-0731":
                    preview = run_dir / "preview.md"
                    preview.write_text("quarantined DeepSeek report\n", encoding="utf-8")
                    (run_dir / "manifest.json").write_text(
                        json.dumps(
                            {
                                "status": "complete",
                                "final": {
                                    "status": "rejected",
                                    "findings": [
                                        {
                                            "check": "ungrounded_link",
                                            "message": "outside corpus",
                                        }
                                    ],
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    return RunResult(1, run_dir, preview, "rejected")
                final = run_dir / "final.md"
                content = b"ready MiMo report\n"
                final.write_bytes(content)
                run_settings.output_path.write_bytes(content)
                digest = hashlib.sha256(content).hexdigest()
                (run_dir / "manifest.json").write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "artifacts": {"final.md": digest},
                            "final": {
                                "status": "ready",
                                "artifact_type": "final",
                                "run_artifact": "final.md",
                                "output_sha256": digest,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return RunResult(0, run_dir, run_settings.output_path, "ready")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("run_daily_briefing.run_workflow", side_effect=fake_run),
                patch(
                    "run_daily_briefing._catalog_model_removed_from_openrouter",
                    return_value=True,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = run_fallback_chain(settings, root / "run", max_tokens=100_000)

            log = json.loads((root / "run/fallback-log.json").read_text(encoding="utf-8"))
            text_log = (root / "run/fallback.log").read_text(encoding="utf-8")

        self.assertEqual(
            seen,
            [
                ("tencent/hy3", "high", 100_000),
                ("deepseek/deepseek-v4-flash-0731", "high", 100_000),
                ("google/gemini-3.7-flash", None, 65_536),
            ],
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.selected_model, "google/gemini-3.7-flash")
        self.assertEqual(log["selected_model"], "google/gemini-3.7-flash")
        self.assertTrue(log["attempts"][0]["model_removed_from_openrouter"])
        self.assertIn("failure.md", log["attempts"][0]["quarantined_report"])
        self.assertIn("ungrounded_link: outside corpus", log["attempts"][1]["failure_reason"])
        self.assertTrue(log["attempts"][1]["quarantined_report"].endswith("preview.md"))
        self.assertIn("model_removed_from_openrouter=true", text_log)
        self.assertIn("quarantined_report=", text_log)
        self.assertIn("READY", stdout.getvalue())
        self.assertIn("FAILED", stderr.getvalue())

    def test_catalog_check_distinguishes_present_removed_and_unknown_models(self) -> None:
        class CatalogResponse:
            def __init__(self, payload):
                self.payload = json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        from run_daily_briefing import _catalog_model_removed_from_openrouter

        present = CatalogResponse({"data": [{"id": "tencent/hy3"}]})
        absent = CatalogResponse({"data": [{"id": "deepseek/deepseek-v4-flash-0731"}]})
        with patch("urllib.request.urlopen", return_value=present):
            self.assertFalse(_catalog_model_removed_from_openrouter("tencent/hy3"))
        with patch("urllib.request.urlopen", return_value=absent):
            self.assertTrue(_catalog_model_removed_from_openrouter("tencent/hy3"))
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(_catalog_model_removed_from_openrouter("tencent/hy3"))

    def test_ready_primary_stops_before_fallback_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            seen: list[str] = []

            def fake_run(provider, run_settings, run_dir):
                seen.append(provider.model)
                run_dir.mkdir(parents=True)
                content = b"ready\n"
                (run_dir / "final.md").write_bytes(content)
                run_settings.output_path.write_bytes(content)
                digest = hashlib.sha256(content).hexdigest()
                (run_dir / "manifest.json").write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "artifacts": {"final.md": digest},
                            "final": {
                                "status": "ready",
                                "artifact_type": "final",
                                "run_artifact": "final.md",
                                "output_sha256": digest,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return RunResult(0, run_dir, run_settings.output_path, "ready")

            with (
                patch("run_daily_briefing.run_workflow", side_effect=fake_run),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                result = run_fallback_chain(settings, root / "run", max_tokens=20_000)
            log = json.loads((root / "run/fallback-log.json").read_text(encoding="utf-8"))

        self.assertEqual(seen, [PRODUCTION_MODEL_CHAIN[0].model])
        self.assertEqual(result.selected_model, "tencent/hy3")
        self.assertEqual(len(log["attempts"]), 1)

    def test_ready_status_with_missing_artifacts_advances_to_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            seen: list[str] = []

            def fake_run(provider, run_settings, run_dir):
                seen.append(provider.model)
                run_dir.mkdir(parents=True)
                if provider.model == "tencent/hy3":
                    return RunResult(0, run_dir, run_settings.output_path, "ready")
                content = b"verified fallback\n"
                (run_dir / "final.md").write_bytes(content)
                run_settings.output_path.write_bytes(content)
                digest = hashlib.sha256(content).hexdigest()
                (run_dir / "manifest.json").write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "artifacts": {"final.md": digest},
                            "final": {
                                "status": "ready",
                                "artifact_type": "final",
                                "run_artifact": "final.md",
                                "output_sha256": digest,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return RunResult(0, run_dir, run_settings.output_path, "ready")

            with (
                patch("run_daily_briefing.run_workflow", side_effect=fake_run),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                result = run_fallback_chain(settings, root / "run", max_tokens=20_000)
            log = json.loads((root / "run/fallback-log.json").read_text(encoding="utf-8"))

        self.assertEqual(seen, ["tencent/hy3", "deepseek/deepseek-v4-flash-0731"])
        self.assertEqual(result.selected_model, "deepseek/deepseek-v4-flash-0731")
        self.assertEqual(
            log["attempts"][0]["failure_reason"],
            "ready result failed final artifact integrity checks",
        )


if __name__ == "__main__":
    unittest.main()
