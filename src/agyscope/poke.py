"""Poke: tells running panels to re-read the link store now.

Every panel listens on <state>/run/<pid>.sock (mode 0600). The link files stay the
source of truth; the socket only says "re-read now", so a lost poke loses nothing and
panels still pick changes up on their next collector cycle.

Protocol: one JSON line each way.
    -> {"op": "refresh"}  re-collect now        -> {"ok": true, "shows": [<conversation ids>]}
    -> {"op": "ping"}     just answer           -> same reply
"shows" is every conversation id the panel currently lists, so `agyscope attach` can say
how many panels show the parent.

service() is the panel side (AnyIO, started inside the collector's task group); notify()
is the synchronous client used by the attach/detach commands.
"""

import json
import os
import socket

import anyio

from . import resolve

PATH_MAX = 100        # bytes; macOS allows 104 for an AF_UNIX path
MAX_LINE = 4096
READ_TIMEOUT = 1.0
CLIENT_TIMEOUT = 0.2


def run_dir(state_dir):
    return os.path.join(state_dir, "run")


def socket_path(state_dir, pid=None):
    return os.path.join(run_dir(state_dir), "%d.sock" % (os.getpid() if pid is None else pid))


def _reply(ok, **extra):
    return (json.dumps(dict(ok=ok, **extra)) + "\n").encode()


def service(path, shown, on_error=None):
    """An async callable(collector) that serves pokes on path until cancelled, then removes it.

    shown() returns the conversation ids the panel lists; on_error(message) reports why the
    listener could not start (the panel keeps working without it).
    """

    async def handle(collector, stream):
        try:
            async with stream:
                data = b""
                with anyio.move_on_after(READ_TIMEOUT):
                    while b"\n" not in data and len(data) <= MAX_LINE:
                        try:
                            data += await stream.receive(1024)
                        except (anyio.EndOfStream, anyio.BrokenResourceError):
                            break
                line = data.split(b"\n", 1)[0]
                if len(line) > MAX_LINE:
                    await stream.send(_reply(False, error="request too long"))
                    return
                try:
                    op = json.loads(line or b"{}").get("op")
                except (ValueError, AttributeError):
                    await stream.send(_reply(False, error="bad request"))
                    return
                if op == "refresh":
                    collector.poke()
                elif op != "ping":
                    await stream.send(_reply(False, error="unknown op"))
                    return
                await stream.send(_reply(True, shows=sorted(shown())))
        except Exception:  # one bad client must not stop the listener
            pass

    async def run(collector):
        report = on_error or getattr(collector, "report", None)

        def warn(message):
            if report is not None:
                report(message)

        if len(os.fsencode(path)) > PATH_MAX:
            warn("poke: socket path too long (%s); attach/detach apply within seconds" % path)
            return
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            listener = await anyio.create_unix_listener(path, mode=0o600)
        except OSError as e:
            warn("poke: can't listen on %s: %s" % (path, e))
            return
        try:
            async with listener:
                await listener.serve(lambda stream: handle(collector, stream))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    return run


def _pid_of(name):
    try:
        return int(name[:-len(".sock")])
    except ValueError:
        return None


def notify(state_dir, op="refresh", timeout=CLIENT_TIMEOUT):
    """[set of shown ids] from every panel reached; stale sockets are removed. Never raises."""
    d = run_dir(state_dir)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".sock"):
            continue
        path = os.path.join(d, name)
        pid = _pid_of(name)
        if pid is not None and not resolve.pid_alive(pid):
            _unlink(path)
            continue
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(path)
            s.sendall((json.dumps({"op": op}) + "\n").encode())
            data = b""
            while b"\n" not in data and len(data) < (1 << 20):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (ConnectionRefusedError, FileNotFoundError):
            _unlink(path)
            continue
        except OSError:  # busy or timed out: leave it alone
            continue
        finally:
            s.close()
        try:
            reply = json.loads(data.split(b"\n", 1)[0])
        except ValueError:
            continue
        if isinstance(reply, dict) and reply.get("ok"):
            out.append(set(reply.get("shows") or ()))
    return out


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass
