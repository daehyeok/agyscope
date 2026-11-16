"""StepView (a Line API view of one subagent's steps) and SubagentTree (the subagent list)."""

import tempfile
import unittest

import helpers  # noqa: F401
import make_fixtures as fx
from agyscope import discovery
from agyscope.transcript import col_width
from agyscope.viewstate import ViewState

try:
    from textual import events
    from textual.app import App
    from textual.content import Content
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False

if HAVE_TEXTUAL:
    # outside the try: a missing widget must fail, not skip
    from textual.geometry import Region

    from agyscope.widgets import (COLS, MODEL_MIN, STYLES, TEXT_MIN, StepView, SubagentTree,
                                         header_columns, row_columns, visible_cols)

SIZE = (80, 12)
ROW = {"id": "c1", "role": "Final Review Fixer", "state": "done", "model": "gemini-3.8-flash-tiered"}


def item(key, body=0, running=False):
    return {"key": key, "depth": 0, "title": "t%d" % key, "style": "step", "running": running,
            "failed": False, "body": lambda: [("dim", "b%d.%d" % (key, i)) for i in range(body)]}


def items(n, **kw):
    return [item(k, **kw) for k in range(n)]


if HAVE_TEXTUAL:
    class Host(App):
        """Mounts one StepView, nothing else."""

        def compose(self):
            yield StepView()


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class StepViewTest(unittest.IsolatedAsyncioTestCase):
    async def test_header_model_badge(self):
        async with Host().run_test(size=SIZE) as pilot:
            view = pilot.app.query_one(StepView)
            view.show(ROW, items(3), ViewState())
            self.assertEqual(view.header_text(), "Final Review Fixer [done · gemini-3.8-flash-tiered]  ⏬ follow")
            self.assertEqual(Content.from_markup(view.border_title).plain, view.header_text())
            state = ViewState()
            state.follow = False
            view.show(dict(ROW, model="-"), items(3), state)
            self.assertEqual(view.header_text(), "Final Review Fixer [done]  ⏸ paused")
            self.assertEqual(Content.from_markup(view.border_title).plain, view.header_text())
            view.show(dict(ROW, model=""), items(3), state)
            self.assertEqual(view.header_text(), "Final Review Fixer [done]  ⏸ paused")
            self.assertEqual(Content.from_markup(view.border_title).plain, view.header_text())
            view.show(dict(ROW, role="Fix [b] thing", model="-"), items(3), state)
            self.assertEqual(Content.from_markup(view.border_title).plain, "Fix [b] thing [done]  ⏸ paused")

    async def test_no_inherited_bindings(self):
        self.assertEqual(StepView._merge_bindings().key_to_bindings, {})

    async def test_empty_text(self):
        async with Host().run_test(size=SIZE) as pilot:
            view = pilot.app.query_one(StepView)
            view.show(None, [], ViewState())
            self.assertEqual(view.render_line(0).text.strip(), "No subagents yet.")
            view.show(None, [], ViewState(), empty=StepView.NO_CONVERSATION)
            self.assertEqual(view.render_line(0).text.strip(), "No conversation found for this pane.")

    async def test_renders_visible_lines_only(self):
        async with Host().run_test(size=SIZE) as pilot:
            view = pilot.app.query_one(StepView)
            view.show(ROW, [item(0, body=500, running=True)], ViewState())
            await pilot.pause()
            self.assertEqual(view.virtual_size.height, 501)
            calls = []
            real = view.render_line

            def spy(y):
                calls.append(y)
                return real(y)

            view.render_line = spy
            view.refresh()
            await pilot.pause()
            self.assertTrue(calls)
            self.assertLessEqual(len(calls), view.size.height)
            self.assertIn("b0.499", real(view.scrollable_content_region.height - 1).text)

    async def test_wheel_scroll_pauses_follow(self):
        async with Host().run_test(size=SIZE) as pilot:
            view = pilot.app.query_one(StepView)
            state = ViewState()
            view.show(ROW, [item(0, body=500, running=True)], state)
            await pilot.pause()
            bottom = view.scroll_y
            self.assertGreater(bottom, 0)
            view.post_message(events.MouseScrollUp(view, 2, 2, 0, -1, 0, False, False, False))
            await pilot.pause()
            self.assertLess(view.scroll_y, bottom)
            self.assertFalse(state.follow)
            self.assertEqual(state.top, round(view.scroll_y))
            self.assertIn("⏸ paused", view.header_text())

    async def test_refresh_keeps_scroll(self):
        async with Host().run_test(size=SIZE) as pilot:
            view = pilot.app.query_one(StepView)
            state = ViewState()
            its = items(100)
            view.show(ROW, its, state)
            await pilot.pause()
            for _ in range(3):
                view.post_message(events.MouseScrollUp(view, 2, 2, 0, -1, 0, False, False, False))
            await pilot.pause()
            self.assertFalse(state.follow)
            y = view.scroll_y
            view.show(ROW, its + [item(100)], state)
            await pilot.pause()
            self.assertEqual(view.scroll_y, y)
            self.assertFalse(state.follow)

    async def test_click_title_toggles_fold(self):
        async with Host().run_test(size=SIZE) as pilot:
            view = pilot.app.query_one(StepView)
            state = ViewState()
            its = items(3, body=2)
            view.show(ROW, its, state)
            await pilot.pause()
            self.assertNotIn(1, state.folds)
            top_left = view.content_region.offset - view.region.offset
            await pilot.click(StepView, offset=(top_left.x + 2, top_left.y + 1))  # item 1's title
            self.assertEqual(state.cursor, 1)
            self.assertTrue(state.folds[1])
            self.assertFalse(state.follow)
            self.assertIn("b1.1", view.render_line(3).text)

    async def test_action_moves_cursor_and_highlights(self):
        async with Host().run_test(size=SIZE) as pilot:
            view = pilot.app.query_one(StepView)
            state = ViewState()
            view.show(ROW, items(3), state)
            view.action("up")
            self.assertEqual(state.cursor, 1)
            self.assertFalse(state.follow)
            self.assertTrue(list(view.render_line(1))[0].style.reverse)
            self.assertFalse(list(view.render_line(0))[0].style.reverse)


def fixture_groups():
    """discovery groups for the shared fixture: Working = Planner > Checker, Completed = Reviewer."""
    app = tempfile.mkdtemp()
    fx.build_tree(app)
    return discovery.Tree(app, fx.ROOT).rows()


def shape(node):
    """Nested [(group name or subagent id, children)] below `node`."""
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

if HAVE_TEXTUAL:
    class TreeHost(App):
        """Mounts one SubagentTree and records the messages it posts."""

        def __init__(self):
            super().__init__()
            self.posted = []

        def compose(self):
            yield SubagentTree()

        def on_subagent_tree_selected(self, message):
            self.posted.append(("selected", message.id))

        def on_subagent_tree_open_leaf(self, message):
            self.posted.append(("open", message.id))


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class SubagentTreeTest(unittest.IsolatedAsyncioTestCase):
    async def test_tree_structure_from_fixture(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            tree.update_groups(fixture_groups())
            await pilot.pause()
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)
            self.assertFalse(tree.show_root)
            self.assertTrue(all(g.is_expanded for g in tree.root.children))
            self.assertTrue(find(tree.root, fx.PLANNER).is_expanded)
            self.assertEqual(tree.selected_id(), fx.PLANNER)  # initial selection: first row
            self.assertIn(("selected", fx.PLANNER), pilot.app.posted)
            self.assertEqual(SubagentTree._merge_bindings().key_to_bindings, {})

    async def test_update_preserves_expansion_and_cursor(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            groups = fixture_groups()
            tree.update_groups(groups)
            tree.action("open")  # cursor on Planner: collapse it
            tree.action("last")  # Reviewer
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            changed = [(name, [dict(r, age="9m", text="fresh step") for r in rows]) for name, rows in groups]
            tree.update_groups(changed)
            await pilot.pause()
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)
            self.assertFalse(find(tree.root, fx.PLANNER).is_expanded)
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            self.assertIn("fresh step", find(tree.root, fx.REVIEWER).label.plain)
            self.assertIn("9m", find(tree.root, fx.REVIEWER).label.plain)

    async def test_group_move_keeps_state(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            (_, working), (_, completed) = groups = fixture_groups()
            tree.update_groups(groups)
            tree.action("open")  # collapse Planner, cursor stays on it
            await pilot.pause()
            moved = [("Working", []), ("Completed", [dict(r, state="done") for r in working] + completed)]
            tree.update_groups(moved)
            await pilot.pause()
            completed_shape = [(fx.PLANNER, [(fx.CHECKER, [])]), (fx.REVIEWER, [])]
            self.assertEqual(shape(tree.root), [("Completed", completed_shape)])  # empty Working hidden
            planner = find(tree.root, fx.PLANNER)
            self.assertFalse(planner.is_expanded)
            self.assertEqual(tree.selected_id(), fx.PLANNER)
            self.assertIs(tree.cursor_node, planner)
            self.assertIn("done", planner.label.plain)
            self.assertEqual([m for m in pilot.app.posted if m[0] == "selected"], [("selected", fx.PLANNER)])

    async def test_removed_row_moves_cursor_to_neighbor(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            (_, working), (_, completed) = groups = fixture_groups()
            tree.update_groups(groups)
            tree.action("down")  # Checker
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.CHECKER)
            tree.update_groups([("Working", working[:1]), ("Completed", completed)])
            await pilot.pause()
            self.assertIsNone(find(tree.root, fx.CHECKER))
            self.assertEqual(tree.selected_id(), fx.REVIEWER)  # next subagent below
            tree.update_groups([("Working", working[:1]), ("Completed", [])])
            await pilot.pause()
            self.assertEqual(tree.selected_id(), fx.PLANNER)  # nothing below: previous subagent
            self.assertEqual(pilot.app.posted[-1], ("selected", fx.PLANNER))

    async def test_empty_groups_hidden(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            tree.update_groups([("Working", []), ("Completed", [])])
            await pilot.pause()
            self.assertEqual(shape(tree.root), [])
            self.assertIsNone(tree.selected_id())
            (_, working), (_, completed) = fixture_groups()
            tree.update_groups([("Working", []), ("Completed", completed)])
            await pilot.pause()
            self.assertEqual(shape(tree.root), [("Completed", [(fx.REVIEWER, [])])])
            self.assertEqual(tree.selected_id(), fx.REVIEWER)

    async def test_group_reappears_in_order_with_its_expansion(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            (_, working), (_, completed) = groups = fixture_groups()
            tree.update_groups(groups)
            tree.action("first")  # the Working group header
            tree.action("open")  # collapse it
            tree.action("last")  # Reviewer
            await pilot.pause()
            tree.update_groups([("Working", []), ("Completed", completed)])
            await pilot.pause()
            self.assertEqual(shape(tree.root), [("Completed", [(fx.REVIEWER, [])])])
            tree.update_groups(groups)
            await pilot.pause()
            self.assertEqual(shape(tree.root), FIXTURE_SHAPE)  # Working back before Completed
            self.assertFalse(tree.root.children[0].is_expanded)  # prior collapse kept
            self.assertTrue(tree.root.children[1].is_expanded)
            self.assertEqual(tree.selected_id(), fx.REVIEWER)

    async def test_emptied_group_under_cursor_moves_cursor(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            (_, working), (_, completed) = groups = fixture_groups()
            tree.update_groups(groups)
            tree.action("first")  # the Working group header
            await pilot.pause()
            self.assertIsNone(tree.selected_id())
            tree.update_groups([("Working", []), ("Completed", completed)])
            await pilot.pause()
            self.assertEqual(shape(tree.root), [("Completed", [(fx.REVIEWER, [])])])
            self.assertEqual(tree.selected_id(), fx.REVIEWER)  # nearest remaining row
            self.assertEqual(pilot.app.posted[-1], ("selected", fx.REVIEWER))
            tree.action("last")
            tree.update_groups(groups)
            await pilot.pause()
            tree.update_groups([("Working", working), ("Completed", [])])
            await pilot.pause()
            self.assertEqual(shape(tree.root), [("Working", [(fx.PLANNER, [(fx.CHECKER, [])])])])
            self.assertEqual(tree.selected_id(), fx.CHECKER)  # nothing below: previous row

    async def test_orphan_attaches_to_group(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            (_, working), (_, completed) = fixture_groups()
            tree.update_groups([("Working", working[1:]), ("Completed", completed)])
            await pilot.pause()
            self.assertEqual(shape(tree.root), [("Working", [(fx.CHECKER, [])]),
                                                ("Completed", [(fx.REVIEWER, [])])])

    async def test_actions(self):
        async with TreeHost().run_test(size=SIZE) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            tree.update_groups(fixture_groups())
            await pilot.pause()
            tree.action("down")  # Checker (leaf)
            tree.action("open")
            await pilot.pause()
            self.assertEqual(pilot.app.posted[-1], ("open", fx.CHECKER))
            tree.action("back")  # leaf: go to parent
            self.assertEqual(tree.selected_id(), fx.PLANNER)
            tree.action("back")  # expanded: collapse
            self.assertFalse(find(tree.root, fx.PLANNER).is_expanded)
            self.assertEqual(tree.selected_id(), fx.PLANNER)
            tree.action("open")  # collapsed: expand
            self.assertTrue(find(tree.root, fx.PLANNER).is_expanded)
            tree.action("first")
            self.assertIsNone(tree.selected_id())  # the Working group header
            tree.action("last")
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            tree.action("up")
            tree.action("page_up")
            self.assertEqual(tree.cursor_line, 0)
            tree.action("page_down")
            self.assertEqual(tree.selected_id(), fx.REVIEWER)
            await pilot.pause()
            self.assertEqual(pilot.app.posted[-1], ("selected", fx.REVIEWER))


def with_models(groups, **models):
    """`groups` with each row's model set from models[role] (other rows keep theirs)."""
    return [(name, [dict(r, model=models.get(r["role"], r["model"])) for r in rows]) for name, rows in groups]


def screen_lines(tree):
    """The tree's visible lines as drawn; the tree sits at x=0, so string columns are screen columns."""
    return [strip.text for strip in tree.render_lines(Region(0, 0, tree.size.width, tree.size.height))]


def line_of(lines, role):
    return next(line for line in lines if role in line)


def col_of(line, text):
    """Screen column where `text` starts in `line`."""
    return col_width(line[:line.index(text)])


CARBON = "Gemini 4 Carbon"
ROLE_ROW = {"id": "x", "depth": 0, "parent": None, "role": "Planner", "state": "waiting",
            "model": CARBON, "age": "4m", "text": "Plan written."}


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class ColumnsTreeTest(unittest.IsolatedAsyncioTestCase):
    async def test_columns_align_across_depths(self):
        async with TreeHost().run_test(size=(100, 12)) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            tree.update_groups(with_models(fixture_groups(), Planner=CARBON, Checker=CARBON, Reviewer=CARBON))
            await pilot.pause()
            lines = screen_lines(tree)
            planner, checker, reviewer = (line_of(lines, r) for r in ("Planner", "Checker", "Reviewer"))
            # Planner: depth 0 with children; Checker: depth 1 leaf; Reviewer: depth 0 leaf
            model_cols = {col_of(line, CARBON) for line in (planner, checker, reviewer)}
            self.assertEqual(len(model_cols), 1, lines)
            state_cols = {col_of(planner, "waiting"), col_of(checker, "working"), col_of(reviewer, "done")}
            self.assertEqual(state_cols, {COLS["role"] + 1}, lines)
            header = header_columns(tree.size.width).plain
            self.assertEqual(model_cols, {header.index("Model")})

    async def test_model_visible(self):
        async with TreeHost().run_test(size=(100, 12)) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            groups = with_models(fixture_groups(), Planner=CARBON, Checker="gemini-3.8-flash-tiered")
            tree.update_groups(groups)
            await pilot.pause()
            lines = screen_lines(tree)
            self.assertIn(CARBON + " ", line_of(lines, "Planner"))
            self.assertIn("Plan written.", line_of(lines, "Planner"))
            checker = line_of(lines, "Checker")
            self.assertIn("gemini-3.8-flash-tier… ", checker)  # longer than 22 cells: cut with an ellipsis
            self.assertNotIn("tiered", checker)
            self.assertFalse(tree.show_horizontal_scrollbar)

    async def test_relayout_on_resize(self):
        async with TreeHost().run_test(size=(100, 12)) as pilot:
            tree = pilot.app.query_one(SubagentTree)
            tree.update_groups(with_models(fixture_groups(), Planner=CARBON))
            await pilot.pause()
            self.assertIn("Plan written.", line_of(screen_lines(tree), "Planner"))
            await pilot.resize_terminal(50, 12)
            await pilot.pause()
            planner = line_of(screen_lines(tree), "Planner")
            self.assertNotIn("Plan", planner.replace("Planner", ""))  # too narrow for Last step
            self.assertIn("waiting", planner)
            self.assertRegex(planner.rstrip(), r" \d+[smhd]$")  # Age is still the last column
            self.assertLessEqual(tree.virtual_size.width, tree.size.width)  # nothing past the right edge


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class ColumnsTest(unittest.TestCase):
    def test_narrow_drops_columns(self):
        every = ("role", "state", "model", "age", "text")
        fixed = COLS["role"] + 1 + COLS["state"] + 1 + COLS["age"]  # role, state and age always fit
        text_from = fixed + 1 + COLS["model"] + 1 + TEXT_MIN
        model_from = fixed + 1 + MODEL_MIN
        self.assertEqual(visible_cols(100), every)
        self.assertEqual(visible_cols(text_from), every)
        self.assertEqual(visible_cols(text_from - 1), ("role", "state", "model", "age"))
        self.assertEqual(visible_cols(model_from), ("role", "state", "model", "age"))
        self.assertEqual(visible_cols(model_from - 1), ("role", "state", "age"))
        self.assertNotIn("Plan written.", row_columns(ROLE_ROW, 18, text_from - 1).plain)
        self.assertIn(CARBON, row_columns(ROLE_ROW, 18, text_from - 1).plain)
        narrow = row_columns(ROLE_ROW, 18, model_from - 1).plain
        self.assertNotIn("Gemini", narrow)
        self.assertIn("waiting", narrow)
        self.assertNotIn("Last step", header_columns(text_from - 1).plain)
        self.assertNotIn("Model", header_columns(model_from - 1).plain)

    def test_age_and_model_fit_every_width(self):
        row = dict(ROLE_ROW, model="gemini-3.8-flash-tiered")
        for width in range(40, 91):
            header = header_columns(width).plain
            self.assertTrue(header.endswith("Age") or header.rstrip().endswith("Last step"), (width, header))
            for label_width in (COLS["role"] - 6, COLS["role"] - 10):
                shift = COLS["role"] - label_width
                plain = row_columns(row, label_width, width).plain
                self.assertLessEqual(col_width(plain), width - shift, (width, plain))
                self.assertEqual(header.index("Age") + 3, shift + plain.index("4m") + 2, (width, plain))
                if "model" in visible_cols(width):
                    model = plain[plain.index("waiting") + COLS["state"]:plain.index("4m")].strip()
                    self.assertTrue(model.startswith("gemini") and model.endswith("…"), (width, plain))
                    self.assertEqual(header.index("Model"), shift + plain.index("gemini"))
                else:
                    self.assertNotIn("gemini", plain)
                    self.assertNotIn("Model", header)

    def test_long_age_shortened(self):
        for age, shown in (("4m", "4m"), ("59s", "59s"), ("9h59m", "9h59m"), ("12h05m", "12h"),
                           ("99h59m", "99h"), ("100h00m", "4d"), ("7664h36m", "319d")):
            plain = row_columns(dict(ROLE_ROW, age=age), 18, 100).plain
            self.assertIn(" %s Plan written." % shown.rjust(COLS["age"]), plain, age)
            self.assertNotIn("…", plain, age)

    def test_header_matches_rows(self):
        header = header_columns(100)
        self.assertTrue(header.plain.startswith("Subagent "))
        for label_width in (COLS["role"] - 6, COLS["role"] - 10):
            row = row_columns(ROLE_ROW, label_width, 100).plain
            shift = COLS["role"] - label_width  # the row label starts after the tree guides and icon
            self.assertEqual(header.plain.index("State"), shift + row.index("waiting"))
            self.assertEqual(header.plain.index("Model"), shift + row.index(CARBON))
            self.assertEqual(header.plain.index("Age") + 3, shift + row.index("4m") + 2)  # right-aligned
            self.assertEqual(header.plain.index("Last step"), shift + row.index("Plan written."))
            self.assertLessEqual(col_width(row), 100 - shift)
        self.assertLessEqual(col_width(header_columns(70).plain), 70)

    def test_wide_chars_truncate(self):
        row = {"id": "x", "depth": 0, "parent": None, "role": "한글로 된 아주 긴 검토자 역할", "state": "working",
               "model": "모델 이름이 아주 긴 한글 모델입니다", "age": "3m", "text": "한글로 된 아주 긴 마지막 단계 설명입니다"}
        label_width = COLS["role"] - 6
        for total in (61, 62, 80, 100):
            label = row_columns(row, label_width, total)
            plain = label.plain
            self.assertLessEqual(col_width(plain), total - (COLS["role"] - label_width))
            role, rest = plain[:plain.index(" working")], plain[plain.index(" working"):]
            self.assertTrue(role.rstrip().endswith("…"))
            self.assertEqual(col_width(role), label_width)  # padded when a wide char doesn't fit
            self.assertIn(STYLES["working"], [span.style for span in label.spans])
            self.assertIn("…", rest)
        wide = row_columns(row, label_width, 200).plain
        model = wide[wide.index("모델"):]
        self.assertEqual(col_of(wide, "3m") - col_of(wide, "모델"), COLS["model"] + 1 + COLS["age"] - 2)
        self.assertTrue(model.startswith("모델 이름이 아주"))
        self.assertTrue(wide.endswith("설명입니다"))
        self.assertNotIn("-", row_columns(dict(row, model="-"), label_width, 200).plain)


if __name__ == "__main__":
    unittest.main()
