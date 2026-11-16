"""Plain text rendering of a subagent's view (used by --view and pager)."""

from .transcript import layout


def plain(tree, row):
    return [text for _, _, text in layout(tree.items(row), lambda it: True)]
