"""Pruebas de las correcciones de robustez y reglas de negocio de la v0.4."""

import http.client
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from unittest import mock

from npldesk.api import App, make_handler
from npldesk.db import Database
from tests.test_app import INPUT, NOTE, TODAY, AppTestCase, provision


def confirm_all(case: AppTestCase, cid: int, saldo=118500.25, fecha="20/10/2026"):
    for field, value in [("titular", "Eduardo Gabarri Céspedes"), ("finca", "93296"),
                         ("saldo_pendiente", saldo), ("fecha_nota", fecha)]:
        s, b = case.call("POST", f"/api/cases/{cid}/diligence/confirm", {"field": field, "value": value}, "analista")
        assert s == 200, b


class DateValidationTests(AppTestCase):
    def test_bad_recovery_date_rejected(self):
        cid = self.ready_case()
        self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        s, b = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 5, "date": "2027-13-45"}, "responsable")
        self.assertEqual(s, 400)
        self.assertIn("AAAA-MM-DD", b["error"])

    def test_bad_recovery_date_does_not_break_portfolio(self):
        cid = self.ready_case()
        self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 5, "date": "ayer"}, "responsable")
        self.assertEqual(self.call("GET", "/api/portfolio", None, "inversor")[0], 200)

    def test_bad_task_due_date_rejected_and_task_list_still_works(self):
        cid = self.new_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/tasks", {"title": "t", "due_date": "ayer"}, "analista")
        self.assertEqual(s, 400)
        self.assertEqual(self.call("GET", f"/api/cases/{cid}/tasks", None, "analista")[0], 200)

    def test_bad_visit_date_rejected(self):
        cid = self.new_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/visits",
                         {"lat": 40.4, "lon": -3.7, "date": "2026-02-30"}, "gestor_campo")
        self.assertEqual(s, 400)

    def test_invalid_note_date_is_not_saved(self):
        cid = self.new_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/diligence/extract",
                         {"document": "n", "text": NOTE.replace("20/10/2026", "31/02/2026")}, "analista")
        self.assertEqual(s, 400)
        case = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        self.assertEqual(case["state"].get("diligence", {}).get("candidates", {}), {})

    def test_task_with_unknown_dependency_rejected(self):
        cid = self.new_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/tasks",
                         {"title": "t", "depends_on": [999]}, "analista")
        self.assertEqual(s, 400)


class DiligenceAlertTests(AppTestCase):
    def test_discrepancy_resolved_by_confirmation(self):
        cid = self.new_case()
        self.call("POST", f"/api/cases/{cid}/diligence/extract", {"document": "a", "text": NOTE}, "analista")
        self.call("POST", f"/api/cases/{cid}/diligence/extract",
                  {"document": "b", "text": NOTE.replace("118.500,25", "118.000,00")}, "analista")
        confirm_all(self, cid)
        self.call("POST", f"/api/cases/{cid}/decision", {"chosen": "negociación"}, "analista")
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(s, 201, b)

    def test_new_conflicting_document_reopens_discrepancy(self):
        cid = self.ready_case()  # saldo confirmado: 118.500,25
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 60000}, "responsable")
        self.assertEqual(s, 201, b)
        # Llega un documento nuevo con otro saldo: hay que volver a revisarlo.
        self.call("POST", f"/api/cases/{cid}/diligence/extract",
                  {"document": "c", "text": NOTE.replace("118.500,25", "117.000,00")}, "analista")
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 60000}, "responsable")
        self.assertEqual(s, 409)
        self.assertIn("discrepancia", b["error"])
        # Al confirmar el valor nuevo, la discrepancia desaparece.
        self.call("POST", f"/api/cases/{cid}/diligence/confirm", {"field": "saldo_pendiente", "value": 117000.0}, "analista")
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 60000, "motivo": "saldo corregido"}, "responsable")
        self.assertEqual(s, 201, b)

    def test_confirmed_note_date_is_used_not_the_oldest(self):
        cid = self.new_case()
        old = NOTE.replace("20/10/2026", "01/01/2026")
        self.call("POST", f"/api/cases/{cid}/diligence/extract", {"document": "vieja", "text": old}, "analista")
        self.call("POST", f"/api/cases/{cid}/diligence/confirm", {"field": "fecha_nota", "value": "01/01/2026"}, "analista")
        self.call("POST", f"/api/cases/{cid}/diligence/extract", {"document": "nueva", "text": NOTE}, "analista")
        confirm_all(self, cid)  # confirma la fecha nueva
        self.call("POST", f"/api/cases/{cid}/decision", {"chosen": "negociación"}, "analista")
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(s, 201, b)


class OfferRulesTests(AppTestCase):
    def test_recovery_requires_offer(self):
        cid = self.ready_case()
        s, b = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 1000}, "responsable")
        self.assertEqual(s, 409)
        self.assertIn("oferta", b["error"])

    def test_offer_over_max_price_is_flagged_not_blocked(self):
        cid = self.ready_case()
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(s, 201)
        self.assertTrue(b["over_max_price"])  # P_max de negociación ≈ 79.019 €
        self.assertTrue(b["warnings"])
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 60000, "motivo": "ajuste de precio"}, "responsable")
        self.assertEqual(s, 201)
        self.assertFalse(b["over_max_price"])
        self.assertEqual(b["warnings"], [])

    def test_offer_locked_after_recovery(self):
        cid = self.ready_case()
        self.call("POST", f"/api/cases/{cid}/offer", {"amount": 60000}, "responsable")
        self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 10000, "date": "2026-10-30"}, "responsable")
        s, _ = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 70000, "motivo": "x"}, "responsable")
        self.assertEqual(s, 409)

    def test_decision_locked_after_offer(self):
        cid = self.ready_case()
        self.call("POST", f"/api/cases/{cid}/offer", {"amount": 60000}, "responsable")
        s, _ = self.call("POST", f"/api/cases/{cid}/decision", {"chosen": "venta ordenada", "justification": "x"}, "analista")
        self.assertEqual(s, 409)

    def test_nonviable_decision_blocks_phase_change(self):
        cid = self.ready_case()  # decisión: negociación (solo viable en precontenciosa)
        s, b = self.call("POST", f"/api/cases/{cid}/phase", {"phase": "judicial_sin_adjudicar"}, "analista")
        self.assertEqual(s, 409)
        self.assertIn("no es viable", b["error"])

    def test_same_phase_is_noop(self):
        cid = self.new_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/phase", {"phase": "precontenciosa"}, "analista")
        self.assertEqual(s, 200)
        case = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        self.assertEqual(len(case["state"]["phase_history"]), 1)


class RobustnessTests(AppTestCase):
    def test_montecarlo_iterations_are_capped(self):
        s, b = self.call("POST", "/api/valuation", {**INPUT, "montecarlo_iterations": 10**9}, "analista")
        self.assertEqual(s, 400)
        self.assertIn("montecarlo_iterations", b["error"])

    def test_route_must_be_object(self):
        s, _ = self.call("POST", "/api/valuation", {**INPUT, "routes": ["negociación"]}, "analista")
        self.assertEqual(s, 400)

    def test_unexpected_error_returns_500_json(self):
        with mock.patch.object(self.db, "list_cases", side_effect=RuntimeError("boom")):
            s, b = self.call("GET", "/api/cases", None, "analista")
        self.assertEqual(s, 500)
        self.assertNotIn("boom", b["error"])  # no se filtran detalles internos

    def test_nan_amount_rejected(self):
        cid = self.ready_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/offer", {"amount": float("nan")}, "responsable")
        self.assertEqual(s, 400)


class HttpLimitTests(unittest.TestCase):
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

    def test_oversized_body_is_413(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/valuation")
        conn.putheader("Authorization", f"Bearer {self.tokens['analista']}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(5_000_000))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()

    def test_bad_content_length_is_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/valuation")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()


if __name__ == "__main__":
    unittest.main()
