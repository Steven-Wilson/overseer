from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, ListItem, ListView, Static

from overseer.model import QUESTIONS, Change, Hunk, Source
from overseer.store import Store, resolve_hunks

POLL_SECONDS = 1.0


def render_change(
    change: Change, hunks: list[tuple[Source, int, Hunk]], hunk_index: int
) -> Text:
    """Explanation first, then the commit the current hunk came from, then the
    hunk. Built as `Text` (not markup) because diff content is full of brackets."""
    out = Text()
    out.append(f"{change.title}\n", style="bold underline")
    if not change.planned:
        out.append(
            f"Not yet decomposed: whole commit, {len(hunks)} hunks. "
            "Ask an agent to run the overseer-decompose skill.\n",
            style="yellow",
        )
    out.append("\n")

    for key, answer in change.answers.items():
        if key in QUESTIONS:
            out.append(f"{QUESTIONS[key]}\n", style="bold")
            out.append(f"{answer}\n\n")

    if hunks:
        hunk_index = max(0, min(hunk_index, len(hunks) - 1))
        source, idx, hunk = hunks[hunk_index]
        out.append(f"Commit {source.id}\n", style="dim")
        out.append(f"{source.message}\n\n", style="dim")
        out.append(
            f"Hunk {hunk_index + 1}/{len(hunks)} "
            f"({source.hunk_id(idx)})  {hunk.path}\n",
            style="bold cyan",
        )
        out.append(f"{hunk.header}\n", style="magenta")
        for line in hunk.lines:
            style = {"+": "green", "-": "red"}.get(line[:1], "")
            out.append(f"{line}\n", style=style)
    else:
        out.append("No hunks.\n", style="dim")
    return out


class OverseerApp(App):
    TITLE = "Overseer"
    CSS = """
    #changes { width: 35%; min-width: 24; border-right: solid $primary; }
    #detail-pane { width: 1fr; padding: 0 1; }
    """
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
        self._sources: dict[str, Source] = {}
        self._selected: str | None = None  # change id, stable across refreshes
        self._hunk = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListView(id="changes")
            with VerticalScroll(id="detail-pane"):
                yield Static(id="detail")
        yield Footer()

    async def on_mount(self) -> None:
        await self.action_refresh()
        # Agents write while the TUI is open, so poll rather than load once.
        self.set_interval(POLL_SECONDS, self.action_refresh)

    async def action_refresh(self) -> None:
        sources = self.store.sources()
        changes = self.store.changes(sources)
        same_changes = [c.to_dict() for c in changes] == [
            c.to_dict() for c in self._changes
        ]
        if same_changes and sources == self._sources:
            return
        self._changes, self._sources = changes, sources
        view = self.query_one("#changes", ListView)
        await view.clear()
        await view.extend(ListItem(Static(self._label(c))) for c in changes)
        ids = [c.id for c in changes]
        if self._selected in ids:
            view.index = ids.index(self._selected)
        elif changes:
            view.index = 0
        self._show()

    @staticmethod
    def _label(change: Change) -> Text:
        label = Text(change.title)
        if not change.planned:
            label.append(" (whole commit)", style="dim yellow")
        return label

    def _current(self) -> Change | None:
        for c in self._changes:
            if c.id == self._selected:
                return c
        return None

    def _show(self) -> None:
        change = self._current()
        detail = self.query_one("#detail", Static)
        if change:
            hunks = resolve_hunks(change, self._sources)
            detail.update(render_change(change, hunks, self._hunk))
        else:
            detail.update("")

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
        if change and change.hunk_ids:
            last = len(resolve_hunks(change, self._sources)) - 1
            self._hunk = max(0, min(self._hunk + delta, last))
            self._show()
            self.query_one("#detail-pane").scroll_home(animate=False)

    def action_next_hunk(self) -> None:
        self._step(1)

    def action_prev_hunk(self) -> None:
        self._step(-1)
