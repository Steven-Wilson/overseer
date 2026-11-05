import json
import os
import stat
import subprocess

import pytest
from conftest import chg, dele, ins, mov  # noqa: F401

from overseer import cli
from overseer.gitdiff import GitError, file_at, head_rev
from overseer.model import Change, FileDiff, Link, Source
from overseer.store import Store
from overseer.tui.app import OverseerApp


def git(cwd, *args):
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """b.py is "one/two" at v1 and "zero/one/two" at v2 (HEAD)."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "b.py").write_text("one\ntwo\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "v1")
    v1 = git(root, "rev-parse", "HEAD")
    (root / "b.py").write_text("zero\none\ntwo\n")
    git(root, "commit", "-qam", "v2")
    monkeypatch.chdir(root)
    return root, v1, git(root, "rev-parse", "HEAD")


def test_head_rev_and_file_at(repo, tmp_path_factory):
    root, v1, v2 = repo
    assert head_rev() == v2 and len(v2) == 40
    assert file_at(v1, "b.py") == b"one\ntwo\n"
    assert file_at(v2, "b.py") == b"zero\none\ntwo\n"
    with pytest.raises(GitError):
        file_at(v1, "nope.py")
    with pytest.raises(GitError):
        file_at("0" * 40, "b.py")
    assert head_rev(str(tmp_path_factory.mktemp("not-a-repo"))) == ""


def test_links_record_the_checkout_they_were_written_in(repo, tmp_path, monkeypatch):
    root, _, v2 = repo
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path / "ov"))
    assert cli.main(["ingest", "HEAD", "--reviewset", "x"]) == 0
    sha = v2[:7]
    plan = {
        "changes": [
            {
                "title": "t",
                "risk": "green",
                "related": [{"path": "b.py", "line": 2}],
                "deltas": [
                    {
                        "kind": "insert",
                        "commit": sha,
                        "after": {"path": "b.py", "lines": "1"},
                        "related": [{"path": "b.py", "line": 1}],
                    }
                ],
            }
        ]
    }
    planfile = tmp_path / "plan.json"
    planfile.write_text(json.dumps(plan))
    assert cli.main(["plan", str(planfile), "--reviewset", "x"]) == 0
    (change,) = Store(reviewset="x").changes()
    assert change.links[0].rev == v2
    assert change.deltas[0].links[0].rev == v2

    assert cli.main(["link", change.id, "b.py:3", "--reviewset", "x"]) == 0
    assert Store(reviewset="x").changes()[0].links[-1].rev == v2


def app_with_link(tmp_path, rev, monkeypatch):
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.delenv("VISUAL", raising=False)
    store = Store(tmp_path / "ov")
    store.save_source(
        Source("s1", "msg", [FileDiff("b.py", ["@@ -1 +1 @@", "+x"])], seq=1)
    )
    store.save_change(
        Change(
            "s1-01",
            "T",
            [ins("s1", "b.py", "1")],
            planned=True,
            links=[Link("b.py", 2, "the second line", rev)],
        )
    )
    app = OverseerApp(store)
    ran, notes = [], []
    app.run_editor = lambda cmd, gui: ran.append(cmd)
    app.notify = lambda msg, **kw: notes.append(msg)
    return app, ran, notes


def link_text(app):
    return " ".join(
        str(i.query_one("Static").render()) for i in app.query("#links ListItem")
    )


async def test_matching_commit_opens_the_real_file_read_write(
    repo, tmp_path, monkeypatch
):
    _, _, v2 = repo
    app, ran, notes = app_with_link(tmp_path, v2, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "@" not in link_text(app)  # no pinned marker
        await pilot.press("l", "enter")
        assert ran == [["vim", "+2", "b.py"]] and notes == []


async def test_other_commit_opens_a_read_only_copy_from_that_commit(
    repo, tmp_path, monkeypatch
):
    root, v1, v2 = repo
    app, ran, notes = app_with_link(tmp_path, v1, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert f"@{v1[:7]}" in link_text(app)
        await pilot.press("l", "enter")
        (cmd,) = ran
        copy = cmd[-1]
        assert cmd[:2] == ["vim", "+2"] and copy != "b.py" and copy.endswith("/b.py")
        with open(copy) as f:
            assert f.read() == "one\ntwo\n"  # the lines the agent saw
        assert not os.access(copy, os.W_OK) or not (
            os.stat(copy).st_mode & stat.S_IWUSR
        )
        assert (root / "b.py").read_text() == "zero\none\ntwo\n"  # real file untouched
        assert "read-only" in notes[-1] and "O opens" in notes[-1]
        pin_dir = copy.split("/b.py")[0].rsplit("/", 1)[0]
    assert not os.path.exists(pin_dir)  # cleaned up on exit


async def test_O_opens_the_working_tree_file_even_when_pinned(
    repo, tmp_path, monkeypatch
):
    _, v1, _ = repo
    app, ran, _ = app_with_link(tmp_path, v1, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("O")  # links pane not focused: does nothing
        assert ran == []
        await pilot.press("l", "O")
        assert ran == [["vim", "+2", "b.py"]]


async def test_unavailable_commit_falls_back_with_a_warning(
    repo, tmp_path, monkeypatch
):
    app, ran, notes = app_with_link(tmp_path, "f" * 40, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l", "enter")
        assert ran == [["vim", "+2", "b.py"]]
        assert "not available" in notes[-1] and "may not match" in notes[-1]


async def test_unknown_commit_in_old_links_behaves_as_before(
    repo, tmp_path, monkeypatch
):
    app, ran, _ = app_with_link(tmp_path, "", monkeypatch)  # pre-rev link
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l", "enter")
        assert ran == [["vim", "+2", "b.py"]]
