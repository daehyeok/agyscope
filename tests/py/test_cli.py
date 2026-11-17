import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401
import make_fixtures as fx
from agyscope import cli, discovery, resolve, sources, textview  # noqa: F401


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {
            "XDG_CONFIG_HOME": os.path.join(self.tmp, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp, "state"),
        }
        fx.build_tree(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_flags(self):
        args = cli.parse(["--pid", "12", "--rex-block", "b", "--keymap", "vim"])
        self.assertEqual(args.pid, 12)
        self.assertEqual(args.rex_block, "b")
        self.assertEqual(args.keymap, "vim")

    def test_theme_and_marquee_flags(self):
        flags, warnings = cli.flags_of(cli.parse(["--theme", "nord", "--no-marquee"]))
        self.assertEqual(warnings, [])
        self.assertEqual(flags["theme"], "nord")
        self.assertIs(flags["marquee"], False)

        flags_on, _ = cli.flags_of(cli.parse(["--marquee"]))
        self.assertIs(flags_on["marquee"], True)

        flags_default, _ = cli.flags_of(cli.parse([]))
        self.assertIsNone(flags_default["theme"])
        self.assertIsNone(flags_default["marquee"])

    def test_live_and_main_rows_flags(self):
        flags, _ = cli.flags_of(cli.parse(["--no-live", "--main-rows"]))
        self.assertIs(flags["live"], False)
        self.assertIs(flags["main_rows"], True)
        flags, _ = cli.flags_of(cli.parse([]))
        self.assertIsNone(flags["live"])
        self.assertIsNone(flags["main_rows"])

    def test_block_flag_removed(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parse(["--block", "b"])

    def test_bad_json_flag_warns(self):
        args = cli.parse(["--keys-json", "{"])
        _, warnings = cli.flags_of(args)
        self.assertTrue(any("keys-json" in w for w in warnings))

    def test_setup_builds_collector_and_forest(self):
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--conversation", "x2"], env=self.env)
        self.assertIsInstance(s, cli.Setup)
        self.assertEqual(s.args.conversation, [fx.ROOT, "x2"])
        self.assertEqual(s.settings["app_dir"], self.tmp)
        self.assertEqual(s.warnings, [])
        self.assertIsInstance(s.collector, sources.Collector)
        self.assertIsInstance(s.forest, discovery.Forest)
        self.assertEqual(s.forest.app_dir, self.tmp)
        self.assertEqual(s.collector.links.root, os.path.join(self.tmp, "state", "agyscope"))
        self.assertIs(s.forest.links, s.collector.links)
        self.assertEqual([(r.conv, r.source) for r in s.collector.initial()], [(fx.ROOT, "pane"), ("x2", "pinned")])

    def test_setup_rex_block_uses_pointer_fallback(self):
        from agyscope.integrations.rex import pointer
        pointer.write_pointer("b", "conv-p", self.env)
        s = cli.setup(["--app-dir", self.tmp, "--rex-block", "b"], self.env)
        self.assertEqual([r.conv for r in s.collector.initial()], ["conv-p"])

    def test_setup_rex_block_sets_on_close(self):
        s = cli.setup(["--app-dir", self.tmp, "--rex-block", "b"], self.env)
        self.assertTrue(callable(s.on_close))
        s = cli.setup(["--app-dir", self.tmp, "--conversation", "x"], self.env)
        self.assertIsNone(s.on_close)

    def test_core_does_not_import_integrations(self):
        try:
            import textual  # noqa: F401
            mods = "agyscope.cli, agyscope.app"
        except ImportError:
            mods = "agyscope.cli"
        code = ("import sys, %s\n"
                "from agyscope import cli\n"
                "cli.run_text(cli.setup(['--once', '--app-dir', %r, '--conversation', 'x'], {'XDG_CONFIG_HOME': %r}))\n"
                "print([m for m in sys.modules if m.startswith('agyscope.integrations')])\n"
                % (mods, self.tmp, self.tmp))
        proc = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.rstrip().endswith("[]"), proc.stdout)

    def test_run_text_once(self):
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--once"], env=self.env)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.run_text(s)
        self.assertEqual(code, 0)
        output = out.getvalue()
        self.assertIn("Working", output)
        self.assertIn("\n  main ", output)  # the main row
        self.assertIn("    Planner", output)
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--once", "--no-main-rows"], env=self.env)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run_text(s)
        self.assertIn("  Planner", out.getvalue())
        self.assertIn("Completed", out.getvalue())
        self.assertNotIn(" main ", out.getvalue())

    def test_run_text_view_prefix(self):
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--view", "chk"], env=self.env)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.run_text(s)
        self.assertEqual(code, 0)
        tree = discovery.Tree(self.tmp, fx.ROOT)
        row = next(r for r in discovery.flat(tree.rows()) if r["id"].startswith("chk"))
        expected = "\n".join(textview.plain(tree, row)) + "\n"
        self.assertEqual(out.getvalue(), expected)

        s_bad = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--view", "bad_prefix"],
                          env=self.env)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code_bad = cli.run_text(s_bad)
        self.assertEqual(code_bad, 1)
        err_output = err.getvalue()
        self.assertIn("no conversation or subagent bad_prefix", err_output)

    def test_run_text_warnings_to_stderr(self):
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--once",
                       "--keys-json", "{" ], env=self.env)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.run_text(s)
        self.assertEqual(code, 0)
        self.assertIn("flag --keys-json must be JSON; ignored", err.getvalue())

    def test_run_text_no_subagents(self):
        empty_app = tempfile.mkdtemp()
        try:
            s = cli.setup(["--app-dir", empty_app, "--conversation", "empty-root", "--once"],
                          env=self.env)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.run_text(s)
            self.assertEqual(code, 0)
            self.assertIn("No subagents yet.", out.getvalue())
        finally:
            shutil.rmtree(empty_app, ignore_errors=True)

    def test_run_text_no_conversation(self):
        empty_dir = tempfile.mkdtemp()
        try:
            s = cli.setup(["--app-dir", empty_dir], env=self.env)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.run_text(s)
            self.assertEqual(code, 1)
            self.assertIn("No conversation found for this pane.", out.getvalue())
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"XDG_CONFIG_HOME": os.path.join(self.tmp, "cfg"),
                    "XDG_STATE_HOME": os.path.join(self.tmp, "state"),
                    "AGYSCOPE_CONFIG": ""}  # don't inherit the developer's config file
        fx.build_tree(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_once_returns_0(self):
        # Shadow textual and rich with packages that fail to import: --once must not need them.
        shadow = os.path.join(self.tmp, "shadow")
        for name in ("rich", "textual"):
            os.makedirs(os.path.join(shadow, name))
            with open(os.path.join(shadow, name, "__init__.py"), "w") as f:
                f.write("raise ImportError('no textual')\n")
        env = dict(os.environ, PYTHONPATH=shadow, **self.env)
        proc = subprocess.run([sys.executable, "-m", "agyscope", "--once", "--app-dir", self.tmp,
                               "--conversation", fx.ROOT], env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Planner", proc.stdout)
        self.assertIn("Reviewer", proc.stdout)

    def crash(self, isatty):
        err = io.StringIO()
        with mock.patch.object(cli, "setup", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys.stdin, "isatty", return_value=isatty), \
                mock.patch("builtins.input", side_effect=EOFError) as inp, \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--once"])
        return code, inp, err.getvalue()

    def test_main_crash_returns_2_waits_on_tty(self):
        code, inp, err = self.crash(True)
        self.assertEqual(code, 2)
        inp.assert_called_once()
        self.assertIn("Traceback", err)
        self.assertIn("RuntimeError: boom", err)
        code, inp, _ = self.crash(False)
        self.assertEqual(code, 2)
        inp.assert_not_called()

    def test_main_app_return_2_waits_on_tty(self):
        fake_app = mock.Mock(run=mock.Mock(return_value=2))
        with mock.patch.dict(sys.modules, {"agyscope.app": fake_app}), \
                mock.patch("agyscope.app", fake_app, create=True), \
                mock.patch.object(cli, "setup", return_value=mock.sentinel.setup), \
                mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", side_effect=EOFError) as inp, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([]), 2)
        fake_app.run.assert_called_once_with(mock.sentinel.setup)
        inp.assert_called_once()

    @unittest.skipUnless(os.path.exists(os.path.join(os.path.dirname(sys.executable), "agyscope")),
                         "agyscope entry point not installed")
    def test_entry_point_help(self):
        exe = os.path.join(os.path.dirname(sys.executable), "agyscope")
        proc = subprocess.run([exe, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: agyscope", proc.stdout)

    def test_module_help(self):
        proc = subprocess.run([sys.executable, "-m", "agyscope", "--help"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: agyscope", proc.stdout)


if __name__ == "__main__":
    unittest.main()
