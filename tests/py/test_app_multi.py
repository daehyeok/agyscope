"""Panel behaviour with several root conversations: main rows, pane switch, picker, detach."""

import os
import sqlite3
import tempfile
import unittest

import helpers  # noqa: F401
import make_fixtures as fx

try:
    import textual  # noqa: F401
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False

if HAVE_TEXTUAL:
    from agyscope.app import ListScreen, PickerScreen, ViewScreen
    from agyscope.sources import Root
    from agyscope.widgets import SubagentTree
    from test_app import SETTINGS, SIZE, find, make_app, shape, text_of

OTHER = "other-0000"
W1 = "b029292c-7ebe-417c-b9a7-522ae4b576d1"
MAIN = dict(SETTINGS, main_rows=True)


def worker(app, conv, sender="user"):
    fx.write(app, conv, [
        fx.system("<SYSTEM_MESSAGE>sender=%s content=go</SYSTEM_MESSAGE>" % sender, at="2026-01-01T00:10:00Z"),
        fx.planner(calls=[("run_command", {"CommandLine": "make", "toolSummary": "build"})]),
        fx.generic("ok"), fx.planner(content="Done."),
    ])


def summaries(app, rows):
    con = sqlite3.connect(os.path.join(app, "conversation_summaries.db"))
    con.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, agent_name text, "
                "last_modified_time datetime, parent_conversation_id text, raw_summary blob)")
    con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,NULL)", rows)
    con.commit()
    con.close()


async def start(pilot, app):
    app.refresh_data()
    await pilot.pause()
    return app.query_one("#tree", SubagentTree)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class MultiRootTest(unittest.IsolatedAsyncioTestCase):
    def two_roots(self, settings=MAIN):
        app = make_app(settings=settings)
        worker(app.sources.app_dir, OTHER)
        app.sources.extra = [Root(OTHER, app.sources.app_dir, "watch")]
        return app

    async def test_main_rows_two_roots(self):
        app = self.two_roots()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            self.assertEqual(shape(tree.root), [
                ("Working", [(fx.ROOT, [(fx.REVIEWER, []), (fx.PLANNER, [(fx.CHECKER, [])])])]),
                ("Completed", [(OTHER, [])]),
            ])
            self.assertEqual(tree.selected_id(), fx.ROOT)
            await pilot.press("enter")  # opens the main conversation's own steps
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)
            self.assertEqual(app.screen.row_id, fx.ROOT)
            titles = [it["title"] for it in app.agents.items(app.rows[fx.ROOT])]
            self.assertEqual(titles[0], "Message from user")
            self.assertIn("Started subagent Planner", titles)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            app.open_view(OTHER)
            await pilot.pause()
            self.assertEqual(app.screen.row_id, OTHER)

    async def test_pane_switch_keeps_other_root_and_its_view(self):
        app = self.two_roots()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            app.open_view(OTHER)
            await pilot.pause()
            other_tree = app.agents.trees[(OTHER, app.sources.app_dir)]
            app.sources.root = "pane-2"
            app.refresh_data()
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)  # still open
            self.assertEqual(app.screen.row_id, OTHER)
            self.assertIs(app.agents.trees[(OTHER, app.sources.app_dir)], other_tree)

    async def test_toggle_main(self):
        app = self.two_roots()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            self.assertIsNotNone(find(tree.root, fx.ROOT))
            await pilot.press("m")
            await pilot.pause()
            self.assertIsNone(find(tree.root, fx.ROOT))
            self.assertIsNotNone(find(tree.root, fx.PLANNER))
            await pilot.press("m")
            await pilot.pause()
            self.assertIsNotNone(find(tree.root, fx.ROOT))
            await pilot.press("question_mark")
            await pilot.pause()
            help_body = str(app.screen.query_one("#helpbody").content)
            self.assertRegex(help_body, r"toggle_main\s+m\s+list")

    async def test_picker_filters_attaches_under_and_esc(self):
        app = make_app(settings=MAIN)
        data = app.sources.app_dir
        worker(data, W1, sender="nobody")
        summaries(data, [(W1, "", "gpowers-implementer", "2026-01-01 00:10:00+00:00", ""),
                         (OTHER, "fix emacs server", "", "2026-01-01 00:05:00+00:00", "")])
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("A")  # attach under the highlighted (main) conversation
            await pilot.pause()
            self.assertIsInstance(app.screen, PickerScreen)
            await pilot.press(*"implementer")
            await pilot.pause()
            self.assertEqual([c["conv"] for c in app.screen.shown], [W1])
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertEqual(app.agents.links.children(fx.ROOT), [W1])
            self.assertEqual(app.sources.pokes, 1)
            self.assertTrue(app.rows[W1]["linked"])
            self.assertEqual(app.rows[W1]["parent"], fx.ROOT)
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("q")  # typed into the filter, doesn't close the panel
            await pilot.pause()
            self.assertIsInstance(app.screen, PickerScreen)
            self.assertEqual(app.screen.query_one("#pickquery").value, "q")
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertEqual(app.agents.links.top(), [])
            self.assertIsNone(app.return_value)

    async def test_picker_attach_top_level_with_data_dir(self):
        app = make_app(settings=MAIN)
        data = app.sources.app_dir
        summaries(data, [(OTHER, "fix emacs server", "", "2026-01-01 00:05:00+00:00", "")])
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("a")
            await pilot.pause()
            await pilot.press(*"emacs", "enter")
            await pilot.pause()
            self.assertEqual(app.agents.links.top(), [(OTHER, data)])

    async def test_detach_branches(self):
        app = self.two_roots()
        links = app.agents.links
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            app.detach_row(fx.ROOT)  # the pane's own conversation
            await pilot.pause()
            self.assertIn("can't detach", text_of(app.query_one("#status")))
            self.assertEqual(links.entries(), [])
            app.detach_row(fx.PLANNER)
            app.detach_row(OTHER)
            await pilot.pause()
            self.assertEqual(links.hidden(fx.ROOT), {fx.PLANNER})
            self.assertEqual(links.hidden(None), {OTHER})
            self.assertNotIn(fx.PLANNER, app.rows)
            self.assertEqual(app.sources.pokes, 2)
            for path, _, files in os.walk(links.root):
                for f in files:
                    self.assertTrue(os.path.join(path, f).startswith(links.root))

    async def test_detach_key(self):
        app = self.two_roots()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.press("ctrl+n")  # Reviewer (newest subagent)
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            await pilot.press("d")
            await pilot.pause()
            self.assertEqual(app.agents.links.hidden(fx.ROOT), {fx.REVIEWER})
            self.assertIsNone(find(tree.root, fx.REVIEWER))


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class SourcesWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_collector_worker_publishes(self):
        from agyscope import discovery
        from agyscope.app import AgyscopeApp
        from agyscope.sources import Collector
        data = tempfile.mkdtemp()
        fx.build_tree(data)
        col = Collector(data, conversations=[fx.ROOT], lsof="/nonexistent/lsof", recheck=0.05)
        app = AgyscopeApp(discovery.Forest(data), col, MAIN)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            self.assertIn(fx.ROOT, app.rows)
            col.conversations.append("late-1")
            col.poke()
            await pilot.pause(0.3)
            self.assertEqual([r.conv for r in app.agents.roots], [fx.ROOT, "late-1"])
