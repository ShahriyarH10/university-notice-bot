import json

import pytest

from notice_bot.state import SeenStore, StateError


def make_store(tmp_path, **kwargs) -> SeenStore:
    return SeenStore(tmp_path / "state.json", **kwargs)


def test_missing_file_reports_not_existing(tmp_path):
    assert not make_store(tmp_path).exists


def test_round_trip_preserves_order(tmp_path):
    store = make_store(tmp_path)
    for url in ("https://a", "https://b", "https://c"):
        store.add(url)
    store.save()

    reloaded = make_store(tmp_path)
    reloaded.load()
    assert json.loads(store.path.read_text()) == ["https://a", "https://b", "https://c"]
    assert "https://b" in reloaded
    assert "https://z" not in reloaded
    assert len(reloaded) == 3


def test_add_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    store.add("https://a")
    store.add("https://a")
    assert len(store) == 1


def test_load_removes_duplicates(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('["https://a", "https://b", "https://a"]')
    store = SeenStore(path)
    store.load()
    assert len(store) == 2


def test_save_drops_oldest_entries_beyond_limit(tmp_path):
    store = make_store(tmp_path, max_entries=3)
    for i in range(5):
        store.add(f"https://n/{i}")
    store.save()
    assert json.loads(store.path.read_text()) == ["https://n/2", "https://n/3", "https://n/4"]
    assert "https://n/0" not in store


def test_save_leaves_no_temp_file_behind(tmp_path):
    store = make_store(tmp_path)
    store.add("https://a")
    store.save()
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


@pytest.mark.parametrize("content", ["not json", '{"a": 1}', "[1, 2]", ""])
def test_corrupt_file_raises_instead_of_reading_as_empty(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content)
    with pytest.raises(StateError):
        SeenStore(path).load()
