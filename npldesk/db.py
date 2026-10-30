"""Persistencia en SQLite (biblioteca estándar).

El esquema es sencillo y portable para migrar a PostgreSQL más adelante.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phase TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    owner TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'pendiente',
    depends_on_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    photo_ref TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = "data/npldesk.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- expedientes -------------------------------------------------------

    def create_case(self, name: str, phase: str, input_data: dict,
                    result: dict | None, state: dict) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO cases (name, phase, input_json, result_json, state_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, phase, json.dumps(input_data), json.dumps(result), json.dumps(state), now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_cases(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, phase, created_at, updated_at FROM cases ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_case(self, case_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "phase": row["phase"],
            "input": json.loads(row["input_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def all_cases(self) -> list[dict]:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM cases").fetchall()]
        return [self.get_case(i) for i in ids]

    def save_case(self, case_id: int, *, phase: str | None = None,
                  result: dict | None = None, state: dict | None = None) -> None:
        current = self.get_case(case_id)
        if current is None:
            raise LookupError("expediente no encontrado")
        new_phase = phase or current["phase"]
        new_result = result if result is not None else current["result"]
        new_state = state if state is not None else current["state"]
        self.conn.execute(
            "UPDATE cases SET phase = ?, result_json = ?, state_json = ?, updated_at = ? WHERE id = ?",
            (new_phase, json.dumps(new_result), json.dumps(new_state), _now(), case_id),
        )
        self.conn.commit()

    def delete_case(self, case_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- tareas ------------------------------------------------------------

    def add_task(self, case_id: int, title: str, owner: str | None,
                 due_date: str | None, depends_on: list[int]) -> int:
        cur = self.conn.execute(
            "INSERT INTO tasks (case_id, title, owner, due_date, status, depends_on_json)"
            " VALUES (?, ?, ?, ?, 'pendiente', ?)",
            (case_id, title, owner, due_date, json.dumps(depends_on)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_tasks(self, case_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
        return [
            {**dict(r), "depends_on": json.loads(r["depends_on_json"])} for r in rows
        ]

    def set_task_status(self, task_id: int, case_id: int, status: str) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ? AND case_id = ?", (status, task_id, case_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---- visitas -----------------------------------------------------------

    def add_visit(self, case_id: int, date: str, lat: float, lon: float,
                  photo_ref: str | None, notes: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO visits (case_id, date, lat, lon, photo_ref, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (case_id, date, lat, lon, photo_ref, notes),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_visits(self, case_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM visits WHERE case_id = ? ORDER BY date, id", (case_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- auditoría ---------------------------------------------------------

    def add_audit(self, user: str, action: str, entity: str,
                  entity_id: str | None, detail: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO audit (ts, user, action, entity, entity_id, detail_json) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), user, action, entity, entity_id, json.dumps(detail or {}, ensure_ascii=False)),
        )
        self.conn.commit()

    def list_audit(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in rows]
