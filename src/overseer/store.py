"""File-backed store: one JSON file per change under `.overseer/changes/`.

The CLI writes, the TUI reads. Writes are atomic so a long-running TUI never
sees a partial file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from overseer.model import Change


class Store:
    def __init__(self, root: Path | None = None) -> None:
        root = root or Path(os.environ.get("OVERSEER_DIR", ".overseer"))
        self.dir = root / "changes"

    def save(self, change: Change) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{change.id}.tmp"
        tmp.write_text(json.dumps(change.to_dict(), indent=2))
        tmp.replace(self.dir / f"{change.id}.json")

    def load(self, change_id: str) -> Change | None:
        path = self.dir / f"{change_id}.json"
        if not path.exists():
            return None
        return Change.from_dict(json.loads(path.read_text()))

    def list(self) -> list[Change]:
        if not self.dir.exists():
            return []
        return [
            Change.from_dict(json.loads(p.read_text()))
            for p in sorted(self.dir.glob("*.json"))
        ]
