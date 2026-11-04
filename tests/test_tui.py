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
