# Retained artifact compatibility verification, October 21, 2026

Companion to [`retention-2026-10-21.json`](retention-2026-10-21.json), which holds the
per-day table, digests and artifact identifiers. This closes the remaining checks in
issue #242 after the reader retirement in #282 and the October 15
[retention receipt](retention-2026-10-15.json). The readers under test are those at
main `83ea357` (the reader revision). Where they reject a retained artifact, the same
artifact was also read at `393c78d` (the last main commit before #291) and at the run's
own recorded source revision, each in a separate `git archive` checkout.

## Verified

- **Preservation integrity.** The originals archive digest matches the receipt, every one
  of its 102,566 members matches the private member inventory, and all 52 preserved GitHub
  wrapper digests match the preserved manifest.
- **Operational inventory.** GitHub held 37 unexpired artifacts (October 8 to 21), all from
  main-branch, same-repository runs. 22 preserved wrappers are still unexpired; 30 now exist
  only in the local preservation set. The 15 artifacts created after October 15 are listed
  in the JSON record; they were inventoried, not downloaded.
- **Encrypted wrappers.** All 39 NBPA1 wrappers authenticate with the archive passphrase:
  19 corpus archives, 19 diagnostics archives and the retired grounding-monitor review. No
  wrapper contains an unsafe member.
- **Corpus wrappers.** 266 inner corpora, all corpus schema 7 with zero schema problems,
  covering September 18 to October 15. The offline restore of the window ending October 1
  succeeded. The window ending October 15 fails closed because October 2, 6 and 8 each have
  more than one preserved copy: those days had two workflow runs and each run fetched the
  day's corpus fresh. The newest-wrapper-per-day manifest written by `run_jev_stage.py`
  resolves this.
- **Diagnostics wrappers.** 25 run days. `prepare_publication.py` at the reader revision
  produces a `ready` record with integrity version 1 for every day, and the site builder's
  strict sidecar reader accepts all 25 regenerated sidecars in an isolated site environment.
  For the seven days whose published record is available (the preserved October 15 Pages
  artifact and the live history), six re-derive the published Markdown and
  published-artifact hash byte for byte. October 12 does not; see below.
- **Publication receipts.** All 12 retained receipts are schema 1 and every declared
  artifact hash (84 of 84) matches the retained run files.
- **Public surfaces.** The preserved October 15 `history.json` (schema 9, seven entries),
  the live `history.json` (October 15 to 21) and the live metrics ledger (schema 1, eight
  rows) load with the current strict readers.
- **Research dataset.** `evaluator.research verify` reports the receipt's figures both in a
  separate checkout of the recorded source revision with the extractor copied in, and at the
  reader revision.
- **Public benchmark bundles.** `verify-public-run` verifies all four fetched bundles and
  their in-tree `SHA256SUMS` files are identical to the fetched copies.

## Explicitly unsupported or unreadable at the reader revision

1. **Semantic-audit envelopes with metadata schema 1 or 2** (23 of the 25 retained days,
   September 26 to October 13). `load_public_audit` returns no audit for them by design,
   because #282 retired those schemas, and `prepare_publication.py` then publishes the
   original candidate without the audit. That is why re-deriving October 12 from its
   diagnostics differs from the historical publication, which accepted a repaired candidate
   under the then-current policy. Public history keeps the historical record unchanged.
   Recovery: the readers at each run's recorded source revision accept every retained
   envelope; the JSON record shows this per day.
2. **Four ready runs rejected by `load_verified_run`** (October 1, 3, 5 and the first
   October 6 run) with "recorded evidence differs from the frozen corpus projection". The
   cause is #291 (`f4aea95`, merged October 21): `redact_destinations` now removes backslash
   escapes, so the current projection of those corpora no longer equals the evidence recorded
   at generation. At `393c78d` and at the recorded source revisions all 25 days pass. None of
   the 17 research-dataset runs is affected, and the published Parity v3 and Jev-stage
   results were computed before #291 at their recorded revisions. A maintainer decision is
   open: keep this as documented historical-reader recovery, or add a narrow re-redaction of
   recorded evidence in `load_verified_run` that does not weaken #291.
3. **Publication receipts and the grounding-monitor review packet.** Their producers were
   retired in `e509125` and nothing in the tree reads them. They are cold evidence with
   verified hashes.

## Limits

No paid generation or judging ran. No history was cleared and `--allow-empty-history` was
not used. Decrypted inputs were read in a session scratch directory and deleted afterwards;
only metadata and digests are recorded. Reader acceptance at a revision proves readability
at that revision, not the historical quality of any run.
