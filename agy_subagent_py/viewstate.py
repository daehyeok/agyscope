"""Live view state for one subagent, without any UI toolkit: folds, cursor, follow and scrolling.

frame() is the only scroll algorithm; the widget just draws frame.lines[frame.top:] and
forwards keys (move), mouse wheel (scroll_by) and clicks (click).
"""

from collections import namedtuple

from .transcript import layout


class Frame(namedtuple("Frame", "lines cur first top")):
    """(lines, cursor item index, cursor's first line, top line). `heads` = title line indices."""

    def __new__(cls, lines, cur, first, top, items=(), heads=frozenset()):
        self = super(Frame, cls).__new__(cls, lines, cur, first, top)
        self.items = list(items)
        self.heads = heads
        return self


def first_lines(lines):
    """Item index -> index of its first line."""
    out = {}
    for i, (n, _, _) in enumerate(lines):
        out.setdefault(n, i)
    return out


def title_lines(items, lines, width):
    """Line indices that belong to an item's title (wrapped titles span several lines)."""
    count = {}
    for n, _, _ in layout(items, lambda it: False, width):
        count[n] = count.get(n, 0) + 1
    out, seen = set(), {}
    for i, (n, _, _) in enumerate(lines):
        seen[n] = seen.get(n, 0) + 1
        if seen[n] <= count.get(n, 0):
            out.add(i)
    return frozenset(out)


class ViewState:
    """Cursor item key, fold overrides, follow, and the cursor's screen row (anchor)."""

    def __init__(self):
        self.cursor = None   # item key under the cursor
        self.folds = {}      # item key -> expanded (user's choice)
        self.all = None      # None, True (all open) or False (all closed)
        self.follow = True
        self.anchor = 0      # cursor's first line row on screen, kept while not following
        self.top = 0         # first line on screen
        self._free = False   # anchor came from a free scroll: the cursor may be off-screen

    def expanded(self, it):
        if it["key"] in self.folds:
            return self.folds[it["key"]]
        if self.all is not None:
            return self.all
        return it["running"]

    def frame(self, items, width, height):
        keys = [it["key"] for it in items]
        if self.follow or self.cursor not in keys:
            self.cursor = keys[-1] if keys else None
        cur = keys.index(self.cursor) if keys else 0
        lines = layout(items, self.expanded, width)
        body = max(1, height)
        first = next((i for i, l in enumerate(lines) if l[0] == cur), 0)
        if self.follow:
            top = max(0, len(lines) - body)
            self._free = False
        else:
            top = max(0, min(first - self.anchor, max(0, len(lines) - body)))
            if not self._free and (first < top or first >= top + body):
                top = max(0, first - body // 3)
        self.anchor, self.top = first - top, top
        return Frame(lines, cur, first, top, items, title_lines(items, lines, width))

    def place(self, first, top, height):
        """Shows the cursor's first line `first` with the screen starting at `top`."""
        self.top, self.anchor = top, first - top
        self._free = not 0 <= self.anchor < max(1, height)

    def scroll_by(self, n, total, height):
        """Free line scroll that leaves the cursor alone; reaching the bottom going down resumes follow."""
        max_top = max(0, total - max(1, height))
        top = max(0, min(self.top + n, max_top))
        if n < 0 or top < max_top:
            self.follow = False
        elif n > 0:
            self.follow = True
        self.anchor -= top - self.top
        self.top = top
        self._free = not self.follow

    def move(self, action, items, frame, height):
        """Applies a cursor/fold action, scrolling as little as needed to keep the cursor visible.

        up/down while the cursor's title is off-screen (after a free scroll) scroll one line instead,
        so reading a long unfolded body with the wheel and then a key doesn't jump past it.
        """
        if not items:
            return
        keys = [it["key"] for it in items]
        lines, cur, top = frame.lines, frame.cur, self.top  # self.top: latest, even after scroll_by
        body = max(1, height)
        if action in ("up", "down") and not 0 <= self.anchor < body:
            self.scroll_by(1 if action == "down" else -1, len(lines), body)
            return
        starts = first_lines(lines)
        max_top = max(0, len(lines) - body)

        def goto(n):
            self.cursor, self.follow = keys[n], False
            f = starts.get(n, 0)
            t = top
            if f < t:
                t = f
            elif f >= t + body:
                t = f - body + 1
            self.place(f, t, body)

        def page(n, t):
            self.cursor, self.follow = keys[n], False
            self.place(starts.get(n, 0), t, body)

        def landing(t):
            """The first item whose title starts on the screen at `t`, else the item owning line t."""
            on = [n for n, f in sorted(starts.items()) if t <= f < t + body]
            return on[0] if on else lines[min(t, len(lines) - 1)][0]

        if action == "down":
            goto(min(cur + 1, len(keys) - 1))
        elif action == "up":
            goto(max(cur - 1, 0))
        elif action == "page_down":
            t = min(top + body, max_top)
            if t == top:
                goto(len(keys) - 1)
            else:
                page(max(cur, landing(t)), t)
        elif action == "page_up":
            t = max(0, top - body)
            if t == top:
                goto(0)
            else:
                page(min(cur, landing(t)), t)
        elif action == "first":
            self.cursor, self.follow = keys[0], False
            self.place(0, 0, body)
        elif action == "last":
            self.follow, self._free = True, False
        elif action == "open":
            it = items[cur]
            self.folds[it["key"]] = not self.expanded(it)
            self.follow = False
        elif action == "fold_all":
            self.all = not all(self.expanded(it) for it in items)
            self.folds = {}

    def click(self, line, frame):
        """Moves the cursor to the item on absolute line `line`; a title line also toggles its fold."""
        if not 0 <= line < len(frame.lines) or not frame.items:
            return
        n = frame.lines[line][0]
        it = frame.items[n]
        self.cursor, self.follow = it["key"], False
        f = first_lines(frame.lines).get(n, 0)
        self.top, self.anchor = frame.top, f - frame.top
        self._free = self.anchor < 0  # title scrolled off above a clicked body line
        if line in frame.heads:
            self.folds[it["key"]] = not self.expanded(it)
