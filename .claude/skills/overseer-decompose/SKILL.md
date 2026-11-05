---
name: overseer-decompose
description: Break a git commit (or range) into small, single-purpose, reviewable changes made of deltas with answers to the review questions, using overseer-cli. Use when asked to prepare commits for Overseer review, decompose or split a change, or when `overseer-cli list --unplanned` shows whole-commit changes.
---

# Decompose commits into reviewable changes

Goal: the reviewer reviews each *change* in a few minutes. Turn each commit into
changes that each do one thing, in review order (so they read as a story), with the
answers the reviewer needs. Commands below run from the repo being reviewed;
in the overseer repo itself use `uv run overseer-cli`.

## Vocabulary

Use these terms, in chat, titles, answers and summaries, exactly as the README's Vocabulary table defines them: **reviewer** (the person; never "the human" or "the user"), **agent** (you), **commit**, **changed line**, **delta**, **move**, **section**, **change**, **whole commit** (an undecomposed change), **decompose**, **plan**, **review order**, **answer** (to one of the eight **review questions**), **delta summary**, **related code**, **risk** (green/yellow/red/white) and **risk reason**, **reviewset**, **note**, **baseline**. Do not invent synonyms ("PR chunk", "diff section", "hunk" for a delta, "link" for related code in prose, "snapshot" for baseline). A **delta is not a git hunk**: you declare it. CLI names stay as they are (`--unplanned` lists whole commits; `link` / `related` carry related code).

## Reviewsets

Review material is grouped into **reviewsets** (e.g. `local`, `pr-123`) so the reviewer can swap between unrelated reviews in the TUI (`c`). Pick a name for what you are preparing (`pr-<number>` for a teammate's PR, otherwise ask or use `local`) and pass `--reviewset <name>` on **every** command below (the CLI rejects commands without it, because the active reviewset is whichever one the reviewer last switched to, which may be a different review). Ingest creates the reviewset without switching the reviewer's view; they switch when ready. `overseer-cli reviewset list|new|use|delete` manages them (`delete` discards everything in one).

**Several agents at once.** The store is shared, so keep to your own lane: run `overseer-cli reviewset list` first and use a reviewset name that is yours (the PR number, or something specific like `local-<task>`), never one another agent is working in. Do not `reviewset use` or `reviewset delete` a reviewset you did not create, and do not `ingest --force` or re-`plan` commits already decomposed in someone else's reviewset. Say in chat which reviewset you used so the reviewer and other agents can find it.

For a PR, fetch it so the range resolves (e.g. `gh pr checkout` or `git fetch origin pull/123/head:pr-123`), then ingest `<base>..pr-123` into its own reviewset.

## What you are declaring

Overseer cannot know what a change *means*; only you can. So you do not point at diff lines and let the tool guess: you **declare** each delta, saying what it is and what the code looked like. What you declare is the source of truth for how the reviewer sees it.

- A **change** is one intention you can state in a sentence ("renamed `foo` to `bar`", "guarded the empty case"). Title it that way.
- A **delta** is the smallest piece of a change worth reviewing on its own. It has a `kind`, and the code at each end:
  - `insert`: new code. Give `after` (where it is now and what it looks like). Shown whole, syntax highlighted, green bar.
  - `delete`: code removed. Give `before`. Red bar.
  - `change`: X became Y in one file ("`if a:` became `if a and b:`"). Give `before` and `after`. Shown as a small diff with the changed words emphasised. Keep these small and focused.
  - `move`: code relocated (extracted into a function, nested under a new `if`, carried to another file). Give `before` (where it was, what it looked like) and `after` (where it is, what it looks like). Shown as *removed from here* / *added here*.
- Each end (`before`/`after`) is `{"path", "lines", "symbol", "text"}`: the file, the line or range in the file *at that end* (`"12"` or `"12-20"`: old-file numbers for `before`, new-file numbers for `after`, as `show` prints them), the enclosing function or class (`symbol`, give it whenever there is one), and `text`, the code as it looked there. Leave `text` out when the commit's diff shows those lines and Overseer copies it; include it whenever the diff does not (or you want to show something other than the raw lines). Overseer does not check your `text` against the commit.
- Git's hunk boundaries do not matter. Declare the pieces you mean, however they fall in the diff.

Do not make deltas bigger than one idea or smaller than the reviewer would want to read separately. A `-` line and the `+` line that replaces it are one `change`, not a delete plus an insert.

## Moves

When code is relocated, declare it as **one `move` delta** and declare everything else that changed around it as *separate* deltas, so the reviewer sees "removed from here, added here" once, and the new branch or new signature is judged on its own.

`overseer-cli show` ends with **Possible moves**: removed lines that reappear as added lines, ignoring indentation. They are only hints from a text match; confirm by reading before using one. For example, after extracting a block into a new function that guards it with an `if`:

```json
{"kind": "move", "commit": "abc1234",
 "before": {"path": "src/a.py", "symbol": "run", "lines": "2-6"},
 "after":  {"path": "src/b.py", "symbol": "helper", "lines": "4-8"},
 "summary": "The loop body, moved unchanged and re-indented"},
{"kind": "insert", "commit": "abc1234",
 "after": {"path": "src/b.py", "symbol": "helper", "lines": "2-3"},
 "summary": "The new function and the `if` that now guards the moved block"},
{"kind": "change", "commit": "abc1234",
 "before": {"path": "src/a.py", "symbol": "run", "lines": "7"},
 "after":  {"path": "src/a.py", "symbol": "run", "lines": "2"},
 "summary": "The call that replaces the block"}
```

If the code was edited on the way, still declare one move: the TUI tints lines with no match on the other side. Do not call something a move when it was merely rewritten, or when only a few lines match.

## Procedure

1. **Ingest**: `overseer-cli ingest <rev> --reviewset <name>` (`HEAD`, a sha, or `A..B`). Re-running is safe. Output shows the file and changed-line counts per commit (it also stores each file as one unclassified `raw` delta until you plan).
2. **Read**: `overseer-cli show <sha> --reviewset <name>` prints the commit message, then each file's diff with old/new line numbers beside every line (`--json` gives the same as data). Read all of it, and read surrounding code in the repo when a line's purpose isn't clear from the diff.
3. **Group** the commit's changes into changes, then declare each change's deltas:
   - one purpose per change (a refactor, a behavior change, tests, a rename, config), not one per file;
   - within a change, one delta per piece, in any number of files;
   - relocated code is a `move` (see "Moves"): check `show`'s Possible moves before declaring;
   - keep mechanical noise (renames, formatting, moved code) separate from logic changes, so the reviewer can skim it;
   - tests go with the behavior they cover unless large;
   - write changes in review order, so each makes sense after the previous one; order deltas within a change the same way
     (the TUI lists red first, then yellow, unrated, green, keeping your order within each tier; `white` changes are hidden behind a key, so write each change to stand reasonably alone rather than relying on the previous one);
   - a small commit may stay a single change. Do not split for its own sake.
4. **Account for everything**: Overseer does not force you to cover every changed line (you are the authority on what the change is), but it reports any changed line no delta mentions, as a `warning:` when you submit and a `⚠ N changed lines in no delta` badge in the TUI. Treat a warning as a likely omission: declare the missing piece, or accept that the reviewer will see the badge.
5. **Answer** the review questions for each change via `answers`. Valid keys: `mechanical`, `semantic`, `why`, `impact`, `behavior`, `patterns`, `refactor`, `coverage` (the eight review questions). Include only answers you can support from the code and commit message; do not guess intent. For `why`, say so when the commit message gives no reason. Use `patterns`/`refactor` to flag duplication of existing code you actually found. Keep each answer to one or two sentences.
   **Scale commentary to risk** (so rate risk, step 7, first): the reviewer's attention is the scarce resource, and an unanswered question is fine. Do not answer all eight by default; omit any question whose answer is obvious from the title, the diff, or another answer.
   - `white` and `green`: almost nothing, often a single short `mechanical` or `why`, or no answers at all.
   - `yellow`: only the answers that explain *why it is not green* or that the reviewer needs to judge it (typically `semantic`, `coverage`, `impact`; two to four answers).
   - `red`: the fullest, still short; cover what makes it red and what to check.
   **Summarise each delta** (`summary` on the delta): the TUI opens a change on its description plus an outline of its deltas, then `]` steps through them, showing only that delta's summary above the diff (the change-wide answers are *not* repeated). So write each summary as one sentence saying what this delta does and, when the order isn't obvious, why it comes here ("defines the field the next deltas fill in", "caller updated for the new signature"). Give every delta of a multi-delta change one; do not restate the title. Order: definitions, then implementation, then callers, then tests, unless the story reads better otherwise. Add or fix one later with `overseer-cli record <change-id> --delta <delta-id> --summary "..."`.
6. **Add related code** so the reviewer can jump straight to what matters. It is shown in the TUI and opened in `$EDITOR` on Enter. Per change (`related`) or, when it concerns one delta specifically, on that delta (`related` inside the delta). Look for:
   - **consumers**: callers/importers of any function, type or format whose behavior or signature changed (grep or LSP; do not guess);
   - **tests** that cover (or should cover) the change;
   - **precedent**: existing code that does the same job (reuse or duplication, relevant to `patterns`/`refactor`);
   - **dependencies**: definitions the change relies on that the diff doesn't show.

   Each entry is `{"path": "src/x.py", "line": 42, "note": "why, in a few words"}`; `line` and `note` are optional. Paths are relative to the repo root and refer to the *checkout you are in*: Overseer records `HEAD` with each entry, and the reviewer's editor opens the file as of that commit (read-only if their checkout differs, the real file if it matches). So choose line numbers in a checkout of the code under review; for someone else's PR, check out the PR head (or a `git worktree`) first, and make sure the commit is fetched, or the reviewer falls back to their own, possibly different, file. Keep lists short (about 6 or fewer); omit rather than pad. Add one later with `overseer-cli link <change-id> src/x.py:42 --note "..." [--delta <delta-id>]`.
7. **Rate risk** for each change (`risk`, plus `risk_reason` which is required for yellow and red). Risk tells the reviewer where to spend attention:
   - `green`: very little or no risk; behavior is *verifiably* correct (tests that actually exercise it, or a purely mechanical rename/move).
   - `white`: **tests, documentation or comments only**; nothing that can change runtime behavior. The TUI hides these by default (the reviewer presses `t` to see them), so this must be strictly true: if any delta changes executable code, config, build files or a public signature, even trivially, the change is not white. Give a `risk_reason` that says what it is ("only adds tests; no source touched"). A test-only or docs-only change is white, not green; when tests ride along with the behavior they cover in one change, rate by the code.
   - `yellow`: worth a look; tests cover the behavior only partially, so a manual check may be warranted.
   - `red`: guesswork (you are unsure of the intent), a breaking change, behavior only testable by mocking external services (tests weak or absent), or sweeping impact (many consumers, a core function changed).

   "Verifiable" means you checked: read the tests that cover the change and run them when you can. Do not rate green on the strength of the commit message. When torn between two levels, pick the higher. The reason should name the specific cause (e.g. "no test covers the error path", "rename touches 14 callers").
8. **Submit** a plan: `overseer-cli plan plan.json --reviewset <name>` (or `-` for stdin; give the file a name unique to you, not a shared `plan.json`):

   ```json
   {"changes": [
     {"title": "Extract the patch parser into gitdiff",
      "risk": "yellow",
      "risk_reason": "parser is covered, but no test hits a rename-only diff",
      "answers": {"semantic": "...", "why": "..."},
      "related": [{"path": "src/overseer/cli.py", "line": 40, "note": "only caller"}],
      "deltas": [
        {"kind": "move", "commit": "abc1234",
         "before": {"path": "src/overseer/cli.py", "symbol": "parse", "lines": "10-39"},
         "after":  {"path": "src/overseer/gitdiff.py", "symbol": "parse_patch",
                    "lines": "1-30"},
         "summary": "The parser, moved here unchanged"},
        {"kind": "change", "commit": "abc1234",
         "before": {"path": "src/overseer/cli.py", "symbol": "ingest",
                    "lines": "40", "text": "src = parse(raw)"},
         "after":  {"path": "src/overseer/cli.py", "symbol": "ingest",
                    "lines": "11", "text": "src = gitdiff.parse_patch(raw)"},
         "summary": "cli.py now calls it",
         "related": [{"path": "tests/test_x.py", "line": 12}]}
      ]}
   ]}
   ```

   `lines` are line numbers in the file at that end, as `show` printed them. Forms a plan is rejected for (nothing is written): unknown `kind`/commit, a `kind` missing a side it needs (or carrying one it must not have: `insert` only `after`, `delete` only `before`, `change` and `move` both; a `change` stays in one file), malformed `lines`, missing `text` that the commit's diff cannot supply, the same delta declared twice, bad risk/answers/related. A plan may mix commits when one logical change spans them.
9. **Verify**: `overseer-cli list --reviewset <name>` should show your changes with `"planned": true`; each delta's `id` is what notes and `record --delta` use.

Later corrections (also with `--reviewset`): `overseer-cli record <change-id> --question why --text "..."` `--title "..."`, or `--risk green|yellow|red|white --risk-reason "..."`. The reviewer views everything with `overseer` (the TUI).
