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
from textual.widgets import Static, Tree

from . import config
from .themes import FALLBACK_STYLES
from .transcript import char_width, col_slice, col_width
from .viewstate import Frame

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
        self.state_styles = FALLBACK_STYLES
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
            style = base + self.state_styles["dim"] if self.empty == self.NO_CONVERSATION else base
            return Strip([Segment(text, style)]).adjust_cell_length(width, base)
        i = self.scroll_offset.y + y
        if not 0 <= i < len(self.frame.lines):
            return Strip.blank(width, base)
        n, name, text = self.frame.lines[i]
        style = base + self.state_styles.get(name, Style())
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
DEFAULT_COLS = dict(config.COLUMNS)
MODEL_MIN, TEXT_MIN = 8, 10  # narrowest Model / Last step cell worth showing
MARQUEE_STEP_S = 0.15
MARQUEE_PAUSE_S = 1.5
MARQUEE_PAUSE_TICKS = round(MARQUEE_PAUSE_S / MARQUEE_STEP_S)
_HEADER = {"role": "Subagent", "state": "State", "model": "Model", "age": "Age", "text": "Last step"}


def _fixed(cols: dict) -> int:
    return cols["role"] + 1 + cols["state"] + 1 + cols["age"]


def model_min(cols: dict) -> int:
    return min(MODEL_MIN, cols["model"])


def visible_cols(width: int, cols: Optional[dict] = None) -> tuple:
    """Columns shown at `width`: Last step needs a full Model and TEXT_MIN cells, Model needs model_min."""
    c = DEFAULT_COLS if cols is None else cols
    if width >= _fixed(c) + 1 + c["model"] + 1 + TEXT_MIN:
        drop = ()
    elif width >= _fixed(c) + 1 + model_min(c):
        drop = ("text",)
    else:
        drop = ("text", "model")
    return tuple(col for col in ("role", "state", "model", "age", "text") if col not in drop)


def _widths(cols: dict, role_width: int, total_width: int) -> dict:
    return dict(cols, role=role_width, model=min(cols["model"], total_width - _fixed(cols) - 1))


def _short_age(age: str, width: int) -> str:
    """discovery.age() text that fits `width` cells: 12h05m -> 12h, 7664h36m -> 319d."""
    m = re.match(r"(\d+)h\d+m$", age)
    if col_width(age) <= width or not m:
        return age
    hours = int(m.group(1))
    return "%dh" % hours if hours < 100 else "%dd" % (hours // 24)


def _fit(text: str, width: int, right: bool = False) -> str:
    """`text` cut to `width` cells (ending in "…" when cut) and padded to exactly `width` cells."""
    if col_width(text) > width:
        text = col_slice(text, width - 1)[0] + "…" if width > 0 else ""
    pad = " " * (width - col_width(text))
    return pad + text if right else text + pad


def text_width(total_width: int, cols: Optional[dict] = None) -> int:
    """Cell width available for the Last step column at `total_width` (0 when hidden)."""
    c = DEFAULT_COLS if cols is None else cols
    if "text" not in visible_cols(total_width, c):
        return 0
    return max(0, total_width - (_fixed(c) + 1 + c["model"] + 1))


def marquee_span(text: str, width: int) -> int:
    """Maximum cell offset before the end of `text` is visible in `width` cells (0 if it fits)."""
    if width <= 0:
        return 0
    return max(0, col_width(text) - width)


def marquee_window(text: str, width: int, offset: int) -> str:
    """`width`-cell slice of `text` starting at cell `offset`, padding if a wide char is clipped."""
    if width <= 0:
        return ""
    offset = max(0, min(offset, marquee_span(text, width)))
    skipped, rest = col_slice(text, offset)
    pad_left = 0
    if col_width(skipped) < offset and rest:
        w0 = char_width(rest[0])
        pad_left = max(0, col_width(skipped) + w0 - offset)
        i = 1
        while i < len(rest) and char_width(rest[i]) == 0:
            i += 1
        rest = rest[i:]
    vis, _ = col_slice(rest, max(0, width - pad_left))
    pad_right = max(0, width - pad_left - col_width(vis))
    return " " * pad_left + vis + " " * pad_right


def _columns(cells: dict, role_width: int, total_width: int, cols: Optional[dict] = None,
             text_offset: int = 0) -> Text:
    """Lays out `cells` (column -> (text, style)) after a `role_width` role cell, within the
    `total_width - (cols["role"] - role_width)` cells the label has in a `total_width` tree."""
    c = DEFAULT_COLS if cols is None else cols
    left = total_width - (c["role"] - role_width)
    widths = _widths(c, role_width, total_width)
    label = Text()
    for col in visible_cols(total_width, c):
        text, style = cells.get(col, ("", None))
        if col == "text":
            if col_width(text) > left - 1:
                text = marquee_window(text, left - 1, text_offset) if text_offset > 0 else _fit(text, left - 1)
        else:
            text = _fit(text, widths[col], right=col == "age")
        for piece, piece_style in (((" ", None),) if col != "role" else ()) + ((text, style),):
            head, rest = col_slice(piece, left)
            label.append(head, piece_style)
            left -= col_width(head)
            if rest:
                return label
    return label


def row_columns(row: dict, label_width: int, total_width: int, state_styles=None,
                cols: Optional[dict] = None, text_offset: int = 0) -> Text:
    """Tree label of a subagent row: role cut to `label_width` cells, then colored state, model, age and
    last step lined up with header_columns(total_width, cols)."""
    c = DEFAULT_COLS if cols is None else cols
    styles = FALLBACK_STYLES if state_styles is None else state_styles
    model = row.get("model")
    cells = {"role": (row["role"], None), "state": (row["state"], styles.get(row["state"])),
             "model": (model if model and model != "-" else "", None),
             "age": (_short_age(row.get("age") or "", c["age"]), None), "text": (row.get("text") or "", None)}
    return _columns(cells, label_width, total_width, c, text_offset=text_offset)


def header_columns(total_width: int, cols: Optional[dict] = None) -> Text:
    """Column titles for a `total_width` tree, starting at the tree's left edge."""
    c = DEFAULT_COLS if cols is None else cols
    return _columns({col: (title, None) for col, title in _HEADER.items()}, c["role"], total_width, c)


def column_borders(total_width: int, cols: Optional[dict] = None) -> dict[str, int]:
    """x coordinate of the separator cell right of each visible non-last column."""
    c = DEFAULT_COLS if cols is None else cols
    widths = _widths(c, c["role"], total_width)
    vis = visible_cols(total_width, c)
    borders = {}
    x = 0
    for i, col in enumerate(vis):
        if i > 0:
            x += 1
        if col != "text":
            x += widths[col]
            if i < len(vis) - 1:
                borders[col] = x
    return borders


class ColumnsChanged(Message):
    """User dragged a column border in ColumnHeader to resize columns."""

    def __init__(self, cols: dict) -> None:
        super().__init__()
        self.cols = cols


class ColumnHeader(Static, can_focus=False):
    """List column header whose borders can be hovered and dragged to resize columns."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.total_width = 0
        self.cols = dict(DEFAULT_COLS)
        self.borders: dict[str, int] = {}
        self._drag: Optional[tuple[str, int]] = None
        self._hover: Optional[str] = None

    def show(self, total_width: int, cols: dict) -> None:
        self.total_width = total_width
        self.cols = dict(cols)
        self.borders = column_borders(total_width, self.cols)
        self._render_header()

    def _render_header(self) -> None:
        text = header_columns(self.total_width, self.cols)
        if self._hover and self._hover in self.borders:
            x = self.borders[self._hover]
            plain = text.plain
            if 0 <= x < len(plain):
                styled = Text()
                styled.append(plain[:x])
                styled.append("┃", Style(reverse=True))
                styled.append(plain[x + 1:])
                text = styled
        self.update(text)

    def _nearest_border(self, x: int) -> Optional[tuple[str, int]]:
        return min(
            ((col, bx) for col, bx in self.borders.items() if abs(x - bx) <= 1),
            key=lambda item: abs(x - item[1]),
            default=None,
        )

    def on_mouse_down(self, event) -> None:
        hit = self._nearest_border(event.x)
        if hit is None:
            return
        col, bx = hit
        widths = _widths(self.cols, self.cols["role"], self.total_width)
        self.capture_mouse()
        self._drag = (col, bx - widths[col])
        self._hover = col
        self._render_header()
        event.stop()

    def on_mouse_move(self, event) -> None:
        if self._drag is not None:
            col, start = self._drag
            lo, hi = config.COLUMN_LIMITS[col]
            new = max(lo, min(hi, event.x - start))
            if new != self.cols[col]:
                new_cols = dict(self.cols, **{col: new})
                self.cols = new_cols
                self.borders = column_borders(self.total_width, new_cols)
                self._hover = col
                self._render_header()
                self.post_message(ColumnsChanged(new_cols))
            return
        hit = self._nearest_border(event.x)
        hover = hit[0] if hit is not None else None
        if hover != self._hover:
            self._hover = hover
            self._render_header()

    def on_mouse_up(self, event) -> None:
        if self._drag is not None:
            self.release_mouse()
            self._drag = None
            hit = self._nearest_border(event.x)
            self._hover = hit[0] if hit is not None else None
            self._render_header()

    def cancel_drag(self) -> None:
        if self._drag is not None:
            self.release_mouse()
        self._drag = None
        self._hover = None
        self._render_header()

    def on_mouse_release(self, event) -> None:
        self._drag = None
        self._hover = None
        self._render_header()

    def on_leave(self, event) -> None:
        self.cancel_drag()


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

    def __init__(self, state_styles=None, cols=None, **kw):
        super().__init__("subagents", **kw)
        self.state_styles = FALLBACK_STYLES if state_styles is None else state_styles
        self.cols = dict(DEFAULT_COLS if cols is None else cols)
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
        self._updating = False
        self._marquee_id = None
        self._marquee_text = ""
        self._marquee_width = 0
        self._marquee_cols = dict(self.cols)
        self._marquee_offset = 0
        self._marquee_pause = MARQUEE_PAUSE_TICKS

    def _set_marquee_cursor(self, sid: Optional[str], width: int) -> None:
        self._marquee_id = sid
        self._marquee_text = (self._rows[sid].get("text") or "") if sid in self._rows else ""
        self._marquee_width = width
        self._marquee_cols = dict(self.cols)
        self._marquee_offset = 0
        self._marquee_pause = MARQUEE_PAUSE_TICKS

    def _relabel_node(self, cid: str, width: int, text_offset: int = 0) -> None:
        node = self._by_id.get(cid)
        row = self._rows.get(cid)
        if node is None or row is None or not width:
            return
        label = self._label(node.parent, row, width, text_offset=text_offset)
        if node.label != label:
            node.set_label(label)

    def reset_marquee(self) -> None:
        """Resets the marquee to offset 0 and restores the previously scrolled row's label."""
        if self._marquee_offset > 0 and self._marquee_id is not None:
            self._relabel_node(self._marquee_id, self.scrollable_content_region.width)
        self._set_marquee_cursor(self.selected_id(), self.scrollable_content_region.width)

    def tick_marquee(self) -> bool:
        """Advances the cursor row's Last step marquee by one tick; returns True if relabeled."""
        sid = self.selected_id()
        if sid is None or sid not in self._by_id or sid not in self._rows:
            return False
        width = self.scrollable_content_region.width
        if not width:
            return False
        text = self._rows[sid].get("text") or ""
        span = marquee_span(text, text_width(width, self.cols))
        if span <= 0:
            return False
        if (sid != self._marquee_id or text != self._marquee_text
                or width != self._marquee_width or self.cols != self._marquee_cols):
            self._set_marquee_cursor(sid, width)
            return False
        self._marquee_pause -= 1
        if self._marquee_pause > 0:
            return False
        if self._marquee_offset < span:
            self._marquee_offset += 1
            if self._marquee_offset == span:
                self._marquee_pause = MARQUEE_PAUSE_TICKS
        else:
            self._marquee_offset = 0
            self._marquee_pause = MARQUEE_PAUSE_TICKS
        node = self._by_id[sid]
        label = self._label(node.parent, self._rows[sid], width, text_offset=self._marquee_offset)
        if node.label != label:
            node.set_label(label)
            return True
        return False

    def reset(self) -> None:
        """Drops every node and the remembered expansion and cursor (a new root conversation)."""
        self.clear()
        self.expanded_ids, self._known_ids = set(), set()
        self._group_nodes, self._group_expanded, self._by_id, self._rows = {}, {}, {}, {}
        self._cursor_key = self._announced = None
        self._set_marquee_cursor(None, 0)

    def selected_id(self) -> Optional[str]:
        node = self.cursor_node
        if node is None or node.data is None or self._by_id.get(node.data) is not node:
            return None
        return node.data

    def update_groups(self, groups) -> None:
        """Updates the tree from discovery.Tree.rows() groups, keeping expansion and selection by id.
        A group without rows has no node; it comes back, with its old fold state, when rows arrive."""
        self._updating = True
        try:
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
            same_marquee = (
                self._marquee_id is not None
                and self._marquee_id in rows
                and (rows[self._marquee_id].get("text") or "") == self._marquee_text
                and width == self._marquee_width
                and self.cols == self._marquee_cols
            )
            for key in names + list(rows):
                parent = self._by_id[key] if key in rows else self._group_nodes[key]
                for i, cid in enumerate(kids.get(key, ())):
                    node = self._by_id.get(cid)
                    if node is not None and (i >= len(parent.children) or parent.children[i] is not node):
                        self._drop(node)
                        node = None
                    offset = self._marquee_offset if same_marquee and cid == self._marquee_id else 0
                    label = self._label(parent, rows[cid], width, text_offset=offset)
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
            cur_id = self.selected_id()
            if not (same_marquee and cur_id == self._marquee_id):
                if same_marquee and self._marquee_offset > 0:
                    self._relabel_node(self._marquee_id, width)
                self._set_marquee_cursor(cur_id, width)
        finally:
            self._updating = False

    def _label(self, parent, row: dict, width: int, text_offset: int = 0) -> Text:
        """Label of `row` under `parent`: the role is cut to what the guides and fold icon leave of
        self.cols["role"], so the columns after it line up at every depth."""
        level, p = 0, parent
        while p is not None and p is not self.root:
            level, p = level + 1, p.parent
        role_width = self.cols["role"] - self.guide_depth * level - col_width(self.ICON_NODE)
        return row_columns(row, max(1, role_width), width, self.state_styles, cols=self.cols,
                           text_offset=text_offset)

    def render_label(self, node, base_style: Style, style: Style) -> Text:
        """A subagent without children gets two blank cells where the fold icon would be."""
        label = super().render_label(node, base_style, style)
        if node.data is not None and not node.allow_expand:
            label = Text.assemble((" " * col_width(self.ICON_NODE), base_style), label)
        return label

    def relabel(self) -> None:
        """Lays the labels out again at the current width (on resize or theme change)."""
        width = self.scrollable_content_region.width
        if not width:
            return
        if width != self._marquee_width or self.cols != self._marquee_cols:
            self._set_marquee_cursor(self.selected_id(), width)
        for cid, node in self._by_id.items():
            offset = self._marquee_offset if cid == self._marquee_id else 0
            label = self._label(node.parent, self._rows[cid], width, text_offset=offset)
            if node.label != label:
                node.set_label(label)

    def on_resize(self, event) -> None:
        """Lays the labels out again for the new width (columns appear, disappear or grow)."""
        self.relabel()

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
        if not hasattr(self, "_by_id"):
            return
        self._announce()  # here, not on NodeHighlighted, so Selected is posted before a later OpenLeaf
        if not self._updating and self.selected_id() != self._marquee_id:
            self.reset_marquee()

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
