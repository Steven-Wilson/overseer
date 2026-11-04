"""Find code that was moved, not rewritten.

`git diff` shows a moved block as a deletion in one place and an unrelated
addition in another. Spotting that is mechanical enough to hand to the agent as
a hint: runs of removed lines whose text (ignoring indentation) reappears as a
run of added lines, in the same file or another. The agent decides whether it
really is a move and says so in its plan (`moved_to`); nothing here is trusted
by the store.

Nothing here may import textual.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from overseer.model import Source, is_changed

MIN_LINES = 3
MIN_CHARS = 40  # of text across the run, so a few `}` and blank lines don't count


@dataclass
class Move:
    path: str
    lines: list[int]  # removed changed-line numbers
    to_path: str
    to_lines: list[int]  # added changed-line numbers

    @property
    def size(self) -> int:
        return len(self.lines)


@dataclass
class _Run:
    path: str
    numbers: list[int]
    texts: list[str]  # stripped


def _runs(source: Source, sign: str) -> list[_Run]:
    """Maximal runs of adjacent changed lines with the given sign, per file."""
    out: list[_Run] = []
    for f in source.files:
        n, cur = 0, None
        for line in f.lines:
            if is_changed(line):
                n += 1
            if line[:1] == sign:
                if cur is None:
                    cur = _Run(f.path, [], [])
                    out.append(cur)
                cur.numbers.append(n)
                cur.texts.append(line[1:].strip())
            else:
                cur = None
    return out


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
                            r.numbers[m.a : m.a + m.size],
                            a.path,
                            a.numbers[m.b : m.b + m.size],
                        )
                    )
    return sorted(found, key=lambda m: -m.size)
