"""Bounded Japanese business-document rules; never consult benchmark annotations."""
from __future__ import annotations

import re

from app.detectors import Candidate

KANJI = "一-龯々髙﨑"
WORD = rf"[{KANJI}ぁ-ゖァ-ヶーA-Za-z0-9・&＆－-]"
NAME = rf"[{KANJI}ぁ-ゖァ-ヶー・]{{2,20}}(?:[ \t][{KANJI}ぁ-ゖァ-ヶー・]{{1,12}})?"
LEGAL = r"(?:株式会社|合同会社|有限会社|一般社団法人|一般財団法人|医療法人|学校法人|社会福祉法人|相互会社)"
FACILITY = r"(?:弓道場|地域プラザ|コミュニティ(?:会館|センター)|学習センター|健康センター|サービスセンター|管理事務所|フィールドハウス|テニスコート|サッカー場|野球場|球技場|競技場|運動場|体育館|図書館|図書室|博物館|美術館|公民館|会館|プール|区役所|市役所|出張所)"
GENERIC_NAMES = re.compile(
    r"(?:担当|受付|申請|利用|参加|確認|記入|責任|作業|関係|各位|お客|疲れ|お世話|業務|資料|会議|事務|委員|係長|部長|課長|所長|社長|先生|日時|場所|会場|以上|不明|未定|当日|当方|予定|通り|明細|詳細|段取り|準備|仕様|来訪者|弊社|御社|貴社|本人|会社|職員|学生|社員)"
)


def is_name(value: str) -> bool:
    value = re.sub(r"(?:さん|さま|様|氏|殿|先生)$", "", value).strip()
    return (bool(re.fullmatch(rf"(?:[{KANJI}]{{1,8}}[ぁ-ゖ]{{0,6}}|[ァ-ヶー・]{{3,20}}|[ぁ-ゖ]{{4,12}})(?:[ \t][{KANJI}ぁ-ゖァ-ヶー・]{{1,10}})?", value)) and not GENERIC_NAMES.search(value)
            and not re.search(FACILITY + "|" + LEGAL, value)
            and (len(value) >= 3 or bool(re.fullmatch(rf"[{KANJI}]{{2}}", value)))
            and not value.endswith(("です", "ます", "ください", "について", "予定", "もの", "ため", "こと", "以下", "済み")))


def _candidate(kind: str, start: int, end: int, score: float = .89) -> Candidate:
    return Candidate(kind, start, end, score, "jp-business-rule")


def structured_candidates(text: str) -> list[Candidate]:
    result = []
    number = r"[0-9一二三四五六七八九十百千]+"
    prefecture = rf"(?:東京都|北海道|(?:京都|大阪)府|[{KANJI}]{{2,3}}県)"
    # Administrative components are bounded; punctuation and particles stop the
    # address. A chome may be followed by a mixed Arabic-number street address.
    start = rf"(?:{prefecture}[{KANJI}ァ-ヶー]{{1,12}}?[市区町村]|[{KANJI}]{{1,8}}区)"
    street = rf"[{KANJI}ぁ-ゖァ-ヶー]{{1,24}}?"
    numeric = rf"{number}(?:・{number})?(?:条{number})?(?:丁目(?:{number})?)?(?:[-ー]{number}|(?:番地?|号)(?:{number})?)*"
    pattern = re.compile(start + rf"(?P<street>{street})" + numeric)
    for match in pattern.finditer(text):
        if re.search(r"には|では|の担当|は|から|まで|です|ます|へ", match.group("street")):
            continue
        end = match.end()
        tail = re.match(r"(?:地先|先)|(?:号?室)|(?:[一-龯々ァ-ヶー]{1,20}(?:学校|大学|病院)内)", text[end:])
        if tail:
            end += tail.end()
        building = re.match(
            rf"[ \t]*{WORD}{{1,24}}?(?:ビル|マンション|ハイツ|荘|館)"
            r"(?:[ \t]*[0-9]+(?:階|号室|室)){0,2}", text[end:])
        if building and not re.search(r"です|ます|から|まで|では|を|には", building.group()):
            end += building.end()
        result.append(Candidate("ADDRESS", match.start(), end, .94, "jp-address-rule"))

    date = re.compile(
        r"(?<!\d)(?:19|20)\d{2}\((?:明治|大正|昭和|平成|令和)(?:元|\d{1,2})\)年\d{1,2}月\d{1,2}日(?!\d)"
        r"|(?<!\d)(?:明治|大正|昭和|平成|令和)(?:元|\d{1,2})年\d{1,2}月\d{1,2}日(?!\d)"
        r"|(?<!\d)(?:(?:19|20)\d{2}年)?\d{1,2}月\d{1,2}日(?!\d)"
        r"|(?<!\d)(?:19|20)\d{2}([/.-])\d{1,2}\1\d{1,2}(?!\d)"
    )
    for match in date.finditer(text):
        end = match.end()
        weekday = re.match(r"[ \t]*\([月火水木金土日](?:曜日|曜)?\)", text[end:])
        if weekday:
            end += weekday.end()
        result.append(Candidate("DATE_TIME", match.start(), end, .9, "date-rule"))
    for match in re.finditer(r"(?<![A-Za-z0-9/])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])|(?<!\d)(?:[01]?\d|2[0-3])時(?:[0-5]?\d分|半)?(?!\d|間|前|後)", text):
        context = text[max(0, match.start()-16):match.start()]
        if re.search(r"型番|品番|商品コード|バージョン|[0-9]:$", context):
            continue
        result.append(Candidate("DATE_TIME", match.start(), match.end(), .9, "date-rule"))
    for match in re.finditer(r"(?<![\d/])(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])(?![\d/])", text):
        context = text[max(0, match.start()-12):match.end()+16]
        if (re.search(r"日程|日時|開催|会議|打合|締切|予定|集合|\([月火水木金土日]\)", context)
                and not re.search(r"比率|割合|分数|型番|品番", context)):
            result.append(Candidate("DATE_TIME", match.start(), match.end(), .86, "date-rule"))
    return result


def business_candidates(text: str) -> list[Candidate]:
    result = []
    # Honorifics, explicit person fields and participant lists provide evidence
    # independent of whether the name occurs in the model vocabulary.
    def person(start, end):
        value = text[start:end]
        cleaned = re.sub(r"(?:さん|さま|様|氏|殿|先生)$", "", value).rstrip()
        if is_name(cleaned):
            result.append(_candidate("PERSON", start, start + len(cleaned), .93))

    for match in re.finditer(rf"(?P<name>[{KANJI}]{{1,8}}[ぁ-ゖ]{{0,6}}|[ァ-ヶー・]{{3,20}})(?:様|さん|氏|殿|先生)(?![一-龯])", text):
        person(*match.span("name"))
    fields = re.compile(rf"(?:氏名|担当者|申請者|記入者|作成者|責任者|連絡担当)(?:名)?[ \t]*[:：][ \t]*(?P<name>{NAME})(?=$|[\n、,。()])")
    for match in fields.finditer(text):
        person(*match.span("name"))
    lists = re.compile(r"(?:出席者|参加者|担当者一覧)(?:[ \t]*[:：][ \t]*|[ \t]*\n)(?P<names>[^\n。]{2,100})")
    for match in lists.finditer(text):
        for item in re.finditer(NAME, match.group("names")):
            person(match.start("names") + item.start(), match.start("names") + item.end())

    # A sender's self-introduction identifies both the affiliation and person.
    intro = re.compile(rf"(?P<org>{WORD}{{2,50}})の(?P<name>{NAME})(?=です|でございます|と申します|までご連絡|様より)")
    for match in intro.finditer(text):
        if not is_name(match.group("name")):
            continue
        org = match.group("org")
        if org.endswith(("担当", "受付", "当日", "先日")) or re.search(r"は|への|から|まで|および|にて|として|です|ます|以下|明細書|当日|次回|リスト", org):
            continue
        kind = "LOCATION" if re.search(FACILITY, org) and not org.endswith(("役所", "出張所")) else "ORGANIZATION"
        result.append(_candidate(kind, *match.span("org"), .94))
        person(*match.span("name"))

    # Corporate designators need no artificial "registration value" prefix.
    for match in re.finditer(rf"(?:{LEGAL}{WORD}{{1,40}}?|{WORD}{{1,40}}?{LEGAL})(?=の|です|でございます|側|にて|から|では|[\s、。:：()／]|$)", text):
        start = match.start()
        prefix = list(re.finditer(r"は|には|から|より|[をが]", match.group()))
        if prefix and re.search(LEGAL, match.group()[prefix[-1].end():]):
            start += prefix[-1].end()
        result.append(_candidate("ORGANIZATION", start, match.end()))

    # Named facilities: a bounded compound plus optional alias. Generic meeting
    # rooms are not independently promoted to named facilities.
    facility = re.compile(rf"(?P<name>{WORD}{{1,35}}?{FACILITY}(?:図書室|[一-龯々]{{1,10}}支所)?)(?:「[^\n「」]{{1,30}}」|\((?![^)]*(?:[0-9〒:：]|丁目|所在地|住所|郵便))[^\n()]{{1,30}}\))?")
    for match in facility.finditer(text):
        start, end = match.span()
        while text[start:start+1] in {"・", "-"}:
            start += 1
        name = match.group()
        # Hiragana particles separate a sentence from a facility compound.
        boundary = list(re.finditer(r"(?:おります|されている|となる|にある|通り|速やかに|は|を|にて|から|まで|より|への|[でがとのに]、?)", name))
        if boundary:
            last = boundary[-1].end()
            if re.search(FACILITY, name[last:]) and re.search(rf"[{KANJI}ァ-ヶ]", name[last:]):
                start = match.start() + last
        if text[start:end] in {"会議室", "管理事務所", "サービスセンター", "テニスコート", "図書室"}:
            continue
        result.append(_candidate("LOCATION", start, end, .91))
    # Prefix-style facility names and aliases such as "...プラザ..." or
    # "...（...センター）"; keep the original spelling and offsets.
    prefix_facility = (
        rf"{WORD}{{0,24}}スポーツプラザ[一-龯々ァ-ヶー]{{1,12}}"
        rf"|{WORD}{{1,24}}フィールド(?:\([^\n()]{{1,30}}運動場\))?(?=の|に|へ|[\s、。()]|$)"
        rf"|{WORD}{{2,24}}\([^\n()]{{1,30}}(?:センター|運動場)\)"
    )
    for match in re.finditer(prefix_facility, text):
        result.append(_candidate("LOCATION", match.start(), match.end(), .92))
    for match in re.finditer(r"(?m)^弓道場(?=様|へ|の|$)", text):
        result.append(_candidate("LOCATION", match.start(), match.end()))
    for match in re.finditer(rf"(?m)^{WORD}{{2,24}}[ \t]+本社ビル[ \t]+[0-9]+階[ \t]+第[0-9]+会議室$", text):
        result.append(_candidate("LOCATION", match.start(), match.end(), .95))
    return result


def morphological_candidates(document, candidates: list[Candidate]) -> list[Candidate]:
    text = document.text
    result = []
    tokens = list(document)
    # Surname morphology plus a bounded subject or signature supplies evidence
    # for unknown given names, including mixed kanji/hiragana spellings.
    for index, token in enumerate(tokens):
        if not token.tag_.endswith("人名-姓"):
            continue
        start = token.idx
        rest = text[start:]
        match = re.match(rf"[{KANJI}]{{2,8}}(?:[ぁ-ゖ]{{1,5}})?", rest)
        if not match:
            continue
        value = match.group()
        # Prefer token boundaries for hiragana given names, not following prose.
        end = start + len(value)
        kanji = re.match(rf"[{KANJI}]{{2,8}}", rest)
        if kanji and len(kanji.group()) > len(token.text):
            end = start + kanji.end()
        following = text[end:]
        context = text[max(0, start-20):start]
        anchored = bool(re.match(r"(?:が|は|へ|に|を|の|まで|です|でございます|さん|様)", following))
        signature = bool(re.search(r"(?:^|\n)[ \t]*$", context) and
                         re.match(r"[ \t]*(?:\n|$)", following))
        name_tokens = [t for t in tokens[index:index+5] if start <= t.idx < end]
        has_given = any(t.tag_.endswith("人名-名") for t in name_tokens)
        if end > start+len(token.text) and is_name(text[start:end]) and (anchored or (signature and has_given and len(name_tokens) == 2)):
            result.append(Candidate("PERSON", start, end, .9, "ginza-business-name"))
    # Existing PERSON spans can be completed inside a grammatical subject, but
    # isolated ambiguous roster fields retain the stricter roster rules.
    for candidate in candidates:
        if candidate.entity_type != "PERSON":
            continue
        match = re.match(rf"[{KANJI}]{{1,3}}(?=が|は|へ|に|を|の|さん|様)", text[candidate.end:])
        if match and is_name(text[candidate.start:candidate.end+match.end()]):
            result.append(Candidate("PERSON", candidate.start, candidate.end+match.end(), .9, "ginza-business-name"))
    # A short final signature paragraph has an affiliation immediately before
    # a model-supported person. It must not be a heading or an arbitrary list.
    last_paragraph = text.rsplit("\n\n", 1)[-1]
    paragraph_start = len(text) - len(last_paragraph)
    if paragraph_start and len(last_paragraph.splitlines()) <= 6:
        for match in re.finditer(rf"(?m)^(?P<org>{WORD}{{2,35}})[ \t\n]+(?P<name>{NAME})[ \t]*$", last_paragraph):
            start = paragraph_start + match.start("name")
            end = paragraph_start + match.end("name")
            if (is_name(match.group("name")) and
                    any(c.entity_type == "PERSON" and start <= c.start < end for c in candidates + result) and
                    not GENERIC_NAMES.search(match.group("org")) and
                    not re.search(FACILITY, match.group("org"))):
                result.append(_candidate("ORGANIZATION", paragraph_start+match.start("org"), paragraph_start+match.end("org"), .94))
                result.append(_candidate("PERSON", start, end, .94))
    return result
