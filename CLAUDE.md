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

Two-layer data model (`overseer/model.py`, the contract between CLI and TUI):

- **`Source`** — raw ingested material: currently one git commit (message + `Hunk`s). Hunks are addressed `<7-char sha>:<1-based n>`. Immutable once ingested.
- **`Change`** — the human-sized review unit. References hunk ids (possibly spanning sources), carries `answers` to the eight `QUESTIONS` and `links`/`hunk_links` (related code: `Link(path, line, note)`, per change and per hunk id) and a `risk` (`RISKS`: green/yellow/red; rubric in `model.py` and the skill; `risk_reason` required for yellow/red, enforced by `apply_plan` and `record`; `""` = unrated, shown as `○`). Ingest creates one default `Change` per commit (`planned=False`, shown as "whole commit"); decomposing replaces it with several (`planned=True`, ids `<sha>-NN`).

Modules:

- `store.py` — `$OVERSEER_DIR` (default `./.overseer/`): `sources/*.json`, `changes/*.json`, atomic writes. `Store.changes()` orders by source ingest `seq`, then plan `position`. `resolve_hunks` skips dangling ids so a stale store can't crash the viewer.
- `gitdiff.py` — shells out to `git`, parses `git show` output into a `Source`. `--context N` sets `-U` (finer hunks).
- `planning.py` — `apply_plan` validates a decomposition plan. Strict by design: every hunk of every touched source must be assigned exactly once, so a plan can reorganise review but never hide code; validation failure writes nothing.
- `cli.py` — `overseer-cli` (`ingest`, `show`, `plan`, `record`, `link`, `list`), the headless interface agents use. Must never import `textual` or `overseer.tui`.
- `tui/` — Textual app (`overseer`). Reads only from the store and polls it (agents write while it's open), so it must tolerate the store changing underneath it. Selection is tracked by change id, not list index. Detail text is `rich.text.Text`, never markup — diffs contain `[...]`. Vim-style keys only (Tab is not used for navigation): `h`/`l` focus the changes / related-code pane, `j`/`k` move within the focused list, `[`/`]` step hunks, `ctrl+d`/`ctrl+u` scroll the diff, Enter on a related-code link opens it. Both lists are `VimList`s; the related-code list is rebuilt from the current hunk's `Change.hunk_links` plus `Change.links` and is only rebuilt when its rows change (so polling doesn't reset the cursor). Handlers that touch lists are `async` because `ListView.clear/extend` must be awaited.
- `editor.py` — builds the editor command: `$EDITOR`, else `$VISUAL`; per-editor line-number syntax (`+N`, `file:N`, `--goto`); GUI editors are spawned detached, terminal editors run with the TUI suspended. Paths are relative to the TUI's cwd (the repo root).

Decomposition is done by an LLM, not by code: `.claude/skills/overseer-decompose/SKILL.md` tells an agent how to read sources (`show`), group hunks, and submit a plan. Keep the skill and the CLI's plan format/commands in sync when changing either.

The CLI writes, the TUI reads; keep that one-way coupling through `model`/`store`.
