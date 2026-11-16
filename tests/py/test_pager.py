import os
import tempfile
import unittest

import helpers  # noqa: F401
from agyscope import pager


class PagerTest(unittest.TestCase):
    def test_append(self):
        self.assertEqual(pager.argv(["bat", "--paging=always"], "/t/f"), ["bat", "--paging=always", "/t/f"])

    def test_placeholder(self):
        self.assertEqual(pager.argv(["view", "--file={file}", "-x"], "/t/f"), ["view", "--file=/t/f", "-x"])

    def test_prompt_only_for_default(self):
        a = pager.argv(pager.DEFAULT, "/t/f", "role (read-only)")
        self.assertEqual(a[:4], ["less", "-R", "+G", "-P"])
        self.assertEqual(a[-1], "/t/f")
        self.assertEqual(pager.argv(["less"], "/t/f", "x"), ["less", "/t/f"])

    def test_show_runs_and_cleans_up(self):
        out = os.path.join(tempfile.mkdtemp(), "copy")
        err = pager.show(["sh", "-c", 'cat "$1" > %s' % out, "sh"], ["a", "b"], "r")
        self.assertIsNone(err)
        with open(out) as f:
            self.assertEqual(f.read(), "a\nb\n")
        left = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith("agyscope-")]
        self.assertEqual(left, [])

    def test_missing_pager(self):
        err = pager.show(["/nonexistent/pager"], ["a"], "r")
        self.assertIn("pager", err)


if __name__ == "__main__":
    unittest.main()
