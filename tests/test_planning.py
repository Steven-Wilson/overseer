import json
import subprocess

import pytest

from overseer import cli
from overseer.deltaview import uncovered
from overseer.model import FileDiff, Note, Source
from overseer.planning import PlanError, apply_plan, default_change
from overseer.store import Store


def ins(commit, path, lines="1", **extra):
    """A plan delta declaring an insert; the code is copied from the diff."""
    return {
        "kind": "insert",
        "commit": commit,
        "after": {"path": path, "lines": lines},
        **extra,
    }


ALL = [ins("aaa", f"aaa{i}.py") for i in range(3)]


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


def test_default_change_is_one_raw_delta_per_file(store):
    (c, _) = store.changes()
    assert [d.id for d in c.deltas] == [
        "aaa:raw@aaa0.py",
        "aaa:raw@aaa1.py",
        "aaa:raw@aaa2.py",
    ]
    assert {d.kind for d in c.deltas} == {"raw"} and not c.planned
    assert c.deltas[0].diff == ["@@ -1 +1 @@", "+0"]


def test_plan_replaces_default_and_preserves_order(store):
    plan = {
        "changes": [
            {
                "risk": "green",
                "title": "second half",
                "deltas": [ALL[2]],
                "answers": {"why": "w"},
            },
            {"risk": "green", "title": "first half", "deltas": ALL[:2]},
        ]
    }
    new = apply_plan(store, plan)
    assert [c.id for c in new] == ["aaa-01", "aaa-02"]
    got = store.changes()
    assert [c.id for c in got] == ["aaa-01", "aaa-02", "bbb"]  # aaa default gone
    assert got[0].planned and got[0].answers == {"why": "w"}
    assert not got[2].planned


def test_change_may_span_sources(store):
    both = [ins(s, f"{s}{i}.py") for s in ("aaa", "bbb") for i in range(3)]
    apply_plan(store, {"changes": [{"title": "both", "risk": "green", "deltas": both}]})
    assert [c.id for c in store.changes()] == ["aaa-01"]
    # replanning only one of the sources now would orphan the other's deltas
    plan = {"changes": [{"risk": "green", "title": "t", "deltas": ALL}]}
    with pytest.raises(PlanError, match="also covers bbb"):
        apply_plan(store, plan)


def test_the_agent_declares_each_kind(store):
    deltas = [
        ins("aaa", "aaa0.py", summary="adds it"),
        {
            "kind": "delete",
            "commit": "aaa",
            "before": {"path": "aaa1.py", "lines": "7-8", "text": "old\nlines"},
        },
        {
            "kind": "change",
            "commit": "aaa",
            "before": {"path": "aaa2.py", "lines": "3", "text": "if a:"},
            "after": {"path": "aaa2.py", "lines": "3", "text": "if a and b:"},
        },
        {
            "kind": "move",
            "commit": "aaa",
            "before": {
                "path": "aaa1.py",
                "symbol": "f",
                "lines": "1-2",
                "text": "p\nq",
            },
            "after": {
                "path": "aaa2.py",
                "symbol": "g",
                "lines": "9-10",
                "text": "p\nq",
            },
        },
    ]
    (c,) = apply_plan(
        store, {"changes": [{"title": "t", "risk": "green", "deltas": deltas}]}
    )
    ins_, dele, chg, mov = store.load_change(c.id).deltas
    assert [d.kind for d in (ins_, dele, chg, mov)] == [
        "insert",
        "delete",
        "change",
        "move",
    ]
    assert ins_.after.text == "0"  # copied from the commit's diff
    assert (dele.before.text, dele.after) == ("old\nlines", None)
    assert (chg.before.text, chg.after.text) == ("if a:", "if a and b:")
    assert mov.before.symbol == "f" and mov.after.where == "aaa2.py:9-10"
    assert mov.id == "aaa:move@aaa1.py:1-2>aaa2.py:9-10"
    assert ins_.id == "aaa:insert@aaa0.py:1"


@pytest.mark.parametrize(
    "delta, message",
    [
        ({"kind": "rewrite", "commit": "aaa"}, "kind must be one of"),
        ({"kind": "raw", "commit": "aaa"}, "kind must be one of"),
        (ins("zzz", "x.py"), "unknown commit 'zzz'"),
        ({**ins("aaa", "aaa0.py"), "before": {"path": "x", "lines": "1"}}, "remove"),
        ({"kind": "delete", "commit": "aaa"}, "a delete needs before"),
        (
            {"kind": "change", "commit": "aaa", "before": {"path": "a", "lines": "1"}},
            "a change needs before and after",
        ),
        (ins("aaa", "aaa0.py", "x"), "lines must be a line or range"),
        (ins("aaa", "aaa0.py", "5-2"), "lines must be a line or range"),
        (ins("aaa", "aaa0.py", "40"), "no text given and the commit's diff"),
        (ins("aaa", "nope.py"), "no text given and the commit's diff"),
        (
            {
                "kind": "change",
                "commit": "aaa",
                "before": {"path": "a.py", "lines": "1", "text": "x"},
                "after": {"path": "b.py", "lines": "1", "text": "y"},
            },
            "use kind move",
        ),
        (ins("aaa", "aaa0.py", summary=" "), "summary needs text"),
        ("insert", "must be an object"),
    ],
)
def test_invalid_deltas_write_nothing(store, delta, message):
    before = [c.to_dict() for c in store.changes()]
    plan = {"changes": [{"title": "t", "risk": "green", "deltas": [delta]}]}
    with pytest.raises(PlanError, match=message):
        apply_plan(store, plan)
    assert [c.to_dict() for c in store.changes()] == before


def test_the_same_delta_twice_is_rejected(store):
    plan = {"changes": [{"title": "t", "risk": "green", "deltas": [ALL[0], ALL[0]]}]}
    with pytest.raises(PlanError, match="same as changes"):
        apply_plan(store, plan)


def test_text_may_be_given_instead_of_copied(store):
    d = ins("aaa", "aaa0.py", "40-41")
    d["after"]["text"] = "written\nby the agent"
    (c,) = apply_plan(
        store, {"changes": [{"title": "t", "risk": "green", "deltas": [d]}]}
    )
    assert store.load_change(c.id).deltas[0].after.text == "written\nby the agent"


@pytest.mark.parametrize(
    "plan, message",
    [
        ("nope", "plan must be an object"),
        ({"changes": []}, "is empty"),
        ({"changes": [{"risk": "green", "deltas": ALL}]}, "missing title"),
        ({"changes": [{"title": "t", "risk": "green", "deltas": []}]}, "non-empty"),
        (
            {"changes": [{"title": "t", "risk": "green", "deltas": ALL, "answers": 3}]},
            "answers must be an object",
        ),
        (
            {
                "changes": [
                    {
                        "title": "t",
                        "risk": "green",
                        "deltas": ALL,
                        "answers": {"x": "y"},
                    }
                ]
            },
            "unknown question 'x'",
        ),
    ],
)
def test_invalid_plans_write_nothing(store, plan, message):
    before = [c.to_dict() for c in store.changes()]
    with pytest.raises(PlanError, match=message):
        apply_plan(store, plan)
    assert [c.to_dict() for c in store.changes()] == before


def test_unmentioned_changed_lines_are_reported_not_rejected(store):
    # the agent covers only two of aaa's three files: accepted, but the gap shows
    plan = {"changes": [{"title": "t", "risk": "green", "deltas": ALL[:2]}]}
    new = apply_plan(store, plan)
    (gap,) = uncovered(new, store.sources())
    assert (gap.commit, gap.path, gap.new, gap.old) == ("aaa", "aaa2.py", [1], [])
    full = apply_plan(
        store, {"changes": [{"title": "t", "risk": "green", "deltas": ALL}]}
    )
    assert uncovered(full, store.sources()) == []


def test_a_raw_default_has_no_gaps(store):
    assert uncovered(store.changes()[:1], store.sources()) == []


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


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
    deltas = [dict(d) for d in ALL]
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
    deltas = [ins("aaa", "aaa0.py", summary=" does x "), *ALL[1:]]
    (change,) = apply_plan(
        store, {"changes": [{"risk": "green", "title": "t", "deltas": deltas}]}
    )
    assert store.load_change(change.id).deltas[0].summary == "does x"
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    args = ["record", change.id, "--reviewset", "default"]
    assert cli.main([*args, "--delta", "aaa:insert@aaa2.py:1", "--summary", "y"]) == 0
    assert store.load_change(change.id).deltas[2].summary == "y"
    assert cli.main([*args, "--delta", "bbb:insert@bbb0.py:1", "--summary", "z"]) != 0
    assert cli.main([*args, "--delta", "aaa:insert@aaa0.py:1"]) != 0  # no summary


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path / "ov"))
    return repo


def test_end_to_end_change_and_notes(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    (repo / "f.txt").write_text("".join(f"{i}\n" for i in range(30)))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    lines = (repo / "f.txt").read_text().splitlines()
    lines[1], lines[25] = "A", "B"
    (repo / "f.txt").write_text("\n".join(lines) + "\n")
    git(repo, "commit", "-qam", "do two things")

    assert cli.main(["ingest", "HEAD", "--reviewset", "default"]) == 0
    out = capsys.readouterr().out
    sha = out.split()[0]
    assert "1 files, 4 changed lines" in out
    assert cli.main(["ingest", "HEAD", "--reviewset", "default"]) == 0  # idempotent
    assert "already ingested" in capsys.readouterr().out

    assert cli.main(["show", sha, "--reviewset", "default"]) == 0
    shown = capsys.readouterr().out
    assert "    2       -1" in shown and "      2 +A" in shown  # old / new numbers

    assert cli.main(["show", sha, "--json", "--reviewset", "default"]) == 0
    (f,) = json.loads(capsys.readouterr().out)["files"]
    assert {"kind": "add", "old": None, "new": 2, "text": "A"} in f["lines"]

    plan = {
        "changes": [
            {
                "risk": "green",
                "title": "tweak A",
                "deltas": [
                    {
                        "kind": "change",
                        "commit": sha,
                        "before": {"path": "f.txt", "lines": "2"},  # text from diff
                        "after": {"path": "f.txt", "lines": "2"},
                        "summary": "rename the second line",
                    }
                ],
            }
        ]
    }
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan))
    assert cli.main(["plan", str(planfile), "--reviewset", "default"]) == 0
    captured = capsys.readouterr()
    # the agent said nothing about the B edit: reported as a warning, not an error
    assert "warning: no delta mentions" in captured.err and "26" in captured.err
    assert cli.main(["list", "--unplanned", "--reviewset", "default"]) == 0
    assert json.loads(capsys.readouterr().out) == []

    assert cli.main(["list", "--reviewset", "default"]) == 0
    (c,) = json.loads(capsys.readouterr().out)
    (d,) = c["deltas"]
    assert d["id"] == f"{sha}:change@f.txt:2>f.txt:2"
    assert (d["before"]["text"], d["after"]["text"]) == ("1", "A")

    Store(reviewset="default").save_note(Note("n1", "delta", d["id"], "why?"))
    assert cli.main(["notes", "--reviewset", "default"]) == 0
    (note,) = json.loads(capsys.readouterr().out)
    ctx = note["context"]
    assert ctx["kind"] == "change" and ctx["diff"] == ["-1", "+A"]
    assert ctx["after"]["lines"] == "2" and ctx["summary"] == "rename the second line"


def test_end_to_end_move_in_a_real_commit(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
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

    assert cli.main(["ingest", "HEAD", "--reviewset", "r"]) == 0
    sha = capsys.readouterr().out.split()[0]
    assert cli.main(["show", sha, "--reviewset", "r"]) == 0
    shown = capsys.readouterr().out
    assert "Possible moves" in shown and "a.py lines 2-6 -> b.py lines 4-8" in shown
    assert cli.main(["show", sha, "--json", "--reviewset", "r"]) == 0
    (mv,) = json.loads(capsys.readouterr().out)["possible_moves"]
    assert mv["before"] == {"path": "a.py", "lines": "2-6"}
    assert mv["after"] == {"path": "b.py", "lines": "4-8"}

    # the agent declares the move, and says what else happened around it
    plan = {
        "changes": [
            {
                "title": "Extract helper",
                "risk": "green",
                "deltas": [
                    {
                        "kind": "move",
                        "commit": sha,
                        "before": mv["before"],
                        "after": {**mv["after"], "symbol": "helper"},
                    },
                    {
                        "kind": "insert",
                        "commit": sha,
                        "after": {"path": "b.py", "lines": "2-3", "symbol": "helper"},
                        "summary": "the new function and its guard",
                    },
                    {
                        "kind": "change",
                        "commit": sha,
                        "before": {"path": "a.py", "lines": "7"},
                        "after": {"path": "a.py", "lines": "2"},
                        "summary": "call the helper",
                    },
                ],
            }
        ]
    }
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan))
    assert cli.main(["plan", str(planfile), "--reviewset", "r"]) == 0
    captured = capsys.readouterr()
    # every changed line is declared, so nothing is reported
    assert "warning" not in captured.err

    move_id = f"{sha}:move@a.py:2-6>b.py:4-8"
    Store(reviewset="r").save_note(Note("n1", "delta", move_id, "why nested?"))
    assert cli.main(["notes", "--reviewset", "r"]) == 0
    (note,) = json.loads(capsys.readouterr().out)
    ctx = note["context"]
    assert ctx["kind"] == "move"
    assert ctx["before"]["path"] == "a.py" and ctx["after"]["symbol"] == "helper"
    assert any("step_0" in line for line in ctx["diff"])
