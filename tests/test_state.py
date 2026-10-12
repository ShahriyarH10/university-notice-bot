import json

import pytest

from notice_bot.state import SeenStore, StateError


def make_store(tmp_path, **kwargs) -> SeenStore:
    return SeenStore(tmp_path / "state.json", **kwargs)


def urls(store: SeenStore) -> list[str]:
    return [e["url"] for e in json.loads(store.path.read_text())]


def test_missing_file_reports_not_existing(tmp_path):
    assert not make_store(tmp_path).exists


def test_round_trip_preserves_order_and_details(tmp_path):
    store = make_store(tmp_path)
    store.add("https://a", title="A", date="1 Oct 2026", seen_at="2026-10-12 16:14:33")
    store.add("https://b")
    store.save()

    assert json.loads(store.path.read_text()) == [
        {
            "url": "https://a",
            "title": "A",
            "date": "1 Oct 2026",
            "seen_at": "2026-10-12 16:14:33",
        },
        {"url": "https://b"},
    ]
    reloaded = make_store(tmp_path)
    reloaded.load()
    assert "https://a" in reloaded
    assert "https://z" not in reloaded
    assert len(reloaded) == 2


def test_unicode_titles_are_written_readably(tmp_path):
    store = make_store(tmp_path)
    store.add("https://a", title="Fall 2026\u201327")
    store.save()
    assert "Fall 2026\u201327" in store.path.read_text(encoding="utf-8")


def test_old_format_of_bare_urls_is_read_and_upgraded(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('["https://old1", "https://old2"]')
    store = SeenStore(path)
    store.load()
    store.add("https://new", title="New", seen_at="2026-10-12 16:14:33")
    store.save()

    assert json.loads(path.read_text()) == [
        {"url": "https://old1"},
        {"url": "https://old2"},
        {"url": "https://new", "title": "New", "seen_at": "2026-10-12 16:14:33"},
    ]


def test_add_is_idempotent_and_keeps_the_first_record(tmp_path):
    store = make_store(tmp_path)
    store.add("https://a", seen_at="first")
    store.add("https://a", seen_at="second")
    store.save()
    assert len(store) == 1
    assert json.loads(store.path.read_text()) == [{"url": "https://a", "seen_at": "first"}]


def test_load_removes_duplicates(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('["https://a", {"url": "https://b"}, "https://a"]')
    store = SeenStore(path)
    store.load()
    assert len(store) == 2


def test_save_drops_oldest_entries_beyond_limit(tmp_path):
    store = make_store(tmp_path, max_entries=3)
    for i in range(5):
        store.add(f"https://n/{i}")
    store.save()
    assert urls(store) == ["https://n/2", "https://n/3", "https://n/4"]
    assert "https://n/0" not in store


def test_save_leaves_no_temp_file_behind(tmp_path):
    store = make_store(tmp_path)
    store.add("https://a")
    store.save()
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"a": 1}',
        "[1, 2]",
        "",
        '[{"title": "no url"}]',
        '[{"url": "https://a", "seen_at": 5}]',
    ],
)
def test_corrupt_file_raises_instead_of_reading_as_empty(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content)
    with pytest.raises(StateError):
        SeenStore(path).load()
