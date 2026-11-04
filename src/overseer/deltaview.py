"""Show one delta: its changed lines plus a little context, with real line numbers.

A `Delta` is a set of changed lines of one file, chosen by the agent, so it does
not line up with git's hunks. This module cuts it out of the stored `FileDiff`:
the delta's own lines, a few lines of context around them, and, dimmed, any
changed lines in that window that belong to *other* deltas (so a context line is
never a lie). Git's `@@` headers are used for line numbers and for the enclosing
function name, and are otherwise not shown.

Nothing here may import textual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from overseer.model import FileDiff, is_changed

CONTEXT = 3
_HEADER = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)")


@dataclass
class Row:
    kind: str  # "sel" in the delta | "other" changed line of another delta |
    #            "ctx" unchanged | "gap" lines skipped | "note" non-text change
    sign: str = ""  # "+", "-" or " " (blank for gap/note)
    text: str = ""  # the line without its sign; for a gap, the enclosing function
    old: int | None = None  # line number before the commit
    new: int | None = None  # line number after the commit
    # character ranges of `text` that differ from the paired line on the other
    # side of a small edit ("the `and b` that was added")
    emph: list[tuple[int, int]] = field(default_factory=list)
    # in a move: whether the line reappears on the other side (ignoring indent)
    match: bool | None = None


@dataclass
class _Entry:
    kind: str  # "header" | "add" | "del" | "ctx" | "meta" | "note"
    text: str
    number: int | None  # changed-line number
    old: int | None
    new: int | None
    section: str  # text after the closing @@ of the git hunk this line is in


def _entries(file: FileDiff) -> list[_Entry]:
    out: list[_Entry] = []
    old = new = number = 0
    section = ""
    for line in file.lines:
        head, body = line[:1], line[1:]
        if line.startswith("@@"):
            m = _HEADER.match(line)
            if m:
                old, new, section = int(m[1]), int(m[2]), m[3].strip()
            out.append(_Entry("header", line, None, None, None, section))
        elif head == "+":
            number += 1
            out.append(_Entry("add", body, number, None, new, section))
            new += 1
        elif head == "-":
            number += 1
            out.append(_Entry("del", body, number, old, None, section))
            old += 1
        elif head == "!":
            number += 1
            out.append(_Entry("note", body, number, None, None, section))
        elif head == "\\":
            out.append(_Entry("meta", line, None, None, None, section))
        else:
            out.append(_Entry("ctx", body, None, old, new, section))
            old += 1
            new += 1
    return out


def delta_rows(file: FileDiff, numbers: set[int] | list[int]) -> list[Row]:
    """The rows to show for the changed lines `numbers` of `file`."""
    wanted = set(numbers)
    entries = _entries(file)
    chosen = [i for i, e in enumerate(entries) if e.number in wanted]
    visible: set[int] = set()
    for i in chosen:
        visible.add(i)
        for step in (-1, 1):
            j, used = i + step, 0
            while 0 <= j < len(entries) and entries[j].kind != "header":
                visible.add(j)
                if entries[j].kind != "meta":
                    used += 1
                    if used >= CONTEXT:
                        break
                j += step

    # `\ No newline at end of file` belongs to the line before it
    visible |= {
        i + 1 for i in visible if i + 1 < len(entries) and entries[i + 1].kind == "meta"
    }

    rows: list[Row] = []
    prev = -2
    for i in sorted(visible):
        e = entries[i]
        if i != prev + 1:  # a break: show where we are in the file
            at_start = i == 0 or entries[i - 1].kind == "header"
            if rows or e.section or not at_start:
                rows.append(Row("gap", text=e.section))
        prev = i
        if e.kind == "meta":
            rows.append(Row("ctx", text=e.text))
        elif e.kind == "note":
            rows.append(Row("note", text=e.text))
        elif e.kind == "ctx":
            rows.append(Row("ctx", " ", e.text, e.old, e.new))
        else:
            kind = "sel" if e.number in wanted else "other"
            sign = "+" if e.kind == "add" else "-"
            rows.append(Row(kind, sign, e.text, e.old, e.new))
    _emphasise(rows)
    return rows


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


def kind_of(file: FileDiff, numbers: set[int] | list[int]) -> str:
    """How to show a delta: `added` (only new lines: a section), `removed` (only
    deleted lines) or `edit` (a mix: a small change to existing code)."""
    wanted = set(numbers)
    signs = {
        line[0]
        for n, line in enumerate((x for x in file.lines if is_changed(x)), 1)
        if n in wanted
    }
    if signs == {"+"}:
        return "added"
    if signs == {"-"}:
        return "removed"
    return "edit"


@dataclass
class MoveView:
    removed: list[Row]  # where the code was
    added: list[Row]  # where it is now
    same: int  # lines that reappear unchanged apart from indentation
    total: int  # lines in the removed side
    reindent: int | None  # uniform indentation change (+4, -4) if there was one


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def move_view(
    file: FileDiff, numbers: list[int], dest: FileDiff, to_numbers: list[int]
) -> MoveView:
    """Both ends of a move, with each line marked as matched (it reappears on
    the other side ignoring indentation) or not (edited, dropped or new)."""

    def side(f: FileDiff, nums: list[int], sign: str) -> list[Row]:
        wanted = set(nums)
        out = []
        for e in _entries(f):
            if e.number in wanted and e.kind in ("add", "del"):
                out.append(Row("sel", sign, e.text, e.old, e.new))
        return out

    removed, added = side(file, numbers, "-"), side(dest, to_numbers, "+")
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


def new_range(rows: list[Row]) -> tuple[int, int] | None:
    """First and last line, in the file after the commit, that the delta touches.
    None when it only removes lines."""
    nums = [r.new for r in rows if r.kind == "sel" and r.sign == "+" and r.new]
    return (min(nums), max(nums)) if nums else None


def plain(rows: list[Row]) -> list[str]:
    """The delta as text lines (its own and context only, in diff style), for
    machine readers."""
    out = []
    for r in rows:
        if r.kind == "gap":
            out.append(f"... {r.text}".rstrip())
        elif r.kind in ("sel", "ctx"):
            out.append(f"{r.sign}{r.text}")
        elif r.kind == "note":
            out.append(f"! {r.text}")
    return out


def stats(file: FileDiff, numbers: set[int] | list[int]) -> tuple[int, int]:
    """(added, removed) among the delta's changed lines."""
    wanted = set(numbers)
    changed = [line for line in file.lines if is_changed(line)]
    picked = [line for n, line in enumerate(changed, 1) if n in wanted]
    return (
        sum(1 for line in picked if line[0] == "+"),
        sum(1 for line in picked if line[0] == "-"),
    )
