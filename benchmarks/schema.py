from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models import EntityType


class GoldSpan(BaseModel):
    entity_type: EntityType
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


class DictionaryTerm(BaseModel):
    term: str = Field(min_length=1)
    entity_type: EntityType = "CUSTOM"


class BenchmarkSample(BaseModel):
    id: str = Field(min_length=1)
    language: Literal["ja"] = "ja"
    split: Literal["train", "dev", "test"] = "test"
    source: Literal["synthetic", "licensed", "internal"]
    generator_version: str | None = None
    template_id: str | None = None
    text: str
    entities: list[GoldSpan] = Field(default_factory=list)
    dictionary_terms: list[DictionaryTerm] = Field(default_factory=list)
    enabled_entities: list[EntityType] | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_spans(self) -> "BenchmarkSample":
        seen: set[tuple[int, int, str]] = set()
        for entity in self.entities:
            if entity.end > len(self.text):
                raise ValueError(
                    f"span {entity.start}:{entity.end} exceeds text length {len(self.text)}"
                )
            actual = self.text[entity.start : entity.end]
            if actual != entity.text:
                raise ValueError(
                    f"span text mismatch for {entity.entity_type}: "
                    f"expected {entity.text!r}, got {actual!r}"
                )
            key = (entity.start, entity.end, entity.entity_type)
            if key in seen:
                raise ValueError(f"duplicate span: {key}")
            seen.add(key)
        return self


def load_jsonl(path: str | Path) -> list[BenchmarkSample]:
    dataset_path = Path(path)
    samples: list[BenchmarkSample] = []
    ids: set[str] = set()
    with dataset_path.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                sample = BenchmarkSample.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{dataset_path}:{line_number}: {exc}") from exc
            if sample.id in ids:
                raise ValueError(
                    f"{dataset_path}:{line_number}: duplicate sample id {sample.id!r}"
                )
            ids.add(sample.id)
            samples.append(sample)
    if not samples:
        raise ValueError(f"dataset is empty: {dataset_path}")
    return samples
