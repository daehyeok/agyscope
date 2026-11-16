import unittest

import helpers  # noqa: F401
from agyscope.viewstate import ViewState

W = 80


def item(key, body=0, running=False):
    """A synthetic foldable item; `body` is a line count or a one-element list (mutable count)."""
    size = body if isinstance(body, list) else [body]
    return {"key": key, "depth": 0, "title": "t%d" % key, "style": "step", "running": running,
            "failed": False, "body": lambda: [("dim", "b%d.%d" % (key, i)) for i in range(size[0])]}


def items(n, **kw):
    return [item(k, **kw) for k in range(n)]


def row_of_cursor(f):
    return f.first - f.top


class FollowTest(unittest.TestCase):
    def test_follow_pins_bottom(self):
        v = ViewState()
        f = v.frame(items(30), W, 10)
        self.assertTrue(v.follow)
        self.assertEqual(v.cursor, 29)
        self.assertEqual(f.cur, 29)
        self.assertEqual(f.top, 20)
        f = v.frame(items(35), W, 10)
        self.assertEqual((v.cursor, f.top), (34, 25))

    def test_follow_short_content_top_zero(self):
        v = ViewState()
        f = v.frame(items(3), W, 10)
        self.assertEqual(f.top, 0)

    def test_manual_up_disables_follow(self):
        v = ViewState()
        its = items(30)
        f = v.frame(its, W, 10)
        v.move("up", its, f, 10)
        self.assertFalse(v.follow)
        self.assertEqual(v.cursor, 28)
        f = v.frame(its, W, 10)
        self.assertEqual(f.cur, 28)
        self.assertEqual(f.top, 20)  # cursor still visible: no scroll needed

    def test_last_reenables_follow(self):
        v = ViewState()
        its = items(30)
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        self.assertFalse(v.follow)
        f = v.frame(its, W, 10)
        self.assertEqual((f.cur, f.top), (0, 0))
        v.move("last", its, f, 10)
        self.assertTrue(v.follow)
        f = v.frame(items(31), W, 10)
        self.assertEqual((v.cursor, f.top), (30, 21))


class AnchorTest(unittest.TestCase):
    def test_anchor_stable_when_lines_appended(self):
        v = ViewState()
        its = items(30)
        f = v.frame(its, W, 10)
        for _ in range(3):
            v.move("up", its, f, 10)
            f = v.frame(its, W, 10)
        self.assertEqual(v.cursor, 26)
        row = row_of_cursor(f)
        top = f.top
        f = v.frame(items(40), W, 10)
        self.assertEqual(v.cursor, 26)
        self.assertEqual(row_of_cursor(f), row)
        self.assertEqual(f.top, top)

    def test_anchor_stable_when_body_above_grows(self):
        v = ViewState()
        grow = [2]
        its = items(30)
        its[20] = item(20, body=grow, running=True)
        f = v.frame(its, W, 10)
        for _ in range(3):
            v.move("up", its, f, 10)
            f = v.frame(its, W, 10)
        self.assertEqual(v.cursor, 26)
        row = row_of_cursor(f)
        top = f.top
        grow[0] = 7
        f = v.frame(its, W, 10)
        self.assertEqual(v.cursor, 26)
        self.assertEqual(row_of_cursor(f), row)
        self.assertEqual(f.top, top + 5)
        self.assertEqual(f.lines[f.first][2], "> t26")

    def test_recentres_when_height_shrinks(self):
        v = ViewState()
        its = items(30)
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        f = v.frame(its, W, 10)
        for _ in range(9):
            v.move("down", its, f, 10)
            f = v.frame(its, W, 10)
        self.assertEqual(row_of_cursor(f), 9)
        f = v.frame(its, W, 6)
        self.assertTrue(f.top <= f.first < f.top + 6)


class ScrollTest(unittest.TestCase):
    def big(self):
        its = items(5)
        its[2] = item(2, body=200)
        v = ViewState()
        v.folds[2] = True
        return v, its

    def test_scroll_by_independent_of_cursor(self):
        v, its = self.big()
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        f = v.frame(its, W, 10)
        v.move("down", its, f, 10)
        f = v.frame(its, W, 10)
        v.move("down", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (2, 0))
        for _ in range(10):
            v.scroll_by(10, len(f.lines), 10)
            f = v.frame(its, W, 10)
        self.assertEqual(v.cursor, 2)
        self.assertEqual(f.top, 100)
        self.assertEqual(v.top, 100)
        self.assertFalse(v.follow)
        f = v.frame(its, W, 10)  # a refresh keeps the free scroll position
        self.assertEqual(f.top, 100)
        v.scroll_by(-30, len(f.lines), 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (2, 70))

    def test_scroll_up_disables_follow(self):
        v, its = self.big()
        f = v.frame(its, W, 10)
        self.assertTrue(v.follow)
        v.scroll_by(-1, len(f.lines), 10)
        self.assertFalse(v.follow)
        f = v.frame(its, W, 10)
        self.assertEqual(f.top, len(f.lines) - 10 - 1)

    def scrolled_into_body(self):
        """Cursor on the 200-line item 2, wheel-scrolled so the screen starts at line 100."""
        v, its = self.big()
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        f = v.frame(its, W, 10)
        for _ in range(2):
            v.move("down", its, f, 10)
            f = v.frame(its, W, 10)
        v.scroll_by(100, len(f.lines), 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (2, 100))
        return v, its, f

    def test_down_off_screen_scrolls_one_line(self):
        v, its, f = self.scrolled_into_body()
        v.move("down", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (2, 101))
        self.assertFalse(v.follow)

    def test_up_off_screen_scrolls_one_line(self):
        v, its, f = self.scrolled_into_body()
        v.move("up", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (2, 99))

    def test_up_down_resume_item_moves_once_cursor_visible(self):
        v, its, f = self.scrolled_into_body()
        v.scroll_by(-100, len(f.lines), 10)
        f = v.frame(its, W, 10)
        self.assertEqual(f.top, 0)
        v.move("down", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual(v.cursor, 3)
        self.assertEqual(f.lines[f.first][2], "> t3")
        self.assertEqual(row_of_cursor(f), 9)

    def test_move_uses_latest_scroll_even_with_stale_frame(self):
        v, its, f = self.scrolled_into_body()
        v.scroll_by(5, len(f.lines), 10)  # no re-frame in between
        v.move("down", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (2, 106))

    def test_scroll_by_zero_keeps_follow(self):
        v, its = self.big()
        f = v.frame(its, W, 10)
        v.scroll_by(0, len(f.lines), 10)
        self.assertTrue(v.follow)
        v.scroll_by(-5, len(f.lines), 10)
        v.scroll_by(0, len(f.lines), 10)
        self.assertFalse(v.follow)

    def test_scroll_to_bottom_resumes_follow(self):
        v, its = self.big()
        f = v.frame(its, W, 10)
        v.scroll_by(-50, len(f.lines), 10)
        f = v.frame(its, W, 10)
        v.scroll_by(20, len(f.lines), 10)
        self.assertFalse(v.follow)
        v.scroll_by(1000, len(f.lines), 10)
        self.assertTrue(v.follow)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (4, len(f.lines) - 10))


class MoveTest(unittest.TestCase):
    def test_page_down_moves_by_screen(self):
        v = ViewState()
        its = items(50)
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        f = v.frame(its, W, 10)
        v.move("page_down", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (10, 10))
        v.move("page_up", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (0, 0))
        self.assertFalse(v.follow)

    def test_page_boundaries_jump_to_ends(self):
        v = ViewState()
        its = items(50)
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        f = v.frame(its, W, 10)
        v.move("page_up", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (0, 0))
        v.move("up", its, f, 10)  # cursor visible: plain item move (stays at 0)
        f = v.frame(its, W, 10)
        for _ in range(4):
            v.move("page_down", its, f, 10)
            f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (40, 40))
        v.move("page_down", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (49, 40))
        self.assertFalse(v.follow)

    def test_page_down_through_long_body_scrolls_lines(self):
        v = ViewState()
        its = items(3)
        its[1] = item(1, body=50)
        v.folds[1] = True
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        f = v.frame(its, W, 10)
        v.move("down", its, f, 10)
        f = v.frame(its, W, 10)
        v.move("page_down", its, f, 10)
        f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (1, 10))

    def test_down_scrolls_minimally(self):
        v = ViewState()
        its = items(30)
        f = v.frame(its, W, 10)
        v.move("first", its, f, 10)
        f = v.frame(its, W, 10)
        for _ in range(10):
            v.move("down", its, f, 10)
            f = v.frame(its, W, 10)
        self.assertEqual((v.cursor, f.top), (10, 1))

    def test_open_toggles_and_disables_follow(self):
        v = ViewState()
        its = items(5, body=3)
        f = v.frame(its, W, 10)
        self.assertEqual(len(f.lines), 5)
        v.move("open", its, f, 10)
        self.assertFalse(v.follow)
        self.assertEqual(v.folds, {4: True})
        f = v.frame(its, W, 10)
        self.assertEqual(len(f.lines), 8)
        v.move("open", its, f, 10)
        self.assertEqual(v.folds, {4: False})

    def test_fold_all_toggle(self):
        v = ViewState()
        its = items(5, body=3)
        v.folds[1] = True
        f = v.frame(its, W, 10)
        v.move("fold_all", its, f, 10)
        self.assertIs(v.all, True)
        self.assertEqual(v.folds, {})
        self.assertEqual(len(v.frame(its, W, 50).lines), 20)
        v.move("fold_all", its, v.frame(its, W, 50), 50)
        self.assertIs(v.all, False)
        self.assertEqual(len(v.frame(its, W, 50).lines), 5)

    def test_move_on_empty_is_noop(self):
        v = ViewState()
        f = v.frame([], W, 10)
        self.assertEqual((f.lines, f.top), ([], 0))
        v.move("down", [], f, 10)
        self.assertIsNone(v.cursor)


class ClickTest(unittest.TestCase):
    def test_click_title_toggles(self):
        v = ViewState()
        its = items(5, body=3)
        f = v.frame(its, W, 10)
        v.click(1, f)
        self.assertEqual(v.cursor, 1)
        self.assertFalse(v.follow)
        self.assertEqual(v.folds, {1: True})
        f = v.frame(its, W, 10)
        self.assertEqual(f.cur, 1)
        self.assertEqual(len(f.lines), 8)

    def test_click_body_moves_cursor_only(self):
        v = ViewState()
        its = items(5, body=3)
        v.folds[1] = True
        f = v.frame(its, W, 10)
        v.click(3, f)  # line 3 = second body line of item 1
        self.assertEqual(v.cursor, 1)
        self.assertEqual(v.folds, {1: True})
        self.assertFalse(v.follow)

    def test_click_wrapped_title_continuation_toggles(self):
        v = ViewState()
        its = items(2)
        its[0]["title"] = "x" * 30
        f = v.frame(its, 20, 10)
        self.assertEqual([n for n, _, _ in f.lines], [0, 0, 1])
        v.click(1, f)
        self.assertEqual(v.folds, {0: True})

    def test_click_outside_lines_ignored(self):
        v = ViewState()
        its = items(3)
        f = v.frame(its, W, 10)
        v.click(7, f)
        self.assertTrue(v.follow)
        self.assertEqual(v.folds, {})


class ExpandedTest(unittest.TestCase):
    def test_running_items_default_expanded(self):
        v = ViewState()
        run, idle = item(0, running=True), item(1)
        self.assertTrue(v.expanded(run))
        self.assertFalse(v.expanded(idle))
        v.all = False
        self.assertFalse(v.expanded(run))
        v.all = True
        self.assertTrue(v.expanded(idle))
        v.folds[1] = False
        self.assertFalse(v.expanded(idle))


if __name__ == "__main__":
    unittest.main()
