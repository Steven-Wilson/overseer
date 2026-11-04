"""Decompose sources into reviewable `Change`s.

An agent reads hunks (`overseer-cli show`), decides how they group into small,
single-purpose changes, and submits a plan. `apply_plan` is deliberately strict:
every hunk of every source the plan touches must be assigned exactly once, so a
plan can reorganise the review but never hide code from it.

Plan format:

    {"changes": [{"title": "...",
                  "hunks": ["abc1234:1", "abc1234:4"],
                  "risk": "yellow",
                  "risk_reason": "required unless green",
                  "answers": {"why": "...", "semantic": "..."},
                  "related": [{"path": "src/x.py", "line": 12, "note": "caller"}],
                  "hunk_related": {"abc1234:1": [{"path": "tests/t.py", "line": 3}]}}]}

`related` and `hunk_related` are optional; `hunk_related` keys must be hunks of
the same change.
"""

from __future__ import annotations

from overseer import gitdiff
from overseer.model import QUESTIONS, RISKS, Change, Link, Source, split_hunk_id
from overseer.store import Store, _source_of


class PlanError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def default_change(source: Source) -> Change:
    """1:1 wrapper so freshly ingested commits are reviewable before any
    decomposition happens."""
    return Change(
        id=source.id,
        title=source.title,
        hunk_ids=[source.hunk_id(i) for i in range(len(source.hunks))],
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
        hunks = c.get("hunks")
        if not isinstance(hunks, list) or not hunks:
            problems.append(f"{where}: needs a non-empty hunks list")
        elif not all(isinstance(h, str) for h in hunks):
            problems.append(f"{where}: hunks must be strings like 'abc1234:1'")
        risk, reason = c.get("risk"), c.get("risk_reason", "")
        if risk not in RISKS:
            problems.append(f"{where}: risk must be one of {', '.join(RISKS)}")
        elif risk != "green" and not (isinstance(reason, str) and reason.strip()):
            problems.append(f"{where}: risk_reason is required for {risk}")
        elif not isinstance(reason, str):
            problems.append(f"{where}: risk_reason must be a string")
        _parse_links(c.get("related", []), f"{where}.related", problems)
        hunk_related = c.get("hunk_related", {})
        if not isinstance(hunk_related, dict):
            problems.append(f"{where}.hunk_related: must be an object keyed by hunk id")
        else:
            for hid, links in hunk_related.items():
                if isinstance(hunks, list) and hid not in hunks:
                    problems.append(
                        f"{where}.hunk_related: {hid} is not one of this change's hunks"
                    )
                _parse_links(links, f"{where}.hunk_related[{hid}]", problems)
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
    assigned: dict[str, int] = {}  # hunk id -> plan index of first claim
    for n, c in enumerate(plan["changes"], 1):
        for hid in c["hunks"]:
            try:
                sid, idx = split_hunk_id(hid)
            except ValueError as e:
                problems.append(f"changes[{n}]: {e}")
                continue
            if sid not in sources:
                problems.append(f"changes[{n}]: unknown source {sid!r} (ingest first)")
            elif idx >= len(sources[sid].hunks):
                problems.append(
                    f"changes[{n}]: {hid} does not exist "
                    f"({sid} has {len(sources[sid].hunks)} hunks)"
                )
            elif hid in assigned:
                problems.append(
                    f"changes[{n}]: {hid} already assigned to changes[{assigned[hid]}]"
                )
            else:
                assigned[hid] = n
    if problems:
        raise PlanError(problems)

    touched = {_source_of(h) for h in assigned}
    for sid in sorted(touched):
        missing = [
            sources[sid].hunk_id(i)
            for i in range(len(sources[sid].hunks))
            if sources[sid].hunk_id(i) not in assigned
        ]
        if missing:
            problems.append(f"unassigned hunks in {sid}: {', '.join(missing)}")

    # Replacing a change that also covers sources outside this plan would
    # orphan those hunks, so make the agent include them.
    stale = [
        c
        for c in store.changes(sources)
        if any(_source_of(h) in touched for h in c.hunk_ids)
    ]
    for c in stale:
        outside = {_source_of(h) for h in c.hunk_ids} - touched
        if outside:
            problems.append(
                f"existing change {c.id} also covers {', '.join(sorted(outside))}; "
                "include those sources' hunks in the plan too"
            )
    if problems:
        raise PlanError(problems)

    new = []
    for n, c in enumerate(plan["changes"], 1):
        ignored: list[str] = []  # shape already validated above
        new.append(
            Change(
                id=f"{_source_of(c['hunks'][0])}-{n:02d}",
                title=c["title"].strip(),
                hunk_ids=list(c["hunks"]),
                answers=dict(c.get("answers", {})),
                planned=True,
                position=n,
                risk=c["risk"],
                risk_reason=c.get("risk_reason", "").strip(),
                links=_parse_links(c.get("related", []), "", ignored),
                hunk_links={
                    h: _parse_links(xs, "", ignored)
                    for h, xs in c.get("hunk_related", {}).items()
                },
            )
        )
    rev = gitdiff.head_rev()  # the checkout the agent chose these lines in
    for c in new:
        for link in [*c.links, *(x for xs in c.hunk_links.values() for x in xs)]:
            link.rev = rev
        store.save_change(c)
    keep = {c.id for c in new}
    for c in stale:
        if c.id not in keep:
            store.delete_change(c.id)
    return new
