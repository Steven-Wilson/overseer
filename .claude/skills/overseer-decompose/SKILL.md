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
6. **Rate risk** for each change (`risk`, plus `risk_reason` which is required for yellow and red). Risk tells the reviewer where to spend attention:
   - `green`: very little or no risk; behavior is *verifiably* correct (tests that actually exercise it, or purely mechanical/docs/rename).
   - `yellow`: worth a look; tests cover the behavior only partially, so a manual check may be warranted.
   - `red`: guesswork (you are unsure of the intent), a breaking change, behavior only testable by mocking external services (tests weak or absent), or sweeping impact (many consumers, a core function changed).

   "Verifiable" means you checked: read the tests that cover the change and run them when you can. Do not rate green on the strength of the commit message. When torn between two levels, pick the higher. The reason should name the specific cause (e.g. "no test covers the error path", "rename touches 14 callers").
7. **Submit** a plan: `overseer-cli plan plan.json` (or `-` for stdin):

   ```json
   {"changes": [
     {"title": "Extract hunk parser into gitdiff module",
      "hunks": ["abc1234:1", "abc1234:2"],
      "risk": "yellow",
      "risk_reason": "parser is covered, but no test hits a rename-only diff",
      "answers": {"semantic": "...", "why": "..."}}
   ]}
   ```

   Every hunk of every commit the plan mentions must be assigned **exactly once**. On any problem nothing is written and each problem is printed; fix the plan and resubmit. A plan may mix hunks from several commits when one logical change spans them (then include all hunks of all those commits).
8. **Verify**: `overseer-cli list` should show your changes with `"planned": true`.

Later corrections: `overseer-cli record <change-id> --question why --text "..."` `--title "..."`, or `--risk green|yellow|red --risk-reason "..."`. The human views everything with `overseer` (TUI).
