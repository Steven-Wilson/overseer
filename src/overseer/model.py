"""Data contract between agent-invoked CLI commands and the TUI.

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


@dataclass
class Hunk:
    path: str
    header: str  # the `@@ -a,b +c,d @@ ...` line, or a note for non-text changes
    lines: list[str] = field(default_factory=list)  # raw diff lines, prefix included


@dataclass
class Change:
    id: str
    title: str
    message: str = ""  # full commit message
    hunks: list[Hunk] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Change:
        data = dict(data)
        data["hunks"] = [Hunk(**h) for h in data.get("hunks", [])]
        return cls(**data)
