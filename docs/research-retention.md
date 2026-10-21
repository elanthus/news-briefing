# Research evidence retention and reader retirement

The portfolio evaluation uses a private, source-bound research dataset instead
of feeding historical production audits through the current publication loader.
Frozen public benchmarks remain unchanged. Historical source bytes and their
original policies are evidence, not current operating inputs or judge caches.

## Retained material

| Material | Treatment |
|---|---|
| September 30 HY3/Jev study | Retain the complete study: all original runs, unchanged reports, repair candidates, controlled prose errors, confirmations, costs, and manual assessments. |
| October 2 assessment | Retain reconstructed reports, frozen evidence, blind packets, judgments, disagreements, adjudications, and correspondence maps. This is machine assessment, not independent human validation or historical deployment measurement. |
| Citation-relevance and published-text review studies | Retain source files and their limitations; do not infer excerpt support from text-only review. |
| Portfolio v2, Parity v1, Parity v2 public bundles | Preserve exact release bytes, checksums, protocols, and source tags. Reproduce historical reporting with the recorded source revision. |
| Recent Actions artifacts | Preserve ciphertext and deployment receipts separately. A downloaded encrypted wrapper does not establish its inner schema or quality. |
| Historical development and raw benchmark working trees | Exclude from the active portfolio dataset. Preserve a verified original archive before removing redundant working copies. |

The retained September review files have no in-repo reader. They are historical
attachments, not inputs to either planned eval: Parity v3 uses its frozen
generation fixtures and Parity v2 baseline; the Jev-stage evaluation uses new
schema-3 daily audits and verified current-schema corpora. Neither reuses the
September reviews as judge labels or caches.

The raw archive also preserves uncertain review-provenance records. Conflicting
human-review claims do not become validated human assessments through migration.
Failures, rejected repairs, partial reviews, and missing information remain visible;
they are never converted to successful scores. See the [evaluation methodology](evaluation-methodology.md).

## One-time research extraction

From the original checkout, before removing any selected source collection:

```bash
python3 -S -m evaluator.research migrate --root . \
  --output .news-briefing/portfolio-retained-v1-final
python3 -S -m evaluator.research verify .news-briefing/portfolio-retained-v1-final
```

The fixed collection list is `COLLECTIONS` in `evaluator/research.py`. Migration
requires each collection to exist and the destination not to exist. It never
rewrites or deletes inputs. It captures original files as deduplicated SHA-256
blobs, then extracts current-schema ready runs and the September study's
original/repair correspondence. Raw reports, prose experiments, assessment
packets, and scripts remain indexed by their original relative paths, including
files that have no runner checkpoint. They are historical attachments, not new
current-schema production audits.

The version-1 manifest records collections, file sizes and hashes, complete run
views, repair pairs, and unavailable run records. Verification checks all blobs,
reconstructs the captured file tree in a temporary directory, and re-derives run
views from the original frozen selections, evidence, corpus, configuration, and
hash-bound Markdown. Altered derived topics fail verification. Symlinks, escaping
paths, overlapping collection roots, and oversized inputs fail closed.

`verify()` of the research dataset depends on the readers at the recorded
`source_revision` in the [retention receipt](results/retention-2026-10-15.json).
It reconstructs the captured tree and calls those readers to re-derive run views;
blob integrity alone does not make current readers compatible with old inputs.
Run verification with this extractor and the recorded revision's reader modules
in a separate checkout. The extractor was introduced after that revision, so an
unmodified checkout of `source_revision` alone does not contain the command.

An unavailable run stays in the sampling frame with null extracted topics and a
fixed reason; its original files remain available for investigation. Migration
does not add missing generation-time digests, invent follow-up checks, rerun a
model, relabel a reviewer, or change an old repair's acceptance policy. Capture
hashes prove which bytes were migrated; they do not retroactively prove an
unrecorded generation identity. Keep the returned manifest digest outside the
dataset when using it as an integrity anchor.

This dataset is private and gitignored. Publish only approved aggregates and
examples, consistent with the repository's corpus-publication policy. The
[October 15 retention receipt](results/retention-2026-10-15.json) records the
completed extraction and preservation digests without private source text.

## Current reader contract

Production accepts history schema 9, the complete current sidecar shape,
semantic-repair audit metadata schema 3, and versioned failure records schema 1.
Semantic audits require their explicit citation threshold. A performed follow-up
requires its recorded check list; a run with no follow-up still omits that list.
Current per-slot apply/reject decisions, bounded parsing, artifact verification,
and fail-closed publication acceptance remain mandatory.

Audit versions 1 and 2, candidate-only publication states, old sidecar/history shapes, unversioned
failures, and textual stop-reason inference are retired. The synthetic historical
candidate fixture is no longer an active runtime-compatibility promise. Its
original source and bytes remain available in Git history at `d76a990`.
Historical `candidate_retained` and `historical_unknown` states are not accepted
as current publication decisions. Missing optional provenance remains unavailable;
removing old readers does not invent historical facts.

The corpus reader already accepts only schema 7. Its historical snapshots do not
need to be rewritten. Frozen public evidence still uses its authenticated release
contract and original source revision; current generation and judging retain
their existing stricter evidence requirements.

## Recovery and limits

The retention receipt identifies the local original archive and its SHA-256. The
private companion inventory lists every archived member and its digest. Verify
both archive and member hashes before restoring original relative paths into a
separate checkout of the recorded revision. Never extract an untrusted archive
over a live checkout. The research extractor has no in-place or delete mode.

Current public history was checked with the strict readers without changing its
Markdown or clearing history. The preserved GitHub wrappers' authenticated inner
contents were inspected on October 21, 2026; the
[compatibility record](results/retention-2026-10-21.md) lists every retained
format, the readers that accept it, and the artifacts the current readers reject.
They remain cold evidence: not inputs to current publication and not counted as
migrated research samples.
Scheduled corpus restoration remains governed by its separate current-corpus
validation and retention contract. This change does not run a new quality
benchmark or establish a prompt-only baseline.

### October 21 compatibility verification

Two retained formats were rejected by the readers at current main on October
21. One still is, and is recovered only with the readers at the run's recorded
source revision; the other now reads at current main:

- Daily semantic-audit envelopes with metadata schema 1 or 2 (runs before
  October 14). `load_public_audit` returns no audit for them, so a re-derived
  publication record carries the original candidate without the audit. The
  historical public record is unchanged.
- Runs whose recorded `selected-evidence.json` keeps CommonMark backslash
  escapes inside strings the projection masks, which #291 removed from
  `redact_destinations` output: October 1, 3, 5 and the first October 6 run. On
  October 21 `load_verified_run` reported that their recorded evidence differed
  from the frozen corpus projection, and they read only at `393c78d`, the last
  main commit before #291. `load_verified_run` now re-decodes just the recorded
  strings that carry the destination marker with the current decoder, still
  requires exact equality with the projection, and hands consumers the
  projection rather than the recorded text; all four read at current main. The
  production projection grammar is unchanged, and the research dataset was never
  affected.

Every other retained format reads at current main. The
[compatibility record](results/retention-2026-10-21.md) and its
[JSON table](results/retention-2026-10-21.json) give the per-day evidence.

### Restoring preserved local wrappers

`restore_private_corpora.py --artifact-dir DIR --newest YYYY-MM-DD --output-dir OUT`
reads `DIR/manifest.json` and the preserved `ID.zip` wrappers for
`briefing-corpus-archive`, without a GitHub token or network request. For example,
`DIR` can be `.news-briefing/portfolio-preservation-2026-10-15/github`.
The manifest must also record `workflow_run.head_branch`, `head_repository_id`
and `repository_id` for each corpus wrapper. The same main-branch, same-repository
check as the API path is required; missing or branch/fork provenance fails closed.
It checks wrapper sizes and SHA-256 values, extracts the same bounded encrypted
member as the API path, authenticates it with `CORPUS_ARCHIVE_PASSPHRASE`, and
uses the existing strict corpus restoration checks. Conflicting copies of a date
fail closed. Output must be absent or empty.

The original preservation manifest lacks workflow provenance. Keep its recorded
bytes and digest unchanged. Use `--artifact-manifest PATH` to supply a separate
copy with those fields added from authenticated GitHub run metadata for each
row's `run_id`, preserving its wrapper ID, size, digest and source revision.
For a run's provenance fields, the read-only metadata command is:

```bash
gh api repos/OWNER/REPO/actions/runs/RUN_ID \
  --jq '{head_branch, head_repository_id: .head_repository.id, repository_id: .repository.id}'
```

Check the run's `head_sha` against the row's `source_revision` before recording
its provenance. The private supplemented copy prepared for the preserved wrappers
is `.news-briefing/portfolio-preservation-2026-10-15/github-corpus-main-manifest.json`;
the original manifest remains the receipt's integrity anchor. This one-time
provenance capture is separate from restoration, which remains fully offline and
never reads a token. Origin metadata does not authenticate encrypted contents.

Preserved wrappers can supply corpus days beyond one API retention window.
Each invocation still restores only the fourteen-date window ending at `--newest`;
use separate empty output directories and earlier end dates for earlier windows.
The preserved manifest and ciphertext alone do not establish inner schemas,
usable corpus dates, source quality, or suitability for either planned eval.
Authentication and current-schema validation must succeed before counting a day.
