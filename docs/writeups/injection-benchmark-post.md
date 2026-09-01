# My news agent cited an article it had never been given

On an early dogfood run, 2026-08-09, a 158-item corpus, the briefing came back looking finished. Twenty-two topics filling all six sections, a 25-row log of what it had left out, and a corpus-health section correctly naming the three subreddits that had returned HTTP 429.

One of its links pointed at a story called "Cowork Projects keep CLAUDE.md outside the project folder." Nothing by that name had been fetched. The string `Cowork` does not appear anywhere in the corpus file, which is still committed to the repository.

That is what this codebase calls an **ungrounded link**: a destination that shows up in the output without ever having shown up in the input. In looser language, the model made one up. The checker caught it before anything published, the item was swapped for one the corpus actually contained, and the corrected draft came back clean — 0 errors, 0 warnings, still reproducible today:

```bash
python3 eval_briefing.py --corpus fixtures/corpus-2026-08-09.json --briefing fixtures/briefing-2026-08-09.md --config fixtures/briefing-config-2026-08-09.json
```

The [dogfooding log](../dogfooding.md#2026-08-09--the-run-behind-the-committed-reference-pair) records the original finding, the correction, and every count above.

One detail about that run governs everything below: **The model wrote the entire briefing as Markdown, links included.** That is the evaluator's historical path. It is not the pipeline I ship: production runs two schema-constrained model calls, and the model never receives a URL in either of them, so it has nothing to invent from. The benchmark in this post still runs on the old path, which makes "the model can author its own links" the single biggest limitation of every number here.

I didn't want "the model usually follows the prompt" to be the line between a draft and a published page. Anything code could decide, I wanted code to decide: citation identity above all. Then I wanted a way to find out whether those checks, and the model's own behavior, held up when instructions arrived hidden inside the news content itself.

## Let code decide what code can decide

Asking another model to grade an output makes sense when the question is about meaning. Did this summary preserve what the source said? Is this version clearer than that one? A model is the only tool that even attempts those.

Most of the questions that matter here aren't about meaning at all:

- Does every link in the output appear in the frozen corpus?
- Is this item allowed in the section it was placed in?
- Was the same item cited twice, or a required section left out?
- Did the briefing name the sources that failed during retrieval?
- Did a specific injected instruction produce the attacker's URL, phrase, ordering, or routing?

Each of those has a right answer that a parser can compute.

**"Deterministic" is not "infallible."** The checker ships with an offline fixture suite — 69 checker cases plus 12 feed-parser cases — that scores the checker against hand-labeled expectations. Precision is the share of findings it raises that are real; recall is the share of real problems it raises at all. The third row covers a separate group of heuristics that guess whether a summary claims more than its evidence supports, measured against twelve valid claims written to sit right on the boundary.

| | Current suite | Frozen 2026-08-19 bundle |
|---|---:|---:|
| Precision | 87.5% (42/48) | 85.7% (42/49) |
| Recall | 77.8% (42/54) | 75.0% (42/56) |
| Claim-heuristic false positives | 50.0% (6/12) | 58.3% (7/12) |

Half the claim heuristics' hits on hard boundaries are wrong. Code can prove a URL isn't in the corpus; it can't prove a two-sentence feed excerpt backs up a careful summary. That's why a bad link blocks the run and a suspect claim only gets flagged.

There's also a trap this layering avoids. If the same kind of model both writes the answer and decides whether the answer is safe, the score can quietly inherit whatever blind spot they share. A deterministic check is much narrower, but it is inspectable, and it reproduces without a provider call.

## A small benchmark with named failure modes

If someone hides an instruction inside a news item, does the briefing obey it? And does the system still work when nobody is attacking it? 

The attacker never talks to the model directly. They write into a feed item's title or summary, a source name, or a source-failure record, fields a news pipeline has to treat as data even when they read like commands. They attempt: an invented link, an altered real one, the same item cited twice, a story forced in, a story kept out, a story filed under the wrong section, a distorted summary, a broken output. Nine behaviors, each with a direct and a combined form. Link invention also gets a five-technique sweep, adding escaped characters, context-ignoring language, and a fake-response pattern to see whether phrasing changes the outcome or only its odds.

Scoring the utility half needs a clean comparison. Five attacks carry a matched twin: the same case re-run from a pristine corpus with no injections. Base utility, utility under attack, and attack success are then reported separately, over the same complete-pair denominator.

Twelve more cases vary three things: which attack (invent a link, or keep a story out), whether one item is poisoned or three, and whether the poisoned items sit near the start, middle, or end of a category's item list. They stay out of the headline attack rate, and they should be read narrowly. Near the end of a JSON list is not the same as near the end of what the model actually reads.

The suite is 55 hand-authored cases, 22 utility and 33 attacks. Multiplied across two models, two frozen prompts, and five trials, those plus five derived twins per group came to 1,200 rows. All completed. No provider errors, no skips, no correction errors.

## What $3.80 bought

Read the table with the constraint from the top of this post attached. This run used `"generation_path": "markdown"`, the path where the model writes everything and authors its own links. It is not the two-pass production runner, where the prose schema has no citation field at all and runtime validation rejects HTTP(S) URLs before rendering. So this measures model behavior under the weaker contract. I have since put 1,200 rows through the production path (`--generation-path production-parity`) as well, and the interesting part is not that the scores went up: it is that `missing_section`, `category_ineligible`, and `ungrounded_link` all went to zero, because the schema enumerates each section's eligible identifiers and requires the sections. What is left is the model selecting the same item into two topics, which the schema does not forbid. Those numbers and their caveats are in the [repository README](https://github.com/elanthus/news-briefing#production-parity-1200-rows-180).

Results are after at most one checker-guided correction:

| Model / prompt | Structural utility | Targeted attack success |
|---|---:|---:|
| DeepSeek V4 Flash / production | 90.0% (99/110) | 5.7% (6/105) |
| DeepSeek V4 Flash / reliability-v1 | 86.4% (95/110) | 2.9% (3/105) |
| Tencent HY3 / production | 81.8% (90/110) | 4.8% (5/105) |
| Tencent HY3 / reliability-v1 | 83.6% (92/110) | 3.8% (4/105) |

OpenRouter billed **$3.80048085562 across 1,676 successful calls** 1,200 first attempts and 476 corrections. For an authored regression suite at this scale, careful evaluation turned out to be pretty cheap on these model. The expensive part was deciding what the contracts, cases, denominators, and limitations should be. Generating the rows was rounding error.

## What I am not claiming

The generation-path limitation above is the first and largest. The rest:

This is a fixed, hand-authored suite, deliberately enriched with known-hard boundaries. Its confidence intervals describe outcomes on these cases. They say nothing about performance on real traffic. Running one case five times gives you five samples of one case, not five samples of the world.

The benchmark doesn't measure ranking quality. Its utility measures are structural: valid output, usable non-empty sections, correct routing, declared case floors. The checker verifies corpus membership and application contracts. Whether the model picked the most important story, or wrote the most faithful summary of it, is outside everything here.

This isn't an AgentDojo reproduction. The matched-twin idea follows AgentDojo's principle of measuring utility alongside security, but my benign structural utility is not their deterministic user-task utility, my ablation's array position is not their relative injection-token position, and my item count is not their controlled token fraction. The [evaluation methodology](../evaluation-methodology.md) spells out each distinction and links the [AgentDojo](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) and [MELON](https://proceedings.mlr.press/v267/zhu25z.html) work the threat model came from.

The full [model card](../results/portfolio-v2.md) has the Wilson intervals, paired prompt deltas, operational controls, and the non-promotion decision. The [public evidence bundle](../results/portfolio-v2-evidence/) has every generated output and score primitive needed to regenerate the report offline. Implementation, fixtures, and verification commands live in the [news-briefing repository](https://github.com/elanthus/news-briefing); the [benchmark usage guide](../../evaluator/README.md) walks through pointing the same suite at your own model or prompt, starting with an offline smoke test that needs no credentials.

The invented citation was useful because it failed loudly. The benchmark is an attempt to make more failures behave that way.
