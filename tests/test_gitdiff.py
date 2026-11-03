import subprocess

import pytest

from overseer import cli, gitdiff
from overseer.gitdiff import parse_patch


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("".join(f"line {i}\n" for i in range(40)))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    lines = (tmp_path / "a.txt").read_text().splitlines()
    lines[2] = "changed [early]"
    lines[35] = "changed late"
    (tmp_path / "a.txt").write_text("\n".join(lines) + "\n")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Tweak things\n\nBecause reasons.")
    return tmp_path


def test_commit_to_change_splits_hunks(repo):
    change = gitdiff.commit_to_change("HEAD", cwd=str(repo))
    assert change.title == "Tweak things"
    assert "Because reasons." in change.message
    text = [h for h in change.hunks if h.path == "a.txt"]
    assert len(text) == 2
    assert "-line 2" in text[0].lines and "+changed [early]" in text[0].lines
    binary = [h for h in change.hunks if h.path == "b.bin"]
    assert binary[0].header == "(binary file changed)"


def test_range_expands_oldest_first(repo):
    (repo / "c.txt").write_text("c\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "third")
    shas = gitdiff.resolve_commits("HEAD~2..HEAD", cwd=str(repo))
    titles = [gitdiff.commit_to_change(s, cwd=str(repo)).title for s in shas]
    assert titles == ["Tweak things", "third"]


def test_root_commit_and_deleted_file(repo):
    (repo / "a.txt").unlink()
    git(repo, "commit", "-qam", "drop")
    change = gitdiff.commit_to_change("HEAD", cwd=str(repo))
    assert change.hunks and all(h.path == "a.txt" for h in change.hunks)
    root = gitdiff.commit_to_change("HEAD~2", cwd=str(repo))
    assert root.title == "initial" and root.hunks[0].path == "a.txt"


def test_parse_patch_blank_context_line():
    patch = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"
    (hunk,) = parse_patch(patch)
    assert hunk.lines == [" a", " ", "-b", "+c"]


def test_ingest_cli_preserves_answers(repo, tmp_path_factory, monkeypatch, capsys):
    store_dir = tmp_path_factory.mktemp("store")
    monkeypatch.setenv("OVERSEER_DIR", str(store_dir))
    monkeypatch.chdir(repo)
    assert cli.main(["ingest", "HEAD"]) == 0
    sha = capsys.readouterr().out.split()[0]
    cli.main(["record", sha, "--question", "why", "--text", "kept"])
    cli.main(["ingest", "HEAD"])
    capsys.readouterr()
    cli.main(["list"])
    import json

    (c,) = json.loads(capsys.readouterr().out)
    assert c["answers"] == {"why": "kept"} and c["hunks"]


def test_ingest_bad_rev(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    monkeypatch.chdir(repo)
    assert cli.main(["ingest", "nope"]) == 1
    assert "error" in capsys.readouterr().err
