from html.parser import HTMLParser
from pathlib import Path


STATIC_DIR = Path("app/static")


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])


def test_workspace_uses_comparison_layout_and_modal_detection_settings() -> None:
    content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    parser = IdCollector()
    parser.feed(content)

    assert len(parser.ids) == len(set(parser.ids))
    assert 'class="comparison-grid"' in content
    assert 'id="source-text"' in content
    assert 'id="highlight-preview"' in content
    assert '<dialog id="settings-dialog"' in content
    assert 'id="entity-legend"' in content
    assert 'id="manual-finding-form"' in content
    assert 'id="manual-entity-type"' in content
    assert 'name="manual-scope"' in content
    assert 'id="manual-save-dictionary"' in content
    assert "ラベル付きマスク結果" in content
    assert 'class="settings-panel"' not in content


def test_ui_renders_entity_labels_and_requests_labeled_mask_output() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'replacement_mode: "entity_label"' in script
    assert 'data-label="${escapeHtml(statusLabel)}"' in script
    assert "capturePreviewSelection" in script
    assert "codePointOffsetWithin" in script
    assert 'source === "manual-selection" ? "手動追加"' in script
    assert 'class="finding-state"' in script
    assert "entityClass(finding.entity_type)" in script
    assert ".entity-person" in styles
    assert ".entity-phone-number" in styles
    assert ".highlight-preview mark.rejected" in styles
    assert ".highlight-preview mark::before" in styles
    assert ".pending-selection" in styles
    assert ".manual-finding-form" in styles
    assert "text-decoration: none" in styles
    assert ".state-retained" in styles
    assert "@media (max-width: 980px)" in styles
