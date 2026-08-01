from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.detectors import ENTITY_CATALOG
from app.models import DocumentBlock, Finding, ManualFindingOptions


MAX_MANUAL_TERM_LENGTH = 200
VALID_ENTITY_TYPES = frozenset(item["id"] for item in ENTITY_CATALOG)


class ManualFindingError(ValueError):
    """Raised when a manual selection is invalid."""


class ManualFindingOverlapError(ManualFindingError):
    """Raised when the selected occurrence is already represented by a finding."""


@dataclass(frozen=True)
class ManualFindingResult:
    added_findings: list[Finding]
    selected_term: str
    skipped_count: int


def create_manual_findings(
    blocks: list[DocumentBlock],
    existing_findings: list[Finding],
    options: ManualFindingOptions,
) -> ManualFindingResult:
    if options.entity_type not in VALID_ENTITY_TYPES:
        raise ManualFindingError("指定されたPII分類は利用できません。")

    block_by_id = {block.id: block for block in blocks}
    selected_block = block_by_id.get(options.block_id)
    if selected_block is None:
        raise ManualFindingError("選択した文書ブロックが見つかりません。")
    if options.end > len(selected_block.text) or options.end <= options.start:
        raise ManualFindingError("選択範囲が原文の範囲外です。")

    raw_term = selected_block.text[options.start : options.end]
    leading = len(raw_term) - len(raw_term.lstrip())
    trailing = len(raw_term) - len(raw_term.rstrip())
    start = options.start + leading
    end = options.end - trailing
    term = selected_block.text[start:end]
    if not term:
        raise ManualFindingError("空白だけの範囲は追加できません。")
    if len(term) > MAX_MANUAL_TERM_LENGTH:
        raise ManualFindingError("選択範囲は200文字以内にしてください。")

    if _overlaps_existing(options.block_id, start, end, existing_findings):
        raise ManualFindingOverlapError(
            "すでに検出候補です。候補のチェックをONにしてください。"
        )

    if options.scope == "single":
        occurrences = [(options.block_id, start, end)]
    else:
        occurrences = _find_all_occurrences(blocks, term)

    added: list[Finding] = []
    skipped_count = 0
    for block_id, occurrence_start, occurrence_end in occurrences:
        if _overlaps_existing(
            block_id,
            occurrence_start,
            occurrence_end,
            [*existing_findings, *added],
        ):
            skipped_count += 1
            continue
        block = block_by_id[block_id]
        added.append(
            _to_manual_finding(
                block,
                occurrence_start,
                occurrence_end,
                options.entity_type,
            )
        )

    if not added:
        raise ManualFindingOverlapError(
            "同じ文字列はすべて既存の検出候補と重なっています。"
        )
    return ManualFindingResult(added, term, skipped_count)


def _find_all_occurrences(
    blocks: list[DocumentBlock], term: str
) -> list[tuple[str, int, int]]:
    occurrences: list[tuple[str, int, int]] = []
    for block in blocks:
        cursor = 0
        while True:
            start = block.text.find(term, cursor)
            if start < 0:
                break
            end = start + len(term)
            occurrences.append((block.id, start, end))
            cursor = end
    return occurrences


def _overlaps_existing(
    block_id: str,
    start: int,
    end: int,
    findings: list[Finding],
) -> bool:
    return any(
        finding.block_id == block_id
        and start < finding.end
        and end > finding.start
        for finding in findings
    )


def _to_manual_finding(
    block: DocumentBlock,
    start: int,
    end: int,
    entity_type: str,
) -> Finding:
    raw_id = f"{block.id}:{start}:{end}:{entity_type}"
    finding_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:20]
    return Finding(
        id=finding_id,
        entity_type=entity_type,
        start=start,
        end=end,
        text=block.text[start:end],
        score=1.0,
        source="manual-selection",
        block_id=block.id,
    )
