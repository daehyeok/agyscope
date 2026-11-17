"""Shared test paths and helpers. pytest (pyproject.toml) puts tests/py and the fixtures on sys.path."""

import json
import os
import re

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS))
FIXTURES = os.path.join(TESTS, "fixtures")

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _toml_key(key):
    if not isinstance(key, str):
        raise TypeError("TOML key must be str, got %s" % type(key).__name__)
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key)


def _toml_scalar(val):
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return repr(val)
    if isinstance(val, str):
        return json.dumps(val)
    raise TypeError("unsupported TOML scalar: %s" % type(val).__name__)


def _toml_value(val):
    if isinstance(val, list):
        return "[" + ", ".join(_toml_scalar(x) for x in val) + "]"
    return _toml_scalar(val)


def toml_dumps(data):
    """Minimal TOML writer for tests: scalars/lists at top level, then [name] tables."""
    if not isinstance(data, dict):
        raise TypeError("TOML document must be dict, got %s" % type(data).__name__)
    lines = []
    tables = []
    for k, v in data.items():
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            lines.append("%s = %s" % (_toml_key(k), _toml_value(v)))
    for name, table in tables:
        if lines:
            lines.append("")
        lines.append("[%s]" % _toml_key(name))
        for k, v in table.items():
            lines.append("%s = %s" % (_toml_key(k), _toml_value(v)))
    return ("\n".join(lines) + "\n") if lines else ""
