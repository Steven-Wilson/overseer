---
name: overseer-decompose
description: Break a git commit (or range) into small, single-purpose, human-reviewable changes with explanations, using overseer-cli. Use when asked to prepare commits for Overseer review, decompose or split a change, or when `overseer-cli list --unplanned` shows whole-commit changes.
---

# Decompose commits into reviewable changes

Goal: a human reviews each *change* in a few minutes. Turn each commit into
changes that each do one thing, ordered so they read as a story, with the
answers the reviewer needs. Commands below run from the repo being reviewed;
in the overseer repo itself use `uv run overseer-cli`.

## Changesets

Review material is grouped into **changesets** (e.g. `local`, `pr-123`) so the human can swap between unrelated reviews in the TUI (`c`). Pick a name for what you are preparing (`pr-<number>` for a teammate's PR, otherwise ask or use `local`) and pass `--changeset <name>` on **every** command below (the CLI rejects commands without it, because the active changeset is whichever one the human last switched to, which may be a different review). Ingest creates the changeset without switching the human's view; they switch when ready. `overseer-cli changeset list|new|use|delete` manages them (`delete` discards everything in one).

For a PR, fetch it so the range resolves (e.g. `gh pr checkout` or `git fetch origin pull/123/head:pr-123`), then ingest `<base>..pr-123` into its own changeset.

## Procedure

1. **Ingest**: `overseer-cli ingest <rev> --changeset <name>` (`HEAD`, a sha, or `A..B`). Re-running is safe. Output shows hunk and line counts per commit.
2. **Read**: `overseer-cli show <sha>` prints the commit message and every hunk with its id (`<sha>:<n>`). Read all of it, and read surrounding code in the repo when a hunk's purpose isn't clear from the diff.
3. **Too coarse?** If one hunk mixes concerns, re-ingest finer: `overseer-cli ingest <sha> --context 0 --force`. This discards any changes already planned for that commit, so do it *before* planning. Hunk ids change; re-run `show`.
4. **Group** hunks into changes:
   - one purpose per change (a refactor, a behavior change, tests, a rename, config), not one per file;
   - keep mechanical noise (renames, formatting, moved code) separate from logic changes, so the reviewer can skim it;
   - tests go with the behavior they cover unless large;
   - order changes so each makes sense after the previous one; order hunks within a change the same way
     (the TUI lists red first, then yellow, unrated, green, keeping your order within each tier; `white` changes are hidden behind a key, so write each change to stand reasonably alone rather than relying on the previous one);
   - a small commit may stay a single change. Do not split for its own sake.
5. **Explain** each change via `answers`. Valid keys: `mechanical`, `semantic`, `why`, `impact`, `behavior`, `patterns`, `refactor`, `coverage` (the eight review questions). Include only answers you can support from the code and commit message; do not guess intent. For `why`, say so when the commit message gives no reason. Use `patterns`/`refactor` to flag duplication of existing code you actually found. Keep each answer to one or two sentences.
   **Scale commentary to risk** (so rate risk, step 7, first): the reviewer's attention is the scarce resource, and an unanswered question is fine. Do not answer all eight by default; omit any question whose answer is obvious from the title, the diff, or another answer.
   - `white` and `green`: almost nothing, often a single short `mechanical` or `why`, or no answers at all.
   - `yellow`: only the answers that explain *why it is not green* or that the reviewer needs to judge it (typically `semantic`, `coverage`, `impact`; two to four answers).
   - `red`: the fullest, still short; cover what makes it red and what to check.
   **Summarise each hunk** (`hunk_summaries`, keyed by hunk id): the TUI opens a change on its description plus an outline of its hunks, then `]` steps through them, showing only that hunk's summary above the diff (the change-wide answers are *not* repeated). So write each summary as one sentence saying what this hunk does and, when the order isn't obvious, why it comes here ("defines the field the next hunks fill in", "caller updated for the new signature"). Give every hunk of a multi-hunk change one; do not restate the title. Order: definitions, then implementation, then callers, then tests, unless the story reads better otherwise. Add or fix one later with `overseer-cli record <change-id> --hunk <hunk-id> --summary "..."`.
6. **Link related code** so the reviewer can jump straight to what matters. Links are shown in the TUI and opened in `$EDITOR` on Enter. Per change (`related`) or, when it concerns one hunk specifically, per hunk (`hunk_related`, keyed by hunk id, which must belong to that change). Look for:
   - **consumers**: callers/importers of any function, type or format whose behavior or signature changed (grep or LSP; do not guess);
   - **tests** that cover (or should cover) the change;
   - **precedent**: existing code that does the same job (reuse or duplication, relevant to `patterns`/`refactor`);
   - **dependencies**: definitions the change relies on that the diff doesn't show.

   Each link is `{"path": "src/x.py", "line": 42, "note": "why, in a few words"}`; `line` and `note` are optional. Paths are relative to the repo root and refer to the *checkout you are in*: Overseer records `HEAD` with each link, and the reviewer's editor opens the file as of that commit (read-only if their checkout differs, the real file if it matches). So choose line numbers in a checkout of the code under review; for someone else's PR, check out the PR head (or a `git worktree`) first, and make sure the commit is fetched, or the reviewer falls back to their own, possibly different, file. Keep lists short (about 6 or fewer); omit rather than pad. Add one later with `overseer-cli link <change-id> src/x.py:42 --note "..." [--hunk <hunk-id>]`.
7. **Rate risk** for each change (`risk`, plus `risk_reason` which is required for yellow and red). Risk tells the reviewer where to spend attention:
   - `green`: very little or no risk; behavior is *verifiably* correct (tests that actually exercise it, or a purely mechanical rename/move).
   - `white`: **tests, documentation or comments only**; nothing that can change runtime behavior. The TUI hides these by default (the reviewer presses `t` to see them), so this must be strictly true: if any hunk changes executable code, config, build files or a public signature, even trivially, the change is not white. Give a `risk_reason` that says what it is ("only adds tests; no source touched"). A test-only or docs-only change is white, not green; when tests ride along with the behavior they cover in one change, rate by the code.
   - `yellow`: worth a look; tests cover the behavior only partially, so a manual check may be warranted.
   - `red`: guesswork (you are unsure of the intent), a breaking change, behavior only testable by mocking external services (tests weak or absent), or sweeping impact (many consumers, a core function changed).

   "Verifiable" means you checked: read the tests that cover the change and run them when you can. Do not rate green on the strength of the commit message. When torn between two levels, pick the higher. The reason should name the specific cause (e.g. "no test covers the error path", "rename touches 14 callers").
8. **Submit** a plan: `overseer-cli plan plan.json` (or `-` for stdin):

   ```json
   {"changes": [
     {"title": "Extract hunk parser into gitdiff module",
      "hunks": ["abc1234:1", "abc1234:2"],
      "risk": "yellow",
      "risk_reason": "parser is covered, but no test hits a rename-only diff",
      "answers": {"semantic": "...", "why": "..."},
      "hunk_summaries": {"abc1234:1": "Moves the parser out of cli.py", "abc1234:2": "cli.py now calls it"},
      "related": [{"path": "src/overseer/cli.py", "line": 40, "note": "only caller"}],
      "hunk_related": {"abc1234:2": [{"path": "tests/test_x.py", "line": 12}]}}
   ]}
   ```

   Every hunk of every commit the plan mentions must be assigned **exactly once**. On any problem nothing is written and each problem is printed; fix the plan and resubmit. A plan may mix hunks from several commits when one logical change spans them (then include all hunks of all those commits).
9. **Verify**: `overseer-cli list` should show your changes with `"planned": true`.

Later corrections (also with `--changeset`): `overseer-cli record <change-id> --question why --text "..."` `--title "..."`, or `--risk green|yellow|red|white --risk-reason "..."`. The human views everything with `overseer` (TUI).
