from overseer.model import Change, Hunk, Link, Source
from overseer.store import Store
from overseer.tui.app import OverseerApp


def seed(store):
    store.save_source(
        Source(
            "s1",
            "Commit title\n\nbody [bracketed]",
            [
                Hunk("a.py", "@@ -1 +1 @@", ["-old", "+new [x]"]),
                Hunk("b.py", "@@ -1 +1 @@", ["-o2", "+n2"]),
            ],
            seq=1,
        )
    )
    store.save_change(
        Change(
            "s1-01",
            "Part one",
            ["s1:2"],
            {"why": "because"},
            planned=True,
            risk="red",
            risk_reason="core fn",
        )
    )
    store.save_change(Change("s1-02", "Part two", ["s1:1", "s1:2"], planned=True))


def text(app):
    return str(app.query_one("#detail").render())


async def test_app_lists_changes_and_shows_context(tmp_path):
    store = Store(tmp_path)
    seed(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query("#changes ListItem")) == 2
        t = text(app)
        assert "Part one" in t and "because" in t
        assert "body [bracketed]" in t and "b.py" in t and "Hunk 1/1" in t


async def test_hunk_navigation_clamps(tmp_path):
    store = Store(tmp_path)
    seed(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j")  # Part two: two hunks
        await pilot.pause()
        assert "Hunk 1/2" in text(app) and "+new [x]" in text(app)
        await pilot.press("right_square_bracket")
        assert "Hunk 2/2" in text(app) and "b.py" in text(app)
        await pilot.press("right_square_bracket")
        assert "Hunk 2/2" in text(app)
        await pilot.press("left_square_bracket")
        assert "Hunk 1/2" in text(app)


async def test_unplanned_change_is_flagged(tmp_path):
    store = Store(tmp_path)
    seed(store)
    store.save_change(Change("s1", "Commit title", ["s1:1", "s1:2"]))
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j")  # red "Part one" sorts first; then the unrated ones
        await pilot.pause()
        assert "Not yet decomposed" in text(app)


async def test_panes_are_side_by_side(tmp_path):
    app = OverseerApp(Store(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        left = app.query_one("#changes").region
        right = app.query_one("#detail-pane").region
        assert left.right <= right.x and left.y == right.y


async def test_risk_is_shown(tmp_path):
    store = Store(tmp_path)
    seed(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "RED" in text(app) and "core fn" in text(app)
        await pilot.press("j")
        await pilot.pause()
        assert "unrated" in text(app)


def seed_links(store):
    c = store.load_change("s1-02")
    c.hunk_links = {
        "s1:1": [Link("a.py", 3, "caller")],
        "s1:2": [Link("b.py", 9, "test")],
    }
    c.links = [Link("README.md", None, "docs")]
    store.save_change(c)


def link_labels(app):
    return [str(i.query_one("Static").render()) for i in app.query("#links ListItem")]


async def test_vim_pane_switching_and_links_follow_hunk(tmp_path):
    store = Store(tmp_path)
    seed(store)
    seed_links(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused.id == "changes"
        await pilot.press("j")  # Part two
        await pilot.pause()
        labels = link_labels(app)
        assert labels[0].startswith("This hunk") and "a.py:3" in labels[1]
        assert labels[-2:] == ["This change", "README.md  docs"]
        await pilot.press("right_square_bracket")  # second hunk, other links
        await pilot.pause()
        assert "b.py:9" in link_labels(app)[1]
        await pilot.press("l")
        assert app.focused.id == "links"
        await pilot.press("j")  # skips the "This change" header
        assert app.query_one("#links").highlighted_child.link.path == "README.md"
        await pilot.press("k")
        assert app.query_one("#links").highlighted_child.link.path == "b.py"
        await pilot.press("h")
        assert app.focused.id == "changes"


async def test_enter_opens_editor_at_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "b.py").write_text("x\n")
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.delenv("VISUAL", raising=False)
    store = Store(tmp_path / ".ov")
    seed(store)
    seed_links(store)
    app = OverseerApp(store)
    ran = []
    app.run_editor = lambda cmd, gui: ran.append((cmd, gui))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j", "right_square_bracket", "l", "enter")
        assert ran == [(["vim", "+9", "b.py"], False)]


async def test_enter_reports_missing_editor_and_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    store = Store(tmp_path / ".ov")
    seed(store)
    seed_links(store)
    app = OverseerApp(store)
    ran = []
    app.run_editor = lambda cmd, gui: ran.append(cmd)
    notes = []
    app.notify = lambda msg, **kw: notes.append(msg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j", "l", "enter")
        assert "EDITOR" in notes[-1]
        monkeypatch.setenv("VISUAL", "nano")  # VISUAL used when EDITOR unset
        await pilot.press("enter")
        assert "not found" in notes[-1] and not ran


def test_by_priority_is_stable_within_a_tier():
    from overseer.model import by_priority

    def c(cid, risk):
        return Change(cid, cid, [], risk=risk)

    order = [
        c("a", "green"),
        c("b", ""),
        c("c", "red"),
        c("d", "yellow"),
        c("e", "red"),
        c("f", "green"),
        c("g", "yellow"),
    ]
    assert [x.id for x in by_priority(order)] == list("cedgbaf")
    assert [x.id for x in order] == list("abcdefg")  # input untouched


async def test_list_is_sorted_by_priority_and_shows_size_and_notes(tmp_path):
    store = Store(tmp_path)
    store.save_source(
        Source(
            "s1",
            "Commit",
            [
                Hunk("a.py", "@@", ["+1"]),
                Hunk("a.py", "@@", ["+2"]),
                Hunk("b.py", "@@", ["+3"]),
            ],
            seq=1,
        )
    )
    store.save_change(
        Change("c1", "Safe tidy", ["s1:3"], planned=True, risk="green", position=1)
    )
    store.save_change(
        Change(
            "c2",
            "Core logic",
            ["s1:1", "s1:2", "s1:3"],
            planned=True,
            risk="red",
            risk_reason="core",
            position=2,
        )
    )
    store.save_change(
        Change(
            "c3",
            "Middling",
            ["s1:1"],
            planned=True,
            risk="yellow",
            risk_reason="x",
            position=3,
        )
    )
    from overseer.model import Note

    store.save_note(Note("n1", "hunk", "s1:1", "q"))
    store.save_note(Note("n2", "change", "c2", "r", status="addressed"))
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        labels = [str(s.render()) for s in app.query("#changes Static")]
        assert [x.splitlines()[0][2:] for x in labels] == [
            "Core logic",
            "Middling",
            "Safe tidy",
        ]
        assert all(len(x.splitlines()) == 2 for x in labels)
        assert labels[0].splitlines()[1].strip() == "3 hunks · 2 files · ✎1 · ✓1"
        assert labels[1].splitlines()[1].strip() == "1 hunk · 1 file · ✎1"
        assert labels[2].splitlines()[1].strip() == "1 hunk · 1 file"
        # selection follows the id, not the position, when the order changes
        await pilot.press("j")
        assert app._current().id == "c3"
        store.save_change(
            Change(
                "c3",
                "Middling",
                ["s1:1"],
                planned=True,
                risk="red",
                risk_reason="x",
                position=3,
            )
        )
        await app.action_refresh()
        await pilot.pause()
        assert app._current().id == "c3"
        assert [c.id for c in app._changes] == ["c2", "c3", "c1"]


async def test_long_titles_do_not_grow_past_two_lines(tmp_path):
    store = Store(tmp_path)
    seed(store)
    store.save_change(
        Change("s1-03", "T" * 200, ["s1:1"], planned=True, risk="green", position=3)
    )
    for width in (60, 120):
        app = OverseerApp(store)
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause()
            heights = [i.size.height for i in app.query("#changes ListItem")]
            assert heights and set(heights) == {2}


def seed_white(store):
    store.save_source(
        Source(
            "w1",
            "Mixed",
            [Hunk("a.py", "@@", ["+x"]), Hunk("t.py", "@@", ["+t"])],
            seq=1,
        )
    )
    store.save_change(
        Change("w1-01", "Code", ["w1:1"], planned=True, risk="green", position=1)
    )
    store.save_change(
        Change(
            "w1-02",
            "Red code",
            ["w1:1"],
            planned=True,
            risk="red",
            risk_reason="r",
            position=2,
        )
    )
    store.save_change(
        Change(
            "w1-03",
            "Tests: a",
            ["w1:2"],
            planned=True,
            risk="white",
            risk_reason="tests only",
            position=3,
        )
    )
    store.save_change(
        Change(
            "w1-04",
            "Docs",
            ["w1:2"],
            planned=True,
            risk="white",
            risk_reason="docs only",
            position=4,
        )
    )


async def test_white_changes_hidden_until_t_swaps_views(tmp_path):
    store = Store(tmp_path)
    seed_white(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert [c.id for c in app._changes] == ["w1-02", "w1-01"]
        assert "2 tests/text hidden" in app.query_one("#changes").border_title
        await pilot.press("j")  # remember the second code change
        await pilot.press("t")
        await pilot.pause()
        assert [c.id for c in app._changes] == ["w1-03", "w1-04"]
        assert "back to code" in app.query_one("#changes").border_title
        assert app._current().id == "w1-03"
        await pilot.press("j", "t")
        await pilot.pause()
        assert app._current().id == "w1-01"  # selection restored per view
        await pilot.press("t")
        await pilot.pause()
        assert app._current().id == "w1-04"


async def test_only_white_changes_gives_a_hint(tmp_path):
    store = Store(tmp_path)
    store.save_source(Source("d1", "Docs", [Hunk("README.md", "@@", ["+x"])], seq=1))
    store.save_change(
        Change(
            "d1-01", "Docs", ["d1:1"], planned=True, risk="white", risk_reason="docs"
        )
    )
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "press t" in text(app)
        await pilot.press("t")
        await pilot.pause()
        assert app._current().id == "d1-01"


def test_white_sorts_last_and_needs_a_reason(tmp_path, monkeypatch, capsys):
    from overseer import cli
    from overseer.model import by_priority

    order = [
        Change(i, i, [], risk=r)
        for i, r in [("a", "white"), ("b", "green"), ("c", ""), ("d", "red")]
    ]
    assert [c.id for c in by_priority(order)] == ["d", "c", "b", "a"]

    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    Store().save_change(Change("c1", "t", []))
    argv = ["record", "c1", "--risk", "white", "--changeset", "default"]
    assert cli.main(argv) == 1
    assert "white needs --risk-reason" in capsys.readouterr().err
    assert cli.main([*argv, "--risk-reason", "tests only"]) == 0


def test_plural_uses_the_lookup_table():
    import pytest

    from overseer.tui.app import PLURALS, _plural

    assert [_plural(n, "file") for n in (0, 1, 2)] == ["0 files", "1 file", "2 files"]
    assert _plural(1, "hunk") == "1 hunk" and _plural(3, "change") == "3 changes"
    assert set(PLURALS) >= {"hunk", "file", "change"}
    with pytest.raises(KeyError):  # unknown words must be added, not guessed
        _plural(2, "mouse")
