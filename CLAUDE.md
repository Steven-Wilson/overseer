# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Package manager is `uv`; Python >=3.12.

```
uv sync                         # install deps
uv run overseer                 # launch the TUI
uv run overseer-cli --help      # agent-facing CLI
uv run pytest                   # all tests (pytest-asyncio, asyncio_mode=auto)
uv run pytest tests/test_tui.py::test_app_shows_changes   # single test
uv run ruff check . && uv run ruff format .
```

The store (`.overseer/`, gitignored) lives in the cwd unless `OVERSEER_DIR` is set; point the TUI and CLI at the same one. Run both from the repo root being reviewed, since link paths are relative to it.

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

Two-layer data model (`src/overseer/model.py`, the contract between CLI and TUI):

- **`Source`** — raw ingested material: currently one git commit (message + `Hunk`s). Hunks are addressed `<7-char sha>:<1-based n>`. Immutable once ingested.
- **`Change`** — the human-sized review unit. References hunk ids (possibly spanning sources), carries `answers` to the eight `QUESTIONS`, `hunk_summaries` (one sentence per hunk id: what it does and why it sits there), and `links`/`hunk_links` (related code: `Link(path, line, note)`, per change and per hunk id) and a `risk` (`RISKS`: green/yellow/red/white, where white = tests/docs/comments only; rubric in `model.py` and the skill; `risk_reason` required for everything but green, enforced by `apply_plan` and `record`; `""` = unrated, shown as `○`). Ingest creates one default `Change` per commit (`planned=False`, shown as "whole commit"); decomposing replaces it with several (`planned=True`, ids `<sha>-NN`).

Modules (all under `src/overseer/`; the TUI is a single file, `tui/app.py`):

- `store.py` — `$OVERSEER_DIR` (default `./.overseer/`): `changesets/<name>/{sources,changes}/*.json`, atomic writes. A **changeset** is an isolated review set (local commits vs a teammate's PR); the same sha can live in several. `Store(changeset=None)` follows the `active` pointer file, re-read on every access so the polling TUI notices switches; `Store(changeset="x")` pins. The old flat `sources/`+`changes/` layout auto-migrates into `default`. `Store.changes()` orders by source ingest `seq`, then plan `position` (the agent's story order); the TUI re-sorts that with `model.by_priority` (red, yellow, unrated, green, white; stable, so story order holds within a tier). `list` output stays in story order. `resolve_hunks` skips dangling ids so a stale store can't crash the viewer.
- **Reviewer notes** (`model.Note`, `changesets/<name>/notes/`, one file each): the human's feedback on a hunk (`n`), change (`N`) or changeset (`m`) in the TUI; agents read them via `notes` (JSON enriched with the target's path/hunk) and close them with `resolve --reply`. Notes are keyed by hunk/change id, so they survive re-planning; a vanished target is reported as `stale`, never dropped. `review request` → `review wait` → TUI `D` is the pause-for-review handshake (`review.json`); `wait` exits 3 on `--timeout`. This is the second direction the TUI writes (notes, `D`, plus the active pointer and changeset delete); the CLI still never imports the TUI. Skill: `.claude/skills/overseer-feedback/SKILL.md` — keep it in sync with these commands.
- `freshness.py` — "changed since the last review". `D` (done reviewing) or `R` (mark reviewed, no agent needed) writes `reviewed.json`: content hashes of every hunk (path + diff lines, **not** the `@@` header, id or position) plus a hash of each change's title/risk/answers. A change is then `NEW` (no hunk seen), `CHANGED +N` (some hunks new) or `UPDATED` (code familiar, explanation/risk differs). Matching by content is deliberate: re-planning renumbers changes and unrelated edits shift line numbers, neither should look new. No baseline means nothing is flagged. The list border counts fresh changes in both views so one can't hide behind the white view. Pure logic, no textual import.
- `gitdiff.py` — shells out to `git`, parses `git show` output into a `Source`. `--context N` sets `-U` (finer hunks).
- `planning.py` — `apply_plan` validates a decomposition plan. Strict by design: every hunk of every touched source must be assigned exactly once, so a plan can reorganise review but never hide code; validation failure writes nothing.
- `cli.py` — `overseer-cli` (`ingest`, `show`, `plan`, `record`, `link`, `list`, `notes`, `resolve`, `review request|wait|status`, `changeset list|current|new|use|delete`), the headless interface agents use. Every non-`changeset` command **requires** `--changeset NAME` (argparse-enforced, no fallback to the active pointer, since that is whatever the human last switched the TUI to); only `ingest` creates a changeset. Must never import `textual` or `overseer.tui`.
- `tui/` — Textual app (`overseer`). Polls the store (agents write while it's open) and writes only the reviewer's own input, so it must tolerate the store changing underneath it. Selection is tracked by change id, not list index (and highlight events are ignored while the list is rebuilt, because `clear()` yields a "nothing highlighted" event that would otherwise reset it). Each change is exactly two lines — title (CSS no-wrap, ellipsis), then `N hunks · M files · ✎open · ✓addressed` — keep it two lines; `tests/test_tui.py` asserts the height. Detail text is `rich.text.Text`, never markup — diffs contain `[...]`. Vim-style keys only (Tab is not used for navigation): `c` opens the changeset picker above the change list (Enter switches and writes the `active` pointer; `d` then `y` deletes the highlighted changeset, any other key cancels The delete prompt lives in `ChangesetList.on_key`, not a modal, because a `ModalScreen` would break the polling refresh's `query_one`s. Selection is remembered per changeset). `n`/`N`/`m` open the in-place note `Input` (Enter saves, Esc cancels); a note-only change relabels list items in place instead of rebuilding, so the cursor and hunk survive. `D` marks a requested review done. `h`/`l` focus the changes / related-code pane, `j`/`k` move within the focused list, `t` swaps between the code view and the white (tests/text) view, which is hidden by default but always advertised with a count in the list's border title so it can't hide code silently (selection is remembered per view), `[`/`]` step hunks (a planned change opens on an overview, its answers plus an outline of hunk summaries, before hunk 1; hunk views show only that hunk's summary, never the change-wide answers; `_hunk == -1` is the overview, and unplanned changes skip it), `ctrl+d`/`ctrl+u` scroll the diff, Enter on a related-code link opens it: `Link.rev` is the `HEAD` of the checkout where the CLI wrote the link (`plan`/`link` stamp it); if it equals the reviewer's `HEAD` (re-read every poll) the real file opens read/write, otherwise `git show rev:path` goes to a read-only temp copy (row tagged `@abc1234`) and `O` opens the working-tree file instead; an unfetched commit warns and falls back; links with no `rev` behave as before. Both lists are `VimList`s; the related-code list is rebuilt from the current hunk's `Change.hunk_links` plus `Change.links` and is only rebuilt when its rows change (so polling doesn't reset the cursor). Handlers that touch lists are `async` because `ListView.clear/extend` must be awaited.
- `editor.py` — builds the editor command: `$EDITOR`, else `$VISUAL`; per-editor line-number syntax (`+N`, `file:N`, `--goto`); GUI editors are spawned detached, terminal editors run with the TUI suspended. Paths are relative to the TUI's cwd (the repo root).

Decomposition is done by an LLM, not by code: `.claude/skills/overseer-decompose/SKILL.md` tells an agent how to read sources (`show`), group hunks, and submit a plan. Keep the skill and the CLI's plan format/commands in sync when changing either.

The CLI writes, the TUI reads; keep that one-way coupling through `model`/`store`.
