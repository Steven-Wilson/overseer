"""Headless commands for agents. Must not import the TUI."""

from __future__ import annotations

import argparse
import json
import sys

from overseer import gitdiff
from overseer.model import QUESTIONS, RISKS, Link
from overseer.planning import PlanError, apply_plan, default_change
from overseer.store import Store, _source_of


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
    if change.risk in ("yellow", "red") and not change.risk_reason:
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
    for existing in links:
        if (existing.path, existing.line) == (path, line):
            existing.note = args.note or existing.note
            break
    else:
        links.append(Link(path, line, args.note))
    store.save_change(change)
    return 0


def _list(store: Store, args: argparse.Namespace) -> int:
    changes = store.changes()
    if args.unplanned:
        changes = [c for c in changes if not c.planned]
    json.dump([c.to_dict() for c in changes], sys.stdout, indent=2)
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="overseer-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser(
        "ingest", help="import commit(s) from git as sources split into hunks"
    )
    ing.add_argument("rev", nargs="?", default="HEAD", help="commit or A..B range")
    ing.add_argument(
        "--context",
        type=int,
        default=3,
        help="diff context lines (git -U); lower splits hunks more finely",
    )
    ing.add_argument("--force", action="store_true", help="replace changed source")

    show = sub.add_parser("show", help="print a source's hunks with their ids")
    show.add_argument("source")
    show.add_argument("--json", action="store_true")

    plan = sub.add_parser(
        "plan", help="decompose sources into changes from a JSON plan (file or -)"
    )
    plan.add_argument("file")

    rec = sub.add_parser("record", help="edit an existing change")
    rec.add_argument("change_id")
    rec.add_argument("--title")
    rec.add_argument("--risk", choices=list(RISKS))
    rec.add_argument("--risk-reason")
    rec.add_argument("--question", choices=sorted(QUESTIONS))
    rec.add_argument("--text", help="answer text for --question")

    lnk = sub.add_parser(
        "link", help="add related code to a change (or one of its hunks)"
    )
    lnk.add_argument("change_id")
    lnk.add_argument("target", help="PATH or PATH:LINE, relative to the repo root")
    lnk.add_argument("--note", default="", help="why it is related, a few words")
    lnk.add_argument("--hunk", help="attach to this hunk id instead of the change")

    ls = sub.add_parser("list", help="list changes as JSON")
    ls.add_argument(
        "--unplanned", action="store_true", help="only changes not yet decomposed"
    )

    args = parser.parse_args(argv)
    handler = {
        "ingest": _ingest,
        "show": _show,
        "plan": _plan,
        "record": _record,
        "link": _link,
        "list": _list,
    }[args.cmd]
    return handler(Store(), args)


if __name__ == "__main__":
    raise SystemExit(main())
