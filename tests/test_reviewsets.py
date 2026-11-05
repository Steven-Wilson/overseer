import pytest
from conftest import chg, dele, ins, mov  # noqa: F401

from overseer import cli
from overseer.model import Change, FileDiff, Source
from overseer.store import Store
from overseer.tui.app import OverseerApp


def put(store, sid, title):
    store.save_source(Source(sid, title, [FileDiff("a.py", ["+x"])], seq=1))
    store.save_change(Change(sid, title, [ins(sid, "a.py")]))


def test_reviewsets_are_isolated(tmp_path):
    local = Store(tmp_path, "local")
    pr = Store(tmp_path, "pr-7")
    put(local, "aaa", "mine")
    put(pr, "aaa", "theirs")  # same sha in both, no collision
    assert [c.title for c in local.changes()] == ["mine"]
    assert [c.title for c in pr.changes()] == ["theirs"]
    assert Store(tmp_path).reviewset_names() == ["default", "local", "pr-7"]


def test_unpinned_store_follows_active_pointer(tmp_path):
    store = Store(tmp_path)
    assert store.name == "default"
    put(Store(tmp_path, "pr-7"), "bbb", "theirs")
    store.set_active("pr-7")
    assert store.name == "pr-7" and [c.id for c in store.changes()] == ["bbb"]
    store.delete_reviewset("pr-7")
    assert store.name == "default" and store.changes() == []


@pytest.mark.parametrize("bad", ["../x", "a/b", ".hidden", "", "a b"])
def test_invalid_names_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        Store(tmp_path, bad)
    with pytest.raises(ValueError):
        Store(tmp_path).create_reviewset(bad)


def test_cli_reviewset_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    put(Store(reviewset="local"), "ccc", "mine")
    assert cli.main(["reviewset", "new", "pr-7"]) == 0  # does not switch
    assert Store().name == "default"
    assert cli.main(["reviewset", "use", "local"]) == 0
    assert cli.main(["list", "--reviewset", "local"]) == 0
    assert '"id": "ccc"' in capsys.readouterr().out
    assert cli.main(["list", "--reviewset", "pr-7"]) == 0
    assert "ccc" not in capsys.readouterr().out
    assert cli.main(["reviewset", "list"]) == 0  # pointer is still on local
    out = capsys.readouterr().out
    assert "* local  (1 changes)" in out and "  pr-7" in out
    assert cli.main(["reviewset", "new", "pr-7"]) == 1  # exists
    assert cli.main(["reviewset", "use", "nope"]) == 1
    assert cli.main(["show", "ccc", "--reviewset", "nope"]) == 1  # no implicit create
    assert cli.main(["reviewset", "delete", "local"]) == 0
    assert Store().name == "default"


def test_reviewset_flag_is_required(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    put(Store(reviewset="local"), "ccc", "mine")
    Store().set_active("local")  # even with an active pointer, no implicit target
    for argv in (["list"], ["show", "ccc"], ["ingest", "HEAD"], ["record", "ccc"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 2
        assert "--reviewset" in capsys.readouterr().err


async def test_tui_switches_and_remembers_selection(tmp_path):
    a, b = Store(tmp_path, "a"), Store(tmp_path, "b")
    put(a, "a1", "Alpha one")
    put(b, "b1", "Beta one")
    store = Store(tmp_path)
    store.set_active("a")
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Alpha one" in str(app.query_one("#detail").render())
        assert "reviewset: a" in app.sub_title
        await pilot.press("c")
        await pilot.pause()
        assert len(app.query("#reviewsets ListItem")) == 2
        await pilot.press("j", "enter")  # a -> b
        await pilot.pause()
        assert store.name == "b"
        assert "Beta one" in str(app.query_one("#detail").render())
        assert "reviewset: b" in app.sub_title
        assert app.focused.id == "changes"
        # pointer moved from outside (CLI) is followed too
        store.set_active("a")
        await app.action_refresh()
        await pilot.pause()
        assert "Alpha one" in str(app.query_one("#detail").render())


async def test_tui_empty_reviewset_clears_list(tmp_path):
    put(Store(tmp_path, "a"), "a1", "Alpha one")
    Store(tmp_path).create_reviewset("empty")
    store = Store(tmp_path)
    store.set_active("a")
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        store.set_active("empty")
        await app.action_refresh()
        await pilot.pause()
        assert len(app.query("#changes ListItem")) == 0


async def _open_picker_on(pilot, app, name):
    await pilot.press("c")
    await pilot.pause()
    picker = app.query_one("#reviewsets")
    while picker.highlighted_child.reviewset != name:
        await pilot.press("j")
    return picker


async def test_tui_delete_needs_y(tmp_path):
    put(Store(tmp_path, "a"), "a1", "Alpha one")
    put(Store(tmp_path, "b"), "b1", "Beta one")
    store = Store(tmp_path)
    store.set_active("a")
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = await _open_picker_on(pilot, app, "b")
        await pilot.press("d")
        assert picker.has_class("confirm") and "'b'" in picker.border_title
        await pilot.press("n")  # any other key cancels
        await pilot.pause()
        assert not picker.has_class("confirm") and store.has_reviewset("b")
        await pilot.press("d", "h")  # h must cancel, not also change focus
        await pilot.pause()
        assert store.has_reviewset("b") and app.focused.id == "reviewsets"
        await pilot.press("d", "y")
        await pilot.pause()
        assert not store.has_reviewset("b") and store.name == "a"
        assert len(app.query("#reviewsets ListItem")) == 1


async def test_tui_delete_viewed_reviewset_falls_back(tmp_path):
    put(Store(tmp_path, "a"), "a1", "Alpha one")
    store = Store(tmp_path)
    store.set_active("a")
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _open_picker_on(pilot, app, "a")
        await pilot.press("d", "y")
        await pilot.pause()
        assert not store.has_reviewset("a") and store.name == "default"
        assert "reviewset: default" in app.sub_title
        assert len(app.query("#changes ListItem")) == 0


async def test_d_outside_picker_does_nothing(tmp_path):
    put(Store(tmp_path, "a"), "a1", "Alpha one")
    store = Store(tmp_path)
    store.set_active("a")
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d", "y")
        await pilot.pause()
        assert store.has_reviewset("a")
