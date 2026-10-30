"""Due diligence documental.

Las extracciones se hacen con reglas deterministas sobre texto. El modelo de
lenguaje y el OCR de la especificación quedan fuera de esta versión: el
punto de entrada es el mismo, así que pueden sustituir a `extract` sin
tocar el resto. Cada dato debe confirmarlo una persona antes de usarse.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from npldesk.config import DEFAULTS

PATTERNS = {
    "titular": re.compile(r"^\s*Titular:\s*(.+)$", re.M | re.I),
    "saldo_pendiente": re.compile(r"^\s*Saldo pendiente:\s*([\d.,]+)\s*€", re.M | re.I),
    "fecha_nota": re.compile(r"^\s*Fecha de emisi[óo]n:\s*(\d{2}/\d{2}/\d{4})", re.M | re.I),
    "finca": re.compile(r"^\s*Finca:\s*(\S+)", re.M | re.I),
}


def parse_amount(text: str) -> float:
    """'123.456,78' -> 123456.78"""
    return float(text.replace(".", "").replace(",", "."))


def extract(document: str, text: str) -> list[dict]:
    found = []
    for field, rx in PATTERNS.items():
        m = rx.search(text)
        if not m:
            continue
        raw = m.group(1).strip()
        value = parse_amount(raw) if field == "saldo_pendiente" else raw
        found.append({
            "field": field,
            "value": value,
            "document": document,
            "source_fragment": m.group(0).strip(),
        })
    return found


def _block(state: dict) -> dict:
    return state.setdefault("diligence", {"candidates": {}, "confirmed": {}})


def add_candidates(state: dict, items: list[dict]) -> None:
    d = _block(state)
    for it in items:
        d["candidates"].setdefault(it["field"], []).append({
            "value": it["value"],
            "document": it["document"],
            "source_fragment": it["source_fragment"],
        })


def confirm(state: dict, field: str, value, user: str, today: date) -> None:
    d = _block(state)
    candidates = d["candidates"].get(field)
    if not candidates:
        raise ValueError(f"No hay datos extraídos para '{field}'")
    if not any(str(c["value"]) == str(value) for c in candidates):
        raise ValueError(f"El valor confirmado no coincide con ninguna extracción de '{field}'")
    d["confirmed"][field] = {"value": value, "by": user, "date": today.isoformat()}


def missing_required(state: dict) -> list[str]:
    confirmed = state.get("diligence", {}).get("confirmed", {})
    return [f for f in DEFAULTS["required_diligence"] if f not in confirmed]


def alerts(state: dict, declared_saldo: float | None = None,
           today: date | None = None) -> list[dict]:
    today = today or date.today()
    d = state.get("diligence", {})
    out: list[dict] = []

    for field, items in d.get("candidates", {}).items():
        values = {str(i["value"]) for i in items}
        if len(values) > 1:
            out.append({
                "type": "discrepancia",
                "field": field,
                "detail": f"Los documentos dan valores distintos para '{field}': {', '.join(sorted(values))}",
            })

    fecha = d.get("candidates", {}).get("fecha_nota")
    if fecha:
        emitida = datetime.strptime(fecha[0]["value"], "%d/%m/%Y").date()
        age = (today - emitida).days
        if age > DEFAULTS["note_max_age_days"]:
            out.append({
                "type": "nota_caducada",
                "field": "fecha_nota",
                "detail": f"La nota simple tiene {age} días; el máximo es {DEFAULTS['note_max_age_days']}",
            })

    confirmed_saldo = d.get("confirmed", {}).get("saldo_pendiente")
    if confirmed_saldo is not None and declared_saldo:
        diff = abs(float(confirmed_saldo["value"]) - declared_saldo) / declared_saldo
        if diff > 0.01:
            out.append({
                "type": "saldo_no_cuadra",
                "field": "saldo_pendiente",
                "detail": f"El saldo confirmado difiere un {diff:.1%} del declarado",
            })
    return out
