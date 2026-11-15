"""Works out which root conversation a panel shows, and where the agent keeps its data.

  1. --pid PID (from the Rex action): the agent holds <data dir>/presence/<id>.lock open.
     The open files come from `lsof`, or from /proc/PID/fd when lsof is missing or fails.
     The lock names the conversation, and the directory holding presence/ is the data
     directory. Re-checked every RECHECK seconds, so resuming another conversation in
     the same process switches the panel. A dead pid keeps the last answer.
  2. the block pointer, $XDG_STATE_HOME/rex-agy-subagent/blocks/<block>, which the hook
     rewrites on every subagent start with that pane's latest root conversation (line 1)
     and, when it knew it, the data directory (line 2).
  3. --conversation ID (from the hook).
  4. the last answer, or None.

Resolver.app_dir is the data directory for the current answer: the lock's or the
pointer's when they gave one, else the configured one.
"""

import os
import re
import subprocess
import time

RECHECK = 3.0
LSOF_TIMEOUT = 2.0
PROC = "/proc"
LOCK = re.compile(r"^(.+)\.lock$")


def state_dir(env=None):
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "rex-agy-subagent")


def pointer_path(block, env=None):
    return os.path.join(state_dir(env), "blocks", block.replace("/", "_"))


def read_pointer_full(block, env=None):
    """(conversation, app_dir or None), or (None, None)."""
    try:
        with open(pointer_path(block, env), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None, None
    conv = lines[0].strip() if lines else ""
    app_dir = lines[1].strip() if len(lines) > 1 else ""
    return (conv or None), (app_dir or None) if conv else None


def read_pointer(block, env=None):
    return read_pointer_full(block, env)[0]


def write_pointer(block, conv_id, env=None, app_dir=None):
    """Atomically records conv_id (and app_dir, if known) as the block's latest root conversation."""
    path = pointer_path(block, env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(conv_id + "\n")
        if app_dir:
            f.write(app_dir + "\n")
    os.replace(tmp, path)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def lsof_files(pid, lsof="lsof", timeout=LSOF_TIMEOUT):
    """Paths pid has open according to lsof, or None when lsof is missing or fails."""
    try:
        p = subprocess.run([lsof, "-p", str(int(pid)), "-Fn"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    paths = [l[1:] for l in p.stdout.decode("utf-8", "replace").splitlines() if l.startswith("n")]
    if p.returncode != 0 and not paths:
        return None
    return paths


def proc_files(pid, proc=PROC):
    """Paths pid has open according to /proc/<pid>/fd, or None where that isn't readable."""
    d = os.path.join(proc, str(int(pid)), "fd")
    try:
        names = os.listdir(d)
    except (OSError, ValueError):
        return None
    paths = []
    for n in names:
        try:
            paths.append(os.readlink(os.path.join(d, n)))
        except OSError:
            pass
    return paths


def open_files(pid, lsof="lsof", proc=PROC):
    """Paths pid has open: lsof first, then /proc. Empty when neither works."""
    paths = lsof_files(pid, lsof)
    if paths is None:
        paths = proc_files(pid, proc)
    return paths or []


def presence_lock(paths, prefer=None):
    """(conversation, data dir) from the first <dir>/presence/<id>.lock in paths, or (None, None).

    A lock under prefer (the configured data directory) wins over others.
    """
    found = []
    for path in paths:
        d, base = os.path.split(path)
        m = LOCK.match(base)
        if m and os.path.basename(d) == "presence":
            found.append((m.group(1), os.path.dirname(d)))
    if not found:
        return None, None
    if prefer:
        want = os.path.realpath(prefer)
        for conv, app in found:
            if app == prefer or os.path.realpath(app) == want:
                return conv, app
    return found[0]


def presence_conversation(pid, app_dir=None, lsof="lsof", proc=PROC):
    """Conversation id whose presence lock pid holds open, or None."""
    return presence_lock(open_files(pid, lsof, proc), app_dir)[0]


class Resolver:
    def __init__(self, app_dir, pid=None, block=None, conversation=None, env=None,
                 lsof="lsof", clock=time.monotonic, proc=PROC):
        self.default_app_dir = app_dir
        self.app_dir = app_dir
        self.pid = pid
        self.block = block
        self.conversation = conversation
        self.env = env
        self.lsof = lsof
        self.proc = proc
        self.clock = clock
        self.last = None
        self.checked = None

    def resolve(self):
        """Fresh answer, ignoring the recheck interval. Updates self.app_dir."""
        found, app = None, None
        if self.pid:
            found, app = presence_lock(open_files(self.pid, self.lsof, self.proc), self.default_app_dir)
            if not found and self.last and not pid_alive(self.pid):
                return self.last
        if not found and self.block:
            found, app = read_pointer_full(self.block, self.env)
        if not found:
            found, app = self.conversation or self.last, None
            if found and found == self.last:
                return found  # keep the data directory it was found in
        self.app_dir = app or self.default_app_dir
        return found

    def current(self):
        """Current root conversation, re-resolved at most every RECHECK seconds."""
        now = self.clock()
        if self.checked is None or now - self.checked >= RECHECK:
            self.checked = now
            self.last = self.resolve()
        return self.last
