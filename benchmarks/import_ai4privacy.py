"""Fetch a pinned CC-BY-4.0 dataset and convert annotations without training."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
from urllib.request import urlopen

from benchmarks.schema import BenchmarkSample, GoldSpan, SourceSpan

DATASET = "ai4privacy/pii-masking-mini-10k"
REVISION = "7b686c2e7475b02e10e38add1eb54a9f604f4361"
SOURCE_URL = f"https://huggingface.co/datasets/{DATASET}"
ROOT = Path("data/benchmarks/ai4privacy-pii-masking-mini-10k")
FILES = {
    "README.md": "2ab73f826990f482741d6ab537cefef918865eea791737b3204ab9c960e56e9d",
    "data/train.jsonl": "7f09434dbc029f32ecf702a5c302cb2599086f10212e72b34a00a9700de4cf50",
    "data/validation.jsonl": "ca828522c77b9802ae95422b846d7cd817c4309e4cde589ef4af74da9b94db7b",
}
LABEL_MAP = {
    "GIVENNAME": "PERSON", "SURNAME": "PERSON",
    "EMAIL": "EMAIL_ADDRESS", "TELEPHONENUM": "PHONE_NUMBER",
    "ZIPCODE": "POSTAL_CODE", "DATE": "DATE_TIME", "TIME": "DATE_TIME",
    "CITY": "LOCATION", "COUNTRY": "LOCATION",
    "STREET": "ADDRESS", "BUILDINGNUM": "ADDRESS",
    "DRIVERLICENSENUM": "DRIVER_LICENSE", "CREDITCARDNUMBER": "CREDIT_CARD",
    "BANKNAME": "ORGANIZATION", "ORGANISATION": "ORGANIZATION", "URL": "URL",
}
# These are not equivalent to the app's Japanese My Number or bank-account labels.
UNMAPPED_LABELS = {
    "TITLE", "AGE", "GENDER", "SEX", "IDCARDNUM", "SOCIALNUM", "PASSPORTNUM",
    "TAXNUM", "JOBTITLE", "AMOUNT", "SALARY",
}


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fetch_raw(root: Path, *, offline: bool = False) -> None:
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        destination = raw / Path(name).name
        if destination.exists():
            content = destination.read_bytes()
        elif offline:
            raise ValueError(f"Missing cached source: {destination.name}")
        else:
            with urlopen(f"{SOURCE_URL}/resolve/{REVISION}/{name}", timeout=60) as response:
                content = response.read(100_000_001)
            if len(content) > 100_000_000:
                raise ValueError(f"Source file exceeds size limit: {name}")
        if sha256(content) != expected:
            raise ValueError(f"Source SHA-256 mismatch: {name}; cached file was not overwritten")
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)


def convert_row(row: dict, *, name_spans: str = "merged") -> BenchmarkSample:
    if name_spans not in {"merged", "separate"}:
        raise ValueError("name_spans must be merged or separate")
    text = row["source_text"]
    source = [SourceSpan(entity_type=item["label"], start=item["start"], end=item["end"],
                         text=item["value"]) for item in row["privacy_mask"]]
    sample = BenchmarkSample(
        id=f"ai4privacy-mini:{row['split']}:{row['uid']}",
        language=row["language"], split=row["split"], source="licensed", text=text,
        source_entities=source,
        enabled_entities=sorted(set(LABEL_MAP.values())),
        tags=["ai4privacy-mini", f"region:{row['region']}", f"name-spans:{name_spans}"],
        notes=f"{SOURCE_URL}/tree/{REVISION}; CC-BY-4.0; original split retained",
    )  # Validate original offsets/text even for unmapped annotations.
    annotations = sorted(row["privacy_mask"], key=lambda item: (item["start"], item["end"]))
    entities = []
    index = 0
    while index < len(annotations):
        item = annotations[index]
        label = item["label"]
        if label not in LABEL_MAP and label not in UNMAPPED_LABELS:
            raise ValueError(f"Unknown source label: {label}")
        start, end = item["start"], item["end"]
        if name_spans == "merged" and index + 1 < len(annotations):
            following = annotations[index + 1]
            gap = text[end:following["start"]]
            if ({label, following["label"]} == {"GIVENNAME", "SURNAME"}
                    and item.get("label_index") is not None
                    and item["label_index"] == following.get("label_index")
                    and end <= following["start"]
                    and all(char in " \t\u3000" for char in gap)):
                end = following["end"]
                index += 1
        if label in LABEL_MAP:
            entities.append(GoldSpan(entity_type=LABEL_MAP[label], start=start, end=end,
                                     text=text[start:end]))
        index += 1
    return BenchmarkSample.model_validate({**sample.model_dump(), "entities": entities})


def prepare(root: Path = ROOT, *, languages: list[str] | None = None,
            splits: list[str] | None = None, name_spans: str = "merged",
            offline: bool = False) -> dict:
    languages = languages or ["ja"]
    splits = splits or ["train", "validation"]
    if "all" in languages and languages != ["all"]:
        raise ValueError("Use all alone, or list specific language codes")
    if set(splits) - {"train", "validation"}:
        raise ValueError("Only original train and validation splits are available")
    fetch_raw(root, offline=offline)
    started = time.perf_counter()
    selected = []
    ids = set()
    available_languages = set()
    for split in splits:
        rows = []
        for line in (root / "raw" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["split"] != split:
                raise ValueError(f"Source split mismatch for UID {row['uid']}")
            if row["uid"] in ids:
                raise ValueError(f"Duplicate source UID: {row['uid']}")
            ids.add(row["uid"])
            available_languages.add(row["language"])
            if languages == ["all"] or row["language"] in languages:
                rows.append(convert_row(row, name_spans=name_spans))
        if not rows:
            raise ValueError(f"No matching samples in split {split}")
        selected.append((split, rows))
    if languages != ["all"] and set(languages) - available_languages:
        raise ValueError(f"Unknown languages: {sorted(set(languages) - available_languages)}")

    output = root / f"converted-{name_spans}"
    output.mkdir(parents=True, exist_ok=True)
    prefix = "-".join(sorted(set(languages)))
    manifest = {
        "dataset": DATASET, "revision": REVISION, "source_url": SOURCE_URL,
        "license": "CC-BY-4.0", "attribution": "Copyright © 2026 Ai Suisse SA / ai4privacy",
        "converter_version": 1, "name_spans": name_spans, "languages": languages,
        "label_map": LABEL_MAP, "unmapped_labels": sorted(UNMAPPED_LABELS),
        "source_sha256": FILES, "outputs": {},
    }
    for split, samples in selected:
        content = "".join(json.dumps(s.model_dump(), ensure_ascii=False, separators=(",", ":")) + "\n"
                          for s in samples).encode("utf-8")
        destination = output / f"{prefix}-{split}.jsonl"
        temporary = destination.with_suffix(".jsonl.tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
        source_counts = Counter(e.entity_type for s in samples for e in s.source_entities)
        manifest["outputs"][destination.name] = {
            "sha256": sha256(content), "samples": len(samples),
            "language_counts": dict(Counter(s.language for s in samples)),
            "mapped_gold_spans": sum(len(s.entities) for s in samples),
            "source_label_counts": dict(sorted(source_counts.items())),
            "unmapped_source_spans": sum(n for label, n in source_counts.items() if label not in LABEL_MAP),
        }
    manifest["conversion_ms_excluding_download"] = round((time.perf_counter() - started) * 1000, 3)
    (output / f"{prefix}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--languages", nargs="+", default=["ja"], help="Language codes, or all")
    parser.add_argument("--splits", nargs="+", choices=["train", "validation"], default=["train", "validation"])
    parser.add_argument("--name-spans", choices=["merged", "separate"], default="merged")
    parser.add_argument("--offline", action="store_true", help="Use checksum-verified cached source files only")
    args = parser.parse_args(argv)
    manifest = prepare(args.root, languages=args.languages, splits=args.splits,
                       name_spans=args.name_spans, offline=args.offline)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
