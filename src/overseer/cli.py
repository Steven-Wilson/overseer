"""Headless commands for agents. Must not import the TUI."""

from __future__ import annotations

import argparse
import json
import sys
import time

from overseer import gitdiff
from overseer.model import QUESTIONS, RISKS, Link
from overseer.planning import PlanError, apply_plan, default_change
from overseer.store import Store, _source_of, resolve_hunks, valid_changeset_name


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _ingest(store: Store, args: argparse.Namespace) -> int:
    try:
        shas = gitdiff.resolve_commits(args.rev)
        for sha in shas:
            src = gitdiff.commit_to_source(sha, context=args.context)
            old = store.load_source(src.id)
            if old and old.hunks == src.hunks:
                print(f"{src.id} already ingested")
                continue
            if old:
                if not args.force:
                    return _err(
                        f"{src.id} already ingested with different hunks "
                        "(different --context?); use --force to replace it and "
                        "discard its changes"
                    )
                doomed = [
                    c
                    for c in store.changes()
                    if any(_source_of(h) == src.id for h in c.hunk_ids)
                ]
                spanning = [
                    c.id
                    for c in doomed
                    if any(_source_of(h) != src.id for h in c.hunk_ids)
                ]
                if spanning:
                    return _err(
                        f"changes {', '.join(spanning)} span other sources; "
                        "re-plan them instead of --force"
                    )
                for c in doomed:
                    store.delete_change(c.id)
                src.seq = old.seq
            else:
                src.seq = store.next_seq()
            store.save_source(src)
            store.save_change(default_change(src))
            lines = sum(len(h.lines) for h in src.hunks)
            print(f"{src.id} {src.title} ({len(src.hunks)} hunks, {lines} lines)")
    except gitdiff.GitError as e:
        return _err(str(e))
    return 0


def _show(store: Store, args: argparse.Namespace) -> int:
    src = store.load_source(args.source)
    if not src:
        return _err(f"unknown source {args.source!r} (ingest it first)")
    if args.json:
        data = src.to_dict()
        for i, h in enumerate(data["hunks"]):
            h["id"] = src.hunk_id(i)
        json.dump(data, sys.stdout, indent=2)
        print()
        return 0
    print(f"source {src.id}\n\n{src.message}\n")
    for i, h in enumerate(src.hunks):
        print(f"== {src.hunk_id(i)}  {h.path}  {h.header}")
        for line in h.lines:
            print(line)
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
        print(f"{c.id} {c.title} ({len(c.hunk_ids)} hunks)")
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
    if args.hunk:
        if args.hunk not in change.hunk_ids:
            return _err(f"{args.hunk} is not a hunk of {change.id}")
        links = change.hunk_links.setdefault(args.hunk, [])
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
    elif note.kind == "hunk":
        owner = next((c for c in changes if note.ref in c.hunk_ids), None)
        if owner:
            for source, idx, hunk in resolve_hunks(owner, sources):
                if source.hunk_id(idx) == note.ref:
                    ctx = {
                        "change": owner.id,
                        "change_title": owner.title,
                        "commit": source.id,
                        "path": hunk.path,
                        "header": hunk.header,
                        "lines": hunk.lines,
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
        print("review requested; run `review wait` to block until the human is done")
    elif args.action == "wait":
        if review["state"] == "idle":
            return _err("no review requested; run `review request` first")
        deadline = time.monotonic() + args.timeout if args.timeout else None
        while store.review()["state"] != "done":
            if deadline is not None and time.monotonic() >= deadline:
                print(
                    "still waiting for the human; run `review wait` again",
                    file=sys.stderr,
                )
                return 3
            time.sleep(1)
        _dump_notes(store, only_open=True)
    return 0


def _changeset(store: Store, args: argparse.Namespace) -> int:
    name = getattr(args, "name", None)
    if args.action == "current":
        print(store.name)
    elif args.action == "list":
        for n in store.changeset_names():
            mark = "*" if n == store.name else " "
            print(f"{mark} {n}  ({store.count_changes(n)} changes)")
    elif args.action == "new":
        if not valid_changeset_name(name):
            return _err(f"invalid changeset name {name!r}")
        if store.has_changeset(name):
            return _err(f"changeset {name!r} already exists")
        store.create_changeset(name)
        if args.use:
            store.set_active(name)
        print(f"created {name}" + (" (now active)" if args.use else ""))
    elif args.action == "use":
        if not store.has_changeset(name):
            return _err(f"unknown changeset {name!r}")
        store.set_active(name)
    elif args.action == "delete":
        if not store.has_changeset(name):
            return _err(f"unknown changeset {name!r}")
        store.delete_changeset(name)
        print(f"deleted {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="overseer-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    # Required, never defaulted to the active changeset: that is whatever the
    # human last switched the TUI to, which may be an unrelated review.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--changeset",
        metavar="NAME",
        required=True,
        help="changeset to operate on (see `changeset list`); ingest creates it",
    )

    ing = sub.add_parser(
        "ingest",
        parents=[common],
        help="import commit(s) from git as sources split into hunks",
    )
    ing.add_argument("rev", nargs="?", default="HEAD", help="commit or A..B range")
    ing.add_argument(
        "--context",
        type=int,
        default=3,
        help="diff context lines (git -U); lower splits hunks more finely",
    )
    ing.add_argument("--force", action="store_true", help="replace changed source")

    show = sub.add_parser(
        "show", parents=[common], help="print a source's hunks with their ids"
    )
    show.add_argument("source")
    show.add_argument("--json", action="store_true")

    plan = sub.add_parser(
        "plan",
        parents=[common],
        help="decompose sources into changes from a JSON plan (file or -)",
    )
    plan.add_argument("file")

    rec = sub.add_parser("record", parents=[common], help="edit an existing change")
    rec.add_argument("change_id")
    rec.add_argument("--title")
    rec.add_argument("--risk", choices=list(RISKS))
    rec.add_argument("--risk-reason")
    rec.add_argument("--question", choices=sorted(QUESTIONS))
    rec.add_argument("--text", help="answer text for --question")

    lnk = sub.add_parser(
        "link",
        parents=[common],
        help="add related code to a change (or one of its hunks)",
    )
    lnk.add_argument("change_id")
    lnk.add_argument("target", help="PATH or PATH:LINE, relative to the repo root")
    lnk.add_argument("--note", default="", help="why it is related, a few words")
    lnk.add_argument("--hunk", help="attach to this hunk id instead of the change")

    ls = sub.add_parser("list", parents=[common], help="list changes as JSON")
    ls.add_argument(
        "--unplanned", action="store_true", help="only changes not yet decomposed"
    )

    nts = sub.add_parser(
        "notes",
        parents=[common],
        help="the human reviewer's notes as JSON, with the code each refers to",
    )
    nts.add_argument("--open", action="store_true", help="only unaddressed notes")

    res = sub.add_parser(
        "resolve", parents=[common], help="mark a reviewer note as addressed"
    )
    res.add_argument("note_id")
    res.add_argument("--reply", help="what you did (or why not), shown to the human")

    rv = sub.add_parser("review", help="hand the changeset to the human and wait")
    rv_sub = rv.add_subparsers(dest="action", required=True)
    rq = rv_sub.add_parser(
        "request", parents=[common], help="tell the TUI you are awaiting review"
    )
    rq.add_argument("--message", help="what you want feedback on, shown in the TUI")
    wt = rv_sub.add_parser(
        "wait",
        parents=[common],
        help="block until the human presses D, then print their open notes "
        "(exit 3 on --timeout)",
    )
    wt.add_argument("--timeout", type=float, default=0, help="seconds; 0 = forever")
    rv_sub.add_parser("status", parents=[common], help="idle, requested or done")

    cs = sub.add_parser("changeset", help="manage changesets (separate review sets)")
    cs_sub = cs.add_subparsers(dest="action", required=True)
    cs_sub.add_parser("list", help="list changesets; * marks the active one")
    cs_sub.add_parser("current", help="print the active changeset's name")
    new = cs_sub.add_parser("new", help="create an empty changeset")
    new.add_argument("name")
    new.add_argument("--use", action="store_true", help="also make it the active one")
    use = cs_sub.add_parser("use", help="make a changeset active (the TUI follows)")
    use.add_argument("name")
    dele = cs_sub.add_parser("delete", help="discard a changeset and all its changes")
    dele.add_argument("name")

    args = parser.parse_args(argv)
    handler = {
        "ingest": _ingest,
        "show": _show,
        "plan": _plan,
        "record": _record,
        "link": _link,
        "list": _list,
        "changeset": _changeset,
        "notes": _notes,
        "resolve": _resolve,
        "review": _review,
    }[args.cmd]
    name = getattr(args, "changeset", None)  # absent for `changeset` commands
    try:
        store = Store(changeset=name)
    except ValueError as e:
        return _err(str(e))
    if name and args.cmd != "ingest" and not store.has_changeset(name):
        return _err(f"unknown changeset {name!r} (ingest into it first)")
    return handler(store, args)


if __name__ == "__main__":
    raise SystemExit(main())
