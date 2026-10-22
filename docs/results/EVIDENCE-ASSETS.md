# Evidence release assets

Public evidence is split between this repository and a GitHub release.
Each bundle directory retains `SHA256SUMS`, `metadata.json`, and `report.md`.
Large `manifest.json`, `ledger.json`, `adjudications.json`, and `report.json` files are packaged in one `.tar.gz` per bundle.
The release is [evidence-bundles-2026-09-28](https://github.com/elanthus/news-briefing/releases/tag/evidence-bundles-2026-09-28).
Git history before that release also contains the earlier bundle files.

| Bundle | Asset | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| [`parity-v1-evidence/`](parity-v1-evidence/) | `parity-v1-evidence.tar.gz` | 3,850,373 | `ce95660dddfcf3862cb506c3a84d830b924b98ff6f4e294cf4482cd711aa7000` |
| [`parity-v2-evidence/`](parity-v2-evidence/) | `parity-v2-evidence.tar.gz` | 4,599,011 | `c219cac45494c7d582de12143c8da491c842fc3229ca77f4ab7863051ca63b2f` |
| [`portfolio-v2-evidence/`](portfolio-v2-evidence/) | `portfolio-v2-evidence.tar.gz` | 2,461,937 | `1688d1fe830020a86a2b3e96a0e5f771c10e8f9583a2dd88663869a72b412bd8` |
| [`parity-v3-evidence/`](parity-v3-evidence/) | `parity-v3-evidence.tar.gz` | 6,223,463 | `c4747e940a0d708280bc7ebbe33edb2dd78b237029a3cc5720ad1aeba337b821` |

## Fetch and verify

```bash
python3 -S -m evaluator.evidence_assets fetch
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v1-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v2-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/portfolio-v2-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v3-evidence
python3 -S -m unittest discover -s evaluator/tests -p test_comparison.py -k committed
```

The last command regenerates the committed Parity v2 production-runner comparison and requires matching JSON and Markdown.
It skips when the required bundles are absent.

`fetch` downloads over HTTPS and rejects non-HTTPS redirects before requesting them.
Each archive must match its pinned SHA-256 in `evaluator/evidence-assets.json`.
Only expected regular tar members are accepted.
Each member must match the in-tree `SHA256SUMS`.
Files are written only after every asset passes verification.

Assembled bundles go to ignored `.news-briefing/evidence/<bundle>/`.
Fetch leaves tracked files unchanged.
Use `--asset-dir DIR` to read previously downloaded archives without network access.

## Integrity verification and historical regeneration

`verify-public-run` checks declared names, sizes, hashes, source metadata, and independently rebuilt ledger primitives.
It verifies historical report bytes without rendering them through current code.
Authentication depends on pinned release archives and in-tree checksums checked by `fetch`.
A bundle's own checksum file alone does not authenticate its origin.

1. Read `source_tag` in the bundle's `metadata.json`.
2. Check out that tag in a separate worktree.
3. Run its source reporter with the authenticated public manifest:

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

Replace the bundle and output paths before execution. The output directory must be new.
The public manifest embeds adjudicated scores.
The ordinary `evaluator report` command reads private per-trial adjudications that this flattened bundle does not contain.
Use the snippet above for historical public reports.
Preserve the authenticated originals and hashes.
Current reporter tests check metric semantics, not byte-identical historical presentation.

## Repacking

`python3 -S -m evaluator.evidence_assets pack --source-root DIR --output-dir OUT` creates archives from complete bundle directories.
Members use sorted names and fixed timestamps, owners, and modes.
Identical input files produce identical archive bytes and hashes.
