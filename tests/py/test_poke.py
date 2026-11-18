import json
import os
import socket
import tempfile
import unittest

import anyio

import helpers  # noqa: F401
from agyscope import poke


class FakeCollector:
    def __init__(self):
        self.pokes = 0
        self.messages = []

    def poke(self):
        self.pokes += 1

    def report(self, message):
        self.messages.append(message)


def short_state():
    # macOS AF_UNIX paths are limited to 104 bytes; tempfile.mkdtemp() under /var/folders is long
    return tempfile.mkdtemp(dir="/tmp")


class PokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_listener_answers_wakes_and_cleans_up(self):
        state = short_state()
        path = poke.socket_path(state)
        col = FakeCollector()
        run = poke.service(path, shown=lambda: {"M", "W"})
        async with anyio.create_task_group() as tg:
            tg.start_soon(run, col)
            with anyio.fail_after(3):
                while not os.path.exists(path):
                    await anyio.sleep(0.01)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            reached = await anyio.to_thread.run_sync(poke.notify, state)
            self.assertEqual(reached, [{"M", "W"}])
            self.assertEqual(col.pokes, 1)
            pinged = await anyio.to_thread.run_sync(lambda: poke.notify(state, op="ping"))
            self.assertEqual(pinged, [{"M", "W"}])
            self.assertEqual(col.pokes, 1)
            tg.cancel_scope.cancel()
        self.assertFalse(os.path.exists(path))

    async def test_bad_requests_answered_listener_survives(self):
        state = short_state()
        path = poke.socket_path(state)
        col = FakeCollector()
        async with anyio.create_task_group() as tg:
            tg.start_soon(poke.service(path, shown=set), col)
            with anyio.fail_after(3):
                while not os.path.exists(path):
                    await anyio.sleep(0.01)

            def raw(payload):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect(path)
                s.sendall(payload)
                data = s.recv(4096)
                s.close()
                return json.loads(data)

            self.assertEqual((await anyio.to_thread.run_sync(raw, b"not json\n"))["ok"], False)
            self.assertEqual((await anyio.to_thread.run_sync(raw, b'{"op":"rm -rf"}\n'))["error"], "unknown op")
            self.assertEqual(await anyio.to_thread.run_sync(poke.notify, state), [set()])
            self.assertEqual(col.pokes, 1)
            tg.cancel_scope.cancel()

    async def test_too_long_path_skips_listener(self):
        col = FakeCollector()
        path = os.path.join("/tmp", "x" * 120, "1.sock")
        await poke.service(path, shown=set)(col)  # returns at once
        self.assertTrue(col.messages and "too long" in col.messages[0])
        self.assertFalse(os.path.exists(os.path.dirname(path)))


class NotifyTest(unittest.TestCase):
    def test_stale_sockets_removed_and_not_counted(self):
        state = short_state()
        os.makedirs(poke.run_dir(state))
        dead = poke.socket_path(state, pid=999999)
        open(dead, "w").close()  # dead pid
        mine = poke.socket_path(state, pid=os.getpid())
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(mine)
        s.close()  # bound then closed: connection refused
        self.assertEqual(poke.notify(state), [])
        self.assertFalse(os.path.exists(dead))
        self.assertFalse(os.path.exists(mine))

    def test_no_run_dir(self):
        self.assertEqual(poke.notify(short_state()), [])
