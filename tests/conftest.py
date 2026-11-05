"""Builders for declared deltas, so tests say what a delta is."""

from overseer.model import Delta, Side


def ins(commit, path, lines="1", text="x", **kw):
    return Delta("insert", commit, after=Side(path, lines, text), **kw)


def dele(commit, path, lines="1", text="x", **kw):
    return Delta("delete", commit, before=Side(path, lines, text), **kw)


def chg(commit, path, before="a", after="b", lines="1", **kw):
    return Delta(
        "change", commit, Side(path, lines, before), Side(path, lines, after), **kw
    )


def mov(commit, path, text, to_path, to_text=None, lines="1", to_lines="1", **kw):
    return Delta(
        "move",
        commit,
        Side(path, lines, text),
        Side(to_path, to_lines, text if to_text is None else to_text),
        **kw,
    )
