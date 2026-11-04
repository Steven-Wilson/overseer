"""Text for showing one delta, shaped by what kind of change it is.

* an **edit** (removed and added lines mixed): a diff with an old/new line-number
  gutter, and the exact words that differ between a removed line and the line
  replacing it emphasised, so `if a:` -> `if a and b:` points at `and b`;
* an **added** or **removed section** (only `+` or only `-` lines): the whole
  section, syntax highlighted, beside a green or red accent bar;
* a **move**: the removed lines ("removed from here") and the added lines
  ("added here"), with lines that did not survive the trip tinted.

Built as `rich.text.Text`, never markup, because code is full of brackets.
No textual import.
"""

from __future__ import annotations

from rich.syntax import Syntax
from rich.text import Text

from overseer.deltaview import Row, delta_rows, kind_of, move_view
from overseer.store import Resolved

EMPH = {"+": "bold white on dark_green", "-": "bold white on dark_red"}
TINT = {"green": "on dark_green", "red": "on dark_red"}
BAR = "▌ "


def highlighted(lines: list[str], path: str) -> list[Text]:
    """One styled `Text` per input line, lexed as `path`'s language."""
    code = "\n".join(lines)
    lexer = Syntax.guess_lexer(path, code)
    out = Syntax(code, lexer, theme="ansi_dark").highlight(code).split("\n")
    out = [t.copy() for t in out][: len(lines)]
    return out + [Text(x) for x in lines[len(out) :]]  # lexer ate a line: be safe


def _gap(out: Text, row: Row) -> None:
    out.append(f"{'⋯':>9}  {row.text}".rstrip() + "\n", style="dim magenta")


def _edit(out: Text, rows: list[Row]) -> None:
    for r in rows:
        if r.kind == "gap":
            _gap(out, r)
        elif r.kind == "note":
            out.append(f"{'':>10}{r.text}\n", style="italic dim")
        else:
            old = "" if r.old is None else str(r.old)
            new = "" if r.new is None else str(r.new)
            out.append(f"{old:>4} {new:>4}  ", style="dim")
            style = {
                ("sel", "+"): "green",
                ("sel", "-"): "red",
                ("other", "+"): "dim",
                ("other", "-"): "dim",
            }.get((r.kind, r.sign), "")
            line = Text(f"{r.sign}{r.text}", style=style)
            for a, b in r.emph:  # +1 for the sign
                line.stylize(EMPH[r.sign], a + 1, b + 1)
            out.append_text(line)
            out.append("\n")


def _section(out: Text, rows: list[Row], kind: str, path: str) -> None:
    color = "green" if kind == "added" else "red"
    code = iter(highlighted([r.text for r in rows if r.kind == "sel"], path))
    for r in rows:
        if r.kind == "gap":
            _gap(out, r)
        elif r.kind == "note":
            out.append(f"{'':>5} {BAR}{r.text}\n", style="italic dim")
        elif r.kind == "sel":
            n = r.new if kind == "added" else r.old
            out.append(f"{n:>5} ", style="dim")
            out.append(BAR, style=color)
            out.append_text(next(code))
            out.append("\n")
        else:  # context, and changed lines of other deltas, quietly
            n = r.new if r.new is not None else r.old
            out.append(f"{'' if n is None else n:>5}   {r.sign}{r.text}\n", style="dim")


def _side(out: Text, label: str, path: str, rows: list[Row], color: str) -> None:
    nums = [r.old if r.sign == "-" else r.new for r in rows]
    nums = [n for n in nums if n]
    span = f":{min(nums)}-{max(nums)}" if nums else ""
    out.append(f"{label} · {path}{span}\n", style=f"bold {color}")
    code = highlighted([r.text for r in rows], path)
    for r, line in zip(rows, code, strict=True):
        if not r.match:
            line.stylize(TINT[color])
        n = r.old if r.sign == "-" else r.new
        out.append(f"{'' if n is None else n:>5} ", style="dim")
        out.append(BAR, style=color)
        out.append_text(line)
        out.append("\n")


def _move(out: Text, r: Resolved) -> None:
    assert r.dest is not None
    mv = move_view(r.file, r.numbers, r.dest, r.to_numbers)
    note = f"{mv.same} of {mv.total} lines moved unchanged"
    if mv.reindent:
        note += f", re-indented {mv.reindent:+d}"
    out.append(f"Moved  {r.delta.path} → {r.delta.to_path}  ", style="bold")
    out.append(f"({note})\n", style="dim")
    _side(out, "removed from here", r.delta.path, mv.removed, "red")
    out.append("\n")
    _side(out, "added here", r.delta.to_path, mv.added, "green")
    if any(not x.match for x in mv.removed + mv.added):
        out.append(
            "(tinted lines have no match on the other side)\n", style="dim italic"
        )


def append_delta(out: Text, r: Resolved) -> None:
    """The body for one delta, chosen by its kind."""
    if r.delta.moved:
        _move(out, r)
        return
    rows = delta_rows(r.file, r.numbers)
    kind = kind_of(r.file, r.numbers)
    if any(row.kind == "other" for row in rows):
        out.append("(dim lines belong to other deltas)\n", style="dim italic")
    if kind == "edit":
        _edit(out, rows)
    else:
        _section(out, rows, kind, r.delta.path)
