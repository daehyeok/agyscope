"""Reads one agent transcript (transcript.jsonl) incrementally.

A transcript is JSON Lines, one step per line. The steps this module uses:

  PLANNER_RESPONSE  the model's turn: optional reply text in "content", tool calls in "tool_calls"
  GENERIC           a tool result; results arrive in the same order as the calls
  SYSTEM_MESSAGE    a message from another agent, a finished background task, or a timer
  USER_INPUT        a message from the user
  ERROR_MESSAGE     an error reported by the agent (always an error, whatever its status)

From these it keeps display events (turns, replies, tool calls with results), the
subagent's state, and the subagents this conversation started: each invoke_subagent
call is paired with its result, whose text holds "conversationId" and "logAbsoluteUri".
"""

import calendar
import collections
import json
import os
import re
import time
import unicodedata

QUIET_AFTER = 300  # seconds without transcript changes before a running subagent shows "quiet"
CMD_TAIL = 200     # run_command output: last N lines
RESULT_HEAD = 8    # other tool results: first N lines
LOG_TAIL = 50      # running background command: last N log lines
LOG_BYTES = 65536  # ... read from the last N bytes of its log
MAIN_ARGS = ("AbsolutePath", "Path", "TargetFile", "Url", "query", "Query", "Role", "Recipient", "TaskId", "Name")
FILE_ARGS = ("AbsolutePath", "Path", "TargetFile", "File", "FilePath")
VERBS = {
    "read_file": "Analyzed", "view_file": "Analyzed", "view_file_outline": "Analyzed",
    "edit_file": "Edited", "replace_file_content": "Edited", "multi_replace_file_content": "Edited",
    "write_file": "Created", "write_to_file": "Created",
    "list_dir": "Listed", "grep_search": "Searched", "find_by_name": "Searched", "search_web": "Searched",
    "invoke_subagent": "Started subagent", "define_subagent": "Defined subagent",
}

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]")
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
BG_START = re.compile(r"running as a background task with task id:\s*(\S+)")
BG_LOG = re.compile(r"Task logs are available at:\s*file://(\S+)")
BG_DONE = re.compile(r'Task id "([^"]+)" finished with result:\s*(.*?)(?:\nLog: file://\S+\s*)?(?:</SYSTEM_MESSAGE>|$)', re.S)
SENDER = re.compile(r"sender=(\S+)")
CHILD_ID = re.compile(r'"conversationId"\s*:\s*"([^"]+)"')
CHILD_LOG = re.compile(r'"logAbsoluteUri"\s*:\s*"file://([^"]+)"')


def clean(text):
    """Terminal-safe text: no escape sequences, no control characters, no CRs."""
    text = ANSI.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return CTRL.sub("", text)


def char_width(ch):
    """Terminal display column width of a single character (0, 1, or 2)."""
    if not ch:
        return 0
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def col_width(text):
    """Total display column width of text."""
    return sum(char_width(c) for c in (text or ""))


def col_slice(text, max_cols):
    """Slices text into (head, rest) where col_width(head) <= max_cols.

    Any trailing zero-width combining characters stay attached to head rather
    than starting rest. When max_cols <= 0, returns ("", text).
    """
    if not text or max_cols <= 0:
        return "", text or ""
    cols = 0
    idx = 0
    for i, ch in enumerate(text):
        w = char_width(ch)
        if cols + w > max_cols:
            break
        cols += w
        idx = i + 1
    return text[:idx], text[idx:]


def col_pad(text, width):
    """Pads or truncates text to exactly width display columns (for width >= 0)."""
    head, _ = col_slice(text, width)
    return head + " " * max(0, width - col_width(head))


def oneline(text, limit=160):
    return " ".join(str(text or "").split())[:limit]


def first_line(text, limit=200):
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#>*- ").strip()
        if line and not line.startswith("```") and not line.startswith("|"):
            return line[:limit]
    return ""


def arg(call, key):
    """Tool argument values may be JSON-encoded inside the transcript ("\\"sleep 25\\""); decode them."""
    v = (call.get("args") or {}).get(key)
    if isinstance(v, str) and v[:1] in "\"[{":
        try:
            v = json.loads(v)
        except ValueError:
            pass
    return v


def nlines(text):
    return len(str(text).split("\n")) if text else 0


def parse_time(stamp):
    """Epoch seconds for an RFC 3339 UTC stamp like 2026-01-02T03:04:05Z, or None."""
    if not isinstance(stamp, str):
        return None
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", stamp)
    if not m:
        return None
    try:
        t = calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
    except (ValueError, OverflowError):
        return None
    tz = re.search(r"([+-])(\d\d):?(\d\d)$", stamp[19:])
    if tz:
        off = int(tz.group(2)) * 3600 + int(tz.group(3)) * 60
        t -= off if tz.group(1) == "+" else -off
    return t


def summary(call):
    """One-line summary of a tool call: (verb, object)."""
    name = call.get("name") or "?"
    if name == "run_command":
        return "Ran", oneline(arg(call, "toolSummary") or arg(call, "CommandLine"))
    if name == "schedule":
        when = arg(call, "DurationSeconds")
        when = ("%ss" % when) if when else (arg(call, "CronExpression") or "")
        return "Timer", ("%s %s" % (when, oneline(arg(call, "Prompt"), 120))).strip()
    if name == "send_message":
        return "Sent message", oneline(arg(call, "Message"), 120)
    verb = VERBS.get(name)
    path = next((arg(call, k) for k in FILE_ARGS if isinstance(arg(call, k), str)), None)
    if verb and path:
        obj = os.path.basename(path.rstrip("/")) or path
        if verb == "Analyzed" and arg(call, "StartLine"):
            obj += " #L%s-%s" % (arg(call, "StartLine"), arg(call, "EndLine") or "")
        if verb == "Edited":
            add, rem = nlines(arg(call, "Replacement") or arg(call, "ReplacementContent")), \
                nlines(arg(call, "Target") or arg(call, "TargetContent"))
            if add or rem:
                obj += " +%d -%d" % (add, rem)
        return verb, obj
    for k in MAIN_ARGS:
        v = arg(call, k)
        if isinstance(v, str) and v.strip():
            return (verb or name), oneline(v)
    return (verb or name), ""


def short(call):
    """Row text for a call: the command itself for run_command, else the summary."""
    if (call.get("name") or "") == "run_command":
        return "$ " + oneline(arg(call, "CommandLine"), 400)
    v, o = summary(call)
    return ("%s %s" % (v, o)).strip()


def result_text(content):
    lines = [l for l in clean(content).split("\n")
             if not l.startswith("Created At:") and not l.startswith("Completed At:")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def message_text(content):
    t = clean(content)
    m = re.search(r"content=(.*?)(?:</SYSTEM_MESSAGE>|$)", t, re.S)
    if not m:
        m = re.search(r"<SYSTEM_MESSAGE>(.*?)(?:</SYSTEM_MESSAGE>|$)", t, re.S)
    if m:
        t = m.group(1)
    return [l for l in t.strip().split("\n") if not l.startswith("Log: file://")]


def tail_file(path, nlines_, nbytes=LOG_BYTES):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            data = f.read()
    except OSError:
        return []
    lines = clean(data.decode("utf-8", "replace")).split("\n")
    if size > nbytes:
        lines = lines[1:]  # first line is likely cut
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-nlines_:]


def limit(lines, tool):
    if tool == "run_command":
        if len(lines) > CMD_TAIL:
            return [("dim", "... %d earlier lines" % (len(lines) - CMD_TAIL))] + [("out", l) for l in lines[-CMD_TAIL:]]
        return [("out", l) for l in lines]
    if len(lines) > RESULT_HEAD:
        return [("out", l) for l in lines[:RESULT_HEAD]] + [("dim", "... %d more lines" % (len(lines) - RESULT_HEAD))]
    return [("out", l) for l in lines]


def _call_event(name="?", verb="Result", obj=""):
    return {"kind": "call", "tool": name, "verb": verb, "obj": obj, "cmd": None, "result": None,
            "failed": False, "task": None, "log": None, "final": None, "role": None, "type": None, "model": None}


class Transcript:
    """Follows one transcript.jsonl (reading only new bytes) and keeps it as display events.

    conv_id is this transcript's conversation id; it becomes the parent of the
    subagents found in it (self.children, in start order).
    """

    def __init__(self, path, conv_id=None):
        self.path = path
        self.conv_id = conv_id
        self.mtime = 0.0
        self.reset()

    def reset(self):
        self.offset = 0
        self.partial = b""
        self.events = []
        self.pending = collections.deque()  # call events waiting for their result
        self.bg = {}                         # task id -> call event
        self.turn_bg = False                 # background work started in the current turn
        self.last = None                     # {"final", "error", "text"}
        self.nsteps = 0
        self.children = []                   # [{id, role, type, model, parent, ts, transcript}]
        self.child_ids = set()
        self.started = None                  # epoch seconds of the first step that has created_at

    def _cancel_pending(self):
        while self.pending:
            ev = self.pending.popleft()
            if ev["result"] is None:
                ev["result"] = ["[interrupted]"]

    def poll(self):
        """Reads new lines; returns True if anything was read."""
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        self.mtime = st.st_mtime
        if st.st_size < self.offset:  # rewritten: start over
            self.reset()
        if st.st_size == self.offset:
            return False
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return False
        self.offset += len(data)
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            if not raw.strip():
                continue
            try:
                step = json.loads(raw)
            except ValueError:
                continue
            if isinstance(step, dict):
                try:
                    self.add(step)
                except Exception:  # one odd step must not break the panel
                    pass
        return True

    def add(self, step):
        kind = step.get("type") or ""
        status = step.get("status") or ""
        content = step.get("content") if isinstance(step.get("content"), str) else ""
        error = status == "ERROR" and kind != "GENERIC"  # GENERIC ERROR = a failed tool call
        first = self.nsteps == 0
        self.nsteps += 1
        if self.started is None and step.get("created_at"):
            self.started = parse_time(step.get("created_at"))

        if kind == "PLANNER_RESPONSE":
            self._cancel_pending()
            calls = [c for c in (step.get("tool_calls") or []) if isinstance(c, dict)]
            if content.strip():
                self.events.append({"kind": "reply", "lines": clean(content).strip().split("\n")})
            for c in calls:
                verb, obj = summary(c)
                ev = _call_event(c.get("name") or "?", verb, obj)
                if ev["tool"] == "run_command":
                    ev["cmd"] = oneline(arg(c, "CommandLine"), 100000)
                if ev["tool"] == "invoke_subagent":
                    ev["role"] = arg(c, "Role") if isinstance(arg(c, "Role"), str) else None
                    ev["type"] = arg(c, "TypeName") if isinstance(arg(c, "TypeName"), str) else None
                    m = arg(c, "Model")
                    ev["model"] = m.strip() if isinstance(m, str) and m.strip() else None
                self.events.append(ev)
                self.pending.append(ev)
            if calls:
                self.last = {"final": False, "error": error, "text": short(calls[-1])}
            else:
                self.last = {"final": not error, "error": error,
                             "text": first_line(content) or (self.last or {}).get("text", "")}
        elif kind == "GENERIC":
            lines = result_text(content)
            ev = self.pending.popleft() if self.pending else None
            if ev is None:
                ev = _call_event()
                self.events.append(ev)
            ev["result"] = lines
            ev["failed"] = status == "ERROR"
            if ev["tool"] == "invoke_subagent" and not ev["failed"]:
                self._add_child(ev, content, step.get("created_at"))
                self.turn_bg = True  # the subagent reports back in a later turn
            text = "\n".join(lines)
            m = BG_START.search(text)
            if m:
                ev["task"] = m.group(1)
                lm = BG_LOG.search(text)
                ev["log"] = lm.group(1) if lm else None
                self.bg[ev["task"]] = ev
                self.turn_bg = True
            self.last = {"final": False, "error": False, "text": (self.last or {}).get("text", "")}
        elif kind in ("SYSTEM_MESSAGE", "USER_INPUT"):
            self._cancel_pending()
            self.turn_bg = False  # a new turn starts
            c = clean(content)
            done = BG_DONE.search(c)
            sender = SENDER.search(c)
            turn = {"kind": "turn", "first": first, "user": kind == "USER_INPUT",
                    "sender": sender.group(1) if sender else None, "task": None,
                    "lines": message_text(content) if kind == "SYSTEM_MESSAGE" else c.strip().split("\n")}
            if done and done.group(1) in self.bg:
                tev = self.bg[done.group(1)]
                tev["final"] = done.group(2).strip().split("\n")
                turn["task"] = tev
            self.events.append(turn)
            self.last = {"final": False, "error": error, "text": "[message]"}
        elif error or kind == "ERROR_MESSAGE":
            self._cancel_pending()
            text = content or (step.get("error") if isinstance(step.get("error"), str) else "") or "Error"
            self.events.append({"kind": "turn", "first": False, "user": False, "sender": None, "task": None,
                                "error": True, "lines": result_text(text)})
            self.last = {"final": False, "error": True, "text": first_line(text)}

    def _add_child(self, ev, content, created_at):
        m = CHILD_ID.search(content or "")
        if not m or m.group(1) in self.child_ids:
            return
        child = m.group(1)
        log = CHILD_LOG.search(content)
        self.child_ids.add(child)
        self.children.append({
            "id": child,
            "role": ev.get("role"),
            "type": ev.get("type"),
            "model": ev.get("model"),
            "parent": self.conv_id,
            "ts": parse_time(created_at) or time.time(),
            "transcript": log.group(1) if log else None,
        })

    def state(self, now):
        s = self.last
        if s is None:
            if self.mtime and now - self.mtime > QUIET_AFTER:
                return "quiet", ""
            return "working", ""
        if s["error"]:
            return "error", s["text"]
        if s["final"]:
            return ("waiting" if self.turn_bg else "done"), s["text"]
        if self.mtime and now - self.mtime > QUIET_AFTER:
            return "quiet", s["text"]
        return "working", s["text"]

    def items(self, names, parent):
        """Foldable items: dicts with key, depth, title, style, running, failed, body()."""
        out = []
        for i, ev in enumerate(self.events):
            if ev["kind"] == "turn":
                out.append({"key": i, "depth": 0, "title": turn_title(ev, names, parent), "style": "msg",
                            "running": False, "failed": ev.get("error", False),
                            "body": (lambda ev=ev: [("msgbody", l) for l in ev["lines"]])})
            elif ev["kind"] == "reply":
                out.append({"key": i, "depth": 1, "title": "Reply: " + (first_line("\n".join(ev["lines"])) or "..."),
                            "style": "reply", "running": False, "failed": False,
                            "body": (lambda ev=ev: [("reply", l) for l in ev["lines"]])})
            else:
                running = ev["result"] is None or (ev["task"] is not None and ev["final"] is None
                                                   and ev["tool"] != "schedule")
                verb = ev["verb"]
                if running and verb == "Ran":
                    verb = "Running"
                out.append({"key": i, "depth": 1, "title": ("%s %s" % (verb, ev["obj"])).strip(),
                            "style": "step", "running": running, "failed": ev["failed"],
                            "body": (lambda ev=ev: call_body(ev))})
        return out


def turn_title(ev, names, parent):
    if ev.get("error"):
        return "Error"
    if ev["first"]:
        return "Message from parent" if parent is not None else "Message from user"
    if ev["user"]:
        return "Message from user"
    if ev["task"] is not None:
        return "Task finished: %s" % ev["task"]["obj"]
    s = ev["sender"] or ""
    if "/task-" in s:  # a timer (or other background task) speaking
        return "Timer: %s" % (first_line("\n".join(ev["lines"])) or s.split("/")[-1])
    if s:
        return "Message from %s" % ("parent" if s == parent else names.get(s) or s[:8])
    return "Message"


def call_body(ev):
    out = []
    if ev["cmd"]:
        out.append(("cmd", "$ " + ev["cmd"]))
    if ev["task"] and ev["final"] is None and ev["tool"] != "schedule":
        out.append(("dim", "[running in background: %s]" % ev["task"].split("/")[-1]))
        out += [("out", l) for l in (tail_file(ev["log"], LOG_TAIL) if ev["log"] else [])]
    elif ev["task"] and ev["final"] is not None:
        out += limit(ev["final"], ev["tool"])
    elif ev["result"] is None:
        out.append(("dim", "..."))
    else:
        out += limit(ev["result"], ev["tool"])
    return out


def layout(items, expanded, width=None):
    """Lines as (item index, style, text). expanded(item) says whether to show its body."""
    lines = []
    for n, it in enumerate(items):
        ind = "  " * it["depth"]
        open_ = expanded(it)
        mark = "v " if open_ else "> "
        flag = "* " if it["running"] else ("x " if it["failed"] else "")
        style = "running" if it["running"] else ("failed" if it["failed"] else it["style"])
        lines += [(n, style, t) for t in wrap(ind + mark + flag + it["title"], width, ind + "    ")]
        if open_:
            for st, text in it["body"]():
                lines += [(n, st, t) for t in wrap(ind + "    " + text, width, ind + "    ")]
    return lines


def wrap(text, width, indent=""):
    if not width or width <= 0 or col_width(text) <= width:
        return [text]
    first_budget = max(2, width)
    head, text = col_slice(text, first_budget)
    out = [head]
    sub_budget = max(2, width - col_width(indent))
    while text:
        head, text = col_slice(text, sub_budget)
        out.append(indent + head)
    return out
