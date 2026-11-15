"""The curses panel: a list of subagents and a live view of one of them.

The panel only reads files. It never sends anything to an agent or a terminal,
so closing it stops nothing but the panel.
"""

import curses
import os

from . import discovery, keymap, pager
from .transcript import col_pad, layout

REFRESH_MS = 1000

COLOR = {"working": 1, "running": 1, "done": 2, "error": 3, "failed": 3, "quiet": 4, "dim": 4,
         "waiting": 5, "msg": 6, "cmd": 6, "warn": 3}
BOLD = ("cmd", "msg", "running", "step", "warn")


def attr(name):
    a = curses.A_BOLD if name in BOLD else 0
    if curses.has_colors() and name in COLOR:
        a |= curses.color_pair(COLOR[name])
    return a


def put(stdscr, y, text, w, a=0):
    try:
        padded = col_pad(text, w)
        if hasattr(stdscr, "addstr"):
            stdscr.addstr(y, 0, padded, a)
        else:
            stdscr.addnstr(y, 0, padded, len(padded), a)
    except curses.error:
        pass


def plain(tree, row):
    return [text for _, _, text in layout(tree.items(row), lambda it: True)]


class View:
    """Live view state for one subagent: cursor item, fold overrides, follow, screen anchor."""

    def __init__(self, child):
        self.child = child
        self.cursor = None   # item key under the cursor
        self.folds = {}      # item key -> expanded (user's choice)
        self.follow = True
        self.anchor = 0      # cursor line's row on screen, kept while not following
        self.all = None      # None, True (all open) or False (all closed)

    def expanded(self, it):
        if it["key"] in self.folds:
            return self.folds[it["key"]]
        if self.all is not None:
            return self.all
        return it["running"]


class Panel:
    def __init__(self, tree, resolver, settings, warnings=()):
        self.tree = tree
        self.resolver = resolver
        self.settings = settings
        self.status = list(warnings)
        try:
            self.keys = keymap.build(settings["keymap"], settings["keys"])
        except ValueError as e:
            self.status.append("keys: %s; using defaults" % e)
            self.keys = keymap.build("emacs")

    # -- helpers ---------------------------------------------------------------

    def hint(self, action):
        """The key to show for an action on the title row: Enter for open, else a preset key."""
        keys = keymap.keys_for(self.keys, action)
        if action == "open" and "Enter" in keys:
            return "Enter"
        always = set(k for ks in keymap.ALWAYS.values() for k in ks)
        preferred = [k for k in keys if k not in always] or keys
        return preferred[0] if preferred else "?"

    def read_key(self, stdscr):
        code = stdscr.getch()
        if code == -1:
            return None
        if code == curses.KEY_RESIZE:
            return "Resize"

        def nxt():
            stdscr.timeout(40)
            try:
                return stdscr.getch()
            finally:
                stdscr.timeout(REFRESH_MS)

        return keymap.decode(code, nxt)

    def action(self, key):
        return self.keys.get(key) if key else None

    def status_line(self, stdscr, h, w):
        if self.status:
            put(stdscr, h - 1, " ! " + self.status[-1], w, attr("warn"))
            return h - 2
        return h - 1

    # -- live view -------------------------------------------------------------

    def draw_view(self, stdscr, row, v, h, w):
        items = self.tree.items(row)
        keys = [it["key"] for it in items]
        if v.follow or v.cursor not in keys:
            v.cursor = keys[-1] if keys else None
        cur = keys.index(v.cursor) if keys else 0
        lines = layout(items, v.expanded, w)
        last_row = self.status_line(stdscr, h, w)
        body = max(1, last_row)
        first = next((i for i, l in enumerate(lines) if l[0] == cur), 0)
        if v.follow:
            top = max(0, len(lines) - body)
        else:
            top = max(0, min(first - v.anchor, max(0, len(lines) - body)))
            if first < top or first >= top + body:
                top = max(0, first - body // 3)
        v.anchor = first - top
        model = row.get("model")
        badge = "[%s · %s]" % (row["state"], model) if model and model != "-" else "[%s]" % row["state"]
        title = " %s %s  %s/%s move  %s fold  %s all  %s follow  %s pager  %s back" % (
            row["role"], badge, self.hint("down"), self.hint("up"), self.hint("open"),
            self.hint("fold_all"), self.hint("last"), self.hint("pager"), self.hint("back"))
        put(stdscr, 0, title, w, curses.A_REVERSE)
        for i, (n, style, text) in enumerate(lines[top: top + body]):
            a = attr(style)
            if n == cur and lines[top + i] is lines[first]:
                a |= curses.A_REVERSE
            put(stdscr, 1 + i, text, w, a)
        return items, keys, cur, lines, top, body

    def view_key(self, stdscr, act, row, v, items, keys, cur, lines, top, body):
        """Handles one action in the live view; returns False to go back to the list."""
        if act in ("back", "close"):
            return False
        if not keys:
            return True
        if act == "down":
            v.cursor = keys[min(cur + 1, len(keys) - 1)]
            v.anchor = min(v.anchor + 1, body - 1)
            v.follow = False
        elif act == "up":
            v.cursor, v.follow = keys[max(cur - 1, 0)], False
            v.anchor = max(v.anchor - 1, 0)
        elif act == "page_down":
            n = lines[min(len(lines) - 1, top + body)][0] if lines else cur
            v.cursor, v.follow = keys[max(cur, n)], False
        elif act == "page_up":
            n = lines[max(0, top - body)][0] if lines else cur
            v.cursor, v.follow, v.anchor = keys[min(cur, n)], False, 0
        elif act == "first":
            v.cursor, v.follow, v.anchor = keys[0], False, 0
        elif act == "last":
            v.follow = True
        elif act == "open":
            it = items[cur]
            v.folds[it["key"]] = not v.expanded(it)
            v.follow = False
        elif act == "fold_all":
            v.all = not all(v.expanded(it) for it in items)
            v.folds = {}
        elif act == "pager":
            curses.endwin()
            err = pager.show(self.settings["pager"], plain(self.tree, row), row["role"])
            stdscr.clear()
            if err:
                self.status.append(err)
        return True

    # -- list ------------------------------------------------------------------

    def run(self, stdscr):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        stdscr.timeout(REFRESH_MS)
        try:
            has_colors = curses.has_colors()
        except curses.error:
            has_colors = False
        if has_colors:
            curses.use_default_colors()
            for n, c in ((1, curses.COLOR_YELLOW), (2, curses.COLOR_GREEN), (3, curses.COLOR_RED),
                         (4, curses.COLOR_MAGENTA), (5, curses.COLOR_BLUE), (6, curses.COLOR_CYAN)):
                curses.init_pair(n, c, -1)
        selected, top = None, 0
        views, viewing = {}, None
        while True:
            root = self.resolver.current()
            app_dir = getattr(self.resolver, "app_dir", None)
            if root != self.tree.root or (app_dir and app_dir != self.tree.app_dir):
                self.tree.set_root(root, app_dir)
                selected, top, views, viewing = None, 0, {}, None
            groups = self.tree.rows()
            rows = discovery.flat(groups)
            ids = [r["id"] for r in rows]
            h, w = stdscr.getmaxyx()
            stdscr.erase()

            if viewing in ids:
                row = rows[ids.index(viewing)]
                v = views.setdefault(viewing, View(viewing))
                state = self.draw_view(stdscr, row, v, h, w)
                stdscr.refresh()
                act = self.action(self.read_key(stdscr))
                if act and not self.view_key(stdscr, act, row, v, *state):
                    viewing = None
                continue
            viewing = None

            last_row = self.status_line(stdscr, h, w)
            body = max(1, last_row)
            idx = ids.index(selected) if selected in ids else 0
            put(stdscr, 0, " Subagents  %s/%s move  %s view  %s close" % (
                self.hint("down"), self.hint("up"), self.hint("open"), self.hint("close")), w, curses.A_REVERSE)
            lines = []  # (row index or None, text, attr)
            for name, grows in groups:
                if not grows:
                    continue
                lines.append((None, name, curses.A_BOLD | curses.A_UNDERLINE))
                for r in grows:
                    lines.append((ids.index(r["id"]), discovery.row_text(r), attr(r["state"])))
            if not root:
                lines.append((None, " No conversation found for this pane.", attr("dim")))
            elif not rows:
                lines.append((None, " No subagents yet.", 0))
            sel = next((i for i, l in enumerate(lines) if l[0] == idx), 0)
            if sel < top:
                top = max(0, sel - 1)
            elif sel >= top + body:
                top = sel - body + 1
            for i, (n, text, a) in enumerate(lines[top: top + body]):
                put(stdscr, 1 + i, text, w, a | (curses.A_REVERSE if n == idx and rows else 0))
            stdscr.refresh()
            selected = ids[idx] if rows else None

            act = self.action(self.read_key(stdscr))
            if act == "close":
                return True
            if not rows:
                continue
            if act == "down":
                selected = ids[min(idx + 1, len(ids) - 1)]
            elif act == "up":
                selected = ids[max(idx - 1, 0)]
            elif act == "page_down":
                selected = ids[min(idx + body, len(ids) - 1)]
            elif act == "page_up":
                selected = ids[max(idx - body, 0)]
            elif act == "first":
                selected = ids[0]
            elif act == "last":
                selected = ids[-1]
            elif act == "open":
                viewing = ids[idx]
                views[viewing] = View(viewing)  # open following the newest step
            # every other key: nothing


def run(tree, resolver, settings, warnings=()):
    os.environ.setdefault("ESCDELAY", "25")
    try:
        return bool(curses.wrapper(Panel(tree, resolver, settings, warnings).run))
    except KeyboardInterrupt:
        return False
