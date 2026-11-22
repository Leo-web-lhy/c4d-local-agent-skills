"""Long-term memory for the local agent: SQLite episodes + facts + retrieval.

Three layers
------------
1. episodes  - raw turns (task, thought, tool observation, answer) with tags
2. facts     - consolidated short statements extracted from episodes
3. retrieval - FTS5 BM25 ranking with a recency boost, LIKE fallback

The store survives process restarts, which is what makes cross-session recall
testable (see demo/session-a, demo/session-b).
"""
import os
import re
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    tags TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source INTEGER,
    confidence REAL DEFAULT 0.8
);
CREATE INDEX IF NOT EXISTS idx_ep_session ON episodes(session);
CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
"""

STOP = set("the a an of to and or is are was were in on for with what how why do does did "
           "i you it this that be been being at by from as if then than 的 了 是 在 我 你 他 "
           "她 它 和 与 就 也 都 吗 呢 请 帮 帮我 这 那 个".split())


def _tokens(text):
    return [t for t in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", (text or "").lower()) if t and t not in STOP]


def _fts_query(text):
    toks = _tokens(text)[:12]
    if not toks:
        return ""
    return " OR ".join('"%s"' % t.replace('"', "") for t in toks)


class Memory:
    def __init__(self, path="data/memory.sqlite3"):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.fts = self._init_fts()
        self.conn.commit()

    # ------------------------------------------------------------------ fts
    def _init_fts(self):
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS ep_fts USING fts5("
                "text, tags, content='episodes', content_rowid='id', tokenize='unicode61')"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS ep_ai AFTER INSERT ON episodes BEGIN "
                "INSERT INTO ep_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS ep_ad AFTER DELETE ON episodes BEGIN "
                "INSERT INTO ep_fts(ep_fts, rowid, text, tags) VALUES('delete', old.id, old.text, old.tags); END"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS ep_au AFTER UPDATE ON episodes BEGIN "
                "INSERT INTO ep_fts(ep_fts, rowid, text, tags) VALUES('delete', old.id, old.text, old.tags); "
                "INSERT INTO ep_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END"
            )
            self.conn.commit()
            return True
        except sqlite3.OperationalError:
            self.conn.rollback()
            return False

    # ------------------------------------------------------------- writing
    def add_episode(self, session, kind, text, tags=""):
        cur = self.conn.execute(
            "INSERT INTO episodes(ts, session, kind, text, tags) VALUES(?,?,?,?,?)",
            (time.time(), session, kind, text, tags),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_fact(self, key, value, source=None, confidence=0.8):
        cur = self.conn.execute(
            "INSERT INTO facts(ts, key, value, source, confidence) VALUES(?,?,?,?,?)",
            (time.time(), key, value, source, confidence),
        )
        self.conn.commit()
        return cur.lastrowid

    # ----------------------------------------------------------- retrieval
    def search(self, query, k=4, recency_tau_s=86400.0):
        q = _fts_query(query)
        rows = []
        if self.fts and q:
            try:
                rows = self.conn.execute(
                    "SELECT e.id, e.ts, e.session, e.kind, e.text, e.tags, bm25(ep_fts) AS score "
                    "FROM ep_fts JOIN episodes e ON e.id = ep_fts.rowid "
                    "WHERE ep_fts MATCH ? ORDER BY score LIMIT 40", (q,)
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            toks = _tokens(query)[:8]
            if toks:
                like = " OR ".join(["text LIKE ? OR tags LIKE ?"] * len(toks))
                params = []
                for t in toks:
                    params += ["%" + t + "%", "%" + t + "%"]
                rows = self.conn.execute(
                    "SELECT id, ts, session, kind, text, tags, 1.0 AS score FROM episodes "
                    "WHERE " + like + " ORDER BY ts DESC LIMIT 40", params
                ).fetchall()
        now = time.time()
        scored = []
        for r in rows:
            age = max(0.0, now - r["ts"])
            recency = 0.5 ** (age / recency_tau_s)
            base = 1.0 / (1.0 + abs(float(r["score"])))
            scored.append((base * (0.7 + 0.3 * recency), dict(r)))
        scored.sort(key=lambda x: (-x[0], -x[1]["ts"]))
        out = []
        for s, r in scored[:k]:
            r["relevance"] = round(s, 4)
            r.pop("score", None)
            out.append(r)
        return out

    def facts_for(self, query, k=4):
        toks = set(_tokens(query))
        rows = self.conn.execute("SELECT * FROM facts ORDER BY ts DESC LIMIT 200").fetchall()
        hits = []
        for r in rows:
            hay = set(_tokens(r["key"] + " " + r["value"]))
            inter = len(hay & toks)
            if inter:
                hits.append((inter * float(r["confidence"]), dict(r)))
        hits.sort(key=lambda x: -x[0])
        return [h[1] for h in hits[:k]]

    # --------------------------------------------------------------- stats
    def stats(self):
        ep = self.conn.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
        fa = self.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        se = self.conn.execute("SELECT COUNT(DISTINCT session) c FROM episodes").fetchone()["c"]
        return {"episodes": ep, "facts": fa, "sessions": se, "fts": self.fts,
                "size_kb": round(os.path.getsize(self.path) / 1024.0, 1) if os.path.exists(self.path) else 0}

    def close(self):
        self.conn.close()
