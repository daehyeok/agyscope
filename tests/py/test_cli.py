import contextlib
import io
import os
import shutil
import tempfile
import unittest

import helpers  # noqa: F401
import make_fixtures as fx
from agy_subagent_py import cli, discovery, resolve, textview


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
        args = cli.parse(["--pid", "12", "--block", "b", "--keymap", "vim"])
        self.assertEqual(args.pid, 12)
        self.assertEqual(args.block, "b")
        self.assertEqual(args.keymap, "vim")

    def test_bad_json_flag_warns(self):
        args = cli.parse(["--keys-json", "{"])
        _, warnings = cli.flags_of(args)
        self.assertTrue(any("keys-json" in w for w in warnings))

    def test_setup_builds_resolver_and_tree(self):
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT], env=self.env)
        self.assertIsInstance(s, cli.Setup)
        self.assertEqual(s.args.conversation, fx.ROOT)
        self.assertEqual(s.settings["app_dir"], self.tmp)
        self.assertEqual(s.warnings, [])
        self.assertIsInstance(s.resolver, resolve.Resolver)
        self.assertIsInstance(s.tree, discovery.Tree)
        self.assertEqual(s.tree.app_dir, self.tmp)

    def test_run_text_once(self):
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--once"], env=self.env)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.run_text(s)
        self.assertEqual(code, 0)
        output = out.getvalue()
        self.assertIn("Working", output)
        self.assertIn("  Planner", output)
        self.assertIn("Completed", output)

    def test_run_text_view_prefix(self):
        s = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--view", "chk"], env=self.env)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.run_text(s)
        self.assertEqual(code, 0)
        s.tree.set_root(fx.ROOT, self.tmp)
        row = next(r for r in discovery.flat(s.tree.rows()) if r["id"].startswith("chk"))
        expected = "\n".join(textview.plain(s.tree, row)) + "\n"
        self.assertEqual(out.getvalue(), expected)

        s_bad = cli.setup(["--app-dir", self.tmp, "--conversation", fx.ROOT, "--view", "bad_prefix"],
                          env=self.env)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code_bad = cli.run_text(s_bad)
        self.assertEqual(code_bad, 1)
        err_output = err.getvalue()
        self.assertIn("no subagent bad_prefix under root-0000", err_output)

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


if __name__ == "__main__":
    unittest.main()
