from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
)

from app.detectors import JapanesePiiEngine
from app.documents import DocumentProcessor
from app.models import DictionaryEntry, Finding
from app.pdf_documents import PdfProcessingError


def make_pdf(path, content=None, *, composite=False, mapping=None):
    """Synthetic subset encoding like spreadsheet exports, with no private data."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    mapping = mapping or {0x81: '山', 0x82: '田', 0x83: '太', 0x84: '郎', 0x85: '、', 0x86: '係'}
    size = 4 if composite else 2
    cmap = DecodedStreamObject()
    pairs = '\n'.join(f'<{code:0{size}X}> <{text.encode("utf-16-be").hex()}>' for code, text in mapping.items())
    cmap.set_data((f'1 begincodespacerange\n<{0:0{size}X}> <{"F" * size}>\nendcodespacerange\n'
                   f'{len(mapping)} beginbfchar\n{pairs}\nendbfchar').encode())
    font = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/TrueType'),
        NameObject('/BaseFont'): NameObject('/SyntheticSubset'),
        NameObject('/FirstChar'): NumberObject(0x81),
        NameObject('/LastChar'): NumberObject(0x86),
        NameObject('/Widths'): ArrayObject([NumberObject(v) for v in [1000, 900, 1000, 1000, 500, 1000]]),
        NameObject('/ToUnicode'): writer._add_object(cmap),
    })
    if composite:
        descendant = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/CIDFontType2'),
            NameObject('/BaseFont'): NameObject('/SyntheticSubset'),
            NameObject('/DW'): NumberObject(1000),
            NameObject('/W'): ArrayObject([NumberObject(0x82), ArrayObject([NumberObject(900)])]),
        })
        font[NameObject('/Subtype')] = NameObject('/Type0')
        font[NameObject('/Encoding')] = NameObject('/Identity-H')
        font[NameObject('/DescendantFonts')] = ArrayObject([writer._add_object(descendant)])
        for key in ['/FirstChar', '/LastChar', '/Widths']:
            del font[key]
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(content or b'BT /F1 12 Tf 20 150 Td [<8182> -20 <83> 15 <84> <85>] TJ <86> Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


def findings_for(blocks, term):
    return DocumentProcessor.analyze_blocks(
        blocks, JapanesePiiEngine(), ['CUSTOM'],
        [DictionaryEntry(id=1, term=term, created_at='2026-01-01', entity_type='CUSTOM')],
    )


def test_subset_font_decoding_joins_split_glyphs_and_preserves_unselected_text(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf')
    processor = DocumentProcessor()
    blocks = processor.extract(source)
    assert [b.text for b in blocks] == ['山田太郎、係']
    findings = findings_for(blocks, '山田太郎')
    assert len(findings) == 1
    output = tmp_path / 'masked.pdf'
    processor.mask(source, output, findings, {f.id for f in findings}, '█')
    page = PdfReader(output).pages[0]
    assert page.extract_text() == '████、係'
    operations = page.get_contents().operations
    # Preserve the kerning numbers and the original widths, including 900.
    assert [float(args[0][0]) for args, op in operations if op == b'TJ'] == [-20, 15]
    mask_fonts = [font.get_object() for name, font in page['/Resources']['/Font'].items() if name.startswith('/KVMask')]
    assert {float(font['/Widths'][0]) for font in mask_fonts} == {900, 1000}
    assert all(b'\x81\x82' not in value.get_object().get_data()
               for value in [page['/Contents']])
    assert '山田太郎' not in ''.join(b.text for b in processor.extract(output))


def test_only_accepted_occurrence_is_masked(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf',
                      b'BT /F1 12 Tf 20 150 Td <81828384> Tj 0 -20 Td <81828384> Tj ET')
    processor = DocumentProcessor()
    blocks = processor.extract(source)
    findings = findings_for(blocks, '山田太郎')
    assert len(findings) == 2
    output = tmp_path / 'masked.pdf'
    processor.mask(source, output, findings, {findings[0].id}, '█')
    assert PdfReader(output).pages[0].extract_text() == '████\n山田太郎'


def test_composite_font_byte_strings_and_unicode_offsets(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf',
                      b'BT /F1 12 Tf 20 150 Td [<00810082> -20 <0083008400850086>] TJ ET', composite=True)
    processor = DocumentProcessor()
    blocks = processor.extract(source)
    assert blocks[0].text == '山田太郎、係'
    findings = findings_for(blocks, '田太')
    output = tmp_path / 'masked.pdf'
    processor.mask(source, output, findings, {f.id for f in findings}, '█')
    assert PdfReader(output).pages[0].extract_text() == '山██郎、係'


@pytest.mark.parametrize('operator', [b"<81828384> '", b'4 2 <81828384> "'])
def test_quote_text_operators_are_decoded_and_masked(tmp_path, operator):
    source = make_pdf(tmp_path / 'source.pdf', b'BT /F1 12 Tf 20 150 Td 14 TL ' + operator + b' ET')
    processor = DocumentProcessor()
    findings = findings_for(processor.extract(source), '山田太郎')
    output = tmp_path / 'masked.pdf'
    processor.mask(source, output, findings, {f.id for f in findings}, '█')
    assert PdfReader(output).pages[0].extract_text().strip() == '████'
    ops = [op for _, op in PdfReader(output).pages[0].get_contents().operations]
    assert b'T*' in ops
    if b'"' in operator:
        assert b'Tw' in ops and b'Tc' in ops


def test_font_switch_and_graphics_state_are_tracked(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf',
                      b'BT /F1 12 Tf 20 150 Td <81> Tj q /F1 14 Tf <82> Tj Q <8384> Tj ET')
    assert ''.join(b.text for b in DocumentProcessor().extract(source)) == '山田太郎'


def test_missing_text_and_unsupported_fonts_fail_explicitly(tmp_path):
    source = make_pdf(tmp_path / 'empty.pdf', b'q Q')
    with pytest.raises(PdfProcessingError, match='OCR'):
        DocumentProcessor().extract(source)
    source = make_pdf(tmp_path / 'source.pdf', b'BT /F1 12 Tf <FF> Tj ET')
    with pytest.raises(PdfProcessingError, match='対応情報'):
        DocumentProcessor().extract(source)


def test_actual_text_is_not_silently_left_behind(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf',
                      b'/Span << /ActualText (secret) >> BDC BT /F1 12 Tf <81828384> Tj ET EMC')
    with pytest.raises(PdfProcessingError, match='ActualText'):
        DocumentProcessor().extract(source)


def test_stale_analysis_is_rejected(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf')
    finding = Finding(id='old', entity_type='PERSON', text='山田', start=0, end=2,
                      score=1, source='manual-selection', block_id='pdf:0:3:0')
    with pytest.raises(PdfProcessingError, match='再アップロード'):
        DocumentProcessor().mask(source, tmp_path / 'out.pdf', [finding], {'old'}, '█')


def test_pdf_upload_manual_selection_mask_and_download(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.database import DictionaryStore
    from app.documents import DocumentSessionStore

    monkeypatch.setattr(main, 'dictionary_store', DictionaryStore(tmp_path / 'db.sqlite3'))
    monkeypatch.setattr(main, 'session_store', DocumentSessionStore(tmp_path / 'sessions'))
    source = make_pdf(tmp_path / 'source.pdf')
    with TestClient(main.app) as client:
        response = client.post('/api/documents/analyze', files={'file': ('test.pdf', source.read_bytes(), 'application/pdf')},
                               data={'entities': 'EMAIL_ADDRESS'})
        assert response.status_code == 200
        analysis = response.json()
        assert analysis['blocks'][0]['text'] == '山田太郎、係'
        base = f"/api/documents/{analysis['session_id']}"
        response = client.post(base + '/findings/manual', json={
            'block_id': analysis['blocks'][0]['id'], 'start': 1, 'end': 3,
            'entity_type': 'PERSON', 'scope': 'single',
        })
        assert response.status_code == 200
        accepted = [f['id'] for f in response.json()['added_findings']]
        response = client.post(base + '/mask', json={'accepted_ids': accepted})
        assert response.status_code == 200
        download = client.get(response.json()['download_url'])
        assert download.status_code == 200
        assert PdfReader(BytesIO(download.content)).pages[0].extract_text() == '山██郎、係'


def test_partial_selection_of_a_ligature_removes_the_whole_glyph(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf', b'BT /F1 12 Tf 20 150 Td <8186> Tj ET',
                      mapping={0x81: '山田', 0x86: '係'})
    processor = DocumentProcessor()
    blocks = processor.extract(source)
    assert blocks[0].text == '山田係'
    findings = findings_for(blocks, '田')
    output = tmp_path / 'masked.pdf'
    processor.mask(source, output, findings, {f.id for f in findings}, '█')
    assert PdfReader(output).pages[0].extract_text() == '█係'


def test_large_kerning_gap_separates_words(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf',
                      b'BT /F1 12 Tf 20 150 Td [<8182> -500 <8384>] TJ ET')
    processor = DocumentProcessor()
    blocks = processor.extract(source)
    assert blocks[0].text == '山田 太郎'
    assert not findings_for(blocks, '山田太郎')


def test_api_reports_scanned_pdf_and_stale_analysis(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.database import DictionaryStore
    from app.documents import DocumentSessionStore

    monkeypatch.setattr(main, 'dictionary_store', DictionaryStore(tmp_path / 'db.sqlite3'))
    store = DocumentSessionStore(tmp_path / 'sessions')
    monkeypatch.setattr(main, 'session_store', store)
    empty = make_pdf(tmp_path / 'empty.pdf', b'q Q')
    with TestClient(main.app) as client:
        response = client.post('/api/documents/analyze', files={'file': ('empty.pdf', empty.read_bytes(), 'application/pdf')})
        assert response.status_code == 400
        assert 'OCR' in response.json()['detail']
        source = make_pdf(tmp_path / 'source.pdf')
        session, path = store.create('source.pdf', source.read_bytes())
        blocks = DocumentProcessor().extract(path)
        finding = findings_for(blocks, '山田太郎')[0]
        finding.block_id = 'pdf:0:3:0'
        store.save_analysis(session, blocks, [finding])
        response = client.post(f'/api/documents/{session}/mask', json={'accepted_ids': [finding.id]})
        assert response.status_code == 422
        assert '再アップロード' in response.json()['detail']
