# Evidence release assets

The three public evidence bundles are split between the repository and a GitHub release.

Each bundle directory in `docs/results/` keeps its small, reviewable files:
`SHA256SUMS`, `metadata.json`, and `report.md`. The large files (`manifest.json`,
`ledger.json`, `adjudications.json`, and `report.json`) are published as one
`.tar.gz` asset per bundle on the release
[`evidence-bundles-2026-09-28`](https://github.com/elanthus/news-briefing/releases/tag/evidence-bundles-2026-09-28).
Git history before that release still contains the files.

| Bundle | Asset | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| [`parity-v1-evidence/`](parity-v1-evidence/) | `parity-v1-evidence.tar.gz` | 3,850,373 | `ce95660dddfcf3862cb506c3a84d830b924b98ff6f4e294cf4482cd711aa7000` |
| [`parity-v2-evidence/`](parity-v2-evidence/) | `parity-v2-evidence.tar.gz` | 4,599,011 | `c219cac45494c7d582de12143c8da491c842fc3229ca77f4ab7863051ca63b2f` |
| [`portfolio-v2-evidence/`](portfolio-v2-evidence/) | `portfolio-v2-evidence.tar.gz` | 2,461,937 | `1688d1fe830020a86a2b3e96a0e5f771c10e8f9583a2dd88663869a72b412bd8` |

[`evaluator/evidence-assets.json`](../../evaluator/evidence-assets.json) pins the tag, asset
names, archive hashes, and member lists. CI and the commands below read that file.

The former `docs/results/data/portfolio-v2-ledger.json` was a byte-identical copy of
`portfolio-v2-evidence/ledger.json` and is not published separately.

## Fetch and verify

```bash
python3 -S -m evaluator.evidence_assets fetch
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v1-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v2-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/portfolio-v2-evidence
```

`fetch` downloads each asset over HTTPS, refusing any redirect hop to a non-HTTPS URL before it is requested, and rejects it unless its SHA-256 matches
`evaluator/evidence-assets.json`. It accepts only regular tar members with the
expected names, and each member must match the bundle's in-tree `SHA256SUMS`.
Files are written only after every asset passes. The assembled bundles, including
copies of the in-tree files, go to `.news-briefing/evidence/<bundle>/`, which is
gitignored, so a fetch leaves the working tree clean. Pass `--asset-dir <dir>` to
read already-downloaded archives instead of the release.

## Repacking

`python3 -S -m evaluator.evidence_assets pack --source-root <dir> --output-dir <dir>`
writes the archives from full bundle directories. Members are sorted, and mtime,
owner, and mode are fixed, so the same files produce the same bytes and hashes.
