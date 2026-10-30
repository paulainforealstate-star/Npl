"""Motor de cálculo de NPLDesk.

Fórmulas de referencia (especificación):
  VN = P_venta - costes - cargas - participación deudora
  VE = sum_s p_s * VN_s / (1 + r)^t_s
  P_max = VE * (1 - m)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Scenario:
    """Un escenario de recuperación (base, optimista, pesimista, estrés)."""

    name: str
    probability: float      # p_s, entre 0 y 1
    sale_price: float       # P_venta
    costs: float            # costes de la vía
    charges: float          # cargas registrales a asumir
    debtor_share: float     # participación de la deudora (0 si no hay)
    time_years: float       # t_s, plazo hasta el cobro

    def net_value(self) -> float:
        """VN = P_venta - costes - cargas - participación deudora."""
        return self.sale_price - self.costs - self.charges - self.debtor_share


@dataclass(frozen=True)
class Route:
    """Una vía de resolución con sus escenarios."""

    name: str
    scenarios: tuple[Scenario, ...]

    def check(self) -> None:
        total = sum(s.probability for s in self.scenarios)
        if not self.scenarios:
            raise ValueError(f"La vía '{self.name}' no tiene escenarios")
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Las probabilidades de '{self.name}' suman {total:.6f}, no 1"
            )


def expected_value(scenarios: Iterable[Scenario], rate: float) -> float:
    """VE = sum p_s * VN_s / (1 + r)^t_s."""
    if rate <= -1:
        raise ValueError("La tasa de descuento debe ser mayor que -1")
    return sum(
        s.probability * s.net_value() / (1 + rate) ** s.time_years
        for s in scenarios
    )


def max_purchase_price(ve: float, safety_margin: float) -> float:
    """P_max = VE * (1 - m)."""
    if not 0 <= safety_margin < 1:
        raise ValueError("El margen de seguridad debe estar en [0, 1)")
    return ve * (1 - safety_margin)


def irr(cashflows: list[tuple[float, float]], low: float = -0.99, high: float = 10.0) -> float | None:
    """TIR anual por bisección sobre flujos (t en años, importe).

    Devuelve None si no hay cambio de signo que permita encontrar la raíz.
    """

    def npv(rate: float) -> float:
        return sum(cf / (1 + rate) ** t for t, cf in cashflows)

    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


@dataclass
class RouteResult:
    route: str
    expected_value: float
    max_price: float


@dataclass
class Recommendation:
    best: RouteResult
    ranking: list[RouteResult] = field(default_factory=list)


def recommend(routes: Iterable[Route], rate: float, safety_margin: float) -> Recommendation:
    """Compara vías por valor esperado y propone la mejor.

    Los costes ya gastados no deben incluirse en los escenarios: la
    decisión se toma solo con flujos futuros.
    """
    results: list[RouteResult] = []
    for route in routes:
        route.check()
        ve = expected_value(route.scenarios, rate)
        results.append(
            RouteResult(
                route=route.name,
                expected_value=ve,
                max_price=max_purchase_price(ve, safety_margin),
            )
        )
    if not results:
        raise ValueError("No hay vías que comparar")
    ranking = sorted(results, key=lambda r: r.expected_value, reverse=True)
    return Recommendation(best=ranking[0], ranking=ranking)
