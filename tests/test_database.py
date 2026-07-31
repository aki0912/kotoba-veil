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

