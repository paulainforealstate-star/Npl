"""Tareas con dependencias y calendario de plazos procesales.

Los plazos de PLAZOS_EJEMPLO son EJEMPLOS para probar el calendario. No son
plazos legales: deben sustituirse por los vigentes, validados por un abogado.
"""

from __future__ import annotations

from datetime import date, timedelta

PLAZOS_EJEMPLO: dict[str, list[tuple[str, int]]] = {
    "judicial_sin_adjudicar": [
        ("Revisar la nota simple (EJEMPLO)", 7),
        ("Comunicación al deudor (EJEMPLO)", 30),
    ],
    "judicial_adjudicada": [
        ("Revisar cargas posteriores (EJEMPLO)", 14),
    ],
}


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


def deadline_calendar(phase: str, start: date) -> list[dict]:
    return [
        {"title": title, "due_date": (start + timedelta(days=days)).isoformat()}
        for title, days in PLAZOS_EJEMPLO.get(phase, [])
    ]
