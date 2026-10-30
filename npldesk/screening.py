"""Cribado automático: LTV, ubicación, importe y ocupación. Cada descarte lleva su motivo."""

from __future__ import annotations

from npldesk import validation as V
from npldesk.config import DEFAULTS


def screen(data: dict, cfg: dict | None = None) -> dict:
    c = cfg or DEFAULTS["screening"]
    loan = V.amount(data["loan"], "loan")
    appraisal = V.amount(data["appraisal"], "appraisal", positive=True)
    amount = V.amount(data.get("amount", loan), "amount")

    ltv = loan / appraisal
    reasons: list[str] = []
    if ltv > c["ltv_max"]:
        reasons.append(f"LTV {ltv:.1%} supera el máximo de {c['ltv_max']:.0%}")
    if data.get("province") not in c["provinces"]:
        reasons.append(f"Ubicación fuera de criterio ({data.get('province')})")
    if not c["amount_min"] <= amount <= c["amount_max"]:
        reasons.append(f"Importe {amount:,.0f} € fuera del rango permitido")
    if data.get("occupied") and not c["allow_occupied"]:
        reasons.append("Vivienda ocupada")

    return {
        "status": "descartado" if reasons else "aceptado",
        "reasons": reasons,
        "ltv": round(ltv, 4),
    }
