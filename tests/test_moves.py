import pytest

from overseer.deltaview import kind_of, move_view
from overseer.model import Delta, FileDiff, Source, split_delta_id
from overseer.moves import find_moves

BODY = [
    "total = compute(items)",
    "for item in items:",
    "    total += weigh(item)",
    "return total",
]


def removed(path, body, indent=""):
    return FileDiff(path, ["@@ -1 +1 @@", *(f"-{indent}{x}" for x in body)])


def added(path, body, indent=""):
    return FileDiff(path, ["@@ -1 +1 @@", *(f"+{indent}{x}" for x in body)])


def test_find_moves_matches_across_files_ignoring_indentation():
    src = Source("s", "m", [removed("a.py", BODY), added("b.py", BODY, "    ")])
    (m,) = find_moves(src)
    assert (m.path, m.lines, m.to_path, m.to_lines) == (
        "a.py",
        [1, 2, 3, 4],
        "b.py",
        [1, 2, 3, 4],
    )


def test_find_moves_ignores_tiny_or_trivial_matches():
    short = ["x = 1", "y = 2"]
    assert find_moves(Source("s", "m", [removed("a", short), added("b", short)])) == []
    braces = ["}", "}", "}", ""]
    assert (
        find_moves(Source("s", "m", [removed("a", braces), added("b", braces)])) == []
    )


def test_find_moves_finds_the_matching_part_of_a_longer_block():
    big = ["setup()", *BODY, "teardown()"]
    src = Source("s", "m", [removed("a.py", big), added("b.py", ["other()", *BODY])])
    (m,) = find_moves(src)
    assert m.lines == [2, 3, 4, 5] and m.to_lines == [2, 3, 4, 5]


def test_kind_of():
    mixed = FileDiff("x", ["@@ -1 +1 @@", "-a", "+b"])
    assert kind_of(mixed, [1, 2]) == "edit"
    assert kind_of(mixed, [1]) == "removed" and kind_of(mixed, [2]) == "added"


def test_move_view_matches_lines_and_reports_reindent():
    a = removed("a.py", BODY)
    edited = [*BODY[:2], "    total += weigh(item) * 2", BODY[3]]
    b = added("b.py", edited, "    ")
    mv = move_view(a, [1, 2, 3, 4], b, [1, 2, 3, 4])
    assert (mv.same, mv.total, mv.reindent) == (3, 4, 4)
    assert [r.match for r in mv.removed] == [True, True, False, True]
    assert [r.match for r in mv.added] == [True, True, False, True]


def test_move_delta_id_round_trips():
    d = Delta("abc1234", "src/a#b.py", "3-5", to_path="src/b.py", to_lines="1-3")
    assert d.id == "abc1234:src/a#b.py#3-5>src/b.py#1-3"
    assert split_delta_id(d.id) == ("abc1234", "src/a#b.py", "3-5", "src/b.py", "1-3")
    assert split_delta_id("abc:x.py#1-2") == ("abc", "x.py", "1-2", "", "")
    assert Delta.from_dict(d.to_dict()) == d
    for bad in ["nocolon", "abc:x.py", "abc:x.py#1>y.py", "abc:#1"]:
        with pytest.raises(ValueError):
            split_delta_id(bad)


def test_small_edit_marks_only_the_words_that_changed():
    from overseer.deltaview import delta_rows

    f = FileDiff(
        "x", ["@@ -1 +1 @@", "-if a:", "+if a and b:", "-x = 1", "+y = totally_new()"]
    )
    rows = {r.sign + r.text: r for r in delta_rows(f, [1, 2, 3, 4]) if r.kind == "sel"}
    plus = rows["+if a and b:"]
    assert "if a and b:"[plus.emph[0][0] : plus.emph[0][1]].strip() == "and b"
    assert rows["-if a:"].emph == []  # nothing was removed from that line
    # a line that is replaced wholesale gets no word-level marks: it is just new
    assert rows["+y = totally_new()"].emph == [] and rows["-x = 1"].emph == []
