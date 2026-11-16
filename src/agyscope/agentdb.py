"""Read-only SQLite + minimal protobuf reader for Antigravity model & child metadata.

Reads `<app_dir>/conversations/<id>.db` (`gen_metadata`) for resolved model labels
and `<app_dir>/conversation_summaries.db` (`parent_conversation_id`) as a fallback
when a parent transcript omits or compacts a child subagent. All database opens use
`?mode=ro` URIs and cache by `(db_stat, wal_stat)` so unchanged 1s UI refreshes do
zero redundant SQLite reads while uncheckpointed WAL writes invalidate immediately.
"""

import contextlib
import os
import re
import sqlite3
import urllib.parse

EFFORT_RE = re.compile(r"\s*\((?:Minimal|Low|Medium|High|Max)\)\s*$", re.I)


def _read_varint(buf, i, n):
    val = 0
    shift = 0
    while i < n and shift <= 63:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7
    return None, n


def pb_fields(buf):
    """Parses top-level protobuf fields into {field_num: [values...]} without schema deps."""
    if isinstance(buf, memoryview):
        buf = bytes(buf)
    if not isinstance(buf, (bytes, bytearray)) or not buf:
        return {}
    out = {}
    i, n = 0, len(buf)
    while i < n:
        tag, i = _read_varint(buf, i, n)
        if tag is None:
            break
        field, wt = tag >> 3, tag & 7
        if field == 0:
            break
        if wt == 0:
            val, i = _read_varint(buf, i, n)
            if val is None:
                break
            out.setdefault(field, []).append(val)
        elif wt == 2:
            ln, i = _read_varint(buf, i, n)
            if ln is None or ln < 0 or i + ln > n:
                break
            out.setdefault(field, []).append(bytes(buf[i:i + ln]))
            i += ln
        elif wt == 1:
            if i + 8 > n:
                break
            i += 8
        elif wt == 5:
            if i + 4 > n:
                break
            i += 4
        else:
            break
    return out


def _pb_str(fields, num):
    vals = fields.get(num)
    if not vals or not isinstance(vals[-1], (bytes, bytearray)):
        return ""
    return vals[-1].decode("utf-8", "replace").strip()


def _pb_int(fields, num):
    vals = fields.get(num)
    if not vals or not isinstance(vals[-1], int) or isinstance(vals[-1], bool):
        return None
    return vals[-1]


def normalize_model(display_name, slug):
    """Formats model label like `agy /agents`: display name minus effort suffix, else slug."""
    if isinstance(display_name, str) and display_name.strip():
        cleaned = EFFORT_RE.sub("", display_name).strip()
        if cleaned:
            return cleaned
    if isinstance(slug, str) and slug.strip():
        return slug.strip()
    return None


def _one_stat(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _stat_sig(path):
    return (_one_stat(path), _one_stat(path + "-wal"))


def _ro_connect(path):
    uri = "file:" + urllib.parse.quote(os.path.abspath(path), safe="/:") + "?mode=ro"
    return contextlib.closing(sqlite3.connect(uri, uri=True, timeout=0.2))


def _is_busy(err):
    msg = str(err).lower()
    return "locked" in msg or "busy" in msg


def _decode_gen_metadata(blob):
    top = pb_fields(blob)
    f1 = pb_fields(top[1][-1]) if top.get(1) else {}
    f3 = pb_fields(top[3][-1]) if top.get(3) else {}
    display = _pb_str(f1, 21)
    slug = _pb_str(f1, 19) or _pb_str(f3, 28)
    return normalize_model(display, slug)


class AgentDB:
    """Per-app_dir read-only cache over conversations/<id>.db and conversation_summaries.db."""

    def __init__(self, app_dir):
        self.app_dir = app_dir
        self._models = {}           # conv_id -> (sig, resolved_or_none)
        self._summaries_sig = None
        self._children = {}         # parent_id -> [child_dict, ...]

    def model_of(self, conv_id, fallback=None):
        fb = fallback.strip() if isinstance(fallback, str) and fallback.strip() else "-"
        if not self.app_dir or not conv_id or not isinstance(conv_id, str):
            return fb
        if os.sep in conv_id or (os.altsep and os.altsep in conv_id):
            return fb
        path = os.path.join(self.app_dir, "conversations", conv_id + ".db")
        sig = _stat_sig(path)
        if sig[0] is None:
            return fb
        cached = self._models.get(conv_id)
        if cached is not None and cached[0] == sig:
            return cached[1] or fb
        resolved = None
        try:
            with _ro_connect(path) as con:
                row = con.execute("SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 1").fetchone()
            if row and row[0]:
                resolved = _decode_gen_metadata(row[0])
        except sqlite3.Error as err:
            if _is_busy(err):
                return fb
        except OSError:
            return fb
        except Exception:
            resolved = None
        self._models[conv_id] = (sig, resolved)
        return resolved or fb

    def children_of(self, parent_id):
        if not self.app_dir or not parent_id or not isinstance(parent_id, str):
            return []
        path = os.path.join(self.app_dir, "conversation_summaries.db")
        sig = _stat_sig(path)
        if sig[0] is None:
            return []
        if sig != self._summaries_sig:
            self._children = {}
            self._summaries_sig = sig
        if parent_id in self._children:
            return [dict(c) for c in self._children[parent_id]]
        try:
            with _ro_connect(path) as con:
                rows = con.execute(
                    "SELECT conversation_id, agent_name, raw_summary "
                    "FROM conversation_summaries WHERE parent_conversation_id = ?",
                    (parent_id,),
                ).fetchall()
        except sqlite3.Error as err:
            if _is_busy(err):
                self._summaries_sig = None
                return []
            self._children[parent_id] = []
            return []
        except OSError:
            self._summaries_sig = None
            return []
        items = []
        for idx, (cid, agent_name, raw) in enumerate(rows):
            if not cid or not isinstance(cid, str):
                continue
            try:
                top = pb_fields(raw or b"")
                f7 = pb_fields(top[7][-1]) if top.get(7) else {}
                ts = _pb_int(f7, 1)
                f17 = pb_fields(top[17][-1]) if top.get(17) else {}
                f8 = pb_fields(f17[8][-1]) if f17.get(8) else {}
                sub_type = _pb_str(f8, 1) or (agent_name.strip() if isinstance(agent_name, str) else "")
                role = _pb_str(f8, 2)
                model = _pb_str(f8, 12)
            except Exception:
                sub_type = agent_name.strip() if isinstance(agent_name, str) else ""
                role, model, ts = "", "", None
            items.append((idx, {
                "id": cid,
                "role": role or None,
                "type": sub_type or None,
                "model": model or None,
                "parent": parent_id,
                "ts": ts,
                "transcript": None,
            }))
        items.sort(key=lambda pair: (pair[1]["ts"] or 0, pair[0]))
        out = [rec for _, rec in items]
        self._children[parent_id] = out
        return [dict(c) for c in out]
