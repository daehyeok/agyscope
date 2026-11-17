"""Theme loading and state style mapping from Textual CSS variables."""

import glob
import os
import re
import tomllib
from typing import Optional

from rich.style import Style
from textual.color import Color, ColorParseError
from textual.theme import Theme

# Fallback state/style palette (used when a variable cannot be resolved or before mount).
_COLORS = {
    "working": "yellow",
    "running": "yellow",
    "done": "green",
    "error": "red",
    "failed": "red",
    "warn": "red",
    "quiet": "magenta",
    "dim": "magenta",
    "waiting": "blue",
    "msg": "cyan",
    "cmd": "cyan",
}
_BOLD = ("cmd", "msg", "running", "step", "warn")
FALLBACK_STYLES = {
    name: Style(color=_COLORS.get(name), bold=name in _BOLD or None)
    for name in set(_COLORS) | set(_BOLD)
}

STATE_VARS = {
    "working": "warning",
    "running": "warning",
    "done": "success",
    "error": "error",
    "failed": "error",
    "warn": "error",
    "waiting": "primary",
    "msg": "accent",
    "cmd": "accent",
    "quiet": "text-muted",
    "dim": "text-muted",
}

_COLOR_FIELDS = frozenset({
    "primary",
    "secondary",
    "warning",
    "error",
    "success",
    "accent",
    "foreground",
    "background",
    "surface",
    "panel",
    "boost",
})
_BOOL_FIELDS = frozenset({"dark", "ansi"})
_FLOAT_FIELDS = frozenset({"luminosity_spread", "text_alpha"})
_THEME_FIELDS = frozenset({"name", "variables"}) | _COLOR_FIELDS | _BOOL_FIELDS | _FLOAT_FIELDS

_AUTO_RE = re.compile(r"auto(?:\s+(\d+(?:\.\d+)?)%)?$")


def _parse_and_composite(variables: dict[str, str], value: str) -> Optional[Color]:
    try:
        color = Color.parse(value)
    except (ColorParseError, TypeError, ValueError):
        return None
    if color.a < 1:
        bg_val = variables.get("background")
        if not bg_val:
            return None
        try:
            bg = Color.parse(bg_val)
        except (ColorParseError, TypeError, ValueError):
            return None
        color = bg + color
    return color


def _resolve(variables: dict[str, str], value: str) -> Optional[Color]:
    """Resolves a CSS color variable value (including alpha and `auto N%`) into a Color."""
    if not isinstance(value, str) or not value.strip():
        return None
    color = _parse_and_composite(variables, value)
    if color is not None:
        return color
    m = _AUTO_RE.match(value.strip())
    if m:
        pct = float(m.group(1)) if m.group(1) is not None else 100.0
        try:
            bg = Color.parse(variables["background"])
            fg = Color.parse(variables["foreground"])
            return bg.blend(fg, pct / 100.0)
        except (KeyError, ColorParseError, TypeError, ValueError):
            fg_muted = variables.get("foreground-muted")
            if isinstance(fg_muted, str) and fg_muted.strip():
                return _parse_and_composite(variables, fg_muted)
    return None


def styles_from(variables: dict[str, str]) -> dict[str, Style]:
    """Builds the state/step Rich Style mapping from a theme's CSS variables."""
    out = {}
    for name, fallback in FALLBACK_STYLES.items():
        raw = variables.get("agyscope-" + name)
        if raw is None and name in STATE_VARS:
            raw = variables.get(STATE_VARS[name])
        color = _resolve(variables, raw) if raw is not None else None
        if color is not None:
            out[name] = Style(color=color.rich_color, bold=fallback.bold)
        else:
            out[name] = fallback
    return out


def _validate_theme_data(data: dict, stem: str) -> dict:
    unknown = set(data) - _THEME_FIELDS
    if unknown:
        raise ValueError("unknown key %r" % (sorted(unknown)[0],))
    out = dict(data)
    out.setdefault("name", stem)
    if not isinstance(out["name"], str) or not out["name"].strip():
        raise ValueError("name must be a non-empty string")
    if "primary" not in out:
        raise ValueError("missing required 'primary'")
    if not isinstance(out["primary"], str) or not out["primary"].strip():
        raise ValueError("primary must be a non-empty string")
    for k, v in out.items():
        if k in _COLOR_FIELDS:
            if not isinstance(v, str):
                raise ValueError("%s must be a string" % k)
        elif k in _BOOL_FIELDS:
            if not isinstance(v, bool):
                raise ValueError("%s must be true or false" % k)
        elif k in _FLOAT_FIELDS:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("%s must be a number" % k)
        elif k == "variables":
            if not isinstance(v, dict) or not all(
                isinstance(vk, str) and isinstance(vv, str) for vk, vv in v.items()
            ):
                raise ValueError("variables must be a table of strings")
    return out


def load_user_themes(directory: str) -> tuple[list[Theme], list[str]]:
    """Loads *.toml user themes from `directory`, returning (themes, warnings)."""
    if not directory or not os.path.isdir(directory):
        return [], []
    loaded = []
    warnings = []
    for path in sorted(glob.glob(os.path.join(directory, "*.toml"))):
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
            stem = os.path.splitext(os.path.basename(path))[0]
            data = _validate_theme_data(raw, stem)
            theme = Theme(**data)
            theme.to_color_system().generate()
            loaded.append(theme)
        except Exception as e:
            warnings.append("theme %s: %s; skipped" % (path, e))
    return loaded, warnings
