"""Works out which root conversation a panel shows, and where the agent keeps its data.

  1. --pid PID (from the Rex action): the agent holds <data dir>/presence/<id>.lock open.
     The open files come from `lsof`, or from /proc/PID/fd when lsof is missing or fails.
     The lock names the conversation, and the directory holding presence/ is the data
     directory. Re-checked every RECHECK seconds, so resuming another conversation in
     the same process switches the panel. A dead pid keeps the last answer.
  2. the fallback, e.g. the Rex block pointer, which the hook rewrites on every subagent
     start with that pane's latest root conversation and, when it knew it, the data
     directory. It returns (conversation, data directory), either may be None.
  3. --conversation ID (from the hook).
  4. the last answer, or None.

Resolver.app_dir is the data directory for the current answer: the lock's or the
fallback's when they gave one, else the configured one.
"""

import os
import re
import subprocess
import time

RECHECK = 3.0
LSOF_TIMEOUT = 2.0
PROC = "/proc"
LOCK = re.compile(r"^(.+)\.lock$")


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


def parse_lsof(data):
    """{pid: [path, ...]} from `lsof -F pn` output (bytes or str); other fields are ignored."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    out, pid = {}, None
    for line in (data or "").splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
            if pid is not None:
                out.setdefault(pid, [])
        elif line.startswith("n") and pid is not None:
            out[pid].append(line[1:])
    return out


def held_locks(paths):
    """[(conversation, data dir)] for every <dir>/presence/<id>.lock in paths, each once, in order."""
    seen, out = set(), []
    for path in paths:
        d, base = os.path.split(path)
        m = LOCK.match(base)
        if m and os.path.basename(d) == "presence" and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append((m.group(1), os.path.dirname(d)))
    return out


def lock_paths(app_dirs):
    """Every presence/*.lock file under the given data directories (existing ones only)."""
    out = []
    for app in dict.fromkeys(a for a in app_dirs if a):
        d = os.path.join(app, "presence")
        try:
            names = os.listdir(d)
        except OSError:
            continue
        out.extend(os.path.join(d, n) for n in sorted(names) if LOCK.match(n))
    return out


def proc_all_files(proc=PROC):
    """Every path any process has open according to /proc/*/fd (empty where /proc is missing)."""
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return []
    out = []
    for p in pids:
        out.extend(proc_files(p, proc) or [])
    return out


class Resolver:
    """fallback: optional callable returning (conversation, app_dir), either may be None;
    asked when the pid gives no answer (e.g. the Rex block pointer)."""

    def __init__(self, app_dir, pid=None, conversation=None, fallback=None,
                 lsof="lsof", clock=time.monotonic, proc=PROC):
        self.default_app_dir = app_dir
        self.app_dir = app_dir
        self.pid = pid
        self.conversation = conversation
        self.fallback = fallback
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
        if not found and self.fallback:
            found, app = self.fallback()
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
