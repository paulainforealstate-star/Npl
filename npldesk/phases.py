"""Vías de resolución disponibles en cada fase procesal.

La decisión se toma solo entre las vías que son viables en la fase actual.
Los costes ya gastados no se pasan al motor: solo entran flujos futuros.
"""

from __future__ import annotations

PHASES: dict[str, tuple[str, ...]] = {
    "precontenciosa": (
        "negociación",
        "dación en pago",
        "refinanciación",
        "venta ordenada",
    ),
    "judicial_sin_adjudicar": (
        "dación en pago",
        "venta ordenada",
        "continuación judicial",
    ),
    "judicial_adjudicada": (
        "venta ordenada",
        "continuación judicial",
    ),
}


def allowed_routes(phase: str) -> tuple[str, ...]:
    if phase not in PHASES:
        raise ValueError(
            f"Fase desconocida '{phase}'. Válidas: {', '.join(PHASES)}"
        )
    return PHASES[phase]


def check_routes_for_phase(phase: str, route_names: list[str]) -> None:
    allowed = set(allowed_routes(phase))
    not_allowed = [r for r in route_names if r not in allowed]
    if not_allowed:
        raise ValueError(
            f"En la fase '{phase}' no son viables: {', '.join(not_allowed)}"
        )
