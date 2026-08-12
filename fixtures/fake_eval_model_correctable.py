#!/usr/bin/env python3
"""Deterministic stdin/stdout model stand-in that restores a required marker
only when the correction pass explicitly tells it what is missing. Used to
prove run_ai_eval.py threads missing required content into the correction
request rather than silently dropping it."""

import sys

MARKER = "RESTORE_ME_ONLY_WHEN_TOLD"

BASE = """# Daily Briefing — August 9, 2026

## AI Dev Tools

**Third-party models as subagents** — A patch lets subagents use other model providers.
🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/
"""

data = sys.stdin.read()
if "Required content missing" in data and MARKER in data:
    print(BASE + f"\n{MARKER}\n")
else:
    print(BASE)
