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

## How it works

Overseer has two halves that share a small on-disk store (`.overseer/`):

- **`overseer-cli`**: the headless interface your coding agent uses. It ingests
  git commits, and the agent (guided by a skill) splits them into small,
  single-purpose *changes*, each with answers to the questions above, a risk
  rating, and links to related code.
- **`overseer`**: a terminal UI where you review those changes, leave notes
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

- `overseer-decompose`: break commits into reviewable changes.
- `overseer-feedback`: read your TUI notes, or pause for your review.

## Use it in a project

Run everything from the root of the repo being reviewed (link paths are
relative to it). The store lives in `./.overseer/` unless `OVERSEER_DIR` is
set; add `.overseer/` to that project's `.gitignore`. The TUI and CLI must
point at the same store.

1. **Prepare a review.** Ask your agent to "decompose the last commit for
   Overseer" (or a range, or a teammate's PR). It will run something like:

   ```
   overseer-cli ingest main..HEAD --changeset local
   overseer-cli show <sha> --changeset local
   overseer-cli plan plan.json --changeset local
   ```

   Every CLI command takes `--changeset NAME`, an isolated review set
   (e.g. `local`, `pr-123`), so unrelated reviews don't mix.

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
| `[` / `]` | previous / next hunk (a planned change opens on an overview) |
| `ctrl+d` / `ctrl+u` | scroll the diff |
| `Enter` | open the highlighted related-code link in `$EDITOR` |
| `O` | open the working-tree file for a link recorded at another commit |
| `t` | toggle the code view and the hidden white (tests/docs) view |
| `c` | switch changeset (`d` then `y` deletes the highlighted one) |
| `n` / `N` / `m` | note on the hunk / change / changeset (Enter saves, Esc cancels) |
| `R` | mark everything reviewed |
| `D` | finish a review the agent requested |

Risk is shown per change: red, yellow, green, or white (tests/docs/comments
only, hidden until you press `t`; the list border always shows how many).
Changes new or modified since your last `R`/`D` are flagged `NEW`,
`CHANGED +N` or `UPDATED`.

Set `$EDITOR` (or `$VISUAL`) for link opening; terminal editors suspend the
TUI, GUI editors are launched detached.

## Development

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format .
```

See `CLAUDE.md` for architecture notes.
