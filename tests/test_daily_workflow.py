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
        for automatic_trigger in ("push:", "pull_request:"):
            self.assertNotIn(automatic_trigger, triggers)

    def test_scheduled_run_handles_today_from_one_fixed_snapshot(self) -> None:
        self.assertIn("snapshot_end_epoch=$(date --utc +%s)", WORKFLOW)
        self.assertIn(
            'today=$(TZ=America/New_York date --date="@$snapshot_end_epoch" +%F)',
            WORKFLOW,
        )
        self.assertIn("MANUAL_MODE: ${{ inputs.mode }}", WORKFLOW)
        self.assertIn(
            '          if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" && '
            '"$MANUAL_MODE" == "backfill-7-days" ]]; then\n'
            "            day_offsets=(6 5 4 3 2 1 0)\n"
            "          else\n"
            "            day_offsets=(0)\n"
            "          fi",
            WORKFLOW,
        )

    def test_manual_run_can_backfill_today_and_six_prior_dates(self) -> None:
        self.assertIn("day_offsets=(6 5 4 3 2 1 0)", WORKFLOW)
        self.assertIn('for days_ago in "${day_offsets[@]}"; do', WORKFLOW)
        self.assertIn('--date="$today $days_ago days ago" +%F', WORKFLOW)
        self.assertIn("TZ=America/New_York", WORKFLOW)

    def test_today_uses_fresh_exact_24_hour_corpus(self) -> None:
        self.assertIn('if (( days_ago == 0 )); then', WORKFLOW)
        self.assertIn(
            'window_start_epoch=$((snapshot_end_epoch - 86400))', WORKFLOW
        )
        self.assertIn(
            'window_start=$(date --utc --date="@$window_start_epoch" --iso-8601=seconds)',
            WORKFLOW,
        )
        self.assertIn(
            'window_end=$(date --utc --date="@$snapshot_end_epoch" --iso-8601=seconds)',
            WORKFLOW,
        )
        self.assertIn('--window-start "$window_start"', WORKFLOW)
        self.assertIn('--window-end "$window_end"', WORKFLOW)
        self.assertIn('--report-date "$report_date"', WORKFLOW)

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
        self.assertIn("corpus_ready=false", WORKFLOW)
        self.assertRegex(WORKFLOW, r'--output "\$corpus"; then\s+corpus_ready=true')
        self.assertIn("::warning::Corpus fetch failed for $report_date", WORKFLOW)
        self.assertRegex(
            WORKFLOW, r'Reusing stored corpus for \$report_date"\s+corpus_ready=true')
        self.assertIn('if [[ "$corpus_ready" == true ]]; then', WORKFLOW)
        self.assertNotIn('if [[ -f "$corpus" ]]; then', WORKFLOW)
        self.assertIn("Skipping briefing generation for $report_date", WORKFLOW)

    def test_preserves_dated_corpora_and_replaces_reports(self) -> None:
        self.assertIn("ref: main", WORKFLOW)
        self.assertIn('corpus="corpora/$report_date.json"', WORKFLOW)
        self.assertIn('run_dir="runs/$report_date"', WORKFLOW)
        self.assertIn('report="reports/$report_date.md"', WORKFLOW)
        self.assertIn("--force", WORKFLOW)
        self.assertIn(
            '          if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]; then\n'
            "            args+=(--replace-existing)\n"
            "          fi",
            WORKFLOW,
        )
        self.assertEqual(WORKFLOW.count("--replace-existing"), 1)

    def test_backfill_reuses_only_prior_stored_corpora(self) -> None:
        self.assertIn(
            "https://elanthus.github.io/news-briefing/corpora/$report_date.json",
            WORKFLOW,
        )
        self.assertIn('elif curl \\', WORKFLOW)
        self.assertIn("no stored corpus is available", WORKFLOW)
        self.assertNotIn("days_ago <= 2", WORKFLOW)

    def test_generation_uses_ordered_production_fallback_chain(self) -> None:
        self.assertIn("python run_daily_briefing.py", WORKFLOW)
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
        self.assertIn("--max-tokens 100000", WORKFLOW)
        self.assertIn("default=100_000", DAILY_RUNNER)
        self.assertIn("min(max_tokens, candidate.max_tokens_cap)", DAILY_RUNNER)

    def test_generation_logs_failures_quarantines_and_removed_models(self) -> None:
        self.assertIn('LOG_NAME = "fallback-log.json"', DAILY_RUNNER)
        self.assertIn('TEXT_LOG_NAME = "fallback.log"', DAILY_RUNNER)
        self.assertIn('"failure_reason": reason', DAILY_RUNNER)
        self.assertIn('"quarantined_report": quarantined_report', DAILY_RUNNER)
        self.assertIn('"model_removed_from_openrouter": removed', DAILY_RUNNER)
        self.assertIn("see $run_dir/fallback.log", WORKFLOW)

    def test_every_run_carries_forward_stored_corpora(self) -> None:
        self.assertIn("for days_ago in $(seq 1 13); do", WORKFLOW)
        self.assertIn("--corpora-dir corpora", WORKFLOW)

    def test_removes_known_bad_historical_pages(self) -> None:
        self.assertIn("--exclude-date 2026-08-15", WORKFLOW)
        self.assertIn("--exclude-date 2026-08-16", WORKFLOW)

    def test_publication_preparation_failure_does_not_abort_remaining_dates(self) -> None:
        self.assertIn("if ! python prepare_publication.py", WORKFLOW)
        self.assertIn("Publication preparation failed for $report_date", WORKFLOW)

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

    def test_uploads_diagnostics_even_when_generation_needs_review(self) -> None:
        self.assertIn("- name: Upload briefing diagnostics\n        if: always()", WORKFLOW)
        self.assertIn("name: briefing-diagnostics-${{ github.run_id }}", WORKFLOW)
        for path in ("corpora", "reports", "runs"):
            self.assertIn(f"            {path}\n", WORKFLOW)
        self.assertIn("retention-days: 7", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
