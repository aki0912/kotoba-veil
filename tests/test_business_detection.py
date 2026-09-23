from types import SimpleNamespace

import pytest

from app.detectors import Candidate, JapanesePiiEngine, _map_ginza_label
from app.documents import DocumentProcessor
from app.models import DictionaryEntry, DocumentBlock
from benchmarks.metrics import SampleResult, Span, evaluate
from benchmarks.compare_detection import summarize


@pytest.mark.parametrize('address', [
    '東京都港区赤坂二丁目13-8',
    '東京都港区赤坂二・三丁目地先',
    '神奈川県川崎市中原区小杉町三丁目12-8',
    '東京都港区赤坂二丁目13-8青葉ビル502号室',
    '東京都港区赤坂二丁目13-8 青葉ビル 5階 502号室',
    '東京都港区赤坂二丁目13-8赤坂小学校内',
    '港区赤坂二丁目13先',
])
def test_address_consumes_components_but_not_following_instructions(address):
    text = f'送付先：{address}。到着後に受付へ連絡してください。'
    found = JapanesePiiEngine().analyze(text, ['ADDRESS'], [])
    assert [f.text for f in found] == [address]
    assert text[found[0].start:found[0].end] == address


def test_two_addresses_and_phone_remain_separate():
    values = ['東京都港区赤坂二丁目13-8', '大阪府大阪市北区梅田3-4-5']
    text = '；'.join(values) + ' 電話：03-1234-5678'
    assert [f.text for f in JapanesePiiEngine().analyze(text, None, [])] == values + ['03-1234-5678']


@pytest.mark.parametrize('name', ['水野こはる', '青山 景和', 'マルタン・レア'])
def test_explicit_person_fields_and_document_repetitions(name):
    text = f'担当者：{name}\n\n連絡は{name}までお願いします。'
    findings = JapanesePiiEngine().analyze(text, ['PERSON'], [])
    assert [f.text for f in findings] == [name, name]
    assert all(text[f.start:f.end] == name for f in findings)


def test_repeated_name_context_crosses_blocks_without_persisting_or_rerunning_nlp():
    engine = JapanesePiiEngine()
    calls = []
    engine.ginza.analyze = lambda text: calls.append(text) or []
    blocks = [DocumentBlock(id='p1', text='担当者：青山景和', kind='paragraph'),
              DocumentBlock(id='p2', text='青山景和へ提出。青山景和子とは別人です。', kind='paragraph')]
    found = DocumentProcessor.analyze_blocks(blocks, engine, ['PERSON'], [])
    assert [(f.block_id, f.text) for f in found] == [('p1', '青山景和'), ('p2', '青山景和')]
    assert len(calls) == 2
    assert engine.analyze('青山景和へ提出。', ['PERSON'], []) == []


def test_repetition_respects_dictionary_and_disabled_corrected_type():
    engine = JapanesePiiEngine()
    engine.ginza.analyze = lambda text: [Candidate('LOCATION', 0, 4, .82, 'ginza')] if text == '青山景和' else []
    blocks = [('a', '担当者：青山景和'), ('b', '青山景和')]
    assert engine.analyze_document(blocks, ['LOCATION'], []) == []
    dictionary = [DictionaryEntry(id=1, term='青山景和', entity_type='CUSTOM', created_at='2026-01-01')]
    assert all(f.entity_type == 'CUSTOM' for f in engine.analyze_document(blocks, None, dictionary))


@pytest.mark.parametrize('text, expected', [
    ('・作業場所となる青葉出張所の予約を確認。', ['青葉出張所']),
    ('・来場者は桜公園少年野球場へ集合。', ['桜公園少年野球場']),
    ('青葉野球場（東京都港区赤坂四・五丁目地先）', ['青葉野球場', '東京都港区赤坂四・五丁目地先']),
    ('会場：青葉テニスコート（〒123-4567）', ['青葉テニスコート', '〒123-4567']),
    ('青葉図書館（東京都港区赤坂二丁目13-8）', ['青葉図書館', '東京都港区赤坂二丁目13-8']),
    ('みらい地域プラザ「交流の里」へ集合。', ['みらい地域プラザ「交流の里」']),
    ('株式会社青空デザインの水野こはるです。', ['株式会社青空デザイン', '水野こはる']),
    ('朝日は青空株式会社側からの連絡です。', ['青空株式会社']),
])
def test_facility_alias_affiliation_and_adjacent_structured_fields(text, expected):
    assert [f.text for f in JapanesePiiEngine().analyze(text, None, [])] == expected


@pytest.mark.parametrize('text', [
    '参加者への案内状を作成してください。',
    '当日の作業手順は以下の通りです。',
    'お疲れ様です。ご担当者様、よろしくお願いします。',
    'リストに基づいた返却手続きと明細書の確認です。',
    '型番：ABC-9:15。所要時間は3時間、開始30分前に集合。',
    '比率は1/2、計算は3/4です。',
    '次回のフィールドワークを確認。フィールド内に入ってください。',
    '打合せ資料の比率は1/2です。',
    '東京都港区には三人がいます。東京都港区の担当は一名です。',
])
def test_general_prose_roles_durations_and_fractions_are_not_business_pii(text):
    assert JapanesePiiEngine().analyze(text, None, []) == []


def test_dates_times_fullwidth_offsets_and_invalid_times():
    text = '日時：２０２８年４月５日（水）１４時、１５：３０。打合せ予定は4/6。'
    found = JapanesePiiEngine().analyze(text, ['DATE_TIME'], [])
    assert [f.text for f in found] == ['２０２８年４月５日（水）', '１４時', '１５：３０', '4/6']
    for f in found:
        assert text[f.start:f.end] == f.text
    assert JapanesePiiEngine().analyze('25時、9時61分、24:30、12:99、3日前、3時間', ['DATE_TIME'], []) == []


def test_facility_ner_labels_are_specific_not_generic():
    for label in ['Sports_Facility', 'Theater', 'Museum']:
        assert _map_ginza_label(label) == 'LOCATION'
    for label in ['GOE_Other', 'N_Facility', 'Period_Time']:
        assert _map_ginza_label(label) is None


def test_coverage_distinguishes_fully_masked_document_and_partial_name():
    name = Span('PERSON', 0, 4, '青山景和')
    rows = [SampleResult('partial', 4, (name,), (Span('PERSON', 0, 3, '青山景'),), 1, source_gold=(name,)),
            SampleResult('full', 4, (name,), (name,), 1, source_gold=(name,))]
    report = evaluate(rows)['source_pii_coverage']
    assert report['fully_covered_documents'] == 1
    assert report['character_recall'] == .875
    assert report['per_source_label']['PERSON']['fully_covered'] == 1


def test_speed_comparison_weights_processes_equally_and_checks_coverage_goals():
    runs = []
    for variant, total in [('before', 10), ('after', 15), ('after', 25), ('before', 10)]:
        runs.append({'variant': variant, 'report': {
            'metadata': {'model_load_ms': 1},
            'latency_ms': {'total': total, 'median': total/2, 'p95': total},
            'source_pii_coverage': {'character_recall': .99, 'character_precision': .99,
                                    'per_source_label': {kind: {'annotations': 100, 'fully_covered': 96}
                                                         for kind in ('PERSON', 'ADDRESS')}}}})
    result = summarize(runs)
    assert result['latency']['total']['ratio'] == 2
    assert result['all_goals_met']
    runs[1]['report']['source_pii_coverage']['character_precision'] = .97
    assert not summarize(runs)['all_goals_met']


def test_numeric_facility_ner_is_not_treated_as_a_location():
    class Parsed(list):
        text = '型番090-8642-9753です。'
        ents = [SimpleNamespace(label_='Theater', start_char=6, end_char=10)]
    engine = JapanesePiiEngine()
    engine.ginza._load_attempted = True
    engine.ginza._nlp = lambda _: Parsed()
    assert engine.analyze(Parsed.text, None, []) == []


def test_morphological_subject_completion_and_signature_affiliation():
    from app.japanese_rules import morphological_candidates
    class Parsed(list):
        text = '山崎景和が準備します。'
    parsed = Parsed([
        SimpleNamespace(text='山崎', idx=0, tag_='名詞-固有名詞-人名-姓'),
        SimpleNamespace(text='景', idx=2, tag_='名詞-固有名詞-人名-名'),
        SimpleNamespace(text='和', idx=3, tag_='名詞-普通名詞-一般'),
    ])
    result = morphological_candidates(parsed, [Candidate('PERSON', 0, 3, .82, 'ginza')])
    assert any(c.entity_type == 'PERSON' and (c.start, c.end) == (0, 4) for c in result)
    parsed.text = 'ご連絡をお願いします。\n\n青空電機\n山崎景和'
    parsed.clear()
    start = parsed.text.index('山崎景和')
    result = morphological_candidates(parsed, [Candidate('PERSON', start, start+4, .82, 'ginza')])
    assert ('ORGANIZATION', '青空電機') in [(c.entity_type, parsed.text[c.start:c.end]) for c in result]
