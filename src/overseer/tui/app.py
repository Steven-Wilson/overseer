from __future__ import annotations

import subprocess
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, ListItem, ListView, Static

from overseer.editor import build_command, is_gui, resolve_editor
from overseer.model import QUESTIONS, RISKS, Change, Hunk, Link, Source
from overseer.store import Store, resolve_hunks

POLL_SECONDS = 1.0


def risk_badge(change: Change) -> Text:
    if change.risk not in RISKS:
        return Text("○ unrated", style="dim")
    return Text(f"● {change.risk.upper()}", style=f"bold {change.risk}")


def render_change(
    change: Change, hunks: list[tuple[Source, int, Hunk]], hunk_index: int
) -> Text:
    """Explanation first, then the commit the current hunk came from, then the
    hunk. Built as `Text` (not markup) because diff content is full of brackets."""
    out = Text()
    out.append(f"{change.title}\n", style="bold underline")
    out.append_text(risk_badge(change))
    if change.risk_reason:
        out.append(f"  {change.risk_reason}")
    out.append("\n")
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


class VimList(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]


class LinkItem(ListItem):
    def __init__(self, link: Link) -> None:
        label = Text()
        label.append(link.path + (f":{link.line}" if link.line else ""), style="bold")
        if link.note:
            label.append(f"  {link.note}", style="dim")
        if not Path(link.path).exists():
            label.append("  (not found)", style="dim red")
        super().__init__(Static(label))
        self.link = link


class OverseerApp(App):
    TITLE = "Overseer"
    CSS = """
    #changes { width: 35%; min-width: 24; border: round $panel; }
    #right { width: 1fr; }
    #detail-pane { height: 1fr; padding: 0 1; }
    #links { height: auto; max-height: 40%; border: round $panel; }
    #changes:focus, #links:focus { border: round $accent; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("h", "pane('left')", "Changes"),
        Binding("l", "pane('right')", "Related code"),
        Binding("right_square_bracket", "step_hunk(1)", "Next hunk", key_display="]"),
        Binding("left_square_bracket", "step_hunk(-1)", "Prev hunk", key_display="["),
        Binding("ctrl+d", "scroll_detail(1)", "Scroll down", show=False),
        Binding("ctrl+u", "scroll_detail(-1)", "Scroll up", show=False),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self._changes: list[Change] = []
        self._sources: dict[str, Source] = {}
        self._selected: str | None = None  # change id, stable across refreshes
        self._hunk = 0
        self._shown_rows: list | None = None  # what the links list was built from

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield VimList(id="changes")
            with Vertical(id="right"):
                pane = VerticalScroll(Static(id="detail"), id="detail-pane")
                pane.can_focus = False  # scrolled with ctrl+d/ctrl+u instead
                yield pane
                yield VimList(id="links")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#changes").border_title = "Changes"
        self.query_one("#links").border_title = "Related code"
        await self.action_refresh()
        self.query_one("#changes").focus()
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
        await self._show()

    @staticmethod
    def _label(change: Change) -> Text:
        label = Text()
        if change.risk in RISKS:
            label.append("● ", style=f"bold {change.risk}")
        else:
            label.append("○ ", style="dim")
        label.append(change.title)
        if not change.planned:
            label.append(" (whole commit)", style="dim yellow")
        return label

    def _current(self) -> Change | None:
        for c in self._changes:
            if c.id == self._selected:
                return c
        return None

    def _link_rows(self) -> list[tuple[str, str | Link]]:
        """Related code for the current hunk, then for the whole change."""
        change = self._current()
        if not change:
            return []
        rows: list[tuple[str, str | Link]] = []
        hunks = resolve_hunks(change, self._sources)
        if hunks:
            source, idx, hunk = hunks[max(0, min(self._hunk, len(hunks) - 1))]
            for_hunk = change.hunk_links.get(source.hunk_id(idx), [])
            if for_hunk:
                rows.append(("head", f"This hunk · {hunk.path}"))
                rows += [("link", link) for link in for_hunk]
        if change.links:
            rows.append(("head", "This change"))
            rows += [("link", link) for link in change.links]
        return rows

    async def _show(self) -> None:
        change = self._current()
        detail = self.query_one("#detail", Static)
        if change:
            hunks = resolve_hunks(change, self._sources)
            detail.update(render_change(change, hunks, self._hunk))
        else:
            detail.update("")
        await self._sync_links()

    async def _sync_links(self) -> None:
        rows = self._link_rows()
        if rows == self._shown_rows:  # keep the cursor put while polling
            return
        self._shown_rows = rows
        view = self.query_one("#links", ListView)
        await view.clear()
        items: list[ListItem] = []
        for kind, value in rows:
            if kind == "head":
                items.append(
                    ListItem(Static(Text(str(value), style="bold")), disabled=True)
                )
            else:
                items.append(LinkItem(value))
        if not items:
            items = [
                ListItem(
                    Static(Text("No related code recorded.", style="dim")),
                    disabled=True,
                )
            ]
        await view.extend(items)
        view.index = next((i for i, (k, _) in enumerate(rows) if k == "link"), None)

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "changes":
            return
        idx = event.list_view.index
        selected = (
            self._changes[idx].id
            if idx is not None and idx < len(self._changes)
            else None
        )
        if selected != self._selected:
            self._selected = selected
            self._hunk = 0
        await self._show()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, LinkItem):
            self._open_link(event.item.link)

    def action_pane(self, which: str) -> None:
        self.query_one("#changes" if which == "left" else "#links").focus()

    async def action_step_hunk(self, delta: int) -> None:
        change = self._current()
        if change and change.hunk_ids:
            last = len(resolve_hunks(change, self._sources)) - 1
            self._hunk = max(0, min(self._hunk + delta, last))
            await self._show()
            self.query_one("#detail-pane").scroll_home(animate=False)

    def action_scroll_detail(self, direction: int) -> None:
        pane = self.query_one("#detail-pane")
        pane.scroll_relative(y=direction * max(1, pane.size.height // 2), animate=False)

    def _open_link(self, link: Link) -> None:
        editor = resolve_editor()
        if not editor:
            self.notify("Set $EDITOR (or $VISUAL) to open code", severity="error")
        elif not Path(link.path).exists():
            self.notify(f"{link.path} not found in {Path.cwd()}", severity="error")
        else:
            try:
                self.run_editor(
                    build_command(editor, link.path, link.line), is_gui(editor)
                )
            except OSError as e:
                self.notify(f"Could not start {editor[0]}: {e}", severity="error")

    def run_editor(self, cmd: list[str], gui: bool) -> None:
        if gui:  # opens its own window; keep the TUI running
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:  # terminal editor: hand over the terminal until it exits
            with self.suspend():
                subprocess.run(cmd)
