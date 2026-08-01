from fastapi.testclient import TestClient

import app.main as main
from app.database import DictionaryStore
from app.documents import DocumentSessionStore


def test_text_api_dictionary_and_static_app(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "dictionary_store", DictionaryStore(tmp_path / "db.sqlite3"))
    monkeypatch.setattr(main, "session_store", DocumentSessionStore(tmp_path / "sessions"))

    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["local_only"] is True

        created = client.post(
            "/api/dictionary",
            json={"term": "極秘案件", "entity_type": "CUSTOM", "note": "test"},
        )
        assert created.status_code == 201

        analyzed = client.post(
            "/api/analyze",
            json={"text": "極秘案件の連絡先はtaro@example.jp", "entities": ["CUSTOM", "EMAIL_ADDRESS"]},
        )
        assert analyzed.status_code == 200
        findings = analyzed.json()["findings"]
        assert {item["entity_type"] for item in findings} == {"CUSTOM", "EMAIL_ADDRESS"}

        masked = client.post(
            "/api/mask",
            json={
                "text": analyzed.json()["text"],
                "findings": findings,
                "accepted_ids": [item["id"] for item in findings],
                "mask_character": "█",
            },
        )
        assert masked.status_code == 200
        assert "極秘案件" not in masked.json()["masked_text"]
        assert "taro@example.jp" not in masked.json()["masked_text"]

        labeled = client.post(
            "/api/mask",
            json={
                "text": analyzed.json()["text"],
                "findings": findings,
                "accepted_ids": [item["id"] for item in findings],
                "replacement_mode": "entity_label",
            },
        )
        assert labeled.status_code == 200
        assert labeled.json()["masked_text"] == "[PII辞書]の連絡先は[メールアドレス]"

        deleted = client.delete(f"/api/dictionary/{created.json()['id']}")
        assert deleted.status_code == 204
        assert deleted.content == b""
