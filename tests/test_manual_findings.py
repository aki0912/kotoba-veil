import pytest

from app.manual_findings import (
    ManualFindingError,
    ManualFindingOverlapError,
    create_manual_findings,
)
from app.models import DocumentBlock, Finding, ManualFindingOptions


def options(**overrides) -> ManualFindingOptions:
    values = {
        "block_id": "a",
        "start": 0,
        "end": 2,
        "entity_type": "CUSTOM",
        "scope": "single",
        "save_to_dictionary": False,
    }
    values.update(overrides)
    return ManualFindingOptions(**values)


def test_single_manual_finding_trims_selection_and_preserves_offsets() -> None:
    blocks = [DocumentBlock(id="a", text="  極秘語  を確認")]

    result = create_manual_findings(
        blocks,
        [],
        options(start=0, end=7, entity_type="ORGANIZATION"),
    )

    assert result.selected_term == "極秘語"
    assert result.skipped_count == 0
    assert len(result.added_findings) == 1
    finding = result.added_findings[0]
    assert (finding.start, finding.end, finding.text) == (2, 5, "極秘語")
    assert finding.entity_type == "ORGANIZATION"
    assert finding.source == "manual-selection"
    assert finding.score == 1.0


def test_all_scope_adds_exact_matches_across_blocks_and_skips_overlap() -> None:
    blocks = [
        DocumentBlock(id="a", text="秘密と秘密"),
        DocumentBlock(id="b", text="別の秘密"),
    ]
    existing = [
        Finding(
            id="existing",
            entity_type="CUSTOM",
            start=3,
            end=5,
            text="秘密",
            score=1,
            source="test",
            block_id="a",
        )
    ]

    result = create_manual_findings(
        blocks,
        existing,
        options(start=0, end=2, scope="all"),
    )

    assert [(item.block_id, item.start, item.end) for item in result.added_findings] == [
        ("a", 0, 2),
        ("b", 2, 4),
    ]
    assert result.skipped_count == 1


def test_manual_selection_rejects_existing_overlap() -> None:
    blocks = [DocumentBlock(id="a", text="秘密情報")]
    existing = [
        Finding(
            id="existing",
            entity_type="CUSTOM",
            start=0,
            end=2,
            text="秘密",
            score=1,
            source="test",
            block_id="a",
        )
    ]

    with pytest.raises(ManualFindingOverlapError, match="すでに検出候補"):
        create_manual_findings(blocks, existing, options(start=1, end=4))


@pytest.mark.parametrize(
    ("blocks", "manual_options", "message"),
    [
        ([DocumentBlock(id="a", text="   ")], options(start=0, end=3), "空白だけ"),
        (
            [DocumentBlock(id="a", text="あ" * 201)],
            options(start=0, end=201),
            "200文字以内",
        ),
        ([DocumentBlock(id="a", text="秘密")], options(end=3), "範囲外"),
        ([DocumentBlock(id="a", text="秘密")], options(block_id="missing"), "見つかりません"),
        ([DocumentBlock(id="a", text="秘密")], options(entity_type="UNKNOWN"), "利用できません"),
    ],
)
def test_manual_selection_validation(blocks, manual_options, message) -> None:
    with pytest.raises(ManualFindingError, match=message):
        create_manual_findings(blocks, [], manual_options)
