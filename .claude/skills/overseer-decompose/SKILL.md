---
name: overseer-decompose
description: Break a git commit (or range) into small, single-purpose, human-reviewable changes with explanations, using overseer-cli. Use when asked to prepare commits for Overseer review, decompose or split a change, or when `overseer-cli list --unplanned` shows whole-commit changes.
---

# Decompose commits into reviewable changes

Goal: a human reviews each *change* in a few minutes. Turn each commit into
changes that each do one thing, ordered so they read as a story, with the
answers the reviewer needs. Commands below run from the repo being reviewed;
in the overseer repo itself use `uv run overseer-cli`.

## Procedure

1. **Ingest**: `overseer-cli ingest <rev>` (`HEAD`, a sha, or `A..B`). Re-running is safe. Output shows hunk and line counts per commit.
2. **Read**: `overseer-cli show <sha>` prints the commit message and every hunk with its id (`<sha>:<n>`). Read all of it, and read surrounding code in the repo when a hunk's purpose isn't clear from the diff.
3. **Too coarse?** If one hunk mixes concerns, re-ingest finer: `overseer-cli ingest <sha> --context 0 --force`. This discards any changes already planned for that commit, so do it *before* planning. Hunk ids change; re-run `show`.
4. **Group** hunks into changes:
   - one purpose per change (a refactor, a behavior change, tests, a rename, config), not one per file;
   - keep mechanical noise (renames, formatting, moved code) separate from logic changes, so the reviewer can skim it;
   - tests go with the behavior they cover unless large;
   - order changes so each makes sense after the previous one; order hunks within a change the same way;
   - a small commit may stay a single change. Do not split for its own sake.
5. **Explain** each change via `answers`. Valid keys: `mechanical`, `semantic`, `why`, `impact`, `behavior`, `patterns`, `refactor`, `coverage` (the eight review questions). Include only answers you can support from the code and commit message; do not guess intent. For `why`, say so when the commit message gives no reason. Use `patterns`/`refactor` to flag duplication of existing code you actually found. Keep each answer to a few sentences.
6. **Submit** a plan: `overseer-cli plan plan.json` (or `-` for stdin):

   ```json
   {"changes": [
     {"title": "Extract hunk parser into gitdiff module",
      "hunks": ["abc1234:1", "abc1234:2"],
      "answers": {"semantic": "...", "why": "..."}}
   ]}
   ```

   Every hunk of every commit the plan mentions must be assigned **exactly once**. On any problem nothing is written and each problem is printed; fix the plan and resubmit. A plan may mix hunks from several commits when one logical change spans them (then include all hunks of all those commits).
7. **Verify**: `overseer-cli list` should show your changes with `"planned": true`.

Later corrections: `overseer-cli record <change-id> --question why --text "..."` or `--title "..."`. The human views everything with `overseer` (TUI).
