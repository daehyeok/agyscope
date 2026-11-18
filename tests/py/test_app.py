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
from agyscope.links import LinkStore
from agyscope.sources import Root

try:
    import textual  # noqa: F401
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False

if HAVE_TEXTUAL:
    # outside the try: a missing app module must fail, not skip
    from agyscope import app as app_mod
    from agyscope.app import AgyscopeApp, ListScreen, ViewScreen
    from textual.color import Color
    from agyscope.widgets import (MARQUEE_PAUSE_TICKS, StepView, SubagentTree, header_columns,
                                  marquee_span, marquee_window, text_width)

SIZE = (120, 30)
NOW = time.time()
SETTINGS = {"keymap": "emacs", "keys": {}, "pager": ["less"], "main_rows": False}


class FakeSources:
    """Stands in for sources.Collector: `root` is the pane's conversation, `extra` more roots."""

    def __init__(self, root, app_dir):
        self.root = root
        self.app_dir = app_dir
        self.extra = []
        self.pokes = 0

    @property
    def roots(self):
        out = [Root(self.root, self.app_dir, "pane", True)] if self.root else []
        return tuple(out + list(self.extra))

    def initial(self):
        return self.roots

    def poke(self):
        self.pokes += 1


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


def make_app(root=fx.ROOT, settings=SETTINGS, warnings=(), fixture=True, themes_dir=None):
    app_dir = tempfile.mkdtemp()
    if fixture:
        fx.build_tree(app_dir)
    store = LinkStore(os.path.join(tempfile.mkdtemp(), "agyscope"))
    return AgyscopeApp(discovery.Forest(app_dir, links=store), FakeSources(root, app_dir), settings,
                       warnings=warnings, clock=lambda: NOW, themes_dir=themes_dir)


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


async def wait_until(pilot, cond, timeout=5.0):
    """Pauses until cond() holds or `timeout` seconds pass; returns cond()."""
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        await pilot.pause(0.02)
    return cond()


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
            self.assertEqual(len(app.query("#listhint")), 0)  # keys live in the ? help dialog now
            head = text_of(app.query_one("#colhead"))
            self.assertTrue(head.startswith("Subagent "))
            for title in ("State", "Model", "Age", "Last step"):
                self.assertIn(title, head)
            self.assertEqual(head, header_columns(tree.scrollable_content_region.width).plain)  # lines up with rows

    async def test_root_switch_replaces_pane_tree(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            tree.action("last")  # Reviewer
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            app.sources.root = "other-root"
            app.refresh_data()
            await pilot.pause()
            self.assertEqual(app.agents.root, "other-root")
            self.assertIsNone(find(tree.root, fx.PLANNER))
            self.assertEqual(shape(tree.root), [])
            self.assertEqual(text_of(app.query_one("#empty")), " " + StepView.NO_SUBAGENTS)
            app.sources.root = fx.ROOT
            app.refresh_data()
            await pilot.pause()
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)
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

    async def test_drag_resizes_tree_and_header(self):
        from textual.geometry import Region
        from agyscope.transcript import col_width
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.mouse_down("#colhead", offset=(24, 0))
            await pilot.hover("#colhead", offset=(34, 0))
            await pilot.mouse_up("#colhead", offset=(34, 0))
            await pilot.pause()
            self.assertEqual(tree.cols["role"], 34)
            head = text_of(app.query_one("#colhead"))
            self.assertEqual(head.index("State"), 35)
            lines = [s.text for s in tree.render_lines(Region(0, 0, tree.size.width, tree.size.height))]
            reviewer = next(line for line in lines if "Reviewer" in line)
            self.assertEqual(col_width(reviewer[:reviewer.index("done")]), 35)

    async def test_open_view_mid_drag_releases_mouse(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            colhead = app.list_screen.query_one("#colhead")
            await pilot.mouse_down("#colhead", offset=(24, 0))
            self.assertIsNotNone(app.mouse_captured)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)
            self.assertIsNone(app.mouse_captured)
            self.assertIsNone(colhead._drag)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class ThemeTest(unittest.IsolatedAsyncioTestCase):
    async def test_theme_applied_at_mount(self):
        import rich.color
        app = make_app(settings=dict(SETTINGS, theme="nord"))
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            self.assertEqual(app.theme, "nord")
            # nord success is #A3BE8C
            self.assertEqual(app.state_styles["done"].color, rich.color.Color.parse("#A3BE8C"))
            tree = app.list_screen.query_one("#tree")
            self.assertEqual(tree.state_styles["done"], app.state_styles["done"])  # not the fallback
            self.assertEqual(tree.state_styles["workflow"], app.state_styles["workflow"])

    async def test_unknown_theme_warns(self):
        app = make_app(settings=dict(SETTINGS, theme="nope"))
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            self.assertEqual(app.theme, "textual-dark")
            status = app.query_one("#status")
            self.assertTrue(status.display)
            self.assertIn("theme 'nope'", text_of(status))

    async def test_user_theme_registered(self):
        import rich.color
        tdir = tempfile.mkdtemp()
        with open(os.path.join(tdir, "mine.toml"), "w", encoding="utf-8") as f:
            f.write('primary = "#010203"\nsuccess = "#0a0b0c"\n')
        app = make_app(settings=dict(SETTINGS, theme="mine"), themes_dir=tdir)
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            self.assertEqual(app.theme, "mine")
            self.assertEqual(app.state_styles["done"].color, rich.color.Color.parse("#0a0b0c"))

    async def test_bad_user_theme_warns(self):
        tdir = tempfile.mkdtemp()
        with open(os.path.join(tdir, "bad.toml"), "w", encoding="utf-8") as f:
            f.write("primary = \n")
        app = make_app(themes_dir=tdir)
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            status = app.query_one("#status")
            self.assertTrue(status.display)
            self.assertIn("bad.toml", text_of(status))

    async def test_bad_color_user_theme_does_not_crash(self):
        tdir = tempfile.mkdtemp()
        with open(os.path.join(tdir, "bad.toml"), "w", encoding="utf-8") as f:
            f.write('primary = "not-a-color"\n')
        app = make_app(settings=dict(SETTINGS, theme="bad"), themes_dir=tdir)
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            self.assertEqual(app.theme, "textual-dark")
            status = app.query_one("#status")
            self.assertTrue(status.display)
            msg = text_of(status)
            self.assertTrue("theme 'bad'" in msg or "skipped" in msg, msg)

    async def test_theme_switch_relabels(self):
        import rich.color
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            app.theme = "nord"
            await pilot.pause()
            await pilot.pause()
            reviewer = find(tree.root, fx.REVIEWER)
            nord_done = rich.color.Color.parse("#A3BE8C")
            self.assertIn(nord_done, [span.style.color for span in reviewer.label.spans if span.style])


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class MarqueeAppTest(unittest.IsolatedAsyncioTestCase):
    """Marquee timing is driven deterministically by the test, not the wall clock."""

    def setUp(self):
        for name in ("REFRESH_S", "MARQUEE_STEP_S"):
            patcher = mock.patch.object(app_mod, name, 3600.0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _with_texts(self, app, **by_id):
        app.agents.update(app.sources.roots)
        orig = app.agents.rows(now=NOW, show_main=False)
        return [(name, [dict(r, text=by_id[r["id"]]) if r["id"] in by_id else r for r in grp])
                for name, grp in orig]

    async def test_only_cursor_animates_pause_advance_end_pause_wrap(self):
        app = make_app()
        async with app.run_test(size=(100, 12)) as pilot:
            w = text_width(98)  # SubagentTree reserves 2 cells for scrollbar_gutter: stable
            long_p = "".join(chr(ord("a") + (i % 26)) for i in range(w + 4))
            long_c = "".join(chr(ord("A") + (i % 26)) for i in range(w + 4))
            groups = self._with_texts(app, **{fx.PLANNER: long_p, fx.CHECKER: long_c})
            with mock.patch.object(app.agents, "rows", return_value=groups):
                tree = await start(pilot, app)
            self.assertEqual(text_width(tree.scrollable_content_region.width, tree.cols), w)
            planner = find(tree.root, fx.PLANNER)
            checker = find(tree.root, fx.CHECKER)
            self.assertTrue(planner.label.plain.endswith("…"))
            self.assertTrue(checker.label.plain.endswith("…"))

            for _ in range(MARQUEE_PAUSE_TICKS - 1):
                app.list_screen.tick_marquee()
            self.assertTrue(planner.label.plain.endswith("…"))

            for off in range(1, 5):
                app.list_screen.tick_marquee()
                self.assertTrue(planner.label.plain.endswith(long_p[off:off + w]))
                self.assertNotIn("…", planner.label.plain)
                self.assertTrue(checker.label.plain.endswith("…"))

            for _ in range(MARQUEE_PAUSE_TICKS - 1):
                app.list_screen.tick_marquee()
            self.assertTrue(planner.label.plain.endswith(long_p[4:4 + w]))

            app.list_screen.tick_marquee()  # wrap back to 0
            self.assertTrue(planner.label.plain.endswith("…"))

    async def test_resets_on_cursor_move_and_text_change_and_keeps_across_refresh(self):
        app = make_app()
        async with app.run_test(size=(100, 12)) as pilot:
            w = text_width(100)
            long_p = "0123456789" + "p" * w
            long_c = "ABCDEFGHIJ" + "c" * w
            groups = self._with_texts(app, **{fx.PLANNER: long_p, fx.CHECKER: long_c})
            with mock.patch.object(app.agents, "rows", return_value=groups):
                tree = await start(pilot, app)
                w = text_width(tree.scrollable_content_region.width, tree.cols)
                planner = find(tree.root, fx.PLANNER)
                for _ in range(MARQUEE_PAUSE_TICKS + 1):
                    app.list_screen.tick_marquee()
                self.assertEqual(tree._marquee_offset, 2)
                self.assertTrue(planner.label.plain.endswith(long_p[2:2 + w]))

                # Same row, same text on refresh keeps offset
                app.refresh_data()
                self.assertEqual(tree._marquee_offset, 2)
                self.assertTrue(planner.label.plain.endswith(long_p[2:2 + w]))

                # Moving cursor resets old row back to "…" and resets offset
                await pilot.press("ctrl+n")
                await pilot.pause()
                self.assertTrue(planner.label.plain.endswith("…"))
                self.assertEqual(tree._marquee_offset, 0)

                # Move back, advance again, then change text on refresh -> resets to 0
                await pilot.press("ctrl+p")
                await pilot.pause()
                for _ in range(MARQUEE_PAUSE_TICKS + 1):
                    app.list_screen.tick_marquee()
                self.assertEqual(tree._marquee_offset, 2)

            updated = self._with_texts(app, **{fx.PLANNER: "UPDATED000" + "u" * w, fx.CHECKER: long_c})
            with mock.patch.object(app.agents, "rows", return_value=updated):
                app.refresh_data()
            self.assertEqual(tree._marquee_offset, 0)
            self.assertTrue(planner.label.plain.endswith("…"))

            # Dragging a column border resets marquee offset to 0
            with mock.patch.object(app.agents, "rows", return_value=updated):
                for _ in range(MARQUEE_PAUSE_TICKS + 1):
                    app.list_screen.tick_marquee()
                self.assertEqual(tree._marquee_offset, 2)
                await pilot.mouse_down("#colhead", offset=(24, 0))
                await pilot.hover("#colhead", offset=(28, 0))
                await pilot.mouse_up("#colhead", offset=(28, 0))
                await pilot.pause()
                self.assertEqual(tree._marquee_offset, 0)

    async def test_short_text_does_nothing(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            planner = find(tree.root, fx.PLANNER)
            before = planner.label
            with mock.patch.object(planner, "set_label", wraps=planner.set_label) as spy:
                for _ in range(MARQUEE_PAUSE_TICKS + 5):
                    app.list_screen.tick_marquee()
                self.assertEqual(spy.call_count, 0)
                self.assertEqual(planner.label, before)

    async def test_marquee_disabled(self):
        app = make_app(settings=dict(SETTINGS, marquee=False))
        async with app.run_test(size=(100, 12)) as pilot:
            w = text_width(100)
            long_p = "0123456789" + "x" * w
            groups = self._with_texts(app, **{fx.PLANNER: long_p})
            with mock.patch.object(app.agents, "rows", return_value=groups):
                tree = await start(pilot, app)
            planner = find(tree.root, fx.PLANNER)
            for _ in range(MARQUEE_PAUSE_TICKS + 5):
                app.list_screen.tick_marquee()
            self.assertEqual(tree._marquee_offset, 0)
            self.assertTrue(planner.label.plain.endswith("…"))

    async def test_cjk_marquee_in_app(self):
        app = make_app()
        async with app.run_test(size=(100, 12)) as pilot:
            cjk = "가나다라마바사아자차카타파하" * 3
            groups = self._with_texts(app, **{fx.PLANNER: cjk})
            with mock.patch.object(app.agents, "rows", return_value=groups):
                tree = await start(pilot, app)
            w = text_width(tree.scrollable_content_region.width, tree.cols)
            span = marquee_span(cjk, w)
            planner = find(tree.root, fx.PLANNER)
            for _ in range(MARQUEE_PAUSE_TICKS):
                app.list_screen.tick_marquee()
            self.assertEqual(tree._marquee_offset, 1)
            self.assertTrue(planner.label.plain.endswith(marquee_window(cjk, w, 1)))
            for _ in range(span - 1):
                app.list_screen.tick_marquee()
            self.assertEqual(tree._marquee_offset, span)
            self.assertTrue(planner.label.plain.endswith(marquee_window(cjk, w, span)))

    async def test_marquee_timer_fires(self):
        app = make_app()
        w = text_width(100)
        long_p = "0123456789" + "t" * w
        groups = self._with_texts(app, **{fx.PLANNER: long_p})
        with mock.patch.object(app_mod, "MARQUEE_STEP_S", 0.01), \
                mock.patch.object(app.agents, "rows", return_value=groups):
            async with app.run_test(size=(100, 12)) as pilot:
                tree = await start(pilot, app)
                planner = find(tree.root, fx.PLANNER)
                self.assertTrue(await wait_until(pilot, lambda: tree._marquee_offset > 0))
                self.assertNotIn("…", planner.label.plain)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class StatusFilterTest(unittest.IsolatedAsyncioTestCase):
    # Fixture: Planner (waiting) > Checker (working); Reviewer (done). FILTER_STATES order:
    # 0 working, 1 waiting, 2 quiet, 3 done, 4 error.

    async def open_filter(self, pilot, app):
        await pilot.press("f")
        await pilot.pause()
        self.assertIsInstance(app.screen, app_mod.StatusFilterScreen)
        return app.screen.query_one("#filterlist", app_mod.SelectionList)

    async def toggle(self, pilot, sel, index):
        sel.highlighted = index
        await pilot.press("space")
        await pilot.pause()

    async def test_pick_two_states_and_apply(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            sel = await self.open_filter(pilot, app)
            self.assertEqual(list(sel.selected), [])
            await pilot.press("space")  # the first state, working, is highlighted on open
            await self.toggle(pilot, sel, 3)  # done
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertEqual(app.status_filter, {"working", "done"})
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)  # Planner kept as Checker's parent
            self.assertTrue(tree._rows[fx.PLANNER].get("context"))
            self.assertFalse(tree._rows[fx.CHECKER].get("context"))
            bar = app.query_one("#filterbar")
            self.assertTrue(bar.display)
            self.assertEqual(text_of(bar), " filter: working, done")

    async def test_reopen_shows_current_and_cancel_keeps(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            app._filtered({"done"})
            await pilot.pause()
            self.assertEqual(shape(tree.root), [("Completed", [(fx.REVIEWER, [])])])
            sel = await self.open_filter(pilot, app)
            self.assertEqual(list(sel.selected), ["done"])
            await self.toggle(pilot, sel, 4)  # error, then cancel
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertEqual(app.status_filter, {"done"})

    async def test_all_or_none_clears(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            app._filtered({"done"})
            await pilot.pause()
            sel = await self.open_filter(pilot, app)
            await self.toggle(pilot, sel, 3)  # uncheck done
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.status_filter, set())
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)
            self.assertFalse(app.query_one("#filterbar").display)
            self.assertFalse(any(r.get("context") for r in tree._rows.values()))
            app._filtered(set(discovery.FILTER_STATES))
            self.assertEqual(app.status_filter, set())

    async def test_no_rows_match_hides_conversation(self):
        app = make_app(settings=dict(SETTINGS, main_rows=True))
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            app._filtered({"error"})
            await pilot.pause()
            self.assertEqual(shape(tree.root), [])  # nothing in the conversation's tree is error
            self.assertEqual(text_of(app.query_one("#filterbar")), " filter: error \u2014 no rows match filter")
            self.assertFalse(app.query_one("#empty").display)  # the bar says it; no second empty line

    async def test_conversation_rows_filtered_by_state(self):
        app = make_app(settings=dict(SETTINGS, main_rows=True))
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            main_state = app.rows[fx.ROOT]["state"]
            app._filtered({"working"})  # Checker is working: conversation and Planner kept dimmed
            await pilot.pause()
            self.assertEqual(shape(tree.root), [("Working", [(fx.ROOT, [(fx.PLANNER, [(fx.CHECKER, [])])])])])
            self.assertEqual(bool(tree._rows[fx.ROOT].get("context")), main_state != "working")
            self.assertTrue(tree._rows[fx.PLANNER].get("context"))
            app._filtered({main_state})  # the conversation row matches itself
            await pilot.pause()
            self.assertFalse(tree._rows[fx.ROOT].get("context"))

    async def test_shift_a_toggles_all(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            sel = await self.open_filter(pilot, app)
            await pilot.press("space")  # one checked: A checks the rest
            await pilot.press("A")
            await pilot.pause()
            self.assertEqual(list(sel.selected), list(discovery.FILTER_STATES))
            await pilot.press("a")  # lowercase works too; all checked: unchecks all
            await pilot.pause()
            self.assertEqual(list(sel.selected), [])
            await pilot.press("A")
            await self.toggle(pilot, sel, 4)  # all but error
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.status_filter, {"working", "waiting", "quiet", "done"})

    async def test_unchecked_box_visible(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            sel = await self.open_filter(pilot, app)
            style = sel.get_component_rich_style("selection-list--button")
            textual_default = Color.parse(app.get_css_variables()["panel-darken-2"]).rich_color
            self.assertNotEqual(style.color, textual_default)  # Textual's near-invisible X replaced
            self.assertNotEqual(style.color, style.bgcolor)

    async def test_filter_key_does_nothing_in_view(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await open_view(pilot, app, downs=1)
            await pilot.press("f")
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)

    async def test_view_stays_open_when_row_filtered_out(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await open_view(pilot, app, downs=1)  # Checker (working)
            app._filtered({"done"})
            await pilot.pause()
            app.refresh_data()
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)
            self.assertEqual(app.screen.row_id, fx.CHECKER)
            self.assertIsNone(find(tree.root, fx.CHECKER))

    async def test_session_only(self):
        app = make_app()
        self.assertEqual(app.status_filter, set())
        self.assertNotIn("status_filter", app.settings)


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
            self.assertEqual(len(app.screen.query("#hints")), 0)  # keys live in the ? help dialog now

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

            def without_checker(now, show_main=True):
                return [(name, [r for r in group if r["id"] != fx.CHECKER])
                        for name, group in rows(now=now, show_main=show_main)]

            with mock.patch.object(app.agents, "rows", side_effect=without_checker):
                app.refresh_data()
                await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertIsNone(find(tree.root, fx.CHECKER))
            self.assertIs(app.focused, tree)
            await open_view(pilot, app)
            app.sources.root = "other-root"  # the pane's conversation switched
            app.refresh_data()
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
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


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class HelpDialogTest(unittest.IsolatedAsyncioTestCase):
    async def test_question_mark_opens_help_on_both_screens(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, app_mod.HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            await open_view(pilot, app)
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, app_mod.HelpScreen)
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)

    async def test_user_keys_listed(self):
        app = make_app(settings=dict(SETTINGS, keys={"down": ["J"]}))
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("question_mark")
            await pilot.pause()
            body = str(app.screen.query_one("#helpbody").content)
            line = next(l for l in body.splitlines() if l.split()[:1] == ["down"])
            for key in ("Down", "C-n", "J"):
                self.assertIn(key, line)
            self.assertIn("Mouse", body)
            self.assertRegex(body, r"help\s+\?\s+both")

    async def test_q_closes_only_the_dialog(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.press("question_mark")
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertIsNone(app.return_code)  # still running

    async def test_keys_do_not_leak_to_list(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.press("question_mark")
            await pilot.pause()
            await pilot.press("ctrl+n")
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.PLANNER)

    async def test_close_returns_to_paused_view_unchanged(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            steps = await open_view(pilot, app)
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertFalse(steps.state.follow)
            before = (steps.state.cursor, steps.state.top)
            await pilot.press("question_mark")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)
            self.assertFalse(steps.state.follow)
            self.assertEqual((steps.state.cursor, steps.state.top), before)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TreeMouseTest(unittest.IsolatedAsyncioTestCase):
    # Rows with main_rows off: 0 Working, 1 Planner (has Checker), 2 Checker, 3 Completed, 4 Reviewer.
    async def test_double_click_leaf_opens_view(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await start(pilot, app)
            await pilot.double_click("#tree", offset=(20, 4))
            await pilot.pause()
            self.assertIsInstance(app.screen, ViewScreen)
            self.assertEqual(app.screen.row_id, fx.REVIEWER)

    async def test_double_click_parent_folds(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.double_click("#tree", offset=(20, 1))
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertFalse(find(tree.root, fx.PLANNER).is_expanded)
            await pilot.double_click("#tree", offset=(20, 1))
            await pilot.pause()
            self.assertTrue(find(tree.root, fx.PLANNER).is_expanded)

    async def test_double_click_group_folds(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.double_click("#tree", offset=(6, 3))
            await pilot.pause()
            self.assertFalse(tree.root.children[1].is_expanded)  # Completed

    async def test_single_click_only_highlights(self):
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            await pilot.click("#tree", offset=(20, 4))
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            await pilot.click("#tree", offset=(20, 1))
            await pilot.pause()
            self.assertIsInstance(app.screen, ListScreen)
            self.assertTrue(find(tree.root, fx.PLANNER).is_expanded)

    async def test_tree_fold_setting_starts_folded(self):
        app = make_app(settings=dict(SETTINGS, tree_fold="fold"))
        async with app.run_test(size=SIZE) as pilot:
            tree = await start(pilot, app)
            self.assertFalse(tree.root.children[0].is_expanded)  # Working
            self.assertFalse(find(tree.root, fx.PLANNER).is_expanded)

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
