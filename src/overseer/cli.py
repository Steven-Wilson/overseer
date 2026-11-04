"""Headless commands for agents. Must not import the TUI."""

from __future__ import annotations

import argparse
import json
import sys
import time

from overseer import gitdiff
from overseer.deltaview import delta_rows, new_range, plain
from overseer.model import QUESTIONS, RISKS, Link, format_lines, is_changed
from overseer.moves import find_moves
from overseer.planning import PlanError, apply_plan, default_change
from overseer.store import Store, resolve_deltas, valid_reviewset_name


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _ingest(store: Store, args: argparse.Namespace) -> int:
    try:
        shas = gitdiff.resolve_commits(args.rev)
        for sha in shas:
            src = gitdiff.commit_to_source(sha)
            old = store.load_source(src.id)
            if old and old.files == src.files:
                print(f"{src.id} already ingested")
                continue
            if old:
                if not args.force:
                    return _err(
                        f"{src.id} already ingested with different content; "
                        "use --force to replace it and discard its changes"
                    )
                doomed = [
                    c
                    for c in store.changes()
                    if any(d.commit == src.id for d in c.deltas)
                ]
                spanning = [
                    c.id for c in doomed if any(d.commit != src.id for d in c.deltas)
                ]
                if spanning:
                    return _err(
                        f"changes {', '.join(spanning)} span other commits; "
                        "re-plan them instead of --force"
                    )
                for c in doomed:
                    store.delete_change(c.id)
                src.seq = old.seq
            else:
                src.seq = store.next_seq()
            store.save_source(src)
            store.save_change(default_change(src))
            n = sum(f.count for f in src.files)
            print(f"{src.id} {src.title} ({len(src.files)} files, {n} changed lines)")
    except gitdiff.GitError as e:
        return _err(str(e))
    return 0


def _show(store: Store, args: argparse.Namespace) -> int:
    src = store.load_source(args.source)
    if not src:
        return _err(f"unknown commit {args.source!r} (ingest it first)")
    if args.json:
        files = []
        for f in src.files:
            n, lines = 0, []
            for line in f.lines:
                if is_changed(line):
                    n += 1
                lines.append({"n": n if is_changed(line) else None, "text": line})
            files.append({"path": f.path, "changed": f.count, "lines": lines})
        json.dump(
            {
                "id": src.id,
                "message": src.message,
                "files": files,
                "possible_moves": [
                    {
                        "path": m.path,
                        "lines": format_lines(m.lines),
                        "moved_to": {
                            "path": m.to_path,
                            "lines": format_lines(m.to_lines),
                        },
                    }
                    for m in find_moves(src)
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0
    print(f"commit {src.id}\n\n{src.message}\n")
    print(
        "Changed lines (+, -) are numbered per file; a plan's deltas pick them "
        "by number.\n"
    )
    for f in src.files:
        print(f"== {f.path}  (changed lines 1-{f.count})")
        n = 0
        for line in f.lines:
            if is_changed(line):
                n += 1
                print(f"{n:>4} {line}")
            else:
                print(f"     {line}")
        print()
    moves = find_moves(src)
    if moves:
        print(
            "Possible moves (removed lines that reappear, ignoring indentation). "
            "If one is a real move, give its delta `moved_to` in the plan:"
        )
        for m in moves:
            print(
                f"  {m.path} lines {format_lines(m.lines)} -> "
                f"{m.to_path} lines {format_lines(m.to_lines)}  ({m.size} lines)"
            )
        print()
    return 0


def _plan(store: Store, args: argparse.Namespace) -> int:
    try:
        raw = sys.stdin.read() if args.file == "-" else open(args.file).read()
        plan = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        return _err(f"cannot read plan: {e}")
    try:
        changes = apply_plan(store, plan)
    except PlanError as e:
        for p in e.problems:
            print(f"error: {p}", file=sys.stderr)
        print("nothing was written", file=sys.stderr)
        return 1
    for c in changes:
        print(f"{c.id} {c.title} ({len(c.deltas)} deltas)")
    return 0


def _record(store: Store, args: argparse.Namespace) -> int:
    if bool(args.question) != (args.text is not None):
        return _err("--question and --text must be given together")
    change = store.load_change(args.change_id)
    if not change:
        return _err(f"unknown change {args.change_id!r}")
    if args.title:
        change.title = args.title
    if args.risk:
        change.risk = args.risk
    if args.risk_reason:
        change.risk_reason = args.risk_reason
    if change.risk in RISKS and change.risk != "green" and not change.risk_reason:
        return _err(f"{change.risk} needs --risk-reason")
    if args.question:
        change.answers[args.question] = args.text
    if bool(args.delta) != (args.summary is not None):
        return _err("--delta and --summary must be given together")
    if args.delta:
        delta = change.delta(args.delta)
        if not delta:
            return _err(f"{args.delta} is not a delta of {change.id}")
        delta.summary = args.summary.strip()
    store.save_change(change)
    return 0


def _link(store: Store, args: argparse.Namespace) -> int:
    change = store.load_change(args.change_id)
    if not change:
        return _err(f"unknown change {args.change_id!r}")
    path, _, tail = args.target.rpartition(":")
    line = int(tail) if path and tail.isdigit() and int(tail) > 0 else None
    if line is None:
        path = args.target
    if args.delta:
        delta = change.delta(args.delta)
        if not delta:
            return _err(f"{args.delta} is not a delta of {change.id}")
        links = delta.links
    else:
        links = change.links
    rev = gitdiff.head_rev()
    for existing in links:
        if (existing.path, existing.line) == (path, line):
            existing.note = args.note or existing.note
            existing.rev = rev  # re-asserted against the current checkout
            break
    else:
        links.append(Link(path, line, args.note, rev))
    store.save_change(change)
    return 0


def _list(store: Store, args: argparse.Namespace) -> int:
    changes = store.changes()
    if args.unplanned:
        changes = [c for c in changes if not c.planned]
    json.dump([c.to_dict() for c in changes], sys.stdout, indent=2)
    print()
    return 0


def _note_dict(note, changes, sources) -> dict:
    """The note plus what it points at, so an agent need not re-derive context.
    Notes outlive re-planning, so a target may be gone: `stale` says so."""
    out = note.to_dict()
    ctx: dict = {}
    if note.kind == "change":
        change = next((c for c in changes if c.id == note.ref), None)
        ctx = {"change_title": change.title} if change else {"stale": True}
    elif note.kind == "delta":
        owner = next((c for c in changes if note.ref in c.delta_ids), None)
        if owner:
            for r in resolve_deltas(owner, sources):
                if r.delta.id == note.ref:
                    rows = delta_rows(r.file, r.numbers)
                    span = new_range(rows)
                    ctx = {
                        "change": owner.id,
                        "change_title": owner.title,
                        "commit": r.source.id,
                        "path": r.delta.path,
                        "changed_lines": r.delta.lines,
                        "new_lines": list(span) if span else None,
                        "diff": plain(rows),
                    }
                    if r.dest is not None:  # a move: say where it went too
                        to_rows = delta_rows(r.dest, r.to_numbers)
                        to_span = new_range(to_rows)
                        ctx["moved_to"] = {
                            "path": r.delta.to_path,
                            "changed_lines": r.delta.to_lines,
                            "new_lines": list(to_span) if to_span else None,
                            "diff": plain(to_rows),
                        }
        ctx = ctx or {"stale": True}
    out["context"] = ctx
    return out


def _dump_notes(store: Store, only_open: bool) -> None:
    changes, sources = store.changes(), store.sources()
    notes = [n for n in store.notes() if not only_open or n.status == "open"]
    json.dump([_note_dict(n, changes, sources) for n in notes], sys.stdout, indent=2)
    print()


def _notes(store: Store, args: argparse.Namespace) -> int:
    _dump_notes(store, args.open)
    return 0


def _resolve(store: Store, args: argparse.Namespace) -> int:
    note = store.load_note(args.note_id)
    if not note:
        return _err(f"unknown note {args.note_id!r}")
    note.status = "addressed"
    if args.reply:
        note.reply = args.reply
    store.save_note(note)
    return 0


def _review(store: Store, args: argparse.Namespace) -> int:
    review = store.review()
    if args.action == "status":
        json.dump(review, sys.stdout)
        print()
    elif args.action == "request":
        store.set_review("requested", args.message or "")
        print("review requested; run `review wait` to block until the reviewer is done")
    elif args.action == "wait":
        if review["state"] == "idle":
            return _err("no review requested; run `review request` first")
        deadline = time.monotonic() + args.timeout if args.timeout else None
        while store.review()["state"] != "done":
            if deadline is not None and time.monotonic() >= deadline:
                print(
                    "still waiting for the reviewer; run `review wait` again",
                    file=sys.stderr,
                )
                return 3
            time.sleep(1)
        _dump_notes(store, only_open=True)
    return 0


def _reviewset(store: Store, args: argparse.Namespace) -> int:
    name = getattr(args, "name", None)
    if args.action == "current":
        print(store.name)
    elif args.action == "list":
        for n in store.reviewset_names():
            mark = "*" if n == store.name else " "
            print(f"{mark} {n}  ({store.count_changes(n)} changes)")
    elif args.action == "new":
        if not valid_reviewset_name(name):
            return _err(f"invalid reviewset name {name!r}")
        if store.has_reviewset(name):
            return _err(f"reviewset {name!r} already exists")
        store.create_reviewset(name)
        if args.use:
            store.set_active(name)
        print(f"created {name}" + (" (now active)" if args.use else ""))
    elif args.action == "use":
        if not store.has_reviewset(name):
            return _err(f"unknown reviewset {name!r}")
        store.set_active(name)
    elif args.action == "delete":
        if not store.has_reviewset(name):
            return _err(f"unknown reviewset {name!r}")
        store.delete_reviewset(name)
        print(f"deleted {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="overseer-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    # Required, never defaulted to the active reviewset: that is whatever the
    # human last switched the TUI to, which may be an unrelated review.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--reviewset",
        metavar="NAME",
        required=True,
        help="reviewset to operate on (see `reviewset list`); ingest creates it",
    )

    ing = sub.add_parser(
        "ingest",
        parents=[common],
        help="import commit(s) from git, one default change per commit",
    )
    ing.add_argument("rev", nargs="?", default="HEAD", help="commit or A..B range")
    ing.add_argument(
        "--force",
        action="store_true",
        help="replace a commit already ingested with different content",
    )

    show = sub.add_parser(
        "show",
        parents=[common],
        help="print a commit's message and its numbered changed lines",
    )
    show.add_argument("source")
    show.add_argument("--json", action="store_true")

    plan = sub.add_parser(
        "plan",
        parents=[common],
        help="decompose commits into changes from a JSON plan (file or -)",
    )
    plan.add_argument("file")

    rec = sub.add_parser("record", parents=[common], help="edit an existing change")
    rec.add_argument("change_id")
    rec.add_argument("--title")
    rec.add_argument("--risk", choices=list(RISKS))
    rec.add_argument("--risk-reason")
    rec.add_argument("--question", choices=sorted(QUESTIONS))
    rec.add_argument("--text", help="answer text for --question")
    rec.add_argument("--delta", help="delta id; use with --summary")
    rec.add_argument("--summary", help="one-sentence summary of --delta")

    lnk = sub.add_parser(
        "link",
        parents=[common],
        help="add related code to a change (or one of its deltas)",
    )
    lnk.add_argument("change_id")
    lnk.add_argument("target", help="PATH or PATH:LINE, relative to the repo root")
    lnk.add_argument("--note", default="", help="why it is related, a few words")
    lnk.add_argument("--delta", help="attach to this delta id instead of the change")

    ls = sub.add_parser("list", parents=[common], help="list changes as JSON")
    ls.add_argument(
        "--unplanned", action="store_true", help="only changes not yet decomposed"
    )

    nts = sub.add_parser(
        "notes",
        parents=[common],
        help="the reviewer's notes as JSON, with the code each refers to",
    )
    nts.add_argument("--open", action="store_true", help="only unaddressed notes")

    res = sub.add_parser(
        "resolve", parents=[common], help="mark a reviewer note as addressed"
    )
    res.add_argument("note_id")
    res.add_argument("--reply", help="what you did (or why not), shown to the reviewer")

    rv = sub.add_parser("review", help="hand the reviewset to the reviewer and wait")
    rv_sub = rv.add_subparsers(dest="action", required=True)
    rq = rv_sub.add_parser(
        "request", parents=[common], help="tell the TUI you are awaiting review"
    )
    rq.add_argument("--message", help="what you want feedback on, shown in the TUI")
    wt = rv_sub.add_parser(
        "wait",
        parents=[common],
        help="block until the reviewer presses D, then print their open notes "
        "(exit 3 on --timeout)",
    )
    wt.add_argument("--timeout", type=float, default=0, help="seconds; 0 = forever")
    rv_sub.add_parser("status", parents=[common], help="idle, requested or done")

    cs = sub.add_parser("reviewset", help="manage reviewsets (separate review sets)")
    cs_sub = cs.add_subparsers(dest="action", required=True)
    cs_sub.add_parser("list", help="list reviewsets; * marks the active one")
    cs_sub.add_parser("current", help="print the active reviewset's name")
    new = cs_sub.add_parser("new", help="create an empty reviewset")
    new.add_argument("name")
    new.add_argument("--use", action="store_true", help="also make it the active one")
    use = cs_sub.add_parser("use", help="make a reviewset active (the TUI follows)")
    use.add_argument("name")
    dele = cs_sub.add_parser("delete", help="discard a reviewset and all its changes")
    dele.add_argument("name")

    args = parser.parse_args(argv)
    handler = {
        "ingest": _ingest,
        "show": _show,
        "plan": _plan,
        "record": _record,
        "link": _link,
        "list": _list,
        "reviewset": _reviewset,
        "notes": _notes,
        "resolve": _resolve,
        "review": _review,
    }[args.cmd]
    name = getattr(args, "reviewset", None)  # absent for `reviewset` commands
    try:
        store = Store(reviewset=name)
    except ValueError as e:
        return _err(str(e))
    if name and args.cmd != "ingest" and not store.has_reviewset(name):
        return _err(f"unknown reviewset {name!r} (ingest into it first)")
    return handler(store, args)


if __name__ == "__main__":
    raise SystemExit(main())
