"""Validación de entrada en un único lugar (plan 2.1).

Cada función devuelve el valor normalizado o lanza ValueError (HTTP 400)
con un mensaje que nombra el campo.
"""

from __future__ import annotations

import math
import re
from datetime import date

MAX_AMOUNT = 1_000_000_000.0
MIN_YEAR, MAX_YEAR = 1990, 2100
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def number(value, field: str, *, minimum: float | None = None, maximum: float | None = None,
           strict_min: bool = False) -> float:
    if value is None or isinstance(value, bool) or value == "":
        raise ValueError(f"'{field}' debe ser un número")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{field}' debe ser un número")
    if not math.isfinite(x):
        raise ValueError(f"'{field}' debe ser un número finito")
    if minimum is not None and (x < minimum or (strict_min and x == minimum)):
        op = "mayor que" if strict_min else "mayor o igual que"
        raise ValueError(f"'{field}' debe ser {op} {minimum:g}")
    if maximum is not None and x > maximum:
        raise ValueError(f"'{field}' debe ser como máximo {maximum:g}")
    return x


def amount(value, field: str, *, positive: bool = False) -> float:
    """Importe en euros: >= 0 (o > 0 si positive) y no mayor que MAX_AMOUNT."""
    return number(value, field, minimum=0, strict_min=positive, maximum=MAX_AMOUNT)


def probability(value, field: str = "probability") -> float:
    return number(value, field, minimum=0, maximum=1)


def years(value, field: str = "time_years") -> float:
    return number(value, field, minimum=0, strict_min=True, maximum=50)


def rate(value) -> float:
    return number(value, "rate", minimum=0, maximum=1)


def margin(value) -> float:
    return number(value, "safety_margin", minimum=0, maximum=0.99)


def iso_date(value, field: str) -> str:
    """Fecha AAAA-MM-DD dentro de un rango razonable."""
    s = str(value)
    if not _ISO.fullmatch(s):
        raise ValueError(f"'{field}' debe tener formato AAAA-MM-DD")
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"'{field}' debe tener formato AAAA-MM-DD")
    if not MIN_YEAR <= d.year <= MAX_YEAR:
        raise ValueError(f"'{field}' está fuera del rango permitido")
    return d.isoformat()
