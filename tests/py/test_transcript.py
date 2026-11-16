import json
import os
import tempfile
import time
import unittest

import helpers  # noqa: F401
import make_fixtures as fx
from agyscope.transcript import (
    Transcript,
    col_pad,
    col_slice,
    col_width,
    layout,
    parse_time,
    wrap,
)


def tr(steps, conv="c"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.jsonl")
    with open(p, "w") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")
    t = Transcript(p, conv)
    t.poll()
    return t, p


class StateTest(unittest.TestCase):
    def test_done(self):
        t, _ = tr([fx.system("go"), fx.planner(content="No issues found.")])
        self.assertEqual(t.state(time.time()), ("done", "No issues found."))

    def test_working_on_command(self):
        t, _ = tr([fx.system("go"), fx.planner(calls=[("run_command", {"CommandLine": "make test"})])])
        self.assertEqual(t.state(time.time()), ("working", "$ make test"))

    def test_waiting_then_working(self):
        steps = [fx.system("go"),
                 fx.planner(calls=[("run_command", {"CommandLine": "make"})]),
                 fx.generic("running as a background task with task id: c/task-1\n"
                            "Task logs are available at: file:///tmp/x.log"),
                 fx.planner(content="Started the build, waiting.")]
        t, p = tr(steps)
        self.assertEqual(t.state(time.time())[0], "waiting")
        with open(p, "a") as f:
            f.write(json.dumps(fx.system('<SYSTEM_MESSAGE>Task id "c/task-1" finished with result: ok'
                                         '</SYSTEM_MESSAGE>')) + "\n")
        t.poll()
        self.assertEqual(t.state(time.time())[0], "working")

    def test_error(self):
        t, _ = tr([fx.system("go"), fx.planner(content="model failed", status="ERROR")])
        self.assertEqual(t.state(time.time())[0], "error")

    def test_waiting_on_nested_subagent(self):
        steps = [fx.system("go"), fx.planner(calls=[fx.invoke("Inner")]), fx.generic(fx.started("inner-1")),
                 fx.planner(content="Started Inner, waiting for its reply.")]
        t, p = tr(steps)
        self.assertEqual(t.state(time.time())[0], "waiting")
        with open(p, "a") as f:
            f.write(json.dumps(fx.system("[Message] sender=inner-1 content=inner-done")) + "\n")
        t.poll()
        self.assertEqual(t.state(time.time())[0], "working")

    def test_error_message_step(self):
        t, _ = tr([fx.system("go"), fx.planner(calls=[("read_file", {"Path": "/x"})]), fx.generic("ok"),
                   {"type": "ERROR_MESSAGE", "status": "DONE", "content": "Agent stopped: quota exceeded"}])
        self.assertEqual(t.state(time.time()), ("error", "Agent stopped: quota exceeded"))
        self.assertTrue(t.events[-1].get("error"))

    def test_failed_tool_is_not_error(self):
        t, _ = tr([fx.system("go"), fx.planner(calls=[("read_file", {"Path": "/x"})]),
                   fx.generic("no such file", status="ERROR")])
        self.assertEqual(t.state(time.time())[0], "working")

    def test_quiet(self):
        t, p = tr([fx.system("go"), fx.planner(calls=[("run_command", {"CommandLine": "sleep 1000"})])])
        old = time.time() - 400
        os.utime(p, (old, old))
        t.poll()
        self.assertEqual(t.state(time.time())[0], "quiet")


class SummaryTest(unittest.TestCase):
    def titles(self, t):
        return [it["title"] for it in t.items({}, "root")]

    def test_summaries(self):
        t, _ = tr([fx.system("go"),
                   fx.planner(calls=[("read_file", {"Path": "/r/BUILD", "StartLine": 1, "EndLine": 674})]),
                   fx.generic("x"),
                   fx.planner(calls=[("run_command", {"CommandLine": "x", "toolSummary": "run dry run test"})]),
                   fx.generic("y"),
                   fx.planner(calls=[("edit_file", {"Path": "/r/foo.py", "Target": "a\nb\nc", "Replacement": "d"})]),
                   fx.generic("z"),
                   fx.planner(calls=[fx.invoke("Checker")]),
                   fx.generic(fx.started("k")),
                   fx.planner(content="All good.\nmore")])
        self.assertEqual(self.titles(t), ["Message from parent", "Analyzed BUILD #L1-674", "Ran run dry run test",
                                          "Edited foo.py +1 -3", "Started subagent Checker", "Reply: All good."])

    def test_turn_titles(self):
        t, _ = tr([fx.system("go"),
                   fx.planner(calls=[("run_command", {"CommandLine": "make", "toolSummary": "build"})]),
                   fx.generic("running as a background task with task id: c/task-9"),
                   fx.planner(content="waiting"),
                   fx.system('<SYSTEM_MESSAGE>Task id "c/task-9" finished with result: done</SYSTEM_MESSAGE>'),
                   fx.system("<SYSTEM_MESSAGE>sender=root content=hi</SYSTEM_MESSAGE>"),
                   fx.system("<SYSTEM_MESSAGE>sender=k2 content=yo</SYSTEM_MESSAGE>")])
        titles = [it["title"] for it in t.items({"k2": "Checker"}, "root")]
        self.assertIn("Task finished: build", titles)
        self.assertIn("Message from parent", titles[1:])
        self.assertIn("Message from Checker", titles)

    def test_running_step_unfolded_in_layout(self):
        t, _ = tr([fx.system("go"), fx.planner(calls=[("run_command", {"CommandLine": "make"})])])
        lines = [x[2] for x in layout(t.items({}, "p"), lambda it: it["running"])]
        self.assertIn("  v * Running make", lines)
        self.assertIn("      $ make", lines)


class ChildrenTest(unittest.TestCase):
    def test_children_and_failed_start(self):
        t, _ = tr([fx.planner(calls=[fx.invoke("Planner", "planner")], at="2026-01-01T00:00:00Z"),
                   fx.generic(fx.started("p1", "/x/p1.jsonl"), at="2026-01-01T00:00:05Z"),
                   fx.planner(calls=[fx.invoke("Broken")]),
                   fx.generic("quota", status="ERROR"),
                   fx.planner(calls=[("read_file", {"Path": "/a"})]),
                   fx.generic(fx.started("not-a-child"))], conv="root")
        self.assertEqual(len(t.children), 1)
        c = t.children[0]
        self.assertEqual((c["id"], c["role"], c["type"], c["parent"], c["transcript"]),
                         ("p1", "Planner", "planner", "root", "/x/p1.jsonl"))
        self.assertEqual(c["ts"], parse_time("2026-01-01T00:00:05Z"))

    def test_incremental_and_partial_line(self):
        t, p = tr([fx.planner(calls=[fx.invoke("A")])], conv="r")
        line = json.dumps(fx.generic(fx.started("a1")))
        with open(p, "a") as f:
            f.write(line[:20])
        t.poll()
        self.assertEqual(t.children, [])
        with open(p, "a") as f:
            f.write(line[20:] + "\n")
        t.poll()
        self.assertEqual([c["id"] for c in t.children], ["a1"])

    def test_bad_line_ignored(self):
        t, p = tr([fx.planner(calls=[fx.invoke("A")])])
        with open(p, "a") as f:
            f.write("not json\n" + json.dumps(fx.generic(fx.started("a1"))) + "\n")
        t.poll()
        self.assertEqual(len(t.children), 1)

    def test_interrupted_turn_does_not_drop_subagent(self):
        steps = [
            fx.planner(calls=[("run_command", {"CommandLine": "cmd1"}),
                              ("run_command", {"CommandLine": "cmd2"})]),
            fx.user("stop and review instead"),
            fx.planner(calls=[("write_file", {"Path": "/tmp/a.md", "Content": "hi"})]),
            fx.generic("Created file."),
            fx.planner(calls=[fx.invoke("Skill Design Builder Checker", "research", model="inherit")]),
            fx.generic(fx.started("child-1184")),
        ]
        t, _ = tr(steps, conv="root")
        self.assertEqual([(c["id"], c["role"], c["type"], c["model"]) for c in t.children],
                         [("child-1184", "Skill Design Builder Checker", "research", "inherit")])
        items = t.items({}, "p")
        cmd_items = [it for it in items if "cmd1" in it["title"] or "cmd2" in it["title"]]
        self.assertTrue(all(not it["running"] for it in cmd_items))
        self.assertEqual(cmd_items[0]["body"]()[-1], ("out", "[interrupted]"))

    def test_parse_time(self):
        self.assertEqual(parse_time("1970-01-01T00:01:00Z"), 60)
        self.assertEqual(parse_time("1970-01-01T01:00:00+01:00"), 0)
        self.assertIsNone(parse_time("nope"))


class UnicodeTest(unittest.TestCase):
    def test_col_width_and_pad(self):
        self.assertEqual(col_width("한글"), 4)
        self.assertEqual(col_width("🚀"), 2)
        self.assertEqual(col_width("e\u0301"), 1)
        self.assertEqual(col_width(col_pad("한글테스트", 5)), 5)
        self.assertEqual(col_pad("한글테스트", 5), "한글 ")

    def test_col_pad_odd_boundary_and_combining(self):
        self.assertEqual(col_pad("가", 1), " ")
        self.assertEqual(col_slice("e\u0301x", 1), ("e\u0301", "x"))

    def test_wrap_wide_chars(self):
        lines = wrap("가나다라마바사", 6, "  ")
        for line in lines:
            self.assertLessEqual(col_width(line), 6)
        preserved = lines[0] + "".join(l[2:] for l in lines[1:])
        self.assertEqual(preserved, "가나다라마바사")


if __name__ == "__main__":
    unittest.main()
