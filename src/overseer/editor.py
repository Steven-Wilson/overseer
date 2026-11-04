"""Open a file at a line in the user's editor. Must not import textual."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from pathlib import Path

# How each known editor takes a line number; anything else gets the bare path.
_PLUS_LINE = {"vim", "vi", "nvim", "nano", "emacs", "emacsclient", "kak", "micro"}
_COLON_LINE = {"hx", "helix", "subl", "zed", "zeditor"}
_GOTO = {"code", "code-insiders", "codium", "cursor"}
# Editors that open their own window and return immediately, so the TUI need
# not hand over the terminal.
_GUI = _COLON_LINE - {"hx", "helix"} | _GOTO | {"gedit", "kate", "mate"}


def resolve_editor(env: Mapping[str, str] = os.environ) -> list[str] | None:
    """$EDITOR, falling back to $VISUAL. May carry arguments ("code -w")."""
    raw = env.get("EDITOR") or env.get("VISUAL")
    try:
        return shlex.split(raw) if raw else None
    except ValueError:
        return None


def is_gui(editor: list[str]) -> bool:
    return Path(editor[0]).name in _GUI


def build_command(editor: list[str], path: str, line: int | None) -> list[str]:
    if path.startswith("-"):  # never let a path read as an editor option
        path = f"./{path}"
    name = Path(editor[0]).name
    if line is None:
        return [*editor, path]
    if name in _PLUS_LINE:
        return [*editor, f"+{line}", path]
    if name in _COLON_LINE:
        return [*editor, f"{path}:{line}"]
    if name in _GOTO:
        return [*editor, "--goto", f"{path}:{line}"]
    return [*editor, path]
