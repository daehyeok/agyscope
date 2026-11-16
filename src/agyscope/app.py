"""The Textual panel: a full-screen subagent list; `open` shows one subagent's steps full screen.

Started by agyscope.cli.main through run(). The panel only reads files; closing it stops
nothing but the panel.
"""

import time

from rich.text import Text
from textual.app import App, SuspendNotSupported
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from . import discovery, keymap, pager, textview
from .viewstate import ViewState
from .widgets import StepView, SubagentTree, header_columns

REFRESH_S = 1.0
_HINT_FIRST = {"open": "Enter", "back": "Esc"}  # action -> key the hints show when it is bound


class ListScreen(Screen):
    """The subagent tree, full width, under a key hint row and the column titles."""

    def compose(self):
        yield Static(Text(self.app.list_hints()), id="listhint")
        yield Static(id="colhead")
        yield Static(id="empty")
        yield SubagentTree(id="tree")
        yield Static(id="status")

    def on_mount(self) -> None:
        """Starts the panel: this screen is composed after App.on_mount, so the first refresh is here."""
        self.query_one("#tree", SubagentTree).focus()
        self.app.show_status()
        self.app.refresh_data()
        self.app.set_interval(REFRESH_S, self.app.refresh_data)

    def on_resize(self, event) -> None:
        self.call_after_refresh(self.show_colhead)

    def show_colhead(self) -> None:
        """Column titles for the tree's current content width, so they line up with the rows."""
        width = self.query_one("#tree", SubagentTree).scrollable_content_region.width
        if width:
            self.query_one("#colhead", Static).update(header_columns(width))

    def action(self, name: str) -> None:
        """close exits with True, open views the subagent under the cursor; the rest go to the tree."""
        tree = self.query_one("#tree", SubagentTree)
        sid = tree.selected_id()
        if name == "close":
            self.app.exit(True)
        elif name == "pager":
            self.app.open_pager(sid)
        elif name == "open" and sid is not None:
            self.app.open_view(sid)
        else:
            tree.action(name)


class ViewScreen(Screen):
    """One subagent's steps, full screen; back and close return to the list."""

    def __init__(self, row_id: str) -> None:
        super().__init__()
        self.row_id = row_id

    def compose(self):
        yield StepView(id="steps")
        yield Static(id="status")
        yield Static(Text(self.app.view_hints()), id="hints")

    def on_mount(self) -> None:
        self.query_one("#steps", StepView).focus()
        self.app.show_status()
        self.app.show_view()

    def action(self, name: str) -> None:
        if name in ("back", "close"):
            self.app.close_view()
        elif name == "pager":
            self.app.open_pager(self.row_id)
        else:
            self.query_one("#steps", StepView).action(name)


class AgyscopeApp(App):
    """One root conversation's subagents, refreshed every second: a list screen and a view screen."""

    CSS = """
    #listhint { height: 1; background: $panel; }
    #colhead { height: 1; text-style: bold; }
    #empty { height: 1; color: $text-muted; }
    #tree { height: 1fr; }
    #steps { height: 1fr; }
    #status { height: 1; color: $error; text-style: bold; }
    #hints { height: 1; background: $panel; }
    """
    BINDINGS = [
        Binding("ctrl+c", "exit_false", show=False, priority=True),
        Binding("ctrl+q", "exit_false", show=False, priority=True),
    ]
    ENABLE_COMMAND_PALETTE = False  # its ctrl+p binding would shadow emacs `up`

    def __init__(self, tree, resolver, settings, warnings=(), clock=time.time):
        super().__init__()
        self.agents = tree  # discovery.Tree (App.tree is taken by Textual)
        self.resolver = resolver
        self.settings = settings
        self.clock = clock
        self.status = list(warnings)
        try:
            self.keys = keymap.build(settings["keymap"], settings["keys"])
        except ValueError as e:
            self.status.append("keys: %s; using defaults" % e)
            self.keys = keymap.build("emacs")
        for k in keymap.unreachable(self.keys):
            self.status.append("keys: the terminal can't send %s; use another key" % k)
        self.views = {}  # subagent id -> ViewState
        self.rows = {}  # subagent id -> row from the last refresh

    def get_default_screen(self) -> Screen:
        self.list_screen = ListScreen()  # stays at the bottom of the stack while a view is shown
        return self.list_screen

    def hint(self, action: str) -> str:
        """The key to show for an action in the hints: Enter for open, Esc for back, else a preset key."""
        keys = keymap.keys_for(self.keys, action)
        if _HINT_FIRST.get(action) in keys:
            return _HINT_FIRST[action]
        always = set(k for ks in keymap.ALWAYS.values() for k in ks)
        preferred = [k for k in keys if k not in always] or keys
        return preferred[0] if preferred else "?"

    def list_hints(self) -> str:
        h = self.hint
        return " Subagents  %s/%s move  %s view  %s close" % (h("down"), h("up"), h("open"), h("close"))

    def view_hints(self) -> str:
        h = self.hint
        return " %s fold  %s all  %s follow  %s pager  %s back  %s back" % (
            h("open"), h("fold_all"), h("last"), h("pager"), h("back"), h("close"))

    def show_status(self) -> None:
        """Shows the newest status message (if any) on every screen's status line."""
        for screen in self.screen_stack:
            for status in screen.query("#status").results(Static):
                status.display = bool(self.status)
                status.update(Text(" ! " + self.status[-1]) if self.status else "")

    def refresh_data(self) -> None:
        """Re-reads the root and its subagents, then updates the list and the open view."""
        tree = self.list_screen.query_one("#tree", SubagentTree)
        root = self.resolver.current()
        app_dir = getattr(self.resolver, "app_dir", None)
        if root != self.agents.root or (app_dir and app_dir != self.agents.app_dir):
            self.agents.set_root(root, app_dir)
            tree.reset()
            self.views, self.rows = {}, {}
            self.close_view()
        groups = self.agents.rows(now=self.clock())
        self.rows = {r["id"]: r for r in discovery.flat(groups)}
        tree.update_groups(groups)
        empty = self.list_screen.query_one("#empty", Static)
        empty.display = not self.rows
        empty.update(" " + (StepView.NO_SUBAGENTS if self.agents.root else StepView.NO_CONVERSATION))
        self.list_screen.show_colhead()
        if isinstance(self.screen, ViewScreen):
            if self.screen.row_id in self.rows:
                self.show_view()
            else:
                self.close_view()  # the viewed subagent is gone

    def open_view(self, row_id: str) -> None:
        """Shows subagent `row_id` full screen (its remembered ViewState, else a new one that follows)."""
        if row_id in self.rows and not isinstance(self.screen, ViewScreen):
            self.push_screen(ViewScreen(row_id))

    def close_view(self) -> None:
        """Returns to the list, which kept its folds and cursor."""
        if isinstance(self.screen, ViewScreen):
            self.pop_screen()

    def show_view(self) -> None:
        """Shows the viewed subagent's current steps in the view screen."""
        screen = self.screen
        steps = screen.query_one_optional("#steps", StepView) if isinstance(screen, ViewScreen) else None
        row = self.rows.get(screen.row_id) if steps is not None else None
        if row is not None:
            steps.show(row, self.agents.items(row), self.views.setdefault(screen.row_id, ViewState()))

    def action_exit_false(self) -> None:
        self.exit(False)

    def on_key(self, event) -> None:
        """Maps a key to an action and hands it to the shown screen."""
        act = self.keys.get(keymap.from_textual(event.key, event.character))
        if act is None:
            return
        event.stop()
        event.prevent_default()  # keeps App._on_key from also running Screen bindings (Tab)
        if isinstance(self.screen, (ListScreen, ViewScreen)):
            self.screen.action(act)

    def open_pager(self, row_id) -> None:
        """Shows subagent `row_id`'s unfolded text in the pager; errors go to the status line."""
        row = self.rows.get(row_id)
        if row is None:
            return
        try:
            with self.suspend():
                err = pager.show(self.settings["pager"], textview.plain(self.agents, row), row["role"])
        except SuspendNotSupported:
            err = "pager: this terminal can't be suspended"
        if err:
            self.status.append(err)
            self.show_status()


def run(setup) -> int:
    """0 after `close` (then setup.on_close runs, if set), 1 after ctrl+c/ctrl+q, 2 when the panel crashed.

    Textual returns None with return_code 1 on an uncaught exception; that must not look like
    ctrl+c, so it becomes 2. Exceptions raised here propagate to cli.main.
    """
    app = AgyscopeApp(setup.tree, setup.resolver, setup.settings, setup.warnings)
    result = app.run()
    if app.return_code:
        return 2
    if result is not True:
        return 1
    if setup.on_close is not None:
        setup.on_close()
    return 0
