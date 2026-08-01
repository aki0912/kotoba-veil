from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.models import DictionaryCreate, DictionaryEntry


class DictionaryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pii_dictionary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term TEXT NOT NULL COLLATE NOCASE,
                    entity_type TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(term, entity_type)
                )
                """
            )

    def list(self) -> list[DictionaryEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, term, entity_type, note, created_at "
                "FROM pii_dictionary ORDER BY term"
            ).fetchall()
        return [DictionaryEntry(**dict(row)) for row in rows]

    def create(self, entry: DictionaryCreate) -> DictionaryEntry:
        normalized = DictionaryCreate(
            term=entry.term.strip(),
            entity_type=entry.entity_type,
            note=entry.note.strip(),
        )
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO pii_dictionary(term, entity_type, note, created_at) "
                "VALUES (?, ?, ?, ?)",
                (normalized.term, normalized.entity_type, normalized.note, created_at),
            )
            entry_id = int(cursor.lastrowid)
        return DictionaryEntry(id=entry_id, created_at=created_at, **normalized.model_dump())

    def get_or_create(self, entry: DictionaryCreate) -> tuple[DictionaryEntry, bool]:
        normalized = DictionaryCreate(
            term=entry.term.strip(),
            entity_type=entry.entity_type,
            note=entry.note.strip(),
        )
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO pii_dictionary(term, entity_type, note, created_at) "
                "VALUES (?, ?, ?, ?)",
                (normalized.term, normalized.entity_type, normalized.note, created_at),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                "SELECT id, term, entity_type, note, created_at FROM pii_dictionary "
                "WHERE term = ? COLLATE NOCASE AND entity_type = ?",
                (normalized.term, normalized.entity_type),
            ).fetchone()
        if row is None:  # pragma: no cover - guarded by the insert/select transaction
            raise sqlite3.IntegrityError("辞書項目を保存できませんでした。")
        return DictionaryEntry(**dict(row)), created

    def delete(self, entry_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM pii_dictionary WHERE id = ?", (entry_id,)
            )
        return cursor.rowcount > 0
