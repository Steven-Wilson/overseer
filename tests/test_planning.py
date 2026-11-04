import json
import subprocess

import pytest

from overseer import cli
from overseer.model import FileDiff, Source
from overseer.planning import PlanError, apply_plan, default_change
from overseer.store import Store


def dl(commit, path, lines=None, **extra):
    """A plan delta; omitting `lines` means the whole file."""
    out = {"commit": commit, "path": path, **extra}
    if lines is not None:
        out["lines"] = lines
    return out


ALL = [dl("aaa", f"aaa{i}.py") for i in range(3)]  # every file of commit aaa


@pytest.fixture
def store(tmp_path):
    """Two commits of three one-line files each (a0.py, a1.py, a2.py, ...)."""
    s = Store(tmp_path)
    for seq, sid in enumerate(["aaa", "bbb"], 1):
        files = [FileDiff(f"{sid}{i}.py", ["@@ -1 +1 @@", f"+{i}"]) for i in range(3)]
        src = Source(sid, f"msg {sid}", files, seq)
        s.save_source(src)
        s.save_change(default_change(src))
    return s


def test_default_change_is_one_delta_per_file(store):
    (c, _) = [c for c in store.changes()]
    assert [d.id for d in c.deltas] == [
        "aaa:aaa0.py#1",
        "aaa:aaa1.py#1",
        "aaa:aaa2.py#1",
    ]
    assert not c.planned


def test_plan_replaces_default_and_preserves_order(store):
    plan = {
        "changes": [
            {
                "risk": "green",
                "title": "second half",
                "deltas": [dl("aaa", "aaa2.py")],
                "answers": {"why": "w"},
            },
            {
                "risk": "green",
                "title": "first half",
                "deltas": [dl("aaa", "aaa0.py"), dl("aaa", "aaa1.py")],
            },
        ]
    }
    new = apply_plan(store, plan)
    assert [c.id for c in new] == ["aaa-01", "aaa-02"]
    got = store.changes()
    assert [c.id for c in got] == ["aaa-01", "aaa-02", "bbb"]  # aaa default gone
    assert got[0].planned and got[0].answers == {"why": "w"}
    assert not got[2].planned


def test_change_may_span_sources(store):
    both = [dl(s, f"{s}{i}.py") for s in ("aaa", "bbb") for i in range(3)]
    apply_plan(store, {"changes": [{"title": "both", "risk": "green", "deltas": both}]})
    assert [c.id for c in store.changes()] == ["aaa-01"]
    # replanning only one of the sources now would orphan the other's lines
    with pytest.raises(PlanError, match="also covers bbb"):
        apply_plan(
            store,
            {
                "changes": [
                    {
                        "risk": "green",
                        "title": "t",
                        "deltas": [dl("aaa", f"aaa{i}.py") for i in range(3)],
                    }
                ]
            },
        )


@pytest.fixture
def big(tmp_path):
    """One file with ten changed lines (-0 +0 ... in two git hunks) and one small."""
    s = Store(tmp_path)
    lines = ["@@ -1,5 +1,5 @@ def f"]
    lines += [f"{'-' if i % 2 else '+'}l{i}" for i in range(5)]
    lines += ["@@ -50,5 +50,5 @@ def g"]
    lines += [f"{'-' if i % 2 else '+'}m{i}" for i in range(5)]
    src = Source("ccc", "m", [FileDiff("f.py", lines), FileDiff("g.py", ["+g"])], 1)
    s.save_source(src)
    s.save_change(default_change(src))
    return s


def test_deltas_cut_lines_regardless_of_git_hunks(big):
    # one delta crosses git's two hunks; another is a single line inside one
    apply_plan(
        big,
        {
            "changes": [
                {
                    "title": "edges",
                    "risk": "green",
                    "deltas": [
                        dl("ccc", "f.py", "5-6", summary=" joins the hunks "),
                        dl("ccc", "g.py"),
                    ],
                },
                {
                    "title": "rest",
                    "risk": "green",
                    "deltas": [dl("ccc", "f.py", "1-4"), dl("ccc", "f.py", "7,8-10")],
                },
            ]
        },
    )
    edges, rest = big.changes()
    assert [d.id for d in edges.deltas] == ["ccc:f.py#5-6", "ccc:g.py#1"]
    assert edges.deltas[0].summary == "joins the hunks"
    assert [d.lines for d in rest.deltas] == ["1-4", "7-10"]  # canonical form


@pytest.mark.parametrize(
    "deltas, message",
    [
        (
            [dl("ccc", "f.py", "1-9"), dl("ccc", "g.py")],
            "unassigned changed lines in ccc f.py: 10",
        ),
        ([dl("ccc", "f.py", "1-10")], "unassigned changed lines in ccc g.py: 1"),
        (
            [dl("ccc", "f.py", "1-6"), dl("ccc", "f.py", "6-10"), dl("ccc", "g.py")],
            r"lines 6 already in changes\[1\].deltas\[1\]",
        ),
        ([dl("ccc", "f.py", "1-11"), dl("ccc", "g.py")], "out of range"),
        ([dl("ccc", "f.py", "0-3"), dl("ccc", "g.py")], "out of range"),
        ([dl("ccc", "f.py", "x"), dl("ccc", "g.py")], "bad line spec"),
        ([dl("ccc", "nope.py"), dl("ccc", "g.py")], "has no changed file"),
        ([dl("zzz", "f.py")], "unknown commit"),
    ],
)
def test_line_level_validation(big, deltas, message):
    before = [c.to_dict() for c in big.changes()]
    with pytest.raises(PlanError, match=message):
        apply_plan(
            big, {"changes": [{"title": "t", "risk": "green", "deltas": deltas}]}
        )
    assert [c.to_dict() for c in big.changes()] == before  # nothing written


@pytest.mark.parametrize(
    "plan, message",
    [
        (
            {
                "changes": [
                    {"risk": "green", "title": "t", "deltas": [dl("aaa", "aaa0.py")]}
                ]
            },
            "unassigned changed lines in aaa aaa1.py: 1",
        ),
        (
            {
                "changes": [
                    {
                        "risk": "green",
                        "title": "t",
                        "deltas": [dl("aaa", f"aaa{i}.py") for i in (0, 0, 1, 2)],
                    }
                ]
            },
            "already in",
        ),
        (
            {
                "changes": [
                    {"risk": "green", "title": "", "deltas": [dl("aaa", "aaa0.py")]}
                ]
            },
            "missing title",
        ),
        (
            {"changes": [{"risk": "green", "title": "t", "deltas": []}]},
            "non-empty deltas",
        ),
        (
            {"changes": [{"risk": "green", "title": "t", "deltas": ["aaa:aaa0.py#1"]}]},
            "must be an object with commit, path",
        ),
        (
            {
                "changes": [
                    {
                        "risk": "green",
                        "title": "t",
                        "deltas": [dl("aaa", "aaa0.py", summary=" ")],
                    }
                ]
            },
            "summary needs text",
        ),
        (
            {
                "changes": [
                    {
                        "risk": "green",
                        "title": "t",
                        "deltas": [dl("aaa", "aaa0.py")],
                        "answers": {"bogus": "x"},
                    }
                ]
            },
            "unknown question",
        ),
        ({"changes": []}, "empty"),
        ([], "plan must be"),
    ],
)
def test_invalid_plans_write_nothing(store, plan, message):
    before = [c.to_dict() for c in store.changes()]
    with pytest.raises(PlanError, match=message):
        apply_plan(store, plan)
    assert [c.to_dict() for c in store.changes()] == before


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_end_to_end_ingest_show_plan(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "f.txt").write_text("".join(f"{i}\n" for i in range(30)))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    lines = (repo / "f.txt").read_text().splitlines()
    lines[1], lines[25] = "A", "B"
    (repo / "f.txt").write_text("\n".join(lines) + "\n")
    git(repo, "commit", "-qam", "do two things")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path / "ov"))

    assert cli.main(["ingest", "HEAD", "--reviewset", "default"]) == 0
    out = capsys.readouterr().out
    sha = out.split()[0]
    assert "1 files, 4 changed lines" in out
    assert cli.main(["ingest", "HEAD", "--reviewset", "default"]) == 0  # idempotent
    assert "already ingested" in capsys.readouterr().out

    assert cli.main(["show", sha, "--reviewset", "default"]) == 0
    shown = capsys.readouterr().out
    assert "== f.txt  (changed lines 1-4)" in shown
    assert "   1 -1" in shown and "   2 +A" in shown and "   4 +B" in shown

    assert cli.main(["show", sha, "--json", "--reviewset", "default"]) == 0
    (f,) = json.loads(capsys.readouterr().out)["files"]
    assert f["path"] == "f.txt" and f["changed"] == 4
    assert [x["n"] for x in f["lines"] if x["n"]] == [1, 2, 3, 4]

    # git sees two hunks; the plan cuts by changed line and may recombine
    plan = {
        "changes": [
            {"risk": "green", "title": "tweak A", "deltas": [dl(sha, "f.txt", "1-2")]},
            {"risk": "green", "title": "tweak B", "deltas": [dl(sha, "f.txt", "3-4")]},
        ]
    }
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan))
    assert cli.main(["plan", str(planfile), "--reviewset", "default"]) == 0
    capsys.readouterr()
    assert cli.main(["list", "--unplanned", "--reviewset", "default"]) == 0
    assert json.loads(capsys.readouterr().out) == []

    assert cli.main(["list", "--reviewset", "default"]) == 0
    first, _ = json.loads(capsys.readouterr().out)
    assert first["deltas"][0]["id"] == f"{sha}:f.txt#1-2"  # ids are in the JSON


@pytest.mark.parametrize(
    "change, message",
    [
        ({"title": "t", "deltas": ALL}, "risk must be one of"),
        ({"title": "t", "deltas": ALL, "risk": "blue"}, "risk must be"),
        (
            {"title": "t", "deltas": ALL, "risk": "red"},
            "risk_reason is required for red",
        ),
        (
            {"title": "t", "deltas": ALL, "risk": "yellow", "risk_reason": " "},
            "required for yellow",
        ),
    ],
)
def test_risk_validation(store, change, message):
    with pytest.raises(PlanError, match=message):
        apply_plan(store, {"changes": [change]})


def test_risk_is_stored(store):
    (c,) = apply_plan(
        store,
        {
            "changes": [
                {
                    "title": "t",
                    "deltas": ALL,
                    "risk": "red",
                    "risk_reason": " core fn ",
                }
            ]
        },
    )
    assert store.load_change(c.id).risk == "red"
    assert store.load_change(c.id).risk_reason == "core fn"


def test_plan_stores_links(store):
    deltas = [dl("aaa", "aaa0.py"), dl("aaa", "aaa1.py"), dl("aaa", "aaa2.py")]
    deltas[1]["related"] = [{"path": "t.py"}]
    (c,) = apply_plan(
        store,
        {
            "changes": [
                {
                    "title": "t",
                    "deltas": deltas,
                    "risk": "green",
                    "related": [{"path": " x.py ", "line": 4, "note": "caller"}],
                }
            ]
        },
    )
    got = store.load_change(c.id)
    assert got.links[0].path == "x.py" and got.links[0].line == 4
    assert got.deltas[1].links[0].line is None and got.deltas[0].links == []


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"related": "x.py"}, "must be a list"),
        ({"related": [{"line": 3}]}, "missing path"),
        ({"related": [{"path": "x", "line": 0}]}, "positive integer"),
        ({"related": [{"path": "x", "bogus": 1}]}, "must be an object"),
    ],
)
def test_invalid_links_rejected(store, extra, message):
    change = {"title": "t", "deltas": ALL, "risk": "green", **extra}
    with pytest.raises(PlanError, match=message):
        apply_plan(store, {"changes": [change]})


def test_delta_summaries_stored_and_recordable(store, monkeypatch, tmp_path):
    deltas = [dl("aaa", "aaa0.py", summary=" does x "), *ALL[1:]]
    (change,) = apply_plan(
        store, {"changes": [{"risk": "green", "title": "t", "deltas": deltas}]}
    )
    assert store.load_change(change.id).deltas[0].summary == "does x"
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    args = ["record", change.id, "--reviewset", "default"]
    assert cli.main([*args, "--delta", "aaa:aaa2.py#1", "--summary", "adds y"]) == 0
    assert store.load_change(change.id).deltas[2].summary == "adds y"
    assert cli.main([*args, "--delta", "bbb:bbb0.py#1", "--summary", "z"]) != 0
    assert cli.main([*args, "--delta", "aaa:aaa0.py#1"]) != 0  # summary missing


@pytest.fixture
def moved(tmp_path):
    """a.py loses a 3-line block; b.py gains it (re-indented) under a new `if`."""
    s = Store(tmp_path)
    block = ["y = compute(x)", "z = other(y)", "return z + 1"]
    a = FileDiff(
        "a.py",
        ["@@ -1,5 +1,2 @@ def big():", " x = 1", *(f"-{b}" for b in block), " end"],
    )
    b = FileDiff(
        "b.py",
        [
            "@@ -0,0 +1,5 @@",
            "+def extracted(x):",
            "+    if x:",
            *(f"+        {t}" for t in block),
        ],
    )
    src = Source("mmm", "extract", [a, b], 1)
    s.save_source(src)
    s.save_change(default_change(src))
    return s


def test_a_move_is_one_delta_with_two_ends(moved):
    (c,) = apply_plan(
        moved,
        {
            "changes": [
                {
                    "title": "Extract",
                    "risk": "green",
                    "deltas": [
                        dl(
                            "mmm",
                            "a.py",
                            "1-3",
                            moved_to={"path": "b.py", "lines": "3-5"},
                        ),
                        dl("mmm", "b.py", "1-2"),  # what else is new there
                    ],
                }
            ]
        },
    )
    move, rest = c.deltas
    assert move.moved and move.id == "mmm:a.py#1-3>b.py#3-5"
    assert rest.id == "mmm:b.py#1-2"
    assert moved.load_change(c.id).deltas[0].to_path == "b.py"


@pytest.mark.parametrize(
    "delta, rest, message",
    [
        (
            dl("mmm", "a.py", "1-2", moved_to={"path": "a.py", "lines": "3"}),
            [],
            "must all be added",
        ),
        (
            dl("mmm", "b.py", "1-2", moved_to={"path": "a.py", "lines": "1-3"}),
            [dl("mmm", "b.py", "3-5")],
            "must all be removed",
        ),
        (
            dl("mmm", "a.py", "1-3", moved_to={"path": "zzz.py"}),
            [],
            "no changed file 'zzz.py'",
        ),
        (
            dl("mmm", "a.py", "1-3", moved_to={"path": "b.py", "lines": "3-5"}),
            [dl("mmm", "b.py", "3-4")],
            "already in",
        ),
        (
            dl("mmm", "a.py", "1-3", moved_to={"path": "b.py", "lines": "3-5"}),
            [],
            "unassigned changed lines in mmm b.py: 1-2",
        ),
        (dl("mmm", "a.py", "1-3", moved_to={"lines": "3-5"}), [], r"moved_to: must be"),
    ],
)
def test_move_validation(moved, delta, rest, message):
    before = [c.to_dict() for c in moved.changes()]
    plan = {"changes": [{"title": "t", "risk": "green", "deltas": [delta, *rest]}]}
    with pytest.raises(PlanError, match=message):
        apply_plan(moved, plan)
    assert [c.to_dict() for c in moved.changes()] == before


def test_end_to_end_move_in_a_real_commit(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    body = "".join(f"    step_{i} = prepare_something_{i}(inputs)\n" for i in range(5))
    (repo / "a.py").write_text(f"def big(inputs):\n{body}    return inputs\n")
    (repo / "b.py").write_text("# helpers\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    (repo / "a.py").write_text("def big(inputs):\n    return helper(inputs)\n")
    nested = body.replace("    step", "        step")
    (repo / "b.py").write_text(
        f"# helpers\ndef helper(inputs):\n    if inputs:\n{nested}"
    )
    git(repo, "commit", "-qam", "extract helper")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path / "ov"))

    assert cli.main(["ingest", "HEAD", "--reviewset", "r"]) == 0
    sha = capsys.readouterr().out.split()[0]
    assert cli.main(["show", sha, "--reviewset", "r"]) == 0
    shown = capsys.readouterr().out
    assert "Possible moves" in shown and "a.py lines 1-5 -> b.py lines 3-7" in shown
    assert cli.main(["show", sha, "--json", "--reviewset", "r"]) == 0
    (mv,) = json.loads(capsys.readouterr().out)["possible_moves"]
    assert mv == {
        "path": "a.py",
        "lines": "1-5",
        "moved_to": {"path": "b.py", "lines": "3-7"},
    }

    # the block moves; the call that replaces it, and the new `if`, are their own deltas
    plan = {
        "changes": [
            {
                "title": "Extract helper",
                "risk": "green",
                "deltas": [
                    dl(sha, "a.py", "1-5", moved_to=mv["moved_to"]),
                    dl(sha, "b.py", "1-2", summary="the new function and its guard"),
                    dl(sha, "a.py", "6-7", summary="call the helper"),
                ],
            }
        ]
    }
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan))
    assert cli.main(["plan", str(planfile), "--reviewset", "r"]) == 0
    capsys.readouterr()

    from overseer.model import Note

    store = Store(reviewset="r")
    move_id = f"{sha}:a.py#1-5>b.py#3-7"
    store.save_note(Note("n1", "delta", move_id, "why nested?"))
    assert cli.main(["notes", "--reviewset", "r"]) == 0
    (note,) = json.loads(capsys.readouterr().out)
    ctx = note["context"]
    assert ctx["path"] == "a.py" and ctx["changed_lines"] == "1-5"
    assert ctx["moved_to"]["path"] == "b.py" and ctx["moved_to"]["new_lines"] == [
        4,
        8,
    ]  # file lines
    assert any("step_0" in line for line in ctx["moved_to"]["diff"])
