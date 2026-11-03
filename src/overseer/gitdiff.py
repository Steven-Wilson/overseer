"""Turn git commits into `Change`s with per-hunk granularity.

Shells out to `git`; must not import textual.
"""

from __future__ import annotations

import subprocess

from overseer.model import Change, Hunk


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace"
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def parse_patch(patch: str) -> list[Hunk]:
    """Split a unified `git diff` into hunks. Non-text file changes (binary,
    mode-only, empty new files) become a single line-less hunk with a note."""
    hunks: list[Hunk] = []
    path = ""
    current: Hunk | None = None
    file_has_hunk = False
    note = ""

    def flush_file() -> None:
        if path and not file_has_hunk:
            hunks.append(Hunk(path=path, header=note or "(no textual changes)"))

    in_header = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush_file()
            # "diff --git a/x b/x"; fixed up by ---/+++ lines when present
            path = line.rsplit(" b/", 1)[-1]
            current, file_has_hunk, note, in_header = None, False, "", True
        elif in_header and line.startswith("--- "):
            if line != "--- /dev/null":
                path = line[6:].split("\t")[0]
        elif in_header and line.startswith("+++ "):
            if line != "+++ /dev/null":
                path = line[6:].split("\t")[0]
        elif line.startswith("@@"):
            in_header = False
            current = Hunk(path=path, header=line)
            hunks.append(current)
            file_has_hunk = True
        elif current is not None and (line[:1] in "+- \\" and line):
            current.lines.append(line)
        elif in_header and line.startswith("Binary files"):
            note = "(binary file changed)"
        elif in_header and line.startswith("new file mode"):
            note = "(new empty file)"
        elif in_header and line.startswith("deleted file mode"):
            note = "(file deleted)"
        elif in_header and line.startswith("old mode"):
            note = "(mode changed)"
        elif current is not None and line == "":
            current.lines.append(" ")  # blank context line stripped of its space
    flush_file()
    return hunks


def commit_to_change(sha: str, cwd: str | None = None) -> Change:
    full = _git("rev-parse", "--verify", f"{sha}^{{commit}}", cwd=cwd).strip()
    message = _git("show", "-s", "--format=%B", full, cwd=cwd).strip()
    # --no-renames keeps each file's hunks self-describing; -m is not used, so
    # merge commits show no diff.
    patch = _git(
        "show", "--format=", "--patch", "--no-color", "--no-renames", full, cwd=cwd
    )
    title = message.splitlines()[0] if message else full[:7]
    return Change(
        id=full[:7],
        title=title,
        message=message,
        hunks=parse_patch(patch),
    )


def resolve_commits(rev: str, cwd: str | None = None) -> list[str]:
    """`A..B` expands to every commit in the range, oldest first; anything else
    is a single commit."""
    if ".." in rev:
        shas = _git("rev-list", "--reverse", rev, cwd=cwd).split()
        if not shas:
            raise GitError(f"no commits in range {rev}")
        return shas
    return [rev]
