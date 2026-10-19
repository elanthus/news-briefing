"""Export blinded human-review packets from verified schema-3 daily audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import secrets
from datetime import date
from pathlib import Path
from typing import Any

from agent_runner.audit_metrics import _within, flagged, load_days
from agent_runner.checkpoint import write_json_atomic
from agent_runner.integrity import position_key
from agent_runner.jev_review import load_verified_run

PROTOCOL_ID = "jev-stage-v1"
# The rubric travels with every packet so each reviewer labels against the same
# definitions. It names neither side of a pair nor any outcome. Reviewers add a
# "labels" object per item with these fields plus a free-text "rationale".
RUBRIC = {
    "support": "For each version: is every material factual claim supported by that version's own supplied "
               "title/excerpt evidence? Label supported, unsupported, strengthened, or reversed; cite the claim.",
    "meaning_preservation": "For a two-version item only: do both versions carry the same supported material "
                            "claims? Label preserved, lost, added, or changed, and name each affected claim. If more "
                            "than one applies, choose in this order: changed, then added, then lost.",
    "citations": "For each version: is every listed source relevant to the prose? Name irrelevant listed sources, "
                 "and any source present in only one version that the other version's prose still relies on.",
    "readability": "For each version: complete sentences, no copied truncated fragments. Label readable or not.",
    "labels": {
        "support": ["supported", "unsupported", "strengthened", "reversed"],
        "meaning_preservation": ["preserved", "lost", "added", "changed", "not_applicable"],
        "citations": ["all_relevant", "irrelevant_listed", "needed_source_absent"],
        "readability": ["readable", "fragment_or_incomplete"],
    },
    "instructions": "Judge only from the supplied evidence; do not consult the articles or outside knowledge. "
                    "Label every item; leave no item blank.",
}


def control_order(report_date: str, position: dict[str, Any], protocol_id: str = PROTOCOL_ID) -> str:
    """October 2 protocol ordering, using the current study's protocol id."""
    identity = protocol_id + report_date + position["section"] + str(position["index"])
    return hashlib.sha256(identity.encode()).hexdigest()


def _version(topic: dict[str, Any]) -> dict[str, Any]:
    # Explicit field selection: source/provider metadata is never forwarded.
    return {"headline": topic["headline"], "prose": topic["prose"],
            "evidence": [{"title": e["title"], "excerpt": e.get("summary")} for e in topic["evidence"]]}


def _records(root: Path, *, start: date, end: date
             ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pairs, controls, coverage = [], [], []
    for row in load_days(root, start=start, end=end):
        audit = row["audit"]
        coverage.append({"report_date": row["report_date"], "status": row["status"],
                         "audit_sha256": row["audit_sha256"]})
        if audit is None:
            continue
        source = load_verified_run(row["run"])
        original = {position_key(t["position"]): t for t in source.topics}
        changed = [t for t in audit["topics"] if t["changed"] is not None]
        candidate = None
        if changed:
            candidate = load_verified_run(_within(row["review"] / "repair" / "run", root))
        revised = {position_key(t["position"]): t for t in candidate.topics} if candidate is not None else {}
        for topic in changed:
            key = position_key(topic["position"])
            pairs.append({"versions": [_version(original[key]), _version(revised[key])],
                          "private": {"report_date": row["report_date"], "position": topic["position"],
                                      "repair_status": topic["repair_status"], "audit_sha256": row["audit_sha256"],
                                      "sources": [str(source.root), str(candidate.root) if candidate else None]}})
        # Partial initial review cannot establish that a topic is unflagged.
        if audit["status"] != "complete":
            continue
        flagged_positions = {position_key(p) for c in audit["checks"] if flagged(c, audit) for p in c["positions"]}
        eligible = [t for t in source.topics if t["included"] and position_key(t["position"]) not in flagged_positions]
        eligible.sort(key=lambda t: control_order(row["report_date"], t["position"]))
        selected = eligible[:3]
        for index, topic in enumerate(selected):
            controls.append({"versions": [_version(topic)], "double_review": index < (len(selected) + 2) // 3,
                             "private": {"report_date": row["report_date"], "position": topic["position"],
                                         "audit_sha256": row["audit_sha256"], "sources": [str(source.root)]}})
    return pairs, controls, coverage


def _packet(records: list[dict[str, Any]], rng: random.Random, reviewer: str
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = list(records)
    rng.shuffle(records)
    packet, mapping = [], []
    for index, record in enumerate(records):
        sides = list(range(len(record["versions"])))
        rng.shuffle(sides)
        packet_id = f"{reviewer}-{index + 1:05d}"
        packet.append({"packet_id": packet_id, "versions": [record["versions"][side] for side in sides]})
        mapping.append({"packet_id": packet_id, **record["private"],
                        "kind": "pair" if len(sides) == 2 else "control",
                        "sides": ["original" if side == 0 else "repaired" for side in sides]})
    return packet, mapping


def export_packets(root: Path, output_dir: Path, key_path: Path, *, start: date, end: date,
                   seed: int | None = None) -> dict[str, int]:
    """Validate before writing; keep correspondence and shuffle seed private."""
    source, destination, private = root.resolve(), output_dir.resolve(), key_path.resolve()
    if (destination.is_relative_to(source) or source.is_relative_to(destination)
            or private.is_relative_to(destination) or private.is_relative_to(source)
            or destination.is_relative_to(private)):
        raise ValueError("packet directory and private key must be separate from each other and the input tree")
    if output_dir.exists() or output_dir.is_symlink() or key_path.exists() or key_path.is_symlink():
        raise ValueError("packet directory and private key must be new")
    pairs, controls, coverage = _records(source, start=start, end=end)
    seed = secrets.randbits(256) if seed is None else seed
    rng = random.Random(seed)
    primary, primary_map = _packet(pairs + controls, random.Random(rng.getrandbits(256)), "r1")
    secondary, secondary_map = _packet(pairs + [c for c in controls if c["double_review"]],
                                       random.Random(rng.getrandbits(256)), "r2")
    key = {"schema_version": 1, "protocol_id": PROTOCOL_ID, "seed": seed,
           "start": start.isoformat(), "end": end.isoformat(), "coverage": coverage,
           "mapping": primary_map + secondary_map}
    # Exclusive creation and owner-only access; never place correspondence beside packets.
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(key, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_atomic(output_dir / "reviewer-primary.json", {"schema_version": 1, "rubric": RUBRIC, "items": primary})
    write_json_atomic(output_dir / "reviewer-double.json", {"schema_version": 1, "rubric": RUBRIC, "items": secondary})
    return {"pairs": len(pairs), "controls": len(controls),
            "primary_items": len(primary), "double_items": len(secondary)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export_packets(args.root, args.output_dir, args.private_key, start=args.start, end=args.end)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
