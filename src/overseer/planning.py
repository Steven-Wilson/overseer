"""Decompose sources into reviewable `Change`s.

An agent reads hunks (`overseer-cli show`), decides how they group into small,
single-purpose changes, and submits a plan. `apply_plan` is deliberately strict:
every hunk of every source the plan touches must be assigned exactly once, so a
plan can reorganise the review but never hide code from it.

Plan format:

    {"changes": [{"title": "...",
                  "hunks": ["abc1234:1", "abc1234:4"],
                  "risk": "yellow",
                  "risk_reason": "required for yellow/red",
                  "answers": {"why": "...", "semantic": "..."}}]}
"""

from __future__ import annotations

from overseer.model import QUESTIONS, RISKS, Change, Source, split_hunk_id
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
            )
        )
    for c in new:
        store.save_change(c)
    keep = {c.id for c in new}
    for c in stale:
        if c.id not in keep:
            store.delete_change(c.id)
    return new
