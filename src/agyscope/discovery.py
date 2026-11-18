"""Builds a conversation's subagent tree from transcripts and groups it for display.

Starting from a root conversation's transcript, every subagent it started is
followed into its own transcript, recursively, so nested subagents and subagents
that finished long ago are all found. A Forest holds several root conversations
(the pane's, attached and live ones), each listed once.
"""

import os
import time

from .agentdb import AgentDB
from .tasklog import TaskLogs
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
    """All subagents under one root conversation, kept up to date by rows() / conversation().

    Children come from, in this order: the transcripts' invoke_subagent results, the
    agent's conversation_summaries.db, conversation links (links, a LinkStore) and the
    workflow agents named in task logs. Children hidden by a detach are skipped.
    """

    def __init__(self, app_dir, root=None, links=None):
        self.app_dir = app_dir
        self.root = root
        self.links = links
        self.db = AgentDB(app_dir)
        self.tasklogs = TaskLogs(app_dir)
        self.tails = {}   # conversation id -> Transcript
        self.names = {}   # conversation id -> display name

    def set_root(self, root, app_dir=None):
        """Follows root, read from app_dir when given (the data directory it was found in)."""
        if app_dir and app_dir != self.app_dir:
            self.app_dir = app_dir
            self.db = AgentDB(app_dir)
            self.tasklogs = TaskLogs(app_dir)
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

    def _children(self, t):
        conv = t.conv_id
        found = list(t.children) + self.db.children_of(conv)
        hidden = set()
        if self.links is not None:
            hidden = self.links.hidden(conv)
            found += [{"id": cid, "role": None, "type": self.db.title_of(cid), "model": None, "parent": conv,
                       "ts": None, "transcript": None, "linked": True} for cid in self.links.children(conv)]
        found += self.tasklogs.children(conv)
        return [c for c in found if c["id"] not in hidden]

    def records(self, exclude=frozenset()):
        """Every subagent under the root, as {id, role, type, model, parent, ts, transcript, linked}.

        Ids in exclude (listed elsewhere already) are skipped with their subtrees.
        """
        if not self.root:
            return []
        recs, by_id, seen = [], {}, {self.root}
        queue = [self._tail(self.root)]
        while queue:
            t = queue.pop(0)
            for child in self._children(t):
                cid = child["id"]
                if cid in seen:
                    prev = by_id.get(cid)
                    if prev is not None:
                        for k in ("role", "type", "model", "ts", "transcript"):
                            if not prev.get(k) and child.get(k):
                                prev[k] = child[k]
                    continue
                if cid in exclude:
                    continue
                seen.add(cid)
                rec = dict(child)
                by_id[cid] = rec
                recs.append(rec)
                tail = self._tail(cid, rec.get("transcript"))
                if not rec.get("ts"):
                    rec["ts"] = tail.started
                queue.append(tail)
        return recs

    def _row(self, r, depth, now):
        t = self.tails[r["id"]]
        state, text = t.state(now)
        return {"id": r["id"], "depth": depth, "role": self.names[r["id"]], "state": state,
                "model": self.db.model_of(r["id"], r.get("model")),
                "age": age(now - (r.get("ts") or now)), "text": text, "t": t,
                "parent": r.get("parent"), "linked": bool(r.get("linked")), "main": False,
                "kind": self.db.kind_of(r["id"]), "tree": self}

    def rows(self, now=None):
        """Display rows grouped: [("Working", [row...]), ("Completed", [row...])], without a main row."""
        now = time.time() if now is None else now
        recs = self.records()
        self.names = {r["id"]: (r.get("role") or r.get("type") or r["id"][:8]) for r in recs}
        working, completed = [], []
        for tree in ordered(recs):
            rows = [self._row(r, depth, now) for depth, r in tree]
            (working if any(x["state"] in ACTIVE for x in rows) else completed).extend(rows)
        return [("Working", working), ("Completed", completed)]

    def main_name(self):
        return self.db.title_of(self.root) or "main"

    def conversation(self, now, exclude=frozenset(), show_main=True, pane=False):
        """(rows, subagent trees): the main row (if show_main) then every subagent, depth-first.

        trees is [[row, ...], ...], one list per top-level subagent tree, for grouping
        when main rows are hidden.
        """
        if not self.root:
            return [], []
        recs = self.records(exclude)
        root_t = self.tails[self.root]
        self.names = {r["id"]: (r.get("role") or r.get("type") or r["id"][:8]) for r in recs}
        self.names[self.root] = self.main_name()
        rows = []
        if show_main:
            state, text = root_t.state(now) if root_t.mtime else ("-", "no transcript")
            rows.append({"id": self.root, "depth": 0, "role": self.names[self.root], "state": state,
                         "model": self.db.model_of(self.root), "age": age(now - (root_t.started or now)),
                         "text": text, "t": root_t, "parent": None, "linked": False, "main": True,
                         "kind": self.db.kind_of(self.root, default="conversation"),
                         "pane": pane, "tree": self})
        base = 1 if show_main else 0
        trees = []
        for tree in ordered(recs):
            sub = [self._row(r, depth + base, now) for depth, r in tree]
            trees.append(sub)
            rows.extend(sub)
        return rows, trees

    def items(self, row):
        return row["t"].items(self.names, row["parent"])


class Forest:
    """Several root conversations, one Tree each, kept across refreshes.

    update() takes the current roots (objects with conv, app_dir, source, in precedence
    order); a Tree survives as long as its (conversation, data directory) stays, so a
    pane switching conversations rebuilds only the pane's Tree. rows() lists each
    conversation once: the first root (or tree) that reaches it keeps it.
    """

    def __init__(self, app_dir, links=None):
        self.app_dir = app_dir
        self.links = links
        self.trees = {}   # (conv, app_dir) -> Tree
        self.roots = ()
        self.shown = set()  # every conversation id the last rows() listed

    @property
    def root(self):
        return self.roots[0].conv if self.roots else None

    def _key(self, r):
        return (r.conv, r.app_dir or self.app_dir)

    def update(self, roots):
        keep = {}
        for r in roots:
            key = self._key(r)
            if key not in keep:
                keep[key] = self.trees.get(key) or Tree(key[1], r.conv, links=self.links)
        self.trees = keep
        self.roots = tuple(roots)

    def rows(self, now=None, show_main=True):
        """[("Working", rows), ("Completed", rows)]: one tree per conversation (pane first, then
        newest), or with show_main off, the subagent trees grouped one by one as a single root's."""
        now = time.time() if now is None else now
        claimed, convs = set(), []
        for i, r in enumerate(self.roots):
            if r.conv in claimed:
                continue
            tree = self.trees[self._key(r)]
            rows, trees = tree.conversation(now, frozenset(claimed), show_main, pane=r.source == "pane")
            claimed.add(r.conv)
            claimed.update(x["id"] for x in rows)
            started = tree.tails[r.conv].started if r.conv in tree.tails else None
            convs.append(((r.source != "pane", -(started or 0), i), rows, trees))
        convs.sort(key=lambda c: c[0])
        working, completed = [], []
        for _, rows, trees in convs:
            if show_main:
                (working if any(x["state"] in ACTIVE for x in rows) else completed).extend(rows)
            else:
                for sub in trees:
                    (working if any(x["state"] in ACTIVE for x in sub) else completed).extend(sub)
        self.shown = claimed
        return [("Working", working), ("Completed", completed)]

    def items(self, row):
        return row["tree"].items(row)


def flat(groups):
    return [r for _, rows in groups for r in rows]


def row_text(row):
    name = col_pad("  " * row["depth"] + ("~ " if row.get("linked") else "") + row["role"], 24)
    model = col_pad(row.get("model") or "-", 20)
    return "  %s %-7s %s %6s  %s" % (name, row["state"], model, row["age"], row["text"])
