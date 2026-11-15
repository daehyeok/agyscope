"""Panel key handling: presets, user-added keys, and decoding curses input into key names.

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
"""

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
                out[n] = action
    return out


def keys_for(keymap, action):
    """Key names bound to action, in a stable order (for the title row)."""
    return sorted((k for k, a in keymap.items() if a == action), key=lambda k: (len(k), k))


def decode(code, next_code=None):
    """Key name for one curses getch() code.

    next_code is called after an Esc (27) to read the following code without waiting
    long; it returns -1 when no key follows (then the key is a lone Esc).
    """
    import curses  # only needed when decoding real input
    named = {
        curses.KEY_UP: "Up", curses.KEY_DOWN: "Down", curses.KEY_LEFT: "Left",
        curses.KEY_RIGHT: "Right", curses.KEY_PPAGE: "PgUp", curses.KEY_NPAGE: "PgDn",
        curses.KEY_HOME: "Home", curses.KEY_END: "End", curses.KEY_ENTER: "Enter",
    }
    if code in named:
        return named[code]
    return decode_byte(code, next_code)


def decode_byte(code, next_code=None):
    """Key name for a plain byte code (no curses KEY_* constants); see decode()."""
    if code in (10, 13):
        return "Enter"
    if code == 9:
        return "Tab"
    if code == 32:
        return "Space"
    if code == 27:
        nxt = next_code() if next_code else -1
        if nxt is None or nxt < 0:
            return "Esc"
        if 32 < nxt < 127:
            return "M-" + chr(nxt)
        return "Esc"
    if 0 < code < 27:
        return "C-" + chr(96 + code)
    if 32 < code < 127:
        return chr(code)
    if code > 127:
        try:
            return chr(code)
        except ValueError:
            return None
    return None
