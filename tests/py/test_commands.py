import io
import os
import shutil
import sqlite3
import tempfile
import unittest

import helpers  # noqa: F401
import make_fixtures as fx
from agyscope import cli, commands
from agyscope.links import HIDDEN, LINKED, LinkStore

W1 = "b029292c-7ebe-417c-b9a7-522ae4b576d1"
W2 = "b0293333-0000-0000-0000-000000000000"
M = "36d6bc77-679a-49f8-af35-f0bc2ece9a12"


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = os.path.join(self.tmp, "data")
        for conv in (W1, W2, M, "other-0000"):
            fx.write(self.app, conv, [fx.user("hi")])
        self.env = {"XDG_CONFIG_HOME": os.path.join(self.tmp, "cfg"),
                    "XDG_STATE_HOME": os.path.join(self.tmp, "state"),
                    "ANTIGRAVITY_APP_DATA_DIR": self.app}
        self.store = LinkStore(os.path.join(self.tmp, "state", "agyscope"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cmd(self, *argv, **env):
        out, err = io.StringIO(), io.StringIO()
        code = commands.run(list(argv), env=dict(self.env, **env), out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_attach_from_agent_env(self):
        code, out, err = self.run_cmd("attach", "b029292c", ANTIGRAVITY_CONVERSATION_ID=M)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.store.children(M), [W1])
        self.assertIn("attached %s under %s (from ANTIGRAVITY_CONVERSATION_ID)" % (W1, M), out)
        self.assertIn("no running panel", out)

    def test_parent_precedence(self):
        self.run_cmd("attach", W1, AGYSCOPE_MAIN="other-0000", ANTIGRAVITY_CONVERSATION_ID=M)
        self.assertEqual(self.store.children("other-0000"), [W1])
        code, out, _ = self.run_cmd("attach", W1, "--under", "36d6", AGYSCOPE_MAIN="other-0000")
        self.assertEqual(self.store.children(M), [W1])
        self.assertIn("(from --under)", out)

    def test_top_forces_top_level(self):
        code, out, _ = self.run_cmd("attach", "other", "--top", ANTIGRAVITY_CONVERSATION_ID=M)
        self.assertEqual(code, 0)
        self.assertEqual(self.store.top(), [("other-0000", self.app)])
        self.assertIn("at the top level (--top)", out)

    def test_plain_shell_is_top_level_with_config_app_dir(self):
        env = dict(self.env)
        del env["ANTIGRAVITY_APP_DATA_DIR"]
        out, err = io.StringIO(), io.StringIO()
        code = commands.run(["attach", "other", "--app-dir", self.app], env=env, out=out, err=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(self.store.top(), [("other-0000", self.app)])
        self.assertIn("at the top level;", out.getvalue())

    def test_bad_ambiguous_missing_ids(self):
        self.assertEqual(self.run_cmd("attach", "../x", "--under", M)[0], 2)
        code, _, err = self.run_cmd("attach", "b029", "--under", M)
        self.assertEqual(code, 2)
        self.assertIn("matches 2 conversations", err)
        code, _, err = self.run_cmd("attach", "ffff", "--under", M)
        self.assertEqual(code, 2)
        self.assertIn("no conversation", err)
        self.assertEqual(self.run_cmd("attach", W1, "--under", M, "--top")[0], 2)
        self.assertEqual(self.run_cmd("attach")[0], 2)
        self.assertEqual(self.store.entries(), [])

    def test_cycle_rejected(self):
        self.run_cmd("attach", W1, "--under", M)
        code, _, err = self.run_cmd("attach", M, "--under", W1)
        self.assertEqual(code, 2)
        self.assertIn("cycle", err)
        self.assertEqual(self.run_cmd("attach", M, "--under", M)[0], 2)

    def test_cycle_through_agent_parent_records(self):
        con = sqlite3.connect(os.path.join(self.app, "conversation_summaries.db"))
        con.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, agent_name text, "
                    "last_modified_time datetime, parent_conversation_id text, raw_summary blob)")
        con.execute("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,NULL)", (W1, "", "self", "", M))
        con.commit()
        con.close()
        self.assertEqual(self.run_cmd("attach", M, "--under", W1)[0], 2)

    def test_repeated_attach_and_detach(self):
        self.assertEqual(self.run_cmd("attach", W1, "--under", M)[0], 0)
        self.assertEqual(self.run_cmd("attach", W1, "--under", M)[0], 0)
        self.assertEqual(self.store.children(M), [W1])
        code, out, _ = self.run_cmd("detach", W1, "--under", M)
        self.assertEqual(code, 0)
        self.assertIn("detached %s under %s" % (W1, M), out)
        self.assertEqual(self.store.children(M), [])
        self.assertEqual(self.store.hidden(M), {W1})

    def test_detach_top_level_and_unknown_full_id(self):
        self.assertEqual(self.run_cmd("detach", "gone-1234", "--top")[0], 0)  # no transcript needed
        self.assertEqual(self.store.hidden(None), {"gone-1234"})

    def test_links_listing(self):
        self.run_cmd("attach", W1, "--under", M)
        self.run_cmd("attach", "other", "--top")
        self.run_cmd("detach", W2, "--under", M)
        code, out, _ = self.run_cmd("links")
        self.assertEqual(code, 0)
        lines = sorted(out.splitlines())
        self.assertEqual(lines, sorted(["%s %s %s" % (M, W1, LINKED), "- other-0000 %s" % LINKED,
                                        "%s %s %s" % (M, W2, HIDDEN)]))
        _, out, _ = self.run_cmd("links", "36d6")
        self.assertEqual(sorted(out.splitlines()), sorted(["%s %s %s" % (M, W1, LINKED), "%s %s %s" % (M, W2, HIDDEN)]))

    def test_cli_main_dispatches(self):
        old = dict(os.environ)
        try:
            os.environ.update(self.env)
            os.environ["ANTIGRAVITY_CONVERSATION_ID"] = M
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["attach", W1]), 0)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(self.store.children(M), [W1])
