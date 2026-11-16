import os
import tempfile
import unittest

import helpers  # noqa: F401
import make_fixtures as fx
from agyscope import discovery
from agyscope.transcript import col_width


class DiscoveryTest(unittest.TestCase):
    def groups(self, with_logs):
        app = tempfile.mkdtemp()
        fx.build_tree(app, with_logs=with_logs)
        tree = discovery.Tree(app, fx.ROOT)
        return tree, tree.rows()

    def check(self, groups):
        named = {name: [(r["depth"], r["role"], r["state"]) for r in rows] for name, rows in groups}
        self.assertEqual(named["Working"], [(0, "Planner", "waiting"), (1, "Checker", "working")])
        self.assertEqual(named["Completed"], [(0, "Reviewer", "done")])

    def test_tree_from_app_dir(self):
        self.check(self.groups(False)[1])

    def test_tree_from_log_uris(self):
        self.check(self.groups(True)[1])

    def test_set_root_switches_data_dir(self):
        empty, app = tempfile.mkdtemp(), tempfile.mkdtemp()
        fx.build_tree(app, with_logs=False)
        tree = discovery.Tree(empty, fx.ROOT)
        self.assertEqual(discovery.flat(tree.rows()), [])
        tree.set_root(fx.ROOT, app)
        self.assertEqual(tree.app_dir, app)
        self.check(tree.rows())

    def test_newest_top_level_first(self):
        app = tempfile.mkdtemp()
        fx.build_tree(app)
        # finish the checker and let it report back to the planner: both trees are then
        # completed; Reviewer started later
        import json
        with open(fx.path_for(app, fx.CHECKER), "a") as f:
            f.write(json.dumps(fx.generic("ok")) + "\n" + json.dumps(fx.planner(content="fine")) + "\n")
        with open(fx.path_for(app, fx.PLANNER), "a") as f:
            f.write(json.dumps(fx.system("[Message] sender=chk-2222 content=fine")) + "\n"
                    + json.dumps(fx.planner(content="Checked.")) + "\n")
        rows = discovery.flat(discovery.Tree(app, fx.ROOT).rows())
        self.assertEqual([r["role"] for r in rows], ["Reviewer", "Planner", "Checker"])

    def test_items_name_parent(self):
        tree, groups = self.groups(False)
        checker = [r for r in discovery.flat(groups) if r["role"] == "Checker"][0]
        self.assertEqual(checker["parent"], fx.PLANNER)
        self.assertEqual(tree.items(checker)[0]["title"], "Message from parent")

    def test_cycle(self):
        app = tempfile.mkdtemp()
        fx.write(app, "a", [fx.planner(calls=[fx.invoke("B")]), fx.generic(fx.started("b"))])
        fx.write(app, "b", [fx.planner(calls=[fx.invoke("A")]), fx.generic(fx.started("a"))])
        rows = discovery.flat(discovery.Tree(app, "a").rows())
        self.assertEqual([r["role"] for r in rows], ["B"])

    def test_no_root_and_missing_transcript(self):
        self.assertEqual(discovery.flat(discovery.Tree("/nonexistent").rows()), [])
        self.assertEqual(discovery.flat(discovery.Tree("/nonexistent", "x").rows()), [])

    def test_static_fixture(self):
        # Checked-in files age, so the running Checker may read "quiet": check the shape only.
        app = os.path.join(helpers.FIXTURES, "app")
        rows = discovery.flat(discovery.Tree(app, fx.ROOT).rows())
        self.assertEqual(sorted((r["depth"], r["role"]) for r in rows),
                         [(0, "Planner"), (0, "Reviewer"), (1, "Checker")])

    def test_wide_role_alignment(self):
        row_ascii = {"depth": 0, "role": "Planner", "state": "waiting", "age": "10s", "text": "planning"}
        row_wide = {"depth": 0, "role": "한글 체커 에이전트 이름 아주 길게 테스트 🚀", "state": "waiting", "age": "10s",
                    "text": "planning"}
        text_ascii = discovery.row_text(row_ascii)
        text_wide = discovery.row_text(row_wide)
        prefix_ascii = text_ascii[:text_ascii.index(row_ascii["state"])]
        prefix_wide = text_wide[:text_wide.index(row_wide["state"])]
        self.assertEqual(col_width(prefix_ascii), col_width(prefix_wide))

    def test_row_text_model_column_alignment(self):
        r_none = {"depth": 0, "role": "Planner", "state": "waiting", "model": None, "age": "4m", "text": "Plan."}
        r_disp = {"depth": 0, "role": "Checker", "state": "done", "model": "Gemini 4 Carbon", "age": "12s",
                  "text": "Checked."}
        r_long = {"depth": 1, "role": "Worker", "state": "working", "model": "gemini-3.8-flash-tiered",
                  "age": "1h02m", "text": "$ make"}
        t_none = discovery.row_text(r_none)
        t_disp = discovery.row_text(r_disp)
        t_long = discovery.row_text(r_long)

        self.assertIn("waiting -                    ", t_none)
        self.assertIn("done    Gemini 4 Carbon      ", t_disp)
        self.assertIn("working gemini-3.8-flash-tie ", t_long)

        # Elapsed time (`age`) right-edge and `text` start column must align identically across all 3 rows.
        self.assertEqual(col_width(t_none[:t_none.index("Plan.")]), col_width(t_disp[:t_disp.index("Checked.")]))
        self.assertEqual(col_width(t_disp[:t_disp.index("Checked.")]), col_width(t_long[:t_long.index("$ make")]))

    def test_tree_merges_summaries_db_and_resolves_models(self):
        import sqlite3
        from test_agentdb import gen_meta_blob, summary_blob

        app = tempfile.mkdtemp()
        # Root transcript has no invoke_subagent step (simulating compacted/missing parent step),
        # while child transcript exists on disk.
        fx.write(app, "root-x", [fx.user("audit"), fx.planner(content="Done.")])
        fx.write(app, "child-1184", [fx.system("check"), fx.planner(content="Skill looks solid.")])

        con_s = sqlite3.connect(os.path.join(app, "conversation_summaries.db"))
        con_s.execute(
            "CREATE TABLE conversation_summaries ("
            "conversation_id TEXT PRIMARY KEY, parent_conversation_id TEXT, "
            "agent_name TEXT, raw_summary BLOB)"
        )
        raw = summary_blob(ts=1794626018, sub_type="research", role="Skill Design Builder Checker", model="inherit")
        con_s.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
                      ("child-1184", "root-x", "research", raw))
        con_s.commit()
        con_s.close()

        os.makedirs(os.path.join(app, "conversations"), exist_ok=True)
        con_c = sqlite3.connect(os.path.join(app, "conversations", "child-1184.db"))
        con_c.execute("CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)")
        blob = gen_meta_blob("gemini-4-carbon", "Gemini 4 Carbon (Medium)", "gemini-4-carbon-medium")
        con_c.execute("INSERT INTO gen_metadata VALUES (0, ?, ?)", (blob, len(blob)))
        con_c.commit()
        con_c.close()

        tree = discovery.Tree("/nonexistent", None)
        tree.set_root("root-x", app)
        rows = discovery.flat(tree.rows(now=1794626078))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "child-1184")
        self.assertEqual(rows[0]["role"], "Skill Design Builder Checker")
        self.assertEqual(rows[0]["state"], "done")
        self.assertEqual(rows[0]["model"], "Gemini 4 Carbon")
        self.assertEqual(rows[0]["age"], "1m")


if __name__ == "__main__":
    unittest.main()
