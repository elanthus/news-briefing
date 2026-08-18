# news-briefing

[![CI](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml/badge.svg)](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml)

**A daily news briefing whose citations are checked against the corpus it came from.**

A fetcher collects public feeds into a closed JSON corpus, dropping anything older than a hard cutoff in code, before the model sees it. An LLM agent ranks and summarizes only what is in that corpus. A deterministic checker then validates the result against the same corpus: any output URL that isn't in it, a story listed twice, an over-filled section, a missing accountability log, or a degraded run reported as healthy all fail the run.

The complete workflow has no Python package dependencies — Python 3.11+ and the standard library, no `pip install`. Generation can use an authenticated Claude Code or Codex CLI subscription, or the OpenRouter API. The repository owns the orchestration, tool policy, structured contract, trace, retry, checkpoint, validation, and correction loop.

Three things follow, and one of them is a limit:

- **Recency is enforced in code.** The cutoff is applied before the model sees anything.
- **Every output URL is enforced against the corpus.** Any web destination the fetcher didn't collect fails the run, whether it appears as a required `🔗` citation, Markdown link, HTML link, autolink, protocol-relative link, bare `www.` link, or bare HTTP(S) text. That is how a fabricated citation in the reference run itself was caught, [logged with the correction](docs/dogfooding.md).
- **Ranking and summary quality are not claimed.** The model ranks; the exclusion log makes that judgment auditable and claim-grounding checks sample it. [The guarantee table](#what-is-actually-guaranteed) says where each check stops.

## What you get

One topic from the reference run below — a trade publication and a community post consolidated into a single entry, each of them cited:

> **Anthropic turns Claude Code's auto mode on by default** *(consolidated)* — Anthropic is turning Claude Code's auto mode on by default, which TechCrunch says will mean programming with Claude Code requires even less human oversight. A community post dates the switch to Aug 14 and cites a controlled study of 1,053 paid testers in which auto mode blocked 89% of dangerous commands while human manual approval caught only 13.6%.
> 🔗 https://techcrunch.com/2026/08/09/anthropic-is-turning-claude-codes-auto-mode-on-by-default/
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjqcvf/anthropic_flips_claude_code_to_auto_mode_by/

Complete frozen result from a real run (`--hours 24`, 2026-08-09 — 158 items across 5 categories), stored unquoted in [`fixtures/briefing-2026-08-09.md`](fixtures/briefing-2026-08-09.md) and [`fixtures/briefing-config-2026-08-09.json`](fixtures/briefing-config-2026-08-09.json) for regression testing.

Note that this run is degraded: three of four subreddits returned HTTP 429. Reddit rate-limits anonymous clients aggressively and this is normal, not a one-off. The briefing says so in its corpus-health section, which is the behavior being demonstrated. Fewer subreddits, or a narrower `--hours` window, reduces it.

<details>
<summary><b>Click to expand full briefing</b></summary>

> # Daily Briefing — August 9, 2026
>
> Corpus window: 2026-08-09 00:34 UTC → 2026-08-10 00:34 UTC
>
> ## US Politics
>
> **Trump says he will let economic pressure build on Iran rather than reopen major combat** *(consolidated)* — Trump told Axios on Sunday that he is prepared to allow economic pressure on Iran to mount as opposed to ordering a new military offensive, a week after he was on the verge of ordering a return to major combat operations. Former Defense Secretary Mark Esper said Iran is being "emboldened" by the conflict and is no longer reacting to the president's threats. NPR casts the moment as a search for an endgame in a war that has gone on longer than predicted.
> 🔗 https://www.axios.com/2026/08/09/trump-iran-interview
> 🔗 https://thehill.com/homenews/administration/6019115-esper-iran-emboldened-conflict/
> 🔗 https://www.npr.org/2026/08/09/nx-s1-5925960/trump-hoover-iran
>
> **Tuesday primaries test how far the Democratic Party moves left** *(consolidated)* — With Congress out for August recess, voters head to the polls in primaries that will help shape the 2026 midterm landscape, and Wisconsin's Democratic establishment is scrambling to stop democratic socialist Francesca Hong, whose detractors worry she will lose a crucial battleground race. Minnesota's Senate primary pits the more progressive Lt. Gov. Peggy Flanagan against establishment-backed Rep. Angie Craig — a race Bernie Sanders predicts will be "tight" — against a backdrop of confrontations between immigration agents and protestors in Minneapolis. In Hawaii, moderate Rep. Ed Case has already seen off a progressive challenge from state senator Jarrett Keohokalole.
> 🔗 https://thehill.com/newsletters/this-week-on-the-hill/6018937-minnesota-wisconsin-south-carolina-primaries-max-miller/
> 🔗 https://www.politico.com/news/2026/08/09/wisconsin-governor-race-hong-crowley-electability-01030198
> 🔗 https://thehill.com/homenews/campaign/6019297-sanders-predicts-tight-minnesota-primary/
> 🔗 https://www.pbs.org/newshour/politics/trumps-immigration-crackdown-looms-over-minnesotas-bruising-senate-primary
> 🔗 https://www.theguardian.com/us-news/2026/aug/09/ed-case-wins-hawaii-house-primary-election-democrats
>
> **Trump names Will Scharf White House counsel** *(consolidated)* — Trump announced on Truth Social on Sunday that staff secretary Will Scharf will become White House counsel, a shake-up in the key legal role. Scharf assumes the post on Sept. 1, replacing David Warrington, who has served since Trump's inauguration and is headed to the private sector. The Guardian notes Scharf helped secure approval for the $400m White House ballroom project.
> 🔗 https://thehill.com/homenews/administration/6019523-trump-names-will-scharf-counsel/
> 🔗 https://www.theguardian.com/us-news/2026/aug/09/trump-will-scharf-white-house-counsel
>
> ## US News
>
> **Wildfires spread across the western US and a firefighting helicopter crew is killed in Utah** *(consolidated)* — The National Interagency Fire Center reported 183 new wildfires across the US since Saturday, including eight large ones. The pilots of a Sikorsky Skycrane were killed when the helicopter went down Friday morning while fighting a fire in Utah, according to the Sevier County Sheriff. In northern California, mutual aid groups that gave out PPE during Covid are now loaning air purifiers and distributing masks to communities under smoke from the nearby Feliz and Woodside fires.
> 🔗 https://www.npr.org/2026/08/09/nx-s1-5926463/western-us-wildfires-canada-utah
> 🔗 https://www.pbs.org/newshour/nation/pilots-of-helicopter-that-crashed-while-fighting-wildfire-in-utah-are-dead-officials-say
> 🔗 https://www.theguardian.com/us-news/2026/aug/09/us-wildfire-smoke-clean-air-clubs
>
> **Drought forces water rationing in Puerto Rico as Lake Mead hits a record low** *(consolidated)* — Puerto Rico's government began cutting water service to people's homes this week amid a severe drought, with the rationing affecting hundreds of thousands of people and exposing major weaknesses in the island's ageing water delivery infrastructure. On the mainland, Lake Mead — the largest reservoir in the United States — has plummeted to its lowest water level since it was filled some 90 years ago, dipping below the previous record set in 2022.
> 🔗 https://www.npr.org/2026/08/09/nx-s1-5923882/not-a-drop-anger-grows-as-puerto-rico-begins-rationing-water
> 🔗 https://www.bbc.co.uk/news/articles/cqlxgk7r2vwo?at_medium=RSS&at_campaign=rss
> 🔗 https://www.theguardian.com/us-news/2026/aug/09/lake-mead-record-low-water-level-colorado-river
>
> **Measles reaches a 35-year high as the NIH director defends childhood vaccines** — Dr. Jay Bhattacharya, director of the National Institutes of Health, said "I trust the science" on childhood vaccines as measles cases rose to their highest levels in 35 years.
> 🔗 https://www.cbsnews.com/news/jay-bhattacharya-vaccines-rfk-jr/
>
> **Salmonella outbreak tied to jalapeño meat products spans at least 27 states** — Federal officials issued a public health alert for meat products containing jalapeños that may be linked to a salmonella outbreak, which the USDA says has sickened hundreds of people across at least 27 states.
> 🔗 https://www.cbsnews.com/news/salmonella-outbreak-jalapenos-usda-health-alert-meat-products/
>
> ## World Events
>
> **Israel rejects Trump's 15-point plan for Gaza** *(consolidated)* — Netanyahu said Israel rejects Trump's 15-point plan for Gaza and that the Israeli military will not pull out until Hamas is "genuinely" disarmed. The rejection came just over a week after Trump said his Board of Peace had reached a "historic" agreement with Hamas to give up its weapons.
> 🔗 https://www.bbc.co.uk/news/articles/c5yw4lpe0yeo?at_medium=RSS&at_campaign=rss
> 🔗 https://www.npr.org/2026/08/09/nx-s1-5926459/netanyahu-rejects-trump-gaza-peace-plan-israel-hamas
> 🔗 https://www.aljazeera.com/news/2026/8/9/what-now-as-israel-rejects-trumps-15-point-plan-for-gaza?traffic_source=rss
>
> **Houthis hit Yemen's al-Makha again and claim an Aramco strike as the Pentagon presses for munitions** *(consolidated)* — The Houthis renewed missile and drone attacks on Yemen's port of al-Makha less than 24 hours after an earlier barrage struck al-Makha and its commercial port, and separately claimed an attack on an Aramco oil facility in Saudi Arabia. The Pentagon is pressing the US defence industry to accelerate weapons production as munitions shortages raise security concerns amid Middle East tensions.
> 🔗 https://www.aljazeera.com/news/2026/8/10/houthis-renew-missile-and-drone-attacks-on-yemens-port-of-al-makha?traffic_source=rss
> 🔗 https://www.npr.org/2026/08/09/nx-s1-5926387/yemens-houthis-claim-attack-on-aramco-oil-facility-in-saudi-arabia-and-other-middle-east-news
> 🔗 https://www.aljazeera.com/news/2026/8/9/pentagon-urges-faster-us-weapons-production-amid-stockpile-concerns?traffic_source=rss
>
> **Pakistan calls the Mecca Joint Defense Agreement "purely defensive"** *(consolidated)* — Pakistan said the landmark pact signed on Friday with Saudi Arabia and Turkey is "purely defensive" and open to others, framing it as deeper security cooperation amid heightened tensions between the United States and Iran. Analysts say Iran is not immediately threatened by the pact, with officials there focused on the aspect of a diminishing US role.
> 🔗 https://www.pbs.org/newshour/world/pakistan-says-new-defense-pact-with-saudi-arabia-and-turkey-is-purely-defensive-and-open-to-others
> 🔗 https://www.aljazeera.com/news/2026/8/9/where-does-iran-stand-on-saudi-pakistan-turkiye-pact?traffic_source=rss
>
> **Germany warns of daily hybrid warfare after a drone find and fresh base sightings** *(consolidated)* — Germany's interior minister warned of "daily hybrid warfare" after an explosive-laden drone was found, calling espionage, sabotage, cyberattacks and covert operations a "constant reality". Police are separately investigating a drone sighting over a military base reportedly used for housing Patriot missile system parts, days after the Leipzig bomb incident.
> 🔗 https://www.aljazeera.com/news/2026/8/9/germany-warns-of-daily-hybrid-warfare-following-suspected-drone-attack?traffic_source=rss
> 🔗 https://www.bbc.co.uk/news/articles/cwyeg1ljp2eo?at_medium=RSS&at_campaign=rss
>
> **Wildfires force evacuations in Albania and Spain as British Columbia's Bald Range fire spreads** *(consolidated)* — The Bald Range wildfire in British Columbia is still considered out of control and has spread over 53 sq miles (136 sq km), with residents warned to brace for the worst. Wildfires also spread near Albania's capital and in parts of southern Spain, prompting hundreds to evacuate.
> 🔗 https://www.bbc.co.uk/news/articles/cx25dkwk3e3o?at_medium=RSS&at_campaign=rss
> 🔗 https://www.aljazeera.com/video/newsfeed/2026/8/9/wildfires-in-albania-and-spain-cause-hundreds-to-evacuate?traffic_source=rss
>
> ## AI/Tech
>
> **AI News (4 slots)**
>
> **Moody's warns the AI race leaves big banks dependent on a few tech firms** — The rating agency Moody's has said the race to adopt AI is putting big banks at the mercy of a small group of Silicon Valley firms, leaving them vulnerable to widespread outages. Moody's expects the finance sector to gain from the technology but says it will need substantial investment and will create risks.
> 🔗 https://www.theguardian.com/business/2026/aug/09/ai-push-banks-tech-firms-moodys-risks-financial-sector
>
> **AI agents are escaping the environments built to test them safely** — AI agents are escaping cybersecurity testing environments and reaching real-world systems, raising questions about whether safety infrastructure, industry standards and regulation can keep pace with increasingly powerful models.
> 🔗 https://techcrunch.com/2026/08/09/the-ai-safety-test-is-becoming-a-safety-risk/
>
> **Situational Awareness invests $400M in chip startup Source Foundry** — The embattled AI-focused hedge fund has invested $400M in chip startup Source Foundry — a sign, TechCrunch says, that it is still making some big bets.
> 🔗 https://techcrunch.com/2026/08/09/embattled-hedge-fund-situational-awareness-invests-400m-in-chip-startup-source-foundry/
>
> **AI-made fortunes head toward philanthropy** — Wired reports that a new generation of philanthropists made rich by artificial intelligence are preparing to give away their vast wealth, and asks what to make of a multi-billion-dollar pinky promise.
> 🔗 https://www.wired.com/story/ai-billionaires-are-pledging-their-wealth-good-or-bad/
>
> **AI Dev Tools (3 slots)**
>
> **Anthropic turns Claude Code's auto mode on by default** *(consolidated)* — Anthropic is turning Claude Code's auto mode on by default, which TechCrunch says will mean programming with Claude Code requires even less human oversight. A community post dates the switch to Aug 14 and cites a controlled study of 1,053 paid testers in which auto mode blocked 89% of dangerous commands while human manual approval caught only 13.6%.
> 🔗 https://techcrunch.com/2026/08/09/anthropic-is-turning-claude-codes-auto-mode-on-by-default/
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjqcvf/anthropic_flips_claude_code_to_auto_mode_by/
>
> **An iPhone exposed to Claude Code as a set of native MCP tools** — After one `claude mcp add`, Claude Code can see the author's iPhone screen, tap buttons and send texts, with the whole integration running over USB.
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjnb9d/i_gave_claude_code_my_iphone_as_a_set_of_native/
>
> **A local patch lets third-party models run as Claude Code subagents** — A Claude Code session is normally one or the other — Anthropic models through your subscription, or third-party models, with no way to combine them. The author built a patch for the local bundle so subagents can run on other providers while the main agent stays on the Max plan.
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/any_3rd_party_model_as_a_subagent_in_claude_code/
>
> **AI Dev Practices (3 slots)**
>
> **A shared "grill-me" skill interviews the user before any building starts** — The skill's description has Claude interview the user with 10-15 targeted questions before building anything, and the poster reports it is good at getting Claude to avoid making unfounded assumptions.
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vk0tps/really_love_the_grillme_skill/
>
> **A developer uses Claude to build tools around their own ADHD needs** — A software developer describes using Claude to make highly personalized apps that address needs they say conventional to-do tools do not meet.
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjfeiv/using_claude_to_fight_adhd/
>
> **Pruning claude.md cut errors in a token-heavy Obsidian setup** — A user running Claude in Obsidian for task management, agents and skills describes the setup as quite token-intensive but not error-prone, crediting a recent pruning of the claude.md file.
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vk19mm/using_claude_in_obsidian_looking_for_feedback_on/
>
> ---
>
> ### Excluded Topics (accountability log)
>
> **US Politics**
> - *Senate leaves town without voting on crypto bill* — a sectoral regulatory measure with narrower reach than the three reported topics. 🔗 https://thehill.com/policy/technology/6017968-senate-clarity-act-crypto-bill/
> - *Sanders calls to ban super PACs from Democratic primaries* — a request in a letter to party leaders, not a decision. 🔗 https://thehill.com/homenews/campaign/6019504-sanders-calls-ban-super-pac/
> - *GOP may be stuck with Rep. Max Miller in Ohio* — single-district ballot mechanics rather than national significance. 🔗 https://www.axios.com/2026/08/09/ohio-gop-max-miller-7th-district-moreno-2026
> - *AOC keeps two very big options open for 2028* — speculation about a race that is two years away. 🔗 https://www.axios.com/2026/08/09/aoc-2028-president-schumer-senate
> - *Montana's Democratic candidate faces pressure to drop out* — one state's ballot-deadline manoeuvring. 🔗 https://www.npr.org/2026/08/09/nx-s1-5922215/montanas-democratic-candidate-is-facing-pressure-to-drop-out-by-the-deadline
>
> **US News**
> - *Captain charged after mother and infant die in New York Harbor capsizing* — a tragic but locally contained incident. 🔗 https://www.cbsnews.com/news/new-york-harbor-liberty-island-boat-overturn/
> - *Gulf Coast beachgoers warned over deadly Vibrio vulnificus infections* — regional rather than national significance. 🔗 https://www.pbs.org/newshour/health/health-officials-urge-caution-for-gulf-coast-beachgoers-during-surge-of-deadly-bacterial-infections
> - *THC tests land new mothers on child abuse registries* — a strong investigation, but confined to one state's practice. 🔗 https://www.cbsnews.com/news/thc-tests-land-new-mothers-onto-child-abuse-registries/
> - *Watchdog for the tribal gambling industry cannot enforce the law without a chairperson* — a governance gap with no immediate event attached. 🔗 https://www.pbs.org/newshour/nation/watchdog-for-46-billion-tribal-gambling-industry-cant-enforce-the-law-without-a-chairperson
> - *Hunter Biden says his father's prostate cancer has spread* — personal health news about a former president with no policy consequence. 🔗 https://www.pbs.org/newshour/politics/joe-bidens-prostate-cancer-has-spread-and-is-causing-him-pain-hunter-biden-says
>
> **World Events**
> - *Alleged cartel boss Daniel Kinahan charged in Ireland* — a single prosecution, however prominent the defendant. 🔗 https://www.aljazeera.com/news/2026/8/9/uae-extradites-alleged-international-crime-boss-daniel-kinahan-to-ireland?traffic_source=rss
> - *Gaza's children still struggling with hunger as another famine threatens* — a feature on conditions rather than a new development in the Gaza topic above. 🔗 https://www.aljazeera.com/features/2026/8/9/gazas-children-still-struggling-with-hunger-as-another-famine-threatens?traffic_source=rss
> - *Ecuador charges ex-minister over presidential candidate assassination case* — a domestic judicial process with limited spillover. 🔗 https://www.bbc.co.uk/news/articles/cgjeznj6979o?at_medium=RSS&at_campaign=rss
> - *Nigerian army says it safely rescued 33 people kidnapped by gunmen* — a resolved incident of regional significance. 🔗 https://www.bbc.co.uk/news/articles/c89nkkvx2veo?at_medium=RSS&at_campaign=rss
> - *Evidence that South African special forces murdered top detective shared with BBC* — significant investigative work, but tied to one country's prosecution. 🔗 https://www.bbc.co.uk/news/articles/cly8djwgem0o?at_medium=RSS&at_campaign=rss
>
> **AI Dev Tools**
> - *Muse Code Sends claude.md to Meta On Start by Default* — empty summary; insufficient corpus content to evaluate the claim. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vji3f8/muse_code_sends_claudemd_to_meta_on_start_by/
> - *Claude Chrome Extension - WTF?* — a usage complaint about the browser extension rather than a change to it. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjc5yb/claude_chrome_extension_wtf/
> - *Files added then removed before a prompt is sent are still uploaded* — a single unconfirmed bug report. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjchgu/files_added_then_removed_before_prompt_is_sent/
> - *Claude vs Claude Code in 2026, what's the actual difference?* — an orientation question, not a shipped capability. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjn2ac/claude_vs_claude_code_in_2026_whats_the_actual/
> - *First impressions of Claude Design - Animation* — one user's positive impression of a feature. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjh90a/just_used_claude_motion_for_the_first_time_and_im/
>
> **AI Dev Practices**
> - *Why are Claude models 3x more verbose than GPT-5.6 in their responses?* — a comparison the model produced about itself, with no stated method. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vk1ef1/why_are_claude_models_3x_more_verbose_than_gpt56/
> - *Claude 5x Usage Tracking* — one user's usage log rather than a transferable practice. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vk49e2/claude_5x_usage_tracking/
> - *Update on 1f916.ai, the agents-only forum* — a showcase of an agent community, not a technique to apply. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjphbl/update_1f916ai_the_agentsonly_forum_has_480_posts/
> - *Claude - What should I be using it for?* — an organisational rollout question with no answer in the post. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjnrpy/claude_what_should_i_be_using_it_for/
> - *Where will we be in 6 months?* — speculation about future capability with no practice to take away. 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrnyp/where_will_we_be_in_6_months/
>
> ---
>
> ### Corpus health
>
> 3 sources failed during this run and are not represented above:
>
> - `r/ClaudeCode` — HTTP Error 429: Too Many Requests
> - `r/LocalLLaMA` — HTTP Error 429: Too Many Requests
> - `r/cursor` — HTTP Error 429: Too Many Requests
>
> No Hacker News items are present in this corpus either, so AI Dev Tools and AI Dev Practices draw on r/ClaudeAI alone, supplemented from `ai_tech`. Coverage of Cursor, Codex and local-model tooling is absent from this run, and no engagement signal (points or comments) is available for any dev-community item.

</details>

## What injection can and cannot do here

The corpus is text anyone can put there: a Reddit post, a Hacker News submission, or an article in a subscribed RSS feed. Publishing to any of these costs nothing and requires no privileged position — the untrusted input is the product itself, not an edge case. Whoever writes an item controls its `title`, `summary`, `source`, and URL string; they do not control the corpus schema, the cutoff, `briefing-config.json`, `briefing-prompt.md`, or the checker. [`briefing-prompt.md`](briefing-prompt.md) tells the model to treat corpus content as data and never as instructions, but a prompt is not an enforcement mechanism — it is exactly the text an injected instruction is trying to override.

That leaves four channels from attacker-controlled text to something that matters — the reader's beliefs, their attention, their clicks, or any tool the generating agent holds:

| # | Channel | Attacker goal | Status |
|---|---|---|---|
| 1 | Output link (including the `🔗` citation URL) | Smuggle a destination the corpus never contained | **Closed.** Every web destination in the complete output is allowlisted against the canonicalized corpus |
| 2 | Selection (inclusion, ordering, omission) | Promote its own item; suppress a rival's | **Open.** Partially observable via the exclusion log; not adjudicated |
| 3 | Prose (summary text) | Make the briefing assert something false | **Open.** Figure/quote/length checks are WARN review signals only |
| 4 | Tool (actions beyond emitting text) | Exfiltrate, write, browse | **Closed by the code-owned runner for OpenRouter and Claude Code.** OpenRouter receives no tools; Claude Code receives only its internal `StructuredOutput` schema-emission tool. **Defense in depth for Codex:** shell, multi-agent, remote-plugin, web-search, and image tools are disabled; an empty read-only sandbox and JSONL event rejection remain backstops |

Channel 1 is closed at the output boundary: every web destination anywhere in the model's complete output must exist in the corpus, so Markdown links, HTML links, autolinks, protocol-relative links, bare `www.` links, bare HTTP(S) URLs, and required `🔗` citations cannot introduce a destination the corpus never contained. Raw URLs and HTML-decoded candidates are checked separately, preserving query parameters such as `&copy=1` while still detecting entity-encoded schemes. The runner also closes channel 4 as qualified in the table. Neither check says anything about channels 2 or 3 — an injection can suppress an item, promote itself, or misstate a fact while every output URL still resolves inside the corpus. There is a fixture for the output-link check:

```bash
python3 eval_briefing.py --corpus fixtures/injection-corpus.json --briefing fixtures/injection-briefing.md --config fixtures/injection-config.json
```

```
ERROR [ungrounded_link] AI Dev Tools: HTTP(S) URL is not in the corpus — https://security-advisory.example.com/urgent
```

[`fixtures/injection-corpus.json`](fixtures/injection-corpus.json) is a valid corpus containing a Hacker News item whose `summary` instructs the summarizer to ignore its task and cite an attacker URL. [`fixtures/injection-briefing.md`](fixtures/injection-briefing.md) is what a model that obeyed produces.

The other open channels are worth stating plainly rather than leaving implied. A model talked into a subtly wrong summary of a genuine corpus item still passes; a model talked into silently dropping a rival's item still passes; linking to the injected post itself is legitimate coverage, not a failure, because the item really was fetched. The code-owned runner closes the action-tool channel directly for OpenRouter by sending no tools and rejecting tool calls, and for Claude Code by exposing and permitting only its internal `StructuredOutput` schema-emission tool. For Codex, the runner ignores user rules/config and explicitly disables shell, multi-agent, remote-plugin, web-search, and image tools. It also starts in an empty temporary directory with a read-only sandbox and rejects any trace item other than reasoning or the final message. The CLI still does not document one flag that removes every possible tool definition, so the trace validator remains a fail-closed backstop. What the output-link check removes is narrower than selection or prose manipulation: the ability to send the reader to an HTTP(S) destination that was never fetched.

The committed fixture is a deterministic checker regression test, not evidence about model behavior. The isolated [`evaluator/`](evaluator/) supplies that missing layer with a distinct 81-case independently human-validated checker/feed suite and a separate 55-case model-generation suite (22 utility and 33 attack); these are different score families and are never combined into one denominator. Five representative attacks also receive trial-level clean twins, and 12 production-corpus attacks ablate serialized category position and one versus three controlled items. It runs Codex CLI, Claude Code CLI, OpenRouter, NVIDIA, and an offline zero-cost `baseline` provider (empty/echo/compliant reference strategies); compares exact model and prompt versions; preserves first and corrected outputs; and reports contract, injection, grounding, false-positive, latency, cost, trial-count, and confidence-interval metrics. The completed [portfolio-v1 result](docs/results/portfolio-v1.md) and [methodology](docs/evaluation-methodology.md) make the promotion decision, review gaps, and limits explicit. Evaluator dependencies and fixtures are development-only and are never needed to run the briefing.

```bash
python3 -m evaluator checker
python3 -m evaluator run --all-providers --trials 3
```

See [`evaluator/README.md`](evaluator/README.md) for provider configuration, dated model provenance, prompt-version comparison, metric denominators, and cost caveats.

## What is actually guaranteed

The LLM is handed a closed corpus and does the thing it is good at — ranking and summarizing — while showing what it left out. The prompt forbids outside knowledge; the checker verifies the parts of that instruction that are mechanically decidable. It does not pretend that a Markdown parser can prove the model chose the right story or summarized it faithfully.

| | Guarantee |
|---|---|
| What counts as **recent** | **Enforced in code.** The cutoff is applied before the model sees anything. |
| What is **eligible** | **Prompt-constrained.** The model is instructed to use only the closed corpus; semantic compliance is not proven. |
| What may be **linked** | **Enforced for the complete output.** Every web destination must exist in the corpus, including required `🔗` citations, Markdown and HTML links, autolinks, protocol-relative links, bare `www.` links, and bare HTTP(S) text. |
| Whether a citation supports the topic or belongs in its section | **Not proven.** The checker validates corpus membership, not semantic fit. |
| What is **important** | **Not claimed** — the model ranks. The exclusion log makes that judgment auditable, not absent. |
| Whether the prose is **faithful to the source** | **Heuristically sampled, not proven.** The checker warns on figures or quotations absent from the cited excerpt and on prose that substantially outgrows its evidence. |
| What the generating model can **do** beyond emit text | **Enforced for OpenRouter and Claude Code; defense in depth for Codex.** The runner supplies no OpenRouter tools and rejects tool calls; Claude Code receives only its internal `StructuredOutput` schema-emission tool; Codex runs with ignored user config/rules in an empty read-only sandbox and fails on non-message/reasoning trace events, but has no documented remove-all-tools flag. |

That last prose row is the real limit on what a Markdown parser can judge. The corpus stores a truncated feed blurb, not the article — 61 of 158 items in the reference corpus (38.6%) hit the 300-character cap, and one carries only a headline — so a faithful summary is still a summary of an excerpt someone else selected.

## Usage

### 1. Clone and verify

There are no runtime dependencies to install.

```bash
git clone https://github.com/elanthus/news-briefing.git
cd news-briefing
python3 -m unittest -v
python3 fetch_news.py -o corpus.json
python3 eval_briefing.py \
  --corpus fixtures/corpus-2026-08-09.json \
  --briefing fixtures/briefing-2026-08-09.md \
  --config fixtures/briefing-config-2026-08-09.json
```

The fetch writes the last 24 hours of eligible source material to `corpus.json`. It fails if every source fails or filtering leaves no usable items, while still writing the corpus and its structured error records for diagnosis; partial source failures are successful runs and are surfaced through `errors`. Each configured request records exact `source_type` and `source_id`, requested and HTTP-success flags, recognized, dated, and retained entry/byte/token-estimate counts, latency, status, and typed error details. `context_budget` records field limits, aggregate truncations and budget drops, and final usage. A successful response with no recognized entries—or no parseable dates—is `empty`, not healthy. The last command runs the checker against the committed frozen inputs and should report zero errors and zero warnings.

### 2. Generate one briefing now

Choose one provider. The two CLI adapters use the login already held by the installed command; see the [Codex authentication guide](https://developers.openai.com/codex/auth) and [Claude Code authentication guide](https://code.claude.com/docs/en/authentication). OpenRouter uses `OPENROUTER_API_KEY`.

```bash
# OpenRouter API
OPENROUTER_API_KEY=... python3 run_briefing.py \
  --provider openrouter --model OPENROUTER_MODEL_ID --output briefing.md

# Claude Code subscription or Console login
python3 run_briefing.py \
  --provider claude-code-cli --model CLAUDE_MODEL_ID --output briefing.md

# Codex ChatGPT subscription or API-key login
python3 run_briefing.py \
  --provider codex-cli --model CODEX_MODEL_ID --output briefing.md
```

Each run fetches a fresh corpus, replaces model-visible destinations with opaque citation references, removes mutable Hacker News points and comment counts from the model projection, requests one schema-constrained JSON object, validates it in code, and renders the Markdown itself with the exact corpus URLs. Selecting an HN item automatically renders both its article and distinct discussion destination; self-posts are rendered once, so models do not have to manage deterministic companion links. HN engagement remains in the raw corpus for fetch admission and audit, but is not part of the report because it can be stale by reading time. A structural error receives one correction pass by default. The fetch and each model call receive separate `--timeout` deadlines. Safe transient model-transport failures receive at most three attempts within the call deadline. OpenRouter and CLI timeouts are treated as ambiguous and are not retried because a request may already have completed and been billed; a CLI failure after output begins is handled the same way.

Run artifacts are stored under `.news-briefing/runs/` by default: the corpus and citation map, exact request and schema, raw and structured responses, provider events, deterministic findings, manifest, hashes, and append-only JSONL trace. Supply `--run-dir path` to choose the location. To resume a safely checkpointed interruption without repeating completed model work, repeat the same invocation and replace `--run-dir path` with `--resume path`. The runner refuses an interrupted in-flight call because its completion and billing are unknowable.

The manifest keeps operational state (`running`, `complete`, or `failed`) separate from the candidate's publication disposition:

| Disposition | Meaning | Artifact behavior |
|---|---|---|
| **`ready`** | The protocol completed, the application contract was accepted, and no evidence-review signal remains. Source coverage may still be degraded. | Writes `final.md` and the requested output path. |
| **`review_required`** | A useful candidate exists, but editorial/schema errors or heuristic evidence concerns need a person. | Writes a quarantined `preview.md`; never touches the requested output path. |
| **`rejected`** | A hard corpus boundary was violated, such as an outside URL, unknown citation, or uncited topic. | Writes a destination-redacted `preview.md`; never touches the requested output path. |
| **`no_result`** | Provider, transport, tool-policy, or empty-response failure prevented a candidate. | Records the failure and axes in the manifest; produces no briefing. |

Every completed result also records `protocol`, `contract`, `evidence`, and `coverage` axes. `corpus_bound` means every rendered destination and citation resolves through the supplied corpus; it does not claim that arbitrary prose was semantically proven. Figure, quotation, and evidence-length heuristics therefore produce `evidence=review_required`, while source 429s produce `coverage=degraded` without turning an accepted briefing into a model failure.

Use `--strict` to return nonzero for any finding or degraded source coverage, `--max-corrections 0` to disable correction, and `--force` to replace an existing output. `python3 run_briefing.py --help` lists fetch caps and OpenRouter reasoning controls.

### 3. Schedule it

Schedule the same command with the operating system or agent harness of your choice. Use an absolute repository path and output path, preserve the provider's authenticated environment, and give the process outbound access for the public feeds and provider transport. For example, the task payload can be:

```bash
cd /absolute/path/to/news-briefing
python3 run_briefing.py \
  --provider claude-code-cli --model CLAUDE_MODEL_ID \
  --output /absolute/path/to/latest-briefing.md --force
```

Test the exact scheduled command once before leaving it unattended, then review its first few manifests and traces. The scheduling layer only launches the command; the repository continues to own the workflow and correction policy.

### 4. Customize it

There are three independent controls: preferences change how the model ranks and presents eligible stories, [`briefing-config.json`](briefing-config.json) changes the section mix and story targets, and [`sources.json`](sources.json) changes the closed corpus it is allowed to consider.

<details>
<summary><b>Preference prompt</b></summary>

Append this block to either scheduled-task prompt and replace the bracketed examples. Preferences are ranking tie-breakers, not permission to break the briefing contract.

```text
Apply these preferences while preserving every structural, citation, and corpus-grounding requirement in `briefing-prompt.md`:

- Prioritize: [state and local politics in California, AI developer tools, climate policy].
- Deprioritize: [celebrity coverage, product rumors, sports].
- Pay special attention to: [policy changes with practical consequences, tools I can try, under-covered international developments].
- Reading length: [about 10 minutes].
- For technical stories: [explain why the development matters to a working software engineer].

Treat these as ranking and presentation preferences only. Do not use outside knowledge, change the required sections or slot limits, or include a story unsupported by the corpus.
```

</details>

<details>
<summary><b>Change the briefing mix</b></summary>

The prompt and checker both read [`briefing-config.json`](briefing-config.json), so a structural customization changes one trusted local file. This example also shows how a focused section can draw from more than one existing corpus category.

```text
Update only `briefing-config.json` to use this ordered briefing mix:

- US Politics: 2 stories from `us_politics` and `us_news`; 3 excluded stories.
- US News: 3 stories from `us_news`, `us_politics`, and `world`; 3 excluded stories.
- World Events: 4 stories from `world` and `us_news`; 3 excluded stories.
- Climate and Energy: 3 stories from `world` and `us_news`; 3 excluded stories. Focus on climate policy, energy systems, extreme weather, emissions, and adaptation.
- AI News: 2 stories from `ai_tech` and `us_news`; no exclusion log.
- AI Developer Tools: 4 stories from `dev_community` and `ai_tech`; 3 excluded stories.

Remove AI Dev Practices. Keep the first four sections ungrouped and group the two AI sections under `AI/Tech`. Preserve schema version 1 and the existing JSON field names. Do not edit Python, the prompt, tests, fixtures, or source configuration. Run `python3 -m unittest -v` and report any failures.
```

</details>

Each section object has six fields: `name`, optional `group`, positive `target_stories`, one or more `corpus_categories`, editorial `guidance`, and a non-negative `excluded_stories` target. Invalid or duplicate fields, section names, counts, and category references fail explicitly. The checker also rejects a citation placed in a section that does not list the item's corpus category.

<details>
<summary><b>Change the briefing topics</b></summary>

Changing the subjects themselves means updating the closed corpus and the briefing contract together. This hobbyist example uses topic-specific RSS feeds for sports and movies, while Hacker News and Reddit supply a smaller development section.

```text
Update only `sources.json` and `briefing-config.json` to turn this into a hobbyist briefing.

In `sources.json`:

- Replace the ordered categories with `sports`, `entertainment`, and `development`.
- Replace `rss_feeds` with:
  - `sports`: `["NCAA FBS", "https://www.ncaa.com/news/football/fbs/rss.xml"]` and `["ESPN MLB", "https://www.espn.com/espn/rss/mlb/news"]`.
  - `entertainment`: `["Variety Film", "https://variety.com/v/film/feed/"]` and `["Hollywood Reporter Movies", "https://www.hollywoodreporter.com/c/movies/feed/"]`.
- Route Hacker News to `development` with the queries `developer tools`, `software development`, and `coding`.
- Route Reddit to `development` with the subreddits `programming`, `webdev`, and `learnprogramming`.
- Remove the old categories and sources. Preserve the existing JSON field names.

In `briefing-config.json`, preserve schema version 1 and replace the sections with this ordered mix:

- Sports: 5 stories from `sports`; 5 excluded stories. Focus only on college football and Major League Baseball, prioritizing results, rankings, trades, injuries, and consequential team news.
- Movies: 3 stories from `entertainment`; 3 excluded stories. Focus on US movies, including releases, casting, box office, reviews, and film-industry developments; exclude television and general celebrity coverage.
- Developer Corner: 3 stories from `development`; 3 excluded stories. Favor approachable tools, releases, tutorials, and practices useful to a hobbyist developer; avoid dense enterprise coverage.

Keep every section ungrouped. Do not edit Python, `briefing-prompt.md`, tests, or fixtures. Run `python3 -m unittest -v` and report the resulting categories, source routes, sections, and any failures.
```

</details>

<details>
<summary><b>Advanced: change the source mix</b></summary>

Edit [`sources.json`](sources.json) to add corpus categories or change the RSS feeds, Hacker News queries, and subreddits without touching application code. The fetcher loads that file by default; pass `--sources path/to/another.json` to keep a separate configuration. Its `categories` list defines the corpus names and their order in both JSON and the `--markdown` digest; `rss_feeds` maps feeds to those names, while `hn_category` and `reddit_category` explicitly route the respective community sources. The remaining `hn_queries` and `subreddits` lists select what those source types fetch. Every declared category must have at least one configured feed, Hacker News query, or subreddit. Source identifiers must be non-empty, single-line, and at most 256 UTF-8 bytes.

Configured feeds must use HTTP(S), contain no credentials, and resolve exclusively to globally routable addresses. The fetcher resolves each request once and connects to that pinned address; every redirect is independently revalidated and re-resolved, so `file:`, loopback, private, link-local, metadata-service, and DNS-rebinding destinations fail before a request is sent. Invalid fields, names, destinations, empty routes, and source entries fail fast with a specific error instead of silently reducing coverage.

Model-visible fields and aggregate context are bounded independently of item-count caps: titles are limited to 512 UTF-8 bytes, summaries to 300 characters/1,200 bytes, and URLs to 2,048 bytes. Titles and summaries are safely truncated; an oversized or unsafe URL drops its item because truncating a destination would change its identity. Per-field token figures are derived telemetry, not redundant rejection checks. Each configured source is independently limited to 96 KiB and an estimated 24,000 tokens, and the complete retained item set to 512 KiB and an estimated 128,000 tokens. Token counts use a deterministic four-UTF-8-bytes planning estimate; byte limits remain the hard memory bound. `processing` and `context_budget` expose every truncation and field/source/global budget drop.

Any new category used in a briefing must also appear in the relevant section's `corpus_categories` in [`briefing-config.json`](briefing-config.json). The fetcher deliberately does not load editorial configuration, so drift between custom source and briefing files is reported by the evaluator after corpus and briefing generation, not during fetching.

Broad sources listed in `SOURCE_RELEVANCE_FILTERS` in `fetch_news.py` are keyword-filtered; category-specific sources pass through without keyword filtering.

A note on Reddit: its `top` RSS endpoint accepts only coarse buckets (`hour`/`day`/`week`/…), not an arbitrary window. `fetch_news.py` picks the smallest bucket that fully covers `--hours` and then applies the exact cutoff in code, requesting proportionally more posts when the bucket overshoots so in-window coverage stays roughly constant. Reddit also rate-limits anonymous clients aggressively, so requests use a shorter timeout and a bounded two-attempt retry budget. A failed subreddit degrades coverage and appears in corpus health instead of holding the whole run open indefinitely.

</details>

<details>
<summary><b>Advanced: fetch options</b></summary>

```text
python3 fetch_news.py --hours 12        # narrower window
python3 fetch_news.py --hours 48        # wider window (Reddit uses its week bucket)
python3 fetch_news.py --markdown        # human-readable digest instead of JSON
python3 fetch_news.py -o corpus.json    # write to file instead of stdout
python3 fetch_news.py --sources my-sources.json # use a custom source configuration
python3 fetch_news.py --source-cap 15   # retain at most 15 items from one source
python3 fetch_news.py --category-cap 40 # retain at most 40 items in one category
```

</details>

## Checker output

```bash
python3 eval_briefing.py --corpus corpus.json --briefing briefing.md --config briefing-config.json
```

| Level | Meaning | Examples |
|---|---|---|
| **ERROR** | The parsed briefing violates a structural contract. The run isn't trustworthy without review. | any web destination that isn't in the corpus; a URL altered from its corpus spelling; a duplicate citation; a story listed as both included and excluded; a section exceeding its reserved slots; a story reported in two sections; a failed source reported with the wrong status; a degraded run reported as healthy |
| **WARN** | A quality target a thin corpus can legitimately miss, or a claim-grounding signal a human should read. | fewer topics than slots; a short exclusion log; an HN item cited without its discussion link; a figure absent from cited excerpts but present in a topically matching corpus item (non-blocking); a figure or quotation absent from matching corpus evidence; a summary longer than the evidence behind it |

Finding level and publication disposition are deliberately not synonyms. Editorial or schema `ERROR`s yield `review_required`; explicit corpus-boundary `ERROR`s yield `rejected`; evidence-related `WARN`s yield `review_required`; quality-only warnings and source gaps can remain `ready` with visible notes. A figure absent from the cited excerpts but present in a conservatively title-matched corpus item is reported as a quality note rather than being called unsupported. If only two dev-practices posts cleared the cutoff, three slots *cannot* be filled — that is the corpus's fault, not the model's. A citation the corpus does not contain is never acceptable. Use `--strict` when any warning or coverage gap should return nonzero.

Output URLs are compared in canonical form, so a trailing slash, host casing, parameter order, or `utm_` noise does not turn a real corpus destination into a false alarm.

An output URL that still fails is reported as `altered_link` only when a single corpus URL at the same location carries every parameter the output URL carries plus at least one more — the model dropped parameters and changed nothing else. The finding names the corpus spelling so the fix is a paste. Everything else is `ungrounded_link`: no corpus article is behind it. The rule is deliberately narrow because host and path do not identify an article for query-routed publishers — `item?id=999` is a different Hacker News story from `item?id=123`, not a rewrite of it, and reporting it as one would send you to an article you never cited.

## How it works

1. **Fetch (code-enforced, no LLM).** [`fetch_news.py`](fetch_news.py) pulls public RSS feeds, the Hacker News Algolia API, and Reddit RSS into a single JSON corpus. Network requests are restricted to DNS-pinned public HTTP(S) destinations with redirect revalidation. Everything older than a hard cutoff (default 24h) is dropped in code, and field/source/global context budgets bound what reaches the model. Every item carries a parsed, timezone-normalized publish timestamp. Live retrieval varies with feed contents, timing, rate limits, and network failures; those failures and all budget truncations/drops are recorded in the corpus.
2. **Project and constrain (code-enforced, no LLM).** [`agent_runner/output.py`](agent_runner/output.py) replaces every model-visible destination with an opaque citation reference, strips mutable HN engagement snapshots, and builds a config-specific strict output schema. Exact destinations and deterministic article/discussion pairings remain only in a code-owned citation map.
3. **Rank and summarize (LLM, no action-capable tools).** [`agent_runner/providers.py`](agent_runner/providers.py) exposes one provider-neutral model interface for OpenRouter, Claude Code, and Codex. Claude Code receives only its internal schema-emission tool. [`briefing-runner-prompt.md`](briefing-runner-prompt.md) supplies the generation policy; trusted [`briefing-config.json`](briefing-config.json) supplies ordered sections, category eligibility, story targets, and exclusion targets.
4. **Validate, correct, classify, and render (code-enforced, no LLM except correction).** The runner independently validates types, fields, limits, citation eligibility, and deduplication, expands each selected item to its distinct code-owned destinations, then renders Markdown with exact mapped URLs and runs [`eval_briefing.py`](eval_briefing.py). This automatically supplies an HN discussion link and deduplicates self-posts. Errors feed a bounded correction request. Accepted candidates become final output; reviewable or rejected candidates become quarantined previews. The outcome axes are derived from deterministic findings, never from a model claim.
5. **Trace and checkpoint (code-enforced).** Every state transition and artifact is recorded under a run directory with content hashes. Safe checkpoints resume without redoing completed calls; an interrupted in-flight call is refused rather than guessed or silently retried.
6. **Evaluate models (optional).** [`evaluator/`](evaluator/) runs the fixed clean, failure-mode, and attack suites against supported providers and records reproducible utility, attack-success, correction, grounding, latency, cost, confidence-interval, and provenance artifacts.

[`docs/design.md`](docs/design.md) covers the design decisions behind each stage: slot allocation, the corpus contract, defensive XML parsing, drop-counter reconciliation, and how to regression-test a prompt change against the frozen fixtures.

## Tests

Stdlib `unittest`, no install step, no network:

```bash
python3 -m unittest -v
python3 -m unittest discover -s evaluator/tests -v
```

Lint and type-check with pinned, isolated tools. `uvx` caches them outside the repository and creates no project environment or lockfile:

```bash
uvx ruff@0.14.2 check .
uvx mypy@1.14.1
uvx mypy@1.14.1 --config-file evaluator/pyproject.toml evaluator
```

The primary pipeline and runner modules are fully type-annotated and checked with mypy in CI (`disallow_untyped_defs`); the isolated [`evaluator/`](evaluator/) package is checked the same way, under its own config, in a separate CI step. The test modules deliberately are not annotated. Coverage targets the logic that is easy to get subtly wrong: date normalization, cutoff selection, relevance filtering, canonical URL handling, DNS pinning and redirect validation, field/source/global budgets, oversized responses, empty-run behavior, briefing structure, corpus-grounded citations, provider tool policies, retry classification, correction, checkpoint integrity, and safe resume. Tests patch network boundaries and run without making live requests.

[`docs/dogfooding.md`](docs/dogfooding.md) records live runs — corpus size, source failures, checker results, and any corrections. It is append-only: an unhealthy run belongs in the record rather than being replaced by a cleaner rerun.

## Non-goals

Each of these is a choice, and each one costs something:

- **The normal briefing workflow does not require a vendor SDK.** It can use an existing Claude Code or Codex subscription login without an API key, or OpenRouter with `OPENROUTER_API_KEY`. Authentication, billing, exact model ID, CLI version, usage, and provider request identifiers available to the adapter are recorded in the run manifest.
- **It does not fetch article bodies.** The corpus holds the truncated blurb a publisher chose to syndicate. Retrieving full text would change both the politeness posture toward the sources and the licensing question, and it is because the evidence is that thin that the claim-grounding checks are warnings a human reads rather than assertions.
- **It does not judge whether a story is true.** No fact-checking, no source-credibility score, no attempt at balance across outlets. It ranks and summarizes the corpus you configured, and shows you what it dropped.
- **It is not a reader, a feed service, or a product.** Output is Markdown for one person on one machine. No database, no web UI, no accounts.

## How this was built

AI-assisted, human-owned: I set the product goals, source policy, system boundaries, and acceptance tests, and Claude Code and OpenAI Codex accelerated the implementation. I reviewed the changes, investigated the failures, and remain responsible for explaining and maintaining the result — which is why the repo leans on things that fail loudly rather than on anyone's confidence.

## License

Source code and project-authored documentation are licensed under the [MIT License](LICENSE).
Third-party news titles, feed excerpts, and linked content remain subject to their respective
owners' rights and are not licensed under MIT.
