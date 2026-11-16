"""Textual widgets for the panel: StepView draws one subagent's steps, SubagentTree lists the subagents."""

import re
from typing import Optional

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.geometry import Size
from textual.markup import escape
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Tree

from .transcript import col_slice, col_width
from .viewstate import Frame

# State/style name -> color, and the names drawn bold.
_COLORS = {"working": "yellow", "running": "yellow", "done": "green", "error": "red", "failed": "red",
           "warn": "red", "quiet": "magenta", "dim": "magenta", "waiting": "blue", "msg": "cyan", "cmd": "cyan"}
_BOLD = ("cmd", "msg", "running", "step", "warn")
STYLES = {name: Style(color=_COLORS.get(name), bold=name in _BOLD or None) for name in set(_COLORS) | set(_BOLD)}
_EMPTY_FRAME = Frame([], 0, 0, 0)


class StepView(ScrollView, can_focus=True, inherit_bindings=False):
    """Line API view of one subagent's steps; keys arrive through action(), not BINDINGS."""

    BINDINGS = []
    NO_SUBAGENTS = "No subagents yet."
    NO_CONVERSATION = "No conversation found for this pane."
    DEFAULT_CSS = """
    StepView {
        border: round $primary;
        overflow-x: hidden;
        scrollbar-gutter: stable;
    }
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.row = None
        self.items = []
        self.state = None
        self.empty = self.NO_SUBAGENTS
        self.frame = _EMPTY_FRAME
        self._syncing = False

    def _body(self):
        region = self.scrollable_content_region
        return region.width, region.height

    def show(self, row: Optional[dict], items: list, state, empty: str = NO_SUBAGENTS) -> None:
        """Shows `items` of `row` (None: the `empty` text) and scrolls to the state's frame."""
        self.row, self.items, self.state, self.empty = row, items, state, empty
        width, height = self._body()
        self.frame = state.frame(items, width, height) if row is not None else _EMPTY_FRAME
        self.virtual_size = Size(width, len(self.frame.lines))
        self._syncing = True
        try:
            self.scroll_to(y=self.frame.top, animate=False, force=True)
        finally:
            self._syncing = False
        self.border_title = escape(self.header_text())  # border_title is markup: keep "[done]" literal
        self.refresh()

    def header_text(self) -> str:
        if self.row is None or self.state is None:
            return ""
        model = self.row.get("model")
        badge = "[%s · %s]" % (self.row["state"], model) if model and model != "-" else "[%s]" % self.row["state"]
        return "%s %s  %s" % (self.row["role"], badge, "⏬ follow" if self.state.follow else "⏸ paused")

    def render_line(self, y: int) -> Strip:
        width = self.scrollable_content_region.width
        base = self.rich_style
        if self.row is None:
            text = " " + self.empty if y == 0 else ""
            style = base + STYLES["dim"] if self.empty == self.NO_CONVERSATION else base
            return Strip([Segment(text, style)]).adjust_cell_length(width, base)
        i = self.scroll_offset.y + y
        if not 0 <= i < len(self.frame.lines):
            return Strip.blank(width, base)
        n, name, text = self.frame.lines[i]
        style = base + STYLES.get(name, Style())
        if n == self.frame.cur and i in self.frame.heads:
            style += Style(reverse=True)
        return Strip([Segment(text, style)]).adjust_cell_length(width, style)

    def action(self, name: str) -> None:
        """Applies a cursor/fold action (viewstate.ViewState.move) and redraws."""
        if self.state is None or self.row is None:
            return
        self.state.move(name, self.items, self.frame, self._body()[1])
        self.show(self.row, self.items, self.state, self.empty)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Wheel / scrollbar scrolls go through ViewState.scroll_by so follow and anchor stay in sync."""
        super().watch_scroll_y(old_value, new_value)
        if self._syncing or self.state is None or self.row is None or round(old_value) == round(new_value):
            return
        f = self.frame
        self.state.scroll_by(round(new_value) - self.state.top, len(f.lines), self._body()[1])
        self.frame = Frame(f.lines, f.cur, f.first, self.state.top, f.items, f.heads)
        self.border_title = escape(self.header_text())  # border_title is markup: keep "[done]" literal

    def on_click(self, event) -> None:
        offset = event.get_content_offset(self)
        if offset is None or self.state is None or self.row is None:
            return
        self.state.click(round(self.scroll_y) + offset.y, self.frame)
        self.show(self.row, self.items, self.state, self.empty)

    def on_resize(self, event) -> None:
        if self.state is not None:
            self.show(self.row, self.items, self.state, self.empty)

    def _size_updated(self, size, virtual_size, container_size, layout=True):
        """A resize clamps scroll_y (e.g. the first show() ran at size 0): not a user scroll, so it
        must not pause follow; on_resize then re-shows the frame for the new size."""
        self._syncing = True
        try:
            return super()._size_updated(size, virtual_size, container_size, layout)
        finally:
            self._syncing = False


# List column widths in cells; "role" counts from the tree's left edge (guides + fold icon + role).
COLS = {"role": 24, "state": 8, "model": 22, "age": 5}
MODEL_MIN, TEXT_MIN = 8, 10  # narrowest Model / Last step cell worth showing
_FIXED = COLS["role"] + 1 + COLS["state"] + 1 + COLS["age"]  # role, state and age are always shown
_HEADER = {"role": "Subagent", "state": "State", "model": "Model", "age": "Age", "text": "Last step"}


def visible_cols(width: int) -> tuple:
    """Columns shown at `width`: Last step needs a full Model and TEXT_MIN cells, Model needs MODEL_MIN."""
    if width >= _FIXED + 1 + COLS["model"] + 1 + TEXT_MIN:
        drop = ()
    elif width >= _FIXED + 1 + MODEL_MIN:
        drop = ("text",)
    else:
        drop = ("text", "model")
    return tuple(c for c in ("role", "state", "model", "age", "text") if c not in drop)


def _short_age(age: str) -> str:
    """discovery.age() text that fits the Age cell: 12h05m -> 12h, 7664h36m -> 319d."""
    m = re.match(r"(\d+)h\d+m$", age)
    if col_width(age) <= COLS["age"] or not m:
        return age
    hours = int(m.group(1))
    return "%dh" % hours if hours < 100 else "%dd" % (hours // 24)


def _fit(text: str, width: int, right: bool = False) -> str:
    """`text` cut to `width` cells (ending in "…" when cut) and padded to exactly `width` cells."""
    if col_width(text) > width:
        text = col_slice(text, width - 1)[0] + "…" if width > 0 else ""
    pad = " " * (width - col_width(text))
    return pad + text if right else text + pad


def _columns(cells: dict, role_width: int, total_width: int) -> Text:
    """Lays out `cells` (column -> (text, style)) after a `role_width` role cell, within the
    `total_width - (COLS["role"] - role_width)` cells the label has in a `total_width` tree."""
    left = total_width - (COLS["role"] - role_width)
    widths = dict(COLS, role=role_width, model=min(COLS["model"], total_width - _FIXED - 1))
    label = Text()
    for col in visible_cols(total_width):
        text, style = cells.get(col, ("", None))
        if col == "text":
            if col_width(text) > left - 1:
                text = _fit(text, left - 1)
        else:
            text = _fit(text, widths[col], right=col == "age")
        for piece, piece_style in (((" ", None),) if col != "role" else ()) + ((text, style),):
            head, rest = col_slice(piece, left)
            label.append(head, piece_style)
            left -= col_width(head)
            if rest:
                return label
    return label


def row_columns(row: dict, label_width: int, total_width: int) -> Text:
    """Tree label of a subagent row: role cut to `label_width` cells, then colored state, model, age and
    last step lined up with header_columns(total_width) (the label starts COLS["role"] - label_width in)."""
    model = row.get("model")
    cells = {"role": (row["role"], None), "state": (row["state"], STYLES.get(row["state"])),
             "model": (model if model and model != "-" else "", None),
             "age": (_short_age(row.get("age") or ""), None), "text": (row.get("text") or "", None)}
    return _columns(cells, label_width, total_width)


def header_columns(total_width: int) -> Text:
    """Column titles for a `total_width` tree, starting at the tree's left edge."""
    return _columns({col: (title, None) for col, title in _HEADER.items()}, COLS["role"], total_width)


class SubagentTree(Tree, inherit_bindings=False):
    """Working/Completed subagent tree updated in place by id; keys arrive through action(), not BINDINGS."""

    BINDINGS = []
    DEFAULT_CSS = """
    SubagentTree { overflow-x: hidden; scrollbar-gutter: stable; }
    """
    _CURSOR = {"up": "action_cursor_up", "down": "action_cursor_down", "page_up": "action_page_up",
               "page_down": "action_page_down", "first": "action_scroll_home", "last": "action_scroll_end"}

    class Selected(Message):
        """The highlighted node changed to subagent `id`."""

        def __init__(self, id: str) -> None:
            super().__init__()
            self.id = id

    class OpenLeaf(Message):
        """`open` on subagent `id`, which has no children."""

        def __init__(self, id: str) -> None:
            super().__init__()
            self.id = id

    def __init__(self, **kw):
        super().__init__("subagents", **kw)
        self.show_root = False
        self.auto_expand = False  # a click highlights; folding is the toggle icon or `open`
        self.expanded_ids = set()
        self._known_ids = set()
        self._group_nodes = {}  # group name -> node
        self._group_expanded = {}  # group name -> fold state when its node was last removed
        self._by_id = {}  # subagent id -> node
        self._rows = {}  # subagent id -> its row from the last update_groups()
        self._cursor_key = None  # subagent id or group name under the cursor
        self._announced = None

    def reset(self) -> None:
        """Drops every node and the remembered expansion and cursor (a new root conversation)."""
        self.clear()
        self.expanded_ids, self._known_ids = set(), set()
        self._group_nodes, self._group_expanded, self._by_id, self._rows = {}, {}, {}, {}
        self._cursor_key = self._announced = None

    def selected_id(self) -> Optional[str]:
        node = self.cursor_node
        if node is None or node.data is None or self._by_id.get(node.data) is not node:
            return None
        return node.data

    def update_groups(self, groups) -> None:
        """Updates the tree from discovery.Tree.rows() groups, keeping expansion and selection by id.
        A group without rows has no node; it comes back, with its old fold state, when rows arrive."""
        order = self._remember()
        names = [name for name, group in groups if group]
        rows, kids = {}, {}  # kids: parent id or group name -> child ids in display order
        for name, group in groups:
            ids = {r["id"] for r in group}
            for r in group:
                rows[r["id"]] = r
                kids.setdefault(r["parent"] if r.get("parent") in ids else name, []).append(r["id"])
        want = {cid: key for key, ids in kids.items() for cid in ids}
        for cid in list(self._by_id):
            node = self._by_id.get(cid)
            if node is not None and self._key(node.parent) != want.get(cid):
                self._drop(node)  # gone, or moved to another parent/group: rebuilt below
        for name in [name for name in self._group_nodes if name not in names]:
            node = self._group_nodes.pop(name)
            self._group_expanded[name] = node.is_expanded
            self._drop(node)
        for i, name in enumerate(names):
            if name not in self._group_nodes:
                before = i if i < len(self.root.children) else None
                expand = self._group_expanded.get(name, True)
                self._group_nodes[name] = self.root.add(name, before=before, expand=expand)
        width = self.scrollable_content_region.width or 200
        for key in names + list(rows):
            parent = self._by_id[key] if key in rows else self._group_nodes[key]
            for i, cid in enumerate(kids.get(key, ())):
                node = self._by_id.get(cid)
                if node is not None and (i >= len(parent.children) or parent.children[i] is not node):
                    self._drop(node)
                    node = None
                label = self._label(parent, rows[cid], width)
                if node is None:
                    expand = cid in self.expanded_ids or cid not in self._known_ids
                    before = i if i < len(parent.children) else None
                    node = parent.add(label, cid, before=before, expand=expand)
                    self._by_id[cid] = node
                    self._known_ids.add(cid)
                elif node.label != label:
                    node.set_label(label)
                if node.allow_expand != (cid in kids):
                    node.allow_expand = cid in kids
        self._rows = rows
        self._restore(order)

    def _label(self, parent, row: dict, width: int) -> Text:
        """Label of `row` under `parent`: the role is cut to what the guides and fold icon leave of
        COLS["role"], so the columns after it line up at every depth."""
        level, p = 0, parent
        while p is not None and p is not self.root:
            level, p = level + 1, p.parent
        role_width = COLS["role"] - self.guide_depth * level - col_width(self.ICON_NODE)
        return row_columns(row, max(1, role_width), width)

    def render_label(self, node, base_style: Style, style: Style) -> Text:
        """A subagent without children gets two blank cells where the fold icon would be."""
        label = super().render_label(node, base_style, style)
        if node.data is not None and not node.allow_expand:
            label = Text.assemble((" " * col_width(self.ICON_NODE), base_style), label)
        return label

    def on_resize(self, event) -> None:
        """Lays the labels out again for the new width (columns appear, disappear or grow)."""
        width = self.scrollable_content_region.width
        if not width:
            return
        for cid, node in self._by_id.items():
            label = self._label(node.parent, self._rows[cid], width)
            if node.label != label:
                node.set_label(label)

    def _key(self, node) -> str:
        return str(node.label) if node.data is None else node.data

    def _remember(self) -> list:
        """Saves expansion and the cursor key; returns the visible keys in display order."""
        for cid, node in self._by_id.items():
            (self.expanded_ids.add if node.is_expanded else self.expanded_ids.discard)(cid)
        if self.cursor_node is not None:
            self._cursor_key = self._key(self.cursor_node)
        return [self._key(line.node) for line in self._tree_lines]

    def _drop(self, node) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            self._by_id.pop(n.data, None)
            stack.extend(n.children)
        node.remove()

    def _restore(self, order: list) -> None:
        """Puts the cursor back on its key, else the nearest surviving subagent, else the first one."""
        key = self._cursor_key
        node = self._by_id.get(key) or self._group_nodes.get(key)
        if node is None and key in order:
            i = order.index(key)
            node = next((self._by_id[k] for k in order[i + 1:] + order[:i][::-1] if k in self._by_id), None)
        if node is None:  # first update, or nothing left nearby: first visible subagent
            node = next((line.node for line in self._tree_lines if line.node.data is not None), None)
        self._cursor_key = None if node is None else self._key(node)
        if node is not None:
            self._move(node)
        self._announce()

    def _move(self, node) -> None:
        """Moves the cursor to `node`, or to its outermost collapsed ancestor if it is hidden."""
        p = node.parent
        while p is not None and p is not self.root:
            if not p.is_expanded:
                node = p
            p = p.parent
        line = next(i for i, tree_line in enumerate(self._tree_lines) if tree_line.node is node)
        if self.cursor_node is not node or self.cursor_line != line:
            self.cursor_line = line  # always_update: the watcher re-reads the node on an unchanged line
            self.scroll_to_line(line, animate=False)

    def _announce(self) -> None:
        sid = self.selected_id()
        if sid is not None and sid != self._announced:
            self._announced = sid
            self.post_message(self.Selected(sid))

    def watch_cursor_line(self, previous_line: int, line: int) -> None:
        super().watch_cursor_line(previous_line, line)
        self._announce()  # here, not on NodeHighlighted, so Selected is posted before a later OpenLeaf

    def action(self, name: str) -> None:
        """up/down/page_*/first/last move; open toggles or opens a leaf; back collapses or goes up;
        fold_all collapses every subagent with children, or expands them all if none is expanded."""
        if name in self._CURSOR:
            getattr(self, self._CURSOR[name])()
            return
        node = self.cursor_node
        if node is None:
            return
        if name == "fold_all":
            parents = [n for n in self._by_id.values() if n.allow_expand]
            expand = not any(n.is_expanded for n in parents)
            for n in parents:
                (n.expand if expand else n.collapse)()
            self._move(node)
        elif name == "open":
            if node.allow_expand:
                node.toggle()
            elif node.data is not None:
                self.post_message(self.OpenLeaf(node.data))
        elif name == "back":
            if node.allow_expand and node.is_expanded:
                node.collapse()
            elif node.parent is not None and node.parent is not self.root:
                self._move(node.parent)
