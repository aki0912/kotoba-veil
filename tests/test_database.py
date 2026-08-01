import sqlite3

import pytest

from app.database import DictionaryStore
from app.models import DictionaryCreate


def test_dictionary_crud_and_uniqueness(tmp_path) -> None:
    store = DictionaryStore(tmp_path / "test.sqlite3")
    created = store.create(DictionaryCreate(term="極秘案件", entity_type="CUSTOM"))

    assert store.list() == [created]
    with pytest.raises(sqlite3.IntegrityError):
        store.create(DictionaryCreate(term="極秘案件", entity_type="CUSTOM"))
    assert store.delete(created.id)
    assert not store.delete(created.id)
    assert store.list() == []


def test_dictionary_get_or_create_is_idempotent_and_normalizes_values(tmp_path) -> None:
    store = DictionaryStore(tmp_path / "test.sqlite3")

    created, was_created = store.get_or_create(
        DictionaryCreate(term="  極秘案件  ", entity_type="CUSTOM", note="  手動  ")
    )
    existing, was_created_again = store.get_or_create(
        DictionaryCreate(term="極秘案件", entity_type="CUSTOM", note="別のメモ")
    )

    assert was_created is True
    assert was_created_again is False
    assert created == existing
    assert created.term == "極秘案件"
    assert created.note == "手動"
