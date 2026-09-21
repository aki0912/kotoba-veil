"""Compare a Git baseline with the working tree, sequentially in fresh processes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def timed(action):
    started = time.perf_counter_ns()
    result = action()
    return result, (time.perf_counter_ns() - started) / 1_000_000


def summarize(runs):
    """Use process-level medians so repetitions are not independent trials."""
    metrics = {
        "model_load_ms": lambda run: run["model_load_ms"],
        "first_inference_ms": lambda run: run["first_inference_ms"],
        "pdf_detection_ms": lambda run: statistics.median(run["pdf_detection_ms"]),
        "synthetic_total_ms": lambda run: run["synthetic_total_ms"],
    }
    for stage in ("extract_ms", "detect_ms", "mask_ms", "total_ms"):
        metrics[f"pipeline_{stage}"] = lambda run, key=stage: statistics.median(
            row[key] for row in run["pipeline"])
    summary = {}
    for name, value in metrics.items():
        item = {}
        for variant in ("before", "after"):
            values = [value(run) for run in runs if run["variant"] == variant]
            item[variant] = {"median_ms": statistics.median(values),
                             "min_ms": min(values), "max_ms": max(values)}
        before, after = item["before"]["median_ms"], item["after"]["median_ms"]
        item["delta_ms"] = after - before
        item["change_percent"] = 100 * (after / before - 1) if before else None
        summary[name] = item
    return summary


def worker(args):
    os.environ.pop("KOTOBA_VEIL_DISABLE_NLP", None)
    snapshots = args.output / "snapshots"
    detector = load_module("app.detectors", snapshots / f"{args.worker}-detectors.py")
    load_module("app.pdf_documents", snapshots / "after-pdf_documents.py")
    documents = load_module("app.documents", snapshots / f"{args.worker}-documents.py")
    corrected = load_module("corrected_documents", snapshots / "after-documents.py")
    from app.models import DictionaryEntry
    from benchmarks.schema import load_jsonl

    gold = load_jsonl(args.review / "gold.jsonl")
    samples = load_jsonl(args.dataset)
    dictionaries = [[DictionaryEntry(id=i, term=t.term, entity_type=t.entity_type,
                                    note="benchmark", created_at="1970-01-01T00:00:00+00:00")
                     for i, t in enumerate(s.dictionary_terms, 1)] for s in samples]
    source = args.review / "source.pdf"
    blocks = corrected.DocumentProcessor().extract(source)
    assert [b.text for b in blocks] == [s.text for s in gold]
    engine, init_ms = timed(detector.JapanesePiiEngine)
    available, load_ms = timed(lambda: engine.nlp_available)
    assert available, "GiNZA is required for this comparison"
    _, first_ms = timed(lambda: engine.analyze(samples[0].text, None, [], samples[0].id))
    for sample in samples[:10]:
        engine.analyze(sample.text, None, [], sample.id)

    def detect_pdf():
        return corrected.DocumentProcessor.analyze_blocks(blocks, engine, None, [])

    findings = detect_pdf()  # Unmeasured whole-document warmup.
    expected = {(i, e.entity_type, e.start, e.end) for i, s in enumerate(gold) for e in s.entities}
    indices = {b.id: i for i, b in enumerate(blocks)}
    predicted = {(indices[f.block_id], f.entity_type, f.start, f.end) for f in findings}
    result = {"model_load_ms": init_ms + load_ms, "first_inference_ms": first_ms,
              "pdf_detection_ms": [], "pipeline": [],
              "pdf_accuracy": {"tp": len(expected & predicted), "fp": len(predicted - expected),
                               "fn": len(expected - predicted)}}
    for _ in range(args.pdf_repeats):
        _, elapsed = timed(detect_pdf)
        result["pdf_detection_ms"].append(elapsed)

    processor = documents.DocumentProcessor()
    output = args.output / f"{args.worker}-masked.pdf"

    def pipeline():
        started = time.perf_counter_ns()
        extracted, extract_ms = timed(lambda: processor.extract(source))
        found, detect_ms = timed(lambda: processor.analyze_blocks(extracted, engine, None, []))
        accepted = {f.id for f in found}
        _, mask_ms = timed(lambda: processor.mask(source, output, found, accepted, "*"))
        return {"extract_ms": extract_ms, "detect_ms": detect_ms, "mask_ms": mask_ms,
                "total_ms": (time.perf_counter_ns() - started) / 1_000_000,
                "blocks": len(extracted), "findings": len(found)}

    pipeline()  # Import/file-cache warmup, excluded from measurements.
    for _ in range(args.pdf_repeats):
        result["pipeline"].append(pipeline())

    latencies = []
    tp = fp = fn = 0
    for sample, dictionary in zip(samples, dictionaries):
        found, elapsed = timed(lambda: engine.analyze(sample.text, sample.enabled_entities,
                                                      dictionary, sample.id))
        latencies.append(elapsed)
        actual = {(f.entity_type, f.start, f.end) for f in found}
        expected = {(e.entity_type, e.start, e.end) for e in sample.entities}
        tp += len(actual & expected)
        fp += len(actual - expected)
        fn += len(expected - actual)
    result["synthetic_sample_ms"] = latencies
    result["synthetic_total_ms"] = sum(latencies)
    result["synthetic_accuracy"] = {"tp": tp, "fp": fp, "fn": fn}
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="HEAD")
    parser.add_argument("--review", type=Path, default=Path("data/annotation-review/r8-roles"))
    parser.add_argument("--dataset", type=Path, default=Path("benchmarks/datasets/synthetic-v1.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("build/performance-comparison"))
    parser.add_argument("--trials", type=int, default=6)
    parser.add_argument("--pdf-repeats", type=int, default=5)
    parser.add_argument("--worker", choices=["before", "after"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.trials < 2 or args.pdf_repeats < 1:
        parser.error("at least two trials and one PDF repetition are required")
    if args.worker:
        worker(args)
        return

    review = json.loads((args.review / "review.json").read_text())
    manifest = json.loads((args.review / "gold.manifest.json").read_text())
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    assert review["status"] == "human_confirmed"
    assert review["revision"] == manifest["review_revision"]
    assert digest(args.review / "source.pdf") == review["source_sha256"] == manifest["source_sha256"]
    assert all(block["reviewed"] for block in review["blocks"])
    snapshots = args.output / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    baseline = subprocess.check_output(["git", "rev-parse", args.baseline], text=True).strip()
    for name in ("detectors", "documents"):
        (snapshots / f"before-{name}.py").write_bytes(
            subprocess.check_output(["git", "show", f"{baseline}:app/{name}.py"]))
        (snapshots / f"after-{name}.py").write_bytes(Path(f"app/{name}.py").read_bytes())
    (snapshots / "after-pdf_documents.py").write_bytes(Path("app/pdf_documents.py").read_bytes())
    report = {"metadata": {"baseline_commit": baseline, "platform": platform.platform(),
              "machine": platform.machine(), "python": platform.python_version(),
              "packages": {n: importlib.metadata.version(n) for n in ("ginza", "ja-ginza", "spacy", "pypdf")},
              "trials": args.trials, "pdf_repeats_per_trial": args.pdf_repeats,
              "source_sha256": digest(args.review / "source.pdf"),
              "gold_sha256": digest(args.review / "gold.jsonl"),
              "dataset_sha256": digest(args.dataset), "review_revision": review["revision"],
              "snapshot_sha256": {p.name: digest(p) for p in sorted(snapshots.glob("*.py"))}},
              "runs": []}
    for trial in range(args.trials):
        order = ("before", "after") if trial % 2 == 0 else ("after", "before")
        for variant in order:
            command = [sys.executable, "-m", "benchmarks.compare_performance", "--worker", variant,
                       "--output", str(args.output), "--review", str(args.review),
                       "--dataset", str(args.dataset), "--pdf-repeats", str(args.pdf_repeats)]
            completed = subprocess.run(command, capture_output=True, text=True, check=True)
            result = json.loads(completed.stdout)
            report["runs"].append({"trial": trial + 1, "variant": variant, **result})
            (args.output / "raw.json").write_text(json.dumps(report, indent=2) + "\n")
            print(f"trial {trial + 1}/{args.trials} {variant}: "
                  f"PDF {statistics.median(result['pdf_detection_ms']):.1f} ms, "
                  f"synthetic {result['synthetic_total_ms']:.1f} ms", flush=True)
    (args.output / "summary.json").write_text(json.dumps(summarize(report["runs"]), indent=2) + "\n")


if __name__ == "__main__":
    main()
