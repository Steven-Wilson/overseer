"""Decompose commits into reviewable `Change`s.

An agent reads a commit's changed lines (`overseer-cli show`), decides how they
group into small, single-purpose changes, and cuts each change into deltas. A
delta is some changed lines of one file, addressed by number, so it is not tied
to git's hunk boundaries. `apply_plan` is deliberately strict: every changed line
of every file of every commit the plan touches must be in exactly one delta, so a
plan can reorganise the review but never hide code from it.

Plan format:

    {"changes": [{"title": "...",
                  "risk": "yellow",
                  "risk_reason": "required unless green",
                  "answers": {"why": "...", "semantic": "..."},
                  "related": [{"path": "src/x.py", "line": 12, "note": "caller"}],
                  "deltas": [{"commit": "abc1234",
                              "path": "src/x.py",
                              "lines": "3-5,9",
                              "summary": "one sentence",
                              "related": [{"path": "tests/t.py", "line": 3}]}]}]}

A delta that *moves* code names where it went; `path`/`lines` are then the removed
lines and `moved_to` the added ones (same commit):

    {"commit": "abc1234", "path": "src/a.py", "lines": "4-12",
     "moved_to": {"path": "src/b.py", "lines": "1-9"}}

`lines` are changed-line numbers as printed by `show`; omit it (or say "all") for
every changed line of the file. `summary` and the deltas' `related` are optional.
"""

from __future__ import annotations

from overseer import gitdiff
from overseer.model import (
    QUESTIONS,
    RISKS,
    Change,
    Delta,
    Link,
    Source,
    format_lines,
    is_changed,
    parse_lines,
)
from overseer.store import Store


class PlanError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def default_change(source: Source) -> Change:
    """1:1 wrapper so freshly ingested commits are reviewable before any
    decomposition happens: one delta per file."""
    return Change(
        id=source.id,
        title=source.title,
        deltas=[
            Delta(source.id, f.path, format_lines(range(1, f.count + 1)))
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


def _validate_delta(d: object, where: str, problems: list[str]) -> None:
    if not isinstance(d, dict) or set(d) - {
        "commit",
        "path",
        "lines",
        "summary",
        "related",
        "moved_to",
    }:
        problems.append(
            f"{where}: must be an object with commit, path, optional lines, "
            "summary, related, moved_to"
        )
        return
    for key in ("commit", "path"):
        if not isinstance(d.get(key), str) or not d[key].strip():
            problems.append(f"{where}: missing {key}")
    lines = d.get("lines", "all")
    if isinstance(lines, bool) or not isinstance(lines, (str, int)):
        problems.append(f"{where}: lines must be a string like '3-5,9' (or 'all')")
    if "summary" in d and not (isinstance(d["summary"], str) and d["summary"].strip()):
        problems.append(f"{where}: summary needs text")
    _parse_links(d.get("related", []), f"{where}.related", problems)
    if "moved_to" in d:
        to = d["moved_to"]
        if not (
            isinstance(to, dict)
            and set(to) <= {"path", "lines"}
            and isinstance(to.get("path"), str)
            and to["path"].strip()
            and isinstance(to.get("lines", "all"), (str, int))
            and not isinstance(to.get("lines", "all"), bool)
        ):
            problems.append(f"{where}.moved_to: must be {{path, lines?}}")


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
            deltas = []
        for m, d in enumerate(deltas, 1):
            _validate_delta(d, f"{where}.deltas[{m}]", problems)
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


def _numbers(file, spec: object) -> list[int]:
    spec = str(spec).strip()
    if spec == "all":
        return list(range(1, file.count + 1))
    return parse_lines(spec, file.count)


def _signs(file, nums: list[int]) -> set[str]:
    changed = [line[0] for line in file.lines if is_changed(line)]
    return {changed[n - 1] for n in nums}


def _claim_lines(
    sources: dict[str, Source], plan: dict, problems: list[str]
) -> tuple[dict[tuple[int, int], tuple[list[int], list[int]]], dict]:
    """Resolve every delta to changed-line numbers and check that no line is in
    two deltas. Returns (change, delta) -> (numbers, destination numbers), both
    1-based (the second is empty unless the delta is a move), and which lines of
    each (commit, path) are claimed."""
    claimed: dict[tuple[str, str], dict[int, str]] = {}  # (commit, path) -> n -> who
    resolved: dict[tuple[int, int], tuple[list[int], list[int]]] = {}

    def claim(commit: str, path: str, nums: list[int], where: str) -> bool:
        owners = claimed.setdefault((commit, path), {})
        clash = [x for x in nums if x in owners]
        if clash:
            problems.append(
                f"{where}: {commit} {path} lines {format_lines(clash)} already "
                f"in {owners[clash[0]]}"
            )
            return False
        owners.update({x: where for x in nums})
        return True

    for n, c in enumerate(plan["changes"], 1):
        for m, d in enumerate(c["deltas"], 1):
            where = f"changes[{n}].deltas[{m}]"
            commit, path = d["commit"].strip(), d["path"].strip()
            src = sources.get(commit)
            file = src.file(path) if src else None
            if src is None:
                problems.append(f"{where}: unknown commit {commit!r} (ingest first)")
                continue
            if file is None:
                have = ", ".join(f.path for f in src.files)
                problems.append(
                    f"{where}: {commit} has no changed file {path!r} ({have})"
                )
                continue
            try:
                nums = _numbers(file, d.get("lines", "all"))
            except ValueError as e:
                problems.append(f"{where} ({commit} {path}): {e}")
                continue
            to_nums: list[int] = []
            if "moved_to" in d:
                to = d["moved_to"]
                to_path = to["path"].strip()
                dest = src.file(to_path)
                if dest is None:
                    problems.append(
                        f"{where}.moved_to: {commit} has no changed file {to_path!r}"
                    )
                    continue
                try:
                    to_nums = _numbers(dest, to.get("lines", "all"))
                except ValueError as e:
                    problems.append(f"{where}.moved_to ({commit} {to_path}): {e}")
                    continue
                if _signs(file, nums) != {"-"}:
                    problems.append(
                        f"{where} ({commit} {path}): a move's lines must all be "
                        "removed (-) lines; put what else changed in another delta"
                    )
                    continue
                if _signs(dest, to_nums) != {"+"}:
                    problems.append(
                        f"{where}.moved_to ({commit} {to_path}): must all be "
                        "added (+) lines"
                    )
                    continue
            ok = claim(commit, path, nums, where)
            if to_nums:
                ok = claim(commit, to_path, to_nums, f"{where}.moved_to") and ok
            if ok:
                resolved[(n, m)] = (nums, to_nums)
    return resolved, claimed


def apply_plan(store: Store, plan: object) -> list[Change]:
    problems = _validate_shape(plan)
    if problems:
        raise PlanError(problems)

    sources = store.sources()
    resolved, claimed = _claim_lines(sources, plan, problems)
    if problems:
        raise PlanError(problems)

    touched = {d["commit"].strip() for c in plan["changes"] for d in c["deltas"]}
    for commit in sorted(touched):
        for file in sources[commit].files:
            have = claimed.get((commit, file.path), {})
            missing = set(range(1, file.count + 1)) - set(have)
            if missing:
                problems.append(
                    f"unassigned changed lines in {commit} {file.path}: "
                    f"{format_lines(missing)}"
                )

    # Replacing a change that also covers commits outside this plan would
    # orphan those lines, so make the agent include them.
    stale = [
        c for c in store.changes(sources) if any(d.commit in touched for d in c.deltas)
    ]
    for c in stale:
        outside = {d.commit for d in c.deltas} - touched
        if outside:
            problems.append(
                f"existing change {c.id} also covers {', '.join(sorted(outside))}; "
                "include those commits' changed lines in the plan too"
            )
    if problems:
        raise PlanError(problems)

    new = []
    ignored: list[str] = []  # shape already validated above
    for n, c in enumerate(plan["changes"], 1):
        deltas = [
            Delta(
                d["commit"].strip(),
                d["path"].strip(),
                format_lines(resolved[(n, m)][0]),
                d.get("summary", "").strip(),
                _parse_links(d.get("related", []), "", ignored),
                d["moved_to"]["path"].strip() if "moved_to" in d else "",
                format_lines(resolved[(n, m)][1]),
            )
            for m, d in enumerate(c["deltas"], 1)
        ]
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
