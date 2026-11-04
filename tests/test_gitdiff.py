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


def test_commit_to_source_has_one_file_diff_per_file(repo):
    src = gitdiff.commit_to_source("HEAD", cwd=str(repo))
    assert src.title == "Tweak things"
    assert "Because reasons." in src.message
    assert [f.path for f in src.files] == ["a.txt", "b.bin"]
    text = src.file("a.txt")
    # git's two hunks survive only as @@ headers; changed lines are numbered across both
    assert sum(1 for x in text.lines if x.startswith("@@")) == 2
    assert text.count == 4
    assert "-line 2" in text.lines and "+changed [early]" in text.lines
    assert src.file("b.bin").lines == ["!(binary file changed)"]
    assert src.file("b.bin").count == 1


def test_range_expands_oldest_first(repo):
    (repo / "c.txt").write_text("c\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "third")
    shas = gitdiff.resolve_commits("HEAD~2..HEAD", cwd=str(repo))
    titles = [gitdiff.commit_to_source(s, cwd=str(repo)).title for s in shas]
    assert titles == ["Tweak things", "third"]


def test_root_commit_and_deleted_file(repo):
    (repo / "a.txt").unlink()
    git(repo, "commit", "-qam", "drop")
    src = gitdiff.commit_to_source("HEAD", cwd=str(repo))
    assert [f.path for f in src.files] == ["a.txt"] and src.files[0].count == 40
    root = gitdiff.commit_to_source("HEAD~2", cwd=str(repo))
    assert root.title == "initial" and root.files[0].path == "a.txt"


def test_parse_patch_blank_context_line():
    patch = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"
    (file,) = parse_patch(patch)
    assert file.lines == ["@@ -1,3 +1,3 @@", " a", " ", "-b", "+c"]


def test_parse_patch_non_text_changes_become_one_changed_line():
    patch = "diff --git a/x b/x\nnew file mode 100644\nindex 0000000..e69de29\n"
    (file,) = parse_patch(patch)
    assert file.lines == ["!(new empty file)"] and file.count == 1


def test_ingest_bad_rev(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    monkeypatch.chdir(repo)
    assert cli.main(["ingest", "nope", "--reviewset", "default"]) == 1
    assert "error" in capsys.readouterr().err
