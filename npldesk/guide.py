"""Estado del expediente para la pantalla: pasos del proceso, estado de la lista y cobrado.

Son vistas calculadas a partir del estado guardado; no guardan nada nuevo.
"""

from __future__ import annotations

from datetime import date

from npldesk import diligence, risks
from npldesk.workflow import offer_blockers

PRIORITY = {"bloqueado": 0, "listo_para_ofertar": 1, "en_curso": 2, "cerrado": 3}
BLOCKING_ALERTS = ("discrepancia", "nota_caducada")


def eur(value: float) -> str:
    """Importe en formato español: 1234.5 -> '1.235 €'."""
    return f"{value:,.0f}".replace(",", ".") + " €"


def collected(state: dict) -> float:
    return sum(r["amount"] for r in state.get("recoveries", []) if not r.get("anulado"))


def status(state: dict, declared: float | None, today: date | None = None) -> str:
    offer = state.get("offer")
    if offer:
        return "cerrado" if collected(state) >= offer["amount"] else "en_curso"
    return "bloqueado" if offer_blockers(state, declared, today) else "listo_para_ofertar"


def _step(key: str, label: str, target: str, status: str, reason: str, action: str) -> dict:
    return {"key": key, "label": label, "target": target, "status": status,
            "reason": reason, "action": action, "next": False}


def steps(state: dict, declared: float | None, today: date | None = None) -> list[dict]:
    out = [
        _screening(state),
        _diligence(state, declared, today),
        _risks(state),
        _decision(state),
        _offer(state, declared, today),
        _execution(state),
    ]
    # El paso siguiente es el primero que no está completado.
    for s in out:
        if s["status"] != "completado":
            s["next"] = True
            break
    return out


def _screening(state: dict) -> dict:
    args = ("cribado", "Cribado", "sec-cribado")
    scr = state.get("screening")
    if not scr:
        return _step(*args, "pendiente", "Falta el cribado de la finca", "Ver cribado")
    if scr["status"] == "aceptado":
        return _step(*args, "completado", "Cribado aceptado", "Ver cribado")
    motivos = scr.get("reasons") or []
    reason = "Descartado: " + ("; ".join(motivos) if motivos else "sin motivo registrado")
    return _step(*args, "bloqueado", reason, "Ver motivos")


def _diligence(state: dict, declared: float | None, today: date | None) -> dict:
    args = ("diligencia", "Diligencia", "sec-diligencia")
    alerts = diligence.alerts(state, declared, today)
    blocking = [a["detail"] for a in alerts if a["type"] in BLOCKING_ALERTS]
    if blocking:
        return _step(*args, "bloqueado", "; ".join(blocking), "Resolver discrepancias")
    missing = diligence.missing_required(state)
    if missing:
        names = ", ".join(diligence.FIELD_LABELS[f] for f in missing)
        return _step(*args, "pendiente", f"Faltan datos por confirmar: {names}", "Confirmar datos")
    avisos = [a["detail"] for a in alerts]
    reason = "Datos confirmados" + (" · Aviso: " + "; ".join(avisos) if avisos else "")
    return _step(*args, "completado", reason, "Revisar datos")


def _risks(state: dict) -> dict:
    args = ("riesgos", "Riesgos", "sec-riesgos")
    open_ = risks.open_blockers(state)
    if open_:
        extra = f" (y {len(open_) - 1} más)" if len(open_) > 1 else ""
        return _step(*args, "bloqueado",
                     f"Riesgo bloqueante sin aprobar: {open_[0]['description']}{extra}", "Aprobar riesgo")
    items = state.get("risks", [])
    if items:
        return _step(*args, "completado", f"{len(items)} riesgo(s) registrados, sin bloqueos pendientes",
                     "Revisar riesgos")
    return _step(*args, "pendiente", "Sin riesgos registrados", "Registrar riesgos")


def _decision(state: dict) -> dict:
    args = ("decision", "Decisión", "sec-decision")
    d = state.get("decision")
    if d:
        return _step(*args, "completado", f"Vía elegida: {d['chosen']}", "Ver decisión")
    return _step(*args, "pendiente", "Falta elegir la vía", "Registrar decisión")


def _offer(state: dict, declared: float | None, today: date | None) -> dict:
    args = ("oferta", "Oferta", "sec-oferta")
    offer = state.get("offer")
    if offer:
        return _step(*args, "completado", f"Oferta vigente: {eur(offer['amount'])}", "Ver oferta")
    blockers = offer_blockers(state, declared, today)
    if blockers:
        extra = f" (y {len(blockers) - 3} más)" if len(blockers) > 3 else ""
        return _step(*args, "bloqueado", "; ".join(blockers[:3]) + extra, "Resolver bloqueos")
    return _step(*args, "pendiente", "Lista para ofertar", "Registrar oferta")


def _execution(state: dict) -> dict:
    args = ("ejecucion", "Ejecución", "sec-ejecucion")
    offer = state.get("offer")
    if not offer:
        return _step(*args, "bloqueado", "Falta la oferta: no hay cobros que registrar", "Ver ejecución")
    cobrado = collected(state)
    if cobrado >= offer["amount"]:
        return _step(*args, "completado", f"Cobrado íntegramente: {eur(cobrado)}", "Revisar cobros")
    return _step(*args, "pendiente",
                 f"Cobrado {eur(cobrado)} de {eur(offer['amount'])}", "Registrar cobro")
