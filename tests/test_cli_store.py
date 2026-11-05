import json

from conftest import chg, dele, ins, mov  # noqa: F401

from overseer import cli
from overseer.model import Change, FileDiff, Source
from overseer.planning import default_change
from overseer.store import Store


def test_record_updates_existing_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store()
    store.save_source(
        Source("s1", "Add X", [FileDiff("a", ["@@ -1 +1 @@", "+x"])], seq=1)
    )
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
                "--reviewset",
                "default",
            ]
        )
        == 0
    )
    assert cli.main(["list", "--reviewset", "default"]) == 0
    (c,) = json.loads(capsys.readouterr().out)
    assert c["answers"] == {"why": "because"} and c["planned"] is False
    assert cli.main(["list", "--unplanned", "--reviewset", "default"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_record_unknown_change_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    Store().create_reviewset("default")
    assert cli.main(["record", "nope", "--title", "x", "--reviewset", "default"]) == 1
    assert "unknown change 'nope'" in capsys.readouterr().err


def test_changes_ordered_by_source_then_position(tmp_path):
    store = Store(tmp_path)
    store.save_source(Source("late", "L", [FileDiff("a", ["+x"])], seq=2))
    store.save_source(
        Source("early", "E", [FileDiff("a", ["+x"]), FileDiff("b", ["+y"])], seq=1)
    )
    store.save_change(Change("late", "L", [ins("late", "a", "1")]))
    store.save_change(Change("early-02", "E2", [ins("early", "b", "1")], position=2))
    store.save_change(Change("early-01", "E1", [ins("early", "a", "1")], position=1))
    assert [c.id for c in store.changes()] == ["early-01", "early-02", "late"]


def test_record_risk(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store()
    store.save_change(Change("c1", "t", []))
    assert cli.main(["record", "c1", "--risk", "red", "--reviewset", "default"]) == 1
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
                "--reviewset",
                "default",
            ]
        )
        == 0
    )
    assert store.load_change("c1").risk == "red"
    assert (
        cli.main(["record", "c1", "--risk", "green", "--reviewset", "default"]) == 0
    )  # reason may stay


def test_link_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store()
    store.save_change(Change("c1", "t", [ins("s", "a.py", "1")]))
    assert (
        cli.main(
            ["link", "c1", "src/a.py:12", "--note", "caller", "--reviewset", "default"]
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
                "--reviewset",
                "default",
            ]
        )
        == 0
    )
    delta = "s:insert@a.py:1"
    assert (
        cli.main(
            ["link", "c1", "README.md", "--delta", delta, "--reviewset", "default"]
        )
        == 0
    )
    c = store.load_change("c1")
    assert [(x.path, x.line, x.note) for x in c.links] == [
        ("src/a.py", 12, "better note")
    ]
    assert c.deltas[0].links[0].line is None
    assert (
        cli.main(["link", "c1", "x", "--delta", "s:a.py#9", "--reviewset", "default"])
        == 1
    )
    assert cli.main(["link", "nope", "x", "--reviewset", "default"]) == 1
