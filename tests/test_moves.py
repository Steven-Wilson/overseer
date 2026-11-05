from overseer.model import Delta, FileDiff, Side, Source
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
        "1-4",
        "b.py",
        "1-4",
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
    assert (m.lines, m.to_lines) == ("2-5", "2-5")


def test_delta_ids_name_the_kind_and_where_and_round_trip():
    d = Delta(
        "move", "abc1234", Side("src/a.py", "3-5", "x"), Side("src/b.py", "1-3", "x")
    )
    assert d.id == "abc1234:move@src/a.py:3-5>src/b.py:1-3"
    assert Delta.from_dict(d.to_dict()) == d
    one = Delta("insert", "abc1234", after=Side("a.py", "9", "n"))
    assert one.id == "abc1234:insert@a.py:9" and one.paths == ["a.py"]
    assert Delta.from_dict(one.to_dict()) == one
