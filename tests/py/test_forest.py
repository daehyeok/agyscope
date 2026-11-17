import collections
import os
import sqlite3
import tempfile
import unittest

import helpers  # noqa: F401
import make_fixtures as fx
from agyscope import discovery, tasklog, textview
from agyscope.agentdb import AgentDB
from agyscope.links import LinkStore
from agyscope.transcript import Transcript

Root = collections.namedtuple("Root", "conv app_dir source")
W1 = "b029292c-7ebe-417c-b9a7-522ae4b576d1"
W2 = "072b8792-e77d-4990-ad54-04ab21eb07f3"
OTHER = "other-0000"
NOW = 1767225600 + 3600  # 2026-01-01T01:00:00Z


def worker(app, conv, sender=fx.ROOT, done=True):
    steps = [fx.system("<SYSTEM_MESSAGE>sender=%s content=go</SYSTEM_MESSAGE>" % sender,
                       at="2026-01-01T00:10:00Z"),
             fx.planner(calls=[("run_command", {"CommandLine": "make", "toolSummary": "build"})])]
    if done:
        steps += [fx.generic("ok"), fx.planner(content="Done.")]
    fx.write(app, conv, steps)


def task_log(app, conv, name, text):
    d = tasklog.tasks_dir(app, conv)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(text)


def ids(groups):
    return [(name, [(r["depth"], r["id"]) for r in rows]) for name, rows in groups]


class TranscriptStartedTest(unittest.TestCase):
    def test_started_is_first_created_at(self):
        app = tempfile.mkdtemp()
        fx.build_tree(app)
        t = Transcript(fx.path_for(app, fx.REVIEWER))
        t.poll()
        self.assertEqual(t.started, 1767225600)  # 2026-01-01T00:00:00Z


class AgentDBMetaTest(unittest.TestCase):
    def test_title_and_recent_roots(self):
        app = tempfile.mkdtemp()
        con = sqlite3.connect(os.path.join(app, "conversation_summaries.db"))
        con.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, agent_name text, "
                    "last_modified_time datetime, parent_conversation_id text, raw_summary blob)")
        con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,NULL)", [
            ("a", "Improve agyscope", "", "2026-11-17 21:29:26.770561+00:00", ""),
            ("b", "", "gpowers-implementer", "2026-11-17 22:00:00+00:00", ""),
            ("c", "", "self", "2026-11-17 23:00:00+00:00", "a"),
        ])
        con.commit()
        con.close()
        db = AgentDB(app)
        self.assertEqual(db.title_of("a"), "Improve agyscope")
        self.assertEqual(db.title_of("b"), "gpowers-implementer")
        self.assertIsNone(db.title_of("zzz"))
        self.assertEqual(db.parent_of("c"), "a")
        self.assertEqual([r["id"] for r in db.recent_roots()], ["b", "a"])
        self.assertEqual(db.recent_roots(limit=1)[0]["agent"], "gpowers-implementer")
        self.assertIsNone(AgentDB(tempfile.mkdtemp()).title_of("a"))


class TaskLogTest(unittest.TestCase):
    def test_parse_and_children(self):
        app = tempfile.mkdtemp()
        worker(app, W1)
        worker(app, W2)
        task_log(app, fx.ROOT, "task-165.log",
                 "- [phase] Task 5: StepView widget\n"
                 "- T5 impl: DONE a58ee0c | mentions 11111111-2222-3333-4444-555555555555 in prose\n"
                 "- [agent:gpowers-implementer] Implementer T5: done, turn 2 (%s): {\"concerns\":\"x\"}\n"
                 "- [agent:gpowers-reviewer] Reviewer T5: done, turn 2 (%s): {}\n"
                 "- [agent:ghost] Ghost: done (99999999-9999-9999-9999-999999999999): {}\n"
                 "- [agent:gpowers-reviewer] Reviewer T5 again (%s)\n" % (W1, W2, W2))
        self.assertEqual([c for _, c in tasklog.parse(open(os.path.join(
            tasklog.tasks_dir(app, fx.ROOT), "task-165.log")).read())],
            [W1, W2, "99999999-9999-9999-9999-999999999999"])
        kids = tasklog.TaskLogs(app).children(fx.ROOT)
        self.assertEqual([(k["id"], k["type"], k["parent"], k["linked"]) for k in kids],
                         [(W1, "gpowers-implementer", fx.ROOT, True), (W2, "gpowers-reviewer", fx.ROOT, True)])
        self.assertEqual(tasklog.TaskLogs(app).children("nope"), [])


class TreeChildrenTest(unittest.TestCase):
    def setUp(self):
        self.app = tempfile.mkdtemp()
        fx.build_tree(self.app)
        self.links = LinkStore(os.path.join(tempfile.mkdtemp(), "agyscope"))

    def test_linked_child_nested_and_marked(self):
        worker(self.app, W1)
        self.links.attach(W1, fx.ROOT)
        tree = discovery.Tree(self.app, fx.ROOT, links=self.links)
        recs = {r["id"]: r for r in tree.records()}
        self.assertTrue(recs[W1]["linked"])
        self.assertEqual(recs[W1]["parent"], fx.ROOT)
        self.assertEqual(recs[W1]["ts"], 1767226200)  # filled from its transcript's first step
        self.assertFalse(recs[fx.PLANNER].get("linked"))
        rows = {r["id"]: r for r in discovery.flat(tree.rows(now=NOW))}
        self.assertIn("~ " + W1[:8], discovery.row_text(rows[W1]))

    def test_hidden_transcript_child_dropped_with_subtree(self):
        self.links.detach(fx.PLANNER, fx.ROOT)
        tree = discovery.Tree(self.app, fx.ROOT, links=self.links)
        self.assertEqual({r["id"] for r in tree.records()}, {fx.REVIEWER})
        self.links.attach(fx.PLANNER, fx.ROOT)
        self.assertEqual({r["id"] for r in tree.records()}, {fx.REVIEWER, fx.PLANNER, fx.CHECKER})

    def test_task_log_child(self):
        worker(self.app, W2)
        task_log(self.app, fx.ROOT, "task-1.log", "- [agent:gpowers-reviewer] R: done (%s)\n" % W2)
        recs = {r["id"]: r for r in discovery.Tree(self.app, fx.ROOT).records()}
        self.assertEqual(recs[W2]["type"], "gpowers-reviewer")
        self.assertTrue(recs[W2]["linked"])

    def test_transcript_child_wins_over_link(self):
        self.links.attach(fx.REVIEWER, fx.ROOT)
        recs = {r["id"]: r for r in discovery.Tree(self.app, fx.ROOT, links=self.links).records()}
        self.assertFalse(recs[fx.REVIEWER].get("linked"))
        self.assertEqual(recs[fx.REVIEWER]["role"], "Reviewer")

    def test_exclude_skips_subtree(self):
        recs = discovery.Tree(self.app, fx.ROOT).records(exclude={fx.PLANNER})
        self.assertEqual({r["id"] for r in recs}, {fx.REVIEWER})


class ForestTest(unittest.TestCase):
    def setUp(self):
        self.app = tempfile.mkdtemp()
        fx.build_tree(self.app)
        self.app2 = tempfile.mkdtemp()
        worker(self.app2, OTHER, sender="user")
        self.links = LinkStore(os.path.join(tempfile.mkdtemp(), "agyscope"))

    def test_main_row_and_working_completed(self):
        f = discovery.Forest(self.app, links=self.links)
        f.update([Root(fx.ROOT, self.app, "pane"), Root(OTHER, self.app2, "watch")])
        groups = f.rows(now=NOW)
        self.assertEqual(ids(groups), [
            ("Working", [(0, fx.ROOT), (1, fx.REVIEWER), (1, fx.PLANNER), (2, fx.CHECKER)]),  # newest first
            ("Completed", [(0, OTHER)]),
        ])
        main = groups[0][1][0]
        self.assertTrue(main["main"] and main["pane"])
        self.assertEqual(main["role"], "main")
        self.assertIsNone(main["parent"])
        self.assertEqual(groups[1][1][0]["tree"].app_dir, self.app2)  # its own data directory
        self.assertEqual(f.shown, {fx.ROOT, fx.PLANNER, fx.CHECKER, fx.REVIEWER, OTHER})
        self.assertEqual(f.root, fx.ROOT)

    def test_main_row_view_and_pager(self):
        f = discovery.Forest(self.app)
        f.update([Root(fx.ROOT, self.app, "pane")])
        main = discovery.flat(f.rows(now=NOW))[0]
        titles = [it["title"] for it in f.items(main)]
        self.assertEqual(titles[0], "Message from user")
        self.assertIn("Started subagent Planner", titles)
        self.assertTrue(textview.plain(f, main))

    def test_hidden_main_equals_single_root_layout(self):
        f = discovery.Forest(self.app)
        f.update([Root(fx.ROOT, self.app, "pane")])
        legacy = discovery.Tree(self.app, fx.ROOT).rows(now=NOW)
        self.assertEqual(ids(f.rows(now=NOW, show_main=False)), ids(legacy))

    def test_pane_switch_keeps_other_tree(self):
        f = discovery.Forest(self.app)
        f.update([Root(fx.ROOT, self.app, "pane"), Root(OTHER, self.app2, "watch")])
        f.rows(now=NOW)
        other = f.trees[(OTHER, self.app2)]
        tails = dict(other.tails)
        worker(self.app, W1, sender="user")
        f.update([Root(W1, self.app, "pane"), Root(OTHER, self.app2, "watch")])
        f.rows(now=NOW)
        self.assertIs(f.trees[(OTHER, self.app2)], other)
        self.assertEqual(other.tails, tails)
        self.assertNotIn((fx.ROOT, self.app), f.trees)

    def test_child_listed_once_under_first(self):
        worker(self.app, W1)
        worker(self.app, OTHER, sender="user")
        self.links.attach(W1, fx.ROOT)
        self.links.attach(W1, OTHER)
        f = discovery.Forest(self.app, links=self.links)
        f.update([Root(fx.ROOT, self.app, "pane"), Root(OTHER, self.app, "watch")])
        rows = discovery.flat(f.rows(now=NOW))
        self.assertEqual([r["id"] for r in rows].count(W1), 1)
        self.assertEqual(next(r for r in rows if r["id"] == W1)["parent"], fx.ROOT)

    def test_root_already_listed_as_child_is_skipped(self):
        worker(self.app, W1)
        self.links.attach(W1, fx.ROOT)
        f = discovery.Forest(self.app, links=self.links)
        f.update([Root(fx.ROOT, self.app, "pane"), Root(W1, self.app, "live")])
        rows = discovery.flat(f.rows(now=NOW))
        self.assertEqual([r["id"] for r in rows].count(W1), 1)

    def test_pane_first_then_newest(self):
        worker(self.app, OTHER, sender="user", done=False)
        f = discovery.Forest(self.app)
        f.update([Root(OTHER, self.app, "live"), Root(fx.ROOT, self.app, "pane")])
        working = f.rows(now=NOW)[0][1]
        self.assertEqual([r["id"] for r in working if r["main"]], [fx.ROOT, OTHER])
