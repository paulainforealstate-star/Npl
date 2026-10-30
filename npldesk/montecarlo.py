"""Simulación Monte Carlo del valor esperado de una vía.

En cada iteración se elige un escenario según su probabilidad y se calcula
su valor descontado. Con muchas iteraciones se obtienen P10, P50 y P90.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from npldesk.engine import Route


@dataclass(frozen=True)
class Percentiles:
    mean: float
    p10: float
    p50: float
    p90: float


def _percentile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def simulate_route(
    route: Route, rate: float, iterations: int = 10_000, seed: int | None = 42
) -> Percentiles:
    if iterations < 1:
        raise ValueError("Se necesita al menos una iteración")
    route.check()
    rng = random.Random(seed)
    weights = [s.probability for s in route.scenarios]
    values = []
    for _ in range(iterations):
        s = rng.choices(route.scenarios, weights=weights, k=1)[0]
        values.append(s.net_value() / (1 + rate) ** s.time_years)
    values.sort()
    return Percentiles(
        mean=sum(values) / len(values),
        p10=_percentile(values, 0.10),
        p50=_percentile(values, 0.50),
        p90=_percentile(values, 0.90),
    )
