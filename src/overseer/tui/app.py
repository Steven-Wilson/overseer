from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, ListItem, ListView, Static

from overseer.editor import build_command, is_gui, resolve_editor
from overseer.model import QUESTIONS, RISKS, Change, Hunk, Link, Note, Source
from overseer.store import Store, resolve_hunks

POLL_SECONDS = 1.0


def risk_badge(change: Change) -> Text:
    if change.risk not in RISKS:
        return Text("○ unrated", style="dim")
    return Text(f"● {change.risk.upper()}", style=f"bold {change.risk}")


def append_note(out: Text, note: Note, where: str) -> None:
    done = note.status == "addressed"
    out.append("✓ " if done else "✎ ", style="green" if done else "bold yellow")
    out.append(f"{note.id} {where}: ", style="dim")
    out.append(f"{note.text}\n")
    if note.reply:
        out.append(f"    agent: {note.reply}\n", style="italic cyan")


def note_count(change: Change, notes: list[Note]) -> tuple[int, int]:
    """(open, addressed) notes on the change itself or any of its hunks."""
    mine = [
        n
        for n in notes
        if (n.kind == "change" and n.ref == change.id)
        or (n.kind == "hunk" and n.ref in change.hunk_ids)
    ]
    opened = sum(1 for n in mine if n.status == "open")
    return opened, len(mine) - opened


def render_change(
    change: Change,
    hunks: list[tuple[Source, int, Hunk]],
    hunk_index: int,
    notes: list[Note] | None = None,
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

    notes = notes or []
    top = [
        (n, "change" if n.kind == "change" else "changeset")
        for n in notes
        if (n.kind == "change" and n.ref == change.id) or n.kind == "changeset"
    ]
    if top:
        out.append("Your notes\n", style="bold")
        for n, where in top:
            append_note(out, n, where)
        out.append("\n")

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
        here = [n for n in notes if n.kind == "hunk" and n.ref == source.hunk_id(idx)]
        if here:
            out.append("\nYour notes on this hunk\n", style="bold")
            for n in here:
                append_note(out, n, "hunk")
    else:
        out.append("No hunks.\n", style="dim")
    return out


class VimList(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]


class ChangesetList(VimList):
    """While `confirming`, the next key answers the delete prompt: `y` confirms,
    anything else cancels. Stopping the event here keeps it from reaching the
    app's own bindings (Textual runs those after the focused widget)."""

    confirming: str | None = None

    async def on_key(self, event: events.Key) -> None:
        if self.confirming is None:
            return
        event.stop()
        event.prevent_default()
        name, self.confirming = self.confirming, None
        await self.app.finish_delete(name, event.key == "y")  # type: ignore[attr-defined]


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


PICKER_TITLE = "Changesets (enter: switch, d: delete)"


class ChangesetItem(ListItem):
    def __init__(self, name: str, count: int, active: bool) -> None:
        label = Text()
        label.append("● " if active else "  ", style="bold green")
        label.append(name, style="bold" if active else "")
        label.append(f"  {count} change{'' if count == 1 else 's'}", style="dim")
        super().__init__(Static(label))
        self.changeset = name


class OverseerApp(App):
    TITLE = "Overseer"
    CSS = """
    #left { width: 35%; min-width: 24; }
    #changes { height: 1fr; border: round $panel; }
    #changesets { display: none; height: auto; max-height: 12; border: round $accent; }
    #changesets.open { display: block; }
    #changesets.confirm { border: round $error; }
    #note-input { display: none; border: round $accent; }
    #note-input.open { display: block; }
    #right { width: 1fr; }
    #detail-pane { height: 1fr; padding: 0 1; }
    #links { height: auto; max-height: 40%; border: round $panel; }
    #changes:focus, #links:focus { border: round $accent; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "changesets", "Changesets"),
        Binding("d", "delete_changeset", "Delete changeset"),
        Binding("n", "note('hunk')", "Note on hunk"),
        Binding("N", "note('change')", "Note on change"),
        Binding("m", "note('changeset')", "Note on changeset"),
        Binding("D", "done_reviewing", "Done reviewing"),
        Binding("escape", "cancel", "Cancel", show=False),
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
        self._name = self.store.name  # changeset being shown
        self._remembered: dict[str, str | None] = {}  # selection per changeset
        self._selected: str | None = None  # change id, stable across refreshes
        self._hunk = 0
        self._shown_rows: list | None = None  # what the links list was built from
        self._notes: list[Note] = []
        self._review: dict = {}
        self._note_target: tuple[str, str] | None = None  # (kind, ref) being typed

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left"):
                yield ChangesetList(id="changesets")
                yield VimList(id="changes")
            with Vertical(id="right"):
                pane = VerticalScroll(Static(id="detail"), id="detail-pane")
                pane.can_focus = False  # scrolled with ctrl+d/ctrl+u instead
                yield pane
                yield VimList(id="links")
        yield Input(id="note-input")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#changes").border_title = "Changes"
        self.query_one("#changesets").border_title = PICKER_TITLE
        self.query_one("#links").border_title = "Related code"
        await self.action_refresh()
        self.query_one("#changes").focus()
        # Agents write while the TUI is open, so poll rather than load once.
        self.set_interval(POLL_SECONDS, self.action_refresh)

    async def action_refresh(self) -> None:
        # The active changeset can move under us (`overseer-cli changeset use`).
        switched = self.store.name != self._name
        if switched:
            self._remembered[self._name] = self._selected
            self._name = self.store.name
            self._selected = self._remembered.get(self._name)
            self._hunk = 0
            self._shown_rows = None
        review = self.store.review()
        if review != self._review:
            self._review = review
            self.refresh_bindings()
        self.sub_title = f"changeset: {self._name}" + (
            "  ·  REVIEW REQUESTED, press D when done"
            + (f": {review['message']}" if review["message"] else "")
            if review["state"] == "requested"
            else ""
        )
        sources = self.store.sources()
        changes = self.store.changes(sources)
        notes = self.store.notes()
        same_changes = [c.to_dict() for c in changes] == [
            c.to_dict() for c in self._changes
        ]
        if same_changes and sources == self._sources and not switched:
            if notes != self._notes:  # relabel in place: keep cursor and hunk
                self._notes = notes
                for item, change in zip(
                    self.query("#changes ListItem"), changes, strict=False
                ):
                    item.query_one(Static).update(self._label(change))
                await self._show()
            return
        self._changes, self._sources, self._notes = changes, sources, notes
        view = self.query_one("#changes", ListView)
        await view.clear()
        await view.extend(ListItem(Static(self._label(c))) for c in changes)
        ids = [c.id for c in changes]
        if self._selected in ids:
            view.index = ids.index(self._selected)
        elif changes:
            view.index = 0
        await self._show()

    def _label(self, change: Change) -> Text:
        label = Text()
        if change.risk in RISKS:
            label.append("● ", style=f"bold {change.risk}")
        else:
            label.append("○ ", style="dim")
        label.append(change.title)
        if not change.planned:
            label.append(" (whole commit)", style="dim yellow")
        opened, addressed = note_count(change, self._notes)
        if opened:
            label.append(f" ✎{opened}", style="bold yellow")
        if addressed:  # resolved notes stay visible, just quieter
            label.append(f" ✓{addressed}", style="green")
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
            detail.update(render_change(change, hunks, self._hunk, self._notes))
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

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, LinkItem):
            self._open_link(event.item.link)
        elif isinstance(event.item, ChangesetItem):
            self.store.set_active(event.item.changeset)
            await self.action_close_changesets()
            await self.action_refresh()

    async def action_changesets(self) -> None:
        picker = self.query_one("#changesets", ListView)
        if picker.has_class("open"):
            await self.action_close_changesets()
            return
        await self._fill_picker()
        picker.add_class("open")
        picker.focus()
        self.refresh_bindings()

    async def _fill_picker(self, index: int | None = None) -> None:
        picker = self.query_one("#changesets", ChangesetList)
        names = self.store.changeset_names()
        await picker.clear()
        await picker.extend(
            ChangesetItem(n, self.store.count_changes(n), n == self._name)
            for n in names
        )
        if index is None:
            index = names.index(self._name) if self._name in names else 0
        picker.index = min(index, len(names) - 1)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "delete_changeset":  # only meaningful (and shown) in the picker
            return self.query_one("#changesets").has_class("open")
        if action == "done_reviewing":
            return self._review.get("state") == "requested"
        return True

    # -- reviewer notes ------------------------------------------------

    def action_note(self, kind: str) -> None:
        change = self._current()
        if kind != "changeset" and not change:
            return
        ref = ""
        if kind == "hunk" and change:
            hunks = resolve_hunks(change, self._sources)
            if hunks:
                source, idx, _ = hunks[max(0, min(self._hunk, len(hunks) - 1))]
                ref = source.hunk_id(idx)
            else:
                kind = "change"  # nothing to point at, so note the change
        if kind == "change" and change:
            ref = change.id
        self._note_target = (kind, ref)
        box = self.query_one("#note-input", Input)
        box.value = ""
        box.placeholder = (
            f"Note on {kind} {ref}".strip() + "  (enter: save, esc: cancel)"
        )
        box.add_class("open")
        box.focus()

    def _close_note_input(self) -> None:
        self._note_target = None
        self.query_one("#note-input").remove_class("open")
        self.query_one("#changes").focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text, target = event.value.strip(), self._note_target
        self._close_note_input()
        if not text or not target:
            return
        kind, ref = target
        self.store.save_note(
            Note(
                self.store.next_note_id(),
                kind,
                ref,
                text,
                datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
        await self.action_refresh()

    async def action_cancel(self) -> None:
        if self._note_target is not None:
            self._close_note_input()
        else:
            await self.action_close_changesets()

    async def action_done_reviewing(self) -> None:
        if self._review.get("state") != "requested":
            return
        self.store.set_review("done", self._review.get("message", ""))
        self.notify("Marked review done; the agent can now read your notes")
        await self.action_refresh()

    def action_delete_changeset(self) -> None:
        picker = self.query_one("#changesets", ChangesetList)
        item = picker.highlighted_child
        if not isinstance(item, ChangesetItem):
            return
        picker.confirming = item.changeset
        picker.add_class("confirm")
        picker.border_title = (
            f"Delete '{item.changeset}' and its changes? y = delete, any key = cancel"
        )

    async def finish_delete(self, name: str, confirmed: bool) -> None:
        picker = self.query_one("#changesets", ChangesetList)
        picker.remove_class("confirm")
        picker.border_title = PICKER_TITLE
        if not confirmed:
            return
        self.store.delete_changeset(name)
        self.notify(f"Deleted changeset '{name}'")
        index = picker.index or 0
        await self._fill_picker(index)
        await self.action_refresh()  # the viewed changeset may be the one deleted
        self._remembered.pop(name, None)

    async def action_close_changesets(self) -> None:
        picker = self.query_one("#changesets", ChangesetList)
        if picker.has_class("open"):
            picker.confirming = None
            picker.remove_class("open", "confirm")
            picker.border_title = PICKER_TITLE
            self.refresh_bindings()
            self.query_one("#changes").focus()

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
