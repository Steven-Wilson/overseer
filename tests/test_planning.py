import json
import subprocess

import pytest

from overseer import cli
from overseer.model import Hunk, Source
from overseer.planning import PlanError, apply_plan, default_change
from overseer.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    for seq, sid in enumerate(["aaa", "bbb"], 1):
        src = Source(
            sid, f"msg {sid}", [Hunk(f"{sid}{i}.py", "@@") for i in range(3)], seq
        )
        s.save_source(src)
        s.save_change(default_change(src))
    return s


def test_plan_replaces_default_and_preserves_order(store):
    plan = {
        "changes": [
            {"title": "second half", "hunks": ["aaa:3"], "answers": {"why": "w"}},
            {"title": "first half", "hunks": ["aaa:1", "aaa:2"]},
        ]
    }
    new = apply_plan(store, plan)
    assert [c.id for c in new] == ["aaa-01", "aaa-02"]
    got = store.changes()
    assert [c.id for c in got] == ["aaa-01", "aaa-02", "bbb"]  # aaa default gone
    assert got[0].planned and got[0].answers == {"why": "w"}
    assert not got[2].planned


def test_change_may_span_sources(store):
    apply_plan(
        store,
        {
            "changes": [
                {
                    "title": "both",
                    "hunks": [f"{s}:{i}" for s in ("aaa", "bbb") for i in (1, 2, 3)],
                }
            ]
        },
    )
    assert [c.id for c in store.changes()] == ["aaa-01"]
    # replanning only one of the sources now would orphan the other's hunks
    with pytest.raises(PlanError, match="also covers bbb"):
        apply_plan(
            store, {"changes": [{"title": "t", "hunks": ["aaa:1", "aaa:2", "aaa:3"]}]}
        )


@pytest.mark.parametrize(
    "plan, message",
    [
        (
            {"changes": [{"title": "t", "hunks": ["aaa:1", "aaa:2"]}]},
            "unassigned hunks in aaa: aaa:3",
        ),
        (
            {
                "changes": [
                    {"title": "t", "hunks": ["aaa:1", "aaa:1", "aaa:2", "aaa:3"]}
                ]
            },
            "already assigned",
        ),
        ({"changes": [{"title": "t", "hunks": ["aaa:9"]}]}, "does not exist"),
        ({"changes": [{"title": "t", "hunks": ["zzz:1"]}]}, "unknown source"),
        ({"changes": [{"title": "t", "hunks": ["nocolon"]}]}, "malformed"),
        ({"changes": [{"title": "", "hunks": ["aaa:1"]}]}, "missing title"),
        (
            {
                "changes": [
                    {"title": "t", "hunks": ["aaa:1"], "answers": {"bogus": "x"}}
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

    assert cli.main(["ingest", "HEAD"]) == 0
    sha = capsys.readouterr().out.split()[0]
    assert cli.main(["ingest", "HEAD"]) == 0  # idempotent
    assert "already ingested" in capsys.readouterr().out
    assert cli.main(["ingest", "HEAD", "--context", "0"]) == 1  # different hunks
    capsys.readouterr()

    assert cli.main(["show", sha, "--json"]) == 0
    hunks = json.loads(capsys.readouterr().out)["hunks"]
    assert [h["id"] for h in hunks] == [f"{sha}:1", f"{sha}:2"]

    plan = {
        "changes": [
            {"title": "tweak A", "hunks": [f"{sha}:1"]},
            {"title": "tweak B", "hunks": [f"{sha}:2"]},
        ]
    }
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan))
    assert cli.main(["plan", str(planfile)]) == 0
    capsys.readouterr()
    assert cli.main(["list", "--unplanned"]) == 0
    assert json.loads(capsys.readouterr().out) == []

    # --force with a finer context rebuilds the source and drops its changes
    assert cli.main(["ingest", "HEAD", "--context", "0", "--force"]) == 0
    capsys.readouterr()
    assert cli.main(["list"]) == 0
    (c,) = json.loads(capsys.readouterr().out)
    assert c["planned"] is False and len(c["hunk_ids"]) == 2
