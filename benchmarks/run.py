from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import sys
import time
from pathlib import Path

from app import detectors
from app.detectors import ENTITY_CATALOG, JapanesePiiEngine
from app.models import DictionaryEntry
from benchmarks.metrics import SampleResult, Span, evaluate
from benchmarks.schema import BenchmarkSample, load_jsonl


DEFAULT_DATASET = Path(__file__).resolve().parent / "datasets" / "synthetic-v1.jsonl"


def _dictionary_entries(sample: BenchmarkSample) -> list[DictionaryEntry]:
    return [
        DictionaryEntry(
            id=index,
            term=item.term,
            entity_type=item.entity_type,
            note=f"benchmark:{sample.id}",
            created_at="1970-01-01T00:00:00+00:00",
        )
        for index, item in enumerate(sample.dictionary_terms, start=1)
    ]


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    bytes_used = peak if sys.platform == "darwin" else peak * 1024
    return round(bytes_used / (1024 * 1024), 3)


def run_benchmark(
    dataset: str | Path,
    *,
    disable_nlp: bool = False,
    entities: list[str] | None = None,
    warmup: int = 0,
    allow_draft: bool = False,
) -> dict[str, object]:
    if warmup < 0:
        raise ValueError("warmup must be nonnegative")
    dataset_path = Path(dataset)
    samples = load_jsonl(dataset_path)
    annotation_statuses = sorted({tag for sample in samples for tag in sample.tags
                                  if tag in {"codex_draft", "review_complete"}})
    reannotation_manifest = None
    if any("text-annotated" in sample.tags for sample in samples):
        manifest_path = dataset_path.parent / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("Reannotated dataset requires its export manifest beside the JSONL")
        reannotation_manifest = json.loads(manifest_path.read_text())
        output_info = reannotation_manifest["outputs"].get(dataset_path.name)
        if not output_info or output_info["sha256"] != hashlib.sha256(dataset_path.read_bytes()).hexdigest():
            raise ValueError("Reannotated dataset checksum mismatch")
        if annotation_statuses != [reannotation_manifest["status"]]:
            raise ValueError("Annotation status differs from its export manifest")
    if "codex_draft" in annotation_statuses and not allow_draft:
        raise ValueError("Unreviewed Codex annotations: use --allow-draft explicitly for provisional evaluation")
    previous_disable_nlp = os.environ.get("KOTOBA_VEIL_DISABLE_NLP")
    if disable_nlp:
        os.environ["KOTOBA_VEIL_DISABLE_NLP"] = "1"
    else:
        os.environ.pop("KOTOBA_VEIL_DISABLE_NLP", None)

    try:
        load_started = time.perf_counter()
        engine = JapanesePiiEngine()
        nlp_available = engine.nlp_available
        model_load_ms = (time.perf_counter() - load_started) * 1000
        warmup_started = time.perf_counter()
        for sample in samples[:warmup]:
            engine.analyze(sample.text, sample.enabled_entities or entities,
                           _dictionary_entries(sample), block_id=sample.id)
        warmup_ms = (time.perf_counter() - warmup_started) * 1000
        results: list[SampleResult] = []
        for sample in samples:
            enabled = sample.enabled_entities or entities
            started = time.perf_counter()
            findings = engine.analyze(
                sample.text,
                enabled,
                _dictionary_entries(sample),
                block_id=sample.id,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            results.append(
                SampleResult(
                    sample_id=sample.id,
                    text_length=len(sample.text),
                    gold=tuple(
                        Span(item.entity_type, item.start, item.end, item.text)
                        for item in sample.entities
                    ),
                    predicted=tuple(
                        Span(
                            item.entity_type,
                            item.start,
                            item.end,
                            item.text,
                            source=item.source,
                            score=item.score,
                        )
                        for item in findings
                    ),
                    latency_ms=latency_ms,
                    tags=tuple(sample.tags),
                    source_gold=tuple(Span(item.entity_type, item.start, item.end, item.text)
                                      for item in sample.source_entities),
                )
            )
        report = evaluate(results)
        languages = sorted({sample.language for sample in samples})
        if len(languages) > 1:
            report["by_language"] = {
                language: evaluate([result for sample, result in zip(samples, results)
                                    if sample.language == language])
                for language in languages
            }
        report["metadata"] = {
            "dataset": dataset_path.as_posix(),
            "dataset_split": sorted({sample.split for sample in samples}),
            "language": languages[0] if len(languages) == 1 else "multilingual",
            "language_counts": dict(sorted(Counter(sample.language for sample in samples).items())),
            "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "detector_sha256": hashlib.sha256(Path(detectors.__file__).read_bytes()).hexdigest(),
            "nlp_available": nlp_available,
            "nlp_disabled": disable_nlp,
            "enabled_entities": entities
            or [item["id"] for item in ENTITY_CATALOG],
            "model_load_ms": round(model_load_ms, 3),
            "warmup_samples": min(warmup, len(samples)),
            "warmup_ms": round(warmup_ms, 3),
            "source_annotation_count": sum(len(sample.source_entities) for sample in samples),
            "annotation_statuses": annotation_statuses,
            "reannotation_manifest": reannotation_manifest,
            "sample_enabled_entities": sorted({tuple(sample.enabled_entities) for sample in samples
                                                if sample.enabled_entities is not None}),
            "peak_rss_mb": _peak_rss_mb(),
            "python": platform.python_version(),
            "packages": {
                name: _package_version(name)
                for name in ("kotoba-veil", "ginza", "ja-ginza", "presidio-analyzer")
            },
        }
        return report
    finally:
        if previous_disable_nlp is None:
            os.environ.pop("KOTOBA_VEIL_DISABLE_NLP", None)
        else:
            os.environ["KOTOBA_VEIL_DISABLE_NLP"] = previous_disable_nlp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Kotoba Veil PII detection")
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="Gold JSONL dataset",
    )
    parser.add_argument("--output", help="Write the JSON report to this path")
    parser.add_argument("--warmup", type=int, default=0,
                        help="Run this many extra, unmeasured samples before the full dataset")
    parser.add_argument("--allow-draft", action="store_true",
                        help="Explicitly allow provisional Codex annotations before human review")
    parser.add_argument(
        "--disable-nlp",
        action="store_true",
        help="Evaluate deterministic rules without GiNZA",
    )
    parser.add_argument(
        "--entities",
        nargs="+",
        help="Restrict detection to these entity types",
    )
    parser.add_argument("--fail-under-recall", type=float)
    parser.add_argument("--fail-under-precision", type=float)
    parser.add_argument("--fail-under-zero-miss-rate", type=float)
    parser.add_argument("--fail-under-core-zero-miss-rate", type=float)
    parser.add_argument("--fail-under-hard-negative-pass-rate", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_benchmark(
        args.dataset,
        disable_nlp=args.disable_nlp,
        entities=args.entities,
        warmup=args.warmup,
        allow_draft=args.allow_draft,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote benchmark report to {output_path}")
    else:
        print(rendered, end="")

    exact = report["exact"]
    assert isinstance(exact, dict)
    micro = exact["micro"]
    assert isinstance(micro, dict)
    recall = float(micro["recall"])
    zero_miss_rate = float(exact["document_zero_miss_rate"])
    slices = report["slices"]
    assert isinstance(slices, dict)
    core = slices.get("core")
    hard_negative = slices.get("hard_negative")
    core_zero_miss_rate = None
    hard_negative_pass_rate = None
    if isinstance(core, dict):
        core_exact = core["exact"]
        assert isinstance(core_exact, dict)
        core_zero_miss_rate = float(core_exact["document_zero_miss_rate"])
    if isinstance(hard_negative, dict):
        hard_negative_exact = hard_negative["exact"]
        assert isinstance(hard_negative_exact, dict)
        hard_negative_pass_rate = float(
            hard_negative_exact["document_no_false_positive_rate"]
        )
    failed = False
    precision = float(micro["precision"])
    if args.fail_under_precision is not None and precision < args.fail_under_precision:
        print(
            f"Exact precision {precision:.6f} is below {args.fail_under_precision:.6f}",
            file=sys.stderr,
        )
        failed = True
    if args.fail_under_recall is not None and recall < args.fail_under_recall:
        print(
            f"Exact recall {recall:.6f} is below {args.fail_under_recall:.6f}",
            file=sys.stderr,
        )
        failed = True
    if (
        args.fail_under_zero_miss_rate is not None
        and zero_miss_rate < args.fail_under_zero_miss_rate
    ):
        print(
            f"Zero-miss rate {zero_miss_rate:.6f} is below "
            f"{args.fail_under_zero_miss_rate:.6f}",
            file=sys.stderr,
        )
        failed = True
    if (
        args.fail_under_core_zero_miss_rate is not None
        and (
            core_zero_miss_rate is None
            or core_zero_miss_rate < args.fail_under_core_zero_miss_rate
        )
    ):
        print(
            f"Core zero-miss rate {core_zero_miss_rate or 0.0:.6f} is below "
            f"{args.fail_under_core_zero_miss_rate:.6f}",
            file=sys.stderr,
        )
        failed = True
    if (
        args.fail_under_hard_negative_pass_rate is not None
        and (
            hard_negative_pass_rate is None
            or hard_negative_pass_rate < args.fail_under_hard_negative_pass_rate
        )
    ):
        print(
            f"Hard-negative pass rate {hard_negative_pass_rate or 0.0:.6f} is below "
            f"{args.fail_under_hard_negative_pass_rate:.6f}",
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
