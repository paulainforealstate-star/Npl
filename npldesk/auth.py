"""Roles, permisos, contraseñas y sesiones.

- Cada persona tiene su cuenta (tabla `users`). El rol es un atributo de la cuenta.
- Las contraseñas se guardan con scrypt (biblioteca estándar), nunca en claro.
- El token de sesión es aleatorio; en base de datos solo se guarda su SHA-256.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

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

PASSWORD_MIN_LENGTH = 12
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


class Unauthorized(Exception):
    pass


class Forbidden(PermissionError):
    pass


def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                   n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


# Hash de referencia: se verifica aunque el usuario no exista, para no revelar
# por el tiempo de respuesta si una cuenta existe.
DUMMY_HASH = hash_password("contraseña-de-referencia")


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_permission(role: str, perm: str) -> None:
    if role not in PERMISSIONS[perm]:
        raise Forbidden(f"Tu rol '{role}' no tiene el permiso '{perm}'")
