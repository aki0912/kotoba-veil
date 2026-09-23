"""Compare frozen application snapshots on one reviewed JSONL dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import sys
from pathlib import Path


def code_hashes(root: Path, package: str = "app") -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / package).rglob("*.py"))}


def summarize(runs: list[dict]) -> dict:
    comparison = {}
    for metric in ("total", "median", "p95", "model_load"):
        values = {}
        for variant in ("before", "after"):
            times = [(r["report"]["metadata"]["model_load_ms"] if metric == "model_load"
                      else r["report"]["latency_ms"][metric])
                     for r in runs if r["variant"] == variant]
            values[variant] = {"median_ms": statistics.median(times),
                               "min_ms": min(times), "max_ms": max(times)}
        before, after = (values[v]["median_ms"] for v in ("before", "after"))
        values.update(ratio=after / before if before else None,
                      change_percent=100 * (after / before - 1) if before else None)
        comparison[metric] = values
    final = next(r["report"] for r in runs if r["variant"] == "after")
    coverage = final["source_pii_coverage"]
    by_type = coverage["per_source_label"]
    gates = {"character_recall_at_least_95": coverage["character_recall"] >= .95,
             "character_precision_at_least_98": coverage["character_precision"] >= .98,
             "total_time_at_most_2x": comparison["total"]["ratio"] <= 2}
    for kind in ("PERSON", "ADDRESS"):
        item = by_type.get(kind, {})
        gates[f"{kind.lower()}_fully_covered_at_least_95"] = (
            bool(item.get("annotations")) and item["fully_covered"] / item["annotations"] >= .95)
    return {"latency": comparison, "goals": gates, "all_goals_met": all(gates.values())}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True,
                        help="Frozen code directory containing app/*.py")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--worker", choices=["before", "after"], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.trials < 3 or args.warmup < 0:
        parser.error("use at least three trials and nonnegative warmup")
    if args.worker:
        # Import the same evaluator with only the application's import root
        # changed. Baseline modules must never import current helper modules.
        sys.path.insert(0, str((args.output / "snapshots" / args.worker).resolve()))
        from benchmarks.run import run_benchmark
        report = run_benchmark(args.dataset, warmup=args.warmup)
        if not report["metadata"]["nlp_available"]:
            raise RuntimeError("GiNZA is required for the comparison")
        print(json.dumps(report, ensure_ascii=False))
        return 0

    from benchmarks.schema import load_jsonl
    samples = load_jsonl(args.dataset)
    if any(s.dictionary_terms or s.enabled_entities is not None for s in samples):
        parser.error("comparison requires an empty dictionary and all categories enabled")
    if not samples or any(s.entities and not s.source_entities for s in samples):
        parser.error("reviewed source annotations are required for coverage goals")
    if args.output.exists():
        parser.error("output must be a new directory to preserve previous measurements")
    root = Path(__file__).resolve().parents[1]
    roots = {"before": args.baseline_dir, "after": root}
    hashes = {variant: code_hashes(path) for variant, path in roots.items()}
    if any("app/detectors.py" not in h for h in hashes.values()):
        parser.error("both snapshots must contain app/detectors.py")
    args.output.mkdir(parents=True)
    for variant, source in roots.items():
        for relative in hashes[variant]:
            dest = args.output / "snapshots" / variant / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, dest)
    evaluator_hashes = code_hashes(root, "benchmarks")
    for relative in evaluator_hashes:
        dest = args.output / "evaluator" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, dest)
    digest = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    metadata = {"dataset_sha256": digest, "code_hashes": hashes,
                "evaluator_hashes": evaluator_hashes,
                "platform": platform.platform(), "python": platform.python_version(),
                "trials_per_variant": args.trials, "warmup": args.warmup,
                "scope": "text detection; excludes model load, I/O, scoring and PDF pipeline"}
    (args.output / "experiment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    runs = []
    accuracy = {}
    for trial in range(args.trials):
        order = ("before", "after") if trial % 2 == 0 else ("after", "before")
        for variant in order:
            command = [sys.executable, "-m", "benchmarks.compare_detection",
                       "--baseline-dir", str(args.baseline_dir), "--dataset", str(args.dataset),
                       "--output", str(args.output), "--trials", str(args.trials),
                       "--warmup", str(args.warmup), "--worker", variant]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            report = json.loads(result.stdout)
            filename = f"{variant}-{trial+1}.json"
            (args.output / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            current = {key: report[key] for key in ("exact", "overlap", "errors", "source_pii_coverage")}
            if variant in accuracy and current != accuracy[variant]:
                raise RuntimeError(f"non-deterministic detection: {variant}")
            accuracy[variant] = current
            runs.append({"variant": variant, "trial": trial+1, "report": report})
            if report["metadata"]["dataset_sha256"] != digest:
                raise RuntimeError("input changed during measurement")
            print(f"{variant} {trial+1}/{args.trials}: {report['latency_ms']['total']/1000:.3f}s", flush=True)
    if evaluator_hashes != code_hashes(root, "benchmarks"):
        raise RuntimeError("evaluator changed during measurement")
    if hashes != {variant: code_hashes(path) for variant, path in roots.items()}:
        raise RuntimeError("source code changed during measurement")
    if any(code_hashes(args.output / "snapshots" / v) != hashes[v] for v in roots):
        raise RuntimeError("snapshot changed during measurement")
    summary = summarize(runs)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["all_goals_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
