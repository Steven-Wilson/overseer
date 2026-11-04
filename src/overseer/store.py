"""File-backed store under `$OVERSEER_DIR` (default `./.overseer`).

    active                                  name of the changeset being viewed
    changesets/<name>/sources/<id>.json     raw ingested material (one per commit)
    changesets/<name>/changes/<id>.json     reviewable units referencing hunks
    changesets/<name>/notes/<id>.json       the human reviewer's notes
    changesets/<name>/review.json           review handshake (idle/requested/done)
    changesets/<name>/reviewed.json         baseline: what the reviewer last saw

A changeset is an independent body of review material (e.g. "my local commits"
vs "teammate's PR 123"). Everything else operates on one changeset at a time:
the one named explicitly, else the one in `active`, else `default`.

The CLI writes, the TUI reads, except for the reviewer's own input (notes, "done
reviewing", the active changeset), which the TUI writes and the CLI reads. Writes
are atomic so a long-running TUI never sees a partial file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from overseer.model import Change, Hunk, Note, Source, split_hunk_id

DEFAULT_CHANGESET = "default"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def valid_changeset_name(name: str) -> bool:
    """Names become directory names, so no separators or leading dots."""
    return bool(_NAME.match(name))


class Store:
    """`changeset=None` follows the `active` pointer (re-read on every access,
    so a long-running TUI notices a switch); a name pins the store to it."""

    def __init__(self, root: Path | None = None, changeset: str | None = None) -> None:
        self.root = root or Path(os.environ.get("OVERSEER_DIR", ".overseer"))
        self._pinned = changeset
        if changeset is not None and not valid_changeset_name(changeset):
            raise ValueError(f"invalid changeset name {changeset!r}")

    # -- changesets ----------------------------------------------------

    @property
    def name(self) -> str:
        if self._pinned is not None:
            return self._pinned
        try:
            name = (self.root / "active").read_text().strip()
        except OSError:
            return DEFAULT_CHANGESET
        return name if valid_changeset_name(name) else DEFAULT_CHANGESET

    def _dir(self, name: str) -> Path:
        return self.root / "changesets" / name

    def _migrate_flat_layout(self) -> None:
        """Before changesets, sources/ and changes/ sat directly in the root."""
        old = [self.root / "sources", self.root / "changes"]
        if not any(d.is_dir() for d in old):
            return
        target = self._dir(DEFAULT_CHANGESET)
        target.mkdir(parents=True, exist_ok=True)
        for d in old:
            if d.is_dir() and not (target / d.name).exists():
                d.replace(target / d.name)

    @property
    def sources_dir(self) -> Path:
        self._migrate_flat_layout()
        return self._dir(self.name) / "sources"

    @property
    def changes_dir(self) -> Path:
        self._migrate_flat_layout()
        return self._dir(self.name) / "changes"

    @property
    def notes_dir(self) -> Path:
        self._migrate_flat_layout()
        return self._dir(self.name) / "notes"

    def changeset_names(self) -> list[str]:
        self._migrate_flat_layout()
        base = self.root / "changesets"
        found = (
            {p.name for p in base.iterdir() if p.is_dir()} if base.exists() else set()
        )
        return sorted(found | {self.name})

    def has_changeset(self, name: str) -> bool:
        self._migrate_flat_layout()
        return self._dir(name).is_dir()

    def count_changes(self, name: str) -> int:
        return len(list((self._dir(name) / "changes").glob("*.json")))

    def create_changeset(self, name: str) -> None:
        if not valid_changeset_name(name):
            raise ValueError(
                f"invalid changeset name {name!r} "
                "(letters, digits, '.', '_', '-'; must start with a letter or digit)"
            )
        self._dir(name).mkdir(parents=True, exist_ok=True)

    def set_active(self, name: str) -> None:
        """Move the shared pointer the TUI (and unpinned CLI calls) follow."""
        self.create_changeset(name)
        self._write_active(name)

    def _write_active(self, name: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / ".active.tmp"
        tmp.write_text(name + "\n")
        tmp.replace(self.root / "active")

    def delete_changeset(self, name: str) -> None:
        if not valid_changeset_name(name):
            raise ValueError(f"invalid changeset name {name!r}")
        shutil.rmtree(self._dir(name), ignore_errors=True)
        try:
            pointed = (self.root / "active").read_text().strip()
        except OSError:
            return
        if pointed == name:
            (self.root / "active").unlink(missing_ok=True)

    # -- low-level -----------------------------------------------------

    def _write(self, directory: Path, name: str, data: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{name}.tmp"
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(directory / f"{name}.json")

    def _read_all(self, directory: Path) -> list[dict]:
        if not directory.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]

    # -- sources -------------------------------------------------------

    def save_source(self, source: Source) -> None:
        self._write(self.sources_dir, source.id, source.to_dict())

    def load_source(self, source_id: str) -> Source | None:
        path = self.sources_dir / f"{source_id}.json"
        return Source.from_dict(json.loads(path.read_text())) if path.exists() else None

    def sources(self) -> dict[str, Source]:
        return {
            s.id: s
            for s in (Source.from_dict(d) for d in self._read_all(self.sources_dir))
        }

    def next_seq(self) -> int:
        return max((s.seq for s in self.sources().values()), default=0) + 1

    # -- notes ---------------------------------------------------------

    def save_note(self, note: Note) -> None:
        self._write(self.notes_dir, note.id, note.to_dict())

    def load_note(self, note_id: str) -> Note | None:
        path = self.notes_dir / f"{note_id}.json"
        return Note.from_dict(json.loads(path.read_text())) if path.exists() else None

    def notes(self) -> list[Note]:
        """Oldest first."""
        notes = [Note.from_dict(d) for d in self._read_all(self.notes_dir)]
        return sorted(notes, key=lambda n: _note_num(n.id))

    def next_note_id(self) -> str:
        return f"n{max((_note_num(n.id) for n in self.notes()), default=0) + 1}"

    # -- review handshake ----------------------------------------------

    def review(self) -> dict:
        """`state` is "idle", "requested" (agent is waiting on the human) or
        "done" (human finished); `message` is what the agent asked."""
        try:
            data = json.loads((self._dir(self.name) / "review.json").read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        return {"state": data.get("state", "idle"), "message": data.get("message", "")}

    def set_review(self, state: str, message: str = "") -> None:
        self._write(
            self._dir(self.name), "review", {"state": state, "message": message}
        )

    # -- reviewed baseline ---------------------------------------------

    def reviewed(self) -> dict | None:
        """Baseline for "changed since last review"; None if never reviewed."""
        try:
            return json.loads((self._dir(self.name) / "reviewed.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def save_reviewed(self, baseline: dict) -> None:
        self._write(self._dir(self.name), "reviewed", baseline)

    # -- changes -------------------------------------------------------

    def save_change(self, change: Change) -> None:
        self._write(self.changes_dir, change.id, change.to_dict())

    def load_change(self, change_id: str) -> Change | None:
        path = self.changes_dir / f"{change_id}.json"
        return Change.from_dict(json.loads(path.read_text())) if path.exists() else None

    def delete_change(self, change_id: str) -> None:
        (self.changes_dir / f"{change_id}.json").unlink(missing_ok=True)

    def changes(self, sources: dict[str, Source] | None = None) -> list[Change]:
        """Display order: by earliest ingested source, then plan position."""
        raw = self._read_all(self.changes_dir)
        if any("hunks" in d for d in raw):
            self._migrate_legacy(raw)
            raw, sources = self._read_all(self.changes_dir), None
        sources = self.sources() if sources is None else sources
        out = [Change.from_dict(d) for d in raw]

        def key(c: Change) -> tuple:
            seqs = [
                sources[sid].seq
                for sid in (_source_of(h) for h in c.hunk_ids)
                if sid in sources
            ]
            return (min(seqs, default=1 << 30), c.position, c.id)

        return sorted(out, key=key)

    def _migrate_legacy(self, raw: list[dict]) -> None:
        """Early stores kept the commit message and hunks inline in each change
        file. Move them into a Source and leave a 1:1 Change behind."""
        for d in raw:
            if "hunks" not in d:
                continue
            source = Source(
                id=d["id"],
                message=d.get("message") or d["title"],
                hunks=[Hunk(**h) for h in d["hunks"]],
                seq=self.next_seq(),
            )
            self.save_source(source)
            self.save_change(
                Change(
                    id=d["id"],
                    title=d["title"],
                    hunk_ids=[source.hunk_id(i) for i in range(len(source.hunks))],
                    answers=d.get("answers", {}),
                )
            )


def _note_num(note_id: str) -> int:
    digits = note_id[1:]
    return int(digits) if digits.isdigit() else 1 << 30


def _source_of(hunk_id: str) -> str:
    try:
        return split_hunk_id(hunk_id)[0]
    except ValueError:
        return ""


def resolve_hunks(
    change: Change, sources: dict[str, Source]
) -> list[tuple[Source, int, Hunk]]:
    """The change's hunks in review order as (source, 0-based index, hunk).
    Ids that no longer resolve are skipped rather than crashing the viewer."""
    out = []
    for hid in change.hunk_ids:
        try:
            sid, idx = split_hunk_id(hid)
        except ValueError:
            continue
        src = sources.get(sid)
        if src and idx < len(src.hunks):
            out.append((src, idx, src.hunks[idx]))
    return out
