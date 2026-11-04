import json
import threading
import time

import pytest

from overseer import cli
from overseer.model import Change, Delta, FileDiff, Note, Source
from overseer.store import Store
from overseer.tui.app import OverseerApp

A = Delta("s1", "a.py", "1-2")
B = Delta("s1", "b.py", "1")


def seed(store):
    store.save_source(
        Source(
            "s1",
            "Commit",
            [
                FileDiff("a.py", ["@@ -1 +1 @@", "-old", "+new"]),
                FileDiff("b.py", ["@@ -0,0 +1 @@", "+y"]),
            ],
            seq=1,
        )
    )
    store.save_change(Change("s1-01", "Part one", [A, B], planned=True))


def run(argv, capsys):
    code = cli.main(argv)
    return code, capsys.readouterr()


def test_notes_json_has_context_and_stale_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store(reviewset="pr")
    seed(store)
    for n in (
        Note("n1", "delta", B.id, "why a new file?"),
        Note("n2", "change", "s1-01", "split this"),
        Note("n3", "reviewset", "", "overall fine"),
        Note("n4", "change", "gone-99", "orphan"),
    ):
        store.save_note(n)
    code, out = run(["notes", "--reviewset", "pr"], capsys)
    notes = {n["id"]: n for n in json.loads(out.out)}
    assert code == 0 and list(notes) == ["n1", "n2", "n3", "n4"]
    assert notes["n1"]["context"]["path"] == "b.py"
    assert notes["n1"]["context"]["diff"] == ["+y"]
    assert notes["n1"]["context"]["new_lines"] == [1, 1]
    assert notes["n1"]["context"]["changed_lines"] == "1"
    assert notes["n2"]["context"]["change_title"] == "Part one"
    assert notes["n3"]["context"] == {}
    assert notes["n4"]["context"] == {"stale": True}


def test_resolve_marks_addressed_and_filters(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store(reviewset="pr")
    store.save_note(Note("n1", "reviewset", "", "a"))
    store.save_note(Note("n2", "reviewset", "", "b"))
    assert (
        run(["resolve", "n1", "--reply", "done", "--reviewset", "pr"], capsys)[0] == 0
    )
    assert run(["resolve", "n9", "--reviewset", "pr"], capsys)[0] == 1
    (only,) = json.loads(run(["notes", "--open", "--reviewset", "pr"], capsys)[1].out)
    assert only["id"] == "n2"
    n1 = store.load_note("n1")
    assert (n1.status, n1.reply) == ("addressed", "done")


def test_review_wait_blocks_until_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    store = Store(reviewset="pr")
    seed(store)
    assert run(["review", "wait", "--reviewset", "pr"], capsys)[0] == 1  # no request
    assert (
        run(["review", "request", "--message", "q?", "--reviewset", "pr"], capsys)[0]
        == 0
    )
    assert store.review() == {"state": "requested", "message": "q?"}
    assert (
        run(["review", "wait", "--timeout", "0.1", "--reviewset", "pr"], capsys)[0] == 3
    )

    def human():
        time.sleep(0.3)
        store.save_note(Note("n1", "reviewset", "", "please add tests"))
        store.set_review("done", "q?")

    t = threading.Thread(target=human)
    t.start()
    code, out = run(["review", "wait", "--timeout", "10", "--reviewset", "pr"], capsys)
    t.join()
    assert code == 0
    assert [n["text"] for n in json.loads(out.out)] == ["please add tests"]


def test_notes_are_per_reviewset_and_deleted_with_it(tmp_path):
    a, b = Store(tmp_path, "a"), Store(tmp_path, "b")
    a.save_note(Note("n1", "reviewset", "", "x"))
    assert b.notes() == [] and b.next_note_id() == "n1" and a.next_note_id() == "n2"
    a.delete_reviewset("a")
    assert a.notes() == []


async def test_tui_adds_notes_at_each_level(tmp_path):
    store = Store(tmp_path, "pr")
    seed(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right_square_bracket", "right_square_bracket")  # delta 2
        await pilot.press("n")
        await pilot.press(*"why [new] file?", "enter")
        await pilot.press("N")
        await pilot.press(*"split", "enter")
        await pilot.press("m")
        await pilot.press(*"overall", "enter")
        await pilot.press("N")
        await pilot.press(*"discard me", "escape")  # cancel saves nothing
        await pilot.pause()
        notes = store.notes()
        assert [(n.kind, n.ref, n.text) for n in notes] == [
            ("delta", B.id, "why [new] file?"),
            ("change", "s1-01", "split"),
            ("reviewset", "", "overall"),
        ]
        t = str(app.query_one("#detail").render())
        assert "why [new] file?" in t and "Delta 2/2" in t  # note kept the delta
        label = str(app.query("#changes Static").first().render())
        assert "✎2" in label
        await pilot.press("left_square_bracket")  # keys work again afterwards
        assert "Delta 1/2" in str(app.query_one("#detail").render())
        assert "why [new] file?" not in str(app.query_one("#detail").render())
        await pilot.press("left_square_bracket")  # overview carries the top notes
        t = str(app.query_one("#detail").render())
        assert "split" in t and "overall" in t and "Delta 1/2" not in t


async def test_tui_shows_agent_reply_and_done_key(tmp_path):
    store = Store(tmp_path, "pr")
    seed(store)
    store.save_note(
        Note("n1", "change", "s1-01", "split", status="addressed", reply="split it")
    )
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = str(app.query_one("#detail").render())
        assert "agent: split it" in t
        await pilot.press("D")  # no review requested: nothing happens
        assert store.review()["state"] == "idle"
        store.set_review("requested", "any thoughts?")
        await app.action_refresh()
        assert "REVIEW REQUESTED" in app.sub_title and "any thoughts?" in app.sub_title
        await pilot.press("D")
        await pilot.pause()
        assert store.review()["state"] == "done"
        assert "REVIEW REQUESTED" not in app.sub_title


@pytest.mark.parametrize("key", ["n", "N"])
async def test_note_keys_without_changes_do_nothing(tmp_path, key):
    app = OverseerApp(Store(tmp_path, "empty"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(key)
        assert not app.query_one("#note-input").has_class("open")


async def test_addressed_notes_stay_visible(tmp_path):
    store = Store(tmp_path, "pr")
    seed(store)
    store.save_note(Note("n1", "delta", A.id, "q", status="addressed", reply="a"))
    store.save_note(Note("n2", "change", "s1-01", "open one"))
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        label = str(app.query("#changes Static").first().render())
        assert "✎1" in label and "✓1" in label
        store.save_note(Note("n2", "change", "s1-01", "open one", status="addressed"))
        await app.action_refresh()
        label = str(app.query("#changes Static").first().render())
        assert "✎" not in label and "✓2" in label
