"""Build a blinded adjudication packet from two reviewers' label files, and validate the result.

Run ``python3 -S -m agent_runner.audit_adjudication export PACKET_R1 LABELS_R1
PACKET_R2 LABELS_R2 --output-dir DIR --private-key KEY`` to write
``adjudication-packet.json`` (shared items whose labels disagree on a material
question, with both reviewers' labels and rationales shown as A and B in a
per-item random order) and a private key that maps each adjudication item back
to the two packet ids and the A/B assignment. Shared items are matched by their
blinded content, so no correspondence key is read. Run ``... validate
ADJUDICATION_PACKET DECISIONS`` to check an adjudicator's returned file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import secrets
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from agent_runner.audit_labels import PER_VERSION, validate_labels
from agent_runner.checkpoint import write_json_atomic

QUESTIONS = (*PER_VERSION, "meaning_preservation")
MATERIAL = ("support", "citations", "meaning_preservation")
MAX_BYTES = 50_000_000


def _read(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name}: missing, oversized, or not a regular file")
    return json.loads(path.read_text(encoding="utf-8"))


def _version_key(version: dict[str, Any]) -> str:
    return json.dumps(version, sort_keys=True, ensure_ascii=True)


def _item_key(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(_version_key(v) for v in item["versions"]))


Shared = list[tuple[dict[str, Any], dict[str, Any], list[int]]]


def shared_items(first: dict[str, Any], second: dict[str, Any]) -> Shared:
    """Items present in both packets by content, with the second packet's version order mapped to the first's."""
    by_key = {_item_key(item): item for item in second["items"]}
    if len(by_key) != len(second["items"]):
        raise ValueError("second packet repeats an item's content")
    matched = []
    for item in first["items"]:
        other = by_key.get(_item_key(item))
        if other is None:
            continue
        other_keys = [_version_key(v) for v in other["versions"]]
        order = [other_keys.index(_version_key(v)) for v in item["versions"]]
        matched.append((item, other, order))
    return matched


def _aligned(entry: dict[str, Any], order: list[int]) -> dict[str, Any]:
    labels = entry["labels"]
    aligned = {question: [labels[question][j] for j in order] for question in PER_VERSION}
    aligned["meaning_preservation"] = labels["meaning_preservation"]
    return aligned


def compare(first: dict[str, Any], labels_first: dict[str, Any], second: dict[str, Any], labels_second: dict[str, Any]
            ) -> dict[str, Any]:
    """Raw agreement over shared items and the per-item list of disagreeing questions."""
    entries_first = {e["packet_id"]: e for e in labels_first["items"]}
    entries_second = {e["packet_id"]: e for e in labels_second["items"]}
    agree: Counter[str] = Counter()
    total: Counter[str] = Counter()
    rows = []
    for item, other, order in shared_items(first, second):
        a = _aligned(entries_first[item["packet_id"]], list(range(len(item["versions"]))))
        b = _aligned(entries_second[other["packet_id"]], order)
        differing = []
        for question in PER_VERSION:
            for index, (left, right) in enumerate(zip(a[question], b[question], strict=True)):
                total[question] += 1
                if left == right:
                    agree[question] += 1
                else:
                    differing.append({"question": question, "version": index})
        if len(item["versions"]) == 2:
            total["meaning_preservation"] += 1
            if a["meaning_preservation"] == b["meaning_preservation"]:
                agree["meaning_preservation"] += 1
            else:
                differing.append({"question": "meaning_preservation", "version": None})
        rows.append({"first": item, "second": other, "order": order, "labels_first": a, "labels_second": b,
                     "rationale_first": entries_first[item["packet_id"]]["rationale"],
                     "rationale_second": entries_second[other["packet_id"]]["rationale"],
                     "differing": differing,
                     "material": any(d["question"] in MATERIAL for d in differing)})
    return {
        "shared_items": len(rows),
        "shared_pairs": sum(len(r["first"]["versions"]) == 2 for r in rows),
        "shared_controls": sum(len(r["first"]["versions"]) == 1 for r in rows),
        "raw_agreement": {q: {"agreements": agree[q], "compared": total[q]} for q in QUESTIONS if total[q]},
        "items_with_any_disagreement": sum(bool(r["differing"]) for r in rows),
        "items_with_material_disagreement": sum(r["material"] for r in rows),
        "rows": rows,
    }


def export_adjudication(packet_first: Path, labels_first: Path, packet_second: Path, labels_second: Path,
                        output_dir: Path, key_path: Path, *, seed: int | None = None) -> dict[str, Any]:
    """Validate both label files, then write the blinded packet and the private key."""
    destination, private = output_dir.resolve(), key_path.resolve()
    if destination.is_relative_to(private) or private.is_relative_to(destination):
        raise ValueError("adjudication directory and private key must be separate")
    if output_dir.exists() or output_dir.is_symlink() or key_path.exists() or key_path.is_symlink():
        raise ValueError("adjudication directory and private key must be new")
    validate_labels(packet_first, labels_first)
    validate_labels(packet_second, labels_second)
    first, second = _read(packet_first), _read(packet_second)
    lf, ls = _read(labels_first), _read(labels_second)
    if first.get("rubric") != second.get("rubric"):
        raise ValueError("the two packets carry different rubrics")
    result = compare(first, lf, second, ls)
    seed = secrets.randbits(256) if seed is None else seed
    rng = random.Random(seed)
    items, mapping = [], []
    for index, row in enumerate(r for r in result["rows"] if r["material"]):
        swap = rng.random() < 0.5
        sides = ("second", "first") if swap else ("first", "second")
        a_name, b_name = ((ls if swap else lf)["reviewer"], (lf if swap else ls)["reviewer"])
        a_labels, b_labels = (row[f"labels_{sides[0]}"], row[f"labels_{sides[1]}"])
        a_rat, b_rat = (row[f"rationale_{sides[0]}"], row[f"rationale_{sides[1]}"])
        adjudication_id = f"adj-{index + 1:05d}"
        items.append({
            "adjudication_id": adjudication_id,
            "versions": row["first"]["versions"],
            "labels": {"A": a_labels, "B": b_labels},
            "rationales": {"A": a_rat, "B": b_rat},
            "disagreements": row["differing"],
        })
        mapping.append({"adjudication_id": adjudication_id, "first_packet_id": row["first"]["packet_id"],
                        "second_packet_id": row["second"]["packet_id"], "reviewer_A": a_name, "reviewer_B": b_name,
                        "second_version_order": row["order"]})
    summary = {key: value for key, value in result.items() if key != "rows"}
    summary["label_files_sha256"] = {lf["reviewer"]: hashlib.sha256(labels_first.read_bytes()).hexdigest(),
                                     ls["reviewer"]: hashlib.sha256(labels_second.read_bytes()).hexdigest()}
    key = {"schema_version": 1, "seed": seed, "mapping": mapping, "summary": summary}
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(key, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_atomic(output_dir / "adjudication-packet.json", {
        "schema_version": 1, "kind": "adjudication", "rubric": first["rubric"],
        "instructions": ("Two independent people labeled each item from the evidence shown; they disagreed on "
                         "the questions listed. Read both rationales, judge only from the supplied evidence, and "
                         "choose the final label for each listed question. Agreed questions are shown for context "
                         "and are not re-decided. A and B are assigned at random per item and are not the same "
                         "person throughout."),
        "items": items,
    })
    return summary


def validate_adjudication(packet_path: Path, decisions_path: Path) -> dict[str, Any]:
    """Return counts when ``decisions_path`` decides every listed disagreement in ``packet_path``."""
    packet, decisions = _read(packet_path), _read(decisions_path)
    if (not isinstance(packet, dict) or packet.get("kind") != "adjudication"
            or not isinstance(packet.get("items"), list)):
        raise ValueError("not an adjudication packet")
    allowed = packet["rubric"]["labels"]
    if (not isinstance(decisions, dict) or decisions.get("schema_version") != 1
            or not isinstance(decisions.get("items"), list)):
        raise ValueError("decisions file must be an object with schema_version 1 and an items list")
    if not isinstance(decisions.get("adjudicator"), str) or not decisions["adjudicator"]:
        raise ValueError("decisions file must name its adjudicator")
    expected = {item["adjudication_id"]: item for item in packet["items"]}
    seen = Counter(str(e.get("adjudication_id")) for e in decisions["items"] if isinstance(e, dict))
    if set(seen) != set(expected) or any(count > 1 for count in seen.values()):
        missing, unknown = len(set(expected) - set(seen)), len(set(seen) - set(expected))
        raise ValueError(f"item coverage: {missing} missing, {unknown} unknown, "
                         f"{sum(count > 1 for count in seen.values())} duplicated")
    counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    flagged_items = 0
    for entry in decisions["items"]:
        item = expected[entry["adjudication_id"]]
        wanted = {(d["question"], d["version"]) for d in item["disagreements"]}
        given = entry.get("decisions")
        if not isinstance(given, list):
            raise ValueError(f"{item['adjudication_id']}: decisions must be a list")
        got = {}
        for decision in given:
            if not isinstance(decision, dict):
                raise ValueError(f"{item['adjudication_id']}: malformed decision")
            key = (decision.get("question"), decision.get("version"))
            if key not in wanted or key in got:
                raise ValueError(f"{item['adjudication_id']}: decision for {key!r} is not a listed "
                                 "disagreement or repeats")
            if decision.get("label") not in allowed[str(key[0])]:
                raise ValueError(f"{item['adjudication_id']}: label {decision.get('label')!r} is not in the "
                                 f"rubric for {key[0]}")
            got[key] = decision["label"]
            counts[f"{key[0]}:{decision['label']}"] += 1
        if set(got) != wanted:
            raise ValueError(f"{item['adjudication_id']}: {len(wanted - set(got))} listed disagreement(s) "
                             "left undecided")
        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"{item['adjudication_id']}: rationale must be a non-empty string")
        # Optional flags: the adjudicator may mark an agreed label as wrong. Flags never
        # replace the frozen labels; they are reported separately as a sensitivity line.
        agreed = question_slots(item) - wanted
        seen_flags: set[tuple[Any, Any]] = set()
        raw_flags = entry.get("flags", [])
        if not isinstance(raw_flags, list):
            raise ValueError(f"{item['adjudication_id']}: flags must be a list")
        for flag in raw_flags:
            if not isinstance(flag, dict):
                raise ValueError(f"{item['adjudication_id']}: malformed flag")
            key = (flag.get("question"), flag.get("version"))
            if key not in agreed or key in seen_flags:
                raise ValueError(f"{item['adjudication_id']}: flag for {key!r} is not an agreed question "
                                 "or repeats")
            question = str(key[0])
            if flag.get("label") not in allowed[question]:
                raise ValueError(f"{item['adjudication_id']}: flag label {flag.get('label')!r} is not in the rubric")
            agreed_label = (item["labels"]["A"][question] if key[1] is None
                            else item["labels"]["A"][question][int(key[1])])
            if flag["label"] == agreed_label:
                raise ValueError(f"{item['adjudication_id']}: flag for {key!r} repeats the agreed label")
            note = flag.get("note")
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"{item['adjudication_id']}: flag for {key!r} needs a note")
            seen_flags.add(key)
            flag_counts[f"{question}:{flag['label']}"] += 1
        flagged_items += bool(seen_flags)
    return {"adjudicator": decisions["adjudicator"], "items": len(expected),
            "decisions": sum(len(item["disagreements"]) for item in expected.values()),
            "label_counts": dict(sorted(counts.items())),
            "flags": sum(flag_counts.values()), "flagged_items": flagged_items,
            "flag_counts": dict(sorted(flag_counts.items()))}


def question_slots(item: dict[str, Any]) -> set[tuple[Any, Any]]:
    """Every question/version slot an item carries, agreed or disputed."""
    slots: set[tuple[Any, Any]] = {(q, i) for q in PER_VERSION for i in range(len(item["versions"]))}
    if len(item["versions"]) == 2:
        slots.add(("meaning_preservation", None))
    return slots


def enrich_evidence(packet_path: Path, stage_root: Path, *, start: date, end: date) -> dict[str, Any]:
    """Add each evidence item's outlet name and publication date to an existing adjudication packet.

    Items are matched by their frozen title and excerpt against every verified
    run in the stage tree, so no correspondence key is read and the packet's
    item ids, version order, and A/B assignment are unchanged. An evidence item
    that matches no run, or matches runs that disagree about its outlet, fails
    closed. The packet is rewritten in place only after every item resolves.
    """
    from agent_runner.audit_metrics import load_days
    from agent_runner.jev_review import load_verified_run

    packet = _read(packet_path)
    if not isinstance(packet, dict) or packet.get("kind") != "adjudication":
        raise ValueError("not an adjudication packet")
    lookup: dict[tuple[str, str | None], set[tuple[str | None, str | None]]] = {}
    for day in load_days(stage_root, start=start, end=end):
        if day["run"] is None:
            continue
        for topic in load_verified_run(day["run"]).topics:
            for evidence in topic["evidence"]:
                key = (str(evidence["title"]), evidence.get("summary"))
                lookup.setdefault(key, set()).add((evidence.get("source"), evidence.get("published")))
    enriched = 0
    for item in packet["items"]:
        for version in item["versions"]:
            for evidence in version["evidence"]:
                matches = lookup.get((str(evidence["title"]), evidence.get("excerpt")), set())
                if len(matches) != 1:
                    raise ValueError(f"{item['adjudication_id']}: evidence resolves to {len(matches)} outlet record(s)")
                source, published = next(iter(matches))
                evidence["source"], evidence["published"] = source, published
                enriched += 1
    packet["evidence_enriched"] = {"fields": ["source", "published"], "matched_by": "frozen title and excerpt",
                                   "frame": {"start": start.isoformat(), "end": end.isoformat()}}
    write_json_atomic(packet_path, packet)
    return {"items": len(packet["items"]), "evidence_enriched": enriched}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    enrich = commands.add_parser("enrich", help="add outlet names and dates to an adjudication packet in place")
    enrich.add_argument("packet", type=Path)
    enrich.add_argument("stage_root", type=Path)
    enrich.add_argument("--start", type=date.fromisoformat, required=True)
    enrich.add_argument("--end", type=date.fromisoformat, required=True)
    export = commands.add_parser("export")
    for name in ("packet_first", "labels_first", "packet_second", "labels_second"):
        export.add_argument(name, type=Path)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument("--private-key", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("packet", type=Path)
    validate.add_argument("decisions", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "export":
            result = export_adjudication(args.packet_first, args.labels_first, args.packet_second, args.labels_second,
                                         args.output_dir, args.private_key)
        elif args.command == "enrich":
            result = enrich_evidence(args.packet, args.stage_root, start=args.start, end=args.end)
        else:
            result = validate_adjudication(args.packet, args.decisions)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
