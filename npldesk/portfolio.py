"""Cartera consolidada y KPIs: capital comprometido, monetizado, pendiente, TIR y tiempos."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from npldesk.engine import irr


def _days_to_years(d0: date, d: date) -> float:
    return (d - d0).days / 365.0


def kpis(cases: list[dict], today: date) -> dict:
    total = len(cases)
    descartados = [c for c in cases
                   if c["state"].get("screening", {}).get("status") == "descartado"]
    ofertas = [c for c in cases if c["state"].get("offer")]

    committed = sum(c["state"]["offer"]["amount"] for c in ofertas)
    recoveries = [(c, r) for c in cases for r in c["state"].get("recoveries", [])
                  if not r.get("anulado")]
    monetized = sum(r["amount"] for _, r in recoveries)

    realized_irr = None
    if ofertas:
        origin = min(date.fromisoformat(c["state"]["offer"]["date"]) for c in ofertas)
        flows = [(_days_to_years(origin, date.fromisoformat(c["state"]["offer"]["date"])),
                  -c["state"]["offer"]["amount"]) for c in ofertas]
        flows += [(_days_to_years(origin, date.fromisoformat(r["date"])), r["amount"])
                  for _, r in recoveries]
        realized_irr = irr(flows)

    planned = [c["state"]["offer"]["planned_irr"] for c in ofertas
               if c["state"]["offer"].get("planned_irr") is not None]

    durations: dict[str, list[int]] = defaultdict(list)
    for c in cases:
        hist = c["state"].get("phase_history", [])
        for a, b in zip(hist, hist[1:]):
            durations[a["phase"]].append(
                (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days)

    return {
        "expedientes": total,
        "descartados": len(descartados),
        "tasa_descarte": round(len(descartados) / total, 4) if total else 0.0,
        "ofertas": len(ofertas),
        "comprometido": committed,
        "monetizado": monetized,
        "pendiente": committed - monetized,
        "tir_realizada": realized_irr,
        "tir_prevista_media": sum(planned) / len(planned) if planned else None,
        "dias_medios_por_fase": {p: sum(v) / len(v) for p, v in durations.items()},
        "fecha": today.isoformat(),
    }
