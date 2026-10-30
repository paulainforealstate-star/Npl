"""Reglas del flujo: qué bloquea una oferta, límites de aprobación y cambios de vía."""

from __future__ import annotations

from datetime import date

from npldesk import diligence, risks
from npldesk.config import DEFAULTS


class GateError(ValueError):
    """La operación no puede avanzar por una regla de negocio (HTTP 409)."""


def offer_blockers(state: dict, declared_saldo: float | None,
                   today: date | None = None) -> list[str]:
    blockers: list[str] = []
    scr = state.get("screening")
    if not scr or scr["status"] != "aceptado":
        blockers.append("El cribado no está aceptado")
    missing = diligence.missing_required(state)
    if missing:
        blockers.append("Faltan datos de diligencia confirmados: " + ", ".join(missing))
    for al in diligence.alerts(state, declared_saldo, today):
        if al["type"] in ("nota_caducada", "discrepancia"):
            blockers.append(f"Alerta sin resolver ({al['type']}): {al['detail']}")
    for r in risks.open_blockers(state):
        blockers.append(f"Riesgo bloqueante sin aprobar: {r['description']}")
    if not state.get("decision"):
        blockers.append("No hay decisión de vía registrada")
    return blockers


def check_offer_limit(role: str, amount: float) -> None:
    if role == "admin":
        return
    limit = DEFAULTS["approval_limits"].get(role)
    if limit is None or amount > limit:
        raise GateError(f"El importe supera tu límite de aprobación ({limit:,.0f} €)"
                        if limit else "Tu rol no puede aprobar ofertas")


def register_decision(state: dict, recommended: str, chosen: str,
                      justification: str, user: str, today: date) -> None:
    if chosen != recommended and not justification.strip():
        raise ValueError(
            "Si eliges una vía distinta a la recomendada, debes justificarlo"
        )
    state["decision"] = {
        "recommended": recommended,
        "chosen": chosen,
        "justification": justification.strip(),
        "by": user,
        "date": today.isoformat(),
    }


def change_phase(state: dict, phase: str, today: date) -> None:
    history = state.setdefault("phase_history", [])
    history.append({"phase": phase, "date": today.isoformat()})
