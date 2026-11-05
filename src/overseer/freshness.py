"""What changed since the reviewer last said "I've reviewed this".

A baseline records content hashes, not ids or positions: a re-plan renumbers
changes, and unrelated edits shift line numbers, but none of that should make an
already-reviewed delta look new. The unit hashed is the delta's declared content
(kind, and the path and code at each end).

Nothing here may import textual.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from overseer.model import Change, Delta


def delta_hash(d: Delta) -> str:
    """What the delta says, not where it sits or what it is called: its kind and
    the code at each end (path included, line numbers not, since unrelated edits
    shift them)."""
    ends = [(x.path, x.text) for x in (d.before, d.after) if x]
    data = [d.kind, ends, d.diff]
    return hashlib.sha1(json.dumps(data).encode()).hexdigest()[:12]


def meta_hash(change: Change) -> str:
    """The reviewer-facing text that is not diff: title, risk and answers."""
    data = [
        change.title,
        change.risk,
        change.risk_reason,
        sorted(change.answers.items()),
    ]
    return hashlib.sha1(json.dumps(data).encode()).hexdigest()[:12]


def take_baseline(changes: list[Change]) -> dict:
    seen = {delta_hash(d) for c in changes for d in c.deltas}
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "deltas": sorted(seen),
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


def freshness(change: Change, baseline: dict | None) -> Freshness:
    """`new`: the baseline knew none of the change's deltas. `changed`: some
    deltas it did not know. `updated`: the deltas are all familiar but the
    answers, title or risk differs."""
    if not baseline:
        return Freshness()
    seen = set(baseline.get("deltas", []))
    hashes = [delta_hash(d) for d in change.deltas]
    fresh = [i + 1 for i, h in enumerate(hashes) if h not in seen]
    out = Freshness(new_deltas=fresh, total=len(hashes), since=baseline.get("at", ""))
    if hashes and not any(h in seen for h in hashes):
        out.kind = "new"
    elif fresh:
        out.kind = "changed"
    elif baseline.get("changes", {}).get(change.id, meta_hash(change)) != meta_hash(
        change
    ):
        out.kind = "updated"
    return out
