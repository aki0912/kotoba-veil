from app.detectors import JapanesePiiEngine, _map_ginza_label, apply_entity_labels, apply_mask
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


def test_entity_label_mask_explains_the_removed_value_without_leaking_it() -> None:
    engine = JapanesePiiEngine()
    text = "連絡先はtaro@example.jp、電話は090-1234-5678です"
    findings = engine.analyze(text, ["EMAIL_ADDRESS", "PHONE_NUMBER"], [])

    masked = apply_entity_labels(
        text,
        findings,
        {finding.id for finding in findings},
        {"EMAIL_ADDRESS": "メールアドレス", "PHONE_NUMBER": "電話番号"},
    )

    assert masked == "連絡先は[メールアドレス]、電話は[電話番号]です"
    assert "taro@example.jp" not in masked
    assert "090-1234-5678" not in masked


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


def test_width_normalization_preserves_original_offsets_and_text() -> None:
    engine = JapanesePiiEngine()
    text = "郵便番号は〒１２１－１０９３、電話は０９０－１０７４－１１０６です。"

    findings = engine.analyze(text, ["POSTAL_CODE", "PHONE_NUMBER"], [])

    assert [(finding.entity_type, finding.text) for finding in findings] == [
        ("POSTAL_CODE", "〒１２１－１０９３"),
        ("PHONE_NUMBER", "０９０－１０７４－１１０６"),
    ]
    assert all(text[finding.start : finding.end] == finding.text for finding in findings)


def test_parenthesized_japanese_phone_number_is_detected() -> None:
    engine = JapanesePiiEngine()

    findings = engine.analyze("連絡先は(080)1148-1212です。", ["PHONE_NUMBER"], [])

    assert [finding.text for finding in findings] == ["(080)1148-1212"]


def test_url_and_ip_stop_before_japanese_sentence_suffix() -> None:
    engine = JapanesePiiEngine()
    text = "詳細はhttps://example.jp/資料/2です。接続元は192.0.2.1です。"

    findings = engine.analyze(text, ["URL", "IP_ADDRESS", "DATE_TIME"], [])

    assert [(finding.entity_type, finding.text) for finding in findings] == [
        ("URL", "https://example.jp/資料/2"),
        ("IP_ADDRESS", "192.0.2.1"),
    ]


def test_full_japanese_address_span_is_detected() -> None:
    engine = JapanesePiiEngine()
    values = [
        "東京都千代田区霞が関1丁目1番1号",
        "大阪府大阪市北区梅田2-2-2",
        "北海道札幌市中央区北一条３丁目３番地",
    ]

    for value in values:
        findings = engine.analyze(f"送付先は{value}です。", ["ADDRESS"], [])
        assert [finding.text for finding in findings] == [value]


def test_number_shaped_product_identifiers_are_not_contact_details() -> None:
    engine = JapanesePiiEngine()
    text = "型番090-1005-2005と商品コード4000097を出荷しました。"

    findings = engine.analyze(text, ["PHONE_NUMBER", "POSTAL_CODE"], [])

    assert findings == []


def test_ginza_mapping_only_accepts_named_entity_labels() -> None:
    assert _map_ginza_label("Person") == "PERSON"
    assert _map_ginza_label("Company") == "ORGANIZATION"
    assert _map_ginza_label("Research_Institute") == "ORGANIZATION"
    assert _map_ginza_label("City") == "LOCATION"
    assert _map_ginza_label("N_Person") is None
    assert _map_ginza_label("N_Organization") is None
    assert _map_ginza_label("Date") is None
    assert _map_ginza_label("Phone_Number") is None
    assert _map_ginza_label("URL") is None


def test_context_rules_complete_person_name_variants() -> None:
    engine = JapanesePiiEngine()
    cases = [
        ("申請書にはさとうたろうさんです。", "さとうたろう"),
        ("確認対象はカトウイチロウさんです。", "カトウイチロウ"),
        ("記録された値はMisaki Itoさんです。", "Misaki Ito"),
    ]

    for text, expected in cases:
        findings = engine.analyze(text, ["PERSON"], [])
        assert [finding.text for finding in findings] == [expected]
        assert findings[0].source == "jp-person-context-rule"


def test_context_rules_complete_organization_names() -> None:
    engine = JapanesePiiEngine()
    cases = [
        "株式会社青空1",
        "合同会社ことのは2",
        "一般社団法人みらい3",
        "東都データ研究所4",
        "医療法人さくら会5",
    ]

    for expected in cases:
        findings = engine.analyze(f"確認対象は{expected}です。", ["ORGANIZATION"], [])
        assert [finding.text for finding in findings] == [expected]
        assert findings[0].source == "jp-organization-context-rule"


def test_location_suffix_rule_recovers_ginza_misclassification() -> None:
    engine = JapanesePiiEngine()

    findings = engine.analyze("申請書には那覇市が配送地域です。", ["LOCATION"], [])

    assert [finding.text for finding in findings] == ["那覇市"]
    assert findings[0].source == "jp-location-context-rule"


def test_ginza_candidates_inside_invalid_email_are_suppressed() -> None:
    engine = JapanesePiiEngine()

    findings = engine.analyze(
        "入力値user31@localhostはメールではありません。",
        ["PERSON", "ORGANIZATION", "LOCATION"],
        [],
    )

    assert findings == []
