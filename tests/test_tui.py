from overseer.model import Change, Delta, FileDiff, Link, Source
from overseer.store import Store, resolve_deltas
from overseer.tui.app import OverseerApp, render_change

A = Delta("s1", "a.py", "1-2")
B = Delta("s1", "b.py", "1-2")


def seed(store):
    store.save_source(
        Source(
            "s1",
            "Commit title\n\nbody [bracketed]",
            [
                FileDiff("a.py", ["@@ -1 +1 @@", "-old", "+new [x]"]),
                FileDiff("b.py", ["@@ -1 +1 @@", "-o2", "+n2"]),
            ],
            seq=1,
        )
    )
    store.save_change(
        Change(
            "s1-01",
            "Part one",
            [B],
            {"why": "because"},
            planned=True,
            risk="red",
            risk_reason="core fn",
        )
    )
    store.save_change(Change("s1-02", "Part two", [A, B], planned=True))


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
        assert "body [bracketed]" in t and "Delta 1/1" not in t  # commit message here
        await pilot.press("right_square_bracket")
        t = text(app)
        assert "body [bracketed]" not in t  # not repeated per hunk
        assert "+n2" in t and "b.py" in t and "Delta 1/1" in t


async def test_delta_navigation_clamps(tmp_path):
    store = Store(tmp_path)
    seed(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j")  # Part two: two deltas
        await pilot.pause()
        assert "Delta 1/2" not in text(app)  # starts on the overview
        await pilot.press("left_square_bracket")  # clamps at the overview
        assert "Deltas, in review order" in text(app)
        await pilot.press("right_square_bracket")
        assert "Delta 1/2" in text(app) and "+new [x]" in text(app)
        await pilot.press("right_square_bracket")
        assert "Delta 2/2" in text(app) and "b.py" in text(app)
        await pilot.press("right_square_bracket")
        assert "Delta 2/2" in text(app)
        await pilot.press("left_square_bracket")
        assert "Delta 1/2" in text(app)
        await pilot.press("left_square_bracket")
        assert "Deltas, in review order" in text(app)


async def test_unplanned_change_is_flagged(tmp_path):
    store = Store(tmp_path)
    seed(store)
    store.save_change(Change("s1", "Commit title", [A, B]))
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
    c.deltas[0].links = [Link("a.py", 3, "caller")]
    c.deltas[1].links = [Link("b.py", 9, "test")]
    c.links = [Link("README.md", None, "docs")]
    store.save_change(c)


def link_labels(app):
    return [str(i.query_one("Static").render()) for i in app.query("#links ListItem")]


async def test_vim_pane_switching_and_links_follow_delta(tmp_path):
    store = Store(tmp_path)
    seed(store)
    seed_links(store)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused.id == "changes"
        await pilot.press("j")  # Part two
        await pilot.pause()
        assert link_labels(app)[0] == "This change"  # overview: no delta yet
        await pilot.press("right_square_bracket")
        await pilot.pause()
        labels = link_labels(app)
        assert labels[0].startswith("This delta") and "a.py:3" in labels[1]
        assert labels[-2:] == ["This change", "README.md  docs"]
        await pilot.press("right_square_bracket")  # second delta, other links
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
        await pilot.press(
            "j", "right_square_bracket", "right_square_bracket", "l", "enter"
        )
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


D1 = Delta("s1", "a.py", "1")
D2 = Delta("s1", "a.py", "2")
D3 = Delta("s1", "b.py", "1")


async def test_list_is_sorted_by_priority_and_shows_size_and_notes(tmp_path):
    store = Store(tmp_path)
    store.save_source(
        Source(
            "s1",
            "Commit",
            [
                FileDiff("a.py", ["@@ -1 +1 @@", "+1", "@@ -9 +9 @@", "+2"]),
                FileDiff("b.py", ["@@ -1 +1 @@", "+3"]),
            ],
            seq=1,
        )
    )
    store.save_change(
        Change("c1", "Safe tidy", [D3], planned=True, risk="green", position=1)
    )
    store.save_change(
        Change(
            "c2",
            "Core logic",
            [D1, D2, D3],
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
            [D1],
            planned=True,
            risk="yellow",
            risk_reason="x",
            position=3,
        )
    )
    from overseer.model import Note

    store.save_note(Note("n1", "delta", D1.id, "q"))
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
        assert labels[0].splitlines()[1].strip() == "3 deltas · 2 files · ✎1 · ✓1"
        assert labels[1].splitlines()[1].strip() == "1 delta · 1 file · ✎1"
        assert labels[2].splitlines()[1].strip() == "1 delta · 1 file"
        # selection follows the id, not the position, when the order changes
        await pilot.press("j")
        assert app._current().id == "c3"
        store.save_change(
            Change(
                "c3",
                "Middling",
                [D1],
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
        Change("s1-03", "T" * 200, [A], planned=True, risk="green", position=3)
    )
    for width in (60, 120):
        app = OverseerApp(store)
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause()
            heights = [i.size.height for i in app.query("#changes ListItem")]
            assert heights and set(heights) == {2}


WA = Delta("w1", "a.py", "1")
WT = Delta("w1", "t.py", "1")


def seed_white(store):
    store.save_source(
        Source(
            "w1",
            "Mixed",
            [
                FileDiff("a.py", ["@@ -1 +1 @@", "+x"]),
                FileDiff("t.py", ["@@ -1 +1 @@", "+t"]),
            ],
            seq=1,
        )
    )
    store.save_change(
        Change("w1-01", "Code", [WA], planned=True, risk="green", position=1)
    )
    store.save_change(
        Change(
            "w1-02",
            "Red code",
            [WA],
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
            [WT],
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
            [WT],
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
        assert "2 white hidden" in app.query_one("#changes").border_title
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
    store.save_source(
        Source("d1", "Docs", [FileDiff("README.md", ["@@ -1 +1 @@", "+x"])], seq=1)
    )
    store.save_change(
        Change(
            "d1-01",
            "Docs",
            [Delta("d1", "README.md", "1")],
            planned=True,
            risk="white",
            risk_reason="docs",
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
    argv = ["record", "c1", "--risk", "white", "--reviewset", "default"]
    assert cli.main(argv) == 1
    assert "white needs --risk-reason" in capsys.readouterr().err
    assert cli.main([*argv, "--risk-reason", "tests only"]) == 0


def test_plural_uses_the_lookup_table():
    import pytest

    from overseer.tui.app import PLURALS, _plural

    assert [_plural(n, "file") for n in (0, 1, 2)] == ["0 files", "1 file", "2 files"]
    assert _plural(1, "delta") == "1 delta" and _plural(3, "change") == "3 changes"
    assert set(PLURALS) >= {"delta", "file", "change"}
    with pytest.raises(KeyError):  # unknown words must be added, not guessed
        _plural(2, "mouse")


async def test_delta_summaries_listed_in_overview_and_shown_per_delta(tmp_path):
    store = Store(tmp_path)
    seed(store)
    c = store.load_change("s1-02")
    c.deltas[0].summary = "defines the thing"
    c.deltas[1].summary = "uses the thing"
    c.answers = {"why": "GLOBAL-WHY"}
    store.save_change(c)
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j")
        t = text(app)
        assert "GLOBAL-WHY" in t and "defines the thing" in t and "uses the thing" in t
        await pilot.press("right_square_bracket")
        t = text(app)
        assert "defines the thing" in t and "uses the thing" not in t
        assert "GLOBAL-WHY" not in t  # the change-wide text is not repeated per delta


EDIT = FileDiff(
    "x.py",
    [
        "@@ -10,5 +10,5 @@ def foo():",
        " keep",
        "-if a:",
        "+if a and b:",
        " mid",
        "-x = 1",
        "+x = 2",
        " tail",
    ],
)


def span_texts(store, change_id, index, style_part):
    """Text covered by styles that mention `style_part` in that delta's view
    (checked on rich's `Text`; Textual re-encodes styles once it renders)."""
    change = store.load_change(change_id)
    t = render_change(change, resolve_deltas(change, store.sources()), index)
    return [t.plain[sp.start : sp.end] for sp in t.spans if style_part in str(sp.style)]


async def test_edit_view_has_a_gutter_dims_other_deltas_and_marks_the_words(tmp_path):
    store = Store(tmp_path)
    store.save_source(Source("s9", "m", [EDIT], seq=1))
    store.save_change(
        Change(
            "s9-01",
            "Guard",
            [Delta("s9", "x.py", "1-2"), Delta("s9", "x.py", "3-4")],
            planned=True,
            risk="green",
        )
    )
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right_square_bracket")
        t = text(app)
        assert "Delta 1/2  x.py  +1 −1" in t and "section" not in t
        assert "(dim lines belong to other deltas)" in t
        assert "⋯  def foo():" in t  # the enclosing function, from git's header
        rows = [line.split() for line in t.splitlines()]
        assert ["10", "10", "keep"] in rows  # old and new line numbers
        assert ["11", "-if", "a:"] in rows  # removal: old number only
        assert ["11", "+if", "a", "and", "b:"] in rows  # addition: new number only
        # just the words that changed are emphasised, not the whole line
        assert "".join(span_texts(store, "s9-01", 0, "dark_green")).strip() == "and b"


SECTION = FileDiff(
    "m.py",
    [
        "@@ -1,2 +1,6 @@",
        " import os",
        "+def helper(x):",
        '+    return "[ok]" if x else None',
        "+",
        " print(os)",
    ],
)


async def test_added_section_is_highlighted_with_an_accent_bar(tmp_path):
    store = Store(tmp_path)
    store.save_source(Source("s8", "m", [SECTION], seq=1))
    store.save_change(
        Change(
            "s8-01", "Helper", [Delta("s8", "m.py", "1-3")], planned=True, risk="green"
        )
    )
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right_square_bracket")
        t = text(app)
        assert "+3 −0  added section" in t
        rows = [line.split(None, 2) for line in t.splitlines()]
        assert ["2", "▌", "def helper(x):"] in rows  # new-file number, bar, no `+`
        assert "+def" not in t
        assert "▌ " in "".join(span_texts(store, "s8-01", 0, "green"))  # accent bar
        # the code carries syntax colours beyond the accent bar
        change = store.load_change("s8-01")
        shown = render_change(change, resolve_deltas(change, store.sources()), 0)
        assert len({str(sp.style) for sp in shown.spans}) > 3


MOVE = FileDiff(
    "a.py",
    [
        "@@ -1,8 +1,2 @@ def big():",
        " x = 1",
        "-    y = compute(x)",
        "-    z = other(y)",
        "-    return z + 1",
        " end",
    ],
)
DEST = FileDiff(
    "b.py",
    [
        "@@ -0,0 +1,4 @@",
        "+def extracted(x):",
        "+    y = compute(x)",
        "+    z = other(y, 2)",
        "+    return z + 1",
    ],
)


async def test_a_move_shows_both_ends(tmp_path):
    store = Store(tmp_path)
    store.save_source(Source("s7", "m", [MOVE, DEST], seq=1))
    store.save_change(
        Change(
            "s7-01",
            "Extract",
            [
                Delta(
                    "s7", "a.py", "1-3", "carried over", to_path="b.py", to_lines="2-4"
                ),
                Delta("s7", "b.py", "1", "the new function header"),
            ],
            planned=True,
            risk="green",
        )
    )
    app = OverseerApp(store)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = text(app)
        assert "a.py → b.py  moved 3 lines" in t  # in the overview outline
        await pilot.press("right_square_bracket")
        t = text(app)
        assert "Moved  a.py → b.py  (2 of 3 lines moved unchanged)" in t
        assert "removed from here · a.py:2-4" in t
        assert "added here · b.py:2-4" in t
        assert "tinted lines have no match" in t
        # the line that changed on the way (a new argument) is tinted, the rest is not
        tinted = "".join(span_texts(store, "s7-01", 0, "on dark_green"))
        assert "z = other(y, 2)" in tinted and "y = compute(x)" not in tinted
        await pilot.press("right_square_bracket")
        assert "Delta 2/2  b.py" in text(app)  # what else changed is its own delta
