"""Decompose commits into reviewable `Change`s.

An agent reads a commit (`overseer-cli show`), decides how its changes group into
small, single-purpose changes, and *declares* each delta: what kind it is and what
the code looked like. Overseer does not infer meaning from the diff; what the
agent says is the source of truth. `apply_plan` checks the plan is well formed,
not that it is true, and reports (without rejecting) any changed lines of the
commit that no delta mentions.

Plan format:

    {"changes": [{"title": "...",
                  "risk": "yellow",
                  "risk_reason": "required unless green",
                  "answers": {"why": "...", "semantic": "..."},
                  "related": [{"path": "src/x.py", "line": 12, "note": "caller"}],
                  "deltas": [DELTA, ...]}]}

A DELTA says what it is. `lines` is where, in the file at that end ("12" or
"12-20": the old file for `before`, the new file for `after`); `symbol` is the
enclosing function or class; `text` is the code as it looked there (when left
out it is copied from the commit's diff if the diff shows those lines):

    {"kind": "insert", "commit": "abc1234",
     "after": {"path": "src/x.py", "symbol": "parse", "lines": "30-41"},
     "summary": "...", "related": [...]}
    {"kind": "delete", "commit": "abc1234", "before": {"path": ..., "lines": ...}}
    {"kind": "change", "commit": "abc1234",     # X became Y, in one file
     "before": {"path": "src/x.py", "lines": "12", "text": "if a:"},
     "after":  {"path": "src/x.py", "lines": "12", "text": "if a and b:"}}
    {"kind": "move", "commit": "abc1234",       # code relocated
     "before": {"path": "src/a.py", "symbol": "f", "lines": "4-12"},
     "after":  {"path": "src/b.py", "symbol": "g", "lines": "1-9"}}
"""

from __future__ import annotations

import re

from overseer import gitdiff
from overseer.deltaview import side_text
from overseer.model import (
    QUESTIONS,
    RISKS,
    Change,
    Delta,
    Link,
    Side,
    Source,
    parse_lines,
)
from overseer.store import Store

_RANGE = re.compile(r"(\d+)(?:-(\d+))?\Z")
SHAPES = {  # kind -> the sides it must have
    "insert": ("after",),
    "delete": ("before",),
    "change": ("before", "after"),
    "move": ("before", "after"),
}


class PlanError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def default_change(source: Source) -> Change:
    """1:1 wrapper so freshly ingested commits are reviewable before any
    decomposition happens: one `raw` (unclassified git diff) delta per file."""
    return Change(
        id=source.id,
        title=source.title,
        deltas=[
            Delta("raw", source.id, before=Side(f.path, "", ""), diff=list(f.lines))
            for f in source.files
        ],
    )


def _parse_links(value: object, where: str, problems: list[str]) -> list[Link]:
    if not isinstance(value, list):
        problems.append(f"{where}: must be a list of {{path, line?, note?}}")
        return []
    out = []
    for n, x in enumerate(value, 1):
        w = f"{where}[{n}]"
        if not isinstance(x, dict) or set(x) - {"path", "line", "note"}:
            problems.append(f"{w}: must be an object with path, optional line, note")
            continue
        path, line, note = x.get("path"), x.get("line"), x.get("note", "")
        if not isinstance(path, str) or not path.strip():
            problems.append(f"{w}: missing path")
        elif line is not None and (
            not isinstance(line, int) or isinstance(line, bool) or line < 1
        ):
            problems.append(f"{w}: line must be a positive integer")
        elif not isinstance(note, str):
            problems.append(f"{w}: note must be a string")
        else:
            out.append(Link(path.strip(), line, note.strip()))
    return out


def _parse_side(
    d: dict, key: str, where: str, source: Source | None, problems: list[str]
) -> Side | None:
    raw = d.get(key)
    here = f"{where}.{key}"
    if not isinstance(raw, dict) or set(raw) - {"path", "lines", "text", "symbol"}:
        problems.append(f"{here}: must be {{path, lines, symbol?, text?}}")
        return None
    path, lines = raw.get("path"), raw.get("lines")
    if not isinstance(path, str) or not path.strip():
        problems.append(f"{here}: missing path")
        return None
    m = _RANGE.match(str(lines).strip()) if lines is not None else None
    if not m or (m[2] and int(m[2]) < int(m[1])) or int(m[1]) < 1:
        problems.append(f"{here}: lines must be a line or range like '12' or '12-20'")
        return None
    spec = str(lines).strip()
    symbol = raw.get("symbol", "")
    text = raw.get("text")
    if not isinstance(symbol, str) or (text is not None and not isinstance(text, str)):
        problems.append(f"{here}: symbol and text must be strings")
        return None
    if text is None:  # copy the code from the commit's diff, if it shows it
        file = source.file(path.strip()) if source else None
        found = (
            side_text(file, "old" if key == "before" else "new", parse_lines(spec))
            if file
            else None
        )
        if found is None:
            problems.append(
                f"{here}: no text given and the commit's diff does not show "
                f"{path.strip()} lines {spec}; include the code in `text`"
            )
            return None
        text = found
    return Side(path.strip(), spec, text.strip("\n"), symbol.strip())


def _parse_delta(
    d: object, where: str, sources: dict[str, Source], problems: list[str]
) -> Delta | None:
    allowed = {"kind", "commit", "before", "after", "summary", "related"}
    if not isinstance(d, dict) or set(d) - allowed:
        problems.append(
            f"{where}: must be an object with kind, commit, before/after, "
            "summary, related"
        )
        return None
    kind, commit = d.get("kind"), d.get("commit")
    if kind not in SHAPES:
        problems.append(f"{where}: kind must be one of {', '.join(SHAPES)}")
        return None
    if not isinstance(commit, str) or commit.strip() not in sources:
        problems.append(f"{where}: unknown commit {commit!r} (ingest first)")
        return None
    commit = commit.strip()
    wanted = SHAPES[kind]
    extra = {"before", "after"} & set(d) - set(wanted)
    missing = [k for k in wanted if k not in d]
    if extra or missing:
        problems.append(
            f"{where}: a {kind} needs {' and '.join(wanted)}"
            + (f" (remove {', '.join(sorted(extra))})" if extra else "")
        )
        return None
    if "summary" in d and not (isinstance(d["summary"], str) and d["summary"].strip()):
        problems.append(f"{where}: summary needs text")
    sides = {k: _parse_side(d, k, where, sources[commit], problems) for k in wanted}
    links = _parse_links(d.get("related", []), f"{where}.related", problems)
    if any(v is None for v in sides.values()):
        return None
    before, after = sides.get("before"), sides.get("after")
    if kind == "change" and before.path != after.path:
        problems.append(
            f"{where}: a change stays in one file ({before.path} vs {after.path}); "
            "use kind move for code that went elsewhere"
        )
        return None
    return Delta(kind, commit, before, after, str(d.get("summary", "")).strip(), links)


def _validate_shape(plan: object) -> list[str]:
    if not isinstance(plan, dict) or not isinstance(plan.get("changes"), list):
        return ['plan must be an object with a "changes" list']
    if not plan["changes"]:
        return ['"changes" is empty']
    problems = []
    for n, c in enumerate(plan["changes"], 1):
        where = f"changes[{n}]"
        if not isinstance(c, dict):
            problems.append(f"{where}: must be an object")
            continue
        if not isinstance(c.get("title"), str) or not c["title"].strip():
            problems.append(f"{where}: missing title")
        deltas = c.get("deltas")
        if not isinstance(deltas, list) or not deltas:
            problems.append(f"{where}: needs a non-empty deltas list")
        risk, reason = c.get("risk"), c.get("risk_reason", "")
        if risk not in RISKS:
            problems.append(f"{where}: risk must be one of {', '.join(RISKS)}")
        elif risk != "green" and not (isinstance(reason, str) and reason.strip()):
            problems.append(f"{where}: risk_reason is required for {risk}")
        elif not isinstance(reason, str):
            problems.append(f"{where}: risk_reason must be a string")
        _parse_links(c.get("related", []), f"{where}.related", problems)
        answers = c.get("answers", {})
        if not isinstance(answers, dict):
            problems.append(f"{where}: answers must be an object")
            continue
        for k, v in answers.items():
            if k not in QUESTIONS:
                problems.append(
                    f"{where}: unknown question {k!r} (valid: {', '.join(QUESTIONS)})"
                )
            elif not isinstance(v, str):
                problems.append(f"{where}: answer {k!r} must be a string")
    return problems


def apply_plan(store: Store, plan: object) -> list[Change]:
    problems = _validate_shape(plan)
    if problems:
        raise PlanError(problems)

    sources = store.sources()
    parsed: list[list[Delta]] = []
    seen: dict[str, str] = {}
    for n, c in enumerate(plan["changes"], 1):
        row = []
        for m, raw in enumerate(c["deltas"], 1):
            where = f"changes[{n}].deltas[{m}]"
            delta = _parse_delta(raw, where, sources, problems)
            if delta is None:
                continue
            if delta.id in seen:
                problems.append(f"{where}: same as {seen[delta.id]} ({delta.id})")
                continue
            seen[delta.id] = where
            row.append(delta)
        parsed.append(row)
    if problems:
        raise PlanError(problems)

    touched = {d.commit for row in parsed for d in row}
    # Replacing a change that also covers commits outside this plan would
    # orphan its deltas, so make the agent include them.
    stale = [
        c for c in store.changes(sources) if any(d.commit in touched for d in c.deltas)
    ]
    for c in stale:
        outside = {d.commit for d in c.deltas} - touched
        if outside:
            problems.append(
                f"existing change {c.id} also covers {', '.join(sorted(outside))}; "
                "include those commits' deltas in the plan too"
            )
    if problems:
        raise PlanError(problems)

    new = []
    ignored: list[str] = []  # shape already validated above
    for n, c in enumerate(plan["changes"], 1):
        deltas = parsed[n - 1]
        new.append(
            Change(
                id=f"{deltas[0].commit}-{n:02d}",
                title=c["title"].strip(),
                deltas=deltas,
                answers=dict(c.get("answers", {})),
                planned=True,
                position=n,
                risk=c["risk"],
                risk_reason=c.get("risk_reason", "").strip(),
                links=_parse_links(c.get("related", []), "", ignored),
            )
        )
    rev = gitdiff.head_rev()  # the checkout the agent chose these lines in
    for c in new:
        for link in [*c.links, *(x for d in c.deltas for x in d.links)]:
            link.rev = rev
        store.save_change(c)
    keep = {c.id for c in new}
    for c in stale:
        if c.id not in keep:
            store.delete_change(c.id)
    return new
