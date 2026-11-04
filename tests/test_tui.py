from overseer.model import Change, Hunk, Source
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
        assert len(app.query("ListItem")) == 2
        t = text(app)
        assert "Part one" in t and "because" in t
        assert "body [bracketed]" in t and "b.py" in t and "Hunk 1/1" in t


async def test_hunk_navigation_clamps(tmp_path):
    store = Store(tmp_path)
    seed(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # Part two: two hunks
        await pilot.pause()
        assert "Hunk 1/2" in text(app) and "+new [x]" in text(app)
        await pilot.press("n")
        assert "Hunk 2/2" in text(app) and "b.py" in text(app)
        await pilot.press("n")
        assert "Hunk 2/2" in text(app)
        await pilot.press("p")
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
        await pilot.press("down")
        await pilot.pause()
        assert "unrated" in text(app)
