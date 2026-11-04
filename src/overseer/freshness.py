"""What changed since the reviewer last said "I've reviewed this".

A snapshot records content hashes, not ids or positions: a re-plan renumbers
changes, and unrelated edits shift line numbers, but neither should make
already-reviewed code look new. A hunk's identity is its path plus its diff
lines (the `@@` header is excluded because it carries line numbers).

Nothing here may import textual.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from overseer.model import Change, Hunk, Source
from overseer.store import resolve_hunks


def hunk_hash(hunk: Hunk) -> str:
    text = hunk.path + "\n" + "\n".join(hunk.lines)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def meta_hash(change: Change) -> str:
    """The reviewer-facing text that is not diff: title, risk and explanations."""
    data = [
        change.title,
        change.risk,
        change.risk_reason,
        sorted(change.answers.items()),
    ]
    return hashlib.sha1(json.dumps(data).encode()).hexdigest()[:12]


def take_snapshot(changes: list[Change], sources: dict[str, Source]) -> dict:
    hunks: set[str] = set()
    for c in changes:
        hunks.update(hunk_hash(h) for _, _, h in resolve_hunks(c, sources))
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "hunks": sorted(hunks),
        "changes": {c.id: meta_hash(c) for c in changes},
    }


def when(at: str) -> str:
    """The snapshot time in the viewer's local zone, e.g. `Nov 04 04:50`."""
    try:
        return datetime.fromisoformat(at).astimezone().strftime("%b %d %H:%M")
    except ValueError:
        return ""


@dataclass
class Freshness:
    kind: str = ""  # "new" | "changed" | "updated" | "" (unchanged or no baseline)
    new_hunks: list[int] = field(default_factory=list)  # 1-based, within the change
    total: int = 0
    since: str = ""  # when the baseline was taken (ISO), for display

    @property
    def tag(self) -> str:
        if self.kind == "changed":
            return f"CHANGED +{len(self.new_hunks)}"
        return self.kind.upper()


def freshness(
    change: Change, sources: dict[str, Source], snapshot: dict | None
) -> Freshness:
    """`new`: no hunk was in the snapshot. `changed`: some were not. `updated`:
    the code is all familiar but the explanation, title or risk differs."""
    if not snapshot:
        return Freshness()
    seen = set(snapshot.get("hunks", []))
    hunks = resolve_hunks(change, sources)
    fresh = [i + 1 for i, (_, _, h) in enumerate(hunks) if hunk_hash(h) not in seen]
    out = Freshness(new_hunks=fresh, total=len(hunks), since=snapshot.get("at", ""))
    if hunks and len(fresh) == len(hunks):
        out.kind = "new"
    elif fresh:
        out.kind = "changed"
    elif snapshot.get("changes", {}).get(change.id, meta_hash(change)) != meta_hash(
        change
    ):
        out.kind = "updated"
    return out
