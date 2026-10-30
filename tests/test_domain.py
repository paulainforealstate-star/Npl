import unittest
from datetime import date

from npldesk import diligence, risks, tasks
from npldesk.engine import Route, Scenario
from npldesk.portfolio import kpis
from npldesk.scenarios import analyze, irr_for_price
from npldesk.screening import screen
from npldesk.workflow import GateError, check_offer_limit, offer_blockers, register_decision

TODAY = date(2026, 10, 30)


class ScreeningTests(unittest.TestCase):
    base = {"loan": 100_000, "appraisal": 200_000, "province": "Madrid", "amount": 100_000, "occupied": False}

    def test_accepts_clean_case(self):
        self.assertEqual(screen(self.base)["status"], "aceptado")

    def test_rejects_with_reasons(self):
        r = screen({**self.base, "loan": 190_000, "province": "Sevilla", "occupied": True})
        self.assertEqual(r["status"], "descartado")
        self.assertEqual(len(r["reasons"]), 3)

    def test_rejects_zero_appraisal(self):
        with self.assertRaises(ValueError):
            screen({**self.base, "appraisal": 0})


class DiligenceTests(unittest.TestCase):
    text = "Titular: Ana Pérez\nFinca: 93296\nSaldo pendiente: 118.500,25 €\nFecha de emisión: 20/10/2026"

    def test_extract_parses_amounts_and_keeps_fragment(self):
        items = {i["field"]: i for i in diligence.extract("nota.txt", self.text)}
        self.assertEqual(items["saldo_pendiente"]["value"], 118500.25)
        self.assertIn("Saldo pendiente", items["saldo_pendiente"]["source_fragment"])
        self.assertEqual(items["titular"]["document"], "nota.txt")

    def test_confirm_requires_existing_candidate(self):
        state = {}
        diligence.add_candidates(state, diligence.extract("nota.txt", self.text))
        with self.assertRaises(ValueError):
            diligence.confirm(state, "finca", "99999", "ana", TODAY)
        diligence.confirm(state, "finca", "93296", "ana", TODAY)
        self.assertNotIn("finca", diligence.missing_required(state))

    def test_discrepancy_between_documents(self):
        state = {}
        diligence.add_candidates(state, diligence.extract("a.txt", "Titular: Ana"))
        diligence.add_candidates(state, diligence.extract("b.txt", "Titular: Luis"))
        kinds = [a["type"] for a in diligence.alerts(state, today=TODAY)]
        self.assertIn("discrepancia", kinds)

    def test_stale_note_detected(self):
        state = {}
        diligence.add_candidates(state, diligence.extract("n.txt", "Fecha de emisión: 01/01/2026"))
        kinds = [a["type"] for a in diligence.alerts(state, today=TODAY)]
        self.assertIn("nota_caducada", kinds)

    def test_saldo_mismatch_detected(self):
        state = {}
        diligence.add_candidates(state, diligence.extract("n.txt", self.text))
        diligence.confirm(state, "saldo_pendiente", 118500.25, "ana", TODAY)
        kinds = [a["type"] for a in diligence.alerts(state, declared_saldo=120_000, today=TODAY)]
        self.assertIn("saldo_no_cuadra", kinds)


class RiskTests(unittest.TestCase):
    def test_only_blocking_risks_are_approved(self):
        state = {}
        i = risks.add_risk(state, "procesal", "Subasta pendiente", "medio")
        with self.assertRaises(ValueError):
            risks.approve(state, i, "resp")

    def test_blocking_risk_must_be_approved(self):
        state = {}
        i = risks.add_risk(state, "ocupacion", "Ocupante sin título", "bloqueante", norm_ref="(referencia)")
        self.assertEqual(len(risks.open_blockers(state)), 1)
        risks.approve(state, i, "responsable")
        self.assertEqual(risks.open_blockers(state), [])

    def test_unknown_category_rejected(self):
        with self.assertRaises(ValueError):
            risks.add_risk({}, "inventada", "x", "bajo")


class WorkflowTests(unittest.TestCase):
    def test_offer_blocked_without_decision(self):
        blockers = offer_blockers({"screening": {"status": "aceptado"}}, None, TODAY)
        self.assertTrue(any("decisión" in b for b in blockers))

    def test_limit_for_responsable(self):
        check_offer_limit("responsable", 400_000)
        with self.assertRaises(GateError):
            check_offer_limit("responsable", 600_000)
        check_offer_limit("admin", 10_000_000)

    def test_changing_route_requires_justification(self):
        with self.assertRaises(ValueError):
            register_decision({}, "negociación", "dación en pago", "  ", "ana", TODAY)
        state = {}
        register_decision(state, "negociación", "dación en pago", "menor riesgo procesal", "ana", TODAY)
        self.assertEqual(state["decision"]["chosen"], "dación en pago")


class ScenarioTests(unittest.TestCase):
    route = Route("negociación", (Scenario("s", 1.0, 100_000, 0, 0, 0, 1.0),))

    def test_stress_is_worse_than_base(self):
        a = analyze(self.route, rate=0.05, safety_margin=0.1)
        self.assertLess(a["estres"]["expected_value"], a["base"]["expected_value"])
        self.assertLess(a["optimista"]["expected_value"], a["base"]["expected_value"] * 1.2)

    def test_irr_at_expected_value_equals_rate(self):
        ve = analyze(self.route, rate=0.05, safety_margin=0.0)["base"]["expected_value"]
        self.assertAlmostEqual(irr_for_price(ve, self.route), 0.05, places=4)


class TaskTests(unittest.TestCase):
    def test_ready_respects_dependencies(self):
        items = [
            {"id": 1, "status": "pendiente", "depends_on": [], "due_date": None},
            {"id": 2, "status": "pendiente", "depends_on": [1], "due_date": None},
        ]
        self.assertEqual([t["id"] for t in tasks.ready_tasks(items)], [1])
        items[0]["status"] = "completada"
        self.assertEqual([t["id"] for t in tasks.ready_tasks(items)], [2])

    def test_overdue(self):
        items = [{"id": 1, "status": "pendiente", "depends_on": [], "due_date": "2026-10-01"}]
        self.assertEqual(len(tasks.overdue_tasks(items, TODAY)), 1)


class PortfolioTests(unittest.TestCase):
    def test_kpis_with_recovery(self):
        cases = [{
            "state": {
                "screening": {"status": "aceptado"},
                "offer": {"amount": 100_000, "date": "2026-01-01", "planned_irr": 0.1},
                "recoveries": [{"amount": 130_000, "date": "2027-01-01"}],
                "phase_history": [{"phase": "precontenciosa", "date": "2026-01-01"},
                                  {"phase": "judicial_sin_adjudicar", "date": "2026-03-01"}],
            }
        }, {"state": {"screening": {"status": "descartado"}}}]
        k = kpis(cases, TODAY)
        self.assertEqual(k["comprometido"], 100_000)
        self.assertEqual(k["monetizado"], 130_000)
        self.assertEqual(k["pendiente"], -30_000)
        self.assertAlmostEqual(k["tir_realizada"], 0.30, places=2)
        self.assertEqual(k["tasa_descarte"], 0.5)
        self.assertEqual(k["dias_medios_por_fase"]["precontenciosa"], 59)


if __name__ == "__main__":
    unittest.main()
