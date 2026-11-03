"""Headless commands for agents. Must not import the TUI."""

from __future__ import annotations

import argparse
import json
import sys

from overseer import gitdiff
from overseer.model import QUESTIONS, Change
from overseer.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="overseer-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="create or update a change's answer")
    rec.add_argument("change_id")
    rec.add_argument("--title", help="set/replace the change title")
    rec.add_argument("--question", choices=sorted(QUESTIONS))
    rec.add_argument("--text", help="answer text for --question")

    ing = sub.add_parser(
        "ingest", help="import commit(s) from git as changes, split into hunks"
    )
    ing.add_argument("rev", nargs="?", default="HEAD", help="commit or A..B range")

    sub.add_parser("list", help="list recorded changes as JSON")

    args = parser.parse_args(argv)
    store = Store()

    if args.cmd == "list":
        json.dump([c.to_dict() for c in store.list()], sys.stdout, indent=2)
        print()
        return 0

    if args.cmd == "ingest":
        try:
            shas = gitdiff.resolve_commits(args.rev)
            for sha in shas:
                fresh = gitdiff.commit_to_change(sha)
                # re-ingesting must not discard answers recorded earlier
                old = store.load(fresh.id)
                if old:
                    fresh.answers = old.answers
                store.save(fresh)
                print(f"{fresh.id} {fresh.title} ({len(fresh.hunks)} hunks)")
        except gitdiff.GitError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if bool(args.question) != (args.text is not None):
        parser.error("--question and --text must be given together")
    change = store.load(args.change_id) or Change(
        id=args.change_id, title=args.title or args.change_id
    )
    if args.title:
        change.title = args.title
    if args.question:
        change.answers[args.question] = args.text
    store.save(change)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
