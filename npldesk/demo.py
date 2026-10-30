"""Modo demo pública: datos ficticios y contraseñas conocidas.

Solo para probar la app. Arranca únicamente con NPLDESK_DB=:memory:, así que
ningún dato sobrevive al reinicio y no puede mezclarse con datos reales.
"""

from __future__ import annotations

from npldesk import auth
from npldesk.api import App

# (usuario, rol, contraseña). Son públicas a propósito: las cuentas de demo no tienen datos reales.
DEMO_USERS: list[tuple[str, str, str]] = [
    ("demo_admin", "admin", "demo-admin-npl-2026"),
    ("demo_analista", "analista", "demo-analista-npl-2026"),
    ("demo_responsable", "responsable", "demo-responsable-npl-2026"),
    ("demo_gestor", "gestor_campo", "demo-gestor-npl-2026"),
    ("demo_inversor", "inversor", "demo-inversor-npl-2026"),
    ("demo_auditor", "auditor", "demo-auditor-npl-2026"),
]

DEMO_INPUT = {
    "rate": 0.05,
    "safety_margin": 0.10,
    "routes": [
        {"name": "negociación", "scenarios": [
            {"name": "base", "probability": 1.0, "sale_price": 90000, "time_years": 0.5,
             "source": "Datos ficticios", "reviewed_at": "2026-10-01"}]},
        {"name": "venta ordenada", "scenarios": [
            {"name": "favorable", "probability": 0.7, "sale_price": 110000, "costs": 8000,
             "time_years": 1, "source": "Datos ficticios", "reviewed_at": "2026-10-01"},
            {"name": "desfavorable", "probability": 0.3, "sale_price": 70000, "costs": 8000,
             "time_years": 1.5, "source": "Datos ficticios", "reviewed_at": "2026-10-01"}]},
    ],
    "screening": {"loan": 118500.25, "appraisal": 200000, "province": "Madrid",
                  "amount": 118500.25, "occupied": False},
    "declared_saldo": 118500.25,
}

DEMO_NOTE = ("Titular: Titular Ficticio Demo\nFinca: 00001\n"
             "Saldo pendiente: 118.500,25 €\nFecha de emisión: 27/10/2026")


def login(app: App, username: str, password: str) -> str:
    status, body = app.handle("POST", "/api/login", {"user": username, "password": password},
                              None, ip="127.0.0.1")
    if status != 200:
        raise RuntimeError(f"No se pudo iniciar sesión en la demo como {username}: {body}")
    return body["token"]


def provision(app: App) -> dict[str, str]:
    """Crea las cuentas de demo (si no existen) y devuelve un token por rol."""
    tokens = {}
    for username, role, password in DEMO_USERS:
        if app.db.get_user_by_name(username) is None:
            app.db.create_user(username, username.replace("_", " ").capitalize(), None,
                               role, auth.hash_password(password))
        tokens[role] = login(app, username, password)
    return tokens


def seed(app: App) -> int:
    """Crea un expediente ficticio con diligencia y decisión, para que se vea el flujo."""
    from datetime import date
    token = provision(app)["analista"]
    note = DEMO_NOTE.replace("27/10/2026", date.today().strftime("%d/%m/%Y"))
    status, body = app.handle("POST", "/api/cases", {
        "name": "Expediente ficticio demo",
        "phase": "precontenciosa",
        "input": DEMO_INPUT,
    }, token)
    if status != 201:
        raise RuntimeError(f"No se pudo crear el expediente demo: {body}")
    cid = body["id"]
    app.handle("POST", f"/api/cases/{cid}/diligence/extract",
               {"document": "nota-demo.txt", "text": note}, token)
    for field, value in [("titular", "Titular Ficticio Demo"), ("finca", "00001"),
                         ("saldo_pendiente", 118500.25), ("fecha_nota", date.today().strftime("%d/%m/%Y"))]:
        app.handle("POST", f"/api/cases/{cid}/diligence/confirm",
                   {"field": field, "value": value}, token)
    app.handle("POST", f"/api/cases/{cid}/decision", {"chosen": "negociación"}, token)
    return cid
