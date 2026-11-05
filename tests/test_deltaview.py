from conftest import chg, ins, mov

from overseer.deltaview import (
    change_rows,
    move_view,
    plain,
    side_text,
    stats,
    uncovered,
)
from overseer.model import Change, FileDiff, Side, Source

DIFF = FileDiff(
    "a.py",
    [
        "@@ -10,4 +10,5 @@ def f():",
        " keep",
        "-if a:",
        "+if a and b:",
        "+    extra()",
        " tail",
    ],
)


def test_change_rows_emphasise_only_the_words_that_differ():
    rows = change_rows(Side("a.py", "12", "if a:"), Side("a.py", "12", "if a and b:"))
    old, new = rows
    assert (old.sign, old.old, old.emph) == ("-", 12, [])
    assert (new.sign, new.new) == ("+", 12)
    assert new.text[new.emph[0][0] : new.emph[-1][1]] == " and b"


def test_change_rows_keep_shared_lines_as_context():
    before = Side("a.py", "10-12", "keep\nold\ntail")
    after = Side("a.py", "10-13", "keep\nnew\nmore\ntail")
    rows = change_rows(before, after)
    assert [(r.kind, r.sign, r.text) for r in rows] == [
        ("ctx", " ", "keep"),
        ("sel", "-", "old"),
        ("sel", "+", "new"),
        ("sel", "+", "more"),
        ("ctx", " ", "tail"),
    ]
    assert (rows[0].old, rows[0].new) == (10, 10)
    assert (rows[-1].old, rows[-1].new) == (12, 13)


def test_a_rewritten_line_is_not_emphasised_word_by_word():
    a, b = change_rows(Side("a", "1", "x = 1"), Side("a", "1", "y = totally_new()"))
    assert a.emph == [] and b.emph == []


def test_stats_follow_the_declared_kind():
    assert stats(ins("s", "a.py", text="a\nb\nc")) == (3, 0)
    assert stats(chg("s", "a.py", "if a:", "if a and b:")) == (1, 1)
    assert stats(mov("s", "a.py", "p\nq", "b.py")) == (0, 0)


def test_plain_is_the_declared_before_and_after():
    assert plain(chg("s", "a.py", "x", "y")) == ["-x", "+y"]
    assert plain(ins("s", "a.py", text="n1\nn2")) == ["+n1", "+n2"]


def test_move_view_matches_lines_ignoring_indent_and_finds_reindent():
    d = mov(
        "s",
        "a.py",
        "y = compute(x)\nz = other(y)\nreturn z",
        "b.py",
        "        y = compute(x)\n        z = other(y, 2)\n        return z",
        lines="2-4",
        to_lines="5-7",
    )
    v = move_view(d)
    assert (v.same, v.total) == (2, 3)
    assert [r.match for r in v.removed] == [True, False, True]
    assert [r.match for r in v.added] == [True, False, True]
    assert v.reindent == 8  # every matched line moved in by 8
    assert (v.removed[0].old, v.added[0].new) == (2, 5)


def test_move_view_reports_a_uniform_reindent():
    d = mov("s", "a.py", "a = 1\nb = 2", "b.py", "    a = 1\n    b = 2")
    assert move_view(d).reindent == 4


def test_side_text_copies_what_the_diff_shows():
    assert side_text(DIFF, "old", [11]) == "if a:"
    assert side_text(DIFF, "new", [11, 12]) == "if a and b:\n    extra()"
    assert side_text(DIFF, "old", [10, 11]) == "keep\nif a:"
    assert side_text(DIFF, "old", [40]) is None  # not in the diff: agent must say
    assert side_text(DIFF, "new", [13]) == "tail"


def test_uncovered_reports_lines_no_delta_mentions():
    src = Source("s", "m", [DIFF])
    only_change = Change("c", "t", [chg("s", "a.py", lines="11")])
    (gap,) = uncovered([only_change], {"s": src})
    assert (gap.path, gap.old, gap.new) == (
        "a.py",
        [],
        [12],
    )  # only `extra()` is unmentioned
    full = Change(
        "c",
        "t",
        [chg("s", "a.py", lines="11"), ins("s", "a.py", "12")],
    )
    assert uncovered([full], {"s": src}) == []
    assert uncovered([Change("c", "t", [])], {"s": src}) == []
