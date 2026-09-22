import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from benchmarks import reannotation as ann
from benchmarks.review_reannotations import ReviewStore, create_app
from benchmarks.schema import load_jsonl


@pytest.fixture
def corpus(tmp_path):
    rows = [ann.text_record("1", "train", "🙂担当は山田です。注文番号はAB123456。"),
            ann.text_record("2", "validation", "佐藤の年齢は20歳。"),
            ann.text_record("3", "train", "This is an English document.")]
    (tmp_path / "texts-only.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in rows))
    (tmp_path / "annotations").mkdir()
    annotations = [{"id": "1", "entities": [["PERSON", "山田"], ["ORDER_ID", "AB123456"]],
                    "issues": ["注文番号の扱いを確認"]},
                   {"id": "2", "entities": [["PERSON", "佐藤"], ["AGE", "20歳"]]}]
    (tmp_path / "annotations" / "test.json").write_text(json.dumps(annotations, ensure_ascii=False))
    return tmp_path


def test_complete_reannotation_preserves_text_and_extra_labels(corpus):
    state = ann.build(corpus, complete=True)
    assert state["retained_rows"] == 2
    assert state["codex_reviewed_rows"] == 2
    assert len(state["rows"]) == 3
    assert state["rows"][0]["entities"][0]["start"] == 4  # Emoji counts as one codepoint.
    result = ann.export_dataset(state, corpus)
    assert result["unresolved_rows"] == 1
    path = corpus / "exports/revision-0-codex_draft/ja-train.jsonl"
    sample = load_jsonl(path)[0]
    assert sample.text == state["rows"][0]["text"]
    assert [e.entity_type for e in sample.entities] == ["PERSON"]
    assert [e.entity_type for e in sample.source_entities] == ["PERSON", "ORDER_ID"]
    assert not sample.dictionary_terms and sample.enabled_entities is None
    assert len(load_jsonl(path)) == 1
    assert ann.export_dataset(state, corpus) == result
    path.write_text("tampered")
    with pytest.raises(ValueError, match="modified"):
        ann.export_dataset(state, corpus)


def test_missing_candidate_and_duplicates_rejected(corpus):
    p = corpus / "annotations/test.json"
    rows = json.loads(p.read_text())
    p.write_text(json.dumps(rows[:1]))
    with pytest.raises(ValueError, match="Unannotated Japanese"):
        ann.build(corpus, complete=True)
    p.write_text(json.dumps(rows + rows[:1]))
    with pytest.raises(ValueError, match="duplicate"):
        ann.build(corpus)
    p.write_text(json.dumps(rows + [{"id": "unknown", "entities": []}]))
    with pytest.raises(ValueError, match="Unknown"):
        ann.build(corpus)


def test_repeated_literal_and_overlap_require_explicit_decision():
    with pytest.raises(ValueError, match="uniquely"):
        ann.materialize("佐藤と佐藤", [["PERSON", "佐藤"]])
    assert ann.materialize("佐藤と佐藤", [["PERSON", "佐藤", 2]])[0]["start"] == 3
    with pytest.raises(ValueError, match="Overlapping"):
        ann.materialize("山田太郎", [["PERSON", "山田太郎"], ["PERSON", "太郎"]])
    with pytest.raises(ValueError):
        ann.validate_entities("🙂山田", [{"entity_type": "PERSON", "start": 2, "end": 4, "text": "山田"}])


def test_source_projection_ignores_labels_and_checks_cache(tmp_path, monkeypatch):
    from benchmarks import import_ai4privacy as importer
    raw = tmp_path / "raw"
    raw.mkdir()
    for i, split in enumerate(("train", "validation")):
        data = (json.dumps({"uid": i, "source_text": "社員番号: A", "language": "wrong",
                            "privacy_mask": [{"nonsense": "not read"}]}) + "\n").encode()
        (raw / f"{split}.jsonl").write_bytes(data)
        monkeypatch.setitem(importer.FILES, f"data/{split}.jsonl", ann.digest(data))
    root = tmp_path / "output"
    ann.prepare_source(root, raw)
    records = [json.loads(line) for line in (root / "texts-only.jsonl").read_text().splitlines()]
    assert set(records[0]) == {"id", "text", "split", "kana", "han", "hangul"}
    assert records[0]["text"] == "社員番号: A"
    (raw / "train.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        ann.prepare_source(root, raw)


def test_han_only_uncertain_language_cannot_silently_disappear():
    with pytest.raises(ValueError, match="Unreviewed Han-only"):
        ann.excluded_reason(ann.text_record("x", "train", "社員番号: A"))
    assert "中国語" in ann.excluded_reason(ann.text_record("x", "train", "请提供信息"))


def client_for(corpus):
    ann.build(corpus, complete=True)
    return TestClient(create_app(corpus), base_url="http://127.0.0.1:8012", headers={"Origin": "http://127.0.0.1:8012"})


def payload(row, revision=0):
    return {"revision": revision, "keep": row["keep"], "human_reviewed": True,
            "entities": row["entities"], "review_note": "本文で確認"}


def test_review_save_conflict_and_finalize_gate(corpus):
    client = client_for(corpus)
    first = client.get("/api/rows/1").json()["row"]
    assert client.post("/api/export", json={"revision": 0, "finalize": True}).status_code == 409
    edit = payload(first)
    edit["entities"][1]["entity_type"] = "IDENTIFIER"
    saved = client.put("/api/rows/1", json=edit)
    assert saved.status_code == 200
    assert saved.json()["summary"]["unresolved_rows"] == 0
    assert client.put("/api/rows/1", json=edit).status_code == 409
    assert client.post("/api/export", json={"revision": 1, "finalize": True}).status_code == 409
    assert client.put("/api/policy", json={"revision": 1, "accepted": True}).status_code == 200
    exported = client.post("/api/export", json={"revision": 2, "finalize": True})
    assert exported.status_code == 200
    data = exported.json()
    assert data["manifest"]["status"] == "review_complete"
    assert not data["manifest"]["all_rows_human_reviewed"]
    output = client.get(data["downloads"]["ja-train.jsonl"])
    assert json.loads(output.text)["source_entities"][1]["entity_type"] == "IDENTIFIER"
    # Persisted review can be reopened, and any later edit invalidates completion.
    store = ReviewStore(corpus)
    assert store.state["history"][0]["before"]["entities"][1]["entity_type"] == "ORDER_ID"
    edit["revision"] = 3
    assert client.put("/api/rows/1", json=edit).json()["summary"]["status"] == "codex_draft"


def test_invalid_text_overlap_and_original_mutation_rejected(corpus):
    client = client_for(corpus)
    row = client.get("/api/rows/1").json()["row"]
    edit = payload(row)
    edit["entities"][0]["text"] = "違う文字"
    assert client.put("/api/rows/1", json=edit).status_code == 422
    edit = payload(client.get("/api/rows/1").json()["row"])
    edit["entities"].append(edit["entities"][0])
    assert client.put("/api/rows/1", json=edit).status_code == 422
    edit = payload(row) | {"text": "原文の書き換え"}
    assert client.put("/api/rows/1", json=edit).status_code == 422
    assert client.get("/api/state").json()["revision"] == 0


def test_exclusion_restoration_and_cross_origin(corpus):
    client = client_for(corpus)
    row = client.get("/api/rows/3").json()["row"]
    edit = payload(row) | {"keep": True, "human_reviewed": False}
    assert client.put("/api/rows/3", json=edit).status_code == 422
    edit["human_reviewed"] = True
    assert client.put("/api/rows/3", json=edit, headers={"Origin": "https://other.example"}).status_code == 403
    assert client.put("/api/rows/3", json=edit).status_code == 200
    assert client.get("/api/rows?view=excluded").json()["total"] == 0
    assert client.get("/api/rows?view=all&q=🙂").json()["total"] == 1
    assert client.get("/api/state", headers={"Host": "other.example"}).status_code == 403


def test_initial_export_and_stale_draft_never_overwrite_human_work(corpus):
    state = ann.build(corpus, complete=True)
    ann.export_dataset(state, corpus)
    client = TestClient(create_app(corpus), base_url="http://localhost", headers={"Origin": "http://localhost"})
    assert client.post("/api/export", json={"revision": 0}).status_code == 200
    path = corpus / "codex-draft.json"
    changed = json.loads(path.read_text())
    changed["rows"][0]["issues"].append("追加")
    ann.atomic_json(path, changed)
    with pytest.raises(ValueError, match="human review was preserved"):
        ReviewStore(corpus)
    assert (corpus / "review.json").exists()


def test_new_unconfirmed_edits_enter_review_queue(corpus):
    client = client_for(corpus)
    row = client.get("/api/rows/2").json()["row"]
    edit = payload(row) | {"human_reviewed": False, "review_note": "再検討"}
    saved = client.put("/api/rows/2", json=edit)
    assert saved.status_code == 200
    assert saved.json()["summary"]["unresolved_rows"] == 2
    assert client.get("/api/rows?view=unresolved&q=佐藤").json()["total"] == 1


def test_draft_benchmark_requires_opt_in_and_manifest(corpus):
    from benchmarks.run import run_benchmark
    state = ann.build(corpus, complete=True)
    ann.export_dataset(state, corpus)
    path = corpus / "exports/revision-0-codex_draft/ja-validation.jsonl"
    with pytest.raises(ValueError, match="allow-draft"):
        run_benchmark(path, disable_nlp=True)
    report = run_benchmark(path, disable_nlp=True, allow_draft=True)
    assert report["metadata"]["annotation_statuses"] == ["codex_draft"]
    assert len(report["metadata"]["detector_sha256"]) == 64
    assert report["metadata"]["reannotation_manifest"]["unresolved_rows"] == 1
    path.write_text(path.read_text().replace("20歳", "21歳"))
    with pytest.raises(ValueError, match="checksum"):
        run_benchmark(path, disable_nlp=True, allow_draft=True)
