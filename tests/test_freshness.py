from overseer.freshness import freshness, line_hashes, take_baseline
from overseer.model import Change, Delta, FileDiff, Source
from overseer.store import Store
from overseer.tui.app import OverseerApp


def src(sid, *bodies, seq=1):
    files = [
        FileDiff(f"{b}.py", [f"@@ -{i},1 +{i},1 @@", f"+{b}"])
        for i, b in enumerate(bodies)
    ]
    return Source(sid, "msg", files, seq=seq)


def d(sid, body):
    """The delta covering the one changed line `src` makes in `<body>.py`."""
    return Delta(sid, f"{body}.py", "1")


def labels(app):
    return [str(x.render()) for x in app.query("#changes Static")]


def test_line_identity_ignores_header_line_numbers_but_not_content():
    a = FileDiff("a.py", ["@@ -1,2 +1,3 @@ def f", "+x"])
    moved = FileDiff("a.py", ["@@ -40,2 +41,3 @@ def f", " ctx", "+x"])
    assert line_hashes(a) == line_hashes(moved)
    assert line_hashes(a) != line_hashes(FileDiff("b.py", a.lines))
    assert line_hashes(a) != line_hashes(FileDiff("a.py", ["@@ -1 +1 @@", "+y"]))
    twice = line_hashes(FileDiff("a.py", ["@@ -1 +1 @@", "+x", "+x"]))
    assert twice[0] != twice[1]  # identical text is told apart by occurrence


def test_kinds_new_changed_updated_and_no_baseline():
    s1 = src("s1", "a", "b")
    sources = {"s1": s1}
    c = Change(
        "c1", "T", [d("s1", "a"), d("s1", "b")], answers={"why": "w"}, risk="green"
    )
    assert freshness(c, sources, None).kind == ""  # no baseline: flag nothing
    snap = take_baseline([c], sources)
    assert freshness(c, sources, snap).kind == ""

    # the change gains a delta from a new commit
    both = {"s1": s1, "s2": src("s2", "c", seq=2)}
    grown = Change(
        "c1",
        "T",
        [d("s1", "a"), d("s1", "b"), d("s2", "c")],
        answers={"why": "w"},
        risk="green",
    )
    f = freshness(grown, both, snap)
    assert (f.kind, f.new_deltas, f.total, f.tag) == ("changed", [3], 3, "CHANGED +1")

    assert (
        freshness(Change("c9", "Brand new", [d("s2", "c")]), both, snap).kind == "new"
    )

    # same code, different explanation or risk
    same_code = Change(
        "c1", "T", [d("s1", "a"), d("s1", "b")], answers={"why": "w2"}, risk="red"
    )
    assert freshness(same_code, sources, snap).kind == "updated"


def test_regrouping_or_recutting_deltas_does_not_look_new():
    sources = {"s1": src("s1", "a", "b")}
    snap = take_baseline(
        [Change("old-01", "All", [d("s1", "a"), d("s1", "b")])], sources
    )
    split = [
        Change("new-01", "A", [d("s1", "a")]),
        Change("new-02", "B", [d("s1", "b")]),
    ]
    assert [freshness(c, sources, snap).kind for c in split] == ["", ""]

    # one file with two changed lines, reviewed as one delta, later cut in two
    two = {"s1": Source("s1", "m", [FileDiff("f.py", ["@@ -1 +1 @@", "+p", "+q"])])}
    whole = Change("w", "W", [Delta("s1", "f.py", "1-2")])
    snap = take_baseline([whole], two)
    halves = Change("h", "H", [Delta("s1", "f.py", "1"), Delta("s1", "f.py", "2")])
    assert freshness(halves, two, snap).kind == ""


async def test_tui_flags_changes_after_mark_reviewed(tmp_path):
    store = Store(tmp_path, "pr")
    store.save_source(src("s1", "a", "b"))
    store.save_change(
        Change("c1", "One", [d("s1", "a")], planned=True, risk="red", risk_reason="r")
    )
    store.save_change(Change("c2", "Two", [d("s1", "b")], planned=True, risk="green"))
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not any("NEW" in x or "CHANGED" in x for x in labels(app))  # no baseline
        await pilot.press("R")
        await pilot.pause()
        assert store.reviewed() is not None
        assert not any("UPDATED" in x or "CHANGED" in x for x in labels(app))

        # the agent pushes more: a new commit extends c2, and c1's rating changes
        store.save_source(src("s2", "c", seq=2))
        store.save_change(
            Change(
                "c2", "Two", [d("s1", "b"), d("s2", "c")], planned=True, risk="green"
            )
        )
        store.save_change(
            Change(
                "c1",
                "One",
                [d("s1", "a")],
                planned=True,
                risk="yellow",
                risk_reason="r",
            )
        )
        await app.action_refresh()
        await pilot.pause()
        first, second = labels(app)  # yellow sorts above green
        assert "UPDATED" in first and "CHANGED +1" in second
        assert "2 changed since review" in app.query_one("#changes").border_title

        await pilot.press("j")  # c2, then step to its new delta
        await pilot.press("right_square_bracket", "right_square_bracket")
        await pilot.pause()
        t = str(app.query_one("#detail").render())
        assert "CHANGED since your last review: delta 2 of 2 new" in t
        assert (
            "Delta 2/2  c.py" in t and "NEW" in t.split("Delta 2/2")[1].splitlines()[0]
        )
        await pilot.press("left_square_bracket")
        await pilot.pause()
        header = str(app.query_one("#detail").render()).split("Delta 1/2")[1]
        assert "NEW" not in header.splitlines()[0]  # the old delta is not flagged


async def test_done_key_takes_baseline_and_hidden_changes_are_counted(tmp_path):
    store = Store(tmp_path, "pr")
    store.save_source(src("s1", "a", "t"))
    store.save_change(Change("c1", "Code", [d("s1", "a")], planned=True, risk="green"))
    store.save_change(
        Change(
            "c2", "Tests", [d("s1", "t")], planned=True, risk="white", risk_reason="t"
        )
    )
    store.set_review("requested", "q")
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert store.reviewed() is not None and store.review()["state"] == "done"

        store.save_change(
            Change(
                "c2",
                "Tests",
                [d("s1", "t")],
                planned=True,
                risk="white",
                risk_reason="x",
            )
        )
        await app.action_refresh()
        await pilot.pause()
        title = app.query_one("#changes").border_title
        assert "1 white hidden (1 changed)" in title  # not lost behind t
        await pilot.press("t")
        await pilot.pause()
        assert "UPDATED" in labels(app)[0]


def test_when_formats_the_baseline_and_tolerates_junk():
    from overseer.freshness import when

    assert when("2026-11-04T04:50:45+00:00")[:3] == "Nov"
    assert when("not a time") == "" and when("") == ""


def test_freshness_carries_the_baseline_time():
    sources = {"s1": src("s1", "a")}
    c = Change("c1", "T", [d("s1", "a")])
    snap = take_baseline([c], sources)
    assert freshness(c, sources, snap).since == snap["at"]
