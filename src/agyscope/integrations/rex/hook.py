"""agyscope-rex-hook: PostToolUse hook for invoke_subagent; opens the panel on the first subagent.

The agent runs the hook after each invoke_subagent call, with a JSON payload on
stdin (see integrations/rex/hooks.json). The hook always prints {} at once and exits 0,
whatever fails, so it never blocks or changes the agent. Inside Rex (REX_SESSION and
REX_BLOCK set) it:

  1. records the pane's latest root conversation in the block pointer, with the agent's
     data directory taken from the payload's transcript path. The agent also runs the
     hook for subagents' own invoke_subagent calls; those are skipped (see root_of);
  2. unless auto_open is false, forks a detached child that opens the panel in the
     pane's window, without focus, when that window has no panel for this pane yet.
     A lock directory makes parallel starts open only one panel.

Settings (bounds, auto_open, interactive, command) come from the agyscope config file.
Only the standard library and the stdlib-only .pointer/.focus are imported at module level;
agyscope.config is imported inside main, so config/settings failures still answer {}.
"""

import json
import os
import subprocess
import sys
import time

from . import pointer
from .focus import rex_cli

LABEL_PREFIX = "agyscope:"
LOCK_STALE = 60          # seconds after which a leftover lock is ignored
POINTER_MAX_AGE = 7 * 86400
REX_TIMEOUT = 10
FLAVOR = "com.superlogical.terminal.shell"


def _check_bounds(value):
    if (not isinstance(value, list) or len(value) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in value)):
        raise ValueError("must be [x, y, w, h], each between 0 and 1")
    return [float(v) for v in value]


def settings_spec():
    """The hook's own settings for config.load(extra=...); imports agyscope.config lazily."""
    from ... import config
    return {
        "bounds": ([0.6, 0.04, 0.38, 0.42], _check_bounds),
        "auto_open": (True, config.check_bool),
        "interactive": (True, config.check_bool),
        "command": (["agyscope"], config.check_str_list),
    }


def panel_command(command, argv0):
    """command with a bare command[0] replaced by the executable next to argv0 (symlinks
    resolved), when there is one, so the panel starts without relying on PATH."""
    command = list(command)
    if command and "/" not in command[0]:
        sibling = os.path.join(os.path.dirname(os.path.realpath(argv0)), command[0])
        if os.path.isfile(sibling) and os.access(sibling, os.X_OK):
            command[0] = sibling
    return command


def label(block):
    return LABEL_PREFIX + block


def parse(payload):
    """The decoded payload of a successful invoke_subagent call, or None."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    call = data.get("toolCall") or {}
    if not isinstance(call, dict) or call.get("name") != "invoke_subagent":
        return None
    if data.get("error"):
        return None
    return data


def app_dir_of(data):
    """The agent's data directory from the payload's transcript or artifact path, or None.

    Both live under <data dir>/brain/<conversation>/...
    """
    for key in ("transcriptPath", "artifactDirectoryPath"):
        p = data.get(key)
        if not isinstance(p, str) or not p:
            continue
        if p.startswith("file://"):
            p = p[len("file://"):]
        parts = p.rstrip("/").split("/")
        if "brain" in parts:
            i = len(parts) - 1 - parts[::-1].index("brain")
            if i > 0:
                return "/".join(parts[:i]) or "/"
    return None


def root_of(data, app_dir):
    """The root conversation to record for the pane, or None.

    rootConversationId when the agent sends it. Otherwise conversationId, but only when
    that conversation holds a presence lock (only root conversations do), so a subagent
    starting its own subagent doesn't replace the pane's root. Without a known data
    directory the lock can't be checked, and conversationId is taken as before.
    """
    root = data.get("rootConversationId")
    if isinstance(root, str) and root:
        return root
    conv = data.get("conversationId")
    if not isinstance(conv, str) or not conv:
        return None
    if app_dir and not os.path.exists(os.path.join(app_dir, "presence", conv + ".lock")):
        return None
    return conv


def window_of(view, block):
    for w in (view or {}).get("windows") or []:
        for layer in w.get("layers") or []:
            for b in layer.get("blocks") or []:
                if b.get("block_id") == block:
                    return w.get("window_id")
    return None


def has_panel(view, block):
    want = label(block)
    for w in (view or {}).get("windows") or []:
        for layer in w.get("layers") or []:
            for b in layer.get("blocks") or []:
                if b.get("label") == want:
                    return True
    return False


def new_layer_request(session, window, block, conversation, bounds, panel_cmd, app_dir=None,
                      interactive=True):
    """panel_cmd: the panel argv (list). interactive: Rex runs the command through the user's
    shell with its interactive startup files (~/.zshrc), so the panel finds what the user's
    terminals find."""
    command = list(panel_cmd) + ["--conversation", conversation, "--rex-block", block]
    if app_dir:
        command += ["--app-dir", app_dir]
    return {
        "session_id": session, "window_id": window, "bounds": bounds, "focus": False,
        "layout": {"block": {"flavor": FLAVOR, "label": label(block),
                             "options": {"command": command, "interactive": interactive}}},
    }


def open_panel(env, block, conversation, settings, panel_cmd, app_dir=None):
    """Opens the panel unless one exists; returns a short status string (for tests)."""
    lock = pointer.pointer_path(block, env) + ".lock"
    try:
        if time.time() - os.stat(lock).st_mtime > LOCK_STALE:
            os.rmdir(lock)
    except OSError:
        pass
    try:
        os.mkdir(lock)
    except OSError:
        return "locked"
    try:
        rex = rex_cli(env)
        session = env["REX_SESSION"]
        out = subprocess.run([rex, "session", "inspect", session, "--json"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=REX_TIMEOUT, check=False, env=env)
        if out.returncode != 0:
            return "inspect failed"
        view = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
        if has_panel(view, block):
            return "exists"
        window = window_of(view, block)
        if not window:
            return "no window"
        req = new_layer_request(session, window, block, conversation, settings["bounds"], panel_cmd, app_dir,
                                settings["interactive"])
        subprocess.run([rex, "api", "call", "session.new_layer", "-"], input=json.dumps(req).encode(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=REX_TIMEOUT,
                       check=False, env=env)
        return "opened"
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def housekeeping(env):
    d = os.path.join(pointer.state_dir(env), "blocks")
    now = time.time()
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        p = os.path.join(d, n)
        try:
            if os.path.isfile(p) and now - os.stat(p).st_mtime > POINTER_MAX_AGE:
                os.unlink(p)
        except OSError:
            pass


def detach(fn):
    """Runs fn in a detached grandchild; the caller returns at once."""
    try:
        pid = os.fork()
    except OSError:
        return
    if pid > 0:
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        return
    try:
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
        fn()
    except BaseException:
        pass
    os._exit(0)


def main(stdin=None, stdout=None, env=None, argv0=None):
    """Entry point of agyscope-rex-hook. Always writes {} first and returns 0."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    env = dict(os.environ) if env is None else env
    argv0 = sys.argv[0] if argv0 is None else argv0
    try:
        payload = stdin.read()
    except Exception:
        payload = ""
    try:
        stdout.write("{}\n")
        stdout.flush()
    except Exception:
        pass
    try:
        block, session = env.get("REX_BLOCK"), env.get("REX_SESSION")
        if not block or not session:
            return 0
        data = parse(payload)
        if not data:
            return 0
        app_dir = app_dir_of(data)
        root = root_of(data, app_dir)
        if not root:
            return 0
        pointer.write_pointer(block, root, env, app_dir)
        from ... import config
        settings, _ = config.load({}, env, extra=settings_spec())
        if not settings["auto_open"]:
            return 0
        panel_cmd = panel_command(settings["command"], argv0)

        def child():
            open_panel(env, block, root, settings, panel_cmd, app_dir)
            housekeeping(env)

        detach(child)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
