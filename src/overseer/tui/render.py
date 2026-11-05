"""Text for showing one delta, shaped by what kind of change it is.

Shaped by the kind the agent declared, never guessed from the lines:

* a **change** (before/after): a diff with an old/new line-number gutter, and the
  exact words that differ between a removed line and the line replacing it
  emphasised, so `if a:` -> `if a and b:` points at `and b`;
* an **insert** or a **delete**: the whole code, syntax highlighted, beside a
  green or red accent bar;
* a **move**: the removed lines ("removed from here") and the added lines
  ("added here"), with lines that did not survive the trip tinted;
* a **raw** delta (an undecomposed file): git's diff, labelled unclassified.

Built as `rich.text.Text`, never markup, because code is full of brackets.
No textual import.
"""

from __future__ import annotations

from rich.syntax import Syntax
from rich.text import Text

from overseer.deltaview import Row, change_rows, move_view
from overseer.model import Delta, Side
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


def _label(out: Text, side: Side, color: str, verb: str = "") -> None:
    sym = f" · {side.symbol}" if side.symbol else ""
    where = f"{side.path}:{side.lines}" if side.lines else side.path
    out.append(f"{verb}{where}{sym}\n", style=f"bold {color}")


def _edit(out: Text, rows: list[Row]) -> None:
    for r in rows:
        old = "" if r.old is None else str(r.old)
        new = "" if r.new is None else str(r.new)
        out.append(f"{old:>4} {new:>4}  ", style="dim")
        style = {"+": "green", "-": "red"}.get(r.sign, "dim")
        line = Text(f"{r.sign}{r.text}", style=style)
        for a, b in r.emph:  # +1 for the sign
            line.stylize(EMPH[r.sign], a + 1, b + 1)
        out.append_text(line)
        out.append("\n")


def _section(out: Text, side: Side, color: str) -> None:
    """The whole code, highlighted, beside an accent bar: no signs, no gutter noise."""
    _label(out, side, color)
    lines = side.text.split("\n") if side.text else []
    for k, line in enumerate(highlighted(lines, side.path)):
        out.append(f"{side.start + k:>5} ", style="dim")
        out.append(BAR, style=color)
        out.append_text(line)
        out.append("\n")


def _raw(out: Text, d: Delta) -> None:
    out.append(
        "Unclassified: this is git's diff, not a declared delta.\n",
        style="italic yellow",
    )
    for line in d.diff:
        style = {"+": "green", "-": "red", "@": "magenta"}.get(line[:1], "dim")
        out.append(f"{line}\n", style=style)


def _side(out: Text, label: str, side: Side, rows: list[Row], color: str) -> None:
    _label(out, side, color, f"{label} · ")
    code = highlighted([r.text for r in rows], side.path)
    for r, line in zip(rows, code, strict=True):
        if not r.match:
            line.stylize(TINT[color])
        n = r.old if r.sign == "-" else r.new
        out.append(f"{'' if n is None else n:>5} ", style="dim")
        out.append(BAR, style=color)
        out.append_text(line)
        out.append("\n")


def _move(out: Text, d: Delta) -> None:
    assert d.before and d.after
    mv = move_view(d)
    note = f"{mv.same} of {mv.total} lines moved unchanged"
    if mv.reindent:
        note += f", re-indented {mv.reindent:+d}"
    out.append(f"Moved  {d.before.path} → {d.after.path}  ", style="bold")
    out.append(f"({note})\n", style="dim")
    _side(out, "removed from here", d.before, mv.removed, "red")
    out.append("\n")
    _side(out, "added here", d.after, mv.added, "green")
    if any(not x.match for x in mv.removed + mv.added):
        out.append(
            "(tinted lines have no match on the other side)\n", style="dim italic"
        )


def append_delta(out: Text, r: Resolved) -> None:
    """The body for one delta, chosen by the kind the agent declared."""
    d = r.delta
    if d.kind == "raw":
        _raw(out, d)
    elif d.kind == "insert":
        _section(out, d.after, "green")
    elif d.kind == "delete":
        _section(out, d.before, "red")
    elif d.kind == "move":
        _move(out, d)
    else:  # change
        _label(out, d.before, "cyan")
        _edit(out, change_rows(d.before, d.after))
