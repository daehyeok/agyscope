import os
import stat
import tempfile
import unittest

from agyscope import links, state
from agyscope.links import HIDDEN, LINKED, LinkError, LinkStore


class StateDirTest(unittest.TestCase):
    def test_default_and_override(self):
        self.assertEqual(state.state_dir({"XDG_STATE_HOME": "/s"}), "/s/agyscope")
        self.assertEqual(state.state_dir({}), os.path.join(os.path.expanduser("~"), ".local", "state", "agyscope"))

    def test_pointer_reuses_core_state_dir(self):
        from agyscope.integrations.rex import pointer
        self.assertIs(pointer.state_dir, state.state_dir)


class LinkStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(), "agyscope")
        self.store = LinkStore(self.root)

    def test_bad_ids(self):
        for bad in ("", "a/b", "../x", ".hidden", "_top", None, "a\\b"):
            with self.assertRaises(LinkError):
                self.store.attach(bad, "M")
            with self.assertRaises(LinkError):
                self.store.attach("W", bad if bad is not None else "")
        self.assertFalse(os.path.exists(self.root))  # nothing written

    def test_attach_under_and_list(self):
        self.assertTrue(self.store.attach("W1", "M"))
        self.assertTrue(self.store.attach("W2", "M"))
        self.assertEqual(self.store.children("M"), ["W1", "W2"])
        self.assertEqual(self.store.children("other"), [])
        self.assertEqual(self.store.parents(), {"M": ["W1", "W2"]})
        self.assertEqual(self.store.parents_of("W2"), ["M"])
        mode = stat.S_IMODE(os.stat(self.root).st_mode)
        self.assertEqual(mode, 0o700)

    def test_attach_order_survives_coarse_mtimes(self):
        # ext4/tmpfs stamp writes in the same kernel tick with one mtime; simulate it by
        # pushing the first link's mtime ahead so the second can't sort after it by time.
        for parent in ("M", None):
            self.store.attach("zz", parent)
            d = self.store._dir(LINKED, parent)
            ahead = os.stat(os.path.join(d, "zz")).st_mtime_ns + 10 ** 10
            os.utime(os.path.join(d, "zz"), ns=(ahead, ahead))
            self.store.attach("aa", parent)
            self.store.attach("mm", parent)
            want = ["zz", "aa", "mm"]
            got = self.store.children(parent) if parent else [c for c, _ in self.store.top()]
            self.assertEqual(got, want)
            self.assertEqual(LinkStore(self.root).children(parent) if parent else
                             [c for c, _ in LinkStore(self.root).top()], want)

    def test_repeated_attach_is_idempotent(self):
        self.assertTrue(self.store.attach("W", "M"))
        self.assertFalse(self.store.attach("W", "M"))
        self.assertEqual(self.store.children("M"), ["W"])

    def test_self_attach_rejected(self):
        with self.assertRaises(LinkError):
            self.store.attach("M", "M")

    def test_top_level_with_app_dir(self):
        self.store.attach("C", None, app_dir="/data/x")
        self.store.attach("D")
        self.assertEqual(self.store.top(), [("C", "/data/x"), ("D", None)])
        self.assertTrue(self.store.attach("C", None, app_dir="/data/y"))  # app_dir changed
        self.assertEqual(dict(self.store.top())["C"], "/data/y")

    def test_detach_hides_and_attach_undoes(self):
        self.store.attach("W", "M")
        self.assertTrue(self.store.detach("W", "M"))
        self.assertEqual(self.store.children("M"), [])
        self.assertEqual(self.store.hidden("M"), {"W"})
        self.assertFalse(self.store.detach("W", "M"))
        self.store.detach("S", "M")  # a transcript child: hidden although never linked
        self.assertEqual(self.store.hidden("M"), {"W", "S"})
        self.store.attach("S", "M")
        self.assertEqual(self.store.hidden("M"), {"W"})
        self.assertEqual(self.store.children("M"), ["S"])

    def test_top_level_detach(self):
        self.store.attach("L")
        self.store.detach("L")
        self.assertEqual(self.store.top(), [])
        self.assertEqual(self.store.hidden(None), {"L"})

    def test_entries(self):
        self.store.attach("W", "M")
        self.store.attach("C")
        self.store.detach("S", "M")
        self.assertEqual(sorted(self.store.entries(), key=str),
                         sorted([("M", "W", LINKED), (None, "C", LINKED), ("M", "S", HIDDEN)], key=str))
        self.assertEqual(sorted(self.store.entries("M")), [("M", "S", HIDDEN), ("M", "W", LINKED)])

    def test_cache_sees_changes_from_another_store(self):
        other = LinkStore(self.root)
        self.assertEqual(self.store.children("M"), [])
        other.attach("W", "M")
        self.assertEqual(self.store.children("M"), ["W"])
        other.detach("W", "M")
        self.assertEqual(self.store.children("M"), [])

    def test_temp_files_ignored(self):
        self.store.attach("W", "M")
        open(os.path.join(self.root, "links", "M", "X.123.tmp"), "w").close()
        self.assertEqual(self.store.children("M"), ["W"])

    def test_module_constants(self):
        self.assertEqual(links.TOP, "_top")
