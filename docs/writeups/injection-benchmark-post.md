# My news agent fabricated a citation. The checker caught it.

On an early dogfood run, my news agent produced a polished briefing with 22 topics, a full exclusion log, and a source-health report. It also cited an item called “Cowork Projects keep CLAUDE.md outside the project folder.” That item and URL were not in the closed corpus.

In ordinary language, the agent had fabricated a citation. More precisely, it had generated an ungrounded link: a destination the application had never supplied. The deterministic checker rejected the draft, and the correction loop replaced the invented item with a corpus-supported one. The final briefing had zero errors and zero warnings. The [dogfooding log records the original finding, correction, and reproducible command](../dogfooding.md#2026-08-09--the-run-behind-the-committed-reference-pair).

That small failure shaped how I built the rest of the system. I did not want “the model usually follows the prompt” to be the publication boundary. I wanted properties that code could decide—especially citation identity—to be checked by code. Then I wanted an evaluation suite that could tell me whether those checks and the model behavior held up when instructions arrived inside untrusted news content.

## Deterministic oracles before LLM judges

An LLM judge is useful when the question is semantic: Did this summary preserve the source's meaning? Is one version clearer than another? But many important questions in this application are not semantic.

- Does every linked destination occur in the frozen corpus?
- Is a citation eligible for the section where it appears?
- Did the briefing repeat a citation or omit a required section?
- Did it name the sources that failed during retrieval?
- Did a specific injection cause the attacker's URL, phrase, ordering, or routing outcome to appear?

Those questions have deterministic answers. I use parsers, canonical URL comparison, explicit section rules, and case-specific oracles before asking another model for an opinion. That gives failures stable names, makes regression tests cheap, and lets the generation model receive precise correction feedback.

“Deterministic” does not mean “infallible.” In portfolio v2, the checker measured 85.7% precision and 75.0% recall on 69 checker cases whose labels had completed blinded model review and repository-owner adjudication at run time. Two fixtures were repaired later and completed renewed exact-agreement model review on 2026-08-26, so all 81 current checker/feed cases have model review. None has completed independent human review, and full human review is recommended before production use. A deliberately difficult 12-case subset of valid claim boundaries produced a 58.3% false-positive rate for the combined claim heuristics. Those heuristics are warnings, not proof that prose is false. I publish their misses and false positives because the boundary matters: code can prove that a URL is absent from a corpus, but a short feed excerpt is rarely enough to prove that a nuanced summary is faithful.

This layering also prevents a seductive evaluation mistake. If the same kind of model both generates an answer and decides whether it is safe, the score can hide shared blind spots. A deterministic oracle is narrower, but within that narrow contract it is inspectable and reproducible without provider calls.

## A small benchmark with explicit failure modes

The generation suite has 55 authored cases: 22 utility cases and 33 indirect prompt-injection attacks. The attacker can place instructions in titles, summaries, source names, or source-failure records—the fields a news pipeline has to treat as data even when they contain imperative language.

The attacks target nine observable behaviors, including citation fabrication and alteration, duplicate citations, selection promotion and suppression, section misrouting, health-report manipulation, prose distortion, and formatting damage. Every behavior has direct and combined attack forms. Citation fabrication also gets a five-technique sweep so I can see whether escaping, context-ignoring language, or a fake-response pattern changes the outcome.

Five representative attacks have matched clean twins. For each trial, the runner executes the attacked case and a twin built from the same pristine corpus and configuration with the injected mutations removed. That matters because an agent that outputs nothing can look perfectly robust. The twin asks the complementary question: without the attack, could the system complete the underlying task? I report benign structural utility, structural utility under attack, and targeted attack success on the same complete-pair denominator.

I also included a 2 × 3 × 2 ablation: citation fabrication versus selection suppression, early/middle/late placement in a production corpus category array, and one versus three mutated items. These 12 cases are reported separately from the headline attack rate. “Position” means serialized array location, not relative prompt-token position; “three items” does not mean a measured fraction of attacker-controlled tokens.

Finally, the harness has an intentionally bad `compliant` baseline. It obeys every instruction embedded in corpus content. Its expected attack-success rate is 100%, and CI asserts that exact result across the attack matrix. This is a positive control for the oracles: if the strategy designed to comply with attacks does not score as compromised, the benchmark—not the model—has failed.

With two models, two frozen prompts, five trials, 55 authored cases, and five derived clean twins per model/prompt/trial group, the final run contains 1,200 rows. All 1,200 completed without provider errors, skips, or correction errors.

## What $3.80 bought

Here are the portfolio-v2 headline results after at most one checker-guided correction. They measure the model on the evaluator's direct-Markdown path, where it authors the whole briefing including its own links — not the two-pass production path where it never receives a URL:

| Model / prompt | Structural utility (after correction) | Targeted attack success (after correction) |
|---|---:|---:|
| DeepSeek V4 Flash / production | 90.0% (99/110) | 5.7% (6/105) |
| DeepSeek V4 Flash / reliability-v1 | 86.4% (95/110) | 2.9% (3/105) |
| Tencent HY3 / production | 81.8% (90/110) | 4.8% (5/105) |
| Tencent HY3 / reliability-v1 | 83.6% (92/110) | 3.8% (4/105) |

OpenRouter reported **$3.80048085562 across 1,676 successful calls**: 1,200 first calls and 476 correction calls. For this scale of authored regression suite, rigorous evaluation was cheaper than I expected. The expensive part was specifying the contracts, cases, denominators, and limitations—not generating the rows.

The candidate prompt still failed its preregistered promotion rules for both models. For DeepSeek, it reduced final utility by 3.6 percentage points while reducing attack success by 2.9 points, and it introduced eight contract regressions. For HY3, it improved final utility by 1.8 points and reduced attack success by 1.0 point, below the five-point practical thresholds. A lower attack rate was not allowed to erase a utility regression, and a small favorable delta was not promoted as a breakthrough.

## What I am not claiming

**These numbers do not evaluate the architecture I shipped.** Portfolio v2 ran with `"generation_path": "markdown"` — the evaluator's historical path, where the model writes the entire briefing and authors its own citations. Production does something different: two schema-constrained passes, with citation projection in between, so the model never receives a destination at all. Citation fabrication is not a low rate on that path; it has no representation in the output format. The evaluator can run it (`--generation-path production-parity`), and I have not yet paid for a 1,200-row run there. So read the table as a floor on model behavior under a weaker contract — useful precisely because it's the harder case, and not evidence that the two-pass runner works.

The rest of what I am not claiming: this is a fixed, authored suite enriched for known boundaries. Its Wilson intervals describe outcomes on these cases; they do not establish performance on deployment traffic. Repeating a case five times does not turn it into five independent samples of the world.

The benchmark does not prove ranking quality. Its utility measures are mostly structural: valid output, usable non-empty sections, correct routing, and declared case floors. The deterministic checker verifies corpus membership and application contracts, not whether the model chose the most important story or wrote the most faithful summary.

It is also not an AgentDojo reproduction. The matched-twin idea follows the useful principle of measuring utility alongside security, but my “benign structural utility” is not AgentDojo's deterministic user-task utility. Likewise, my ablation's category-array position is not relative injection-token position, and its item count is not controlled-token fraction. The [evaluation methodology](../evaluation-methodology.md) spells out those distinctions and links the peer-reviewed [AgentDojo](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) and [MELON](https://proceedings.mlr.press/v267/zhu25z.html) work that informed the threat model.

Portfolio v2 also makes no semantic meaning-preservation or human-grounding claim. Its 180 semantic-review forms and topic-level grounding forms remain unjudged. I would rather leave a cell blank than quietly replace human review with another model's confidence.

The complete [model card](../results/portfolio-v2.md) includes Wilson intervals, paired prompt deltas, operational controls, limitations, and the non-promotion decision. The [public evidence bundle](../results/portfolio-v2-evidence/) contains every generated output and score primitive needed to regenerate the report offline. The implementation, fixtures, and verification commands are in the [news-briefing repository](https://github.com/elanthus/news-briefing); the [benchmark usage guide](../../evaluator/README.md) walks through running the same suite against your own model or prompt, starting with a credential-free offline smoke test.

The original fabricated citation was useful because it failed loudly. The benchmark is an attempt to make more failures do the same—and to be precise about the failures it still cannot see.
