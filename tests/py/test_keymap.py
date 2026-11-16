import unittest

import helpers  # noqa: F401
from agyscope import keymap


class KeymapTest(unittest.TestCase):
    def test_emacs_default(self):
        km = keymap.build("emacs")
        self.assertEqual(km["C-n"], "down")
        self.assertEqual(km["C-p"], "up")
        self.assertEqual(km["Down"], "down")
        self.assertEqual(km[">"], "last")
        self.assertEqual(km["Enter"], "open")
        self.assertNotIn("j", km)

    def test_pane_actions_removed(self):
        for action in ("focus_next", "toggle_sidebar"):
            self.assertNotIn(action, keymap.ACTIONS)
            for layer in [keymap.ALWAYS] + list(keymap.PRESETS.values()):
                self.assertNotIn(action, layer)
        self.assertNotIn("M-s", keymap.build("emacs"))
        self.assertNotIn("[", keymap.build("vim"))

    def test_emacs_tab_opens(self):
        km = keymap.build("emacs")
        self.assertEqual(km["Tab"], "open")
        self.assertEqual(keymap.keys_for(km, "open"), ["Tab", "Enter", "Right"])
        self.assertNotIn("Tab", keymap.build("vim"))

    def test_emacs_meta_lt_gt_alias(self):
        km = keymap.build("emacs")
        self.assertEqual(km["<"], "first")
        self.assertEqual(km[">"], "last")
        self.assertNotIn("M-<", km)
        self.assertNotIn("M->", km)

    def test_unreachable_meta_warns(self):
        self.assertEqual(keymap.unreachable(keymap.build("emacs", {"down": ["M-b"]})), ["M-b"])
        self.assertEqual(keymap.unreachable(keymap.build("emacs", {"up": ["M-f", "M-1"]})), ["M-1", "M-f"])
        for preset in ("emacs", "vim"):
            self.assertEqual(keymap.unreachable(keymap.build(preset)), [], preset)

    def test_from_textual_basic(self):
        cases = [
            (("ctrl+n", None), "C-n"), (("alt+v", None), "M-v"), (("pagedown", None), "PgDn"),
            (("less_than_sign", "<"), "<"), (("G", "G"), "G"), (("left_square_bracket", "["), "["),
            (("enter", "\r"), "Enter"), (("escape", "\x1b"), "Esc"), (("space", " "), "Space"),
            (("tab", "\t"), "Tab"), (("alt+shift+v", "V"), "M-V"),
            (("ctrl+left", None), None), (("shift+tab", None), None), (("f1", None), None),
        ]
        for args, want in cases:
            self.assertEqual(keymap.from_textual(*args), want, args)

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


if __name__ == "__main__":
    unittest.main()
