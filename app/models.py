from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EntityType = Literal[
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "ADDRESS",
    "PHONE_NUMBER",
    "POSTAL_CODE",
    "EMAIL_ADDRESS",
    "PERSONAL_ID",
    "DRIVER_LICENSE",
    "BANK_ACCOUNT",
    "CREDIT_CARD",
    "DATE_TIME",
    "URL",
    "IP_ADDRESS",
    "CUSTOM",
]


class Finding(BaseModel):
    id: str
    entity_type: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    score: float = Field(ge=0, le=1)
    source: str
    block_id: str = "text"


class AnalyzeRequest(BaseModel):
    text: str = Field(max_length=500_000)
    entities: list[str] | None = None


class AnalyzeResponse(BaseModel):
    text: str
    findings: list[Finding]
    nlp_available: bool


class MaskRequest(BaseModel):
    text: str = Field(max_length=500_000)
    findings: list[Finding]
    accepted_ids: list[str]
    mask_character: str = Field(default="█", min_length=1, max_length=1)


class MaskResponse(BaseModel):
    masked_text: str


class DictionaryCreate(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    entity_type: str = "CUSTOM"
    note: str = Field(default="", max_length=500)


class DictionaryEntry(DictionaryCreate):
    id: int
    created_at: str


class DocumentBlock(BaseModel):
    id: str
    text: str


class DocumentAnalyzeResponse(BaseModel):
    session_id: str
    filename: str
    file_type: str
    blocks: list[DocumentBlock]
    findings: list[Finding]
    nlp_available: bool


class DocumentMaskRequest(BaseModel):
    accepted_ids: list[str]
    mask_character: str = Field(default="█", min_length=1, max_length=1)


class DocumentMaskResponse(BaseModel):
    filename: str
    download_url: str

