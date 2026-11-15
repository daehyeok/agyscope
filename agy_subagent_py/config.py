"""Settings for the panel and the hook.

Each setting is taken from the first of these that sets it:

  1. command-line flags (the Rex action passes them)
  2. environment variables AGY_SUBAGENT_<NAME>  (lists and objects as JSON)
  3. the JSON config file: $AGY_SUBAGENT_CONFIG, or
     ${XDG_CONFIG_HOME:-~/.config}/rex-agy-subagent/config.json
  4. DEFAULTS

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
    "bounds": [0.6, 0.04, 0.38, 0.42],
    "auto_open": True,
}

ENV_PREFIX = "AGY_SUBAGENT_"


def config_path(env):
    if env.get("AGY_SUBAGENT_CONFIG"):
        return os.path.expanduser(env["AGY_SUBAGENT_CONFIG"])
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "rex-agy-subagent", "config.json")


def _check(name, value):
    """Validated value for a setting, or raises ValueError with a short reason."""
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
        if not isinstance(value, list) or not value or not all(isinstance(a, str) and a for a in value):
            raise ValueError("must be a non-empty list of strings")
        return value
    if name == "bounds":
        if (not isinstance(value, list) or len(value) != 4
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in value)):
            raise ValueError("must be [x, y, w, h], each between 0 and 1")
        return [float(v) for v in value]
    if name == "auto_open":
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value
    raise ValueError("unknown setting")


def _from_env(name, raw):
    if name in ("app_dir", "keymap"):
        return raw
    if name == "auto_open":
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise ValueError("must be true or false")
    try:
        return json.loads(raw)
    except ValueError:
        raise ValueError("must be JSON")


def read_file(path):
    """(dict, warnings) from a JSON config file; a missing file is not a warning."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as e:
        return {}, ["config %s: %s" % (path, e)]
    if not isinstance(data, dict):
        return {}, ["config %s: must be a JSON object" % path]
    warnings = ["config %s: unknown setting %r" % (path, k) for k in data if k not in DEFAULTS]
    return {k: v for k, v in data.items() if k in DEFAULTS}, warnings


def load(flags=None, env=None):
    """(settings, warnings). flags: setting name -> value already decoded (None = not given)."""
    env = os.environ if env is None else env
    flags = {k: v for k, v in (flags or {}).items() if v is not None}
    file_values, warnings = read_file(config_path(env))
    settings = {}
    for name, default in DEFAULTS.items():
        value, source = None, None
        if name in flags:
            value, source = flags[name], "flag --%s" % name.replace("_", "-")
        elif (ENV_PREFIX + name.upper()) in env:
            source = ENV_PREFIX + name.upper()
            try:
                value = _from_env(name, env[source])
            except ValueError as e:
                warnings.append("%s %s; using default" % (source, e))
                source = None
        elif name in file_values:
            value, source = file_values[name], "config %s" % name
        if source is None:
            settings[name] = copy.deepcopy(default)
            continue
        try:
            settings[name] = _check(name, value)
        except ValueError as e:
            warnings.append("%s %s; using default" % (source, e))
            settings[name] = copy.deepcopy(default)
    settings["app_dir"] = os.path.expanduser(settings["app_dir"])
    return settings, warnings
