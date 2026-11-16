import os
import stat
import tempfile
import unittest

import helpers  # noqa: F401
from agyscope import resolve
from agyscope.integrations.rex import pointer


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.app, "presence"))
        self.env = {"XDG_STATE_HOME": os.path.join(self.tmp, "state")}
        self.lsof = os.path.join(self.tmp, "lsof")
        self.answer = os.path.join(self.tmp, "answer")
        with open(self.lsof, "w") as f:
            f.write('#!/bin/sh\nprintf "p%%s\\nn/dev/null\\n" "$2"\ncat "%s" 2>/dev/null\n' % self.answer)
        os.chmod(self.lsof, os.stat(self.lsof).st_mode | stat.S_IEXEC)

    def holds(self, conv):
        with open(self.answer, "w") as f:
            f.write("n%s/presence/%s.lock\n" % (self.app, conv))

    def test_presence(self):
        self.holds("conv-a")
        self.assertEqual(resolve.presence_conversation(os.getpid(), self.app, self.lsof), "conv-a")

    def test_pid_switches_conversation(self):
        clock = [0.0]
        r = resolve.Resolver(self.app, pid=os.getpid(), lsof=self.lsof, clock=lambda: clock[0])
        self.holds("conv-a")
        self.assertEqual(r.current(), "conv-a")
        self.holds("conv-b")
        clock[0] = 1.0
        self.assertEqual(r.current(), "conv-a")  # not rechecked yet
        clock[0] = 3.5
        self.assertEqual(r.current(), "conv-b")

    def test_missing_lsof_uses_fallback(self):
        r = resolve.Resolver(self.app, pid=os.getpid(), fallback=lambda: ("conv-p", None),
                             lsof=os.path.join(self.tmp, "no-lsof"))
        self.assertEqual(r.resolve(), "conv-p")

    def test_dead_pid_keeps_last(self):
        self.holds("conv-a")
        r = resolve.Resolver(self.app, pid=os.getpid(), lsof=self.lsof)
        self.assertEqual(r.resolve(), "conv-a")
        r.last = "conv-a"
        r.pid = 2 ** 22 + 12345  # almost surely not running
        r.fallback = lambda: ("other", None)
        self.assertEqual(r.resolve(), "conv-a")

    def test_conversation_flag_and_fallback(self):
        value = [(None, None)]
        r = resolve.Resolver(self.app, fallback=lambda: value[0], conversation="from-hook")
        self.assertEqual(r.resolve(), "from-hook")
        value[0] = ("newer", None)
        self.assertEqual(r.resolve(), "newer")

    def test_fallback_none_value(self):
        r = resolve.Resolver(self.app, fallback=lambda: (None, None), conversation="conv-c")
        self.assertEqual(r.resolve(), "conv-c")
        self.assertEqual(r.app_dir, self.app)

    def test_no_block_attributes(self):
        r = resolve.Resolver(self.app)
        self.assertFalse(hasattr(r, "block"))
        self.assertFalse(hasattr(r, "env"))

    def test_nothing(self):
        self.assertIsNone(resolve.Resolver(self.app).resolve())

    def fake_proc(self, pid, targets):
        proc = os.path.join(self.tmp, "proc")
        fd = os.path.join(proc, str(pid), "fd")
        os.makedirs(fd, exist_ok=True)
        for i, t in enumerate(targets):
            os.symlink(t, os.path.join(fd, str(i)))
        return proc

    def test_proc_fallback_without_lsof(self):
        other = os.path.join(self.tmp, "data", "agent")
        proc = self.fake_proc(os.getpid(), ["/dev/null", os.path.join(other, "presence", "conv-p.lock")])
        r = resolve.Resolver(self.app, pid=os.getpid(),
                             lsof=os.path.join(self.tmp, "no-lsof"), proc=proc)
        self.assertEqual(r.resolve(), "conv-p")
        self.assertEqual(r.app_dir, other)

    def test_failing_lsof_uses_proc(self):
        bad = os.path.join(self.tmp, "bad-lsof")
        with open(bad, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(bad, os.stat(bad).st_mode | stat.S_IEXEC)
        proc = self.fake_proc(os.getpid(), [os.path.join(self.app, "presence", "conv-q.lock")])
        self.assertEqual(resolve.presence_conversation(os.getpid(), self.app, bad, proc), "conv-q")

    def test_data_dir_from_lock_beats_config(self):
        other = os.path.join(self.tmp, "elsewhere")
        with open(self.answer, "w") as f:
            f.write("n%s/presence/conv-x.lock\n" % other)
        r = resolve.Resolver(self.app, pid=os.getpid(), lsof=self.lsof)
        self.assertEqual(r.resolve(), "conv-x")
        self.assertEqual(r.app_dir, other)

    def test_configured_dir_preferred_among_locks(self):
        other = os.path.join(self.tmp, "elsewhere")
        paths = [os.path.join(other, "presence", "conv-o.lock"), os.path.join(self.app, "presence", "conv-a.lock")]
        self.assertEqual(resolve.presence_lock(paths, self.app), ("conv-a", self.app))
        self.assertEqual(resolve.presence_lock(paths), ("conv-o", other))

    def test_not_a_presence_lock(self):
        self.assertEqual(resolve.presence_lock(["/x/locks/conv.lock", "/x/presence/notalock"]), (None, None))

    def test_no_pointer_api(self):
        for name in ("state_dir", "pointer_path", "read_pointer_full", "read_pointer", "write_pointer"):
            self.assertFalse(hasattr(resolve, name), name)

    def test_pointer_with_data_dir(self):
        pointer.write_pointer("block:3", "conv-r", self.env, app_dir="/data/y")
        r = resolve.Resolver(self.app, fallback=lambda: pointer.read_pointer_full("block:3", self.env),
                             lsof=os.path.join(self.tmp, "no-lsof"))
        self.assertEqual(r.resolve(), "conv-r")
        self.assertEqual(r.app_dir, "/data/y")

    def test_old_pointer_uses_configured_dir(self):
        pointer.write_pointer("block:4", "conv-s", self.env)
        r = resolve.Resolver(self.app, fallback=lambda: pointer.read_pointer_full("block:4", self.env))
        self.assertEqual(r.resolve(), "conv-s")
        self.assertEqual(r.app_dir, self.app)


if __name__ == "__main__":
    unittest.main()
