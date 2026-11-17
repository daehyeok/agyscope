import os
import stat
import tempfile
import time
import unittest

import helpers  # noqa: F401
from agyscope import resolve, sources
from agyscope.links import LinkStore
from agyscope.sources import Collector

A, B, C, P = "aaaa-live", "bbbb-live", "cccc-watch", "pppp-pane"


def fake_lsof(body):
    """An executable standing in for lsof; body is shell code."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "lsof")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def lsof_printing(text, rc=0):
    d = tempfile.mkdtemp()
    out = os.path.join(d, "out")
    with open(out, "w") as f:
        f.write(text)
    return fake_lsof('cat "%s"; exit %d' % (out, rc))


def app_with_locks(*convs, mtimes=None):
    app = tempfile.mkdtemp()
    os.makedirs(os.path.join(app, "presence"))
    for i, c in enumerate(convs):
        p = os.path.join(app, "presence", c + ".lock")
        open(p, "w").close()
        t = (mtimes or {}).get(c, 1000 + i)
        os.utime(p, (t, t))
    return app


def held_output(app, *convs, pid=4242):
    lines = ["p%d" % pid]
    for c in convs:
        lines += ["f9", "n" + os.path.join(app, "presence", c + ".lock")]
    return "\n".join(lines) + "\n"


def roots(rs):
    return [(r.conv, r.source, r.live, r.ended_at is not None) for r in rs]


class ParseTest(unittest.TestCase):
    def test_parse_lsof_and_held_locks(self):
        out = b"p1\nf3\nn/d/presence/x.lock\np2\nf4\nn/e/presence/y.lock\nn/e/other\n"
        self.assertEqual(resolve.parse_lsof(out), {1: ["/d/presence/x.lock"], 2: ["/e/presence/y.lock", "/e/other"]})
        self.assertEqual(resolve.parse_lsof(""), {})
        self.assertEqual(resolve.held_locks(["/d/presence/x.lock", "/e/presence/y.lock", "/d/presence/x.lock",
                                             "/e/other", "/f/notpresence/z.lock"]),
                         [("x", "/d"), ("y", "/e")])

    def test_lock_paths(self):
        app = app_with_locks(A, B)
        open(os.path.join(app, "presence", "junk.txt"), "w").close()
        self.assertEqual([os.path.basename(p) for p in resolve.lock_paths([app, app, None, "/nope"])],
                         [A + ".lock", B + ".lock"])


class CollectorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.links = LinkStore(os.path.join(tempfile.mkdtemp(), "agyscope"))

    async def test_live_newest_first_and_stale_lock_ignored(self):
        app = app_with_locks(A, B, "stale", mtimes={A: 100, B: 200, "stale": 300})
        col = Collector(app, links=self.links, lsof=lsof_printing(held_output(app, A, B)))
        rs = await col.collect()
        self.assertEqual(roots(rs), [(B, "live", True, False), (A, "live", True, False)])
        self.assertTrue(all(r.app_dir == app for r in rs))

    async def test_nothing_held_is_exit_1(self):
        app = app_with_locks(A)
        col = Collector(app, lsof=lsof_printing("", rc=1))
        self.assertEqual(await col.collect(), ())

    async def test_order_pane_pinned_watch_live_and_dedupe(self):
        app = app_with_locks(A, P, mtimes={A: 100, P: 200})
        self.links.attach(C, None, app_dir="/other/app")
        self.links.attach(A)  # also live: keeps its watch place
        col = Collector(app, conversations=["pin-1"], links=self.links,
                        pane_fallback=lambda: (P, app), lsof=lsof_printing(held_output(app, A, P)))
        rs = await col.collect()
        self.assertEqual([(r.conv, r.source) for r in rs],
                         [(P, "pane"), ("pin-1", "pinned"), (C, "watch"), (A, "watch")])
        self.assertEqual(rs[2].app_dir, "/other/app")
        self.assertTrue(rs[0].live and rs[3].live)

    async def test_hidden_top_removes_all_but_pane(self):
        app = app_with_locks(A, P)
        self.links.detach(A)
        self.links.detach(P)
        col = Collector(app, links=self.links, pane_fallback=lambda: (P, app),
                        lsof=lsof_printing(held_output(app, A, P)))
        self.assertEqual([r.conv for r in await col.collect()], [P])

    async def test_ended_live_root_kept_for_grace_then_dropped(self):
        app = app_with_locks(A)
        now = [1000.0]
        out = os.path.join(tempfile.mkdtemp(), "out")
        with open(out, "w") as f:
            f.write(held_output(app, A))
        col = Collector(app, lsof=fake_lsof('cat "%s"' % out), clock=lambda: now[0], grace=600)
        self.assertEqual(roots(await col.collect()), [(A, "live", True, False)])
        with open(out, "w") as f:
            f.write("")
        now[0] = 1100.0
        self.assertEqual(roots(await col.collect()), [(A, "live", False, True)])
        now[0] = 1701.0
        self.assertEqual(await col.collect(), ())

    async def test_live_off(self):
        app = app_with_locks(A)
        col = Collector(app, live=False, conversations=["c1"], lsof=lsof_printing(held_output(app, A)))
        self.assertEqual([(r.conv, r.source) for r in await col.collect()], [("c1", "pane")])

    async def test_pane_from_pid_and_dead_pid_keeps_last(self):
        app = app_with_locks(P)
        col = Collector(app, pid=os.getpid(), live=False, lsof=lsof_printing(held_output(app, P, pid=os.getpid())))
        self.assertEqual([(r.conv, r.source) for r in await col.collect()], [(P, "pane")])
        col.lsof = lsof_printing("", rc=1)
        col.pid = 999999  # dead
        self.assertEqual([r.conv for r in await col.collect()], [P])

    async def test_hanging_lsof_is_killed(self):
        app = app_with_locks(A)
        pidfile = os.path.join(tempfile.mkdtemp(), "pid")
        col = Collector(app, lsof=fake_lsof('echo $$ > "%s"; exec sleep 30' % pidfile), lsof_timeout=0.3,
                        proc="/nonexistent")
        t0 = time.monotonic()
        self.assertEqual(await col.collect(), ())  # unknown: nothing live yet
        self.assertLess(time.monotonic() - t0, 3)
        pid = int(open(pidfile).read())
        self.assertFalse(resolve.pid_alive(pid))

    async def test_failed_scan_keeps_previous_live_set(self):
        app = app_with_locks(A)
        col = Collector(app, lsof=lsof_printing(held_output(app, A)), proc="/nonexistent")
        await col.collect()
        col.lsof = "/nonexistent/lsof"  # missing lsof, no /proc: unknown
        self.assertEqual(roots(await col.collect()), [(A, "live", True, False)])

    async def test_initial_uses_cheap_sources_only(self):
        self.links.attach(C)
        col = Collector("/app", conversations=["c1"], links=self.links, lsof="/nonexistent/lsof")
        self.assertEqual([(r.conv, r.source) for r in col.initial()], [("c1", "pane"), (C, "watch")])

    async def test_run_publishes_and_poke_wakes(self):
        import anyio
        app = app_with_locks()
        col = Collector(app, conversations=["c1"], lsof="/nonexistent/lsof", recheck=30)
        published = []
        async with anyio.create_task_group() as tg:
            tg.start_soon(col.run, published.append)
            with anyio.fail_after(2):
                while not published:
                    await anyio.sleep(0.01)
                col.conversations.append("c2")
                col.poke()
                while len(published) < 2:
                    await anyio.sleep(0.01)
            tg.cancel_scope.cancel()
        self.assertEqual([r.conv for r in published[-1]], ["c1", "c2"])

    def test_module_constants(self):
        self.assertEqual(sources.LIVE_GRACE, 600.0)
