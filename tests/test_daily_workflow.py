from __future__ import annotations

import re
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
WORKFLOW = (REPOSITORY / ".github/workflows/daily-briefing.yml").read_text(encoding="utf-8")


def _step(name: str) -> str:
    return WORKFLOW.split(f"- name: {name}", 1)[1].split("- name:", 1)[0]


def _steps() -> list[tuple[str, str]]:
    """Return every job step as (label, text); unnamed steps use their ``uses:`` value."""
    body = WORKFLOW.split("\n    steps:\n", 1)[1]
    chunks = re.split(r"^      - ", body, flags=re.M)[1:]
    steps = []
    for chunk in chunks:
        text = "- " + chunk
        name = re.search(r"^[- ] name: (.+)$", text, re.M)
        uses = re.search(r"uses: (\S+)", text)
        label = name.group(1) if name else uses.group(1) if uses else text
        steps.append((label, text))
    return steps


def _with_inputs(step: str) -> dict[str, str]:
    """Parse the active ``key: value`` lines of a step's ``with:`` block."""
    block = step.split("\n        with:\n", 1)[1]
    inputs = {}
    for line in block.splitlines():
        match = re.fullmatch(r"          ([\w-]+):\s*(\S.*?)\s*", line)
        if match is None:
            break
        inputs[match.group(1)] = match.group(2)
    return inputs


class DailyWorkflowTests(unittest.TestCase):
    def test_supports_daily_schedule_and_manual_dispatch_only(self) -> None:
        triggers = WORKFLOW.split("permissions:", 1)[0]
        self.assertIn('cron: "30 13 * * *"', triggers)
        self.assertIn("workflow_dispatch:", triggers)
        manual_dispatch = triggers.split("workflow_dispatch:", 1)[1]
        mode_input = manual_dispatch.split("      mode:\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("        required: true", mode_input)
        self.assertIn("        default: single-day", mode_input)
        self.assertIn("        type: choice", mode_input)
        self.assertIn("          - single-day", mode_input)
        self.assertIn("          - backfill-7-days", mode_input)
        report_date_input = manual_dispatch.split("      report_date:\n", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertIn("Optional YYYY-MM-DD", report_date_input)
        self.assertIn("        required: false", report_date_input)
        self.assertIn("        type: string", report_date_input)
        for automatic_trigger in ("push:", "pull_request:"):
            self.assertNotIn(automatic_trigger, triggers)

    def test_steps_invoke_daily_publish_subcommands_with_manual_inputs(self) -> None:
        capture_step = _step("Capture briefing window")
        restore_step = _step("Restore private corpus window")
        generation_step = _step("Generate scheduled daily or manual backfill reports")
        self.assertIn("run: python3 daily_publish.py capture-window", capture_step)
        self.assertIn("run: python3 daily_publish.py restore-corpus", restore_step)
        self.assertIn("run: python3 daily_publish.py generate-reports", generation_step)
        self.assertNotIn("run: |", capture_step + restore_step + generation_step)
        for step in (restore_step, generation_step):
            self.assertIn("MANUAL_MODE: ${{ inputs.mode }}", step)
            self.assertIn("MANUAL_REPORT_DATE: ${{ inputs.report_date }}", step)

    def test_checks_out_main_without_persisted_credentials(self) -> None:
        checkout = next(step for step in _steps() if step[0].startswith("actions/checkout@"))
        self.assertEqual(
            _with_inputs(checkout[1]),
            {"ref": "main", "persist-credentials": "false"},
        )

    def test_build_step_replaces_existing_reports_only_on_manual_dispatch(self) -> None:
        build_step = _step("Build static archive")
        self.assertRegex(
            build_step,
            r'if \[\[ "\$GITHUB_EVENT_NAME" == "workflow_dispatch" \]\]; then\s+'
            r"args\+=\(--replace-existing\)\s+fi",
        )
        self.assertEqual(WORKFLOW.count("--replace-existing"), 1)
        self.assertIn("args+=(--corpora-dir corpora)", build_step)

    def test_restored_corpora_are_encrypted_and_uploaded_as_private_archive(self) -> None:
        self.assertIn("actions: read", WORKFLOW.split("jobs:", 1)[0])
        encrypt_step = _step("Encrypt retained corpora and diagnostics")
        upload_step = _step("Upload private corpus archive")
        self.assertIn(
            "python private_archive.py create \\\n"
            "              --output private-artifacts/corpus-archive.tar.gz.enc \\\n"
            "              corpora",
            encrypt_step,
        )
        self.assertIn("name: briefing-corpus-archive", upload_step)
        self.assertIn("path: private-artifacts/corpus-archive.tar.gz.enc", upload_step)
        self.assertIn("retention-days: 14", upload_step)

    def test_secrets_are_scoped_to_the_steps_that_use_them(self) -> None:
        secret_names = (
            "GITHUB_TOKEN",
            "github.token",
            "CORPUS_ARCHIVE_PASSPHRASE",
            "OPENROUTER_API_KEY",
            "SCRAPECREATORS_API_KEY",
        )
        expected = {
            "Restore private corpus window": {
                "GITHUB_TOKEN", "github.token", "CORPUS_ARCHIVE_PASSPHRASE",
            },
            "Generate scheduled daily or manual backfill reports": {
                "OPENROUTER_API_KEY",
                "SCRAPECREATORS_API_KEY",
            },
            "Encrypt retained corpora and diagnostics": {"CORPUS_ARCHIVE_PASSPHRASE"},
        }
        steps = _steps()
        self.assertEqual(len(steps), 16)
        for label, body in steps:
            with self.subTest(step=label):
                present = {secret for secret in secret_names if secret in body}
                self.assertEqual(present, expected.get(label, set()))
        self.assertNotIn("env:", WORKFLOW.split("steps:", 1)[0])

    def test_workflow_passes_no_exclude_date_arguments(self) -> None:
        self.assertNotIn("--exclude-date", WORKFLOW)

    def test_deploy_is_gated_on_prior_history_unless_explicitly_allowed_empty(self) -> None:
        """The prior-history download step tolerates failure with
        continue-on-error so diagnostics still upload, but a missing
        prior-history.json must not silently reach the deploy steps — a
        transient download failure would otherwise look identical to "there
        is genuinely no history yet" and quietly wipe the published archive.
        build_site.py refuses to run without either --prior-history or an
        explicit --allow-empty-history, so wiring that through here is what
        actually stops the deploy."""
        # Assert the behavioral fields, not the human-readable description copy:
        # the boolean input exists, defaults off, is wired to the build env, and
        # selects exactly one of --prior-history / --allow-empty-history.
        self.assertIn("allow_empty_history:", WORKFLOW)
        self.assertIn("type: boolean", WORKFLOW)
        self.assertIn("default: false", WORKFLOW)
        self.assertIn("ALLOW_EMPTY_HISTORY: ${{ inputs.allow_empty_history }}", WORKFLOW)
        self.assertRegex(
            WORKFLOW,
            r'\[\[ -f prior-history\.json \]\]; then\s+'
            r'args\+=\(--prior-history prior-history\.json\)\s+'
            r'elif \[\[ "\$ALLOW_EMPTY_HISTORY" == "true" \]\]; then\s+'
            r'args\+=\(--allow-empty-history\)',
        )
        # No "|| true" or continue-on-error around the build step itself: a
        # missing prior history without the escape hatch must fail the job.
        build_step = WORKFLOW.split("- name: Build static archive", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertNotIn("continue-on-error", build_step)

    def test_uploads_only_encrypted_diagnostics_even_when_generation_needs_review(self) -> None:
        self.assertIn("- name: Upload encrypted briefing diagnostics\n        if: always()", WORKFLOW)
        self.assertIn("name: briefing-diagnostics-${{ github.run_id }}", WORKFLOW)
        self.assertIn("private-artifacts/briefing-diagnostics.tar.gz.enc", WORKFLOW)
        self.assertIn("python private_archive.py create", WORKFLOW)
        self.assertIn("corpora reports runs", WORKFLOW)
        self.assertNotIn("path: |\n            corpora", WORKFLOW)
        self.assertIn("retention-days: 14", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
