"""Builds synthetic agent transcripts for tests (no real transcript content).

    python3 tests/py/fixtures/make_fixtures.py    regenerates tests/py/fixtures/app/

Tree written by build_tree():

    root  "root-0000"
      Planner  "plan-1111"   waiting: ended its turn after starting Checker
        Checker "chk-2222"   working (last step is a tool call with no result)
      Reviewer "rev-3333"    done
      (one invoke_subagent that failed: no child)
"""

import json
import os

ROOT, PLANNER, CHECKER, REVIEWER = "root-0000", "plan-1111", "chk-2222", "rev-3333"


def planner(content="", calls=(), status="DONE", at="2026-01-01T00:00:00Z"):
    return {"type": "PLANNER_RESPONSE", "status": status, "created_at": at, "content": content,
            "tool_calls": [{"name": n, "args": a} for n, a in calls]}


def generic(content, status="DONE", at="2026-01-01T00:00:01Z"):
    return {"type": "GENERIC", "status": status, "created_at": at, "content": content}


def system(content, at="2026-01-01T00:00:00Z"):
    return {"type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": at, "content": content}


def user(content):
    return {"type": "USER_INPUT", "status": "DONE", "content": content}


def invoke(role, type_name="worker", model=None):
    args = {"Role": role, "TypeName": type_name, "Prompt": "do it"}
    if model is not None:
        args["Model"] = model
    return ("invoke_subagent", args)


def started(child, log=None):
    body = {"conversationId": child}
    if log:
        body["logAbsoluteUri"] = "file://" + log
    return "Created At: x\nCompleted At: y\nCreated the following subagent:\n" + json.dumps(body, indent=2)


def path_for(app, conv):
    return os.path.join(app, "brain", conv, ".system_generated", "logs", "transcript.jsonl")


def write(app, conv, steps):
    p = path_for(app, conv)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")
    return p


def build_tree(app, with_logs=False):
    """Writes the tree above under app/brain; with_logs adds logAbsoluteUri to results."""
    log = (lambda c: path_for(app, c)) if with_logs else (lambda c: None)
    write(app, ROOT, [
        user("plan and review"),
        planner(calls=[invoke("Planner")], at="2026-01-01T00:00:00Z"),
        generic(started(PLANNER, log(PLANNER)), at="2026-01-01T00:00:01Z"),
        planner(calls=[invoke("Broken")]),
        generic("invoke failed: quota", status="ERROR"),
        planner(calls=[invoke("Reviewer", "reviewer")], at="2026-01-01T00:01:00Z"),
        generic(started(REVIEWER, log(REVIEWER)), at="2026-01-01T00:01:01Z"),
        planner(content="Both started."),
    ])
    write(app, PLANNER, [
        system("<SYSTEM_MESSAGE>sender=root-0000 content=make a plan</SYSTEM_MESSAGE>"),
        planner(calls=[("read_file", {"Path": "/repo/BUILD", "StartLine": 1, "EndLine": 674})]),
        generic("file contents"),
        planner(calls=[invoke("Checker", "checker")], at="2026-01-01T00:00:30Z"),
        generic(started(CHECKER, log(CHECKER)), at="2026-01-01T00:00:31Z"),
        planner(content="Plan written.\nDetails follow."),
    ])
    write(app, CHECKER, [
        system("<SYSTEM_MESSAGE>sender=plan-1111 content=check it</SYSTEM_MESSAGE>"),
        planner(calls=[("run_command", {"CommandLine": "make test", "toolSummary": "run tests"})]),
    ])
    write(app, REVIEWER, [
        system("<SYSTEM_MESSAGE>sender=root-0000 content=review</SYSTEM_MESSAGE>"),
        planner(calls=[("edit_file", {"Path": "/repo/foo.py", "Target": "a\nb\nc", "Replacement": "d"})]),
        generic("ok"),
        planner(content="No issues found."),
    ])


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    build_tree(os.path.join(here, "app"))
    print("wrote", os.path.join(here, "app"))
