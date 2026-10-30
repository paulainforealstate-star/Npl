"""Roles, permisos y tokens de acceso."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

ROLES = ("admin", "analista", "responsable", "gestor_campo", "inversor", "auditor")

PERMISSIONS: dict[str, set[str]] = {
    "valuation:run": {"admin", "analista", "responsable"},
    "cases:write": {"admin", "analista"},
    "cases:read": {"admin", "analista", "responsable", "gestor_campo", "auditor"},
    "cases:delete": {"admin"},
    "screening:write": {"admin", "analista"},
    "diligence:write": {"admin", "analista", "responsable"},
    "risks:write": {"admin", "analista", "responsable"},
    "risks:approve": {"admin", "responsable"},
    "decision:write": {"admin", "analista", "responsable"},
    "offer:write": {"admin", "responsable"},
    "phase:write": {"admin", "analista", "responsable", "gestor_campo"},
    "recovery:write": {"admin", "responsable", "gestor_campo"},
    "tasks:write": {"admin", "analista", "responsable", "gestor_campo"},
    "visits:write": {"admin", "analista", "gestor_campo"},
    "portfolio:read": {"admin", "responsable", "inversor", "auditor"},
    "report:read": {"admin", "analista", "responsable", "inversor", "auditor"},
    "audit:read": {"admin", "auditor"},
}


class Unauthorized(Exception):
    pass


class Forbidden(PermissionError):
    pass


def load_users(path: str | Path) -> dict[str, dict]:
    """Devuelve {token: {"user": ..., "role": ...}}. Crea el fichero si no existe."""
    p = Path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        users = {
            secrets.token_urlsafe(24): {"user": role, "role": role} for role in ROLES
        }
        p.write_text(json.dumps(users, indent=2), encoding="utf-8")
        p.chmod(0o600)
        return users
    return json.loads(p.read_text(encoding="utf-8"))


def authorize(users: dict[str, dict], token: str | None, perm: str) -> dict:
    if not token or token not in users:
        raise Unauthorized("Token no válido o ausente")
    user = users[token]
    if user["role"] not in PERMISSIONS[perm]:
        raise Forbidden(f"Tu rol '{user['role']}' no tiene el permiso '{perm}'")
    return user
