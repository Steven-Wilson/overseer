import json

from overseer import cli
from overseer.model import Change, Hunk, Source
from overseer.planning import default_change
from overseer.store import Store


def test_record_updates_existing_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store()
    store.save_source(Source("s1", "Add X", [Hunk("a", "@@", ["+x"])], seq=1))
    store.save_change(default_change(store.load_source("s1")))
    assert cli.main(["record", "s1", "--question", "why", "--text", "because"]) == 0
    assert cli.main(["list"]) == 0
    (c,) = json.loads(capsys.readouterr().out)
    assert c["answers"] == {"why": "because"} and c["planned"] is False
    assert cli.main(["list", "--unplanned"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_record_unknown_change_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    assert cli.main(["record", "nope", "--title", "x"]) == 1
    assert "unknown change" in capsys.readouterr().err


def test_changes_ordered_by_source_then_position(tmp_path):
    store = Store(tmp_path)
    store.save_source(Source("late", "L", [Hunk("a", "@@")], seq=2))
    store.save_source(Source("early", "E", [Hunk("a", "@@"), Hunk("b", "@@")], seq=1))
    store.save_change(Change("late", "L", ["late:1"]))
    store.save_change(Change("early-02", "E2", ["early:2"], position=2))
    store.save_change(Change("early-01", "E1", ["early:1"], position=1))
    assert [c.id for c in store.changes()] == ["early-01", "early-02", "late"]
