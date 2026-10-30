"""Persistencia en SQLite (biblioteca estándar).

Novedades de la v0.5:
- Usuarios con contraseña hasheada y sesiones guardadas solo como hash.
- Control de versiones por expediente (`version`), para detectar ediciones concurrentes.
- Auditoría encadenada por hash e inmutable (triggers que impiden UPDATE y DELETE).

El esquema sigue siendo sencillo y portable para migrar a PostgreSQL más adelante.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64
IMMUTABLE_MSG = "El registro de auditoría es inmutable"
AUDIT_FIELDS = ("ts", "user", "user_id", "action", "entity", "entity_id",
                "detail_json", "ip", "request_id")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phase TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
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
    user_id INTEGER,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    ip TEXT,
    request_id TEXT,
    prev_hash TEXT,
    hash TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'El registro de auditoría es inmutable'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'El registro de auditoría es inmutable'); END;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
"""

_USER_FIELDS = {"password_hash", "active", "failed_attempts", "locked_until"}


class StaleVersion(RuntimeError):
    """El expediente cambió desde que se leyó (control de versiones)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(prev_hash: str, row: dict) -> str:
    payload = json.dumps({"prev": prev_hash, **row}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: str | Path = "data/npldesk.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        """Añade a bases de datos de la v0.4 las columnas nuevas."""
        def columns(table: str) -> set[str]:
            return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

        if "version" not in columns("cases"):
            self.conn.execute("ALTER TABLE cases ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        for name, decl in [("user_id", "INTEGER"), ("ip", "TEXT"), ("request_id", "TEXT"),
                           ("prev_hash", "TEXT"), ("hash", "TEXT")]:
            if name not in columns("audit"):
                self.conn.execute(f"ALTER TABLE audit ADD COLUMN {name} {decl}")

    def ping(self) -> None:
        self.conn.execute("SELECT 1").fetchone()

    def backup_to(self, dest: str | Path) -> None:
        if self.path == ":memory:":
            raise ValueError("Una base de datos en memoria no se puede copiar a fichero")
        if Path(dest).exists():
            raise ValueError(f"El destino ya existe: {dest}")
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()

    # ---- usuarios y sesiones -----------------------------------------------

    def create_user(self, username: str, full_name: str, email: str | None,
                    role: str, password_hash: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO users (username, full_name, email, role, password_hash, active,"
            " failed_attempts, locked_until, created_at) VALUES (?, ?, ?, ?, ?, 1, 0, NULL, ?)",
            (username, full_name, email, role, password_hash, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_user(self, user_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, username: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, username, full_name, email, role, active, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_users(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def update_user(self, user_id: int, **fields) -> None:
        unknown = set(fields) - _USER_FIELDS
        if unknown:
            raise ValueError(f"Campos de usuario no permitidos: {', '.join(sorted(unknown))}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))
        self.conn.commit()

    def create_session(self, token_hash: str, user_id: int, created_at: str, expires_at: str) -> None:
        self.conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, revoked)"
            " VALUES (?, ?, ?, ?, 0)",
            (token_hash, user_id, created_at, expires_at),
        )
        self.conn.commit()

    def get_session(self, token_hash: str) -> dict | None:
        row = self.conn.execute(
            "SELECT s.user_id, s.expires_at, s.revoked, u.username, u.full_name, u.role, u.active"
            " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token_hash: str) -> None:
        self.conn.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (token_hash,))
        self.conn.commit()

    def revoke_user_sessions(self, user_id: int) -> None:
        self.conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    # ---- expedientes -------------------------------------------------------

    def create_case(self, name: str, phase: str, input_data: dict,
                    result: dict | None, state: dict) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO cases (name, phase, input_json, result_json, state_json, version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
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
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def all_cases(self) -> list[dict]:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM cases").fetchall()]
        return [self.get_case(i) for i in ids]

    def save_case(self, case_id: int, *, expected_version: int, phase: str | None = None,
                  result: dict | None = None, state: dict | None = None) -> None:
        """Guarda solo si la versión en base de datos sigue siendo la que se leyó."""
        current = self.get_case(case_id)
        if current is None:
            raise LookupError("expediente no encontrado")
        new_phase = phase or current["phase"]
        new_result = result if result is not None else current["result"]
        new_state = state if state is not None else current["state"]
        cur = self.conn.execute(
            "UPDATE cases SET phase = ?, result_json = ?, state_json = ?, updated_at = ?, version = version + 1"
            " WHERE id = ? AND version = ?",
            (new_phase, json.dumps(new_result), json.dumps(new_state), _now(), case_id, expected_version),
        )
        if cur.rowcount == 0:
            self.conn.rollback()
            raise StaleVersion("El expediente se modificó en otra sesión")
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

    def add_audit(self, user: str, action: str, entity: str, entity_id: str | None,
                  detail: dict | None = None, *, user_id: int | None = None,
                  ip: str | None = None, request_id: str | None = None) -> None:
        last = self.conn.execute(
            "SELECT hash FROM audit WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev = last["hash"] if last else GENESIS
        row = {
            "ts": _now(), "user": user, "user_id": user_id, "action": action,
            "entity": entity, "entity_id": entity_id,
            "detail_json": json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            "ip": ip, "request_id": request_id,
        }
        self.conn.execute(
            "INSERT INTO audit (ts, user, user_id, action, entity, entity_id, detail_json, ip,"
            " request_id, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["ts"], row["user"], row["user_id"], row["action"], row["entity"], row["entity_id"],
             row["detail_json"], row["ip"], row["request_id"], prev, _digest(prev, row)),
        )
        self.conn.commit()

    def list_audit(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in rows]

    def verify_audit(self) -> dict:
        """Recalcula la cadena. Las filas anteriores a la v0.5 (sin hash) no se incluyen."""
        prev = GENESIS
        checked = 0
        rows = self.conn.execute("SELECT * FROM audit WHERE hash IS NOT NULL ORDER BY id").fetchall()
        for r in rows:
            row = {k: r[k] for k in AUDIT_FIELDS}
            if r["prev_hash"] != prev or _digest(prev, row) != r["hash"]:
                return {"ok": False, "primera_fila_alterada": r["id"], "comprobadas": checked}
            prev = r["hash"]
            checked += 1
        return {"ok": True, "comprobadas": checked}
