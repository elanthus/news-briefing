# Jev-stage review: instructions for reviewers

You are one of two independent reviewers for the Jev-stage evaluation
([plan](jev-stage-eval-plan.md), protocol
[`jev-stage-v1.json`](../../evaluator/protocols/jev-stage-v1.json)). You will
receive one JSON file, `reviewer-primary.json` (65 items) or
`reviewer-double.json` (31 items), and return one JSON file of labels. Nothing in
the packet tells you which model wrote a text, what an automated reviewer scored,
whether a repair was accepted, or which of two versions came first. Please do not
try to find out; the study measures your judgment from the supplied evidence
alone.

## What is in the packet

The file has three top-level fields: `schema_version`, `rubric`, and `items`.
Each item has a `packet_id` (for example `r1-00017`) and a `versions` list.

- **One version** (a control item): one generated headline and summary with the
  frozen source titles and excerpts it cited.
- **Two versions** (a pair): two generated texts for the same story slot. Each
  version carries its own source list. The two lists may differ, because one
  version may have dropped a source; the order of the two versions is random.

Each version has `headline`, `prose`, and `evidence`, where `evidence` is a list
of `{title, excerpt}` objects. An excerpt can be null when the feed supplied only
a title.

## How to judge

Judge only from the `evidence` shown for that version. Do not open the articles,
search the web, or use what you know about the story. A claim that is true in the
world but absent from the supplied titles and excerpts is unsupported here. Every
item must get labels; leave none blank.

Four questions, from the packet's `rubric`:

1. **Support** (every version). Is every material factual claim in the headline
   and prose supported by that version's own evidence? Label `supported`,
   `unsupported` (a material claim with no basis in the evidence), `strengthened`
   (the text raises certainty, scope, magnitude, or causality beyond the
   evidence), or `reversed` (the text contradicts the evidence). If more than one
   applies, choose the most serious: `reversed`, then `unsupported`, then
   `strengthened`.
2. **Meaning preservation** (pairs only). Do both versions carry the same
   supported material claims? Label `preserved`, `lost` (a supported claim present
   in one version is missing from the other), `added` (one version asserts a claim
   the other does not), or `changed` (the same claim says something different). For
   a one-version item use `not_applicable`.
3. **Citations** (every version). Is each listed source relevant to that
   version's prose? Label `all_relevant`, `irrelevant_listed` (a listed source has
   nothing to do with the prose), or `needed_source_absent` (in a pair, the prose
   of this version still relies on a source that appears only in the other
   version's list). A material claim is one a reader would take as a fact about the
   story: who, what, how many, when, what caused what. Style and emphasis are not
   material.
4. **Readability** (every version). Complete sentences, no copied truncated
   fragments, no dangling ellipsis. Label `readable` or `fragment_or_incomplete`.

Write a one- or two-sentence `rationale` for every item naming the claim or
source that decided your label, even when everything is `supported` and
`all_relevant`. The adjudicator reads rationales, not your name.

## The easiest way to work

Open [`jev-stage-review.html`](jev-stage-review.html) in any current browser
(double-click the file; no server or installation is needed). Use **Load packet**
to pick your packet file. The page shows one item at a time with its versions,
evidence, and the four questions as buttons, keeps a progress bar and a jump
list, remembers your work in that browser between sessions, and **Save label
file** writes `labels-<reviewer>.json` in exactly the format below. If you stop
part way, save anyway; the file is named `-partial` until every item is labeled,
and **Resume from label file** restores it later, in any browser. The page makes
no network requests and embeds no data; everything stays on your machine.

## What to return

Return a separate file; do not edit the packet. The page produces this format
for you; the rest of this section is for anyone labeling by hand. Name it
`labels-<reviewer>.json`, where `<reviewer>` is `r1` for `reviewer-primary.json`
and `r2` for `reviewer-double.json`. Its shape:

```json
{
  "schema_version": 1,
  "reviewer": "r1",
  "packet": "reviewer-primary.json",
  "items": [
    {
      "packet_id": "r1-00001",
      "labels": {
        "support": ["supported", "unsupported"],
        "meaning_preservation": "lost",
        "citations": ["all_relevant", "needed_source_absent"],
        "readability": ["readable", "readable"]
      },
      "rationale": "Version 2 drops the funding figure the second source gave and its prose still cites that figure."
    }
  ]
}
```

Rules the validator enforces:

- `items` contains exactly one entry per `packet_id` in your packet, no more and
  no fewer.
- `support`, `citations`, and `readability` are lists with one label per version,
  in the packet's version order (position 0 is the first version shown).
- `meaning_preservation` is a single label; it is `not_applicable` for a
  one-version item and never `not_applicable` for a pair.
- Every label is one of the values listed in the packet's `rubric.labels`.
- `rationale` is a non-empty string.

Check your file before sending it back:

```bash
python3 -S -m agent_runner.audit_labels reviewer-primary.json labels-r1.json
```

It prints the item and label counts when the file is complete, or names the first
problem. It never changes either file.

## Practicalities

- Expect roughly two to four minutes per control item and five to eight per pair.
  The primary packet is about four hours of work, the double packet about two.
  Stop and resume as you like; nothing is timed.
- Work alone. Do not discuss items with the other reviewer until both label files
  are returned; the study reports your agreement before adjudication.
- Treat the packet as confidential: it contains unpublished generated text and
  feed excerpts. Do not post or share it, and delete your copy after the study
  reports.
- If an item is malformed or a question cannot be answered from the evidence,
  still label it with your best reading and say so in the rationale; do not skip
  it.

## What happens next

Both label files are frozen before anyone looks at the correspondence between
packet ids and the underlying runs. Items where the two reviewers' labels
differ on a material question go to a third person, who sees only the blinded
evidence and both rationales. The study reports raw agreement and adjudicated
outcomes with their exact denominators, and names the date on which labels were
frozen and the correspondence was opened.
