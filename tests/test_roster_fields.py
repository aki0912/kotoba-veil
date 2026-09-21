from types import SimpleNamespace

import pytest

from app.detectors import GinzaDetector, JapanesePiiEngine
from app.models import DictionaryEntry
from benchmarks.run import run_benchmark


class ParsedText(list):
    def __init__(self, text, token_data, entities):
        super().__init__(SimpleNamespace(text=value, idx=start, tag_=tag)
                         for value, start, tag in token_data)
        self.text = text
        self.ents = [SimpleNamespace(label_=kind, start_char=start, end_char=end)
                     for kind, start, end in entities]


def engine_with_parse(text, words, entities):
    token_data = []
    cursor = 0
    for value, kind in words:
        start = text.index(value, cursor)
        token_data.append((value, start, kind))
        cursor = start + len(value)
    parsed = ParsedText(text, token_data, entities)
    engine = JapanesePiiEngine()
    engine.ginza._load_attempted = True
    engine.ginza._nlp = lambda view: parsed
    return engine


SURNAME = '名詞-固有名詞-人名-姓'
GIVEN = '名詞-固有名詞-人名-名'
NOUN = '名詞-普通名詞-一般'


def test_name_morphology_recovers_a_missed_name_and_a_surname_mislabeled_as_place():
    text = '青山春樹、田中花子、山川亮'
    engine = engine_with_parse(text, [
        ('青山', SURNAME), ('春樹', GIVEN), ('田中', SURNAME), ('花子', GIVEN),
        ('山川', SURNAME), ('亮', GIVEN),
    ], [('Person', 5, 9), ('Province', 10, 12)])
    findings = engine.analyze(text, None, [])
    assert [(f.entity_type, f.text) for f in findings] == [
        ('PERSON', '青山春樹'), ('PERSON', '田中花子'), ('PERSON', '山川亮')]
    # Turning PERSON off must not bring back the erroneous location candidate.
    assert engine.analyze(text, ['LOCATION'], []) == []


@pytest.mark.parametrize('last,expanded', [('和', True), ('宅', False), ('役', False)])
def test_name_completion_uses_neighboring_names_and_does_not_absorb_role_suffix(last, expanded):
    text = f'青山春樹、田中花子、林田景{last}'
    engine = engine_with_parse(text, [
        ('青山', SURNAME), ('春樹', GIVEN), ('田中', SURNAME), ('花子', GIVEN),
        ('林田', SURNAME), ('景', GIVEN), (last, NOUN),
    ], [('Person', 0, 4), ('Person', 5, 9), ('Person', 10, 13)])
    findings = engine.analyze(text, ['PERSON'], [])
    assert findings[-1].text == ('林田景和' if expanded else '林田景')


def test_one_name_is_not_sufficient_evidence_for_a_trailing_fragment():
    text = '林田景和'
    engine = engine_with_parse(text, [('林田', SURNAME), ('景', GIVEN), ('和', NOUN)],
                               [('Person', 0, 3)])
    assert [f.text for f in engine.analyze(text, ['PERSON'], [])] == ['林田景']


def test_name_list_does_not_turn_common_tasks_into_people():
    text = '青山春樹、田中花子、受付担当'
    engine = engine_with_parse(text, [
        ('青山', SURNAME), ('春樹', GIVEN), ('田中', SURNAME), ('花子', GIVEN),
        ('受付', NOUN), ('担当', NOUN),
    ], [('Person', 0, 4), ('Person', 5, 9)])
    assert [f.text for f in engine.analyze(text, ['PERSON'], [])] == ['青山春樹', '田中花子']


@pytest.mark.parametrize('date', ['2027（令和9）年10月12日', '令和元年5月1日',
                                  '２０２７（令和９）年１０月１２日'])
def test_era_dates_include_the_whole_year_and_keep_original_offsets(date):
    text = '作成日：' + date
    findings = JapanesePiiEngine().analyze(text, ['DATE_TIME'], [])
    assert [(f.start, f.end, f.text) for f in findings] == [(4, 4 + len(date), date)]


def test_spaced_organization_replaces_wrong_ner_category_before_entity_selection():
    text = '桜　丘　町　会'
    engine = JapanesePiiEngine()
    # NER sees a compact heading and mistakes the organization for a person.
    engine.ginza._load_attempted = True
    def parse(view):
        assert view == '桜丘町会'
        return ParsedText(view, [], [('Person', 0, 4)])
    engine.ginza._nlp = parse
    assert engine.analyze(text, ['PERSON'], []) == []
    findings = engine.analyze(text, ['ORGANIZATION'], [])
    assert [(f.start, f.end, f.text) for f in findings] == [(0, len(text), text)]
    custom = DictionaryEntry(id=1, term=text, entity_type='CUSTOM', created_at='2026-01-01')
    assert engine.analyze(text, None, [custom])[0].entity_type == 'CUSTOM'


def test_layout_normalization_does_not_join_multicharacter_names_or_lines():
    from app.detectors import _compact_layout
    text = '担当者　名\n山田 太郎\n担当\n者名'
    assert _compact_layout(text)[0] == text


def test_role_tokens_do_not_become_person_entities():
    text = '自治会長、運営委員長'
    detector = GinzaDetector()
    detector._load_attempted = True
    detector._nlp = lambda view: ParsedText(view, [], [('Person', 5, 7)])
    assert detector.analyze(text) == []


def test_district_list_requires_both_location_and_numbered_team_signals():
    engine = JapanesePiiEngine()
    text = '四丁目、春野、青丘　２～４班'
    findings = engine.analyze(text, ['LOCATION'], [])
    assert [f.text for f in findings] == ['四丁目', '春野', '青丘']
    for negative in ['受付、誘導、設営', '用具、配置、片付け３班', '四丁目、春野、青丘']:
        assert engine.analyze(negative, None, []) == []


def test_public_roster_regression_with_real_ginza():
    report = run_benchmark('benchmarks/datasets/roster-fields.jsonl')
    assert report['metadata']['nlp_available']
    assert report['exact']['micro']['true_positives'] == 27
    assert report['exact']['micro']['false_positives'] == 0
    assert report['exact']['micro']['false_negatives'] == 0
