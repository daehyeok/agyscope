"""bin/agy-subagent: text modes run in-process; the panel runs through `uv run`."""

import contextlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

import helpers
import make_fixtures as fx

PATH = os.path.join(helpers.REPO, "bin", "agy-subagent")


def load_launcher():
    loader = SourceFileLoader("agy_subagent_bin", PATH)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_launcher()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        os.chmod(self.tmp, stat.S_IRWXU)
        for root, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                os.chmod(os.path.join(root, d), stat.S_IRWXU)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, argv, which="/usr/bin/uv", rc=0):
        out = io.StringIO()
        with mock.patch.object(self.mod.shutil, "which", return_value=which), \
                mock.patch.object(self.mod.subprocess, "call", return_value=rc) as call, \
                mock.patch("builtins.input", side_effect=EOFError) as inp, \
                mock.patch("agy_subagent_py.hook.focus_block") as focus, \
                contextlib.redirect_stdout(out):
            code = self.mod.main(argv)
        return code, out.getvalue(), call, inp, focus

    def test_uv_argv(self):
        self.assertEqual(self.mod.uv_argv("/plug", ["--block", "b"]),
                         ["uv", "run", "--project", "/plug", "--quiet",
                          "python", "-m", "agy_subagent_py.app", "--block", "b"])
        # No lockfile ships, so no lock-enforcing flag: uv resolves from the host's own index.

    def test_uv_env_readonly_fallback(self):
        ro = os.path.join(self.tmp, "ro")
        os.mkdir(ro)
        os.chmod(ro, 0o555)
        environ = {"XDG_CACHE_HOME": "/cache", "PATH": "/bin"}
        env = self.mod.uv_env(ro, environ)
        self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], os.path.join("/cache", "rex-agy-subagent", "venv"))
        self.assertEqual(env["PATH"], "/bin")
        self.assertNotIn("UV_PROJECT_ENVIRONMENT", environ)

        env = self.mod.uv_env(ro, {"HOME": "/home/u"})
        self.assertEqual(env["UV_PROJECT_ENVIRONMENT"],
                         os.path.join("/home/u", ".cache", "rex-agy-subagent", "venv"))

        env = self.mod.uv_env(ro, dict(environ, UV_PROJECT_ENVIRONMENT="/mine"))
        self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], "/mine")

        env = self.mod.uv_env(self.tmp, environ)
        self.assertNotIn("UV_PROJECT_ENVIRONMENT", env)

    def test_uv_env_puts_plugin_on_pythonpath(self):
        env = self.mod.uv_env(self.tmp, {"PATH": "/bin"})
        self.assertEqual(env["PYTHONPATH"], self.tmp)

        env = self.mod.uv_env(self.tmp, {"PYTHONPATH": "/a" + os.pathsep + "/b"})
        self.assertEqual(env["PYTHONPATH"], os.pathsep.join([self.tmp, "/a", "/b"]))

    @unittest.skipUnless(shutil.which("uv"), "uv not on PATH")
    def test_real_uv_from_another_cwd(self):
        plugin_dir = os.path.dirname(os.path.dirname(os.path.realpath(PATH)))
        proc = subprocess.run(self.mod.uv_argv(plugin_dir, ["--help"]), cwd=self.tmp,
                              env=self.mod.uv_env(plugin_dir, os.environ),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage", proc.stdout.lower())

    def test_no_uv_message(self):
        code, out, call, inp, focus = self.run_main(["--block", "b"], which=None)
        self.assertEqual(code, 1)
        self.assertIn("agy-subagent: the panel needs uv (https://docs.astral.sh/uv/); "
                      "--once/--view work without it", out)
        inp.assert_called_once()
        call.assert_not_called()
        focus.assert_not_called()

    def test_uv_failure_message(self):
        for rc in (2, -9):
            code, out, call, inp, focus = self.run_main(["--block", "b"], rc=rc)
            self.assertEqual(code, rc)
            self.assertIn("agy-subagent: uv failed (exit %d); first run needs network to fetch textual, "
                          "and python3 >= 3.9" % rc, out)
            inp.assert_called_once()
            focus.assert_not_called()

    def test_panel_runs_app_via_uv(self):
        plugin_dir = os.path.dirname(os.path.dirname(os.path.realpath(PATH)))
        for rc in (0, 1):
            code, out, call, inp, focus = self.run_main(["--block", "b"], rc=rc)
            self.assertEqual(code, rc)
            self.assertEqual(out, "")
            call.assert_called_once()
            self.assertEqual(call.call_args[0][0], self.mod.uv_argv(plugin_dir, ["--block", "b"]))
            self.assertIn("env", call.call_args[1])
            inp.assert_not_called()
            focus.assert_not_called()

    def test_once_without_uv(self):
        fx.build_tree(self.tmp)
        env = {"XDG_CONFIG_HOME": os.path.join(self.tmp, "cfg"), "XDG_STATE_HOME": os.path.join(self.tmp, "state")}
        with mock.patch.dict(os.environ, env):
            code, out, call, inp, _ = self.run_main(
                ["--once", "--conversation", fx.ROOT, "--app-dir", self.tmp], which=None)
        self.assertEqual(code, 0)
        self.assertIn("Planner", out)
        self.assertIn("Reviewer", out)
        call.assert_not_called()
        inp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
