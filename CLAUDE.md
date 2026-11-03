# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Package manager is `uv`; Python >=3.12.

```
uv sync                         # install deps
uv run overseer                 # launch the TUI
uv run overseer-cli --help      # agent-facing CLI
uv run pytest                   # all tests
uv run pytest tests/test_tui.py::test_app_shows_changes   # single test
uv run ruff check . && uv run ruff format .
```

## Purpose

Overseer helps humans review agentic coding changes at a higher level than line diffs. For each engineering change it should answer:

1. What changed mechanically?
2. What changed semantically?
3. Why was the change made?
4. What system parts are impacted?
5. How does it affect user-observable behavior?
6. Does it follow existing patterns?
7. Should the code be refactored?
8. How has test coverage been impacted?

These eight questions are the product's requirements; design features against them.

## Architecture

TUI framework is **Textual** (async, handles long-running sessions, has a headless `run_test()` pilot for tests).

- `overseer/model.py` — `Change` dataclass and `QUESTIONS` (the README's eight questions, keyed by short id). This is the data contract between CLI and TUI.
- `overseer/store.py` — file-backed store, one JSON file per change in `$OVERSEER_DIR` (default `./.overseer/changes/`), written atomically.
- `overseer/gitdiff.py` — shells out to `git` to turn a commit into a `Change`: commit message plus `Hunk`s (one per `@@` block; binary/mode-only/empty files become a line-less hunk with a note). `overseer-cli ingest [REV|A..B]` runs it, one change per commit (ids are 7-char shas); re-ingesting preserves recorded answers.
- `overseer/cli.py` — `overseer-cli`, headless commands agents run to record/list changes. Must never import `textual` or `overseer.tui`.
- `overseer/tui/` — Textual app (`overseer`). Reads only from the store and polls it (agents write while it's open), so it must tolerate the store changing underneath it. Selection is tracked by change id (not list index) so refreshes don't jump. Detail text is rendered as `rich.text.Text`, never markup — diffs contain `[...]`. Keys: `n`/`p` step hunks.

The CLI writes, the TUI reads; keep that one-way coupling through `model`/`store`.
