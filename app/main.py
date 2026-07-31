from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.database import DictionaryStore
from app.detectors import ENTITY_CATALOG, JapanesePiiEngine, apply_mask
from app.documents import DocumentProcessor, DocumentSessionStore, UnsupportedDocumentError
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    DictionaryCreate,
    DictionaryEntry,
    DocumentAnalyzeResponse,
    DocumentMaskRequest,
    DocumentMaskResponse,
    MaskRequest,
    MaskResponse,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("KOTOBA_VEIL_DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = BASE_DIR / "app" / "static"

dictionary_store = DictionaryStore(DATA_DIR / "kotoba-veil.sqlite3")
session_store = DocumentSessionStore(DATA_DIR / "sessions")
engine = JapanesePiiEngine()
document_processor = DocumentProcessor()

app = FastAPI(
    title="Kotoba Veil",
    description="完全ローカルで動作する日本語PIIレビュー・マスクAPI",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"status": "ok", "nlp_available": engine.nlp_available, "local_only": True}


@app.get("/api/entities")
def entities() -> list[dict[str, str]]:
    return ENTITY_CATALOG


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    findings = engine.analyze(
        request.text,
        request.entities,
        dictionary_store.list(),
    )
    return AnalyzeResponse(
        text=request.text,
        findings=findings,
        nlp_available=engine.nlp_available,
    )


@app.post("/api/mask", response_model=MaskResponse)
def mask(request: MaskRequest) -> MaskResponse:
    accepted = set(request.accepted_ids)
    return MaskResponse(
        masked_text=apply_mask(request.text, request.findings, accepted, request.mask_character)
    )


@app.get("/api/dictionary", response_model=list[DictionaryEntry])
def list_dictionary() -> list[DictionaryEntry]:
    return dictionary_store.list()


@app.post("/api/dictionary", response_model=DictionaryEntry, status_code=201)
def create_dictionary(entry: DictionaryCreate) -> DictionaryEntry:
    try:
        return dictionary_store.create(entry)
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="同じ語がすでに登録されています。") from error


@app.delete("/api/dictionary/{entry_id}", status_code=204, response_class=Response)
def delete_dictionary(entry_id: int) -> Response:
    if not dictionary_store.delete(entry_id):
        raise HTTPException(status_code=404, detail="辞書項目が見つかりません。")
    return Response(status_code=204)


@app.post("/api/documents/analyze", response_model=DocumentAnalyzeResponse)
async def analyze_document(
    file: UploadFile = File(...),
    entities: str | None = Form(default=None),
) -> DocumentAnalyzeResponse:
    filename = file.filename or "document"
    try:
        content = await file.read()
        session_id, source = session_store.create(filename, content)
        blocks = document_processor.extract(source)
        enabled = [item for item in (entities or "").split(",") if item] or None
        findings = document_processor.analyze_blocks(
            blocks, engine, enabled, dictionary_store.list()
        )
        session_store.save_analysis(session_id, blocks, findings)
    except (UnsupportedDocumentError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail="文書を解析できませんでした。ファイルが破損または暗号化されていないか確認してください。",
        ) from error
    return DocumentAnalyzeResponse(
        session_id=session_id,
        filename=filename,
        file_type=source.suffix.lstrip("."),
        blocks=blocks,
        findings=findings,
        nlp_available=engine.nlp_available,
    )


@app.post(
    "/api/documents/{session_id}/mask",
    response_model=DocumentMaskResponse,
)
def mask_document(session_id: str, request: DocumentMaskRequest) -> DocumentMaskResponse:
    try:
        metadata, _, findings = session_store.load(session_id)
        source = session_store.source_path(session_id, metadata)
        output = session_store.output_path(session_id, metadata["extension"])
        known_ids = {finding.id for finding in findings}
        accepted = set(request.accepted_ids) & known_ids
        document_processor.mask(source, output, findings, accepted, request.mask_character)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="文書セッションが見つかりません。") from error
    except Exception as error:
        raise HTTPException(status_code=422, detail="文書のマスク処理に失敗しました。") from error
    filename = f"masked_{metadata['original_filename']}"
    return DocumentMaskResponse(
        filename=filename,
        download_url=f"/api/documents/{session_id}/download",
    )


@app.get("/api/documents/{session_id}/download")
def download_document(session_id: str) -> FileResponse:
    try:
        metadata, _, _ = session_store.load(session_id)
        output = session_store.output_path(session_id, metadata["extension"])
        if not output.is_file():
            raise FileNotFoundError(session_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="出力ファイルが見つかりません。") from error
    return FileResponse(output, filename=f"masked_{metadata['original_filename']}")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
