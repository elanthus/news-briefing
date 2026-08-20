# Near-duplicate retrieval study

This evaluator-only study embeds **title + summary** for 60 labeled pairs (20 duplicate, 40 distinct) and compares cosine thresholds with the exact production 60-character title-key heuristic. URLs are retained for provenance but are not embedded. Nothing here changes production deduplication.

- Embedding model: `openai/text-embedding-3-small`
- Dimensions: 512
- Cache generated: 2026-08-20
- Label provenance: **machine-proposed-2026-08-20, owner review pending**
- CI posture: vectors are committed; report generation is offline and credential-free.

## Comparison

| Classifier | Threshold | Precision | Recall | F1 | TP | FP | TN | FN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Production title key | — | 0.000 | 0.000 | 0.000 | 0 | 0 | 40 | 20 |
| **Embedding (chosen)** | 0.70 | 0.950 | 0.950 | 0.950 | 19 | 1 | 39 | 1 |
| Embedding | 0.75 | 1.000 | 0.800 | 0.889 | 16 | 0 | 40 | 4 |
| Embedding | 0.80 | 1.000 | 0.350 | 0.519 | 7 | 0 | 40 | 13 |
| Embedding | 0.85 | 1.000 | 0.250 | 0.400 | 5 | 0 | 40 | 15 |
| Embedding | 0.90 | 1.000 | 0.050 | 0.095 | 1 | 0 | 40 | 19 |
| Embedding | 0.95 | 0.000 | 0.000 | 0.000 | 0 | 0 | 40 | 20 |

## Operating point

The deterministic selection rule maximizes F1, then precision, then threshold. It chooses **0.70** on this fixture. This is an in-sample descriptive operating point, not a production-ready threshold; the labels still require owner sign-off and the fixture is too small for a deployment claim.

## Hard-negative error analysis

| Pair | Cosine | Embedding | Title key | Result | Rationale |
|---|---:|---|---|---|---|
| `hard-negative-01` | 0.428 | distinct | distinct | both correct | Both concern major wildfires, but they cover separate fires on different continents. |
| `hard-negative-02` | 0.403 | distinct | distinct | both correct | Both concern drought and water shortages, but one is Puerto Rico rationing and the other is Lake Mead. |
| `hard-negative-03` | 0.569 | distinct | distinct | both correct | Both concern drones in Germany, but one is an explosive drone discovery and the other a later base sighting. |
| `hard-negative-04` | 0.582 | distinct | distinct | both correct | Both concern Houthi attacks, but they target different facilities in different countries. |
| `hard-negative-05` | 0.314 | distinct | distinct | both correct | Both concern Gaza, but one is a diplomatic-plan rejection and the other is a famine feature. |
| `hard-negative-06` | 0.412 | distinct | distinct | both correct | Both concern the Iran war, but one explains regional alliances and the other tracks live negotiations. |
| `hard-negative-07` | 0.726 | duplicate | distinct | embedding FP | The templated titles are nearly identical, but the pages report elections in different states. |
| `hard-negative-08` | 0.561 | distinct | distinct | both correct | Both are live primary result pages, but they cover different states and contests. |
| `hard-negative-09` | 0.423 | distinct | distinct | both correct | Both concern identifying AI-generated text, but watermarking and third-party detectors are distinct stories. |
| `hard-negative-10` | 0.447 | distinct | distinct | both correct | Both concern agent-security failures, but they report different incidents and systems. |
| `hard-negative-11` | 0.435 | distinct | distinct | both correct | Both concern coding-agent defaults, but one is Claude auto mode and the other is Muse Code telemetry. |
| `hard-negative-12` | 0.430 | distinct | distinct | both correct | Both ask about Cursor cloud agents, but one concerns setup and the other billing attribution. |
| `hard-negative-13` | 0.457 | distinct | distinct | both correct | Both discuss AI-assisted code review, but one is a workflow bottleneck and the other reviewer independence. |
| `hard-negative-14` | 0.561 | distinct | distinct | both correct | Both concern AOC and the same interview period, but one covers political ambitions and the other fertility. |
| `hard-negative-15` | 0.670 | distinct | distinct | both correct | Both concern Max Miller, but one reports reactions to allegations and the other ballot and voter dynamics. |
| `hard-negative-16` | 0.478 | distinct | distinct | both correct | Both quote Sanders on Democratic primaries, but they concern campaign finance and a Senate race. |
| `hard-negative-17` | 0.445 | distinct | distinct | both correct | Both concern childhood vaccines, but one is an NIH defense of vaccines and the other a policy order. |
| `hard-negative-18` | 0.303 | distinct | distinct | both correct | Both arise from wildfire coverage, but one is a fatal crash and the other community smoke protection. |
| `hard-negative-19` | 0.369 | distinct | distinct | both correct | Both are Gaza human-interest reports, but they cover animal shelters and child hunger respectively. |
| `hard-negative-20` | 0.455 | distinct | distinct | both correct | Both discuss sector-specific AI risks, but one concerns oil emissions and the other bank dependence. |

## Other chosen-threshold errors

| Pair | Label | Cosine | Prediction | Rationale |
|---|---|---:|---|---|
| `duplicate-15` | duplicate | 0.644 | distinct | Both commentaries respond to the same newly published Zuckerberg AI manifesto. |

## Conclusion

On this machine-proposed fixture, the chosen embedding threshold leads by 0.950 F1. The useful result is the measured trade-off, not a predetermined embedding win. Before any production experiment, the owner must review the labels and the study should be repeated on a larger time-split sample with an explicit latency and cost budget. Until then, the production heuristic remains unchanged.
