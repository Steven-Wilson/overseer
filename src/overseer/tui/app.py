from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, ListItem, ListView, Static

from overseer.model import QUESTIONS, Change
from overseer.store import Store

POLL_SECONDS = 1.0


def render_change(change: Change, hunk_index: int) -> Text:
    """Commit message as context, then one hunk, then the recorded answers.
    Built as `Text` (not markup) because diff content is full of brackets."""
    out = Text()
    out.append(f"{change.id}\n", style="dim")
    out.append(change.message or change.title, style="bold")
    out.append("\n\n")

    if change.hunks:
        hunk_index = max(0, min(hunk_index, len(change.hunks) - 1))
        hunk = change.hunks[hunk_index]
        out.append(
            f"Hunk {hunk_index + 1}/{len(change.hunks)}  {hunk.path}\n",
            style="bold cyan",
        )
        out.append(f"{hunk.header}\n", style="magenta")
        for line in hunk.lines:
            style = {"+": "green", "-": "red"}.get(line[:1], "")
            out.append(f"{line}\n", style=style)
    else:
        out.append("No hunks recorded.\n", style="dim")

    answered = {k: v for k, v in change.answers.items() if k in QUESTIONS}
    if answered:
        out.append("\n")
        for key, answer in answered.items():
            out.append(f"{QUESTIONS[key]}\n", style="bold")
            out.append(f"{answer}\n")
    return out


class OverseerApp(App):
    TITLE = "Overseer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("n", "next_hunk", "Next hunk"),
        ("p", "prev_hunk", "Prev hunk"),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self._changes: list[Change] = []
        self._selected: str | None = None  # change id, stable across refreshes
        self._hunk = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(id="changes")
        yield VerticalScroll(Static(id="detail"))
        yield Footer()

    async def on_mount(self) -> None:
        await self.action_refresh()
        # Agents write while the TUI is open, so poll rather than load once.
        self.set_interval(POLL_SECONDS, self.action_refresh)

    async def action_refresh(self) -> None:
        changes = self.store.list()
        if [c.to_dict() for c in changes] == [c.to_dict() for c in self._changes]:
            return
        self._changes = changes
        view = self.query_one("#changes", ListView)
        await view.clear()
        await view.extend(ListItem(Static(Text(f"{c.id} {c.title}"))) for c in changes)
        ids = [c.id for c in changes]
        if self._selected in ids:
            view.index = ids.index(self._selected)
        elif changes:
            view.index = 0
        self._show()

    def _current(self) -> Change | None:
        for c in self._changes:
            if c.id == self._selected:
                return c
        return None

    def _show(self) -> None:
        change = self._current()
        detail = self.query_one("#detail", Static)
        detail.update(render_change(change, self._hunk) if change else "")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        idx = event.list_view.index
        selected = (
            self._changes[idx].id
            if idx is not None and idx < len(self._changes)
            else None
        )
        if selected != self._selected:
            self._selected = selected
            self._hunk = 0
        self._show()

    def _step(self, delta: int) -> None:
        change = self._current()
        if change and change.hunks:
            self._hunk = max(0, min(self._hunk + delta, len(change.hunks) - 1))
            self._show()
            self.query_one(VerticalScroll).scroll_home(animate=False)

    def action_next_hunk(self) -> None:
        self._step(1)

    def action_prev_hunk(self) -> None:
        self._step(-1)
