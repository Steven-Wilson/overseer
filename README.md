# Overseer

When running agentic coding flows, it can be hard to tell what is changing,
why, and what impacts it may have on the system as a whole.  It can be hard to
tell if the changes are well-architected, re-using pre-existing functionality,
and are behaving as intended.

What if there was a way to get more than just line diffs?

This project aims to help every engineering change answer these questions:

1. What changed mechanically?
2. What changed semantically?
3. Why was the change made?
4. What system parts are impacted by the change?
5. How does the change impact user-observable behavior?
6. Does this change follow existing patterns?
7. Should the code be refactored?
8. How has test coverage been impacted?

Some changes are easier than others to reason about, but in 2026 it's no longer
viable to simply review git diffs and say "the code passed code review".

## Vocabulary

These terms mean the same thing in the README, the TUI, the CLI, and the agent
skills. Use them as written.

| Term | Meaning |
| --- | --- |
| **reviewer** | The person using the TUI. Skills never say "the human" or "the user". |
| **agent** | The coding agent driving `overseer-cli`: it prepares changes and answers notes. |
| **commit** | A git commit, as ingested. (`Source` in the code; the CLI, TUI and skills say "commit".) |
| **changed line** | One added (`+`) or removed (`-`) line of a commit's diff. `overseer-cli show` prints each with its line number before (old) and after (new) the commit. Raw material the agent reads; a delta names code by file and these line numbers. |
| **delta** | The atomic unit of review, **declared by the agent**: it says what kind it is (`insert`, `delete`, `change`, `move`) and what the code looked like, with the file, enclosing symbol and line numbers. Overseer never infers meaning from the diff; what the agent declares is the source of truth. Its id is `<sha>:<kind>@<where>`, e.g. `abc1234:change@src/x.py:12>src/x.py:12`. Not a git hunk. |
| **move** | A delta declaring that code was relocated (extracted into a function, nested under a new `if`, carried to another file): `before` is where it was and what it looked like, `after` where it is now and what it looks like. Shown as *removed from here* / *added here*; whatever else changed around it is a separate delta. `overseer-cli show` lists *possible moves* it finds as hints; only the agent's declaration counts. |
| **section** | How an `insert` (green accent) or `delete` (red accent) is shown: the whole code, syntax highlighted. A `change` (before/after) is shown as a diff with the exact words that differ emphasised. |
| **change** | The unit of meaning: one intention you can state in a sentence ("renamed `foo` to `bar`", "guarded the empty case"), made of one or more deltas, possibly across files and commits. It carries a title, answers and a risk. Ids look like `abc1234-01`. |
| **whole commit** | The default change made at ingest: one per commit, one *unclassified* (`raw`: git's own diff of a file) delta per file, not yet decomposed. |
| **decompose** | Replace a commit's whole-commit change with several focused changes. Changes are *decomposed* (`planned: true` in JSON) or *undecomposed*. |
| **plan** | The JSON an agent submits (`overseer-cli plan`) to decompose: changes made of declared deltas. It is checked for form, not truth; changed lines no delta mentions are reported as a warning (and shown in the TUI) but never block it. |
| **review order** | The order the agent wrote its changes and deltas in, so they read as a story. The TUI keeps it within a risk tier. |
| **review questions** | The eight questions at the top of this file. |
| **answer** | The response to one review question, on a change. Only questions worth answering are answered. |
| **delta summary** | One sentence per delta: what it does and why it sits there. |
| **overview** | What the TUI shows first for a decomposed change: its answers and an outline of delta summaries, before delta 1. |
| **related code** | A pointer (`path`, optional line and note) to code that helps judge a change or delta: consumers, tests, precedent, dependencies. Recorded with `link` / `related` in the CLI. Enter opens it. |
| **risk** | How much reviewer attention a change needs: **red** (guesswork, breaking, weakly tested, or sweeping), **yellow** (worth a look), **green** (verifiably correct), **white** (tests, docs or comments only). Everything but green has a *risk reason*; unrated shows as `○`. |
| **code view / white view** | The two lists in the TUI. White changes are hidden from the code view (`t` swaps); the list border always counts them. |
| **reviewset** | Everything being reviewed together: a set of commits and the changes made from them, e.g. `local` or `pr-123`. Every `overseer-cli` command except `reviewset` names one with `--reviewset`. The TUI shows one at a time (the *active* one). |
| **note** | The reviewer's feedback on a delta (`n`), a change (`N`) or a reviewset (`m`). *Open* until the agent *resolves* it with a reply, then *addressed*. A note whose target vanished after a re-ingest or re-plan is *stale*. |
| **review request** | The agent pauses for the reviewer: `review request`, then `review wait` until the reviewer presses `D` (*done reviewing*). |
| **baseline** | What the reviewer had seen at their last `D` or `R` (*mark reviewed*). Matched by changed-line content, not ids. |
| **NEW / CHANGED +N / UPDATED** | Freshness against the baseline: NEW = no delta seen before; CHANGED +N = N deltas not seen; UPDATED = same code, different title, answers or risk. |
| **store** | The `.overseer/` directory both programs share. |

## How it works

Overseer has two halves that share a small on-disk store (`.overseer/`):

- **`overseer-cli`**: the headless interface your coding agent uses. It ingests
  git commits, and the agent (guided by a skill) decomposes them into small,
  single-purpose *changes*, each with answers to the review questions, a risk
  rating, and related code.
- **`overseer`**: a terminal UI where you, the reviewer, review those changes, leave notes
  for the agent, and mark what you've reviewed.

## Install

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
git clone <this repo> ~/overseer
uv tool install ~/overseer      # puts `overseer` and `overseer-cli` on your PATH
```

To update after pulling: `uv tool install --reinstall ~/overseer`.

Then install the agent skills so Claude Code knows how to drive the CLI. Copy
them into the project you want reviewed (or into `~/.claude/skills/` to have
them everywhere):

```
mkdir -p .claude/skills
cp -r ~/overseer/.claude/skills/overseer-* .claude/skills/
```

- `overseer-decompose`: decompose commits into reviewable changes.
- `overseer-feedback`: read your TUI notes, or pause for your review.

## Use it in a project

Run everything from the root of the repo being reviewed (link paths are
relative to it). The store lives in `./.overseer/` unless `OVERSEER_DIR` is
set; add `.overseer/` to that project's `.gitignore`. The TUI and CLI must
point at the same store.

1. **Prepare a review.** Ask your agent to "decompose the last commit for
   Overseer" (or a range, or a teammate's PR). It will run something like:

   ```
   overseer-cli ingest main..HEAD --reviewset local
   overseer-cli show <sha> --reviewset local
   overseer-cli plan plan.json --reviewset local
   ```

   Every CLI command takes `--reviewset NAME` (e.g. `local`, `pr-123`), so
   unrelated reviews don't mix.

2. **Review.** In another terminal, in the same repo:

   ```
   overseer
   ```

   The TUI polls the store, so changes appear as the agent writes them.

3. **Give feedback.** Leave notes for the agent, then tell it to read them
   ("check my Overseer notes"). For agentic loops, the agent can pause with
   `overseer-cli review request` and `review wait` until you press `D`.

### Keys

| Key | Action |
| --- | --- |
| `j` / `k` | move within the focused list |
| `h` / `l` | focus changes / related-code pane |
| `[` / `]` | previous / next delta (a planned change opens on an overview) |
| `ctrl+d` / `ctrl+u` | scroll the diff |
| `Enter` | open the highlighted related code in `$EDITOR` |
| `O` | open the working-tree file for related code recorded at another commit |
| `t` | swap the code view and the white view |
| `c` | switch reviewset (`d` then `y` deletes the highlighted one) |
| `n` / `N` / `m` | note on the delta / change / reviewset (Enter saves, Esc cancels) |
| `R` | mark everything reviewed |
| `D` | finish a review the agent requested |

Risk is shown per change (see Vocabulary). White changes are hidden until you
press `t`; the list border always shows how many. Changes new or modified
since your baseline (your last `R`/`D`) are flagged `NEW`, `CHANGED +N` or
`UPDATED`.

Set `$EDITOR` (or `$VISUAL`) for opening related code; terminal editors suspend the
TUI, GUI editors are launched detached.

## Development

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format .
```

See `CLAUDE.md` for architecture notes.
