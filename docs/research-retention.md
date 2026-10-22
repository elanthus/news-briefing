# Research evidence retention and reader retirement

Retained portfolio research uses a private dataset bound to its source evidence.
Historical production audits are not inputs to the current publication loader.
Frozen public benchmarks retain their original bytes and policies.
They are not current operating inputs or judge caches.

## Retained material

| Material | Treatment |
|---|---|
| September 30 HY3/Jev study | Retain the complete study: all original runs, unchanged reports, repair candidates, controlled prose errors, confirmations, costs, and manual assessments. |
| October 2 assessment | Retain reconstructed reports, frozen evidence, blind packets, judgments, disagreements, adjudications, and correspondence maps. This is machine assessment, not independent human validation or historical deployment measurement. |
| Citation-relevance and published-text review studies | Retain source files and their limitations; do not infer excerpt support from text-only review. |
| Portfolio v2, Parity v1, Parity v2 public bundles | Preserve exact release bytes, checksums, protocols, and source tags. Reproduce historical reporting with the recorded source revision. |
| Recent Actions artifacts | Preserve ciphertext and deployment receipts separately. A downloaded encrypted wrapper does not establish its inner schema or quality. |
| Historical development and raw benchmark working trees | Exclude from the active portfolio dataset. Preserve a verified original archive before removing redundant working copies. |

The retained September review files have no in-repository reader.
They were not inputs to the completed [Parity v3](results/parity-v3.md) or [Jev-stage evaluation](results/jev-stage-v1.md).
Parity v3 used frozen generation fixtures and the Parity v2 baseline.
The Jev-stage evaluation used schema-3 daily audits and verified current-schema corpora.
Neither study reused September review labels or caches.

The raw archive retains uncertain review-provenance records.
Migration does not resolve conflicting human-review claims.
Failures, rejected repairs, partial reviews, and missing facts remain visible.
See the [evaluation methodology](evaluation-methodology.md).

## One-time research extraction

Run extraction from the original checkout before removing any selected collection:

```bash
python3 -S -m evaluator.research migrate --root . \
  --output .news-briefing/portfolio-retained-v1-final
python3 -S -m evaluator.research verify .news-briefing/portfolio-retained-v1-final
```

These commands document the completed extraction. They require the original collections.
`COLLECTIONS` in `evaluator/research.py` defines the fixed collection list.
Every collection must exist, and the destination must not exist.
Migration never rewrites or deletes inputs.

Files become deduplicated SHA-256 blobs.
Extraction derives current-schema ready runs and the September study's original/repair correspondence.
Reports, experiments, packets, and scripts remain indexed by original relative paths, including files without checkpoints.
Historical attachments do not become new production audits.

The version-1 manifest records collections, sizes, hashes, run views, repair pairs, and unavailable runs.
Verification checks blobs, reconstructs the file tree in a temporary directory, and re-derives run views from frozen inputs.
Altered derived topics fail verification.
Symlinks, escaping paths, overlapping roots, and oversized inputs fail closed.

Dataset verification depends on readers at the retention receipt's `source_revision`.
Use this extractor with those reader modules in a separate checkout.
The extractor was introduced later, so that revision alone does not contain the command.
Blob integrity does not guarantee current-reader compatibility.

Unavailable runs retain null topics and a fixed reason within the sampling frame.
Original files remain available for investigation.
Migration does not invent missing digests, follow-up checks, reviewer identities, or acceptance policies.
Capture hashes identify preserved bytes; they cannot prove unrecorded generation provenance.
Keep the returned manifest digest outside the dataset as an integrity anchor.

The dataset is private and gitignored.
Publish only approved aggregates and examples under the corpus-publication policy.
The [October 15 receipt](results/retention-2026-10-15.json) records extraction and preservation digests without source text.

## Current reader contract

| Input | Accepted current version |
|---|---|
| Corpus | Schema 7. |
| Public history | Schema 9. |
| Publication sidecar | Complete current metadata shape. |
| Semantic-repair audit | Metadata schema 3 with an explicit citation threshold. |
| Failure record | Schema 1. |

A performed follow-up requires its recorded check list.
A run without follow-up omits that list.
Per-slot acceptance, bounds, artifact verification, and fail-closed publication remain mandatory.

Audit versions 1 and 2 and candidate-only publication states are retired.
Old sidecar/history shapes, unversioned failures, and textual stop-reason inference are also retired.
`candidate_retained` and `historical_unknown` are not current publication decisions.
Missing optional provenance stays unavailable.

The synthetic historical candidate fixture no longer promises runtime compatibility.
Its original bytes remain in Git history at `d76a990`.
Frozen public bundles use their authenticated release contract and recorded source revision.
Historical corpora do not require rewriting.

## Recovery and limits

The retention receipt identifies the original archive and its SHA-256.
The private companion inventory lists archived members and their digests.

1. Verify the archive hash and member hashes.
2. Prepare a separate checkout of the recorded source revision.
3. Restore original relative paths there.

Do not extract an untrusted archive over a live checkout.
The extractor has no in-place or delete mode.
Current public history passed strict readers without changing its Markdown or clearing history.

The preserved GitHub wrappers' authenticated contents were inspected on October 21, 2026.
The [compatibility record](results/retention-2026-10-21.md) lists retained formats and reader support at that inspection.
These wrappers remain source evidence, not current publication inputs or counted migrated research samples.
Scheduled restoration follows its own current-corpus and retention contract.
Preservation does not establish a new quality benchmark or prompt-only baseline.

### October 21 compatibility verification

The inspection found two incompatible retained formats.
Current handling differs as follows:

- Audit metadata schemas 1 and 2 remain rejected by `load_public_audit`.
- Pre-#291 recorded selected evidence with escaped masked strings now has a narrow compatibility path.

Rejected historical audits cannot replace the original candidate in a newly derived publication record.
Existing historical public records remain unchanged.

The selected-evidence mismatch affected October 1, 3, 5, and the first October 6 run.
At inspection, these ran only with readers at `393c78d`, the last main commit before #291.
`load_verified_run` now re-decodes only recorded strings containing the destination marker.
It still requires exact equality with the current projection and passes the projection to consumers.
All four now pass this boundary. Production projection grammar and the research dataset remain unchanged.

The [JSON compatibility table](results/retention-2026-10-21.json) records per-day inspection evidence.
Other retained formats passed the inspected readers. This is a dated finding, not a permanent compatibility guarantee.

### Restoring preserved local wrappers

Use the local mode without a GitHub token or network request:

```bash
python3 -S restore_private_corpora.py \
  --artifact-dir DIR --newest YYYY-MM-DD --output-dir OUT
```

`DIR` contains `manifest.json` and preserved `ID.zip` wrappers for `briefing-corpus-archive`.
An example source is `.news-briefing/portfolio-preservation-2026-10-15/github`.
Output must be absent or empty.

Each wrapper record must include `workflow_run.head_branch`, `head_repository_id`, and `repository_id`.
Restoration requires main-branch, same-repository provenance, as the API path does.
Missing, branch, or fork provenance fails closed.
It checks wrapper sizes and hashes, extracts the bounded encrypted member, and authenticates it with `CORPUS_ARCHIVE_PASSPHRASE`.
Current corpus validation still applies. Conflicting copies of a date fail restoration.

The original preservation manifest lacks workflow provenance.
Keep its bytes and digest unchanged.
Use `--artifact-manifest PATH` for a separate copy supplemented from authenticated GitHub run metadata.
Preserve wrapper ID, size, hash, and source revision.
Read provenance with:

```bash
gh api repos/OWNER/REPO/actions/runs/RUN_ID \
  --jq '{head_branch, head_repository_id: .head_repository.id, repository_id: .repository.id}'
```

Check the run's `head_sha` against the row's `source_revision` before recording provenance.
The prepared private copy is `.news-briefing/portfolio-preservation-2026-10-15/github-corpus-main-manifest.json`.
The original manifest remains the receipt's integrity anchor.
Provenance capture is a separate online operation. Restoration remains offline and never reads a token.
Origin metadata does not authenticate encrypted contents.

Each invocation restores only the fourteen-date window ending at `--newest`.
Use earlier end dates and separate empty output directories for earlier windows.
Ciphertext and manifest presence alone do not establish usable dates, inner schemas, source quality, or evaluation suitability.
Count a day only after authentication and current-schema validation succeed.
