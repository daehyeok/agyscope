"""Rex block focus: `rex focus <block>`, used to return focus to the agent after `close`.

Standard library only.
"""

import os
import shutil
import subprocess

MACOS_REX = "Library/Application Support/com.superlogical.rex/RexServer/Versions/Current/Rex Server.app/Contents/MacOS/rex"


def rex_cli(env=None):
    if env is None:
        env = os.environ
    d = env.get("REX_BIN_DIR")
    if d and os.access(os.path.join(d, "rex"), os.X_OK):
        return os.path.join(d, "rex")
    which = shutil.which("rex", path=env.get("PATH"))
    if which:
        return which
    home = env.get("HOME")
    if home:
        mac = os.path.join(home, MACOS_REX)
        if os.access(mac, os.X_OK):
            return mac
    return "rex"


def focus_block(block, env=None, timeout=2.0):
    if env is None:
        env = os.environ
    if not block or not env.get("REX_SESSION"):
        return False
    try:
        res = subprocess.run([rex_cli(env), "focus", block],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             timeout=timeout, check=False, env=env)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
