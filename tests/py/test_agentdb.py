import os
import shutil
import sqlite3
import tempfile
import unittest

import helpers  # noqa: F401
from agy_subagent_py.agentdb import AgentDB, normalize_model, pb_fields


def pb_varint_raw(n):
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)
    return bytes(out)


def pb_varint(field, n):
    return pb_varint_raw((field << 3) | 0) + pb_varint_raw(n)


def pb_bytes(field, data):
    return pb_varint_raw((field << 3) | 2) + pb_varint_raw(len(data)) + data


def pb_str(field, text):
    return pb_bytes(field, text.encode("utf-8"))


def gen_meta_blob(slug="", display="", config_slug=""):
    f1 = b""
    if slug:
        f1 += pb_str(19, slug)
    if display:
        f1 += pb_str(21, display)
    out = pb_bytes(1, f1) if f1 else b""
    if config_slug:
        out += pb_bytes(3, pb_str(28, config_slug))
    return out


def summary_blob(ts=None, sub_type="", role="", model=""):
    out = pb_bytes(7, pb_varint(1, ts)) if ts is not None else b""
    f8 = b""
    if sub_type:
        f8 += pb_str(1, sub_type)
    if role:
        f8 += pb_str(2, role)
    if model:
        f8 += pb_str(12, model)
    if f8:
        out += pb_bytes(17, pb_bytes(8, f8))
    return out


class AgentDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conv_db(self, app_dir, conv_id, rows=(), wal=True):
        cdir = os.path.join(app_dir, "conversations")
        os.makedirs(cdir, exist_ok=True)
        path = os.path.join(cdir, conv_id + ".db")
        con = sqlite3.connect(path)
        if wal:
            con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)")
        for idx, blob in rows:
            con.execute("INSERT INTO gen_metadata(idx, data, size) VALUES (?, ?, ?)", (idx, blob, len(blob)))
        con.commit()
        return con, path

    def test_normalize_and_model_of(self):
        self.assertEqual(normalize_model("Gemini 4 Carbon (Medium)", "gemini-4-carbon"), "Gemini 4 Carbon")
        self.assertEqual(normalize_model("Claude Opus 5.5 (high)", "claude-opus-5-5"), "Claude Opus 5.5")
        self.assertEqual(normalize_model("", "gemini-3.8-flash-tiered"), "gemini-3.8-flash-tiered")
        self.assertIsNone(normalize_model("", ""))

        con1, _ = self._conv_db(self.tmp, "c1", [
            (0, gen_meta_blob("gemini-3.8-flash-tiered", "", "gemini-3.8-flash-tiered")),
            (1, gen_meta_blob("gemini-4-carbon", "Gemini 4 Carbon (Medium)", "gemini-4-carbon-medium")),
        ])
        con1.close()

        con2, _ = self._conv_db(self.tmp, "c2", [
            (0, gen_meta_blob("gemini-3.8-flash-tiered", "", "gemini-3.8-flash-tiered")),
        ])
        con2.close()

        con_cfg, _ = self._conv_db(self.tmp, "c_cfg", [
            (0, gen_meta_blob("", "", "gemini-3.1-pro-low")),
        ])
        con_cfg.close()

        db = AgentDB(self.tmp)
        self.assertEqual(db.model_of("c1", "inherit"), "Gemini 4 Carbon")
        self.assertEqual(db.model_of("c2", "pro"), "gemini-3.8-flash-tiered")
        self.assertEqual(db.model_of("c_cfg", "pro"), "gemini-3.1-pro-low")
        self.assertEqual(db.model_of("c3", "flash"), "flash")
        self.assertEqual(db.model_of("c3"), "-")

    def test_wal_update_invalidates_model_cache(self):
        con, path = self._conv_db(self.tmp, "c_wal", rows=(), wal=True)
        try:
            con.execute("PRAGMA wal_autocheckpoint=0")
            db = AgentDB(self.tmp)
            self.assertEqual(db.model_of("c_wal", "inherit"), "inherit")

            blob = gen_meta_blob("claude-opus-5-5-medium", "Claude Opus 5.5 (Medium)", "claude-opus-5-5-medium")
            con.execute("INSERT INTO gen_metadata(idx, data, size) VALUES (?, ?, ?)", (1, blob, len(blob)))
            con.commit()
            self.assertTrue(os.path.exists(path + "-wal"))
            self.assertEqual(db.model_of("c_wal", "inherit"), "Claude Opus 5.5")
        finally:
            con.close()

    def test_children_of_summaries_db(self):
        spath = os.path.join(self.tmp, "conversation_summaries.db")
        con = sqlite3.connect(spath)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute(
            "CREATE TABLE conversation_summaries ("
            "conversation_id TEXT PRIMARY KEY, "
            "parent_conversation_id TEXT, "
            "agent_name TEXT, "
            "raw_summary BLOB)"
        )
        raw1 = summary_blob(ts=1794626018, sub_type="research", role="Skill Design Builder Checker", model="inherit")
        raw2 = summary_blob(ts=1794626050, sub_type="", role="Fallback Agent Name", model="pro")
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
            ("child-1184", "root-1", "research", raw1),
        )
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
            ("child-2222", "root-1", "sdd-reviewer", raw2),
        )
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
            ("other-child", "other-root", "self", raw1),
        )
        con.commit()
        try:
            db = AgentDB(self.tmp)
            kids = db.children_of("root-1")
            self.assertEqual(kids, [
                {
                    "id": "child-1184",
                    "role": "Skill Design Builder Checker",
                    "type": "research",
                    "model": "inherit",
                    "parent": "root-1",
                    "ts": 1794626018,
                    "transcript": None,
                },
                {
                    "id": "child-2222",
                    "role": "Fallback Agent Name",
                    "type": "sdd-reviewer",
                    "model": "pro",
                    "parent": "root-1",
                    "ts": 1794626050,
                    "transcript": None,
                },
            ])
            self.assertEqual(db.children_of("missing-root"), [])
        finally:
            con.close()

    def test_corrupt_db_and_bad_protobuf_fallback(self):
        cdir = os.path.join(self.tmp, "conversations")
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bad.db"), "wb") as f:
            f.write(b"not a sqlite database at all")
        with open(os.path.join(self.tmp, "conversation_summaries.db"), "wb") as f:
            f.write(b"corrupt summaries bytes")

        con, _ = self._conv_db(self.tmp, "trunc", [(0, b"\x0a\xff\x01truncated")])
        con.close()

        # Fixed-width wire types (wt=1 64-bit, wt=5 32-bit) skipped cleanly before valid field 2.
        buf = (
            pb_varint_raw((1 << 3) | 1) + b"\x00" * 8
            + pb_varint_raw((2 << 3) | 5) + b"\x00" * 4
            + pb_str(3, "ok")
            + b"\x0a\x99"  # truncated tail
        )
        self.assertEqual(pb_fields(buf).get(3), [b"ok"])

        db = AgentDB(self.tmp)
        self.assertEqual(db.model_of("bad", "flash"), "flash")
        self.assertEqual(db.model_of("bad"), "-")
        self.assertEqual(db.model_of("trunc", "pro"), "pro")
        self.assertEqual(db.model_of("trunc"), "-")
        self.assertEqual(db.children_of("root-1"), [])

    def test_special_chars_in_app_dir_uri(self):
        app_dir = os.path.join(self.tmp, "app #1 ? % dir")
        con, _ = self._conv_db(app_dir, "c1", [
            (0, gen_meta_blob("gemini-3.8-flash", "Gemini 3.8 Flash (Low)", "")),
        ])
        con.close()

        db = AgentDB(app_dir)
        self.assertEqual(db.model_of("c1"), "Gemini 3.8 Flash")

    def test_wire_type_mismatch_and_sqlite_error_discard_partial(self):
        # 1.21 encoded as varint (int) instead of bytes, and top-level field 1 encoded as varint
        con1, _ = self._conv_db(self.tmp, "wt_str", [(0, pb_bytes(1, pb_varint(21, 42)))])
        con1.close()
        con2, _ = self._conv_db(self.tmp, "wt_msg", [(0, pb_varint(1, 99))])
        con2.close()

        # Summaries DB with non-numeric bytes in 7.1 (ts) and varint in 17.8.2 (role)
        spath = os.path.join(self.tmp, "conversation_summaries.db")
        con_s = sqlite3.connect(spath)
        con_s.execute(
            "CREATE TABLE conversation_summaries ("
            "conversation_id TEXT PRIMARY KEY, parent_conversation_id TEXT, "
            "agent_name TEXT, raw_summary BLOB)"
        )
        bad_raw = pb_bytes(7, pb_str(1, "not-an-int")) + pb_bytes(17, pb_bytes(8, pb_varint(2, 777)))
        con_s.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
                      ("child-wt", "root-wt", "research", bad_raw))
        con_s.commit()
        con_s.close()

        db = AgentDB(self.tmp)
        self.assertEqual(db.model_of("wt_str", "flash"), "flash")
        self.assertEqual(db.model_of("wt_msg", "pro"), "pro")
        self.assertEqual(db.children_of("root-wt"), [{
            "id": "child-wt",
            "role": None,
            "type": "research",
            "model": None,
            "parent": "root-wt",
            "ts": None,
            "transcript": None,
        }])

        # Non-busy sqlite3.Error in children_of must return [] and cache [] without leaking partial state
        app2 = os.path.join(self.tmp, "app_missing_table")
        os.makedirs(app2, exist_ok=True)
        con_empty = sqlite3.connect(os.path.join(app2, "conversation_summaries.db"))
        con_empty.execute("CREATE TABLE unrelated (x INTEGER)")
        con_empty.commit()
        con_empty.close()
        db2 = AgentDB(app2)
        self.assertEqual(db2.children_of("root-1"), [])
        self.assertEqual(db2._children.get("root-1"), [])


if __name__ == "__main__":
    unittest.main()
