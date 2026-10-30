"""Due diligence documental.

Las extracciones se hacen con reglas deterministas sobre texto. El modelo de
lenguaje y el OCR de la especificación quedan fuera de esta versión: el
punto de entrada es el mismo, así que pueden sustituir a `extract` sin
tocar el resto. Cada dato debe confirmarlo una persona antes de usarse.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from npldesk import validation as V
from npldesk.config import DEFAULTS

# Nombres que ve la persona para cada dato (en mensajes y en la pantalla).
FIELD_LABELS = {
    "titular": "titular",
    "saldo_pendiente": "saldo pendiente",
    "fecha_nota": "fecha de emisión",
    "finca": "finca",
}

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
        if field == "fecha_nota":
            # Se valida aquí, antes de guardar nada: una fecha imposible no debe
            # quedar en el expediente y romper después las alertas.
            try:
                datetime.strptime(raw, "%d/%m/%Y")
            except ValueError:
                raise ValueError(f"Fecha de emisión no válida en '{document}': {raw}")
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


def _same(a, b) -> bool:
    """Compara valores de extracción. Un importe llega como 150000 (JSON) y se guardó como 150000.0."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return float(a) == float(b)
    return str(a).strip() == str(b).strip()


def confirm(state: dict, field: str, value, user: str, today: date) -> None:
    d = _block(state)
    candidates = d["candidates"].get(field)
    if not candidates:
        raise ValueError(f"No hay datos extraídos para '{field}'")
    match = next((c for c in candidates if _same(c["value"], value)), None)
    if match is None:
        raise ValueError(f"El valor confirmado no coincide con ninguna extracción de '{field}'")
    # `candidates_seen` marca hasta qué extracción revisó la persona. Así, una
    # discrepancia se resuelve al confirmar, y solo vuelve a aparecer si llega
    # un documento nuevo con otro valor. Se guarda el valor tal como lo extrajo el documento.
    d["confirmed"][field] = {
        "value": match["value"],
        "by": user,
        "date": today.isoformat(),
        "candidates_seen": len(candidates),
    }


def missing_required(state: dict) -> list[str]:
    confirmed = state.get("diligence", {}).get("confirmed", {})
    return [f for f in DEFAULTS["required_diligence"] if f not in confirmed]


def alerts(state: dict, declared_saldo: float | None = None,
           today: date | None = None) -> list[dict]:
    today = today or date.today()
    d = state.get("diligence", {})
    out: list[dict] = []

    confirmed = d.get("confirmed", {})
    for field, items in d.get("candidates", {}).items():
        if field not in confirmed:
            values = {str(i["value"]) for i in items}
            if len(values) > 1:
                out.append({
                    "type": "discrepancia",
                    "field": field,
                    "detail": f"Los documentos dan valores distintos para '{field}': {', '.join(sorted(values))}",
                })
            continue
        # Ya confirmado: solo alerta si llegó después un documento con otro valor.
        seen = confirmed[field].get("candidates_seen", 0)
        newer = [str(i["value"]) for i in items[seen:]
                 if str(i["value"]) != str(confirmed[field]["value"])]
        if newer:
            out.append({
                "type": "discrepancia",
                "field": field,
                "detail": f"Un documento posterior da '{newer[-1]}' para '{field}', distinto del confirmado; hay que volver a confirmar",
            })

    # La nota que cuenta es la confirmada; si no hay confirmación, la más reciente extraída.
    if "fecha_nota" in confirmed:
        fecha_value = confirmed["fecha_nota"]["value"]
    else:
        items = d.get("candidates", {}).get("fecha_nota")
        fecha_value = items[-1]["value"] if items else None
    if fecha_value:
        emitida = datetime.strptime(str(fecha_value), "%d/%m/%Y").date()
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


MANUAL_DOCUMENT = "corrección manual"


def confirm_manual(state: dict, field: str, value, user: str, today: date) -> None:
    """Corrección a mano cuando la extracción falla: el valor lo escribe una persona y queda como fuente propia."""
    if field not in DEFAULTS["required_diligence"]:
        raise ValueError(f"Campo desconocido '{field}'")
    if field == "saldo_pendiente":
        value = V.amount(value, "saldo_pendiente", positive=True)
    else:
        value = str(value).strip()
        if not value or len(value) > 200:
            raise ValueError(f"El valor de '{field}' no puede estar vacío ni superar 200 caracteres")
        if field == "fecha_nota":
            try:
                datetime.strptime(value, "%d/%m/%Y")
            except ValueError:
                raise ValueError("La fecha de emisión debe tener formato DD/MM/AAAA")
    d = _block(state)
    d["candidates"].setdefault(field, []).append(
        {"value": value, "document": MANUAL_DOCUMENT, "source_fragment": None})
    confirm(state, field, value, user, today)
