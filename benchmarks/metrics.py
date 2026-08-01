from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil
from statistics import mean, median
from typing import Iterable


@dataclass(frozen=True)
class Span:
    entity_type: str
    start: int
    end: int
    text: str
    source: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class SampleResult:
    sample_id: str
    text_length: int
    gold: tuple[Span, ...]
    predicted: tuple[Span, ...]
    latency_ms: float


def _score(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def _exact_matches(gold: tuple[Span, ...], predicted: tuple[Span, ...]) -> set[tuple[int, int]]:
    gold_keys = {(item.entity_type, item.start, item.end): index for index, item in enumerate(gold)}
    matches: set[tuple[int, int]] = set()
    matched_gold: set[int] = set()
    for predicted_index, item in enumerate(predicted):
        gold_index = gold_keys.get((item.entity_type, item.start, item.end))
        if gold_index is not None and gold_index not in matched_gold:
            matches.add((gold_index, predicted_index))
            matched_gold.add(gold_index)
    return matches


def _overlap_matches(gold: tuple[Span, ...], predicted: tuple[Span, ...]) -> set[tuple[int, int]]:
    candidates: list[tuple[float, int, int]] = []
    for gold_index, expected in enumerate(gold):
        for predicted_index, actual in enumerate(predicted):
            if expected.entity_type != actual.entity_type:
                continue
            overlap = max(0, min(expected.end, actual.end) - max(expected.start, actual.start))
            if not overlap:
                continue
            union = max(expected.end, actual.end) - min(expected.start, actual.start)
            candidates.append((overlap / union, gold_index, predicted_index))
    matches: set[tuple[int, int]] = set()
    used_gold: set[int] = set()
    used_predicted: set[int] = set()
    for _, gold_index, predicted_index in sorted(candidates, reverse=True):
        if gold_index in used_gold or predicted_index in used_predicted:
            continue
        matches.add((gold_index, predicted_index))
        used_gold.add(gold_index)
        used_predicted.add(predicted_index)
    return matches


def _aggregate(
    results: list[SampleResult],
    match_mode: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    errors: list[dict[str, object]] = []
    documents_without_misses = 0

    for result in results:
        matches = (
            _exact_matches(result.gold, result.predicted)
            if match_mode == "exact"
            else _overlap_matches(result.gold, result.predicted)
        )
        matched_gold = {gold_index for gold_index, _ in matches}
        matched_predicted = {predicted_index for _, predicted_index in matches}

        for gold_index, predicted_index in matches:
            entity_type = result.gold[gold_index].entity_type
            counts[entity_type][0] += 1
        for predicted_index, item in enumerate(result.predicted):
            if predicted_index not in matched_predicted:
                counts[item.entity_type][1] += 1
        for gold_index, item in enumerate(result.gold):
            if gold_index not in matched_gold:
                counts[item.entity_type][2] += 1

        misses = [item for index, item in enumerate(result.gold) if index not in matched_gold]
        false_positives = [
            item for index, item in enumerate(result.predicted) if index not in matched_predicted
        ]
        if not misses:
            documents_without_misses += 1
        if misses or false_positives:
            errors.append(
                {
                    "sample_id": result.sample_id,
                    "misses": [item.__dict__ for item in misses],
                    "false_positives": [item.__dict__ for item in false_positives],
                }
            )

    per_entity = {
        entity_type: _score(*values)
        for entity_type, values in sorted(counts.items())
    }
    totals = [sum(values[index] for values in counts.values()) for index in range(3)]
    f1_values = [float(metrics["f1"]) for metrics in per_entity.values()]
    total_characters = sum(result.text_length for result in results)
    summary: dict[str, object] = {
        "micro": _score(*totals),
        "macro_f1": round(mean(f1_values), 6) if f1_values else 0.0,
        "document_zero_miss_rate": round(
            documents_without_misses / len(results), 6
        ),
        "false_positives_per_1000_characters": round(
            totals[1] * 1000 / total_characters, 6
        )
        if total_characters
        else 0.0,
        "per_entity": per_entity,
    }
    return summary, errors


def evaluate(results: Iterable[SampleResult]) -> dict[str, object]:
    materialized = list(results)
    if not materialized:
        raise ValueError("no benchmark results")
    exact, exact_errors = _aggregate(materialized, "exact")
    overlap, overlap_errors = _aggregate(materialized, "overlap")
    latencies = sorted(item.latency_ms for item in materialized)
    p95_index = max(0, min(len(latencies) - 1, ceil(len(latencies) * 0.95) - 1))
    total_latency_ms = sum(latencies)
    character_count = sum(item.text_length for item in materialized)
    return {
        "sample_count": len(materialized),
        "character_count": character_count,
        "gold_entity_count": sum(len(item.gold) for item in materialized),
        "predicted_entity_count": sum(len(item.predicted) for item in materialized),
        "exact": exact,
        "overlap": overlap,
        "latency_ms": {
            "mean": round(mean(latencies), 3),
            "median": round(median(latencies), 3),
            "p95": round(latencies[p95_index], 3),
            "max": round(max(latencies), 3),
            "total": round(total_latency_ms, 3),
            "characters_per_second": round(
                character_count / (total_latency_ms / 1000), 3
            )
            if total_latency_ms
            else 0.0,
        },
        "errors": {
            "exact": exact_errors,
            "overlap": overlap_errors,
        },
    }
