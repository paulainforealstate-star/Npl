"""Checklist de riesgos por categoría. Un riesgo bloqueante exige aprobación del responsable.

La app marca el riesgo y cita la norma de referencia, pero no concluye sobre
validez jurídica: eso corresponde a un abogado.
"""

from __future__ import annotations

from npldesk.config import DEFAULTS


def add_risk(state: dict, category: str, description: str, level: str,
             norm_ref: str | None = None) -> int:
    if category not in DEFAULTS["risk_categories"]:
        raise ValueError(f"Categoría desconocida '{category}'")
    if level not in DEFAULTS["risk_levels"]:
        raise ValueError(f"Nivel desconocido '{level}'")
    items = state.setdefault("risks", [])
    items.append({
        "category": category,
        "description": description,
        "level": level,
        "norm_ref": norm_ref,
        "approved_by": None,
    })
    return len(items) - 1


def approve(state: dict, index: int, user: str) -> None:
    items = state.get("risks", [])
    if not 0 <= index < len(items):
        raise LookupError("riesgo no encontrado")
    item = items[index]
    if item["level"] != "bloqueante":
        raise ValueError("Solo se aprueban riesgos bloqueantes")
    item["approved_by"] = user


def open_blockers(state: dict) -> list[dict]:
    return [r for r in state.get("risks", [])
            if r["level"] == "bloqueante" and not r["approved_by"]]
