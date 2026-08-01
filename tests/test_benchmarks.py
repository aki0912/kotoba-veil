from pathlib import Path

from benchmarks.metrics import SampleResult, Span, evaluate
from benchmarks.run import run_benchmark
from benchmarks.schema import load_jsonl


DATASET = Path("benchmarks/datasets/smoke.jsonl")


def test_smoke_dataset_has_valid_spans_and_unique_ids() -> None:
    samples = load_jsonl(DATASET)

    assert len(samples) == 10
    assert len({sample.id for sample in samples}) == len(samples)
    assert all(
        sample.text[entity.start : entity.end] == entity.text
        for sample in samples
        for entity in sample.entities
    )


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


def test_rule_only_benchmark_runs_against_application_engine() -> None:
    report = run_benchmark(DATASET, disable_nlp=True)

    assert report["sample_count"] == 10
    assert report["gold_entity_count"] == 17
    assert report["metadata"]["nlp_disabled"] is True
    assert report["exact"]["micro"]["recall"] > 0
