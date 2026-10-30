"""Tareas con dependencias y calendario de plazos procesales.

Los plazos están en plazos.json, una tabla versionada: cada plazo lleva su norma,
su fuente, la fecha de revisión y quién lo validó. Un plazo sin `validado_por`
aparece marcado como "sin validar". Hoy todos son EJEMPLOS, no plazos legales.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

PLAZOS_PATH = Path(__file__).with_name("plazos.json")


def load_plazos(path: Path = PLAZOS_PATH) -> dict[str, list[dict]]:
    return json.loads(path.read_text(encoding="utf-8"))["plazos"]


def ready_tasks(tasks: list[dict]) -> list[dict]:
    """Tareas pendientes cuyas dependencias están completadas."""
    done = {t["id"] for t in tasks if t["status"] == "completada"}
    return [t for t in tasks
            if t["status"] == "pendiente" and all(d in done for d in t["depends_on"])]


def overdue_tasks(tasks: list[dict], today: date) -> list[dict]:
    out = []
    for t in tasks:
        if t["status"] != "completada" and t["due_date"]:
            if date.fromisoformat(t["due_date"]) < today:
                out.append(t)
    return out


def deadline_calendar(phase: str, start: date, plazos: dict | None = None) -> list[dict]:
    table = plazos if plazos is not None else load_plazos()
    return [
        {
            "title": p["titulo"],
            "due_date": (start + timedelta(days=p["dias"])).isoformat(),
            "norma": p.get("norma"),
            "fuente": p.get("fuente"),
            "revisado": p.get("revisado"),
            "validado": bool(p.get("validado_por")),
            "validado_por": p.get("validado_por"),
        }
        for p in table.get(phase, [])
    ]
