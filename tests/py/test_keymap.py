import unittest

import helpers  # noqa: F401
from agy_subagent_py import keymap


class KeymapTest(unittest.TestCase):
    def test_emacs_default(self):
        km = keymap.build("emacs")
        self.assertEqual(km["C-n"], "down")
        self.assertEqual(km["C-p"], "up")
        self.assertEqual(km["Down"], "down")
        self.assertEqual(km["M->"], "last")
        self.assertEqual(km["Enter"], "open")
        self.assertNotIn("j", km)

    def test_vim(self):
        km = keymap.build("vim")
        self.assertEqual(km["j"], "down")
        self.assertEqual(km["G"], "last")
        self.assertEqual(km["Up"], "up")
        self.assertEqual(km["PgDn"], "page_down")

    def test_added_key(self):
        km = keymap.build("emacs", {"down": ["J"]})
        for k in ("J", "C-n", "Down"):
            self.assertEqual(km[k], "down", k)

    def test_user_key_moves_to_user_action(self):
        km = keymap.build("emacs", {"close": "C-g"})
        self.assertEqual(km["C-g"], "close")

    def test_unknown_preset(self):
        with self.assertRaises(ValueError):
            keymap.build("nano")

    def test_normalize(self):
        self.assertEqual(keymap.normalize("c-N"), "C-n")
        self.assertEqual(keymap.normalize("C-m"), "Enter")
        self.assertEqual(keymap.normalize("pgup"), "PgUp")
        self.assertEqual(keymap.normalize(" "), "Space")
        self.assertEqual(keymap.normalize("M-<"), "M-<")
        self.assertIsNone(keymap.normalize("ctrl+x"))
        self.assertIsNone(keymap.normalize(""))

    def test_decode_esc_vs_meta(self):
        self.assertEqual(keymap.decode_byte(27, lambda: -1), "Esc")
        self.assertEqual(keymap.decode_byte(27, lambda: ord("v")), "M-v")
        self.assertEqual(keymap.decode_byte(27, lambda: ord("<")), "M-<")
        self.assertEqual(keymap.decode_byte(14), "C-n")
        self.assertEqual(keymap.decode_byte(13), "Enter")
        self.assertEqual(keymap.decode_byte(9), "Tab")
        self.assertEqual(keymap.decode_byte(ord("G")), "G")
        self.assertEqual(keymap.decode_byte(32), "Space")


if __name__ == "__main__":
    unittest.main()
