"""Font-aware PDF text extraction with a reversible map to source glyphs.

The pypdf internals used here are covered by regression tests and the pinned
pypdf dependency. PDF string objects alone do not contain decoded page text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf._cmap import build_char_map, build_font_width_map
from pypdf._text_extraction._layout_mode._font_widths import STANDARD_WIDTHS
from pypdf.generic import ByteStringObject, TextStringObject

from app.models import DocumentBlock, Finding


class PdfProcessingError(ValueError):
    pass


@dataclass
class PdfFont:
    name: str
    encoding: Any
    cmap: dict
    dictionary: Any
    widths: dict
    code_size: int

    @classmethod
    def load(cls, name: str, page: Any) -> PdfFont:
        subtype, _, encoding, cmap, font = build_char_map(name, 200, page)
        if subtype == "/Type0" and font.get("/Encoding") != "/Identity-H":
            raise PdfProcessingError("このPDFの複合フォントは未対応です。横書きのPDFとして再出力してください。")
        if subtype == "/Type3" and ("/ToUnicode" not in font or
                                   list(font.get("/FontMatrix", [])) != [0.001, 0, 0, 0.001, 0, 0]):
            raise PdfProcessingError("このPDFのType3フォントは未対応です。通常のフォントで再出力してください。")
        if isinstance(encoding, str) and encoding not in {"charmap", "utf-16-be"}:
            raise PdfProcessingError("このPDFの文字コードは未対応です。別の設定でPDFを再出力してください。")
        return cls(name, encoding, cmap, font, build_font_width_map(font, 0),
                   2 if subtype == "/Type0" else 1)

    def decode(self, raw: bytes) -> list[tuple[bytes, str]]:
        if len(raw) % self.code_size:
            raise PdfProcessingError("PDFの文字コードを正しく読み取れませんでした。")
        result = []
        for offset in range(0, len(raw), self.code_size):
            code = raw[offset:offset + self.code_size]
            key = (self.encoding.get(code[0], chr(code[0]))
                   if isinstance(self.encoding, dict)
                   else code.decode(self.encoding, errors="surrogatepass"))
            if self.cmap and key not in self.cmap:
                raise PdfProcessingError("PDFの文字と日本語の対応情報が不足しています。PDFを再出力してください。")
            text = self.cmap.get(key, key)
            if not text or any(0xD800 <= ord(char) <= 0xDFFF for char in text):
                raise PdfProcessingError("PDFに読み取れない文字が含まれています。")
            result.append((code, text))
        return result

    def width(self, raw: bytes, text: str) -> float:
        key = chr(int.from_bytes(raw, "big"))
        if key in self.widths:
            return float(self.widths[key])
        base = str(self.dictionary.get("/BaseFont", "")).lstrip("/")
        metric_name = {"Helvetica-Oblique": "Helvetica", "Times-Roman": "Times"}.get(base, base)
        if base.startswith("Courier"):
            return 600.0
        metrics = STANDARD_WIDTHS.get(metric_name)
        if metrics and text in metrics:
            return float(metrics[text])
        if self.code_size == 2 or "/MissingWidth" in self.descriptor():
            return float(self.widths["default"])
        raise PdfProcessingError("PDFの文字幅を取得できないため、配置を保ってマスクできません。")

    def descriptor(self) -> Any:
        font = self.dictionary
        if self.code_size == 2:
            font = font["/DescendantFonts"][0].get_object()
        descriptor = font.get("/FontDescriptor")
        return descriptor.get_object() if descriptor is not None else {}


@dataclass
class Glyph:
    operation: int
    item: int
    byte_start: int
    raw: bytes
    text: str
    start: int
    end: int
    font: PdfFont
    font_size: Any


@dataclass
class PdfLine:
    block: DocumentBlock
    glyphs: list[Glyph] = field(default_factory=list)


@dataclass
class PdfPage:
    page: Any
    stream: Any
    lines: list[PdfLine]


def _raw_string(value: Any) -> bytes:
    return value.original_bytes if isinstance(value, TextStringObject) else bytes(value)


def read_pdf(path: Path) -> tuple[PdfReader, list[PdfPage]]:
    reader = PdfReader(path)
    pages = [_read_page(page, index) for index, page in enumerate(reader.pages)]
    if not any(page.lines for page in pages):
        raise PdfProcessingError("読み取れる文字がありません。スキャンPDF・画像PDFにはOCRが必要です。")
    return reader, pages


def _read_page(page: Any, page_index: int) -> PdfPage:
    stream = page.get_contents()
    lines: list[PdfLine] = []
    if stream is None:
        return PdfPage(page, stream, lines)
    fonts: dict[str, PdfFont] = {}
    font = None
    font_size = None
    stack = []
    line = None
    # A positioning operation ends the logical line; kerning within TJ does not.
    boundaries = {b"BT", b"ET", b"Tm", b"Td", b"TD", b"T*", b"'", b'"', b"Do", b"q", b"Q", b"cm"}
    for operation, (operands, operator) in enumerate(stream.operations):
        if operator in boundaries:
            line = None
        if operator == b"q":
            stack.append((font, font_size))
        elif operator == b"Q" and stack:
            font, font_size = stack.pop()
        elif operator == b"Tf":
            name = str(operands[0])
            if name not in fonts:
                fonts[name] = PdfFont.load(name, page)
            font, font_size = fonts[name], operands[1]
        elif operator == b"BDC":
            properties = operands[1]
            if isinstance(properties, str):
                properties = page["/Resources"].get("/Properties", {}).get(properties, {})
            if hasattr(properties, "get_object"):
                properties = properties.get_object()
            if "/ActualText" in properties:
                raise PdfProcessingError("代替テキスト（ActualText）を含むPDFは未対応です。別の設定でPDFを再出力してください。")
        elif operator == b"Do":
            obj = page["/Resources"]["/XObject"][operands[0]]
            if obj.get("/Subtype") == "/Form":
                raise PdfProcessingError("Form XObjectを含むPDFは未対応です。通常のページとしてPDFを再出力してください。")
        elif operator in {b"Tj", b"TJ", b"'", b'"'}:
            if font is None:
                raise PdfProcessingError("PDFのフォント情報が不足しています。")
            values = operands[0] if operator == b"TJ" else [operands[-1]]
            for item_index, value in enumerate(values):
                if not isinstance(value, (TextStringObject, ByteStringObject)):
                    if line is not None and float(value) <= -250 and not line.block.text.endswith(" "):
                        line.block.text += " "
                    continue
                if line is None:
                    line = PdfLine(DocumentBlock(id=f"pdf:{page_index}:line:{operation}", text=""))
                    lines.append(line)
                offset = 0
                for raw, text in font.decode(_raw_string(value)):
                    start = len(line.block.text)
                    line.block.text += text
                    line.glyphs.append(Glyph(operation, item_index, offset, raw, text,
                                             start, len(line.block.text), font, font_size))
                    offset += len(raw)
    return PdfPage(page, stream, [line for line in lines if line.block.text.strip()])


def extract_pdf(path: Path) -> list[DocumentBlock]:
    _, pages = read_pdf(path)
    return [line.block for page in pages for line in page.lines]


def mask_pdf(source: Path, output: Path, by_block: dict[str, list[Finding]]) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

    _, pages = read_pdf(source)
    known = {line.block.id for page in pages for line in page.lines}
    if by_block.keys() - known:
        raise PdfProcessingError("PDFの解析情報が古いため、ファイルを再アップロードしてください。")
    writer = PdfWriter()
    for page in pages:
        selected: dict[tuple[int, int, int], Glyph] = {}
        for line in page.lines:
            for finding in by_block.get(line.block.id, []):
                if (finding.end > len(line.block.text) or finding.start >= finding.end or
                        line.block.text[finding.start:finding.end] != finding.text):
                    raise PdfProcessingError("PDFの選択範囲が解析結果と一致しません。再解析してください。")
                for glyph in line.glyphs:
                    if glyph.start < finding.end and glyph.end > finding.start:
                        selected[glyph.operation, glyph.item, glyph.byte_start] = glyph
        if selected:
            resources = DictionaryObject(page.page["/Resources"])
            fonts = DictionaryObject(resources["/Font"])
            resources[NameObject("/Font")] = fonts
            page.page[NameObject("/Resources")] = resources
            masks: dict[tuple[float, float, float], str] = {}
            operations = []
            selected_strings: dict[tuple[int, int], list[Glyph]] = {}
            for key, glyph in selected.items():
                selected_strings.setdefault(key[:2], []).append(glyph)
            selected_operations = {key[0] for key in selected}
            for index, (operands, operator) in enumerate(page.stream.operations):
                if index not in selected_operations:
                    operations.append((operands, operator))
                    continue
                # Expand shorthand show operators, retaining their text-state effects.
                if operator == b'"':
                    operations.extend([([operands[0]], b"Tw"), ([operands[1]], b"Tc"), ([], b"T*")])
                elif operator == b"'":
                    operations.append(([], b"T*"))
                values = operands[0] if operator == b"TJ" else [operands[-1]]
                for item_index, value in enumerate(values):
                    if not isinstance(value, (TextStringObject, ByteStringObject)):
                        operations.append(([ArrayObject([value])], b"TJ"))
                        continue
                    raw = _raw_string(value)
                    cursor = 0
                    for glyph in sorted(selected_strings.get((index, item_index), []),
                                        key=lambda glyph: glyph.byte_start):
                        if cursor < glyph.byte_start:
                            operations.append(([ByteStringObject(raw[cursor:glyph.byte_start])], b"Tj"))
                        width = glyph.font.width(glyph.raw, glyph.text)
                        descriptor = glyph.font.descriptor()
                        ascent = float(descriptor.get("/Ascent", 850))
                        descent = float(descriptor.get("/Descent", -150))
                        dimensions = width, ascent, descent
                        if dimensions not in masks:
                            name = f"/KVMask{len(masks)}"
                            while name in fonts:
                                name += "_"
                            fonts[NameObject(name)] = _mask_font(writer, *dimensions)
                            masks[dimensions] = name
                        # q/Q restore the original font and rendering state, while
                        # the text matrix advances by the original glyph width.
                        # Only a one-byte space receives PDF word spacing.
                        code = b" " if glyph.raw == b" " else b"\x01"
                        operations.extend([
                            ([], b"q"),
                            ([NameObject(masks[dimensions]), glyph.font_size], b"Tf"),
                            ([NumberObject(0)], b"Tr"),
                            ([ByteStringObject(code)], b"Tj"),
                            ([], b"Q"),
                        ])
                        cursor = glyph.byte_start + len(glyph.raw)
                    if cursor < len(raw):
                        operations.append(([ByteStringObject(raw[cursor:])], b"Tj"))
            page.stream.operations = operations
            page.page.replace_contents(page.stream)
        writer.add_page(page.page)
    # Rebuild the file, rather than appending an incremental revision that keeps
    # the original text stream recoverable in an earlier version.
    with output.open("wb") as handle:
        writer.write(handle)


def _mask_font(writer: Any, width: float, ascent: float, descent: float) -> Any:
    from pypdf.generic import (
        ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
        NameObject, NumberObject,
    )

    glyph = DecodedStreamObject()
    glyph.set_data(
        f"{width:g} 0 d0\n0 g\n0 {descent:g} {width:g} {ascent - descent:g} re f\n".encode("ascii")
    )
    cmap = DecodedStreamObject()
    cmap.set_data(b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                  b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                  b"/CMapName /KVMask def\n/CMapType 2 def\n"
                  b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
                  b"2 beginbfchar\n<01> <2588>\n<20> <2588>\nendbfchar\n"
                  b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type3"),
        NameObject("/FontBBox"): ArrayObject([FloatObject(v) for v in [0, descent, width, ascent]]),
        NameObject("/FontMatrix"): ArrayObject([FloatObject(v) for v in [0.001, 0, 0, 0.001, 0, 0]]),
        NameObject("/CharProcs"): DictionaryObject({NameObject("/mask"): writer._add_object(glyph)}),
        NameObject("/Encoding"): DictionaryObject({
            NameObject("/Type"): NameObject("/Encoding"),
            NameObject("/Differences"): ArrayObject([NumberObject(1), NameObject("/mask"),
                                                     NumberObject(32), NameObject("/mask")]),
        }),
        NameObject("/FirstChar"): NumberObject(1),
        NameObject("/LastChar"): NumberObject(32),
        NameObject("/Widths"): ArrayObject([FloatObject(width) for _ in range(32)]),
        NameObject("/Resources"): DictionaryObject(),
        NameObject("/ToUnicode"): writer._add_object(cmap),
    })
    return writer._add_object(font)
