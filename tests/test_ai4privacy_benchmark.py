import json

import pytest

from benchmarks import import_ai4privacy as adapter
from benchmarks.metrics import SampleResult, Span, evaluate
from benchmarks.schema import BenchmarkSample, load_jsonl


def row(text="山田 太郎 80", *, language="ja", split="validation", uid=1):
    return {
        "source_text": text, "language": language, "split": split, "uid": uid, "region": "JP",
        "privacy_mask": [
            {"label": "SURNAME", "start": 0, "end": 2, "value": text[:2], "label_index": 1},
            {"label": "GIVENNAME", "start": 3, "end": 5, "value": text[3:5], "label_index": 1},
            {"label": "AGE", "start": 6, "end": 8, "value": text[6:8], "label_index": 1},
        ],
    }


def test_adapter_merges_name_pair_and_keeps_all_source_annotations():
    original = row()
    before = json.dumps(original)
    sample = adapter.convert_row(original)
    assert sample.split == "validation" and sample.language == "ja"
    assert [(s.entity_type, s.text) for s in sample.entities] == [("PERSON", "山田 太郎")]
    assert [s.entity_type for s in sample.source_entities] == ["SURNAME", "GIVENNAME", "AGE"]
    assert sample.dictionary_terms == []
    assert "PERSONAL_ID" not in sample.enabled_entities
    assert json.dumps(original) == before
    separate = adapter.convert_row(original, name_spans="separate")
    assert [s.text for s in separate.entities] == ["山田", "太郎"]


@pytest.mark.parametrize("gap", ["、", "\n"])
def test_names_do_not_merge_across_punctuation_or_lines(gap):
    assert len(adapter.convert_row(row(f"山田{gap}太郎 80")).entities) == 2


def test_names_require_matching_source_identity():
    original = row()
    original["privacy_mask"][1]["label_index"] = 2
    assert len(adapter.convert_row(original).entities) == 2


def test_unicode_offsets_are_used_without_normalization():
    original = {**row(), "source_text": "😀太郎", "privacy_mask": [
        {"label": "GIVENNAME", "start": 1, "end": 3, "value": "太郎", "label_index": 1}]}
    sample = adapter.convert_row(original)
    assert sample.entities[0].start == 1
    assert sample.text == "😀太郎"


def test_invalid_unmapped_span_and_unknown_label_fail():
    original = row()
    original["privacy_mask"][2]["value"] = "81"
    with pytest.raises(ValueError, match="span text mismatch"):
        adapter.convert_row(original)
    original = row()
    original["privacy_mask"][2]["label"] = "NEW_LABEL"
    with pytest.raises(ValueError, match="Unknown source label"):
        adapter.convert_row(original)


def cache_sources(tmp_path, monkeypatch):
    contents = {"README.md": b"test-only fixture"}
    for split in ("train", "validation"):
        rows = [row(language=lang, split=split, uid=i + (10 if split == "validation" else 0))
                for i, lang in enumerate(("ja", "en"))]
        contents[f"data/{split}.jsonl"] = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
    raw = tmp_path / "raw"
    raw.mkdir()
    for name, content in contents.items():
        (raw / name.rsplit("/", 1)[-1]).write_bytes(content)
    monkeypatch.setattr(adapter, "FILES", {n: adapter.sha256(c) for n, c in contents.items()})
    monkeypatch.setattr(adapter, "urlopen", lambda *a, **kw: pytest.fail("offline test accessed network"))


def test_prepare_preserves_splits_filters_language_and_reproduces_output(tmp_path, monkeypatch):
    cache_sources(tmp_path, monkeypatch)
    first = adapter.prepare(tmp_path, offline=True)
    second = adapter.prepare(tmp_path, offline=True)
    assert first["outputs"] == second["outputs"]
    for split in ("train", "validation"):
        samples = load_jsonl(tmp_path / "converted-merged" / f"ja-{split}.jsonl")
        assert len(samples) == 1 and samples[0].split == split
    all_languages = adapter.prepare(tmp_path, offline=True, languages=["all"])
    assert all_languages["outputs"]["all-validation.jsonl"]["language_counts"] == {"ja": 1, "en": 1}
    with pytest.raises(ValueError, match="Unknown languages"):
        adapter.prepare(tmp_path, offline=True, languages=["ja", "zz"])
    (tmp_path / "raw" / "train.jsonl").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        adapter.prepare(tmp_path, offline=True)


def test_source_coverage_keeps_unmapped_misses_and_penalizes_excess_masking():
    result = SampleResult(sample_id="coverage", text_length=10,
                          gold=(Span("PERSON", 0, 4, "name"),),
                          predicted=(Span("PERSON", 0, 3, "nam"), Span("PERSON", 8, 10, "xx")),
                          source_gold=(Span("SURNAME", 0, 2, "na"), Span("GIVENNAME", 2, 4, "me"),
                                       Span("AGE", 5, 7, "80")), latency_ms=1)
    coverage = evaluate([result])["source_pii_coverage"]
    assert coverage["character_recall"] == 0.5
    assert coverage["character_precision"] == 0.6
    assert coverage["fully_covered_annotations"] == 1
    assert coverage["per_source_label"]["AGE"]["covered_characters"] == 0


def test_coverage_does_not_double_count_overlapping_annotations_or_predictions():
    result = SampleResult("overlap", 4, (), (Span("PERSON", 0, 4, "name"),
                                           Span("LOCATION", 0, 2, "na")), 1,
                          source_gold=(Span("GIVENNAME", 0, 4, "name"), Span("SURNAME", 0, 2, "na")))
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
    samples = [adapter.convert_row(row(uid=1)), adapter.convert_row(row(language="en", uid=2))]
    dataset.write_text("\n".join(s.model_dump_json() for s in samples))
    report = run.run_benchmark(dataset, warmup=10)
    assert len(calls) == 4 and report["sample_count"] == 2
    assert report["metadata"]["warmup_samples"] == 2
    assert report["metadata"]["language"] == "multilingual"
    assert set(report["by_language"]) == {"ja", "en"}
    assert report["source_pii_coverage"]["source_annotations"] == 6
    with pytest.raises(ValueError, match="nonnegative"):
        run.run_benchmark(dataset, warmup=-1)


def test_source_spans_and_language_are_validated():
    with pytest.raises(ValueError):
        BenchmarkSample(id="invalid", source="licensed", text="x", language="日本語")
