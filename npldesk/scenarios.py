"""Escenarios de análisis (base, optimista, pesimista, estrés) y TIR ajustada al precio."""

from __future__ import annotations

from npldesk.engine import Route, Scenario, expected_value, irr, max_purchase_price

# (factor sobre el precio de venta, factor sobre los plazos). Son supuestos a revisar.
MULTIPLIERS: dict[str, tuple[float, float]] = {
    "base": (1.00, 1.00),
    "optimista": (1.10, 0.90),
    "pesimista": (0.85, 1.30),
    "estres": (0.70, 1.50),
}


def stressed_route(route: Route, price_factor: float, time_factor: float) -> Route:
    return Route(
        f"{route.name}",
        tuple(
            Scenario(
                s.name,
                s.probability,
                s.sale_price * price_factor,
                s.costs,
                s.charges,
                s.debtor_share,
                s.time_years * time_factor,
            )
            for s in route.scenarios
        ),
    )


def analyze(route: Route, rate: float, safety_margin: float) -> dict[str, dict]:
    """Para cada escenario: valor esperado, precio máximo y punto de equilibrio."""
    out = {}
    for label, (pf, tf) in MULTIPLIERS.items():
        r = stressed_route(route, pf, tf)
        ve = expected_value(r.scenarios, rate)
        out[label] = {
            "expected_value": ve,
            "max_price": max_purchase_price(ve, safety_margin),
            # Precio al que la operación no genera ni pierde valor a la tasa exigida.
            "breakeven": ve,
        }
    return out


def irr_for_price(price: float, route: Route) -> float | None:
    """TIR esperada si se compra a `price` y se cobran los flujos probabilizados."""
    flows = [(0.0, -price)] + [
        (s.time_years, s.probability * s.net_value()) for s in route.scenarios
    ]
    return irr(flows)
