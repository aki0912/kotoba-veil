from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import threading
from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterable

from app.models import DictionaryEntry, Finding


_DETECTION_TRANSLATION = str.maketrans(
    {
        **{
            chr(codepoint): chr(codepoint - 0xFEE0)
            for codepoint in range(0xFF01, 0xFF5F)
        },
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        "　": " ",
    }
)


def _normalize_detection_text(text: str) -> str:
    """Return a width-normalized, offset-preserving detection view."""

    return text.translate(_DETECTION_TRANSLATION)

try:
    from presidio_analyzer import Pattern, PatternRecognizer
except ImportError:  # pragma: no cover - permits lightweight rule-only development
    Pattern = None  # type: ignore[assignment]
    PatternRecognizer = None  # type: ignore[assignment]


ENTITY_CATALOG = [
    {"id": "PERSON", "label": "人名", "group": "固有表現"},
    {"id": "ORGANIZATION", "label": "組織名", "group": "固有表現"},
    {"id": "LOCATION", "label": "地名", "group": "固有表現"},
    {"id": "ADDRESS", "label": "住所", "group": "日本固有"},
    {"id": "PHONE_NUMBER", "label": "電話番号", "group": "連絡先"},
    {"id": "POSTAL_CODE", "label": "郵便番号", "group": "連絡先"},
    {"id": "EMAIL_ADDRESS", "label": "メールアドレス", "group": "連絡先"},
    {"id": "PERSONAL_ID", "label": "マイナンバー候補", "group": "識別番号"},
    {"id": "DRIVER_LICENSE", "label": "運転免許証番号候補", "group": "識別番号"},
    {"id": "BANK_ACCOUNT", "label": "銀行口座番号候補", "group": "金融"},
    {"id": "CREDIT_CARD", "label": "クレジットカード", "group": "金融"},
    {"id": "DATE_TIME", "label": "日付・時刻", "group": "その他"},
    {"id": "URL", "label": "URL", "group": "ネットワーク"},
    {"id": "IP_ADDRESS", "label": "IPアドレス", "group": "ネットワーク"},
    {"id": "CUSTOM", "label": "ユーザー定義", "group": "ユーザー定義"},
]


@dataclass(frozen=True)
class Candidate:
    entity_type: str
    start: int
    end: int
    score: float
    source: str


_KANJI = "一-龯々髙﨑"
_SPACED_HEADING = re.compile(rf"(?<![{_KANJI}])(?:[{_KANJI}][ \t\u3000]+){{2,}}[{_KANJI}](?![{_KANJI}])")
_ROLES = re.compile(r"(?:町会|町内会|自治会|実行委員|運営委員|委員|班|係)長")
_NAME_FIELD = re.compile(
    rf"(?:^|[、,，\n])[ \t\u3000*＊◎○]*(?P<name>[{_KANJI}]{{2,8}})"
    r"(?=[ \t\u3000]*(?:[（(][^()（）\n]*[）)])?[ \t\u3000]*(?:[、,，\n]|$))"
)


def _compact_layout(text: str) -> tuple[str, list[int]]:
    """Join letter-spaced Japanese headings, keeping an original-offset map.

    Spaces between multi-character words and all line breaks are retained.
    This view is only for detection; the source document is never rewritten.
    """
    removed = set()
    for match in _SPACED_HEADING.finditer(text):
        removed.update(i for i in range(match.start(), match.end()) if text[i].isspace())
    offsets = [i for i in range(len(text)) if i not in removed]
    return "".join(text[i] for i in offsets), offsets


def _original_candidate(candidate: Candidate, offsets: list[int]) -> Candidate:
    return Candidate(candidate.entity_type, offsets[candidate.start],
                     offsets[candidate.end - 1] + 1, candidate.score, candidate.source)


def _name_field_candidates(document, candidates: list[Candidate]) -> list[Candidate]:
    """Use surname/given-name morphology to repair bounded roster fields.

    An arbitrary kanji list is not sufficient name evidence. Completing a
    one-character trailing fragment additionally requires other complete names
    in the list and an NER person span covering the surname and given name.
    """
    fields = list(_NAME_FIELD.finditer(document.text))
    person_spans = {(c.start, c.end) for c in candidates if c.entity_type == "PERSON"}
    anchors = sum(match.span("name") in person_spans for match in fields)
    result = []
    all_tokens = list(document)
    token_starts = [token.idx for token in all_tokens]
    for match in fields:
        start, end = match.span("name")
        tokens = all_tokens[bisect_left(token_starts, start):bisect_left(token_starts, end)]
        if (len(tokens) < 2 or tokens[0].idx != start or
                tokens[-1].idx + len(tokens[-1].text) != end):
            continue
        if not tokens[0].tag_.endswith("人名-姓") or not tokens[1].tag_.endswith("人名-名"):
            continue
        exact_name = len(tokens) == 2
        trailing_fragment = (
            len(tokens) == 3 and len(tokens[2].text) == 1 and
            tokens[2].tag_ == "名詞-普通名詞-一般" and
            tokens[2].text not in "役係長宅家様氏殿班部" and anchors >= 2 and
            (start, tokens[2].idx) in person_spans
        )
        if exact_name or trailing_fragment:
            result.append(Candidate("PERSON", start, end, 0.88, "ginza-name-field"))
    return result


def _local_field_candidates(text: str) -> list[Candidate]:
    compact, offsets = _compact_layout(text)
    view = _normalize_detection_text(compact)
    candidates = []
    # Bounded organization fields, not mentions inside a job title or sentence.
    organization = re.compile(
        rf"(?:^|[、,\n])[ \t*◎○]*(?P<org>[{_KANJI}ァ-ヶー]{{0,24}}"
        r"(?:自治会|町内会|町会|消防団|女性部|青年部|婦人会|子ども会|子供会|委員会))"
        r"(?=$|[ \t\n、,。(])"
    )
    for match in organization.finditer(view):
        candidates.append(Candidate("ORGANIZATION", *match.span("org"), 0.88, "jp-organization-field"))
    # A district list must have a chome anchor and an explicit numbered team.
    # Team numbers themselves are not place names.
    district_list = re.compile(
        rf"^[ \t]*(?P<places>[{_KANJI}]{{2,8}}(?:[、,][ \t]*[{_KANJI}]{{2,8}}){{1,8}})"
        r"[ \t]*[0-9一二三四五六七八九十]+(?:[～~−-][0-9一二三四五六七八九十]+)?班[ \t]*$",
        re.MULTILINE,
    )
    for match in district_list.finditer(view):
        places = list(re.finditer(rf"[{_KANJI}]{{2,8}}", match.group("places")))
        if not any(re.fullmatch(r"[一二三四五六七八九十]+丁目", item.group()) for item in places):
            continue
        for place in places:
            start = match.start("places") + place.start()
            candidates.append(Candidate("LOCATION", start, start + len(place.group()),
                                        0.78, "jp-district-list"))
    return [_original_candidate(candidate, offsets) for candidate in candidates]


class GinzaDetector:
    """Lazy GiNZA loader so rule-only startup and health checks stay fast."""

    def __init__(self) -> None:
        self._nlp = None
        self._load_attempted = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._nlp is not None

    def _ensure_loaded(self) -> None:
        if self._load_attempted:
            return
        with self._lock:
            if self._load_attempted:
                return
            self._load_attempted = True
            if os.getenv("KOTOBA_VEIL_DISABLE_NLP") == "1":
                return
            try:
                import spacy

                self._nlp = spacy.load(
                    "ja_ginza",
                    disable=["parser", "morphologizer", "compound_splitter", "bunsetu_recognizer"],
                )
            except (ImportError, OSError, ValueError):
                self._nlp = None

    def analyze(self, text: str) -> list[Candidate]:
        self._ensure_loaded()
        if self._nlp is None or not text.strip():
            return []
        view, offsets = _compact_layout(text)
        document = self._nlp(view)
        candidates: list[Candidate] = []
        roles = list(_ROLES.finditer(view))
        for entity in document.ents:
            mapped = _map_ginza_label(entity.label_)
            if mapped and not _inside_email_like_token(
                view,
                entity.start_char,
                entity.end_char,
            ):
                if any(role.start() <= entity.start_char and entity.end_char <= role.end()
                       for role in roles):
                    continue
                candidates.append(
                    Candidate(mapped, entity.start_char, entity.end_char, 0.82, "ginza")
                )
        name_fields = _name_field_candidates(document, candidates)
        candidates = [candidate for candidate in candidates
                      if not any(candidate.start < field.end and candidate.end > field.start
                                 for field in name_fields)]
        candidates.extend(name_fields)
        return [_original_candidate(candidate, offsets) for candidate in candidates]


def _inside_email_like_token(text: str, start: int, end: int) -> bool:
    token_characters = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~@-]")
    token_start = start
    token_end = end
    while token_start > 0 and token_characters.fullmatch(text[token_start - 1]):
        token_start -= 1
    while token_end < len(text) and token_characters.fullmatch(text[token_end]):
        token_end += 1
    return "@" in text[token_start:token_end]


def _map_ginza_label(label: str) -> str | None:
    normalized = label.upper().replace("-", "_")
    if normalized in {"PERSON", "PER"}:
        return "PERSON"
    if normalized in {
        "ORG",
        "COMPANY",
        "ORGANIZATION_OTHER",
        "POLITICAL_ORGANIZATION_OTHER",
        "PRO_SPORTS_ORGANIZATION",
        "SHOW_ORGANIZATION",
        "SCHOOL",
        "RESEARCH_INSTITUTE",
        "GOVERNMENT",
        "INTERNATIONAL_ORGANIZATION",
    }:
        return "ORGANIZATION"
    if normalized in {
        "CITY",
        "PROVINCE",
        "COUNTRY",
        "COUNTY",
        "GPE_OTHER",
        "LOCATION_OTHER",
        "DOMESTIC_REGION",
    }:
        return "LOCATION"
    return None


class JapanesePiiEngine:
    def __init__(self) -> None:
        self.ginza = GinzaDetector()
        self._presidio_recognizers = self._build_presidio_recognizers()

    @property
    def nlp_available(self) -> bool:
        return self.ginza.available

    @staticmethod
    def _build_presidio_recognizers() -> list[object]:
        if Pattern is None or PatternRecognizer is None:
            return []
        definitions = {
            "EMAIL_ADDRESS": [
                Pattern(
                    "email",
                    r"(?<![A-Za-z0-9.!#$%&'*+/=?^`{|}~-])[A-Za-z0-9.!#$%&'*+/=?^`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
                    0.92,
                )
            ],
            "POSTAL_CODE": [
                Pattern(
                    "jp-postal",
                    r"(?<!\d)(?:〒\s*)?\d{3}[-ー]?\d{4}(?!\d|[-ー]\d)",
                    0.88,
                )
            ],
            "PHONE_NUMBER": [
                Pattern(
                    "jp-phone",
                    r"(?<!\d)(?:(?:\+81[-ー\s]?(?:0)?\d{1,4})|(?:0\d{1,4})|(?:\(0\d{1,4}\)))[-ー\s]?\d{1,4}[-ー\s]?\d{3,4}(?!\d)",
                    0.78,
                )
            ],
            "URL": [
                Pattern("url", r"https?://[^\s<>()\[\]{}、。]+", 0.9)
            ],
        }
        return [
            PatternRecognizer(supported_entity=entity, patterns=patterns)
            for entity, patterns in definitions.items()
        ]

    def analyze(
        self,
        text: str,
        enabled_entities: Iterable[str] | None,
        dictionary: Iterable[DictionaryEntry],
        block_id: str = "text",
    ) -> list[Finding]:
        enabled = set(enabled_entities or [item["id"] for item in ENTITY_CATALOG])
        candidates = self.ginza.analyze(text)
        patterns = self._pattern_candidates(text)
        corrections = [candidate for candidate in patterns
                       if candidate.source in {"jp-organization-field", "jp-district-list"}]
        # A corrected name/type must not reappear under its old NER type just
        # because the user has disabled the corrected category.
        candidates = [candidate for candidate in candidates
                      if not any(candidate.start < field.end and candidate.end > field.start
                                 for field in corrections)]
        candidates.extend(patterns)
        candidates.extend(self._dictionary_candidates(text, dictionary))
        selected = [candidate for candidate in candidates if candidate.entity_type in enabled]
        selected = self._resolve_overlaps(selected)
        return [self._to_finding(text, candidate, block_id) for candidate in selected]

    def _pattern_candidates(self, text: str) -> list[Candidate]:
        detection_text = _normalize_detection_text(text)
        candidates: list[Candidate] = []
        if self._presidio_recognizers:
            for recognizer in self._presidio_recognizers:
                results = recognizer.analyze(
                    text=detection_text,
                    entities=[recognizer.supported_entities[0]],
                )
                for result in results:
                    candidate = Candidate(
                        result.entity_type,
                        result.start,
                        result.end,
                        result.score,
                        "presidio-pattern",
                    )
                    candidate = self._validate_pattern_candidate(detection_text, candidate)
                    if candidate:
                        candidates.append(candidate)
        else:
            fallback = {
                "EMAIL_ADDRESS": r"(?<![A-Za-z0-9.!#$%&'*+/=?^`{|}~-])[A-Za-z0-9.!#$%&'*+/=?^`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
                "POSTAL_CODE": r"(?<!\d)(?:〒\s*)?\d{3}[-ー]?\d{4}(?!\d|[-ー]\d)",
                "PHONE_NUMBER": r"(?<!\d)(?:(?:\+81[-ー\s]?(?:0)?\d{1,4})|(?:0\d{1,4})|(?:\(0\d{1,4}\)))[-ー\s]?\d{1,4}[-ー\s]?\d{3,4}(?!\d)",
                "URL": r"https?://[^\s<>()\[\]{}、。]+",
            }
            for entity, expression in fallback.items():
                for match in re.finditer(expression, detection_text, re.IGNORECASE):
                    candidate = Candidate(
                        entity,
                        match.start(),
                        match.end(),
                        0.85,
                        "local-pattern",
                    )
                    candidate = self._validate_pattern_candidate(detection_text, candidate)
                    if candidate:
                        candidates.append(candidate)

        number = r"[0-9一二三四五六七八九十百千]+"
        address_pattern = re.compile(
            r"(?:東京都|北海道|(?:京都|大阪)府|.{2,3}県)"
            r"[^\s、。;；]{1,60}?"
            rf"(?:(?:{number}(?:丁目|番地?|番|号)){{1,4}}|"
            rf"{number}(?:[-ー]{number}){{1,3}})"
        )
        candidates.extend(
            Candidate("ADDRESS", match.start(), match.end(), 0.76, "jp-address-rule")
            for match in address_pattern.finditer(detection_text)
        )

        date_pattern = re.compile(
            r"(?<!\d)(?:19|20)\d{2}\((?:明治|大正|昭和|平成|令和)(?:元|\d{1,2})\)年\d{1,2}月\d{1,2}日(?!\d)"
            r"|(?<!\d)(?:明治|大正|昭和|平成|令和)(?:元|\d{1,2})年\d{1,2}月\d{1,2}日(?!\d)"
            r"|(?<!\d)(?:(?:19|20)\d{2}年)?\d{1,2}月\d{1,2}日(?!\d)"
            r"|(?<!\d)(?:19|20)\d{2}([/.-])\d{1,2}\1\d{1,2}(?!\d)"
        )
        candidates.extend(
            Candidate("DATE_TIME", match.start(), match.end(), 0.72, "date-rule")
            for match in date_pattern.finditer(detection_text)
        )

        candidates.extend(self._contextual_number_candidates(detection_text))
        candidates.extend(self._contextual_named_entity_candidates(detection_text))
        candidates.extend(_local_field_candidates(text))
        candidates.extend(self._credit_card_candidates(detection_text))
        candidates.extend(self._ip_candidates(detection_text))
        return candidates

    @staticmethod
    def _validate_pattern_candidate(
        text: str,
        candidate: Candidate,
    ) -> Candidate | None:
        value = text[candidate.start : candidate.end]
        context = text[max(0, candidate.start - 16) : candidate.start]
        negative_number_contexts = (
            "型番",
            "品番",
            "商品コード",
            "注文番号",
            "受付番号",
            "管理番号",
        )
        if candidate.entity_type == "PHONE_NUMBER" and any(
            marker in context for marker in negative_number_contexts
        ):
            return None
        if candidate.entity_type == "POSTAL_CODE":
            has_postal_signal = (
                "〒" in value or "-" in value or "ー" in value or "郵便" in context
            )
            if not has_postal_signal:
                return None
        if candidate.entity_type == "URL":
            end = candidate.end
            for suffix in ("でした", "です", "ました", "ます"):
                if text[candidate.start : end].endswith(suffix):
                    end -= len(suffix)
                    break
            if end <= candidate.start + len("https://"):
                return None
            return Candidate(
                candidate.entity_type,
                candidate.start,
                end,
                candidate.score,
                candidate.source,
            )
        return candidate

    @staticmethod
    def _contextual_named_entity_candidates(text: str) -> list[Candidate]:
        candidates: list[Candidate] = []
        person_pattern = re.compile(
            r"(?:氏名|担当者|申請者|登録値|確認対象|申請書|記録された値)"
            r"(?:には|は|[:：])?\s*"
            r"(?P<person>"
            r"(?:[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){1,2})"
            r"|(?:[一-龯々髙﨑]{2,8})"
            r"|(?:[ぁ-ゖー]{4,12})"
            r"|(?:[ァ-ヶー]{4,16})"
            r")(?=さん|様|氏|先生)"
        )
        for match in person_pattern.finditer(text):
            candidates.append(
                Candidate(
                    "PERSON",
                    match.start("person"),
                    match.end("person"),
                    0.86,
                    "jp-person-context-rule",
                )
            )

        organization_pattern = re.compile(
            r"(?:登録値|確認対象|申請書|記録された値)(?:には|は|[:：])?\s*"
            r"(?P<organization>"
            r"(?:株式会社|合同会社|有限会社|一般社団法人|一般財団法人|"
            r"医療法人|学校法人|社会福祉法人)"
            r"[^\s、。;；／]{1,30}?"
            r"|[一-龯々ぁ-ゖァ-ヶーA-Za-z0-9・&＆]{2,30}?"
            r"(?:研究所|大学|病院|協会|財団|銀行|支店)[0-9]{0,4}"
            r")(?=です|でした|である|／|、|。|$)"
        )
        for match in organization_pattern.finditer(text):
            candidates.append(
                Candidate(
                    "ORGANIZATION",
                    match.start("organization"),
                    match.end("organization"),
                    0.86,
                    "jp-organization-context-rule",
                )
            )

        location_pattern = re.compile(
            r"(?P<location>[一-龯々]{1,8}(?:都|道|府|県|市|区|町|村))"
            r"(?=が配送地域|の会場|への出張|を担当|から届)"
        )
        for match in location_pattern.finditer(text):
            candidates.append(
                Candidate(
                    "LOCATION",
                    match.start("location"),
                    match.end("location"),
                    0.84,
                    "jp-location-context-rule",
                )
            )
        return candidates

    @staticmethod
    def _contextual_number_candidates(text: str) -> list[Candidate]:
        rules = [
            ("PERSONAL_ID", re.compile(r"(?<!\d)\d{12}(?!\d)"), ("マイナンバー", "個人番号"), 0.94),
            ("DRIVER_LICENSE", re.compile(r"(?<!\d)\d{12}(?!\d)"), ("免許証", "免許番号"), 0.9),
            ("BANK_ACCOUNT", re.compile(r"(?<!\d)\d{7,8}(?!\d)"), ("口座", "普通", "当座"), 0.82),
        ]
        candidates: list[Candidate] = []
        for entity, pattern, contexts, score in rules:
            for match in pattern.finditer(text):
                window = text[max(0, match.start() - 20) : min(len(text), match.end() + 10)]
                if any(context in window for context in contexts):
                    candidates.append(Candidate(entity, match.start(), match.end(), score, "jp-context-rule"))
        return candidates

    @staticmethod
    def _credit_card_candidates(text: str) -> list[Candidate]:
        candidates: list[Candidate] = []
        for match in re.finditer(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text):
            digits = re.sub(r"\D", "", match.group())
            if 13 <= len(digits) <= 19 and _passes_luhn(digits):
                candidates.append(
                    Candidate("CREDIT_CARD", match.start(), match.end(), 0.97, "luhn-rule")
                )
        return candidates

    @staticmethod
    def _ip_candidates(text: str) -> list[Candidate]:
        candidates: list[Candidate] = []
        expression = re.compile(
            r"(?<![0-9A-Fa-f:.])(?:[0-9A-Fa-f:.]{3,})(?![0-9A-Fa-f:.])"
        )
        for match in expression.finditer(text):
            value = match.group().strip(".")
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            start = match.start() + (len(match.group()) - len(match.group().lstrip(".")))
            candidates.append(Candidate("IP_ADDRESS", start, start + len(value), 0.98, "ip-validator"))
        return candidates

    @staticmethod
    def _dictionary_candidates(
        text: str, dictionary: Iterable[DictionaryEntry]
    ) -> list[Candidate]:
        candidates: list[Candidate] = []
        for entry in dictionary:
            for match in re.finditer(re.escape(entry.term), text, re.IGNORECASE):
                candidates.append(
                    Candidate(entry.entity_type, match.start(), match.end(), 1.0, "pii-dictionary")
                )
        return candidates

    @staticmethod
    def _resolve_overlaps(candidates: list[Candidate]) -> list[Candidate]:
        priority = {
            "pii-dictionary": 5,
            "luhn-rule": 4,
            "jp-context-rule": 4,
            "ip-validator": 4,
            "presidio-pattern": 3,
            "local-pattern": 3,
            "jp-address-rule": 3,
            "date-rule": 3,
            "jp-person-context-rule": 3,
            "jp-organization-context-rule": 3,
            "jp-location-context-rule": 3,
            "jp-organization-field": 3,
            "jp-district-list": 3,
            "ginza-name-field": 3,
            "ginza": 2,
        }
        ranked = sorted(
            candidates,
            key=lambda item: (
                -priority.get(item.source, 1),
                -item.score,
                -(item.end - item.start),
                item.start,
            ),
        )
        accepted: list[Candidate] = []
        for candidate in ranked:
            if candidate.end <= candidate.start:
                continue
            if any(candidate.start < item.end and candidate.end > item.start for item in accepted):
                continue
            accepted.append(candidate)
        return sorted(accepted, key=lambda item: (item.start, item.end))

    @staticmethod
    def _to_finding(text: str, candidate: Candidate, block_id: str) -> Finding:
        raw_id = f"{block_id}:{candidate.start}:{candidate.end}:{candidate.entity_type}"
        finding_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:20]
        return Finding(
            id=finding_id,
            entity_type=candidate.entity_type,
            start=candidate.start,
            end=candidate.end,
            text=text[candidate.start : candidate.end],
            score=round(candidate.score, 3),
            source=candidate.source,
            block_id=block_id,
        )


def _passes_luhn(digits: str) -> bool:
    checksum = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def apply_mask(text: str, findings: Iterable[Finding], accepted_ids: set[str], mask: str) -> str:
    return _apply_replacements(
        text,
        findings,
        accepted_ids,
        lambda finding: mask * (finding.end - finding.start),
    )


def apply_entity_labels(
    text: str,
    findings: Iterable[Finding],
    accepted_ids: set[str],
    labels: dict[str, str],
) -> str:
    """Replace accepted spans with non-sensitive, human-readable entity labels."""

    return _apply_replacements(
        text,
        findings,
        accepted_ids,
        lambda finding: f"[{labels.get(finding.entity_type, finding.entity_type)}]",
    )


def _apply_replacements(
    text: str,
    findings: Iterable[Finding],
    accepted_ids: set[str],
    replacement,
) -> str:
    selected = sorted(
        (finding for finding in findings if finding.id in accepted_ids),
        key=lambda finding: finding.start,
        reverse=True,
    )
    output = text
    for finding in selected:
        if finding.start < 0 or finding.end > len(text) or finding.end <= finding.start:
            continue
        output = output[: finding.start] + replacement(finding) + output[finding.end :]
    return output
