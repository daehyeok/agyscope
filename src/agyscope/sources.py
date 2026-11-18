"""Which root conversations a panel shows, collected off the UI's critical path with AnyIO.

Sources, in precedence order (a conversation keeps its first place):

  1. pane     the conversation the pane's agent process holds presence/<id>.lock open
              for (--pid, through lsof or /proc), else the pane fallback (the Rex block
              pointer), else the first --conversation id; the last answer survives a
              dead pid
  2. pinned   the other --conversation ids
  3. watch    the top-level watch list of the link store, in attach order
  4. live     with `live` on: every conversation whose lock some process holds open, in
              the configured data directory and every data directory a root came from,
              newest lock first. A released lock keeps its conversation for LIVE_GRACE
              seconds (ended_at set), then it drops out.

Top-level hidden entries remove every root except the pane's.

Everything slow runs in Collector.collect(): `lsof` through anyio.run_process under
move_on_after (a timed-out or cancelled lsof is killed and reaped), the /proc scans in a
worker thread (one at a time). Collector.run() repeats it every RECHECK seconds, or at
once after poke(), and hands each RootSet to `publish`. Inside the panel it runs as an
async Textual worker on the app's own event loop, so publish needs no locking.
`--once` uses anyio.run(collector.collect).
"""

import dataclasses
import os
import subprocess
import time

import anyio

from . import resolve

RECHECK = 2.0
LIVE_GRACE = 600.0
LSOF_TIMEOUT = 2.0
CHUNK = 256


@dataclasses.dataclass(frozen=True)
class Root:
    conv: str
    app_dir: str
    source: str            # "pane", "pinned", "watch" or "live"
    live: bool = False     # some process holds its presence lock now
    ended_at: float = None  # live root whose lock was released at this time


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


class Collector:
    def __init__(self, app_dir, pid=None, pane_fallback=None, conversations=(), links=None, live=True,
                 lsof="lsof", proc=resolve.PROC, clock=time.time, recheck=RECHECK, grace=LIVE_GRACE,
                 lsof_timeout=LSOF_TIMEOUT):
        self.app_dir = app_dir
        self.pid = pid
        self.pane_fallback = pane_fallback
        self.conversations = [c for c in conversations if c]
        self.links = links
        self.live = live
        self.lsof = lsof
        self.proc = proc
        self.clock = clock
        self.recheck = recheck
        self.grace = grace
        self.lsof_timeout = lsof_timeout
        self.services = []       # async callables(collector) started next to the loop in run(), e.g. the poke listener
        self.on_message = None   # callable(str) the panel sets to show service messages
        self.roots = ()          # the latest RootSet
        self.wake = None         # anyio.Event while run() is going
        self._pane = None        # (conv, app_dir): the last pane answer
        self._held = []          # [(conv, app_dir, lock mtime)] from the last good live scan
        self._live_seen = {}     # conv -> (app_dir, lock mtime, last seen)
        self._limiter = None

    # lsof and /proc

    async def _lsof(self, args):
        """lsof's stdout, b"" when it found nothing, None when it is missing, failed or timed out."""
        with anyio.move_on_after(self.lsof_timeout):
            try:
                r = await anyio.run_process([self.lsof] + list(args), check=False, stderr=subprocess.DEVNULL)
            except OSError:
                return None
            if r.returncode != 0 and not r.stdout:
                return b"" if r.returncode == 1 else None
            return r.stdout
        return None

    async def _thread(self, fn, *args):
        if self._limiter is None:
            self._limiter = anyio.CapacityLimiter(1)
        return await anyio.to_thread.run_sync(fn, *args, limiter=self._limiter)

    # sources

    def _fallback(self):
        if self.pane_fallback is None:
            return None, None
        try:
            return self.pane_fallback()
        except Exception:
            return None, None

    async def pane_root(self):
        """(conv, app_dir) of the pane, or None."""
        found = app = None
        if self.pid:
            out = await self._lsof(["-n", "-P", "-Fpn", "-p", str(int(self.pid))])
            if out is None:
                paths = await self._thread(resolve.proc_files, self.pid, self.proc) or []
            else:
                paths = [p for ps in resolve.parse_lsof(out).values() for p in ps]
            found, app = resolve.presence_lock(paths, self.app_dir)
            if not found and self._pane and not resolve.pid_alive(self.pid):
                return self._pane
        if not found:
            found, app = self._fallback()
        if not found and self.conversations:
            found, app = self.conversations[0], None
        if not found:
            return self._pane
        if self._pane and found == self._pane[0] and not app:
            return self._pane  # keep the data directory it was found in
        self._pane = (found, app or self.app_dir)
        return self._pane

    async def held(self, app_dirs):
        """[(conv, app_dir, lock mtime)] of the held presence locks, or None when unknown."""
        paths = resolve.lock_paths(app_dirs)
        if not paths:
            return []
        open_paths = []
        for chunk in _chunks(paths, CHUNK):
            out = await self._lsof(["-n", "-P", "-Fpn", "--"] + chunk)
            if out is None:
                everything = set(await self._thread(resolve.proc_all_files, self.proc))
                if not everything:
                    return None
                open_paths = [p for p in paths if p in everything]
                break
            open_paths += [p for ps in resolve.parse_lsof(out).values() for p in ps]
        out = []
        for conv, app in resolve.held_locks(open_paths):
            try:
                mtime = os.stat(os.path.join(app, "presence", conv + ".lock")).st_mtime
            except OSError:
                mtime = 0
            out.append((conv, app, mtime))
        return out

    def _top(self):
        if self.links is None:
            return [], set()
        try:
            return self.links.top(), self.links.hidden(None)
        except OSError:
            return [], set()

    def initial(self):
        """A RootSet from the cheap sources only (no subprocess), for the panel's first paint."""
        pane = None
        found, app = self._fallback()
        if found:
            pane = (found, app or self.app_dir)
        elif self.conversations and not self.pid:
            pane = (self.conversations[0], self.app_dir)
        top, hidden = self._top()
        self.roots = self._merge(pane, top, hidden, [], self.clock())
        return self.roots

    async def collect(self):
        """Fresh RootSet from every source."""
        top, hidden = self._top()
        result = {}

        async def pane():
            result["pane"] = await self.pane_root()

        async def live():
            dirs = [self.app_dir] + [a for _, a in top if a]
            if self._pane:
                dirs.append(self._pane[1])
            result["held"] = await self.held(dirs)

        async with anyio.create_task_group() as tg:
            tg.start_soon(pane)
            if self.live:
                tg.start_soon(live)
        if self.live:
            if result.get("held") is not None:
                self._held = result["held"]
            held = self._held
        else:
            held = []
        self.roots = self._merge(result.get("pane"), top, hidden, held, self.clock())
        return self.roots

    def _merge(self, pane, top, hidden, held, now):
        held_ids = {conv for conv, _, _ in held}
        for conv, app, mtime in held:
            self._live_seen[conv] = (app, mtime, now)
        roots, seen = [], set()

        def add(conv, app, source, ended_at=None):
            if not conv or conv in seen or (source != "pane" and conv in hidden):
                return
            seen.add(conv)
            roots.append(Root(conv, app or self.app_dir, source, conv in held_ids, ended_at))

        if pane:
            add(pane[0], pane[1], "pane")
        for conv in self.conversations:
            add(conv, None, "pinned")
        for conv, app in top:
            add(conv, app, "watch")
        if self.live:
            for conv, (app, mtime, last) in sorted(self._live_seen.items(), key=lambda e: -e[1][1]):
                ended = None if conv in held_ids else last
                if ended is not None and now - ended > self.grace:
                    del self._live_seen[conv]
                    continue
                add(conv, app, "live", ended)
        return tuple(roots)

    # loop

    def report(self, message):
        if self.on_message is not None:
            self.on_message(message)

    def poke(self):
        """Re-collect now (from the poke listener or after an attach/detach in the panel)."""
        if self.wake is not None:
            self.wake.set()

    async def run(self, publish):
        self.wake = anyio.Event()
        async with anyio.create_task_group() as tg:
            for service in self.services:
                tg.start_soon(service, self)
            while True:
                publish(await self.collect())
                with anyio.move_on_after(self.recheck):
                    await self.wake.wait()
                self.wake = anyio.Event()
