"""The generic reviewer must preserve provenance and never lose a dev split."""
import json

import pytest
from fastapi.testclient import TestClient

from benchmarks import reannotation as ann
from benchmarks.review_reannotations import create_app
from benchmarks.schema import load_jsonl


@pytest.fixture
def generated_corpus(tmp_path):
    text = "🙂山田様、2027年1月5日（火）に伺います。"
    snapshot = {"id": "generated-1", "split": "dev", "text": text}
    (tmp_path / "texts-only.jsonl").write_text(json.dumps(snapshot, ensure_ascii=False)+"\n")
    (tmp_path / "policy.md").write_text("利用者生成データの注釈方針")
    row = dict(snapshot, text_sha256=ann.digest(text.encode()), keep=True,
               language_reason="日本語", entities=ann.materialize(text, [["PERSON", "山田"]]),
               issues=["日付の範囲を確認"], notes=[], codex_reviewed=True, human_reviewed=False)
    state = {"status": "codex_draft", "revision": 0, "policy_accepted": False,
             "source_snapshot_sha256": ann.digest((tmp_path / "texts-only.jsonl").read_bytes()),
             "source_rows": 1, "rows": [row],
             "dataset": {"id": "user-generated", "name": "利用者生成データ", "source": "synthetic",
                         "license": "未指定", "attribution": "利用者提供", "notes": "本文から注釈", "splits": ["dev"]}}
    ann.atomic_json(tmp_path / "codex-draft.json", state)
    return tmp_path, state


def test_generated_review_exports_dev_with_own_provenance(generated_corpus):
    root, state = generated_corpus
    client = TestClient(create_app(root), base_url="http://localhost", headers={"Origin": "http://localhost"})
    assert client.get("/api/policy").json()["text"] == "利用者生成データの注釈方針"
    assert client.get("/api/state").json()["dataset"]["splits"] == ["dev"]
    assert client.post("/api/export", json={"revision": 0, "finalize": True}).status_code == 409
    result = client.post("/api/export", json={"revision": 0}).json()
    assert set(result["downloads"]) == {"ja-dev.jsonl", "manifest.json"}
    assert result["manifest"]["license"] == "未指定"
    assert result["manifest"]["dataset"] == "利用者生成データ"
    sample = load_jsonl(root / "exports/revision-0-codex_draft/ja-dev.jsonl")[0]
    assert sample.split == "dev" and sample.source == "synthetic"
    assert sample.text == state["rows"][0]["text"]
    assert sample.entities[0].start == 1
    assert sample.tags == ["user-generated", "text-annotated", "codex_draft"]
    assert not sample.dictionary_terms
    assert client.get(result["downloads"]["ja-dev.jsonl"]).status_code == 200
    assert "ai4privacy" not in json.dumps(result, ensure_ascii=False)


def test_custom_metadata_cannot_silently_drop_rows_or_change_export(generated_corpus):
    root, state = generated_corpus
    state["dataset"]["splits"] = ["train"]
    with pytest.raises(ValueError, match="omit retained"):
        ann.export_dataset(state, root)
    state["dataset"]["splits"] = ["dev"]
    ann.export_dataset(state, root)
    state["dataset"]["attribution"] = "different source"
    with pytest.raises(ValueError, match="different content"):
        ann.export_dataset(state, root)
    del state["dataset"]["license"]
    with pytest.raises(ValueError, match="Incomplete dataset"):
        ann.export_dataset(state, root)


def test_custom_draft_requires_manifest_and_explicit_benchmark_opt_in(generated_corpus):
    from benchmarks.run import run_benchmark
    root, state = generated_corpus
    ann.export_dataset(state, root)
    path = root / "exports/revision-0-codex_draft/ja-dev.jsonl"
    with pytest.raises(ValueError, match="allow-draft"):
        run_benchmark(path, disable_nlp=True)
    report = run_benchmark(path, disable_nlp=True, allow_draft=True)
    assert report["metadata"]["reannotation_manifest"]["dataset"] == "利用者生成データ"
    path.write_text(path.read_text().replace("山田", "田中"))
    with pytest.raises(ValueError, match="checksum"):
        run_benchmark(path, disable_nlp=True, allow_draft=True)


def test_saved_human_edits_survive_restart_and_finalize(generated_corpus):
    root, _ = generated_corpus
    client = TestClient(create_app(root), base_url="http://localhost", headers={"Origin": "http://localhost"})
    row = client.get("/api/rows/generated-1").json()["row"]
    row["entities"] = ann.materialize(row["text"], [["PERSON", "山田"], ["DATE_TIME", "2027年1月5日（火）"]])
    edit = {k: row[k] for k in ("keep", "entities")}
    edit.update(revision=0, human_reviewed=True, review_note="日付も対象として確認")
    assert client.put("/api/rows/generated-1", json=edit).status_code == 200
    client = TestClient(create_app(root), base_url="http://localhost", headers={"Origin": "http://localhost"})
    assert len(client.get("/api/rows/generated-1").json()["row"]["entities"]) == 2
    assert client.put("/api/policy", json={"revision": 1, "accepted": True}).status_code == 200
    response = client.post("/api/export", json={"revision": 2, "finalize": True})
    assert response.status_code == 200
    assert response.json()["manifest"]["all_rows_human_reviewed"]
    saved = client.get(response.json()["downloads"]["ja-dev.jsonl"]).text
    assert json.loads(saved)["tags"][-1] == "review_complete"
