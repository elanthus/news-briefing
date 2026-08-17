# Portfolio v1 model card

## Evaluation identity

- Run: `portfolio-v1-final-20260815`
- Started: 2026-08-15 17:47 UTC
- Completed: 2026-08-16 01:58 UTC
- Suite: 55 authored cases plus five derived clean twins; SHA-256
  `aa341680517d5f44b3b4dcb9fe4189a4102bcde75843eee8ffceb46f6dc14b5f`
- Trials: five per model, prompt, and case row
- Planned/completed: 1,200/1,200; zero failed or skipped
- Generation controls: temperature 0, no seed, reasoning explicitly disabled, 300-second request timeout,
  at most one checker-guided correction

## Models and prompts

| Role | Provider / exact model | Prompt | Prompt SHA-256 |
|---|---|---|---|
| Original cross-provider design | Claude Code / `claude-sonnet-5` | both | see protocol |
| Final group | OpenRouter / `deepseek/deepseek-v4-flash` | production + candidate | frozen below |
| Authorized Sonnet replacement | OpenRouter / `tencent/hy3` | production + candidate | frozen below |

- Production prompt SHA-256: `41b038151c36031df3d3ae35578b5d959168251a22fb04e6baa8273dd6b9d86c`
- Candidate prompt SHA-256: `f1d0eaf852a955559faa3dbdf850c606d1dedf079c1dc590ea62958dd3f2317e`

Claude Sonnet 5 completed nine small pilot rows, then three consecutive production-corpus calls exceeded the
frozen timeout. Per the owner's prior instruction, HY3 replaced Sonnet for the usable pilot and final matrix.
DeepSeek high and low reasoning exhausted the 8,192-token completion budget without usable production text;
the owner-authorized reasoning-disabled condition completed the pilot and final run.

## Review and adjudication

The 81 offline checker/feed cases completed independent human review. All 180 final `must_convey`
propositions received blinded machine semantic judgments from OpenRouter's
`nvidia/nemotron-3-ultra-550b-a55b:free`: 159 conveyed, 21 not conveyed, and zero unclear. Three transient
unexpected responses were resumed from durable checkpoints. Nemotron never returned a rate-limit response,
so the predeclared `z-ai/glm-5.2` fallback was not used.

Human grounding is incomplete: 0/2,170 final utility topics are labeled. The local review export contains a
fully blinded primary packet and a stratified 434-topic independent double-review packet. Machine semantic
judgments and deterministic grounding proxies do not count as human approval.

## Cost coverage

OpenRouter reported a $3.033816 final generation cost: $0.334516 and $0.325910 for DeepSeek production and
candidate, and $1.186898 and $1.186493 for HY3 production and candidate. All 1,717 generation calls (1,200
first calls plus 517 corrections) reported cost. The final-run ceiling was $4.00. Known pilots, label review,
and final work remained below the user's $5 authorization. Nemotron's selected model was free.

## Intended use and interpretation

This evaluation compares two frozen briefing prompts on an authored utility and prompt-injection suite. It
supports regression review and prompt-change decisions for this repository. It does not estimate arbitrary
model safety, general news accuracy, or all future attacks. Wilson intervals describe rates inside the suite;
paired case-cluster bootstrap intervals compare prompts without treating five repetitions as five independent
deployment cases.

The candidate is not approved for promotion. DeepSeek meets the five-point utility rule and has no final
contract regressions, but misses the five-point attack-resistance rule. HY3 misses both improvement rules and
has five contract regressions. The human-grounding rule remains undetermined for both.

