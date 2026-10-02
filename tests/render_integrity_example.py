"""Render the README auditor example from committed fixtures and fake providers.

Run with the isolated site dependencies: python -m tests.render_integrity_example OUTPUT.
No network or paid model calls are made. Scores illustrate the UI, not model quality.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from agent_runner.semantic_repairs import daily_semantic_review
from build_site import build_site
from prepare_publication import prepare_publication
from tests.test_semantic_repairs import Judge, PatchProvider, make_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    root = parser.parse_args().output
    root.mkdir(parents=True, exist_ok=False)
    run, headline = make_run(root)
    daily_semantic_review(run, root / 'jev-review', apply_repairs=True,
                          judge=Judge(headline), repair_provider=PatchProvider())
    (root / 'fallback-log.json').write_text(json.dumps({
        'status': 'ready', 'selected_run_dir': 'run',
    }), encoding='utf-8')
    prepare_publication(root, root / 'input.json', root / 'history', date(2026, 9, 30))
    build_site(root / 'history', root / 'site', allow_empty_history=True)
    print(root / 'site/reports/2026-09-30.html')


if __name__ == '__main__':
    main()
