import os
import tempfile
import unittest

from agyscope.integrations.rex import pointer


class PointerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"XDG_STATE_HOME": self.tmp}

    def test_state_dir(self):
        self.assertEqual(pointer.state_dir(self.env), os.path.join(self.tmp, "agyscope"))

    def test_round_trip_with_app_dir(self):
        pointer.write_pointer("block:1", "conv-a", self.env, app_dir="/data/x")
        self.assertEqual(pointer.read_pointer_full("block:1", self.env), ("conv-a", "/data/x"))
        self.assertEqual(pointer.read_pointer("block:1", self.env), "conv-a")
        with open(os.path.join(self.tmp, "agyscope", "blocks", "block:1")) as f:
            self.assertEqual(f.read().splitlines(), ["conv-a", "/data/x"])

    def test_round_trip_without_app_dir(self):
        pointer.write_pointer("block:2", "conv-b", self.env)
        self.assertEqual(pointer.read_pointer_full("block:2", self.env), ("conv-b", None))

    def test_missing(self):
        self.assertEqual(pointer.read_pointer_full("nope", self.env), (None, None))
        self.assertIsNone(pointer.read_pointer("nope", self.env))

    def test_slash_sanitized(self):
        self.assertEqual(pointer.pointer_path("a/b", self.env),
                         os.path.join(self.tmp, "agyscope", "blocks", "a_b"))
        pointer.write_pointer("a/b", "conv-c", self.env)
        self.assertEqual(pointer.read_pointer("a/b", self.env), "conv-c")


if __name__ == "__main__":
    unittest.main()
