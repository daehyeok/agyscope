"""Workflow agents named in a conversation's background-task logs.

`/run_workflow` scripts start agents that the agent's own records don't link to the
conversation that ran the workflow. Their ids show up only in that conversation's
<app_dir>/brain/<conv>/.system_generated/tasks/<task>.log, on lines like

    - [agent:gpowers-reviewer] Reviewer T5: done, turn 2 (072b8792-e77d-4990-ad54-04ab21eb07f3): {...

and only after each agent finishes. children() turns those lines into child records,
keeping only ids that have a transcript. Each log is re-read only when its size or
mtime changes. Standard library only.
"""

import os
import re

AGENT_LINE = re.compile(r"\[agent:([^\]\n]+)\][^\n]*?\(([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\)")


def tasks_dir(app_dir, conv):
    return os.path.join(app_dir, "brain", conv, ".system_generated", "tasks")


def parse(text):
    """[(agent type, conversation id)] in order of first appearance."""
    seen, out = set(), []
    for m in AGENT_LINE.finditer(text):
        kind, cid = m.group(1).strip(), m.group(2)
        if cid not in seen:
            seen.add(cid)
            out.append((kind, cid))
    return out


class TaskLogs:
    """Per-data-directory cache of the agents found in each conversation's task logs."""

    def __init__(self, app_dir):
        self.app_dir = app_dir
        self._logs = {}  # log path -> ((size, mtime_ns), [(kind, cid)])

    def _read(self, path, sig):
        hit = self._logs.get(path)
        if hit is not None and hit[0] == sig:
            return hit[1]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                found = parse(f.read())
        except OSError:
            found = []
        self._logs[path] = (sig, found)
        return found

    def children(self, conv):
        """[{id, type, parent, ts, transcript, linked}] of the workflow agents conv's task logs name."""
        if not self.app_dir or not conv:
            return []
        d = tasks_dir(self.app_dir, conv)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return []
        out, seen = [], {conv}
        for n in names:
            if not n.endswith(".log"):
                continue
            path = os.path.join(d, n)
            try:
                st = os.stat(path)
            except OSError:
                continue
            for kind, cid in self._read(path, (st.st_size, st.st_mtime_ns)):
                if cid in seen:
                    continue
                seen.add(cid)
                transcript = os.path.join(self.app_dir, "brain", cid, ".system_generated", "logs",
                                          "transcript.jsonl")
                if not os.path.exists(transcript):
                    continue
                out.append({"id": cid, "role": None, "type": kind, "model": None, "parent": conv,
                            "ts": None, "transcript": transcript, "linked": True})
        return out
