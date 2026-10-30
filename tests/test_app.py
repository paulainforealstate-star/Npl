import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer

from npldesk import auth
from npldesk.api import App, make_handler
from npldesk.db import Database

TODAY = date(2026, 10, 30)
PASSWORD = "clave-de-prueba-larga"


def provision(app):
    """Crea una cuenta por rol y devuelve su token de sesión (iniciada con /api/login)."""
    tokens = {}
    for role in auth.ROLES:
        app.db.create_user(role, f"Usuario {role}", None, role, auth.hash_password(PASSWORD))
        status, body = app.handle("POST", "/api/login", {"user": role, "password": PASSWORD}, None,
                                  ip="10.0.0.1")
        assert status == 200, body
        tokens[role] = body["token"]
    return tokens

ROUTES = [
    {"name": "negociación", "scenarios": [
        {"name": "base", "probability": 1.0, "sale_price": 90000, "time_years": 0.5,
         "source": "tasación", "reviewed_at": "2026-10-01"}]},
    {"name": "venta ordenada", "scenarios": [
        {"name": "f", "probability": 0.7, "sale_price": 110000, "costs": 8000, "time_years": 1,
         "source": "mercado", "reviewed_at": "2026-10-01"},
        {"name": "d", "probability": 0.3, "sale_price": 70000, "costs": 8000, "time_years": 1.5,
         "source": "mercado", "reviewed_at": "2026-10-01"}]},
]

INPUT = {
    "rate": 0.05,
    "safety_margin": 0.10,
    "routes": ROUTES,
    "screening": {"loan": 118500.25, "appraisal": 200000, "province": "Madrid",
                  "amount": 118500.25, "occupied": False},
    "declared_saldo": 118500.25,
}

NOTE = ("Titular: Eduardo Gabarri Céspedes\nFinca: 93296\n"
        "Saldo pendiente: 118.500,25 €\nFecha de emisión: 20/10/2026")


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.app = App(self.db, today=lambda: TODAY)
        self.tokens = provision(self.app)

    def tearDown(self):
        self.db.close()

    def call(self, method, path, body=None, role=None):
        token = self.tokens[role] if role else None
        return self.app.handle(method, path, body, token)

    def new_case(self, name="Expediente Lobos 3"):
        status, body = self.call("POST", "/api/cases",
                                 {"name": name, "phase": "precontenciosa", "input": INPUT}, "analista")
        self.assertEqual(status, 201, body)
        return body["id"]

    def ready_case(self):
        """Caso con diligencia confirmada y decisión registrada, listo para ofertar."""
        cid = self.new_case()
        self.call("POST", f"/api/cases/{cid}/diligence/extract", {"document": "nota.txt", "text": NOTE}, "analista")
        for field, value in [("titular", "Eduardo Gabarri Céspedes"), ("finca", "93296"),
                             ("saldo_pendiente", 118500.25), ("fecha_nota", "20/10/2026")]:
            s, b = self.call("POST", f"/api/cases/{cid}/diligence/confirm",
                             {"field": field, "value": value}, "analista")
            self.assertEqual(s, 200, b)
        s, b = self.call("POST", f"/api/cases/{cid}/decision", {"chosen": "negociación"}, "analista")
        self.assertEqual(s, 200, b)
        return cid


class AccessTests(AppTestCase):
    def test_no_token_is_401(self):
        status, _ = self.call("GET", "/api/cases")
        self.assertEqual(status, 401)

    def test_wrong_role_is_403(self):
        status, _ = self.call("POST", "/api/cases", {"name": "x", "phase": "precontenciosa", "input": INPUT}, "inversor")
        self.assertEqual(status, 403)

    def test_investor_cannot_make_offers(self):
        cid = self.ready_case()
        status, _ = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "analista")
        self.assertEqual(status, 403)

    def test_health_is_public(self):
        self.assertEqual(self.call("GET", "/health")[0], 200)


class CaseTests(AppTestCase):
    def test_create_returns_valuation_and_screening(self):
        cid = self.new_case()
        status, case = self.call("GET", f"/api/cases/{cid}", None, "analista")
        self.assertEqual(status, 200)
        self.assertEqual(case["result"]["best"]["route"], "negociación")
        self.assertEqual(case["state"]["screening"]["status"], "aceptado")
        self.assertEqual(case["state"]["phase_history"][0]["phase"], "precontenciosa")

    def test_source_warning_when_missing(self):
        bad = {**INPUT, "routes": [{"name": "negociación", "scenarios": [
            {"name": "base", "probability": 1.0, "sale_price": 90000, "time_years": 0.5}]}]}
        status, body = self.call("POST", "/api/cases",
                                 {"name": "x", "phase": "precontenciosa", "input": bad}, "analista")
        self.assertEqual(status, 201)
        self.assertTrue(body["warnings"])

    def test_phase_outside_list_rejected(self):
        status, _ = self.call("POST", "/api/cases", {"name": "x", "phase": "otra", "input": INPUT}, "analista")
        self.assertEqual(status, 400)

    def test_discarded_screening_blocks_offer(self):
        cid = self.new_case()
        self.call("POST", f"/api/cases/{cid}/screen",
                  {"loan": 190000, "appraisal": 200000, "province": "Sevilla", "amount": 190000, "occupied": False},
                  "analista")
        self.call("POST", f"/api/cases/{cid}/diligence/extract", {"document": "n", "text": NOTE}, "analista")
        status, body = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(status, 409)
        self.assertIn("cribado", body["error"])

    def test_delete_only_admin(self):
        cid = self.new_case()
        self.assertEqual(self.call("DELETE", f"/api/cases/{cid}", None, "responsable")[0], 403)
        self.assertEqual(self.call("DELETE", f"/api/cases/{cid}", None, "admin")[0], 200)
        self.assertEqual(self.call("GET", f"/api/cases/{cid}", None, "admin")[0], 404)


class WorkflowTests(AppTestCase):
    def test_offer_blocked_until_diligence_confirmed(self):
        cid = self.new_case()
        status, body = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(status, 409)
        self.assertIn("Faltan datos", body["error"])

    def test_full_flow_to_offer_and_portfolio(self):
        cid = self.ready_case()
        status, offer = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(status, 201, offer)
        self.assertIsNotNone(offer["planned_irr"])
        # Un cobro no puede superar la oferta (2.3); y como no ha pasado tiempo, la TIR realizada no es calculable.
        s, b = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 95000, "date": "2026-10-30"}, "responsable")
        self.assertEqual(s, 409, b)
        s, _ = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 60000, "date": "2026-10-30"}, "responsable")
        self.assertEqual(s, 201)
        s, k = self.call("GET", "/api/portfolio", None, "inversor")
        self.assertEqual(s, 200)
        self.assertEqual(k["comprometido"], 80000)
        self.assertEqual(k["monetizado"], 60000)
        self.assertIsNone(k["tir_realizada"])

    def test_blocking_risk_gates_offer_until_approved(self):
        cid = self.ready_case()
        s, r = self.call("POST", f"/api/cases/{cid}/risks",
                         {"category": "ocupacion", "description": "Ocupante sin título",
                          "level": "bloqueante", "norm_ref": "referencia a validar"}, "analista")
        self.assertEqual(s, 201)
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")[0], 409)
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/risks/{r['index']}/approve", None, "analista")[0], 403)
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/risks/{r['index']}/approve", None, "responsable")[0], 200)
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")[0], 201)

    def test_responsable_limit(self):
        cid = self.ready_case()
        status, body = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 600000}, "responsable")
        self.assertEqual(status, 409)
        self.assertIn("límite", body["error"])
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/offer", {"amount": 600000}, "admin")[0], 201)

    def test_route_change_needs_justification(self):
        cid = self.new_case()
        s, b = self.call("POST", f"/api/cases/{cid}/decision", {"chosen": "venta ordenada"}, "analista")
        self.assertEqual(s, 400)
        self.assertIn("justificarlo", b["error"])
        s, _ = self.call("POST", f"/api/cases/{cid}/decision",
                         {"chosen": "venta ordenada", "justification": "Liquidez más rápida"}, "analista")
        self.assertEqual(s, 200)

    def test_route_not_viable_in_phase_rejected(self):
        cid = self.new_case()
        self.call("POST", f"/api/cases/{cid}/phase", {"phase": "judicial_adjudicada"}, "analista")
        s, b = self.call("POST", f"/api/cases/{cid}/decision", {"chosen": "negociación"}, "analista")
        self.assertEqual(s, 400)
        self.assertIn("no son viables", b["error"])

    def test_diligence_alert_blocks_offer(self):
        cid = self.new_case()
        old = NOTE.replace("20/10/2026", "01/01/2026")
        self.call("POST", f"/api/cases/{cid}/diligence/extract", {"document": "vieja.txt", "text": old}, "analista")
        for field, value in [("titular", "Eduardo Gabarri Céspedes"), ("finca", "93296"),
                             ("saldo_pendiente", 118500.25), ("fecha_nota", "01/01/2026")]:
            self.call("POST", f"/api/cases/{cid}/diligence/confirm", {"field": field, "value": value}, "analista")
        self.call("POST", f"/api/cases/{cid}/decision", {"chosen": "negociación"}, "analista")
        status, body = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(status, 409)
        self.assertIn("nota_caducada", body["error"])


class TaskAndVisitTests(AppTestCase):
    def test_dependencies_enforced(self):
        cid = self.new_case()
        _, a = self.call("POST", f"/api/cases/{cid}/tasks", {"title": "Visitar", "owner": "gestor"}, "gestor_campo")
        _, b = self.call("POST", f"/api/cases/{cid}/tasks",
                         {"title": "Informe", "owner": "analista", "depends_on": [a["id"]]}, "analista")
        s, _ = self.call("POST", f"/api/cases/{cid}/tasks/{b['id']}/status", {"status": "completada"}, "analista")
        self.assertEqual(s, 409)
        self.call("POST", f"/api/cases/{cid}/tasks/{a['id']}/status", {"status": "completada"}, "gestor_campo")
        s, _ = self.call("POST", f"/api/cases/{cid}/tasks/{b['id']}/status", {"status": "completada"}, "analista")
        self.assertEqual(s, 200)

    def test_visit_coordinates_validated(self):
        cid = self.new_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/visits", {"lat": 95, "lon": 0}, "gestor_campo")
        self.assertEqual(s, 400)
        s, _ = self.call("POST", f"/api/cases/{cid}/visits",
                         {"lat": 40.4168, "lon": -3.7038, "photo_ref": "foto-1.jpg"}, "gestor_campo")
        self.assertEqual(s, 201)

    def test_calendar_available(self):
        cid = self.new_case()
        s, body = self.call("GET", f"/api/cases/{cid}/calendar", None, "analista")
        self.assertEqual(s, 200)
        self.assertIsInstance(body["calendar"], list)


class ReportAndAuditTests(AppTestCase):
    def test_investor_report_is_anonymized(self):
        cid = self.ready_case()
        s, html_full = self.call("GET", f"/api/cases/{cid}/report", None, "responsable")
        self.assertEqual(s, 200)
        self.assertIn("Lobos 3", html_full)
        self.assertIn("Eduardo Gabarri", html_full)
        s, html_anon = self.call("GET", f"/api/cases/{cid}/report", None, "inversor")
        self.assertEqual(s, 200)
        self.assertNotIn("Lobos 3", html_anon)
        self.assertNotIn("Eduardo Gabarri", html_anon)
        self.assertIn("EXP-", html_anon)

    def test_audit_trail_recorded_and_restricted(self):
        self.ready_case()
        self.assertEqual(self.call("GET", "/api/audit", None, "analista")[0], 403)
        s, body = self.call("GET", "/api/audit", None, "auditor")
        self.assertEqual(s, 200)
        actions = {a["action"] for a in body["audit"]}
        self.assertIn("crear_expediente", actions)
        self.assertIn("decision", actions)

    def test_valuation_endpoint(self):
        s, body = self.call("POST", "/api/valuation", {**INPUT, "montecarlo_iterations": 500}, "analista")
        self.assertEqual(s, 200)
        self.assertIn("montecarlo", body)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        cls.app = App(cls.db, today=lambda: TODAY)
        cls.tokens = provision(cls.app)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.db.close()

    def request(self, method, path, body=None, role=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if role:
            headers["Authorization"] = f"Bearer {self.tokens[role]}"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_end_to_end_over_http(self):
        status, raw = self.request("POST", "/api/cases",
                                   {"name": "HTTP", "phase": "precontenciosa", "input": INPUT}, "analista")
        self.assertEqual(status, 201)
        cid = json.loads(raw)["id"]
        status, raw = self.request("GET", f"/api/cases/{cid}", None, "analista")
        self.assertEqual(json.loads(raw)["name"], "HTTP")
        self.assertEqual(self.request("GET", "/api/cases", None, None)[0], 401)
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"NPLDesk", raw)


if __name__ == "__main__":
    unittest.main()
