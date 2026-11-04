import pytest

from overseer.editor import build_command, is_gui, resolve_editor


def test_editor_beats_visual_and_visual_is_fallback():
    assert resolve_editor({"EDITOR": "vim", "VISUAL": "code"}) == ["vim"]
    assert resolve_editor({"VISUAL": "code -w"}) == ["code", "-w"]
    assert resolve_editor({"EDITOR": ""}) is None
    assert resolve_editor({}) is None
    assert resolve_editor({"EDITOR": "'unterminated"}) is None


@pytest.mark.parametrize(
    "editor, expected",
    [
        (["vim"], ["vim", "+7", "f.py"]),
        (["/usr/bin/nvim", "-R"], ["/usr/bin/nvim", "-R", "+7", "f.py"]),
        (["hx"], ["hx", "f.py:7"]),
        (["code", "-w"], ["code", "-w", "--goto", "f.py:7"]),
        (["someeditor"], ["someeditor", "f.py"]),
    ],
)
def test_line_syntax(editor, expected):
    assert build_command(editor, "f.py", 7) == expected


def test_no_line_and_option_like_paths():
    assert build_command(["vim"], "f.py", None) == ["vim", "f.py"]
    assert build_command(["vim"], "--cmd=evil", 3) == ["vim", "+3", "./--cmd=evil"]


def test_gui_detection():
    assert is_gui(["code", "-w"]) and is_gui(["/usr/bin/subl"])
    assert not is_gui(["vim"]) and not is_gui(["hx"])
