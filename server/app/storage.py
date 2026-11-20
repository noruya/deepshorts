import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("SENARIO_DB", os.path.join(os.path.dirname(__file__), "..", "data.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  speaker TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'normal',
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  writer_id TEXT NOT NULL,
  content TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  speaker TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  ts REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def add_message(self, project_id: str, speaker: str, role: str, text: str, status: str = "normal") -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO messages(project_id,speaker,role,text,status,ts) VALUES(?,?,?,?,?,?)",
                (project_id, speaker, role, text, status, time.time()),
            )
            self.db.commit()
            return cur.lastrowid

    def get_messages(self, project_id: str, limit: int = 500) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,speaker,role,text,status,ts FROM messages WHERE project_id=? ORDER BY id LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def approve_message(self, project_id: str, msg_id: int, key: str = "decision") -> dict | None:
        with self.lock:
            row = self.db.execute(
                "SELECT id,speaker,text,status FROM messages WHERE id=? AND project_id=?", (msg_id, project_id)
            ).fetchone()
            if not row or row["status"] == "approved":
                return None
            self.db.execute("UPDATE messages SET status='approved' WHERE id=?", (msg_id,))
            self.db.execute(
                "INSERT INTO settings(project_id,key,value,ts) VALUES(?,?,?,?)",
                (project_id, key, row["text"], time.time()),
            )
            self.db.commit()
        return {"id": msg_id, "key": key, "value": row["text"]}

    def get_settings(self, project_id: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT key,value,ts FROM settings WHERE project_id=? ORDER BY id", (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def add_usage(self, project_id: str, speaker: str, model: str, usage: dict) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO usage(project_id,speaker,model,prompt_tokens,completion_tokens,ts) VALUES(?,?,?,?,?,?)",
                (
                    project_id,
                    speaker,
                    model,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    time.time(),
                ),
            )
            self.db.commit()

    def get_usage(self, project_id: str) -> dict:
        with self.lock:
            row = self.db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(prompt_tokens),0) p, COALESCE(SUM(completion_tokens),0) c "
                "FROM usage WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return {"calls": row["n"], "prompt_tokens": row["p"], "completion_tokens": row["c"]}

    def save_memory(self, project_id: str, writer_id: str, content: str) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO memories(project_id,writer_id,content,ts) VALUES(?,?,?,?)",
                (project_id, writer_id, content, time.time()),
            )
            self.db.commit()

    def get_memories(self, project_id: str, writer_id: str, limit: int = 5) -> list[str]:
        with self.lock:
            rows = self.db.execute(
                "SELECT content FROM memories WHERE project_id=? AND writer_id=? ORDER BY id DESC LIMIT ?",
                (project_id, writer_id, limit),
            ).fetchall()
        return [r["content"] for r in rows]
