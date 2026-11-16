import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import helpers

HOOK = [sys.executable, "-m", "agyscope.integrations.rex.hook"]

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
        p = subprocess.run(HOOK, input=data.encode(), stdout=subprocess.PIPE,
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
        p = os.path.join(self.tmp, "state", "agyscope", "blocks", "block:src")
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
        self.assertEqual(blk["label"], "agyscope:block:src")
        # argv0 is hook.py, which has no agyscope sibling, so the bare command stays bare
        self.assertEqual(blk["options"]["command"], ["agyscope", "--conversation", "root-1", "--rex-block", "block:src"])
        self.assertIs(blk["options"]["interactive"], True)
        self.assertEqual(self.pointer(), "root-1")
        # a later start finds the panel and opens nothing
        self.run_hook(payload())
        self.assertEqual(len(self.calls()), 1)

    def write_config(self, data):
        d = os.path.join(self.tmp, "config", "agyscope")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump(data, f)

    def test_command_from_config(self):
        self.write_config({"command": ["/opt/a/agyscope", "--keymap", "vim"]})
        self.run_hook(payload())
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["layout"]["block"]["options"]["command"],
                         ["/opt/a/agyscope", "--keymap", "vim", "--conversation", "root-1",
                          "--rex-block", "block:src"])

    def test_interactive_false_from_config(self):
        self.write_config({"interactive": False})
        self.run_hook(payload())
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["layout"]["block"]["options"]["interactive"], False)

    def test_parallel_starts_open_one(self):
        procs = [subprocess.Popen(HOOK, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
        self.write_config({"auto_open": False})
        self.run_hook(payload())
        self.assertEqual(self.calls(0.5), [])
        self.assertEqual(self.pointer(), "root-1")

    def test_missing_rex(self):
        env = dict(self.env, REX_BIN_DIR=os.path.join(self.tmp, "nowhere"), PATH="/nonexistent")
        self.run_hook(payload(), env)
        self.assertEqual(self.pointer(), "root-1")

    def test_config_failure_still_answers(self):
        from agyscope.integrations.rex import hook
        out = io.StringIO()
        with mock.patch("agyscope.config.load", side_effect=RuntimeError("boom")), \
                mock.patch.object(hook, "detach") as detach:
            rc = hook.main(io.StringIO(payload()), out, dict(self.env))
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "{}\n")
        detach.assert_not_called()

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
        with open(os.path.join(self.tmp, "state", "agyscope", "blocks", "block:src")) as f:
            return f.read().splitlines()

    def test_root_without_root_id(self):
        app, data = self.public_payload("root-9", locked=True)
        self.run_hook(data)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["layout"]["block"]["options"]["command"][1:],
                         ["--conversation", "root-9", "--rex-block", "block:src", "--app-dir", app])
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
        from agyscope.integrations.rex import hook
        self.assertEqual(hook.app_dir_of({"artifactDirectoryPath": "/d/x/brain/c1"}), "/d/x")
        self.assertEqual(hook.app_dir_of({"transcriptPath": "file:///d/y/brain/c/.system_generated/logs/t.jsonl"}),
                         "/d/y")
        self.assertIsNone(hook.app_dir_of({"transcriptPath": "/no/such/layout.jsonl"}))
        self.assertIsNone(hook.app_dir_of({}))

    def test_rex_cli_macos_bundle_fallback(self):
        from agyscope.integrations.rex import focus
        env = {"HOME": self.tmp, "PATH": "/nonexistent"}
        self.assertEqual(focus.rex_cli(env), "rex")
        bundle_bin = os.path.join(self.tmp, "Library", "Application Support",
                                  "com.superlogical.rex", "RexServer", "Versions",
                                  "Current", "Rex Server.app", "Contents", "MacOS", "rex")
        os.makedirs(os.path.dirname(bundle_bin), exist_ok=True)
        with open(bundle_bin, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(bundle_bin, os.stat(bundle_bin).st_mode | stat.S_IEXEC)
        self.assertEqual(focus.rex_cli(env), bundle_bin)

    def test_focus_block_guards(self):
        from agyscope.integrations.rex import focus
        self.assertTrue(focus.focus_block("block:src", self.env))
        self.assertEqual(self.focus_calls(), ["block:src"])

        self.assertFalse(focus.focus_block(None, self.env))
        self.assertEqual(self.focus_calls(), ["block:src"])
        self.assertFalse(focus.focus_block("", self.env))
        self.assertEqual(self.focus_calls(), ["block:src"])

        env_no_session = dict(self.env)
        del env_no_session["REX_SESSION"]
        self.assertFalse(focus.focus_block("block:src", env_no_session))
        self.assertEqual(self.focus_calls(), ["block:src"])

        env_missing_rex = dict(self.env, REX_BIN_DIR=os.path.join(self.tmp, "nowhere"), PATH="/nonexistent")
        self.assertFalse(focus.focus_block("block:src", env_missing_rex))
        self.assertEqual(self.focus_calls(), ["block:src"])

    # The panel's close/focus and header tests live with the Textual app now:
    # test_app (RunTest: on_close only after `close`) and test_widgets (test_header_model_badge).


class HookConfigTest(unittest.TestCase):
    def test_settings_keys_are_integration_keys(self):
        from agyscope import config
        from agyscope.integrations.rex import hook
        spec = hook.settings_spec()
        self.assertEqual(set(spec), {"bounds", "auto_open", "interactive", "command"})
        self.assertLessEqual(set(spec), config.INTEGRATION_KEYS)
        self.assertEqual(spec["command"][0], ["agyscope"])

    def test_check_bounds(self):
        from agyscope.integrations.rex import hook
        self.assertEqual(hook._check_bounds([0, 0, 1, 1]), [0.0, 0.0, 1.0, 1.0])
        for bad in ([0, 0, 1], [True, 0, 0, 0], [0, 0, 2, 0], "x", None):
            with self.assertRaises(ValueError):
                hook._check_bounds(bad)

    def test_panel_command_resolves_sibling(self):
        from agyscope.integrations.rex import hook
        tmp = tempfile.mkdtemp()
        exe = os.path.join(tmp, "agyscope")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(exe, 0o755)
        real_hook = os.path.join(tmp, "agyscope-rex-hook")
        open(real_hook, "w").close()
        os.makedirs(os.path.join(tmp, "link"))
        link = os.path.join(tmp, "link", "agyscope-rex-hook")
        os.symlink(real_hook, link)
        self.assertEqual(hook.panel_command(["agyscope", "--x"], link), [os.path.realpath(exe), "--x"])
        # non-bare commands and a missing sibling stay unchanged
        self.assertEqual(hook.panel_command(["/opt/agyscope"], link), ["/opt/agyscope"])
        self.assertEqual(hook.panel_command(["other"], link), ["other"])
        os.chmod(exe, 0o644)  # not executable
        self.assertEqual(hook.panel_command(["agyscope"], link), ["agyscope"])
        cmd = ["agyscope"]
        self.assertIsNot(hook.panel_command(cmd, link), cmd)

    def test_hook_module_imports_stdlib_only(self):
        code = "import sys, agyscope.integrations.rex.hook; print('agyscope.config' in sys.modules)"
        proc = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False")

    @unittest.skipUnless(os.path.exists(os.path.join(os.path.dirname(sys.executable), "agyscope-rex-hook")),
                         "agyscope-rex-hook entry point not installed")
    def test_entry_point_empty_stdin(self):
        exe = os.path.join(os.path.dirname(sys.executable), "agyscope-rex-hook")
        proc = subprocess.run([exe], input=b"", stdout=subprocess.PIPE, timeout=10)
        self.assertEqual((proc.returncode, proc.stdout.decode().strip()), (0, "{}"))

    def test_sample_hooks_json(self):
        with open(os.path.join(helpers.REPO, "integrations", "rex", "hooks.json")) as f:
            data = json.load(f)
        self.assertEqual(list(data), ["agyscope"])
        command = data["agyscope"]["PostToolUse"][0]["hooks"][0]["command"]
        self.assertTrue(command.endswith("agyscope-rex-hook"), command)


if __name__ == "__main__":
    unittest.main()
