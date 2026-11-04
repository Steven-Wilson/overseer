"""Data contract between agent-invoked CLI commands and the TUI.

Two layers:

* `Source` — raw material as it exists upstream (currently: one git commit,
  split into `Hunk`s). Immutable once ingested; hunks are addressed by id
  (`<source id>:<1-based index>`).
* `Note` — the reviewer's feedback, attached to a hunk, a change or a whole
  changeset, for an agent to read back and act on.
* `Change` — a human-sized unit of review. It references hunks (possibly from
  several sources) and carries the answers. A large or multi-purpose commit
  is decomposed into several `Change`s by an agent via `overseer-cli plan`.

Nothing here may import textual.
"""

from __future__ import annotations

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
class Hunk:
    path: str
    header: str  # the `@@ -a,b +c,d @@ ...` line, or a note for non-text changes
    lines: list[str] = field(default_factory=list)  # raw diff lines, prefix included


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


@dataclass
class Source:
    id: str
    message: str  # full commit message
    hunks: list[Hunk] = field(default_factory=list)
    seq: int = 0  # ingest order; drives display order of changes

    @property
    def title(self) -> str:
        return self.message.splitlines()[0] if self.message else self.id

    def hunk_id(self, index: int) -> str:
        """`index` is 0-based; the id is 1-based to match what agents see."""
        return f"{self.id}:{index + 1}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Source:
        data = dict(data)
        data["hunks"] = [Hunk(**h) for h in data.get("hunks", [])]
        return cls(**data)


def split_hunk_id(hunk_id: str) -> tuple[str, int]:
    """`abc1234:3` -> (`abc1234`, 2). Raises ValueError if malformed."""
    source_id, sep, num = hunk_id.rpartition(":")
    if not sep or not source_id or not num.isdigit() or int(num) < 1:
        raise ValueError(f"malformed hunk id {hunk_id!r}")
    return source_id, int(num) - 1


@dataclass
class Change:
    id: str
    title: str
    hunk_ids: list[str] = field(default_factory=list)  # review order
    answers: dict[str, str] = field(default_factory=dict)
    planned: bool = False  # False = default 1:1 wrapper, not yet decomposed
    risk: str = ""  # a key of RISKS; "" = not yet rated
    risk_reason: str = ""  # required for yellow/red
    links: list[Link] = field(default_factory=list)  # related to the whole change
    hunk_links: dict[str, list[Link]] = field(default_factory=dict)  # by hunk id
    # One sentence per hunk id: what this hunk does and why it sits here in the
    # order. Shown instead of the change-wide answers while stepping hunks.
    hunk_summaries: dict[str, str] = field(default_factory=dict)
    position: int = 0  # index within its plan

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Change:
        data = dict(data)
        data["links"] = [Link(**x) for x in data.get("links", [])]
        data["hunk_links"] = {
            h: [Link(**x) for x in xs] for h, xs in data.get("hunk_links", {}).items()
        }
        return cls(**data)


NOTE_KINDS = ("hunk", "change", "changeset")
NOTE_STATUSES = ("open", "addressed")


@dataclass
class Note:
    """Reviewer feedback. Written by the reviewer in the TUI; an agent reads it with
    `overseer-cli notes` and marks it addressed with `overseer-cli resolve`."""

    id: str  # n1, n2, ... within a changeset
    kind: str  # one of NOTE_KINDS
    ref: str = ""  # hunk id / change id / "" for a changeset-level note
    text: str = ""
    created: str = ""  # ISO 8601, UTC
    status: str = "open"  # one of NOTE_STATUSES
    reply: str = ""  # what the agent did about it, set when addressed

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Note:
        return cls(**data)
