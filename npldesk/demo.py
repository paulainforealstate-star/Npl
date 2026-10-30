"""Modo demo pública: datos ficticios y tokens conocidos.

Solo para probar la app. No cargues nunca datos reales en una instancia demo:
los tokens son públicos y la base de datos se reinicia al arrancar.
"""

from __future__ import annotations

from npldesk.api import App

DEMO_TOKENS: dict[str, dict] = {
    "demo-admin": {"user": "demo_admin", "role": "admin"},
    "demo-analista": {"user": "demo_analista", "role": "analista"},
    "demo-responsable": {"user": "demo_responsable", "role": "responsable"},
    "demo-gestor": {"user": "demo_gestor", "role": "gestor_campo"},
    "demo-inversor": {"user": "demo_inversor", "role": "inversor"},
    "demo-auditor": {"user": "demo_auditor", "role": "auditor"},
}

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


def seed(app: App) -> int:
    """Crea un expediente ficticio con diligencia y decisión, para que se vea el flujo."""
    from datetime import date
    token = "demo-analista"
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
