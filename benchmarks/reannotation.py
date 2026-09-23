"""Build app-compatible benchmarks from Codex's text-only annotation records."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from benchmarks.schema import BenchmarkSample, GoldSpan, SourceSpan

ROOT = Path("data/annotation-review/generated-ja-218")
APP_LABELS = {
    "PERSON", "ORGANIZATION", "LOCATION", "ADDRESS", "PHONE_NUMBER", "POSTAL_CODE",
    "EMAIL_ADDRESS", "PERSONAL_ID", "DRIVER_LICENSE", "BANK_ACCOUNT", "CREDIT_CARD",
    "DATE_TIME", "URL", "IP_ADDRESS", "CUSTOM",
}
EXTRA_LABELS = {"PASSPORT", "TAX_ID", "SOCIAL_ID", "ORDER_ID", "IDENTIFIER",
                "PAYMENT_ID", "AGE", "SEX", "GENDER"}
LABELS = APP_LABELS | EXTRA_LABELS


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".review-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_entities(text: str, spans: list[dict]) -> list[dict]:
    end = 0
    ordered = sorted(spans, key=lambda span: (span["start"], span["end"]))
    for span in ordered:
        start, stop = span["start"], span["end"]
        if (type(start) is not int or type(stop) is not int or start < end
                or not 0 <= start < stop <= len(text) or span["entity_type"] not in LABELS
                or span["text"] != text[start:stop]):
            raise ValueError("Invalid, overlapping or mismatched span")
        end = stop
    return ordered


def unresolved(row: dict) -> bool:
    return bool(row["issues"] or row.get("review_required")) and not row["human_reviewed"]


def materialize(text: str, entries: list[list]) -> list[dict]:
    """Resolve manually selected literal strings, never detector predictions.

    Repeated strings require an explicit one-based occurrence index.
    """
    spans = []
    for entry in entries:
        if len(entry) not in (2, 3):
            raise ValueError("Annotation must contain label, literal text, optional occurrence")
        label, literal, *occurrence = entry
        if label not in LABELS or not isinstance(literal, str) or not literal:
            raise ValueError(f"Invalid annotation: {entry!r}")
        matches = list(re.finditer(re.escape(literal), text))
        if not matches or (not occurrence and len(matches) != 1):
            raise ValueError(f"Literal must match uniquely or specify an occurrence: {literal!r}")
        number = occurrence[0] if occurrence else 1
        if type(number) is not int or not 1 <= number <= len(matches):
            raise ValueError(f"Invalid occurrence for {literal!r}")
        match = matches[number - 1]
        spans.append({"entity_type": label, "start": match.start(), "end": match.end(), "text": literal})
    spans.sort(key=lambda span: (span["start"], span["end"]))
    end = 0
    for span in spans:
        if span["start"] < end:
            raise ValueError(f"Overlapping manual annotations: {span['text']!r}")
        end = span["end"]
    return spans


def build(root: Path = ROOT, *, dataset: dict, complete: bool = False) -> dict:
    """Build supplied annotation selections; every exclusion must be explicit."""
    info = dataset_info({"dataset": dataset})
    sources = [json.loads(line) for line in (root / "texts-only.jsonl").read_text().splitlines()]
    by_id = {row["id"]: row for row in sources}
    if len(by_id) != len(sources):
        raise ValueError("Duplicate source IDs")
    labeled = {}
    for path in sorted((root / "annotations").glob("*.json")):
        for row in json.loads(path.read_text()):
            uid = row["id"]
            if uid not in by_id or uid in labeled:
                raise ValueError(f"Unknown or duplicate annotation ID: {uid}")
            original = by_id[uid]
            if row.get("keep", True) is False and row["entities"]:
                raise ValueError(f"Excluded row has annotations: {uid}")
            try:
                spans = materialize(original["text"], row["entities"])
            except ValueError as exc:
                raise ValueError(f"UID {uid}: {exc}") from exc
            labeled[uid] = {
                "id": uid, "split": original["split"], "text": original["text"],
                "text_sha256": digest(original["text"].encode()),
                "keep": row.get("keep", True), "language_reason": row.get("reason", "本文を日本語と判断"),
                "entities": spans, "issues": row.get("issues", []), "notes": row.get("notes", []),
                "codex_reviewed": True, "human_reviewed": False,
            }
    if complete:
        missing = [row["id"] for row in sources if row["id"] not in labeled]
        if missing:
            raise ValueError(f"Unannotated source rows: {missing}")
    result = {
        "dataset": info,
        "status": "codex_draft" if complete else "in_progress", "revision": 0,
        "policy_accepted": False,
        "source_snapshot_sha256": digest((root / "texts-only.jsonl").read_bytes()),
        "source_rows": len(sources), "codex_reviewed_rows": sum(r["codex_reviewed"] for r in labeled.values()),
        "retained_rows": sum(row["keep"] for row in labeled.values()),
        "review_issue_rows": sum(bool(row["issues"]) for row in labeled.values()),
        "rows": [labeled[row["id"]] for row in sources if row["id"] in labeled],
    }
    atomic_json(root / "codex-draft.json", result)
    return result


def dataset_info(state: dict) -> dict:
    # Custom datasets must supply their own provenance, never inherit an unrelated license.
    info = state.get("dataset")
    if not isinstance(info, dict):
        raise ValueError("Dataset metadata is required")
    required = {"id", "name", "source", "license", "attribution", "notes", "splits"}
    if not required <= info.keys() or not isinstance(info["splits"], list):
        raise ValueError("Incomplete dataset metadata")
    if not info["splits"] or len(set(info["splits"])) != len(info["splits"]):
        raise ValueError("Invalid dataset splits")
    if not set(info["splits"]) <= {"train", "validation", "dev", "test"}:
        raise ValueError("Invalid dataset splits")
    return info


def sample_for(row: dict, *, dataset: dict, status: str = "codex_draft") -> BenchmarkSample:
    spans = row["entities"]
    info = dataset_info({"dataset": dataset})
    return BenchmarkSample(
        id=f"{info['id']}:{row['split']}:{row['id']}",
        language="ja", split=row["split"], source=info["source"], text=row["text"],
        entities=[GoldSpan(**span) for span in spans if span["entity_type"] in APP_LABELS],
        source_entities=[SourceSpan(**span) for span in spans],
        tags=[info["id"], "text-annotated", status],
        notes=info["notes"],
    )


def export_dataset(state: dict, root: Path = ROOT) -> dict:
    """Publish an immutable revision directory; manifest is written last."""
    started = time.perf_counter()
    if state["status"] == "in_progress":
        raise ValueError("Codex annotation pass is incomplete")
    info = dataset_info(state)
    destination = root / "exports" / f"revision-{state['revision']}-{state['status']}"
    samples = [sample_for(row, status=state["status"], dataset=info)
               for row in state["rows"] if row["keep"]]
    if not samples:
        raise ValueError("Cannot export an empty dataset")
    if any(sample.split not in info["splits"] for sample in samples):
        raise ValueError("Dataset splits would omit retained rows")
    contents = {}
    outputs = {}
    for split in info["splits"]:
        chosen = [sample for sample in samples if sample.split == split]
        data = "".join(sample.model_dump_json() + "\n" for sample in chosen).encode()
        contents[f"ja-{split}.jsonl"] = data
        counts = Counter(span.entity_type for sample in chosen for span in sample.source_entities)
        outputs[f"ja-{split}.jsonl"] = {
            "sha256": digest(data), "samples": len(chosen), "all_label_counts": dict(sorted(counts.items())),
            "app_gold_spans": sum(len(sample.entities) for sample in chosen),
            "unsupported_spans": sum(count for label, count in counts.items() if label not in APP_LABELS),
        }
    public_state = {key: state[key] for key in (
        "status", "revision", "policy_accepted", "source_snapshot_sha256", "source_rows", "rows"
    )}
    public_state["dataset"] = info
    state_hash = digest(json.dumps(public_state, ensure_ascii=False, sort_keys=True).encode())
    manifest = {
        "dataset": info["name"],
        "license": info["license"], "attribution": info["attribution"],
        "status": state["status"], "review_revision": state["revision"], "review_sha256": state_hash,
        "source_snapshot_sha256": state["source_snapshot_sha256"],
        "source_rows": state["source_rows"], "retained_rows": len(samples),
        "human_reviewed_rows": sum(row["human_reviewed"] for row in state["rows"]),
        "unresolved_rows": sum(unresolved(row) for row in state["rows"]),
        "all_rows_human_reviewed": all(row["human_reviewed"] for row in state["rows"] if row["keep"]),
        "policy_accepted": state.get("policy_accepted", False), "outputs": outputs,
        "unsupported_labels": sorted(EXTRA_LABELS),
    }
    marker = destination / "manifest.json"
    if marker.exists():
        existing = json.loads(marker.read_text())
        if existing["review_sha256"] != state_hash:
            raise ValueError("Export revision already exists with different content")
        for name, content in contents.items():
            if (destination / name).read_bytes() != content:
                raise ValueError("Export file was modified")
        return existing
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        (destination / name).write_bytes(content)
    manifest["export_ms"] = round((time.perf_counter() - started) * 1000, 3)
    atomic_json(marker, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--metadata", type=Path, required=True,
                        help="JSON with dataset ID, provenance and output splits")
    parser.add_argument("--complete", action="store_true", help="Require every candidate to be annotated")
    args = parser.parse_args()
    result = build(args.root, dataset=json.loads(args.metadata.read_text()), complete=args.complete)
    if args.complete:
        export_dataset(result, args.root)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
