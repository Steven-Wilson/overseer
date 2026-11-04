"""What changed since the reviewer last said "I've reviewed this".

A baseline records content hashes, not ids or positions: a re-plan renumbers
changes and recuts deltas, and unrelated edits shift line numbers, but none of
that should make already-reviewed code look new. The unit hashed is the changed
line (path, text, and which occurrence of that text it is within its file), so a
delta cut differently still matches what the reviewer saw.

Nothing here may import textual.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from overseer.model import Change, FileDiff, Source, is_changed
from overseer.store import Resolved, resolve_deltas


def line_hashes(file: FileDiff) -> list[str]:
    """One hash per changed line; entry `n - 1` is changed line `n`."""
    seen: Counter[str] = Counter()
    out = []
    for line in file.lines:
        if is_changed(line):
            text = f"{file.path}\n{line}\n{seen[line]}"
            seen[line] += 1
            out.append(hashlib.sha1(text.encode()).hexdigest()[:12])
    return out


def delta_hashes(r: Resolved) -> list[str]:
    hashes = line_hashes(r.file)
    out = [hashes[n - 1] for n in r.numbers]
    if r.dest is not None:  # a move: both ends count
        dest = line_hashes(r.dest)
        out += [dest[n - 1] for n in r.to_numbers]
    return out


def meta_hash(change: Change) -> str:
    """The reviewer-facing text that is not diff: title, risk and answers."""
    data = [
        change.title,
        change.risk,
        change.risk_reason,
        sorted(change.answers.items()),
    ]
    return hashlib.sha1(json.dumps(data).encode()).hexdigest()[:12]


def take_baseline(changes: list[Change], sources: dict[str, Source]) -> dict:
    lines: set[str] = set()
    for c in changes:
        for r in resolve_deltas(c, sources):
            lines.update(delta_hashes(r))
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "lines": sorted(lines),
        "changes": {c.id: meta_hash(c) for c in changes},
    }


def when(at: str) -> str:
    """The baseline time in the viewer's local zone, e.g. `Nov 04 04:50`."""
    try:
        return datetime.fromisoformat(at).astimezone().strftime("%b %d %H:%M")
    except ValueError:
        return ""


@dataclass
class Freshness:
    kind: str = ""  # "new" | "changed" | "updated" | "" (unchanged or no baseline)
    new_deltas: list[int] = field(default_factory=list)  # 1-based, within the change
    total: int = 0
    since: str = ""  # when the baseline was taken (ISO), for display

    @property
    def tag(self) -> str:
        if self.kind == "changed":
            return f"CHANGED +{len(self.new_deltas)}"
        return self.kind.upper()


def freshness(
    change: Change, sources: dict[str, Source], baseline: dict | None
) -> Freshness:
    """`new`: the baseline knew none of the change's changed lines. `changed`:
    some deltas have a line it did not know (a delta is fresh when any of its
    lines is unseen). `updated`: the code is all familiar but the answers, title
    or risk differs."""
    if not baseline:
        return Freshness()
    seen = set(baseline.get("lines", []))
    deltas = resolve_deltas(change, sources)
    hashes = [delta_hashes(r) for r in deltas]
    fresh = [i + 1 for i, hs in enumerate(hashes) if any(h not in seen for h in hs)]
    out = Freshness(new_deltas=fresh, total=len(deltas), since=baseline.get("at", ""))
    if deltas and not any(h in seen for hs in hashes for h in hs):
        out.kind = "new"
    elif fresh:
        out.kind = "changed"
    elif baseline.get("changes", {}).get(change.id, meta_hash(change)) != meta_hash(
        change
    ):
        out.kind = "updated"
    return out
