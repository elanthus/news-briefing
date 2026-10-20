"""Build a blinded adjudication packet from two reviewers' label files, and validate the result.

Run ``python3 -S -m agent_runner.audit_adjudication export PACKET_R1 LABELS_R1
PACKET_R2 LABELS_R2 --output-dir DIR --private-key KEY`` to write
``adjudication-packet.json`` (shared items whose labels disagree on a material
question, with both reviewers' labels and rationales shown as A and B in a
per-item random order) and a private key that maps each adjudication item back
to the two packet ids and the A/B assignment. Shared items are matched by their
blinded content, so no correspondence key is read. Run ``... validate
ADJUDICATION_PACKET DECISIONS`` to check an adjudicator's returned file.

Run ``... outcomes PACKET_R1 LABELS_R1 PACKET_R2 LABELS_R2 ADJUDICATION_PACKET
DECISIONS --key KEY [--correspondence KEY] [--freeze-record RECORD]`` to compute adjudicated outcomes:
every shared item keeps its agreed labels and takes the adjudicator's decision
on each disputed question. Reading the key is the unblinding step; with
``--freeze-record`` the inputs are checked against the committed digests first,
and the record then receives the counts and ``correspondence_opened_at``.
The optional packet correspondence key splits shared pairs by original/repaired
side and records its digest and separate ``packet_correspondence_opened_at``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import secrets
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_runner.audit_labels import PER_VERSION, validate_labels
from agent_runner.audit_packets import PROTOCOL_ID
from agent_runner.checkpoint import write_json_atomic, write_text_atomic

QUESTIONS = (*PER_VERSION, "meaning_preservation")
MATERIAL = ("support", "citations", "meaning_preservation")
FAVORABLE = {"support": "supported", "meaning_preservation": "preserved",
             "citations": "all_relevant", "readability": "readable"}
RECORD_TIME = "%Y-%m-%dT%H:%M:%SZ"
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
        # Reviewer kind (person or model) is recorded with the frozen labels, not asserted to the adjudicator.
        "instructions": ("Two independent reviewers labeled each item from the evidence shown; they disagreed on "
                         "the questions listed. Read both rationales, judge only from the supplied evidence, and "
                         "choose the final label for each listed question. Agreed questions are shown for context "
                         "and are not re-decided. A and B are assigned at random per item and are not the same "
                         "reviewer throughout."),
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


Final = dict[str, list[str | None]]
Slot = tuple[str, int | None]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _blinded(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each version reduced to the fields every packet carries, so an enriched item still matches its origin."""
    return [{"headline": v["headline"], "prose": v["prose"],
             "evidence": [{"title": e["title"], "excerpt": e.get("excerpt")} for e in v["evidence"]]}
            for v in versions]


def _resolve(first: dict[str, Any], second: dict[str, Any], pair: bool,
             decided: dict[Slot, str]) -> Final:
    """Agreed labels stay; each decided slot takes its decision; a disputed slot nobody decided stays None."""
    final: Final = {q: [left if left == right else None
                        for left, right in zip(first[q], second[q], strict=True)] for q in PER_VERSION}
    if pair:
        agreed = first["meaning_preservation"] == second["meaning_preservation"]
        final["meaning_preservation"] = [first["meaning_preservation"] if agreed else None]
    for (question, version), label in decided.items():
        final[question][0 if version is None else int(version)] = label
    return final


def _own(entry: dict[str, Any], versions: int) -> Final:
    """One reviewer's labels for an item nobody else labeled, in the shape of a resolved item."""
    aligned = _aligned(entry, list(range(versions)))
    final: Final = {q: list(aligned[q]) for q in PER_VERSION}
    if versions == 2:
        final["meaning_preservation"] = [aligned["meaning_preservation"]]
    return final


def _tally(finals: list[tuple[bool, Final]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Per group (pairs, controls) and question: the favorable count, its exact denominator, and every label."""
    tallies: dict[str, dict[str, dict[str, Any]]] = {}
    for group, pair in (("pairs", True), ("controls", False)):
        per_question: dict[str, dict[str, Any]] = {}
        for question in QUESTIONS:
            labels = [label for is_pair, final in finals if is_pair == pair for label in final.get(question, [])]
            if not labels:
                continue
            counts = Counter(label for label in labels if label is not None)
            per_question[question] = {"favorable": FAVORABLE[question], "count": counts[FAVORABLE[question]],
                                      "of": len(labels), "unresolved": sum(label is None for label in labels),
                                      "labels": dict(sorted(counts.items()))}
        tallies[group] = per_question
    return tallies


def _pairs_by_side(rows: list[dict[str, Any]], finals: list[tuple[bool, Final]],
                   correspondence: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Resolve both packet orders before tallying each shared pair once per side."""
    if (not isinstance(correspondence, dict) or correspondence.get("schema_version") != 1
            or correspondence.get("protocol_id") != PROTOCOL_ID
            or not isinstance(correspondence.get("mapping"), list)):
        raise ValueError("not a packet correspondence key")
    mapping = {}
    for entry in correspondence["mapping"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("packet_id"), str):
            raise ValueError("malformed packet correspondence entry")
        packet_id = entry["packet_id"]
        if packet_id in mapping:
            raise ValueError("packet correspondence repeats a packet id")
        mapping[packet_id] = entry
    grouped: dict[str, list[tuple[bool, Final]]] = {"original": [], "repaired": []}
    for row, (pair, final) in zip(rows, finals, strict=True):
        if not pair:
            continue
        orders = []
        for packet in ("first", "second"):
            entry = mapping.get(row[packet]["packet_id"], {})
            sides = entry.get("sides")
            if (entry.get("kind") != "pair" or not isinstance(sides, list)
                    or sides not in (["original", "repaired"], ["repaired", "original"])):
                raise ValueError("each shared pair must map to exactly one original and one repaired side")
            orders.append(sides)
        if orders[0] != [orders[1][index] for index in row["order"]]:
            raise ValueError("packet correspondence sides disagree after version alignment")
        for index, side in enumerate(orders[0]):
            grouped[side].append((True, {q: [final[q][index]] for q in PER_VERSION}))
    return {side: _tally(values)["pairs"] for side, values in grouped.items()}


def adjudicated_outcomes(packet_first: Path, labels_first: Path, packet_second: Path, labels_second: Path,
                         adjudication_path: Path, decisions_path: Path, key_path: Path, *,
                         opened_at: str | None = None, correspondence_path: Path | None = None,
                         packet_opened_at: str | None = None) -> dict[str, Any]:
    """Final labels per shared item and their counts per question, for repair pairs and controls separately.

    Both label files and the decisions file are validated first. Shared items
    are recomputed by blinded content; the key is read only after that, and
    each key entry must agree with the adjudication packet (content, both
    reviewers' labels, and the listed disagreements) before its decisions are
    applied. Any shared item with a material disagreement that the packet does
    not carry fails closed. Flags never change the main outcomes; when present,
    the same counts with flags applied are returned beside them.
    """
    validate_labels(packet_first, labels_first)
    validate_labels(packet_second, labels_second)
    checked = validate_adjudication(adjudication_path, decisions_path)
    first, second = _read(packet_first), _read(packet_second)
    lf, ls = _read(labels_first), _read(labels_second)
    adjudication, decisions = _read(adjudication_path), _read(decisions_path)
    if first.get("rubric") != second.get("rubric") or adjudication.get("rubric") != first.get("rubric"):
        raise ValueError("the packets carry different rubrics")
    result = compare(first, lf, second, ls)
    rows = {row["first"]["packet_id"]: row for row in result["rows"]}
    opened_at = opened_at or datetime.now(UTC).strftime(RECORD_TIME)
    key = _read(key_path)  # The unblinding step: from here on, reviewer identities are known per item.
    if not isinstance(key, dict) or key.get("schema_version") != 1 or not isinstance(key.get("mapping"), list):
        raise ValueError("not an adjudication key")
    digests = {lf["reviewer"]: _sha256(labels_first), ls["reviewer"]: _sha256(labels_second)}
    if not isinstance(key.get("summary"), dict) or key["summary"].get("label_files_sha256") != digests:
        raise ValueError("the key was not built from these label files")
    mapping = {str(entry["adjudication_id"]): entry for entry in key["mapping"]}
    if len(mapping) != len(key["mapping"]) or set(mapping) != {it["adjudication_id"] for it in adjudication["items"]}:
        raise ValueError("the key does not map the adjudication items exactly once")
    sides = {lf["reviewer"]: "first", ls["reviewer"]: "second"}
    entries = {entry["adjudication_id"]: entry for entry in decisions["items"]}
    decided: dict[str, dict[Slot, str]] = {}
    flagged: dict[str, dict[Slot, str]] = {}
    for item in adjudication["items"]:
        adjudication_id = item["adjudication_id"]
        entry = mapping[adjudication_id]
        row = rows.get(str(entry["first_packet_id"]))
        if (row is None or row["second"]["packet_id"] != entry["second_packet_id"]
                or row["order"] != entry["second_version_order"]):
            raise ValueError(f"{adjudication_id}: key entry does not name a shared item")
        if _blinded(item["versions"]) != _blinded(row["first"]["versions"]):
            raise ValueError(f"{adjudication_id}: packet content differs from the reviewer packet")
        for side in ("A", "B"):
            if item["labels"][side] != row[f"labels_{sides[entry[f'reviewer_{side}']]}"]:
                raise ValueError(f"{adjudication_id}: labels shown as {side} are not reviewer "
                                 f"{entry[f'reviewer_{side}']}'s")
        listed = {(d["question"], d["version"]) for d in item["disagreements"]}
        if listed != {(d["question"], d["version"]) for d in row["differing"]} or not row["material"]:
            raise ValueError(f"{adjudication_id}: listed disagreements differ from the recomputed ones")
        packet_id = row["first"]["packet_id"]
        decided[packet_id] = {(d["question"], d["version"]): d["label"] for d in entries[adjudication_id]["decisions"]}
        flagged[packet_id] = {(f["question"], f["version"]): f["label"]
                              for f in entries[adjudication_id].get("flags", [])}
    finals, with_flags = [], []
    for packet_id, row in rows.items():
        if row["material"] and packet_id not in decided:
            raise ValueError(f"{packet_id}: a material disagreement was not adjudicated")
        pair = len(row["first"]["versions"]) == 2
        final = _resolve(row["labels_first"], row["labels_second"], pair, decided.get(packet_id, {}))
        finals.append((pair, final))
        with_flags.append((pair, _resolve(row["labels_first"], row["labels_second"], pair,
                                          {**decided.get(packet_id, {}), **flagged.get(packet_id, {})})))
    single: dict[str, Any] = {}
    shared_second = {row["second"]["packet_id"] for row in rows.values()}
    for packet, labels, shared in ((first, lf, set(rows)), (second, ls, shared_second)):
        entries_by_id = {e["packet_id"]: e for e in labels["items"]}
        own = [(len(item["versions"]) == 2, _own(entries_by_id[item["packet_id"]], len(item["versions"])))
               for item in packet["items"] if item["packet_id"] not in shared]
        if own:
            single[labels["reviewer"]] = {"items": len(own), "pairs": sum(p for p, _ in own),
                                          "controls": sum(not p for p, _ in own), "outcomes": _tally(own)}
    outcomes: dict[str, Any] = _tally(finals)
    unresolved = sum(cell["unresolved"] for group in outcomes.values() for cell in group.values())
    packet_opening = {}
    if correspondence_path is not None:
        packet_opened_at = packet_opened_at or datetime.now(UTC).strftime(RECORD_TIME)
        outcomes["pairs_by_side"] = _pairs_by_side(list(rows.values()), finals, _read(correspondence_path))
        packet_opening["packet_correspondence_opened_at"] = packet_opened_at
    return {
        "correspondence_opened_at": opened_at,
        **packet_opening,
        "adjudicator": checked["adjudicator"],
        "shared_items": result["shared_items"], "shared_pairs": result["shared_pairs"],
        "shared_controls": result["shared_controls"],
        "adjudicated_items": checked["items"], "decisions_applied": checked["decisions"],
        "unresolved_slots": unresolved,
        "outcomes": outcomes,
        "flags": checked["flags"], "flag_counts": checked["flag_counts"],
        "outcomes_with_flags": _tally(with_flags) if checked["flags"] else None,
        "single_labeled": single,
        "inputs_sha256": {"packets": {path.name: _sha256(path) for path in (packet_first, packet_second)},
                          "labels": digests, "adjudication_packet": _sha256(adjudication_path),
                          "decisions": _sha256(decisions_path), "key": _sha256(key_path),
                          **({"packet_correspondence": _sha256(correspondence_path)}
                             if correspondence_path is not None else {})},
    }


def verify_frozen_inputs(record: dict[str, Any], *, packets: list[Path], labels: list[Path],
                         adjudication_path: Path, decisions_path: Path) -> None:
    """Fail closed unless every input's digest is the one the committed freeze record retains."""
    returned = record.get("adjudication_decisions_returned")
    if not isinstance(returned, dict) or not isinstance(record.get("label_files"), dict):
        raise ValueError("freeze record does not retain returned decisions")
    expected = {
        **{path: set(record["packet_files"].values()) for path in packets},
        **{path: {entry["sha256"] for entry in record["label_files"].values()} for path in labels},
        adjudication_path: {returned["validated_against_packet_sha256"]},
        decisions_path: {returned["sha256"]},
    }
    for path, allowed in expected.items():
        if _sha256(path) not in allowed:
            raise ValueError(f"{path.name}: digest is not in the freeze record")


def record_outcomes(record_path: Path, outcomes: dict[str, Any]) -> dict[str, Any]:
    """Write the counts and the unblinding time into the freeze record; the first opening time is kept."""
    record = _read(record_path)
    previous = record.get("adjudicated_outcomes", {}).get("inputs_sha256", {})
    for name in ("key", "packet_correspondence"):
        if name in previous and previous[name] != outcomes["inputs_sha256"].get(name):
            raise ValueError(f"{name}: recorded key digest changed or key omitted")
    if record.get("packet_correspondence_opened_at") and "packet_correspondence_opened_at" not in outcomes:
        raise ValueError("packet correspondence was already opened; rerun with --correspondence")
    opened = record.get("correspondence_opened_at") or outcomes["correspondence_opened_at"]
    outcomes["correspondence_opened_at"] = opened
    record["correspondence_opened_at"] = opened
    if "packet_correspondence_opened_at" in outcomes:
        packet_opened = record.get("packet_correspondence_opened_at") or outcomes["packet_correspondence_opened_at"]
        outcomes["packet_correspondence_opened_at"] = packet_opened
        record["packet_correspondence_opened_at"] = packet_opened
    if isinstance(record.get("adjudication_decisions_returned"), dict):
        record["adjudication_decisions_returned"]["correspondence_opened_at"] = opened
    record["adjudicated_outcomes"] = {
        "date": opened[:10], "computed_at": datetime.now(UTC).strftime(RECORD_TIME),
        "method": ("agreed labels kept; each disputed question takes the adjudicator's decision; shared items "
                   "recomputed by blinded content and every key entry checked against the adjudication packet "
                   "before use"),
        **{k: outcomes[k] for k in ("adjudicator", "shared_items", "shared_pairs", "shared_controls",
                                    "adjudicated_items", "decisions_applied", "unresolved_slots", "outcomes",
                                    "flags", "flag_counts", "outcomes_with_flags", "single_labeled")},
        "inputs_sha256": outcomes["inputs_sha256"],
        **({"packet_correspondence_opened_at": outcomes["packet_correspondence_opened_at"]}
           if "packet_correspondence_opened_at" in outcomes else {}),
    }
    write_text_atomic(record_path, json.dumps(record, indent=2, ensure_ascii=True) + "\n")
    return record


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
    outcomes = commands.add_parser("outcomes", help="final labels and counts after adjudication (reads the key)")
    for name in ("packet_first", "labels_first", "packet_second", "labels_second", "adjudication", "decisions"):
        outcomes.add_argument(name, type=Path)
    outcomes.add_argument("--key", type=Path, required=True, help="private key written by export")
    outcomes.add_argument("--correspondence", type=Path,
                          help="private packet correspondence key; split shared pairs by original/repaired side")
    outcomes.add_argument("--freeze-record", type=Path,
                          help="check inputs against this record's digests, then write the counts into it")
    args = parser.parse_args()
    try:
        if args.command == "outcomes":
            opened_at = None
            packet_opened_at = None
            if args.freeze_record is not None:
                record = _read(args.freeze_record)
                verify_frozen_inputs(record, packets=[args.packet_first, args.packet_second],
                                     labels=[args.labels_first, args.labels_second],
                                     adjudication_path=args.adjudication, decisions_path=args.decisions)
                opened_at = record.get("correspondence_opened_at")
                packet_opened_at = record.get("packet_correspondence_opened_at")
            result = adjudicated_outcomes(args.packet_first, args.labels_first, args.packet_second,
                                          args.labels_second, args.adjudication, args.decisions, args.key,
                                          opened_at=opened_at, correspondence_path=args.correspondence,
                                          packet_opened_at=packet_opened_at)
            if args.freeze_record is not None:
                record_outcomes(args.freeze_record, result)
        elif args.command == "export":
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
