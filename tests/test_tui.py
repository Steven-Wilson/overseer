from overseer.model import Change
from overseer.store import Store
from overseer.tui.app import OverseerApp


async def test_app_shows_changes(tmp_path):
    store = Store(tmp_path)
    store.save(Change(id="c1", title="Add X"))
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query("ListItem")) == 1


async def test_hunk_navigation_and_message(tmp_path):
    from overseer.model import Hunk

    store = Store(tmp_path)
    store.save(
        Change(
            id="c1",
            title="T",
            message="T\n\nbody [bracketed]",
            hunks=[
                Hunk("a.py", "@@ -1 +1 @@", ["-old", "+new [x]"]),
                Hunk("b.py", "@@ -1 +1 @@", ["-o2", "+n2"]),
            ],
        )
    )
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = str(app.query_one("#detail").render())
        assert "body [bracketed]" in text and "Hunk 1/2" in text and "+new [x]" in text
        await pilot.press("n")
        text = str(app.query_one("#detail").render())
        assert "Hunk 2/2" in text and "b.py" in text
        await pilot.press("n")  # clamps at the end
        assert "Hunk 2/2" in str(app.query_one("#detail").render())
