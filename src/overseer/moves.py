"""Find code that was moved, not rewritten.

`git diff` shows a moved block as a deletion in one place and an unrelated
addition in another. Spotting that is mechanical enough to hand to the agent as
a hint: runs of removed lines whose text (ignoring indentation) reappears as a
run of added lines, in the same file or another. The agent decides whether it
really is a move and says so in its plan (`kind: "move"`); nothing here is trusted
by the store.

Nothing here may import textual.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from overseer.deltaview import entries
from overseer.model import Source

MIN_LINES = 3
MIN_CHARS = 40  # of text across the run, so a few `}` and blank lines don't count


@dataclass
class Move:
    path: str
    lines: str  # where the run was removed: old-file lines, "12-20"
    to_path: str
    to_lines: str  # where it reappears: new-file lines
    size: int


@dataclass
class _Run:
    path: str
    numbers: list[int]  # file line numbers (old file for `-`, new file for `+`)
    texts: list[str]  # stripped


def _runs(source: Source, sign: str) -> list[_Run]:
    """Maximal runs of adjacent changed lines with the given sign, per file."""
    out: list[_Run] = []
    for f in source.files:
        cur = None
        for e in entries(f):
            n = e.old if sign == "-" else e.new
            if e.kind == ("del" if sign == "-" else "add"):
                if cur is None:
                    cur = _Run(f.path, [], [])
                    out.append(cur)
                cur.numbers.append(n)
                cur.texts.append(e.text.strip())
            elif e.kind != "meta":
                cur = None
    return out


def _span(numbers: list[int]) -> str:
    return f"{numbers[0]}-{numbers[-1]}" if len(numbers) > 1 else str(numbers[0])


def find_moves(source: Source) -> list[Move]:
    found: list[Move] = []
    removed, added = _runs(source, "-"), _runs(source, "+")
    for r in removed:
        if len(r.texts) < MIN_LINES:
            continue
        for a in added:
            if len(a.texts) < MIN_LINES:
                continue
            sm = SequenceMatcher(None, r.texts, a.texts, autojunk=False)
            for m in sm.get_matching_blocks():
                texts = r.texts[m.a : m.a + m.size]
                if (
                    m.size >= MIN_LINES
                    and sum(1 for t in texts if t) >= MIN_LINES
                    and sum(len(t) for t in texts) >= MIN_CHARS
                ):
                    found.append(
                        Move(
                            r.path,
                            _span(r.numbers[m.a : m.a + m.size]),
                            a.path,
                            _span(a.numbers[m.b : m.b + m.size]),
                            m.size,
                        )
                    )
    return sorted(found, key=lambda m: -m.size)
