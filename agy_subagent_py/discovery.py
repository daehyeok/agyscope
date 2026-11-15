"""Builds a conversation's subagent tree from transcripts and groups it for display.

Starting from the root conversation's transcript, every subagent it started is
followed into its own transcript, recursively, so nested subagents and subagents
that finished long ago are all found. Nothing outside the transcripts is needed.
"""

import os
import time

from .agentdb import AgentDB
from .transcript import Transcript, col_pad

ACTIVE = ("working", "waiting", "quiet")


def transcript_path(app_dir, conv_id):
    """Where a conversation's transcript lives when its start result didn't say."""
    return os.path.join(app_dir, "brain", conv_id, ".system_generated", "logs", "transcript.jsonl")


def ordered(recs):
    """Trees in display order: [[(depth, rec), ...], ...], newest top-level first, children in start order."""
    ids = {r["id"] for r in recs}
    kids = {}
    for r in recs:
        kids.setdefault(r.get("parent"), []).append(r)
    tops = sorted((r for r in recs if r.get("parent") not in ids), key=lambda r: r.get("ts") or 0, reverse=True)
    seen, trees = set(), []

    def walk(r, depth, acc):
        if r["id"] in seen:
            return
        seen.add(r["id"])
        acc.append((depth, r))
        for k in sorted(kids.get(r["id"], []), key=lambda k: k.get("ts") or 0):
            walk(k, depth + 1, acc)

    for r in tops + recs:  # leftovers (a cycle in bad data) still show
        acc = []
        walk(r, 0, acc)
        if acc:
            trees.append(acc)
    return trees


def age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    return "%dh%02dm" % (seconds // 3600, seconds % 3600 // 60)


class Tree:
    """All subagents under one root conversation, kept up to date by rows()."""

    def __init__(self, app_dir, root=None):
        self.app_dir = app_dir
        self.root = root
        self.db = AgentDB(app_dir)
        self.tails = {}   # conversation id -> Transcript
        self.names = {}   # conversation id -> display name

    def set_root(self, root, app_dir=None):
        """Follows root, read from app_dir when given (the data directory it was found in)."""
        if app_dir and app_dir != self.app_dir:
            self.app_dir = app_dir
            self.db = AgentDB(app_dir)
            self.tails = {}  # paths derived from the old directory are stale
            self.names = {}
        if root != self.root:
            self.root = root
            self.names = {}

    def _tail(self, conv_id, path=None):
        t = self.tails.get(conv_id)
        if t is None:
            t = self.tails[conv_id] = Transcript(path or transcript_path(self.app_dir, conv_id), conv_id)
        t.poll()
        return t

    def records(self):
        """Every subagent under the root, as {id, role, type, model, parent, ts, transcript}."""
        if not self.root:
            return []
        recs, by_id, seen = [], {}, {self.root}
        queue = [self._tail(self.root)]
        while queue:
            t = queue.pop(0)
            for child in list(t.children) + self.db.children_of(t.conv_id):
                cid = child["id"]
                if cid in seen:
                    prev = by_id.get(cid)
                    if prev is not None:
                        for k in ("role", "type", "model", "ts", "transcript"):
                            if not prev.get(k) and child.get(k):
                                prev[k] = child[k]
                    continue
                seen.add(cid)
                rec = dict(child)
                by_id[cid] = rec
                recs.append(rec)
                queue.append(self._tail(cid, rec.get("transcript")))
        return recs

    def rows(self, now=None):
        """Display rows grouped: [("Working", [row...]), ("Completed", [row...])]."""
        now = time.time() if now is None else now
        recs = self.records()
        self.names = {r["id"]: (r.get("role") or r.get("type") or r["id"][:8]) for r in recs}
        working, completed = [], []
        for tree in ordered(recs):
            rows = []
            for depth, r in tree:
                t = self.tails[r["id"]]
                state, text = t.state(now)
                rows.append({"id": r["id"], "depth": depth, "role": self.names[r["id"]], "state": state,
                             "model": self.db.model_of(r["id"], r.get("model")),
                             "age": age(now - (r.get("ts") or now)), "text": text, "t": t,
                             "parent": r.get("parent")})
            (working if any(x["state"] in ACTIVE for x in rows) else completed).extend(rows)
        return [("Working", working), ("Completed", completed)]

    def items(self, row):
        return row["t"].items(self.names, row["parent"])


def flat(groups):
    return [r for _, rows in groups for r in rows]


def row_text(row):
    name = col_pad("  " * row["depth"] + row["role"], 24)
    model = col_pad(row.get("model") or "-", 20)
    return "  %s %-7s %s %6s  %s" % (name, row["state"], model, row["age"], row["text"])
