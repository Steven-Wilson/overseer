"""What to show for a declared delta, and how it relates to the raw commit.

The agent declares each delta (kind, where, what the code looked like); nothing
here decides what a change *means*. This module only lays the declared text out:
a before/after pair as a small diff with the differing words marked, a move as
two matched sides. It also reads the raw git diff for two conveniences:
`side_text` (fill in code the agent located but did not retype) and `uncovered`
(changed lines no delta accounts for, a warning, never a rejection).

Nothing here may import textual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from overseer.model import Change, Delta, FileDiff, Side, Source, parse_lines

_HEADER = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)")


@dataclass
class Row:
    kind: str  # "sel" a changed line | "ctx" unchanged | "note" non-text change
    sign: str = ""  # "+", "-" or " " (blank for a note)
    text: str = ""  # the line without its sign
    old: int | None = None  # line number before the commit
    new: int | None = None  # line number after the commit
    # character ranges of `text` that differ from the paired line on the other
    # side of a small edit ("the `and b` that was added")
    emph: list[tuple[int, int]] = field(default_factory=list)
    # in a move: whether the line reappears on the other side (ignoring indent)
    match: bool | None = None


@dataclass
class Entry:
    kind: str  # "header" | "add" | "del" | "ctx" | "meta" | "note"
    text: str
    number: int | None  # changed-line number
    old: int | None
    new: int | None
    section: str  # text after the closing @@ of the git hunk this line is in


def entries(file: FileDiff) -> list[Entry]:
    out: list[Entry] = []
    old = new = number = 0
    section = ""
    for line in file.lines:
        head, body = line[:1], line[1:]
        if line.startswith("@@"):
            m = _HEADER.match(line)
            if m:
                old, new, section = int(m[1]), int(m[2]), m[3].strip()
            out.append(Entry("header", line, None, None, None, section))
        elif head == "+":
            number += 1
            out.append(Entry("add", body, number, None, new, section))
            new += 1
        elif head == "-":
            number += 1
            out.append(Entry("del", body, number, old, None, section))
            old += 1
        elif head == "!":
            number += 1
            out.append(Entry("note", body, number, None, None, section))
        elif head == "\\":
            out.append(Entry("meta", line, None, None, None, section))
        else:
            out.append(Entry("ctx", body, None, old, new, section))
            old += 1
            new += 1
    return out


_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")
SIMILAR = 0.5  # how alike a removed and an added line must be to count as an edit


def _emphasise(rows: list[Row]) -> None:
    """For a removed line immediately followed by an added line that is mostly the
    same (`if a:` -> `if a and b:`), mark just the tokens that differ."""
    i = 0
    while i < len(rows):
        if not (rows[i].kind == "sel" and rows[i].sign == "-"):
            i += 1
            continue
        j = i
        while j < len(rows) and rows[j].kind == "sel" and rows[j].sign == "-":
            j += 1
        k = j
        while k < len(rows) and rows[k].kind == "sel" and rows[k].sign == "+":
            k += 1
        for old, new in zip(rows[i:j], rows[j:k], strict=False):
            _pair(old, new)
        i = max(k, i + 1)


def _pair(old: Row, new: Row) -> None:
    a, b = _TOKEN.findall(old.text), _TOKEN.findall(new.text)
    words = SequenceMatcher(
        None,
        [t for t in a if not t.isspace()],
        [t for t in b if not t.isspace()],
        autojunk=False,
    )
    if words.ratio() < SIMILAR:
        return  # a different line, not an edit of this one
    sm = SequenceMatcher(None, a, b, autojunk=False)
    pos_a = [0]
    for t in a:
        pos_a.append(pos_a[-1] + len(t))
    pos_b = [0]
    for t in b:
        pos_b.append(pos_b[-1] + len(t))
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            continue
        if a1 > a0:
            old.emph.append((pos_a[a0], pos_a[a1]))
        if b1 > b0:
            new.emph.append((pos_b[b0], pos_b[b1]))


def _lines(text: str) -> list[str]:
    return text.split("\n") if text else []


def count(side: Side | None) -> int:
    return len(_lines(side.text)) if side else 0


def change_rows(before: Side, after: Side) -> list[Row]:
    """A small diff of what the agent said the code was and now is: shared lines
    as context, the rest removed/added, with differing words marked."""
    a, b = _lines(before.text), _lines(after.text)
    rows: list[Row] = []
    for op, a0, a1, b0, b1 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(a1 - a0):
                rows.append(
                    Row(
                        "ctx",
                        " ",
                        a[a0 + k],
                        before.start + a0 + k,
                        after.start + b0 + k,
                    )
                )
            continue
        for k in range(a0, a1):
            rows.append(Row("sel", "-", a[k], before.start + k, None))
        for k in range(b0, b1):
            rows.append(Row("sel", "+", b[k], None, after.start + k))
    _emphasise(rows)
    return rows


def stats(delta: Delta) -> tuple[int, int]:
    """(added, removed) lines the delta shows."""
    if delta.kind == "insert":
        return count(delta.after), 0
    if delta.kind == "delete":
        return 0, count(delta.before)
    if delta.kind == "change":
        rows = change_rows(delta.before, delta.after)
        return (
            sum(1 for r in rows if r.sign == "+"),
            sum(1 for r in rows if r.sign == "-"),
        )
    if delta.kind == "raw":
        return (
            sum(1 for x in delta.diff if x[:1] == "+"),
            sum(1 for x in delta.diff if x[:1] == "-"),
        )
    return 0, 0


@dataclass
class MoveView:
    removed: list[Row]  # where the code was
    added: list[Row]  # where it is now
    same: int  # lines that reappear unchanged apart from indentation
    total: int  # lines in the removed side
    reindent: int | None  # uniform indentation change (+4, -4) if there was one


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def move_view(delta: Delta) -> MoveView:
    """Both ends of a declared move, each line marked as matched (it reappears on
    the other side ignoring indentation) or not (edited, dropped or new)."""
    assert delta.before and delta.after
    removed = [
        Row("sel", "-", t, delta.before.start + k)
        for k, t in enumerate(_lines(delta.before.text))
    ]
    added = [
        Row("sel", "+", t, None, delta.after.start + k)
        for k, t in enumerate(_lines(delta.after.text))
    ]
    sm = SequenceMatcher(
        None,
        [r.text.strip() for r in removed],
        [r.text.strip() for r in added],
        autojunk=False,
    )
    shifts: set[int] = set()
    same = 0
    for r in removed + added:
        r.match = False
    for m in sm.get_matching_blocks():
        for k in range(m.size):
            a, b = removed[m.a + k], added[m.b + k]
            a.match = b.match = True
            if a.text.strip():
                same += 1
                shifts.add(_indent(b.text) - _indent(a.text))
    reindent = shifts.pop() if len(shifts) == 1 else None
    return MoveView(removed, added, same, len(removed), reindent or None)


def plain(delta: Delta) -> list[str]:
    """The delta as diff-style text lines, for machine readers."""
    if delta.kind == "raw":
        return list(delta.diff)
    out = [f"-{t}" for t in _lines(delta.before.text)] if delta.before else []
    out += [f"+{t}" for t in _lines(delta.after.text)] if delta.after else []
    return out


# -- the raw commit ------------------------------------------------------


def side_text(file: FileDiff, which: str, numbers: list[int]) -> str | None:
    """The code of `file` at lines `numbers` as the diff shows it (`which` is
    "old" for the file before the commit, "new" for after). None when the diff
    does not include all of them (only the agent can supply code the commit's
    diff has no view of)."""
    wanted = set(numbers)
    found: dict[int, str] = {}
    for e in entries(file):
        n = e.old if which == "old" else e.new
        if n in wanted and e.kind in ("ctx", "add", "del"):
            found[n] = e.text
    if wanted - set(found):
        return None
    return "\n".join(found[n] for n in sorted(found))


@dataclass
class Gap:
    """Changed lines of a commit that no delta accounts for."""

    commit: str
    path: str
    old: list[int] = field(default_factory=list)  # removed lines, pre-commit numbers
    new: list[int] = field(default_factory=list)  # added lines, post-commit numbers


def uncovered(changes: list[Change], sources: dict[str, Source]) -> list[Gap]:
    """Changed lines of the commits `changes` are cut from that no delta covers.
    Advisory: the agent's declaration is the truth, this only notices what it
    left unsaid."""
    old: dict[tuple[str, str], set[int]] = {}
    new: dict[tuple[str, str], set[int]] = {}
    raw: set[tuple[str, str]] = set()
    commits: set[str] = set()
    for c in changes:
        for d in c.deltas:
            commits.add(d.commit)
            if d.kind == "raw":
                raw.add((d.commit, d.path))
            for side, into in ((d.before, old), (d.after, new)):
                if side and side.lines:
                    try:
                        nums = parse_lines(side.lines)
                    except ValueError:
                        continue
                    into.setdefault((d.commit, side.path), set()).update(nums)
    gaps = []
    for commit in sorted(commits & set(sources)):
        for f in sources[commit].files:
            key = (commit, f.path)
            if key in raw:
                continue
            gap = Gap(commit, f.path)
            for e in entries(f):
                if e.kind == "del" and e.old not in old.get(key, ()):
                    gap.old.append(e.old)
                elif e.kind == "add" and e.new not in new.get(key, ()):
                    gap.new.append(e.new)
                elif e.kind == "note" and key not in old and key not in new:
                    gap.new.append(0)
            if gap.old or gap.new:
                gaps.append(gap)
    return gaps
