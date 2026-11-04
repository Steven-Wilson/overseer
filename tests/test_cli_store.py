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
    assert (
        cli.main(
            [
                "record",
                "s1",
                "--question",
                "why",
                "--text",
                "because",
                "--changeset",
                "default",
            ]
        )
        == 0
    )
    assert cli.main(["list", "--changeset", "default"]) == 0
    (c,) = json.loads(capsys.readouterr().out)
    assert c["answers"] == {"why": "because"} and c["planned"] is False
    assert cli.main(["list", "--unplanned", "--changeset", "default"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_record_unknown_change_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    assert cli.main(["record", "nope", "--title", "x", "--changeset", "default"]) == 1
    assert "unknown change" in capsys.readouterr().err


def test_changes_ordered_by_source_then_position(tmp_path):
    store = Store(tmp_path)
    store.save_source(Source("late", "L", [Hunk("a", "@@")], seq=2))
    store.save_source(Source("early", "E", [Hunk("a", "@@"), Hunk("b", "@@")], seq=1))
    store.save_change(Change("late", "L", ["late:1"]))
    store.save_change(Change("early-02", "E2", ["early:2"], position=2))
    store.save_change(Change("early-01", "E1", ["early:1"], position=1))
    assert [c.id for c in store.changes()] == ["early-01", "early-02", "late"]


def test_legacy_inline_change_is_migrated(tmp_path):
    legacy = {
        "id": "old1234",
        "title": "Old",
        "message": "Old\n\nbody",
        "hunks": [{"path": "a", "header": "@@", "lines": ["+x"]}],
        "answers": {"why": "w"},
    }
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / "old1234.json").write_text(json.dumps(legacy))
    store = Store(tmp_path)
    (change,) = store.changes()
    assert change.hunk_ids == ["old1234:1"] and change.answers == {"why": "w"}
    assert store.load_source("old1234").message == "Old\n\nbody"
    assert len(store.changes()) == 1  # idempotent


def test_record_risk(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store()
    store.save_change(Change("c1", "t", []))
    assert cli.main(["record", "c1", "--risk", "red", "--changeset", "default"]) == 1
    assert "needs --risk-reason" in capsys.readouterr().err
    assert (
        cli.main(
            [
                "record",
                "c1",
                "--risk",
                "red",
                "--risk-reason",
                "core",
                "--changeset",
                "default",
            ]
        )
        == 0
    )
    assert store.load_change("c1").risk == "red"
    assert (
        cli.main(["record", "c1", "--risk", "green", "--changeset", "default"]) == 0
    )  # reason may stay


def test_link_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store()
    store.save_change(Change("c1", "t", ["s:1"]))
    assert (
        cli.main(
            ["link", "c1", "src/a.py:12", "--note", "caller", "--changeset", "default"]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "link",
                "c1",
                "src/a.py:12",
                "--note",
                "better note",
                "--changeset",
                "default",
            ]
        )
        == 0
    )
    assert (
        cli.main(["link", "c1", "README.md", "--hunk", "s:1", "--changeset", "default"])
        == 0
    )
    c = store.load_change("c1")
    assert [(x.path, x.line, x.note) for x in c.links] == [
        ("src/a.py", 12, "better note")
    ]
    assert c.hunk_links["s:1"][0].line is None
    assert cli.main(["link", "c1", "x", "--hunk", "s:9", "--changeset", "default"]) == 1
    assert cli.main(["link", "nope", "x", "--changeset", "default"]) == 1
