"""Runs the configured pager on a temporary copy of a subagent's view."""

import os
import re
import subprocess
import tempfile

DEFAULT = ["less", "-R", "+G"]


def argv(pager, path, prompt=None):
    """Pager argv for path: replaces any "{file}" argument, else appends the path.

    prompt is added (as less -P) only for the built-in default pager.
    """
    cmd = list(pager)
    if prompt and cmd == DEFAULT:
        cmd += ["-P", re.sub(r"([?:.%\\])", r"\\\1", prompt)]
    if any("{file}" in a for a in cmd):
        return [a.replace("{file}", path) for a in cmd]
    return cmd + [path]


def show(pager, lines, title):
    """Writes lines to a temp file, runs the pager on it, removes the file.

    Returns None on success or a one-line error. The caller must have suspended curses.
    """
    fd, path = tempfile.mkstemp(prefix="agy-subagent-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as w:
            w.write("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o400)
        except OSError:
            pass
        try:
            subprocess.call(argv(pager, path, " %s (read-only)  q back, / search" % title))
        except OSError as e:
            return "pager %s: %s" % (pager[0], e.strerror or e)
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
