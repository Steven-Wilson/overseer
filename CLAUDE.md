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

**Vocabulary** lives in the README's Vocabulary table and is the one source of truth for terms (reviewer, agent, commit, changed line, delta, change, whole commit, decompose, plan, answer, related code, risk, reviewset, note, baseline, ...). Use it in the TUI, CLI messages, docs and both skills, and update the table first when adding a concept. `Source` and `Link` are internal names; user-facing text says "commit" and "related code".

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

- **`Source`** — raw ingested material: currently one git commit (message + one `FileDiff` per file). A `FileDiff`'s `lines` are the diff lines in order (git's `@@` headers kept inline for line numbers and function names, context lines, changed lines; a non-text change is the single line `!(note)`). Its **changed lines** (`+`/`-`/`!`) are numbered 1.. per file, independent of git's `-U` context, and are the atoms everything points at. Immutable once ingested.
- **`Delta`** — the atomic unit of review: `(commit, path, lines)` where `lines` is a canonical spec of changed-line numbers (`"3-5,9"`), plus a `summary` (one sentence: what it does and why it sits there) and `links` (related code for this delta). Its id is `<sha>:<path>#<lines>`. **A delta is not a git hunk**: the agent cuts it, so it can be a slice of one hunk or span several. A delta can also be a **move**: `to_path`/`to_lines` name where removed code was added (same commit), the id becomes `<sha>:<path>#<lines>><to_path>#<to_lines>`, and both ends count as assigned. Never reintroduce "hunk" for it (the word survives only for git's own hunks, inside `gitdiff`/`deltaview`).
- **`Change`** — the unit of meaning: one stateable intention made of `deltas` (possibly spanning commits), `answers` to the eight `QUESTIONS`, `links` (related code for the whole change) and a `risk` (`RISKS`: green/yellow/red/white, where white = tests/docs/comments only; rubric in `model.py` and the skill; `risk_reason` required for everything but green, enforced by `apply_plan` and `record`; `""` = unrated, shown as `○`). Ingest creates one default `Change` per commit with one delta per file (`planned=False`, shown as "whole commit"); decomposing replaces it with several (`planned=True`, ids `<sha>-NN`).

Modules (all under `src/overseer/`; the TUI is `tui/app.py` plus `tui/render.py`):

- `store.py` — `$OVERSEER_DIR` (default `./.overseer/`): `reviewsets/<name>/{sources,changes}/*.json`, atomic writes. A **reviewset** is everything being reviewed together (local commits vs a teammate's PR); the same sha can live in several. `Store(reviewset=None)` follows the `active` pointer file, re-read on every access so the polling TUI notices switches; `Store(reviewset="x")` pins. There are no store migrations: the format is young and has no users to carry forward. `Store.changes()` orders by source ingest `seq`, then plan `position` (the agent's story order); the TUI re-sorts that with `model.by_priority` (red, yellow, unrated, green, white; stable, so story order holds within a tier). `list` output stays in story order. `resolve_deltas` skips deltas that no longer resolve (commit re-ingested, lines gone) so a stale store can't crash the viewer.
- **Reviewer notes** (`model.Note`, `reviewsets/<name>/notes/`, one file each): the reviewer's feedback on a delta (`n`), change (`N`) or reviewset (`m`) in the TUI; agents read them via `notes` (JSON enriched with the target's path, changed lines, new-side line range and diff) and close them with `resolve --reply`. Notes are keyed by delta/change id, so they survive re-planning when the agent cuts the same deltas; a vanished target is reported as `stale`, never dropped. `review request` → `review wait` → TUI `D` is the pause-for-review handshake (`review.json`); `wait` exits 3 on `--timeout`. This is the second direction the TUI writes (notes, `D`, plus the active pointer and reviewset delete); the CLI still never imports the TUI. Skill: `.claude/skills/overseer-feedback/SKILL.md` — keep it in sync with these commands.
- `freshness.py` — "changed since the last review". `D` (done reviewing) or `R` (mark reviewed, no agent needed) writes `reviewed.json`: content hashes of every changed line a change shows (path + line text + which occurrence of that text it is in its file; **not** the `@@` header, delta id or position, so re-cutting deltas never makes seen code look new) plus a hash of each change's title/risk/answers. A change is then `NEW` (no changed line seen), `CHANGED +N` (N deltas have an unseen line) or `UPDATED` (code familiar, explanation/risk differs). Matching by content is deliberate: re-planning renumbers changes and recuts deltas, and unrelated edits shift line numbers, neither should look new. No baseline means nothing is flagged. The list border counts fresh changes in both views so one can't hide behind the white view. Pure logic, no textual import.
- `gitdiff.py` — shells out to `git`, parses `git show` output into a `Source` of `FileDiff`s. Git's hunk boundaries survive only as `@@` header lines; git is the starting material, not the unit of review, so anything that needs finer or different slicing belongs in deltas, not in git flags (there is no `--context`).
- `deltaview.py` — cuts one delta out of its `FileDiff` for display and decides how to show it. `delta_rows`: the delta's changed lines, 3 lines of context, an old/new line-number gutter from the `@@` headers, the enclosing function name from the header text, any changed lines of *other* deltas in the window kept but dimmed (`kind="other"`) so context is never a lie, and per-row `emph` ranges: for a removed line followed by a mostly-similar added line (similarity on non-space tokens ≥ 0.5) only the differing tokens are marked. `kind_of`: `added` (only `+`), `removed` (only `-`) or `edit` (mixed), derived, never labelled. `move_view`: both ends of a move with each line marked as matching the other side (ignoring indentation) or not, plus the uniform re-indent if any. Pure, no textual import; the CLI's `notes` uses `plain()`/`new_range()`.
- `moves.py` — `find_moves`: runs of removed lines (≥3 lines, ≥40 chars) that reappear as added lines, ignoring indentation, within or across files. It is only a hint (`show` prints it); the agent declares real moves with `moved_to` and `apply_plan` checks only that the ends are all `-` and all `+`.
- `planning.py` — `apply_plan` validates a decomposition plan. Strict by design: every changed line of every file of every touched commit must be in exactly one delta (an omitted `lines` means the whole file), so a plan can reorganise review but never hide code; errors name the exact unassigned or doubly-claimed line numbers, and validation failure writes nothing.
- `cli.py` — `overseer-cli` (`ingest`, `show`, `plan`, `record`, `link`, `list`, `notes`, `resolve`, `review request|wait|status`, `reviewset list|current|new|use|delete`), the headless interface agents use. Every non-`reviewset` command **requires** `--reviewset NAME` (argparse-enforced, no fallback to the active pointer, since that is whatever the reviewer last switched the TUI to); only `ingest` creates a reviewset. `show` prints each file's diff with its changed lines numbered, which is what a plan's `lines` refer to. Must never import `textual` or `overseer.tui`.
- `tui/render.py` — the `Text` for one delta, by kind (rich only, no textual): edit = gutter diff with the changed words emphasised; added/removed section = syntax-highlighted (rich `Syntax`, `ansi_dark`) beside a green/red `▌` bar, signs dropped; move = *removed from here* / *added here* with non-matching lines tinted. Tests assert styles on `render_change`'s `Text`, not on Textual's `render()`, which re-encodes them.
- `tui/app.py` — Textual app (`overseer`). Polls the store (agents write while it's open) and writes only the reviewer's own input, so it must tolerate the store changing underneath it. Selection is tracked by change id, not list index (and highlight events are ignored while the list is rebuilt, because `clear()` yields a "nothing highlighted" event that would otherwise reset it). Each change is exactly two lines — title (CSS no-wrap, ellipsis), then `N deltas · M files · ✎open · ✓addressed` — keep it two lines; `tests/test_tui.py` asserts the height. Detail text is `rich.text.Text`, never markup — diffs contain `[...]`. Vim-style keys only (Tab is not used for navigation): `c` opens the reviewset picker above the change list (Enter switches and writes the `active` pointer; `d` then `y` deletes the highlighted reviewset, any other key cancels. The delete prompt lives in `ReviewsetList.on_key`, not a modal, because a `ModalScreen` would break the polling refresh's `query_one`s. Selection is remembered per reviewset). `n`/`N`/`m` open the in-place note `Input` (Enter saves, Esc cancels); a note-only change relabels list items in place instead of rebuilding, so the cursor and delta survive. `D` marks a requested review done. `h`/`l` focus the changes / related-code pane, `j`/`k` move within the focused list, `t` swaps between the code view and the white (tests/text) view, which is hidden by default but always advertised with a count in the list's border title so it can't hide code silently (selection is remembered per view), `[`/`]` step deltas (a planned change opens on an overview, its answers plus an outline of delta summaries, before delta 1; delta views show only that delta's summary, never the change-wide answers; `_delta == -1` is the overview, and unplanned changes skip it), `ctrl+d`/`ctrl+u` scroll the diff, Enter on a related-code link opens it: `Link.rev` is the `HEAD` of the checkout where the CLI wrote the link (`plan`/`link` stamp it); if it equals the reviewer's `HEAD` (re-read every poll) the real file opens read/write, otherwise `git show rev:path` goes to a read-only temp copy (row tagged `@abc1234`) and `O` opens the working-tree file instead; an unfetched commit warns and falls back; links with no `rev` behave as before. Both lists are `VimList`s; the related-code list is rebuilt from the current delta's `Delta.links` plus `Change.links` and is only rebuilt when its rows change (so polling doesn't reset the cursor). Handlers that touch lists are `async` because `ListView.clear/extend` must be awaited.
- `editor.py` — builds the editor command: `$EDITOR`, else `$VISUAL`; per-editor line-number syntax (`+N`, `file:N`, `--goto`); GUI editors are spawned detached, terminal editors run with the TUI suspended. Paths are relative to the TUI's cwd (the repo root).

Decomposition is done by an LLM, not by code: `.claude/skills/overseer-decompose/SKILL.md` tells an agent how to read sources (`show`), group changed lines into changes and cut each into deltas, and submit a plan. Keep the skill and the CLI's plan format/commands in sync when changing either.

The CLI writes, the TUI reads; keep that one-way coupling through `model`/`store`.
