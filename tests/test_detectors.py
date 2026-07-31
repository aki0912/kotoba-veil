from app.detectors import JapanesePiiEngine, apply_mask
from app.models import DictionaryEntry


def dictionary_entry(term: str, entity_type: str = "CUSTOM") -> DictionaryEntry:
    return DictionaryEntry(
        id=1,
        term=term,
        entity_type=entity_type,
        note="",
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_detects_japanese_contact_and_validated_values() -> None:
    engine = JapanesePiiEngine()
    text = (
        "〒100-0001 東京都千代田区千代田1番1号、"
        "電話 090-1234-5678、mail taro@example.jp、"
        "カード 4111 1111 1111 1111、接続元 192.168.1.20"
    )

    findings = engine.analyze(text, None, [])
    by_type = {finding.entity_type: finding for finding in findings}

    assert by_type["POSTAL_CODE"].text == "〒100-0001"
    assert by_type["ADDRESS"].text.startswith("東京都千代田区")
    assert by_type["PHONE_NUMBER"].text == "090-1234-5678"
    assert by_type["EMAIL_ADDRESS"].text == "taro@example.jp"
    assert by_type["CREDIT_CARD"].text == "4111 1111 1111 1111"
    assert by_type["IP_ADDRESS"].text == "192.168.1.20"


def test_context_is_required_for_ambiguous_japanese_identifiers() -> None:
    engine = JapanesePiiEngine()
    text = "個人番号 123456789012、参照値 987654321098、普通口座 1234567"

    findings = engine.analyze(text, ["PERSONAL_ID", "BANK_ACCOUNT"], [])

    assert [(finding.entity_type, finding.text) for finding in findings] == [
        ("PERSONAL_ID", "123456789012"),
        ("BANK_ACCOUNT", "1234567"),
    ]


def test_dictionary_wins_over_lower_scoring_overlapping_rule() -> None:
    engine = JapanesePiiEngine()
    text = "連絡先は090-1234-5678です"

    findings = engine.analyze(text, None, [dictionary_entry("090-1234-5678")])

    assert len(findings) == 1
    assert findings[0].entity_type == "CUSTOM"
    assert findings[0].source == "pii-dictionary"


def test_mask_only_applies_accepted_findings() -> None:
    engine = JapanesePiiEngine()
    text = "a@example.jp と b@example.jp"
    findings = engine.analyze(text, ["EMAIL_ADDRESS"], [])

    masked = apply_mask(text, findings, {findings[1].id}, "█")

    assert masked.startswith("a@example.jp")
    assert "b@example.jp" not in masked
    assert len(masked) == len(text)


def test_entity_selection_filters_results() -> None:
    engine = JapanesePiiEngine()
    findings = engine.analyze(
        "090-1234-5678 / taro@example.jp",
        ["EMAIL_ADDRESS"],
        [],
    )

    assert [finding.entity_type for finding in findings] == ["EMAIL_ADDRESS"]


def test_japanese_prefix_is_not_part_of_email() -> None:
    engine = JapanesePiiEngine()

    findings = engine.analyze("メールはtaro@example.jpです", ["EMAIL_ADDRESS"], [])

    assert len(findings) == 1
    assert findings[0].text == "taro@example.jp"


def test_deterministic_pattern_has_priority_over_overlapping_ner_candidate() -> None:
    engine = JapanesePiiEngine()
    text = "090-1234-5678"
    engine.ginza.analyze = lambda _: [
        type("CandidateLike", (), {
            "entity_type": "ORGANIZATION",
            "start": 4,
            "end": len(text),
            "score": 0.99,
            "source": "ginza",
        })()
    ]

    findings = engine.analyze(text, None, [])

    assert len(findings) == 1
    assert findings[0].entity_type == "PHONE_NUMBER"
    assert findings[0].text == text
