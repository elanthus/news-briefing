# Evidence release assets

The public evidence bundles are split between the repository and a GitHub release.

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
| [`parity-v3-evidence/`](parity-v3-evidence/) | `parity-v3-evidence.tar.gz` (upload pending) | 6,223,463 | `c4747e940a0d708280bc7ebbe33edb2dd78b237029a3cc5720ad1aeba337b821` |

## Parity v3 asset: upload pending

The Parity v3 archive was packed on 2026-10-16 with `evaluator.evidence_assets pack` from the verified
export (members `adjudications.json`, `ledger.json`, `manifest.json`, `report.json`; member hashes in
[`parity-v3-evidence/SHA256SUMS`](parity-v3-evidence/SHA256SUMS)). It is not yet a release asset, so
`fetch` does not know it and [`evaluator/evidence-assets.json`](../../evaluator/evidence-assets.json)
does not list it. The archive is kept locally at `.news-briefing/evidence/parity-v3-evidence.tar.gz`
beside the full bundle directory, which `verify-public-run` has verified. To publish it:

1. Upload `parity-v3-evidence.tar.gz` as an asset of the release
   `evidence-bundles-2026-09-28` (the configuration pins one release tag), and confirm the uploaded
   asset's SHA-256 is `c4747e940a0d708280bc7ebbe33edb2dd78b237029a3cc5720ad1aeba337b821` and its size
   6,223,463 bytes.
2. Add to `evaluator/evidence-assets.json`:
   `{"bundle": "parity-v3-evidence", "name": "parity-v3-evidence.tar.gz", "sha256": "c4747e940a0d708280bc7ebbe33edb2dd78b237029a3cc5720ad1aeba337b821", "files": ["adjudications.json", "ledger.json", "manifest.json", "report.json"]}`.
3. Run `python3 -S -m evaluator.evidence_assets fetch` and
   `python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v3-evidence`, then remove
   the "upload pending" marks above.

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
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v3-evidence
python3 -S -m unittest discover -s evaluator/tests -p test_comparison.py -k committed
```

The last command regenerates `parity-v2-comparison-production-runner.json` and its Markdown from
the fetched manifests and requires both to match the committed files. It is skipped when the
bundles have not been fetched.

`fetch` downloads each asset over HTTPS, refusing any redirect hop to a non-HTTPS URL before it is requested, and rejects it unless its SHA-256 matches
`evaluator/evidence-assets.json`. It accepts only regular tar members with the
expected names, and each member must match the bundle's in-tree `SHA256SUMS`.
Files are written only after every asset passes. The assembled bundles, including
copies of the in-tree files, go to `.news-briefing/evidence/<bundle>/`, which is
gitignored, so a fetch leaves the working tree clean. Pass `--asset-dir <dir>` to
read already-downloaded archives instead of the release.

## Integrity verification and historical regeneration

The current `verify-public-run` command checks declared file names, byte sizes,
hashes, source metadata, and a ledger rebuilt independently from manifest row
primitives. It treats report JSON and Markdown as authenticated historical files;
it does not render them with today's reporter. Authentication comes from the
pinned release archive and in-tree checksums checked by `fetch`, not from a
bundle's self-declared checksum file alone.

To regenerate a historical report, read `source_tag` in its `metadata.json`, check
out that tag in a separate worktree, and run the source reporter against the
public manifest's already-adjudicated rows:

```bash
python3 -S - /absolute/path/to/bundle /tmp/historical-report <<'PYTHON'
import json
import sys
from pathlib import Path
from evaluator.runner import markdown_report, summarize

bundle, destination = map(Path, sys.argv[1:])
manifest = json.loads((bundle / "manifest.json").read_text())
report = summarize(manifest)
report["generated_at"] = json.loads((bundle / "report.json").read_text())["generated_at"]
destination.mkdir(parents=True, exist_ok=False)
(destination / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
(destination / "report.md").write_text(markdown_report(report))
PYTHON
```

The public manifest embeds adjudicated scores. The ordinary `evaluator report`
command reloads per-trial adjudication files from a private run tree; those files
are not distributed in this flattened bundle. Use the snippet above for public
historical report regeneration and preserve the authenticated original and its
hashes. Current reporting tests check metric and statistical semantics without
requiring current presentation to reproduce historical bytes.

## Repacking

`python3 -S -m evaluator.evidence_assets pack --source-root <dir> --output-dir <dir>`
writes the archives from full bundle directories. Members are sorted, and mtime,
owner, and mode are fixed, so the same files produce the same bytes and hashes.
