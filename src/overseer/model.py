"""Data contract between agent-invoked CLI commands and the TUI.

Two layers:

* `Source` — raw material as it exists upstream (currently: one git commit,
  as one `FileDiff` per file). Immutable once ingested. Its *changed lines*
  (added/removed, numbered per file) are the atoms everything else points at.
* `Change` — the unit of meaning: one stateable intention, made of `Delta`s
  (possibly from several sources) and carrying the answers. A large or
  multi-purpose commit is decomposed into several `Change`s by an agent via
  `overseer-cli plan`.
* `Delta` — the atomic unit of review: some changed lines of one file, chosen
  by the agent. Not a git hunk: it can be a slice of one or span several.
* `Note` — the reviewer's feedback, attached to a delta, a change or a whole
  reviewset, for an agent to read back and act on.

Nothing here may import textual.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

QUESTIONS: dict[str, str] = {
    "mechanical": "What changed mechanically?",
    "semantic": "What changed semantically?",
    "why": "Why was the change made?",
    "impact": "What system parts are impacted?",
    "behavior": "How does it impact user-observable behavior?",
    "patterns": "Does it follow existing patterns?",
    "refactor": "Should the code be refactored?",
    "coverage": "How has test coverage been impacted?",
}


# Risk of a change to the reviewer: how much reviewer attention it needs.
RISKS: dict[str, str] = {
    "green": "very little or no risk; behavior is verifiably correct",
    "yellow": (
        "worth a look; tests verify behavior only partially, "
        "so a manual check may be warranted"
    ),
    "red": (
        "guesswork, a breaking change, only testable by mocking external "
        "services (so tests are weak or absent), or sweeping impact "
        "(many consumers, a core function)"
    ),
    "white": (
        "tests, documentation or comments only; no runtime behavior can change. "
        "Hidden by default in the TUI (press t), so it must be strictly true"
    ),
}


# Review priority, most attention first. Unrated sits between yellow and green:
# nobody has judged it, so it must not hide below changes known to be safe.
# White (tests/docs/comments only) is last and hidden by default in the TUI.
PRIORITY = ("red", "yellow", "", "green", "white")


def by_priority(changes: list[Change]) -> list[Change]:
    """Red, yellow, unrated, green, then white. The sort is stable, so within a tier the
    given order stands (ingest order, then the agent's plan order, which is the
    order it wrote them to read as a story)."""
    rank = {r: i for i, r in enumerate(PRIORITY)}
    return sorted(changes, key=lambda c: rank.get(c.risk, rank[""]))


@dataclass
class Link:
    """Pointer into the working tree that helps a reviewer judge a change."""

    path: str  # relative to the repo root (where the TUI is launched)
    line: int | None = None  # 1-based
    note: str = ""  # why it is related, a few words
    # Full sha of the checkout the agent looked at when it picked this line, so a
    # reviewer on a different checkout can open the code as the agent saw it.
    # "" = unknown (older links), opened from the working tree as before.
    rev: str = ""


def is_changed(line: str) -> bool:
    """Whether a stored diff line is a changed line (`+`, `-`, or `!` for a
    non-text change). Context lines, `@@` headers and `\\ No newline` are not."""
    return line[:1] in ("+", "-", "!")


@dataclass
class FileDiff:
    """One file's side of a commit. `lines` are the diff lines in order: git's
    `@@` headers (kept for their function-name context), context lines (leading
    space) and changed lines. A non-text change (binary, mode, empty file) is the
    single line `!(note)`. Changed lines are numbered 1.. in order of appearance."""

    path: str
    lines: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """How many changed lines there are."""
        return sum(1 for line in self.lines if is_changed(line))


@dataclass
class Source:
    id: str
    message: str  # full commit message
    files: list[FileDiff] = field(default_factory=list)
    seq: int = 0  # ingest order; drives display order of changes

    @property
    def title(self) -> str:
        return self.message.splitlines()[0] if self.message else self.id

    def file(self, path: str) -> FileDiff | None:
        return next((f for f in self.files if f.path == path), None)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Source:
        data = dict(data)
        data["files"] = [FileDiff(**f) for f in data.get("files", [])]
        return cls(**data)


def parse_lines(spec: str, count: int) -> list[int]:
    """`"3-5,9"` -> [3, 4, 5, 9], checked against a file with `count` changed
    lines. Raises ValueError saying what is wrong."""
    nums: set[int] = set()
    for part in spec.split(","):
        lo, sep, hi = part.strip().partition("-")
        if not lo.strip().isdigit() or (sep and not hi.strip().isdigit()):
            raise ValueError(f"bad line spec {spec!r}: use numbers like '3-5,9'")
        first, last = int(lo), int(hi) if sep else int(lo)
        if first < 1 or last < first or last > count:
            raise ValueError(
                f"lines {part.strip()!r} out of range (the file has {count} "
                "changed lines, numbered from 1)"
            )
        nums.update(range(first, last + 1))
    return sorted(nums)


def format_lines(numbers: Iterable[int]) -> str:
    """The canonical spec for changed-line numbers: `[3, 4, 5, 9]` -> `"3-5,9"`."""
    runs: list[list[int]] = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


@dataclass
class Delta:
    """Changed lines of one file in one commit. `lines` is a canonical spec of
    changed-line numbers (see `parse_lines`); the id is its address.

    A *move* (code relocated: extracted into a function, nested under a new `if`,
    carried to another file) is one delta with two ends: the removed lines at
    `path`/`lines` and the added lines at `to_path`/`to_lines` (same commit). The
    reviewer then sees "removed from here, added here" instead of two unrelated
    blobs, and what else changed around it stays a separate delta."""

    commit: str  # Source id
    path: str
    lines: str  # e.g. "3-5,9"
    # One sentence: what this delta does and why it sits here in the order. Shown
    # instead of the change-wide answers while stepping deltas.
    summary: str = ""
    links: list[Link] = field(default_factory=list)  # related code for this delta
    to_path: str = ""  # a move's destination file; "" = not a move
    to_lines: str = ""  # the added lines there

    @property
    def moved(self) -> bool:
        return bool(self.to_path)

    @property
    def id(self) -> str:
        base = f"{self.commit}:{self.path}#{self.lines}"
        return f"{base}>{self.to_path}#{self.to_lines}" if self.moved else base

    def to_dict(self) -> dict:
        return {"id": self.id, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> Delta:
        data = {k: v for k, v in data.items() if k != "id"}
        data["links"] = [Link(**x) for x in data.get("links", [])]
        return cls(**data)


def split_delta_id(delta_id: str) -> tuple[str, str, str, str, str]:
    """`abc1234:src/x.py#3-5,9` -> (`abc1234`, `src/x.py`, `3-5,9`, ``, ``); a
    move `abc1234:a.py#1-4>b.py#7-10` also yields its destination path and lines.
    Raises ValueError if malformed."""
    commit, colon, rest = delta_id.partition(":")
    head, gt, tail = rest.rpartition(">")
    if gt and "#" in tail:
        to_path, _, to_lines = tail.rpartition("#")
    else:
        head, to_path, to_lines = rest, "", ""
    path, hash_, spec = head.rpartition("#")
    ok = colon and hash_ and commit and path and spec
    if not ok or bool(to_path) != bool(to_lines) or (gt and not to_path):
        raise ValueError(f"malformed delta id {delta_id!r}")
    return commit, path, spec, to_path, to_lines


@dataclass
class Change:
    id: str
    title: str
    deltas: list[Delta] = field(default_factory=list)  # review order
    answers: dict[str, str] = field(default_factory=dict)
    planned: bool = False  # False = default 1:1 wrapper, not yet decomposed
    risk: str = ""  # a key of RISKS; "" = not yet rated
    risk_reason: str = ""  # required for yellow/red
    links: list[Link] = field(default_factory=list)  # related to the whole change
    position: int = 0  # index within its plan

    @property
    def delta_ids(self) -> list[str]:
        return [d.id for d in self.deltas]

    def delta(self, delta_id: str) -> Delta | None:
        return next((d for d in self.deltas if d.id == delta_id), None)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["deltas"] = [d.to_dict() for d in self.deltas]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Change:
        data = dict(data)
        data["links"] = [Link(**x) for x in data.get("links", [])]
        data["deltas"] = [Delta.from_dict(d) for d in data.get("deltas", [])]
        return cls(**data)


NOTE_KINDS = ("delta", "change", "reviewset")
NOTE_STATUSES = ("open", "addressed")


@dataclass
class Note:
    """Reviewer feedback. Written by the reviewer in the TUI; an agent reads it with
    `overseer-cli notes` and marks it addressed with `overseer-cli resolve`."""

    id: str  # n1, n2, ... within a reviewset
    kind: str  # one of NOTE_KINDS
    ref: str = ""  # delta id / change id / "" for a reviewset-level note
    text: str = ""
    created: str = ""  # ISO 8601, UTC
    status: str = "open"  # one of NOTE_STATUSES
    reply: str = ""  # what the agent did about it, set when addressed

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Note:
        return cls(**data)
