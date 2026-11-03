"""File-backed store under `$OVERSEER_DIR` (default `./.overseer`).

    sources/<id>.json   raw ingested material (one file per commit)
    changes/<id>.json   reviewable units referencing hunks of sources

The CLI writes, the TUI reads. Writes are atomic so a long-running TUI never
sees a partial file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from overseer.model import Change, Hunk, Source, split_hunk_id


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(os.environ.get("OVERSEER_DIR", ".overseer"))
        self.sources_dir = self.root / "sources"
        self.changes_dir = self.root / "changes"

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
        sources = self.sources() if sources is None else sources
        out = [Change.from_dict(d) for d in self._read_all(self.changes_dir)]

        def key(c: Change) -> tuple:
            seqs = [
                sources[sid].seq
                for sid in (_source_of(h) for h in c.hunk_ids)
                if sid in sources
            ]
            return (min(seqs, default=1 << 30), c.position, c.id)

        return sorted(out, key=key)


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
