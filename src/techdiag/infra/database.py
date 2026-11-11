"""Historico local em SQLite."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..core.models import CheckResult, Session, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT NOT NULL, ticket TEXT NOT NULL,
  technician TEXT, machine TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS check_results(
  session_id INTEGER, key TEXT, payload TEXT, updated_at TEXT, PRIMARY KEY(session_id, key));
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, ts TEXT, action TEXT,
  detail TEXT, success INTEGER, resolves TEXT);
"""


class Database:
    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.executescript(SCHEMA)
            self._con.commit()

    def close(self):
        with self._lock:
            self._con.close()

    def create_session(self, s: Session) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO sessions(client,ticket,technician,machine,created_at) VALUES(?,?,?,?,?)",
                (s.client, s.ticket, s.technician, s.machine, s.created_at))
            self._con.commit()
            s.id = cur.lastrowid
            return s.id

    def get_session(self, sid: int) -> Session | None:
        with self._lock:
            r = self._con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            return None
        return Session(r["client"], r["ticket"], r["technician"] or "", r["machine"] or "",
                       r["created_at"], r["id"])

    def list_sessions(self) -> list[Session]:
        with self._lock:
            rows = self._con.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
        return [Session(r["client"], r["ticket"], r["technician"] or "", r["machine"] or "",
                        r["created_at"], r["id"]) for r in rows]

    def save_result(self, sid: int, res: CheckResult) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO check_results(session_id,key,payload,updated_at) VALUES(?,?,?,?)",
                (sid, res.key, json.dumps(res.to_dict(), ensure_ascii=False, default=str), now_iso()))
            self._con.commit()

    def load_results(self, sid: int) -> dict[str, CheckResult]:
        with self._lock:
            rows = self._con.execute(
                "SELECT key,payload FROM check_results WHERE session_id=?", (sid,)).fetchall()
        return {r["key"]: CheckResult.from_dict(json.loads(r["payload"])) for r in rows}

    def log_action(self, sid: int, action: str, detail: str, success: bool, resolves: str = "") -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO actions(session_id,ts,action,detail,success,resolves) VALUES(?,?,?,?,?,?)",
                (sid, now_iso(), action, detail, int(success), resolves))
            self._con.commit()

    def list_actions(self, sid: int) -> list[dict]:
        with self._lock:
            rows = self._con.execute("SELECT * FROM actions WHERE session_id=? ORDER BY id", (sid,)).fetchall()
        return [dict(r) for r in rows]
