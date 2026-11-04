import pytest

from overseer.deltaview import delta_rows, new_range, plain, stats
from overseer.model import Delta, FileDiff, format_lines, parse_lines

# changed lines: 1 "-old a", 2 "+new a", 3 "-old b", 4 "+new b"
FILE = FileDiff(
    "x.py",
    [
        "@@ -10,12 +10,12 @@ def foo():",
        " c1",
        " c2",
        " c3",
        "-old a",
        "+new a",
        " c4",
        " c5",
        " c6",
        " c7",
        "-old b",
        "+new b",
        " c8",
        " c9",
    ],
)


def test_parse_and_format_lines_round_trip():
    assert parse_lines("3-5,9", 10) == [3, 4, 5, 9]
    assert parse_lines(" 9 , 3-4,4-5 ", 10) == [3, 4, 5, 9]
    assert format_lines([9, 3, 4, 5, 4]) == "3-5,9"
    assert format_lines([]) == ""
    assert Delta("abc", "a/b#c.py", "3-5").id == "abc:a/b#c.py#3-5"


@pytest.mark.parametrize("bad", ["", "a", "0", "3-", "-3", "5-3", "1-11", "11", "1,,2"])
def test_parse_lines_rejects(bad):
    with pytest.raises(ValueError):
        parse_lines(bad, 10)


def test_file_counts_changed_lines_only():
    assert FILE.count == 4
    assert FileDiff("b.bin", ["!(binary file changed)"]).count == 1


def test_a_delta_shows_its_lines_with_context_and_real_line_numbers():
    rows = delta_rows(FILE, {1, 2})
    assert rows[0].kind == "gap" and rows[0].text == "def foo():"
    body = [(r.kind, r.sign + r.text, r.old, r.new) for r in rows[1:]]
    assert body == [
        ("ctx", " c1", 10, 10),
        ("ctx", " c2", 11, 11),
        ("ctx", " c3", 12, 12),
        ("sel", "-old a", 13, None),
        ("sel", "+new a", None, 13),
        ("ctx", " c4", 14, 14),
        ("ctx", " c5", 15, 15),
        ("ctx", " c6", 16, 16),
    ]  # c7 and the second edit are out of the window
    assert new_range(rows) == (13, 13)
    assert plain(rows) == [
        "... def foo():",
        " c1",
        " c2",
        " c3",
        "-old a",
        "+new a",
        " c4",
        " c5",
        " c6",
    ]


def test_neighbouring_changed_lines_of_other_deltas_are_marked_not_hidden():
    rows = delta_rows(FILE, {2})  # only "+new a"
    kinds = {r.sign + r.text: r.kind for r in rows if r.kind in ("sel", "other")}
    assert kinds == {"-old a": "other", "+new a": "sel"}
    # and they never read as unchanged context in the machine-readable form
    assert "-old a" not in plain(rows)


def test_a_delta_spanning_git_hunks_gets_a_gap_with_the_later_function_name():
    two = FileDiff(
        "x.py",
        [
            "@@ -1,2 +1,2 @@ def a():",
            "-p",
            "+q",
            "@@ -90,2 +90,2 @@ def z():",
            "-r",
            "+s",
        ],
    )
    rows = delta_rows(two, {1, 4})
    assert [(r.kind, r.sign + r.text) for r in rows] == [
        ("gap", "def a():"),
        ("sel", "-p"),
        ("other", "+q"),
        ("gap", "def z():"),
        ("other", "-r"),
        ("sel", "+s"),
    ]
    assert rows[5].new == 90


def test_non_text_change_is_a_note_row():
    rows = delta_rows(FileDiff("b.bin", ["!(binary file changed)"]), {1})
    assert [(r.kind, r.text) for r in rows] == [("note", "(binary file changed)")]
    assert new_range(rows) is None


def test_no_newline_marker_stays_with_its_line():
    f = FileDiff("x", ["@@ -1 +1 @@", "-a", "\\ No newline at end of file", "+b"])
    texts = [r.sign + r.text for r in delta_rows(f, {2})]
    assert texts == ["-a", "\\ No newline at end of file", "+b"]


def test_stats_count_added_and_removed_in_the_delta():
    assert stats(FILE, [1, 2, 4]) == (2, 1)
