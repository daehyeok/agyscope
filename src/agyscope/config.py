"""Settings for agyscope.

Each setting is taken from the first of these that sets it:

  1. command-line flags
  2. the JSON config file: $AGYSCOPE_CONFIG, or
     ${XDG_CONFIG_HOME:-~/.config}/agyscope/config.json
  3. the default

DEFAULTS are the core settings. Integrations add their own with load(extra=...); the
file keys they own (INTEGRATION_KEYS) are not reported as unknown by the core.

An invalid value is reported as a warning and the default is used instead, so a
config mistake never stops the panel from starting.
"""

import copy
import json
import os

from . import keymap

DEFAULTS = {
    "app_dir": "~/.gemini/antigravity-cli",
    "keymap": "emacs",
    "keys": {},
    "pager": ["less", "-R", "+G"],
}

INTEGRATION_KEYS = frozenset({"bounds", "auto_open", "interactive", "command"})


def config_path(env):
    if env.get("AGYSCOPE_CONFIG"):
        return os.path.expanduser(env["AGYSCOPE_CONFIG"])
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "agyscope", "config.json")


def check_bool(value):
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def check_str_list(value):
    if not isinstance(value, list) or not value or not all(isinstance(a, str) and a for a in value):
        raise ValueError("must be a non-empty list of strings")
    return list(value)


def _check(name, value):
    """Validated value for a core setting, or raises ValueError with a short reason."""
    if name == "app_dir":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value
    if name == "keymap":
        if value not in keymap.PRESETS:
            raise ValueError("must be one of %s" % ", ".join(sorted(keymap.PRESETS)))
        return value
    if name == "keys":
        if not isinstance(value, dict):
            raise ValueError("must be an object of action -> key or list of keys")
        out = {}
        for action, keys in value.items():
            if action not in keymap.ACTIONS:
                raise ValueError("unknown action %r" % (action,))
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list) or not keys:
                raise ValueError("%s: must be a key or a list of keys" % action)
            for k in keys:
                if keymap.normalize(k) is None:
                    raise ValueError("%s: bad key %r" % (action, k))
            out[action] = list(keys)
        return out
    if name == "pager":
        return check_str_list(value)
    raise ValueError("unknown setting")


def read_file(path, known):
    """(dict, warnings) from a JSON config file; a missing file is not a warning.

    Keys in neither known nor INTEGRATION_KEYS are warned about; only known keys are returned.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as e:
        return {}, ["config %s: %s" % (path, e)]
    if not isinstance(data, dict):
        return {}, ["config %s: must be a JSON object" % path]
    warnings = ["config %s: unknown setting %r" % (path, k) for k in data
                if k not in known and k not in INTEGRATION_KEYS]
    return {k: v for k, v in data.items() if k in known}, warnings


def load(flags=None, env=None, extra=None):
    """(settings, warnings).

    flags: setting name -> value already decoded (None = not given).
    extra: setting name -> (default, check_fn), merged after DEFAULTS; check_fn returns the
    validated value or raises ValueError. extra may not redefine a core setting (ValueError).
    """
    env = os.environ if env is None else env
    extra = dict(extra or {})
    clash = set(extra) & set(DEFAULTS)
    if clash:
        raise ValueError("extra settings redefine core settings: %s" % ", ".join(sorted(clash)))
    flags = {k: v for k, v in (flags or {}).items() if v is not None}
    specs = {name: (default, lambda v, n=name: _check(n, v)) for name, default in DEFAULTS.items()}
    specs.update(extra)
    file_values, warnings = read_file(config_path(env), specs)
    settings = {}
    for name, (default, check) in specs.items():
        if name in flags:
            value, source = flags[name], "flag --%s" % name.replace("_", "-")
        elif name in file_values:
            value, source = file_values[name], "config %s" % name
        else:
            settings[name] = copy.deepcopy(default)
            continue
        try:
            settings[name] = check(value)
        except ValueError as e:
            warnings.append("%s %s; using default %r" % (source, e, default))
            settings[name] = copy.deepcopy(default)
    settings["app_dir"] = os.path.expanduser(settings["app_dir"])
    return settings, warnings
