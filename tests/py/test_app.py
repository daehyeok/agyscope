"""AgyscopeApp: list and view screens, 1 s refresh, root switch, empty states and the status line."""

import contextlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import make_fixtures as fx
from agyscope import discovery, textview

try:
    import textual  # noqa: F401
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False

if HAVE_TEXTUAL:
    # outside the try: a missing app module must fail, not skip
    from agyscope import app as app_mod
    from agyscope.app import AgyscopeApp, ListScreen, ViewScreen
    from agyscope.widgets import StepView, SubagentTree, header_columns

SIZE = (120, 30)
NOW = time.time()
SETTINGS = {"keymap": "emacs", "keys": {}, "pager": ["less"]}


class FakeResolver:
    def __init__(self, root, app_dir):
        self.root = root
        self.app_dir = app_dir

    def current(self):
        return self.root


def shape(node):
    return [(str(c.label) if c.data is None else c.data, shape(c)) for c in node.children]


def find(node, cid):
    for c in node.children:
        if c.data == cid:
            return c
        hit = find(c, cid)
        if hit is not None:
            return hit
    return None


FIXTURE_SHAPE = [("Working", [(fx.PLANNER, [(fx.CHECKER, [])])]), ("Completed", [(fx.REVIEWER, [])])]


def make_app(root=fx.ROOT, settings=SETTINGS, warnings=(), fixture=True):
    app_dir = tempfile.mkdtemp()
    if fixture:
        fx.build_tree(app_dir)
    resolver = FakeResolver(root, app_dir)
    return AgyscopeApp(discovery.Tree(app_dir), resolver, settings, warnings=warnings, clock=lambda: NOW)


def text_of(static):
    return str(static.content)


def steps_items(n):
    """n one-line step items (the shape transcript.items() returns)."""
    return [{"key": k, "depth": 0, "title": "t%d" % k, "style": "step", "running": False,
             "failed": False, "body": lambda: []} for k in range(n)]


async def start(pilot, app):
    app.refresh_data()
    await pilot.pause()
    return app.query_one("#tree", SubagentTree)


async def open_view(pilot, app, downs=0, key="enter"):
    """Moves the list cursor down `downs` rows, presses `key` and returns the view's StepView."""
    for _ in range(downs):
        await pilot.press("ctrl+n")
    await pilot.press(key)
    await pilot.pause()
    return app.screen.query_one("#steps", StepView)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class ListScreenTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_full_width(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            self.assertIsInstance(app.screen, ListScreen)
            self.assertEqual(tree.outer_size.width, 120)
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)
            self.assertIs(app.focused, tree)
            self.assertFalse(app.query_one("#status").display)
            self.assertFalse(app.query_one("#empty").display)
            self.assertEqual(len(app.query("#steps")), 0)  # no step pane on the list
            hint = text_of(app.query_one("#listhint"))
            for part in ("Subagents", "C-n/C-p move", "Enter view", "q close"):
                self.assertIn(part, hint)
            for gone in ("focus", "sidebar"):
                self.assertNotIn(gone, hint)
            head = text_of(app.query_one("#colhead"))
            self.assertTrue(head.startswith("Subagent "))
            for title in ("State", "Model", "Age", "Last step"):
                self.assertIn(title, head)
            self.assertEqual(head, header_columns(tree.scrollable_content_region.width).plain)  # lines up with rows

    async def test_root_switch_resets(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            tree.action("back")  # collapse Planner
            tree.action("last")  # Reviewer
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            app.resolver.root = "other-root"
            app.refresh_data()
            await pilot.pause()
            self.assertEqual(app.agents.root, "other-root")
            self.assertEqual(app.views, {})
            self.assertIsNone(find(tree.root, fx.PLANNER))
            self.assertEqual(shape(tree.root), [])
            self.assertEqual(text_of(app.query_one("#empty")), " " + StepView.NO_SUBAGENTS)
            app.resolver.root = fx.ROOT
            app.refresh_data()
            await pilot.pause()
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)
            self.assertTrue(find(tree.root, fx.PLANNER).is_expanded)  # old fold state forgotten
            self.assertEqual(tree.selected_id(), fx.PLANNER)
            self.assertFalse(app.query_one("#empty").display)

    async def test_empty_states(self):
        app = make_app(root=None)
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            empty = app.query_one("#empty")
            self.assertTrue(empty.display)
            self.assertEqual(text_of(empty), " " + StepView.NO_CONVERSATION)
            await pilot.press("enter")  # nothing to open
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
        app = make_app(fixture=False)
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            self.assertEqual(text_of(app.query_one("#empty")), " " + StepView.NO_SUBAGENTS)
            self.assertEqual(shape(tree.root), [])  # no empty Working/Completed headers

    async def test_open_on_group_toggles(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            tree.action("first")  # the Working group row
            await pilot.pause()
            self.assertIsNone(tree.selected_id())
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertFalse(tree.root.children[0].is_expanded)
            await pilot.press("enter")
            await pilot.pause()
            self.assertTrue(tree.root.children[0].is_expanded)

    async def test_fold_all_in_list(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.press("ctrl+n")  # Checker
            await pilot.press("alt+a")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertFalse(find(tree.root, fx.PLANNER).is_expanded)
            self.assertEqual(tree.selected_id(), fx.PLANNER)  # the cursor left the hidden row
            self.assertTrue(all(g.is_expanded for g in tree.root.children))  # groups stay open
            await pilot.press("alt+a")
            await pilot.pause()
            self.assertTrue(find(tree.root, fx.PLANNER).is_expanded)

    async def test_warnings_shown_on_status_line(self):
        app = make_app(warnings=["config: bad [value]"])
        async with app.run_test(size=SIZE) as pilot:
            status = app.query_one("#status")
            self.assertTrue(status.display)
            self.assertEqual(text_of(status), " ! config: bad [value]")
        settings = dict(SETTINGS, keys={"down": ["M-b"], "up": ["M-f"]})
        app = make_app(settings=settings, warnings=["config: bad"])
        async with app.run_test(size=SIZE) as pilot:
            status = app.query_one("#status")
            self.assertTrue(status.display)
            self.assertTrue(text_of(status).startswith(" ! "))
            self.assertIn("M-f", text_of(status))
            self.assertEqual(app.status[0], "config: bad")
            self.assertTrue(any("M-b" in w for w in app.status))
            self.assertEqual(app.keys.get("C-n"), "down")
        app = make_app(settings=dict(SETTINGS, keymap="nope"))
        async with app.run_test(size=SIZE) as pilot:
            self.assertIn("using defaults", text_of(app.query_one("#status")))
            self.assertEqual(app.keys.get("q"), "close")


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class ViewScreenTest(unittest.IsolatedAsyncioTestCase):
    async def test_open_shows_full_screen_view(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            steps = await open_view(pilot, app, downs=1)  # Checker
            self.assertIsInstance(app.screen, ViewScreen)
            self.assertEqual(app.screen.row_id, fx.CHECKER)
            self.assertEqual(steps.outer_size.width, 120)
            self.assertEqual(steps.row["id"], fx.CHECKER)
            self.assertTrue(steps.header_text().startswith("Checker [working"))
            self.assertIs(steps.state, app.views[fx.CHECKER])
            self.assertIs(app.focused, steps)
            hints = text_of(app.screen.query_one("#hints"))
            for part in ("Enter fold", "M-a all", "> follow", "o pager", "Esc back", "q back"):
                self.assertIn(part, hints)

    async def test_open_on_parent_opens_view(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            steps = await open_view(pilot, app)  # Planner has a child: open still views it
            self.assertEqual(steps.row["id"], fx.PLANNER)
            self.assertTrue(find(tree.root, fx.PLANNER).is_expanded)

    async def test_back_restores_list_state(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            tree.action("back")  # collapse Planner
            await pilot.pause()
            for key in ("escape", "left", "ctrl+g"):
                await open_view(pilot, app, downs=2 if key == "escape" else 0)  # Reviewer
                self.assertEqual(app.screen.row_id, fx.REVIEWER)
                await pilot.press(key)
                await pilot.pause()
                self.assertIsInstance(app.screen, ListScreen, key)
                self.assertIs(app.focused, tree, key)
                self.assertEqual(tree.selected_id(), fx.REVIEWER, key)
                self.assertFalse(find(tree.root, fx.PLANNER).is_expanded, key)
            self.assertIsNone(app.return_value)

    async def test_reopen_restores_view_state(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            steps = await open_view(pilot, app)
            state = steps.state
            await pilot.press("ctrl+p")  # moves the cursor: follow pauses
            await pilot.pause()
            self.assertFalse(state.follow)
            cursor, top = state.cursor, state.top
            await pilot.press("escape")
            await pilot.pause()
            app.refresh_data()
            await pilot.pause()
            steps = await open_view(pilot, app)
            self.assertIs(steps.state, state)
            self.assertFalse(state.follow)
            self.assertEqual((state.cursor, state.top), (cursor, top))
            self.assertIn("paused", steps.header_text())

    async def test_first_open_follows(self):
        app = make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(3):
                app.refresh_data()
                await pilot.pause(0.05)
            self.assertEqual(app.views, {})
            steps = await open_view(pilot, app, downs=1)
            for _ in range(3):
                app.refresh_data()
                await pilot.pause(0.05)
            self.assertTrue(app.views[fx.CHECKER].follow)
            self.assertIn("follow", steps.header_text())

    async def test_last_resumes_follow(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            steps = await open_view(pilot, app)
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertFalse(steps.state.follow)
            await pilot.press("end")
            await pilot.pause()
            self.assertTrue(steps.state.follow)
            self.assertIn("follow", steps.header_text())

    async def test_view_target_vanishes(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await open_view(pilot, app, downs=1)  # Checker
            rows = app.agents.rows

            def without_checker(now):
                return [(name, [r for r in group if r["id"] != fx.CHECKER]) for name, group in rows(now=now)]

            with mock.patch.object(app.agents, "rows", side_effect=without_checker):
                app.refresh_data()
                await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertIsNone(find(tree.root, fx.CHECKER))
            self.assertIs(app.focused, tree)
            await open_view(pilot, app)
            app.resolver.root = "other-root"  # the pane's conversation switched
            app.refresh_data()
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertEqual(app.views, {})
            app.refresh_data()
            await pilot.pause()
            self.assertIsNone(app.return_value)

    async def test_refresh_in_view_follows(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            count = {"n": 100}
            with mock.patch.object(app.agents, "items", side_effect=lambda row: steps_items(count["n"])):
                steps = await open_view(pilot, app, downs=1)
                await pilot.pause()
                self.assertTrue(steps.state.follow)
                bottom = steps.scroll_y
                self.assertGreater(bottom, 0)
                count["n"] = 120
                app.refresh_data()
                await pilot.pause()
            self.assertEqual(len(steps.items), 120)
            self.assertGreater(steps.scroll_y, bottom)
            self.assertEqual(steps.scroll_y, steps.max_scroll_y)
            self.assertTrue(steps.state.follow)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class AppKeysTest(unittest.IsolatedAsyncioTestCase):
    async def test_q_in_list_exits_true(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("q")
            await pilot.pause()
        self.assertIs(app.return_value, True)

    async def test_q_in_view_goes_back(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await open_view(pilot, app)
            await pilot.press("q")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertIsNone(app.return_value)
            await pilot.press("q")
            await pilot.pause()
        self.assertIs(app.return_value, True)

    async def test_ctrl_c_returns_false(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("ctrl+c")
            await pilot.pause()
        self.assertIs(app.return_value, False)

    async def test_ctrl_c_exits_false_from_view(self):
        for key in ("ctrl+c", "ctrl+q"):
            app = make_app()
            async with app.run_test(size=SIZE) as pilot:
                await start(pilot, app)
                await open_view(pilot, app)
                await pilot.press(key)
                await pilot.pause()
            self.assertIs(app.return_value, False, key)

    async def test_ctrl_q_returns_false(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("ctrl+q")
            await pilot.pause()
        self.assertIs(app.return_value, False)

    async def test_emacs_keys_move_tree(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.press("ctrl+n")
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.CHECKER)
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.PLANNER)
            self.assertIs(app.focused, tree)

    async def test_emacs_tab_opens(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            steps = await open_view(pilot, app, downs=1, key="tab")
            self.assertIsInstance(app.screen, ViewScreen)
            self.assertEqual(steps.row["id"], fx.CHECKER)
            self.assertIs(app.focused, steps)

    async def test_vim_preset_keys(self):
        app = make_app(settings=dict(SETTINGS, keymap="vim"))
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.press("j")
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.CHECKER)
            await pilot.press("k")
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.PLANNER)
            await pilot.press("G")
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            await pilot.press("space")
            await pilot.pause()
            self.assertEqual(app.screen.row_id, fx.REVIEWER)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)

    async def test_user_keys_override(self):
        app = make_app(settings=dict(SETTINGS, keys={"down": ["J"]}))
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.press("J")
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.CHECKER)

    async def test_pager_from_view(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await open_view(pilot, app, downs=1)  # Checker
            suspend = mock.Mock(return_value=contextlib.nullcontext())
            with mock.patch.object(app_mod.pager, "show", return_value="pager failed") as show, \
                    mock.patch.object(app_mod.AgyscopeApp, "suspend", suspend):
                await pilot.press("o")
                await pilot.pause()
            suspend.assert_called_once_with()
            row = app.rows[fx.CHECKER]
            show.assert_called_once_with(["less"], textview.plain(app.agents, row), "Checker")
            self.assertIsInstance(app.screen, ViewScreen)
            status = app.screen.query_one("#status")
            self.assertTrue(status.display)
            self.assertEqual(text_of(status), " ! pager failed")
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(text_of(app.query_one("#status")), " ! pager failed")


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class RunTest(unittest.TestCase):
    def setup_mock(self):
        return mock.Mock(on_close=mock.Mock(), settings=SETTINGS, warnings=[])

    def run_app(self, result):
        setup = self.setup_mock()
        with mock.patch.object(app_mod.AgyscopeApp, "run", return_value=result):
            code = app_mod.run(setup)
        return code, setup.on_close

    def test_true_calls_on_close(self):
        code, on_close = self.run_app(True)
        self.assertEqual(code, 0)
        on_close.assert_called_once_with()

    def test_false_returns_one(self):
        code, on_close = self.run_app(None)
        self.assertEqual(code, 1)
        on_close.assert_not_called()

    def test_on_close_only_after_close(self):
        for result, calls in ((True, 1), (False, 0)):
            code, on_close = self.run_app(result)
            self.assertEqual(code, 0 if result else 1)
            self.assertEqual(on_close.call_count, calls)

    def test_no_on_close(self):
        setup = mock.Mock(on_close=None, settings=SETTINGS, warnings=[])
        with mock.patch.object(app_mod.AgyscopeApp, "run", return_value=True):
            self.assertEqual(app_mod.run(setup), 0)

    def test_crash_returns_two(self):
        # Textual sets return_code 1 and run() returns None on an uncaught exception; a crash must
        # not look like ctrl+c (1), so cli.main keeps the traceback on screen.
        def crashed_run(app):
            app._return_code = 1

        setup = self.setup_mock()
        with mock.patch.object(app_mod.AgyscopeApp, "run", autospec=True, side_effect=crashed_run):
            self.assertEqual(app_mod.run(setup), 2)
        setup.on_close.assert_not_called()

    def test_exceptions_propagate(self):
        setup = self.setup_mock()
        with mock.patch.object(app_mod.AgyscopeApp, "run", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                app_mod.run(setup)


class StartupCrashTest(unittest.TestCase):
    """A broken venv (textual fails to import) must exit 2 with the traceback, not 1 like ctrl+c."""

    def test_import_error_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("rich", "textual"):
                os.mkdir(os.path.join(tmp, name))
                with open(os.path.join(tmp, name, "__init__.py"), "w") as f:
                    f.write("raise ImportError('half-built venv')\n")
            env = dict(os.environ, PYTHONPATH=tmp)
            proc = subprocess.run([sys.executable, "-m", "agyscope", "--app-dir", tmp, "--conversation", "x"],
                                  cwd=tmp, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("Traceback", proc.stderr)
        self.assertIn("half-built venv", proc.stderr)


if __name__ == "__main__":
    unittest.main()
