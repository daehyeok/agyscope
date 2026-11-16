"""Pins how Textual's terminal parser reports keys, so a Textual upgrade that changes it fails here."""

import string
import unittest

import helpers  # noqa: F401
from agy_subagent_py import keymap

try:
    from textual import events
    from textual._xterm_parser import XTermParser
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False


def parse(data):
    """Our key names for the key events Textual's parser produces from raw terminal input."""
    parser = XTermParser()
    evs = list(parser.feed(data)) + list(parser.feed(""))
    return [keymap.from_textual(e.key, e.character) for e in evs if isinstance(e, events.Key)]


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TextualKeyTableTest(unittest.TestCase):
    def test_from_textual_table_matches_parser(self):
        cases = {
            "\x1bv": "M-v", "\x1ba": "M-a", "\x1bs": "M-s", "\x07": "C-g", "\t": "Tab",
            "[": "[", "\x1b<": "<", "\x1b>": ">", "\x1b[6~": "PgDn", "\r": "Enter", "\x1b": "Esc",
        }
        for data, want in cases.items():
            self.assertEqual(parse(data), [want], repr(data))
        self.assertNotEqual(parse("\x1bb"), ["M-b"])

    def test_unreachable_matches_parser(self):
        for ch in string.ascii_letters + string.digits + string.punctuation:
            name = "M-" + ch
            got = parse("\x1b" + ch)
            if name in keymap.UNREACHABLE or name in keymap.META_ALIASES:
                self.assertNotEqual(got, [name], name)
            else:
                self.assertEqual(got, [name], name)


if __name__ == "__main__":
    unittest.main()
