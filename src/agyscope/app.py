"""The Textual panel: a full-screen list of conversations and their subagents; `open` shows
one conversation's or subagent's steps full screen.

Started by agyscope.cli.main through run(). The root conversations come from a
sources.Collector running as an async worker on the app's loop; the 1 s refresh reads its
latest RootSet and never waits for it. The panel only reads the agent's files; attach and
detach write only agyscope's own link store. Closing it stops nothing but the panel.
"""

import copy
import os
import time

from rich.text import Text
from textual.app import App, InvalidThemeError, SuspendNotSupported
from textual.binding import Binding
from textual.fuzzy import Matcher
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import config, discovery, keymap, pager, textview, themes
from .agentdb import AgentDB
from .links import LinkError
from .themes import FALLBACK_STYLES
from .viewstate import ViewState
from .widgets import MARQUEE_STEP_S, ColumnHeader, ColumnsChanged, StepView, SubagentTree

REFRESH_S = 1.0
MOUSE_HELP = (
    ("click", "highlight a row; view: fold/unfold a step's title"),
    ("click \u25b6/\u25bc", "fold/unfold a subtree"),
    ("double-click", "fold/unfold a row with children; open a row without"),
    ("wheel", "scroll"),
    ("drag header", "resize list columns"),
)


class ListScreen(Screen):
    """The subagent tree, full width, under the column titles."""

    def compose(self):
        yield ColumnHeader(id="colhead")
        yield Static(id="empty")
        yield SubagentTree(id="tree", state_styles=self.app.state_styles, cols=self.app.settings["columns"],
                           start_folded=self.app.settings["tree_fold"] == "fold")
        yield Static(id="status")

    def on_mount(self) -> None:
        """Starts the panel: this screen is composed after App.on_mount, so the first refresh is here."""
        tree = self.query_one("#tree", SubagentTree)
        tree.state_styles = self.app.state_styles  # a real terminal may compose this before the theme is set
        tree.focus()
        self.app.show_status()
        self.app.start_sources()
        self.app.refresh_data()
        self.app.set_interval(REFRESH_S, self.app.refresh_data)
        if self.app.settings["marquee"]:
            self.set_interval(MARQUEE_STEP_S, self.tick_marquee)

    def tick_marquee(self) -> None:
        if self.app.screen is not self or not self.app.settings["marquee"]:
            return
        self.query_one("#tree", SubagentTree).tick_marquee()

    def on_resize(self, event) -> None:
        self.call_after_refresh(self.show_colhead)

    def show_colhead(self) -> None:
        """Column titles for the tree's current content width, so they line up with the rows."""
        tree = self.query_one("#tree", SubagentTree)
        width = tree.scrollable_content_region.width
        if width:
            self.query_one("#colhead", ColumnHeader).show(width, tree.cols)

    def on_columns_changed(self, message: ColumnsChanged) -> None:
        tree = self.query_one("#tree", SubagentTree)
        tree.cols = dict(message.cols)
        tree.relabel()
        self.show_colhead()

    def on_screen_suspend(self, _event) -> None:
        colhead = self.query_one_optional("#colhead", ColumnHeader)
        if colhead is not None:
            colhead.cancel_drag()

    def action(self, name: str) -> None:
        """close exits with True, open views the row under the cursor, help/toggle_main/attach/
        attach_under/detach go to the app; the rest go to the tree."""
        tree = self.query_one("#tree", SubagentTree)
        sid = tree.selected_id()
        if name == "close":
            self.app.exit(True)
        elif name == "help":
            self.app.open_help()
        elif name == "pager":
            self.app.open_pager(sid)
        elif name == "open" and sid is not None:
            self.app.open_view(sid)
        elif name == "toggle_main":
            self.app.toggle_main()
        elif name in ("attach", "attach_under"):
            self.app.open_picker(under=sid if name == "attach_under" else None)
        elif name == "detach":
            self.app.detach_row(sid)
        elif name not in keymap.CONVERSATION_ACTIONS:
            tree.action(name)

    def on_subagent_tree_open_leaf(self, message: SubagentTree.OpenLeaf) -> None:
        """A double-click on a row without children opens it, like the open key."""
        self.app.open_view(message.id)


class ViewScreen(Screen):
    """One subagent's steps, full screen; back and close return to the list."""

    def __init__(self, row_id: str) -> None:
        super().__init__()
        self.row_id = row_id

    def compose(self):
        yield StepView(id="steps")
        yield Static(id="status")

    def on_mount(self) -> None:
        steps = self.query_one("#steps", StepView)
        steps.state_styles = self.app.state_styles
        steps.focus()
        self.app.show_status()
        self.app.show_view()

    def action(self, name: str) -> None:
        if name in ("back", "close"):
            self.app.close_view()
        elif name == "help":
            self.app.open_help()
        elif name == "pager":
            self.app.open_pager(self.row_id)
        elif name not in keymap.CONVERSATION_ACTIONS:
            self.query_one("#steps", StepView).action(name)


class PickerScreen(ModalScreen):
    """Fuzzy list of conversations to attach: live ones first, then recent ones.

    Dismisses with the chosen candidate dict, or None on Esc.
    """

    DEFAULT_CSS = """
    PickerScreen { align: center middle; }
    #picker { width: 90%; height: 80%; border: round $accent; background: $surface; }
    #pickhint { height: 1; background: $panel; }
    #pickquery { height: 3; }
    #picklist { height: 1fr; }
    """
    BINDINGS = [
        Binding("down,ctrl+n", "move(1)", show=False, priority=True),
        Binding("up,ctrl+p", "move(-1)", show=False, priority=True),
        Binding("escape,ctrl+g", "cancel", show=False, priority=True),
    ]

    def __init__(self, candidates, title: str) -> None:
        super().__init__()
        self.candidates = candidates
        self.title_text = title
        self.shown = []

    def compose(self):
        from textual.containers import Vertical
        with Vertical(id="picker"):
            yield Static(Text(" %s  type to filter  Enter attach  Esc cancel" % self.title_text), id="pickhint")
            yield Input(placeholder="title, agent type or id", id="pickquery")
            yield OptionList(id="picklist")

    def on_mount(self) -> None:
        self.filter("")
        self.query_one("#pickquery", Input).focus()

    def filter(self, query: str) -> None:
        """Lists the candidates matching query, best first (all of them, in order, when empty)."""
        query = query.strip()
        if query:
            m = Matcher(query)
            scored = [(m.match(c["text"]), i, c) for i, c in enumerate(self.candidates)]
            self.shown = [c for score, _, c in sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))]
            prompts = [m.highlight(c["text"]) for c in self.shown]
        else:
            self.shown = list(self.candidates)
            prompts = [Text(c["text"]) for c in self.shown]
        olist = self.query_one("#picklist", OptionList)
        olist.clear_options()
        olist.add_options([Option(p) for p in prompts])
        if self.shown:
            olist.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.choose()

    def on_option_list_option_selected(self, event) -> None:
        self.choose(event.option_index)

    def choose(self, index=None) -> None:
        olist = self.query_one("#picklist", OptionList)
        index = olist.highlighted if index is None else index
        if index is not None and 0 <= index < len(self.shown):
            self.dismiss(self.shown[index])

    def action_move(self, delta: int) -> None:
        olist = self.query_one("#picklist", OptionList)
        if not self.shown:
            return
        cur = olist.highlighted if olist.highlighted is not None else -delta
        olist.highlighted = max(0, min(len(self.shown) - 1, cur + delta))

    def action_cancel(self) -> None:
        self.dismiss(None)


def help_text(keys: dict) -> Text:
    """Every action with its keys in `keys` (key name -> action), where it works and what it does,
    then the mouse actions."""
    rows = [(a, ", ".join(keymap.keys_for(keys, a)) or "-", keymap.where(a), keymap.DESCRIPTIONS[a])
            for a in keymap.ACTIONS]
    wa = max(len("Action"), *(len(r[0]) for r in rows))
    wk = max(len("Keys"), *(len(r[1]) for r in rows))
    out = Text()
    out.append(" %-*s  %-*s  %-5s  %s\n" % (wa, "Action", wk, "Keys", "Where", "Does"), "bold")
    for action, ks, where, desc in rows:
        out.append(" %-*s  %-*s  %-5s  %s\n" % (wa, action, wk, ks, where, desc))
    out.append("\n Mouse (list and view)\n", "bold")
    wm = max(len(m) for m, _ in MOUSE_HELP)
    for mouse, desc in MOUSE_HELP:
        out.append(" %-*s  %s\n" % (wm, mouse, desc))
    return out


class HelpScreen(ModalScreen):
    """Every action and its keys (preset plus user keys), and the mouse actions; Esc, ?, q close it."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help { width: auto; max-width: 95%; height: auto; max-height: 90%; border: round $accent;
            background: $surface; }
    #helptitle { height: 1; background: $panel; }
    #helpbody { width: auto; height: auto; }
    """
    BINDINGS = [
        Binding("escape,question_mark,q,ctrl+g", "close", show=False, priority=True),
    ]

    def __init__(self, keys: dict, preset: str) -> None:
        super().__init__()
        self.keys = dict(keys)
        self.preset = preset

    def compose(self):
        from textual.containers import VerticalScroll
        with VerticalScroll(id="help"):
            yield Static(Text(" Keys (%s preset + your keys)   Esc / ? / q close" % self.preset), id="helptitle")
            yield Static(help_text(self.keys), id="helpbody")

    def action_close(self) -> None:
        self.dismiss(None)


class AgyscopeApp(App):
    """Root conversations and their subagents, refreshed every second: a list screen and a view screen.

    agents is a discovery.Forest; sources is a sources.Collector (or anything with `roots`,
    `initial()`, `poke()` and optionally an async `run(publish)`).
    """

    CSS = """
    #colhead { height: 1; text-style: bold; }
    #empty { height: 1; color: $text-muted; }
    #tree { height: 1fr; }
    #steps { height: 1fr; }
    #status { height: 1; color: $error; text-style: bold; }
    """
    BINDINGS = [
        Binding("ctrl+c", "exit_false", show=False, priority=True),
        Binding("ctrl+q", "exit_false", show=False, priority=True),
    ]
    ENABLE_COMMAND_PALETTE = False  # its ctrl+p binding would shadow emacs `up`

    def __init__(self, forest, sources, settings, warnings=(), clock=time.time, themes_dir=None):
        super().__init__()
        self.agents = forest  # discovery.Forest (App.tree is taken by Textual)
        self.sources = sources
        self.settings = {**copy.deepcopy(config.DEFAULTS), **settings}
        self.show_main = bool(self.settings["main_rows"])
        self._dbs = {}  # data directory -> AgentDB, for the picker
        self.clock = clock
        self.themes_dir = themes_dir
        self.state_styles = FALLBACK_STYLES
        self.status = list(warnings)
        try:
            self.keys = keymap.build(self.settings["keymap"], self.settings["keys"])
        except ValueError as e:
            self.status.append("keys: %s; using defaults" % e)
            self.keys = keymap.build("emacs")
        for k in keymap.unreachable(self.keys):
            self.status.append("keys: the terminal can't send %s; use another key" % k)
        self.views = {}  # subagent id -> ViewState
        self.rows = {}  # subagent id -> row from the last refresh

    def on_mount(self) -> None:
        if self.themes_dir:
            loaded, warnings = themes.load_user_themes(self.themes_dir)
            for theme in loaded:
                self.register_theme(theme)
            self.status.extend(warnings)
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        name = self.settings["theme"]
        try:
            self.theme = name
        except InvalidThemeError:
            self.status.append("theme %r not found; using %s" % (name, self.theme))
        self.state_styles = themes.styles_from(self.get_css_variables())
        self.apply_state_styles()

    def _on_theme_changed(self, _theme) -> None:
        self.state_styles = themes.styles_from(self.get_css_variables())
        self.apply_state_styles()

    def apply_state_styles(self) -> None:
        if hasattr(self, "list_screen") and self.list_screen.is_mounted:
            tree = self.list_screen.query_one("#tree", SubagentTree)
            tree.state_styles = self.state_styles
            tree.relabel()
        if isinstance(self.screen, ViewScreen):
            steps = self.screen.query_one_optional("#steps", StepView)
            if steps is not None:
                steps.state_styles = self.state_styles
                steps.refresh()

    def get_default_screen(self) -> Screen:
        self.list_screen = ListScreen()  # stays at the bottom of the stack while a view is shown
        return self.list_screen

    def open_help(self) -> None:
        """Shows every action's keys in the active keymap, over the current screen."""
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen(self.keys, self.settings["keymap"]))

    def show_status(self) -> None:
        """Shows the newest status message (if any) on every screen's status line."""
        for screen in self.screen_stack:
            for status in screen.query("#status").results(Static):
                status.display = bool(self.status)
                status.update(Text(" ! " + self.status[-1]) if self.status else "")

    def start_sources(self) -> None:
        """First roots from the cheap sources, then the collector as an async worker."""
        initial = getattr(self.sources, "initial", None)
        if initial is not None:
            initial()
        if hasattr(self.sources, "on_message"):
            self.sources.on_message = self._say
        if hasattr(self.sources, "run"):
            self.run_worker(self._run_sources(), exclusive=True, group="sources", exit_on_error=False)

    async def _run_sources(self) -> None:
        try:
            await self.sources.run(self.set_roots)
        except Exception as e:  # the panel keeps showing the last roots
            self.status.append("sources stopped: %s" % e)
            self.show_status()

    def set_roots(self, roots) -> None:
        """The collector published a new RootSet: refresh now instead of on the next tick."""
        if hasattr(self, "list_screen") and self.list_screen.is_mounted:
            self.refresh_data()

    def poke_sources(self) -> None:
        poke = getattr(self.sources, "poke", None)
        if poke is not None:
            poke()

    def refresh_data(self) -> None:
        """Re-reads the roots and their subagents, then updates the list and the open view."""
        tree = self.list_screen.query_one("#tree", SubagentTree)
        self.agents.update(self.sources.roots)
        groups = self.agents.rows(now=self.clock(), show_main=self.show_main)
        self.rows = {r["id"]: r for r in discovery.flat(groups)}
        tree.update_groups(groups)
        empty = self.list_screen.query_one("#empty", Static)
        empty.display = not self.rows
        empty.update(" " + (StepView.NO_SUBAGENTS if self.agents.roots else StepView.NO_CONVERSATION))
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

    def toggle_main(self) -> None:
        self.show_main = not self.show_main
        self.refresh_data()

    def _say(self, message: str) -> None:
        self.status.append(message)
        self.show_status()

    def _db(self, app_dir):
        db = self._dbs.get(app_dir)
        if db is None:
            db = self._dbs[app_dir] = AgentDB(app_dir)
        return db

    def picker_candidates(self, now=None):
        """[{conv, app_dir, title, live, text}]: live roots first (newest first), then recent roots."""
        now = self.clock() if now is None else now
        out, seen = [], set()

        def add(conv, app_dir, title, live, modified):
            if conv in seen:
                return
            seen.add(conv)
            when = "live" if live else (discovery.age(now - modified) if modified else "-")
            text = "%s %s  %s  %s  %s" % ("*" if live else " ", title or "-", conv[:8], when,
                                          os.path.basename(app_dir.rstrip("/")) or app_dir)
            out.append({"conv": conv, "app_dir": app_dir, "title": title, "live": live, "text": text})

        roots = list(getattr(self.sources, "roots", ()))
        for r in roots:
            if r.live:
                add(r.conv, r.app_dir, self._db(r.app_dir).title_of(r.conv), True, None)
        dirs = list(dict.fromkeys([self.agents.app_dir] + [r.app_dir for r in roots]))
        for app_dir in dirs:
            for m in self._db(app_dir).recent_roots():
                add(m["id"], app_dir, m["title"] or m["agent"], False, m["modified"])
        return out

    def open_picker(self, under=None) -> None:
        """Attach picker; with under (a row id), attaches the choice under that row's main conversation."""
        if self.agents.links is None:
            self._say("attach: no link store")
            return
        parent = None
        if under is not None:
            row = self.rows.get(under)
            if row is None:
                self._say("attach under: no conversation highlighted")
                return
            parent = row["tree"].root
        title = "Attach under %s" % (self.rows[parent]["role"] if parent in self.rows else parent[:8]) \
            if parent else "Attach conversation"
        self.push_screen(PickerScreen(self.picker_candidates(), title),
                         callback=lambda choice: self._picked(choice, parent))

    def _picked(self, choice, parent) -> None:
        if not choice:
            return
        try:
            if parent:
                self.agents.links.attach(choice["conv"], parent)
            else:
                self.agents.links.attach(choice["conv"], None, app_dir=choice["app_dir"])
        except (LinkError, OSError) as e:
            self._say("attach: %s" % e)
            return
        self.poke_sources()
        self.refresh_data()

    def detach_row(self, row_id) -> None:
        """Detaches the row from where it is listed: a main row from the top level, others from their parent."""
        row = self.rows.get(row_id)
        if row is None:
            return
        if self.agents.links is None:
            self._say("detach: no link store")
            return
        if row.get("main") and row.get("pane"):
            self._say("can't detach this pane's own conversation")
            return
        try:
            self.agents.links.detach(row_id, None if row.get("main") else row.get("parent"))
        except (LinkError, OSError) as e:
            self._say("detach: %s" % e)
            return
        self.poke_sources()
        self.refresh_data()

    def action_exit_false(self) -> None:
        self.exit(False)

    def on_key(self, event) -> None:
        """Maps a key to an action and hands it to the shown screen."""
        if not isinstance(self.screen, (ListScreen, ViewScreen)):
            return  # the picker's input gets every key
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
    app = AgyscopeApp(setup.forest, setup.collector, setup.settings, setup.warnings,
                      themes_dir=getattr(setup, "themes_dir", None))
    result = app.run()
    if app.return_code:
        return 2
    if result is not True:
        return 1
    if setup.on_close is not None:
        setup.on_close()
    return 0
