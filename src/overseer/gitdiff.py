"""Turn git commits into `Source`s: one `FileDiff` per file.

Shells out to `git`; must not import textual.
"""

from __future__ import annotations

import subprocess

from overseer.model import FileDiff, Source, is_changed


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace"
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def head_rev(cwd: str | None = None) -> str:
    """Full sha of HEAD, or "" outside a repo or in one with no commits."""
    try:
        return _git("rev-parse", "--verify", "HEAD", cwd=cwd).strip()
    except (GitError, OSError):
        return ""


def file_at(rev: str, path: str, cwd: str | None = None) -> bytes:
    """`path` (relative to the repo root) as committed in `rev`. Raises GitError
    if the commit is not available locally or the file did not exist there."""
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=cwd, capture_output=True
    )
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        raise GitError(err or f"cannot read {path} at {rev[:7]}")
    return proc.stdout


def parse_patch(patch: str) -> list[FileDiff]:
    """Split a unified `git diff` into one `FileDiff` per file. Git's hunk
    boundaries survive only as `@@` header lines inside `lines`; they are not
    review units. A non-text change (binary, mode-only, empty new file) becomes
    the single line `!(note)`."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    note = ""
    in_header = False
    in_hunk = False

    def flush_file() -> None:
        if current is not None:
            if not any(is_changed(line) for line in current.lines):
                current.lines.append(f"!{note or '(no textual changes)'}")
            files.append(current)

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush_file()
            # "diff --git a/x b/x"; fixed up by ---/+++ lines when present
            current = FileDiff(path=line.rsplit(" b/", 1)[-1])
            note, in_header, in_hunk = "", True, False
        elif current is None:
            continue
        elif in_header and line.startswith("--- "):
            if line != "--- /dev/null":
                current.path = line[6:].split("\t")[0]
        elif in_header and line.startswith("+++ "):
            if line != "+++ /dev/null":
                current.path = line[6:].split("\t")[0]
        elif line.startswith("@@"):
            in_header, in_hunk = False, True
            current.lines.append(line)
        elif in_hunk and (line[:1] in "+- \\" and line):
            current.lines.append(line)
        elif in_header and line.startswith("Binary files"):
            note = "(binary file changed)"
        elif in_header and line.startswith("new file mode"):
            note = "(new empty file)"
        elif in_header and line.startswith("deleted file mode"):
            note = "(file deleted)"
        elif in_header and line.startswith("old mode"):
            note = "(mode changed)"
        elif in_hunk and line == "":
            current.lines.append(" ")  # blank context line stripped of its space
    flush_file()
    return files


def commit_to_source(sha: str, cwd: str | None = None) -> Source:
    full = _git("rev-parse", "--verify", f"{sha}^{{commit}}", cwd=cwd).strip()
    message = _git("show", "-s", "--format=%B", full, cwd=cwd).strip()
    # --no-renames keeps each file self-describing; -m is not used, so merge
    # commits show no diff.
    patch = _git(
        "show", "--format=", "--patch", "--no-color", "--no-renames", full, cwd=cwd
    )
    return Source(id=full[:7], message=message, files=parse_patch(patch))


def resolve_commits(rev: str, cwd: str | None = None) -> list[str]:
    """`A..B` expands to every commit in the range, oldest first; anything else
    is a single commit."""
    if ".." in rev:
        shas = _git("rev-list", "--reverse", rev, cwd=cwd).split()
        if not shas:
            raise GitError(f"no commits in range {rev}")
        return shas
    return [rev]
