import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import helpers

HOOK = os.path.join(helpers.REPO, "bin", "agy-subagent-hook")

# Fake rex: `session inspect S --json` prints a view of one window holding block:src
# plus any layers created so far; `api call session.new_layer -` records the request
# and adds its block (with its label) to the view.
FAKE_REX = r'''#!%s
import json, os, sys, time
state = os.environ["FAKE_REX_STATE"]
def load():
    try:
        s = json.load(open(state))
    except Exception:
        s = {}
    s.setdefault("calls", [])
    s.setdefault("labels", [])
    s.setdefault("focus", [])
    return s
def save(s):
    tmp = state + ".%%d" %% os.getpid()
    json.dump(s, open(tmp, "w")); os.replace(tmp, state)
a = sys.argv[1:]
if a[:2] == ["session", "inspect"]:
    s = load()
    blocks = [{"block_id": "block:src", "label": "shell"}]
    layers = [{"layer_id": "l0", "kind": "tiled", "blocks": blocks}]
    for i, l in enumerate(s["labels"]):
        layers.append({"layer_id": "f%%d" %% i, "kind": "floating", "blocks": [{"block_id": "p%%d" %% i, "label": l}]})
    print(json.dumps({"windows": [{"window_id": "w1", "layers": layers}]}))
elif a[:3] == ["api", "call", "session.new_layer"]:
    req = json.load(sys.stdin)
    time.sleep(0.2)
    s = load(); s["calls"].append(req); s["labels"].append(req["layout"]["block"]["label"]); save(s)
    print("{}")
elif len(a) >= 2 and a[0] == "focus":
    s = load(); s["focus"].append(a[1]); save(s)
    sys.exit(0)
else:
    sys.exit(2)
''' % sys.executable


def payload(error=None, name="invoke_subagent"):
    return json.dumps({"toolCall": {"name": name, "args": {"Role": "R"}}, "conversationId": "child-parent",
                       "rootConversationId": "root-1", "error": error,
                       "result": '{"conversationId": "c1"}'})


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        self.rex = os.path.join(bindir, "rex")
        with open(self.rex, "w") as f:
            f.write(FAKE_REX)
        os.chmod(self.rex, os.stat(self.rex).st_mode | stat.S_IEXEC)
        self.state = os.path.join(self.tmp, "rex-state.json")
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": self.tmp, "REX_BIN_DIR": bindir,
            "REX_SESSION": "s1", "REX_BLOCK": "block:src", "FAKE_REX_STATE": self.state,
            "XDG_STATE_HOME": os.path.join(self.tmp, "state"),
            "XDG_CONFIG_HOME": os.path.join(self.tmp, "config"),
        }

    def run_hook(self, data, env=None):
        t0 = time.time()
        p = subprocess.run([sys.executable, HOOK], input=data.encode(), stdout=subprocess.PIPE,
                           env=env or self.env, timeout=5)
        took = time.time() - t0
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.decode().strip(), "{}")
        self.assertLess(took, 1.0)
        return p

    def calls(self, wait=2.0):
        deadline = time.time() + wait
        last = []
        while time.time() < deadline:
            try:
                with open(self.state) as f:
                    last = json.load(f)["calls"]
            except Exception:
                last = []
            time.sleep(0.1)
        return last

    def pointer(self):
        p = os.path.join(self.tmp, "state", "rex-agy-subagent", "blocks", "block:src")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return f.read().strip()

    def focus_calls(self):
        try:
            with open(self.state) as f:
                return json.load(f).get("focus", [])
        except Exception:
            return []

    def test_opens_once_with_conversation(self):
        self.run_hook(payload())
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertEqual(req["window_id"], "w1")
        self.assertFalse(req["focus"])
        blk = req["layout"]["block"]
        self.assertEqual(blk["label"], "agy-subagent:block:src")
        self.assertEqual(blk["options"]["command"][1:], ["--conversation", "root-1", "--block", "block:src"])
        self.assertTrue(blk["options"]["command"][0].endswith("/bin/agy-subagent"))
        self.assertEqual(self.pointer(), "root-1")
        # a later start finds the panel and opens nothing
        self.run_hook(payload())
        self.assertEqual(len(self.calls()), 1)

    def test_parallel_starts_open_one(self):
        procs = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  env=self.env) for _ in range(3)]
        for p in procs:
            out, _ = p.communicate(payload().encode(), timeout=5)
            self.assertEqual(out.decode().strip(), "{}")
            self.assertEqual(p.returncode, 0)
        self.assertEqual(len(self.calls(3.0)), 1)

    def test_not_in_rex(self):
        env = dict(self.env)
        del env["REX_BLOCK"]
        self.run_hook(payload(), env)
        self.assertEqual(self.calls(0.5), [])
        self.assertIsNone(self.pointer())

    def test_error_payload(self):
        self.run_hook(payload(error="quota"))
        self.assertEqual(self.calls(0.5), [])

    def test_other_tool_and_garbage(self):
        self.run_hook(payload(name="read_file"))
        self.run_hook("not json")
        self.run_hook("")
        self.assertEqual(self.calls(0.5), [])

    def test_auto_open_false(self):
        env = dict(self.env, AGY_SUBAGENT_AUTO_OPEN="false")
        self.run_hook(payload(), env)
        self.assertEqual(self.calls(0.5), [])
        self.assertEqual(self.pointer(), "root-1")

    def test_missing_rex(self):
        env = dict(self.env, REX_BIN_DIR=os.path.join(self.tmp, "nowhere"), PATH="/nonexistent")
        self.run_hook(payload(), env)
        self.assertEqual(self.pointer(), "root-1")

    def test_broken_install_still_answers(self):
        d = tempfile.mkdtemp()
        lonely = os.path.join(d, "agy-subagent-hook")
        with open(HOOK) as src, open(lonely, "w") as dst:
            dst.write(src.read())
        p = subprocess.run([sys.executable, lonely], input=payload().encode(), stdout=subprocess.PIPE,
                           env=self.env, timeout=5)
        self.assertEqual((p.returncode, p.stdout.decode().strip()), (0, "{}"))

    # Payloads without rootConversationId: the hook also runs for a subagent's own
    # invoke_subagent, with conversationId set to that subagent. Only roots hold a lock.
    def public_payload(self, conv, locked):
        app = os.path.join(self.tmp, "data", "agent")
        os.makedirs(os.path.join(app, "presence"), exist_ok=True)
        if locked:
            open(os.path.join(app, "presence", conv + ".lock"), "w").close()
        tp = os.path.join(app, "brain", conv, ".system_generated", "logs", "transcript_full.jsonl")
        return app, json.dumps({"toolCall": {"name": "invoke_subagent", "args": {"Role": "R"}},
                                "conversationId": conv, "transcriptPath": tp,
                                "artifactDirectoryPath": os.path.join(app, "brain", conv), "error": ""})

    def raw_pointer(self):
        with open(os.path.join(self.tmp, "state", "rex-agy-subagent", "blocks", "block:src")) as f:
            return f.read().splitlines()

    def test_root_without_root_id(self):
        app, data = self.public_payload("root-9", locked=True)
        self.run_hook(data)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["layout"]["block"]["options"]["command"][1:],
                         ["--conversation", "root-9", "--block", "block:src", "--app-dir", app])
        self.assertEqual(self.raw_pointer(), ["root-9", app])

    def test_nested_start_keeps_root(self):
        app, data = self.public_payload("root-9", locked=True)
        self.run_hook(data)
        self.calls()
        _, nested = self.public_payload("outer-7", locked=False)
        self.run_hook(nested)
        self.assertEqual(self.raw_pointer(), ["root-9", app])

    def test_nested_first_opens_nothing(self):
        _, nested = self.public_payload("outer-7", locked=False)
        self.run_hook(nested)
        self.assertEqual(self.calls(0.5), [])
        self.assertIsNone(self.pointer())

    def test_app_dir_from_artifact_path(self):
        from agy_subagent_py import hook
        self.assertEqual(hook.app_dir_of({"artifactDirectoryPath": "/d/x/brain/c1"}), "/d/x")
        self.assertEqual(hook.app_dir_of({"transcriptPath": "file:///d/y/brain/c/.system_generated/logs/t.jsonl"}),
                         "/d/y")
        self.assertIsNone(hook.app_dir_of({"transcriptPath": "/no/such/layout.jsonl"}))
        self.assertIsNone(hook.app_dir_of({}))

    def test_rex_cli_macos_bundle_fallback(self):
        from agy_subagent_py import hook
        env = {"HOME": self.tmp, "PATH": "/nonexistent"}
        self.assertEqual(hook.rex_cli(env), "rex")
        bundle_bin = os.path.join(self.tmp, "Library", "Application Support",
                                  "com.superlogical.rex", "RexServer", "Versions",
                                  "Current", "Rex Server.app", "Contents", "MacOS", "rex")
        os.makedirs(os.path.dirname(bundle_bin), exist_ok=True)
        with open(bundle_bin, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(bundle_bin, os.stat(bundle_bin).st_mode | stat.S_IEXEC)
        self.assertEqual(hook.rex_cli(env), bundle_bin)

    def test_focus_block_guards(self):
        from agy_subagent_py import hook
        self.assertTrue(hook.focus_block("block:src", self.env))
        self.assertEqual(self.focus_calls(), ["block:src"])

        self.assertFalse(hook.focus_block(None, self.env))
        self.assertEqual(self.focus_calls(), ["block:src"])
        self.assertFalse(hook.focus_block("", self.env))
        self.assertEqual(self.focus_calls(), ["block:src"])

        env_no_session = dict(self.env)
        del env_no_session["REX_SESSION"]
        self.assertFalse(hook.focus_block("block:src", env_no_session))
        self.assertEqual(self.focus_calls(), ["block:src"])

        env_missing_rex = dict(self.env, REX_BIN_DIR=os.path.join(self.tmp, "nowhere"), PATH="/nonexistent")
        self.assertFalse(hook.focus_block("block:src", env_missing_rex))
        self.assertEqual(self.focus_calls(), ["block:src"])

    def test_panel_close_returns_true(self):
        import curses
        from unittest import mock
        from agy_subagent_py import config, discovery, resolve, ui

        settings, _ = config.load({}, self.env)
        resolver = resolve.Resolver(self.tmp, conversation="root-1")
        tree = discovery.Tree(self.tmp)
        tree.set_root("root-1")

        class FakeStdscr:
            def keypad(self, flag):
                pass

            def timeout(self, ms):
                pass

            def getmaxyx(self):
                return (24, 80)

            def erase(self):
                pass

            def refresh(self):
                pass

            def addnstr(self, y, x, text, n, attr=0):
                pass

            def getch(self):
                return ord("q")

        panel = ui.Panel(tree, resolver, settings)
        with mock.patch("curses.curs_set"), mock.patch("curses.has_colors", return_value=False):
            res = panel.run(FakeStdscr())
        self.assertTrue(res)

        with mock.patch("curses.wrapper", side_effect=KeyboardInterrupt):
            self.assertFalse(ui.run(tree, resolver, settings))

    def test_bin_agy_subagent_focus_on_close(self):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        from unittest import mock
        path = os.path.join(helpers.REPO, "bin", "agy-subagent")
        loader = SourceFileLoader("agy_subagent_bin", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        with mock.patch("agy_subagent_py.ui.run", return_value=True), \
             mock.patch("agy_subagent_py.hook.focus_block") as mock_focus:
            mod.main(["--block", "block:src"])
            mock_focus.assert_called_once_with("block:src")

        with mock.patch("agy_subagent_py.ui.run", return_value=False), \
             mock.patch("agy_subagent_py.hook.focus_block") as mock_focus:
            mod.main(["--block", "block:src"])
            mock_focus.assert_not_called()

    def test_draw_view_header_shows_full_model(self):
        from unittest import mock
        from agy_subagent_py import config, discovery, resolve, transcript, ui

        settings, _ = config.load({}, self.env)
        resolver = resolve.Resolver(self.tmp, conversation="root-1")
        tree = discovery.Tree(self.tmp, "root-1")
        panel = ui.Panel(tree, resolver, settings)

        t = transcript.Transcript("/nonexistent", "c1")
        drawn = {}

        class CaptureStdscr:
            def addstr(self, y, x, text, attr=0):
                drawn[y] = text

        with mock.patch("curses.has_colors", return_value=False):
            row_with_model = {
                "id": "c1", "role": "Final Review Fixer", "state": "done",
                "model": "gemini-3.8-flash-tiered", "t": t, "parent": "root-1",
            }
            panel.draw_view(CaptureStdscr(), row_with_model, ui.View("c1"), 10, 120)
            self.assertIn("Final Review Fixer [done · gemini-3.8-flash-tiered]", drawn[0])

            row_no_model = {
                "id": "c1", "role": "Final Review Fixer", "state": "done",
                "model": "-", "t": t, "parent": "root-1",
            }
            panel.draw_view(CaptureStdscr(), row_no_model, ui.View("c1"), 10, 120)
            self.assertIn("Final Review Fixer [done]  ", drawn[0])


if __name__ == "__main__":
    unittest.main()
