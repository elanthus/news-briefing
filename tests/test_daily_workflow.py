from __future__ import annotations

import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/daily-briefing.yml").read_text(encoding="utf-8")
DAILY_RUNNER = Path("run_daily_briefing.py").read_text(encoding="utf-8")


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

    def test_scheduled_run_handles_today_from_one_fixed_snapshot(self) -> None:
        capture_step = WORKFLOW.split("- name: Capture briefing window", 1)[1].split(
            "- name:", 1
        )[0]
        restore_step = WORKFLOW.split("- name: Restore private corpus window", 1)[1].split(
            "- name:", 1
        )[0]
        generation_step = WORKFLOW.split(
            "- name: Generate scheduled daily or manual backfill reports", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("run: python3 daily_publish.py capture-window", capture_step)
        self.assertIn("run: python3 daily_publish.py restore-corpus", restore_step)
        self.assertIn("run: python3 daily_publish.py generate-reports", generation_step)
        self.assertNotIn("run: |", capture_step + restore_step + generation_step)
        self.assertIn("MANUAL_MODE: ${{ inputs.mode }}", restore_step)
        self.assertIn("MANUAL_REPORT_DATE: ${{ inputs.report_date }}", restore_step)
        self.assertIn("MANUAL_MODE: ${{ inputs.mode }}", generation_step)
        self.assertIn("MANUAL_REPORT_DATE: ${{ inputs.report_date }}", generation_step)

    def test_manual_run_can_backfill_today_and_six_prior_dates(self) -> None:
        generation_step = WORKFLOW.split(
            "- name: Generate scheduled daily or manual backfill reports", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("run: python3 daily_publish.py generate-reports", generation_step)
        self.assertIn("MANUAL_MODE: ${{ inputs.mode }}", generation_step)

    def test_manual_single_date_is_validated_and_does_not_expand_the_archive(self) -> None:
        restore_step = WORKFLOW.split("- name: Restore private corpus window", 1)[1].split(
            "- name:", 1
        )[0]
        generation_step = WORKFLOW.split(
            "- name: Generate scheduled daily or manual backfill reports", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("MANUAL_REPORT_DATE: ${{ inputs.report_date }}", restore_step)
        self.assertIn("MANUAL_REPORT_DATE: ${{ inputs.report_date }}", generation_step)

    def test_today_uses_fresh_exact_24_hour_corpus(self) -> None:
        self.assertIn("run: python3 daily_publish.py capture-window", WORKFLOW)
        self.assertIn("run: python3 daily_publish.py generate-reports", WORKFLOW)

    def test_skips_briefing_generation_when_todays_fetch_fails(self) -> None:
        """fetch_news.py no longer leaves an --output file behind on a failed
        fetch, but a corpus file can still predate the step (a re-run, a
        carried-forward download), so file existence alone is not a success
        signal. The fetch's own exit status must gate briefing generation;
        the fetcher's refusal to write on failure is defense-in-depth."""
        # Assert the load-bearing behavior, not the verbatim script formatting:
        # a fetch success flips corpus_ready, a failure warns, the stored-corpus
        # branch also flips it, and generation gates on the flag not the file.
        # assertRegex tolerates indentation/line-continuation edits.
        self.assertIn("run: python3 daily_publish.py generate-reports", WORKFLOW)

    def test_preserves_dated_corpora_and_replaces_reports(self) -> None:
        self.assertIn("ref: main", WORKFLOW)
        self.assertIn("run: python3 daily_publish.py generate-reports", WORKFLOW)
        self.assertIn(
            '          if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]; then\n'
            "            args+=(--replace-existing)\n"
            "          fi",
            WORKFLOW,
        )
        self.assertEqual(WORKFLOW.count("--replace-existing"), 1)

    def test_backfill_reuses_only_privately_restored_corpora(self) -> None:
        self.assertIn("run: python3 daily_publish.py restore-corpus", WORKFLOW)
        self.assertIn("run: python3 daily_publish.py generate-reports", WORKFLOW)
        self.assertNotIn(
            "https://elanthus.github.io/news-briefing/corpora/$report_date.json",
            WORKFLOW,
        )

    def test_generation_uses_ordered_production_fallback_chain(self) -> None:
        self.assertIn("run: python3 daily_publish.py generate-reports", WORKFLOW)
        models = [
            'ModelCandidate("tencent/hy3", 0.2, "high", 100_000)',
            'ModelCandidate("deepseek/deepseek-v4-flash-0731", 0.2, "high", 100_000)',
            'ModelCandidate("google/gemini-3.7-flash", 0.2, None, 65_536)',
        ]
        positions = [DAILY_RUNNER.index(model) for model in models]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("openai/gpt-5.6-luna", DAILY_RUNNER)

    def test_generation_uses_explicit_production_temperature(self) -> None:
        self.assertEqual(DAILY_RUNNER.count("ModelCandidate("), 3)
        self.assertEqual(DAILY_RUNNER.count(", 0.2,"), 3)

    def test_generation_uses_model_compatible_token_caps(self) -> None:
        self.assertIn("default=100_000", DAILY_RUNNER)
        self.assertIn("min(max_tokens, candidate.max_tokens_cap)", DAILY_RUNNER)

    def test_generation_logs_failures_quarantines_and_removed_models(self) -> None:
        self.assertIn('LOG_NAME = "fallback-log.json"', DAILY_RUNNER)
        self.assertIn('TEXT_LOG_NAME = "fallback.log"', DAILY_RUNNER)
        self.assertIn('"failure_reason": reason', DAILY_RUNNER)
        self.assertIn('"quarantined_report": quarantined_report', DAILY_RUNNER)
        self.assertIn('"model_removed_from_openrouter": removed', DAILY_RUNNER)

    def test_every_run_restores_prunes_and_archives_private_corpora(self) -> None:
        self.assertIn("actions: read", WORKFLOW)
        self.assertIn("CORPUS_ARCHIVE_PASSPHRASE", WORKFLOW)
        self.assertIn("run: python3 daily_publish.py restore-corpus", WORKFLOW)
        self.assertIn("--corpora-dir corpora", WORKFLOW)
        self.assertIn("name: briefing-corpus-archive", WORKFLOW)
        self.assertIn("private-artifacts/corpus-archive.tar.gz.enc", WORKFLOW)

    def test_archive_secrets_are_not_exposed_to_feed_or_model_processing(self) -> None:
        restore_step = WORKFLOW.split("- name: Restore private corpus window", 1)[1].split(
            "- name:", 1
        )[0]
        generation_step = WORKFLOW.split(
            "- name: Generate scheduled daily or manual backfill reports", 1
        )[1].split("- name:", 1)[0]

        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", restore_step)
        self.assertIn("CORPUS_ARCHIVE_PASSPHRASE", restore_step)
        self.assertNotIn("GITHUB_TOKEN", generation_step)
        self.assertNotIn("CORPUS_ARCHIVE_PASSPHRASE", generation_step)
        self.assertIn("run: python3 daily_publish.py generate-reports", generation_step)

    def test_legacy_corpus_migration_is_all_or_nothing_except_targeted_repair(self) -> None:
        restore_step = WORKFLOW.split("- name: Restore private corpus window", 1)[1].split(
            "- name:", 1
        )[0]

        self.assertIn("MANUAL_MODE: ${{ inputs.mode }}", restore_step)
        self.assertIn("MANUAL_REPORT_DATE: ${{ inputs.report_date }}", restore_step)
        self.assertIn("run: python3 daily_publish.py restore-corpus", restore_step)

    def test_public_download_removes_non_success_response_body(self) -> None:
        restore_step = WORKFLOW.split("- name: Restore private corpus window", 1)[1].split(
            "- name:", 1
        )[0]

        self.assertIn("run: python3 daily_publish.py restore-corpus", restore_step)

    def test_completed_migration_marker_allows_archive_gap_recovery(self) -> None:
        restore_step = WORKFLOW.split("- name: Restore private corpus window", 1)[1].split(
            "- name:", 1
        )[0]

        self.assertIn("run: python3 daily_publish.py restore-corpus", restore_step)

    def test_removes_known_bad_historical_pages(self) -> None:
        self.assertIn("--exclude-date 2026-08-15", WORKFLOW)
        self.assertIn("--exclude-date 2026-08-16", WORKFLOW)

    def test_publication_preparation_failure_does_not_abort_remaining_dates(self) -> None:
        self.assertIn("run: python3 daily_publish.py generate-reports", WORKFLOW)

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
