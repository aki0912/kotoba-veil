import hashlib
import json

import pytest
from collections import Counter
from pathlib import Path

from benchmarks.generate_synthetic import generate
from benchmarks.metrics import SampleResult, Span, evaluate
from benchmarks.run import run_benchmark
from benchmarks.schema import BenchmarkSample, load_jsonl


DATASET = Path("benchmarks/datasets/smoke.jsonl")
FULL_DATASET = Path("benchmarks/datasets/synthetic-v1.jsonl")
MANIFEST = Path("benchmarks/datasets/synthetic-v1.manifest.json")


def test_smoke_dataset_has_valid_spans_and_unique_ids() -> None:
    samples = load_jsonl(DATASET)

    assert len(samples) == 10
    assert len({sample.id for sample in samples}) == len(samples)
    assert all(
        sample.text[entity.start : entity.end] == entity.text
        for sample in samples
        for entity in sample.entities
    )


def test_full_dataset_is_balanced_and_reproducible() -> None:
    samples = load_jsonl(FULL_DATASET)
    entity_counts = Counter(
        entity.entity_type for sample in samples for entity in sample.entities
    )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    generated = "".join(
        json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n"
        for sample in generate(manifest["seed"])
    )

    assert len(samples) == 1000
    assert sum(not sample.entities for sample in samples) == 150
    assert sum("hard-negative" in sample.tags for sample in samples) == 150
    assert sum(len(sample.entities) for sample in samples) == 1050
    assert set(entity_counts.values()) == {70}
    assert all("単独ケース" not in sample.text for sample in samples)
    assert all("複合ケース" not in sample.text for sample in samples)
    assert FULL_DATASET.read_text(encoding="utf-8") == generated
    assert hashlib.sha256(generated.encode("utf-8")).hexdigest() == manifest["sha256"]


def test_metrics_separate_exact_and_overlap_matches() -> None:
    result = SampleResult(
        sample_id="partial",
        text_length=10,
        gold=(Span("ADDRESS", 0, 8, "東京都千代田"),),
        predicted=(Span("ADDRESS", 0, 3, "東京都"),),
        latency_ms=1.0,
    )

    report = evaluate([result])

    assert report["exact"]["micro"]["recall"] == 0.0
    assert report["overlap"]["micro"]["recall"] == 1.0
    assert report["exact"]["document_zero_miss_rate"] == 0.0


def test_duplicate_prediction_cannot_match_one_gold_span_twice() -> None:
    gold = Span("EMAIL_ADDRESS", 0, 5, "a@b.c")
    result = SampleResult(
        sample_id="duplicate",
        text_length=5,
        gold=(gold,),
        predicted=(gold, gold),
        latency_ms=1.0,
    )

    report = evaluate([result])

    assert report["exact"]["micro"]["true_positives"] == 1
    assert report["exact"]["micro"]["false_positives"] == 1


def test_hard_negative_metrics_are_reported_separately() -> None:
    result = SampleResult(
        sample_id="hard-negative",
        text_length=13,
        gold=(),
        predicted=(Span("PHONE_NUMBER", 0, 13, "090-1000-2000"),),
        latency_ms=1.0,
        tags=("hard-negative",),
    )

    report = evaluate([result])
    hard_negative = report["slices"]["hard_negative"]

    assert hard_negative["exact"]["micro"]["false_positives"] == 1
    assert hard_negative["exact"]["document_no_false_positive_rate"] == 0.0


def test_rule_only_benchmark_runs_against_application_engine() -> None:
    report = run_benchmark(DATASET, disable_nlp=True)

    assert report["sample_count"] == 10
    assert report["gold_entity_count"] == 17
    assert report["metadata"]["nlp_disabled"] is True
    assert report["exact"]["micro"]["recall"] > 0


def test_benchmark_cli_rejects_false_positives_with_precision_gate(tmp_path, monkeypatch):
    from benchmarks import run
    report = {
        'exact': {'micro': {'precision': 0.5, 'recall': 1.0}, 'document_zero_miss_rate': 1.0},
        'slices': {},
    }
    monkeypatch.setattr(run, 'run_benchmark', lambda *args, **kwargs: report)
    args = ['--output', str(tmp_path / 'report.json'), '--fail-under-recall', '1.0']
    assert run.main([*args, '--fail-under-precision', '1.0']) == 1
    assert run.main([*args, '--fail-under-precision', '0.5']) == 0


def test_source_coverage_keeps_unmapped_misses_and_penalizes_excess_masking():
    result = SampleResult(sample_id="coverage", text_length=10,
                          gold=(Span("PERSON", 0, 4, "name"),),
                          predicted=(Span("PERSON", 0, 3, "nam"), Span("PERSON", 8, 10, "xx")),
                          source_gold=(Span("PERSON", 0, 2, "na"), Span("LOCATION", 2, 4, "me"),
                                       Span("AGE", 5, 7, "80")), latency_ms=1)
    coverage = evaluate([result])["source_pii_coverage"]
    assert coverage["character_recall"] == 0.5
    assert coverage["character_precision"] == 0.6
    assert coverage["fully_covered_annotations"] == 1
    assert coverage["per_source_label"]["AGE"]["covered_characters"] == 0


def test_coverage_does_not_double_count_overlapping_annotations_or_predictions():
    result = SampleResult("overlap", 4, (), (Span("PERSON", 0, 4, "name"),
                                           Span("LOCATION", 0, 2, "na")), 1,
                          source_gold=(Span("LOCATION", 0, 4, "name"), Span("PERSON", 0, 2, "na")))
    coverage = evaluate([result])["source_pii_coverage"]
    assert coverage["source_characters"] == coverage["masked_characters"] == 4
    assert coverage["character_recall"] == coverage["character_precision"] == 1


def test_runner_warmup_keeps_full_dataset_and_reports_actual_languages(tmp_path, monkeypatch):
    from benchmarks import run
    calls = []

    class Engine:
        nlp_available = True

        def analyze(self, text, entities, dictionary, block_id):
            calls.append(block_id)
            return []

    monkeypatch.setattr(run, "JapanesePiiEngine", Engine)
    dataset = tmp_path / "samples.jsonl"
    samples = [BenchmarkSample(id=str(i), language=language, source="synthetic", text=text,
                               entities=[{"entity_type": "PERSON", "start": 0, "end": len(text), "text": text}],
                               source_entities=[{"entity_type": "PERSON", "start": 0, "end": len(text), "text": text}])
               for i, (language, text) in enumerate([("ja", "太郎"), ("en", "John")]) ]
    dataset.write_text("\n".join(s.model_dump_json() for s in samples))
    report = run.run_benchmark(dataset, warmup=10)
    assert len(calls) == 4 and report["sample_count"] == 2
    assert report["metadata"]["warmup_samples"] == 2
    assert report["metadata"]["language"] == "multilingual"
    assert set(report["by_language"]) == {"ja", "en"}
    assert report["source_pii_coverage"]["source_annotations"] == 2
    with pytest.raises(ValueError, match="nonnegative"):
        run.run_benchmark(dataset, warmup=-1)


def test_source_spans_and_language_are_validated():
    with pytest.raises(ValueError):
        BenchmarkSample(id="invalid", source="licensed", text="x", language="日本語")
