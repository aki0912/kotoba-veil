from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import threading
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
        "ー": "-",
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
    {"id": "CUSTOM", "label": "PII辞書", "group": "ユーザー定義"},
]


@dataclass(frozen=True)
class Candidate:
    entity_type: str
    start: int
    end: int
    score: float
    source: str


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
        document = self._nlp(text)
        candidates: list[Candidate] = []
        for entity in document.ents:
            mapped = _map_ginza_label(entity.label_)
            if mapped:
                candidates.append(
                    Candidate(mapped, entity.start_char, entity.end_char, 0.82, "ginza")
                )
        return candidates


def _map_ginza_label(label: str) -> str | None:
    normalized = label.upper().replace("-", "_")
    if "PERSON" in normalized or normalized in {"PER", "PET_NAME"}:
        return "PERSON"
    if "ORGANIZATION" in normalized or normalized in {"ORG", "COMPANY"}:
        return "ORGANIZATION"
    if normalized in {"PHONE", "PHONE_NUMBER"}:
        return "PHONE_NUMBER"
    if normalized in {"EMAIL", "EMAIL_ADDRESS"}:
        return "EMAIL_ADDRESS"
    if normalized == "URL":
        return "URL"
    if any(part in normalized for part in ("CITY", "PROVINCE", "COUNTRY", "LOCATION", "GPE", "LOC")):
        return "LOCATION"
    if normalized in {"DATE", "TIME", "DATE_TIME"}:
        return "DATE_TIME"
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
                    r"(?<!\d)(?:〒\s*)?\d{3}-?\d{4}(?!\d|-\d)",
                    0.88,
                )
            ],
            "PHONE_NUMBER": [
                Pattern(
                    "jp-phone",
                    r"(?<!\d)(?:(?:\+81[-\s]?(?:0)?\d{1,4})|(?:0\d{1,4})|(?:\(0\d{1,4}\)))[-\s]?\d{1,4}[-\s]?\d{3,4}(?!\d)",
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
        candidates.extend(self._pattern_candidates(text))
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
                "POSTAL_CODE": r"(?<!\d)(?:〒\s*)?\d{3}-?\d{4}(?!\d|-\d)",
                "PHONE_NUMBER": r"(?<!\d)(?:(?:\+81[-\s]?(?:0)?\d{1,4})|(?:0\d{1,4})|(?:\(0\d{1,4}\)))[-\s]?\d{1,4}[-\s]?\d{3,4}(?!\d)",
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
            rf"{number}(?:-{number}){{1,3}})"
        )
        candidates.extend(
            Candidate("ADDRESS", match.start(), match.end(), 0.76, "jp-address-rule")
            for match in address_pattern.finditer(detection_text)
        )

        date_pattern = re.compile(
            r"(?<!\d)(?:(?:19|20)\d{2}年)?\d{1,2}月\d{1,2}日(?!\d)"
            r"|(?<!\d)(?:19|20)\d{2}([/.-])\d{1,2}\1\d{1,2}(?!\d)"
        )
        candidates.extend(
            Candidate("DATE_TIME", match.start(), match.end(), 0.72, "date-rule")
            for match in date_pattern.finditer(detection_text)
        )

        candidates.extend(self._contextual_number_candidates(detection_text))
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
            has_postal_signal = "〒" in value or "-" in value or "郵便" in context
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
    selected = sorted(
        (finding for finding in findings if finding.id in accepted_ids),
        key=lambda finding: finding.start,
        reverse=True,
    )
    output = text
    for finding in selected:
        if finding.start < 0 or finding.end > len(output) or finding.end <= finding.start:
            continue
        output = output[: finding.start] + mask * (finding.end - finding.start) + output[finding.end :]
    return output
