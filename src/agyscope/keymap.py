"""Panel key handling: presets, user-added keys, and mapping Textual key names onto them.

Key names:
  single characters        "q", "G", "<"
  control keys             "C-n", "C-v"         (C-m is Enter, C-i is Tab, C-[ is Esc)
  meta keys                "M-v", "M-<"         (sent by terminals as Esc followed by the key)
  named keys               Up Down Left Right PgUp PgDn Home End Enter Esc Tab Space

Actions:
  up down page_up page_down first last open fold_all pager back close

Keys in ALWAYS work in every preset. A preset adds its own keys, and user keys
(``keys`` setting: action -> key or list of keys) are added last, so a user key
that a preset also uses moves to the user's action.

Textual reports Esc followed by < or > as plain < and >, so build() binds M-< and M->
to the plain keys (META_ALIASES). Meta keys Textual can't report at all are in
UNREACHABLE; unreachable() lists the ones a keymap uses so the panel can warn.
"""

import string

ACTIONS = ("up", "down", "page_up", "page_down", "first", "last",
           "open", "fold_all", "pager", "back", "close")

NAMED = ("Up", "Down", "Left", "Right", "PgUp", "PgDn", "Home", "End",
         "Enter", "Esc", "Tab", "Space")

ALWAYS = {
    "up": ["Up"], "down": ["Down"], "page_up": ["PgUp"], "page_down": ["PgDn"],
    "first": ["Home"], "last": ["End"], "open": ["Enter", "Right"], "back": ["Left", "Esc"],
}

PRESETS = {
    "emacs": {
        "up": ["C-p"], "down": ["C-n"], "page_up": ["M-v"], "page_down": ["C-v"],
        "first": ["M-<"], "last": ["M->"], "open": ["Tab"], "fold_all": ["M-a"],
        "pager": ["M-o", "o"], "back": ["C-g"], "close": ["q"],
    },
    "vim": {
        "up": ["k"], "down": ["j"], "page_up": ["C-u", "C-b"], "page_down": ["C-d", "C-f"],
        "first": ["g"], "last": ["G"], "open": ["Space"], "fold_all": ["a"],
        "pager": ["o"], "back": ["Esc"], "close": ["q"],
    },
}

META_ALIASES = {"M-<": "<", "M->": ">"}

# Esc b / Esc f arrive as ctrl+left / ctrl+right, Esc + digit or "-" / "=" as macOS Option
# characters, and Esc + other punctuation as the plain character.
UNREACHABLE = frozenset("M-" + c for c in "bf " + string.digits + string.punctuation
                        if "M-" + c not in META_ALIASES)

# Textual Key.key -> key name, for keys that have no single printable character.
_TEXTUAL_NAMED = {
    "enter": "Enter", "tab": "Tab", "escape": "Esc", "space": "Space", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "pageup": "PgUp", "pagedown": "PgDn", "home": "Home", "end": "End",
}

# Textual Key.key modifier prefix -> our prefix, for one-character keys (alt+shift+v is M-V).
_TEXTUAL_MODS = (("ctrl+", "C-"), ("alt+shift+", "M-"), ("alt+", "M-"))

_ALIASES = {"c-m": "Enter", "c-j": "Enter", "c-i": "Tab", "c-[": "Esc", " ": "Space",
            "ret": "Enter", "return": "Enter", "escape": "Esc", "pageup": "PgUp",
            "pagedown": "PgDn", "spc": "Space"}


def normalize(name):
    """Canonical key name, or None if the name is not a valid key."""
    if not isinstance(name, str) or name == "":
        return None
    low = name.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    for n in NAMED:
        if low == n.lower():
            return n
    if len(name) == 1:
        return name
    if len(name) == 3 and name[1] == "-" and name[0] in "CcMm":
        mod, ch = name[0].upper(), name[2]
        if mod == "C":
            ch = ch.lower()
            return _ALIASES.get("c-" + ch, "C-" + ch)
        return "M-" + ch
    return None


def build(preset="emacs", extra=None):
    """Map of key name -> action for a preset plus user keys. Unknown preset -> ValueError."""
    if preset not in PRESETS:
        raise ValueError("unknown keymap %r" % (preset,))
    out = {}
    for layer in (ALWAYS, PRESETS[preset], extra or {}):
        for action, keys in layer.items():
            if isinstance(keys, str):
                keys = [keys]
            for k in keys:
                n = normalize(k)
                if n is None:
                    raise ValueError("bad key %r for %s" % (k, action))
                out[META_ALIASES.get(n, n)] = action
    return out


def unreachable(keymap):
    """Key names in keymap that Textual can't report (sorted), for status-line warnings."""
    return sorted(k for k in keymap if k in UNREACHABLE)


def from_textual(key, character=None):
    """Key name for a Textual Key event's key and character, or None if it has none."""
    if key in _TEXTUAL_NAMED:
        return _TEXTUAL_NAMED[key]
    for prefix, mod in _TEXTUAL_MODS:
        if key.startswith(prefix) and len(key) == len(prefix) + 1:
            ch = key[-1].upper() if prefix == "alt+shift+" else key[-1]
            return normalize(mod + ch)
    if "+" not in key and character is not None and len(character) == 1 and character.isprintable():
        return character
    return None


def keys_for(keymap, action):
    """Key names bound to action, in a stable order (for the title row)."""
    return sorted((k for k, a in keymap.items() if a == action), key=lambda k: (len(k), k))
