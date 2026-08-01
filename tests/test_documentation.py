from pathlib import Path


ARCHITECTURE = Path("docs/system-architecture.html")


def test_architecture_document_is_standalone_and_covers_processing_stages() -> None:
    content = ARCHITECTURE.read_text(encoding="utf-8")

    assert '<html lang="ja">' in content
    assert "JapanesePiiEngine" in content
    assert "GiNZA NER" in content
    assert "Presidio Pattern" in content
    assert "日本固有ルール" in content
    assert "PII辞書" in content
    assert "レビュー画面" in content
    assert "元形式で出力" in content
    assert "外部API不使用" in content
    assert "synthetic-v1" in content
    assert "https://" not in content
    assert "<script" not in content
