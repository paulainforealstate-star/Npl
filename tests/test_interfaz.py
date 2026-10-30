"""Pruebas de los cambios de interfaz: pasos del expediente, vista previa de oferta,
corrección manual de datos, catálogo de riesgos y estado en la lista."""

import copy
import unittest

from npldesk import guide
from npldesk.config import DEFAULTS, RISK_CATALOG
from test_app import INPUT, NOTE, AppTestCase


class ManualConfirmTests(AppTestCase):
    def extract(self, cid, text=NOTE):
        s, b = self.call("POST", f"/api/cases/{cid}/diligence/extract", {"document": "nota.txt", "text": text}, "analista")
        self.assertEqual(s, 200, b)

    def test_numeric_confirm_accepts_json_integer_for_extracted_float(self):
        # Al serializar 150000.0 en JSON llega como 150000. Debe reconocerse el mismo importe.
        cid = self.new_case()
        self.extract(cid, "Saldo pendiente: 150.000,00 €")
        s, b = self.call("POST", f"/api/cases/{cid}/diligence/confirm", {"field": "saldo_pendiente", "value": 150000}, "analista")
        self.assertEqual(s, 200, b)
        conf = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]["state"]["diligence"]["confirmed"]
        self.assertEqual(conf["saldo_pendiente"]["value"], 150000.0)

    def test_manual_correction_is_its_own_source(self):
        cid = self.new_case()
        s, b = self.call("POST", f"/api/cases/{cid}/diligence/confirm",
                         {"field": "finca", "value": "93296/B", "manual": True}, "analista")
        self.assertEqual(s, 200, b)
        state = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]["state"]["diligence"]
        self.assertEqual(state["confirmed"]["finca"]["value"], "93296/B")
        self.assertEqual(state["candidates"]["finca"][-1]["document"], "corrección manual")
        self.assertIsNone(state["candidates"]["finca"][-1]["source_fragment"])

    def test_manual_correction_validates_each_field(self):
        cid = self.new_case()
        bad = [("saldo_pendiente", -5), ("saldo_pendiente", "mucho"), ("fecha_nota", "2026-10-20"),
               ("titular", "   "), ("inventado", "x")]
        for field, value in bad:
            s, b = self.call("POST", f"/api/cases/{cid}/diligence/confirm",
                             {"field": field, "value": value, "manual": True}, "analista")
            self.assertEqual(s, 400, (field, value, b))


class GuideTests(AppTestCase):
    def test_new_case_has_six_steps_and_next_is_diligence(self):
        cid = self.new_case()
        steps = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]["steps"]
        self.assertEqual([s["label"] for s in steps],
                         ["Cribado", "Diligencia", "Riesgos", "Decisión", "Oferta", "Ejecución"])
        self.assertEqual(steps[0]["status"], "completado")
        self.assertEqual(steps[1]["status"], "pendiente")
        self.assertIn("Faltan datos", steps[1]["reason"])
        self.assertTrue(steps[1]["next"])
        self.assertEqual(steps[4]["status"], "bloqueado")
        self.assertEqual(sum(1 for s in steps if s["next"]), 1)

    def test_discarded_screening_blocks_its_step(self):
        inp = copy.deepcopy(INPUT)
        inp["screening"]["province"] = "Sevilla"
        s, b = self.call("POST", "/api/cases", {"name": "Fuera de zona", "phase": "precontenciosa", "input": inp}, "analista")
        self.assertEqual(s, 201, b)
        steps = self.call("GET", f"/api/cases/{b['id']}", None, "analista")[1]["steps"]
        self.assertEqual(steps[0]["status"], "bloqueado")
        self.assertTrue(steps[0]["reason"].startswith("Descartado"))
        self.assertTrue(steps[0]["next"])

    def test_ready_case_is_listed_as_ready(self):
        cid = self.ready_case()
        case = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        self.assertEqual(case["estado"], "listo_para_ofertar")
        self.assertEqual(case["steps"][4]["status"], "pendiente")

    def test_offer_then_full_collection_closes_the_case(self):
        cid = self.ready_case()
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(s, 201, b)
        self.assertEqual(self.call("GET", f"/api/cases/{cid}", None, "analista")[1]["estado"], "en_curso")
        s, b = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 80000, "date": "2026-10-30"}, "responsable")
        self.assertEqual(s, 201, b)
        case = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        self.assertEqual(case["estado"], "cerrado")
        self.assertEqual(case["cobrado"], 80000)
        self.assertEqual(case["steps"][5]["status"], "completado")

    def test_voided_recovery_does_not_count(self):
        self.assertEqual(guide.collected({"recoveries": [
            {"amount": 10, "anulado": False}, {"amount": 99, "anulado": True}]}), 10)

    def test_list_puts_blocked_cases_first_and_carries_status(self):
        ready = self.ready_case()
        blocked = self.new_case("Bloqueado sin diligencia")
        s, b = self.call("GET", "/api/cases", None, "analista")
        self.assertEqual(s, 200)
        ids = [c["id"] for c in b["cases"]]
        self.assertLess(ids.index(blocked), ids.index(ready))
        by_id = {c["id"]: c for c in b["cases"]}
        self.assertEqual(by_id[blocked]["estado"], "bloqueado")
        self.assertEqual(by_id[ready]["estado"], "listo_para_ofertar")
        self.assertIsNone(by_id[ready]["oferta"])


class OfferPreviewTests(AppTestCase):
    def test_preview_matches_the_offer_and_saves_nothing(self):
        cid = self.ready_case()
        before = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        s, p = self.call("POST", f"/api/cases/{cid}/offer/preview", {"amount": 80000}, "responsable")
        self.assertEqual(s, 200, p)
        after = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        self.assertEqual(before["version"], after["version"])
        self.assertIsNone(after["state"].get("offer"))
        s, offer = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 80000}, "responsable")
        self.assertEqual(s, 201, offer)
        self.assertAlmostEqual(p["planned_irr"], offer["planned_irr"])
        self.assertEqual(p["max_price"], offer["max_price"])
        self.assertAlmostEqual(p["distance"], offer["max_price"] - 80000)
        self.assertEqual(p["over_max_price"], offer["over_max_price"])

    def test_preview_without_decision_explains_why(self):
        cid = self.new_case()
        s, p = self.call("POST", f"/api/cases/{cid}/offer/preview", {"amount": 80000}, "responsable")
        self.assertEqual(s, 200, p)
        self.assertIsNone(p["planned_irr"])
        self.assertIn("decisión", p["message"])

    def test_preview_flags_over_max_price_and_approval_limit(self):
        cid = self.ready_case()
        s, p = self.call("POST", f"/api/cases/{cid}/offer/preview", {"amount": 900000}, "responsable")
        self.assertEqual(s, 200, p)
        self.assertTrue(p["over_max_price"])
        self.assertFalse(p["approval_ok"])
        self.assertLess(p["distance"], 0)

    def test_preview_rejects_bad_amount_and_wrong_role(self):
        cid = self.ready_case()
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/offer/preview", {"amount": -1}, "responsable")[0], 400)
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/offer/preview", {"amount": 1000}, "inversor")[0], 403)
        self.assertEqual(self.app.handle("POST", f"/api/cases/{cid}/offer/preview", {"amount": 1000}, None)[0], 401)


class CatalogTests(AppTestCase):
    def test_catalog_entries_are_complete_and_valid(self):
        s, cat = self.call("GET", "/api/catalog", None, "analista")
        self.assertEqual(s, 200)
        ids = [r["id"] for r in cat["risk_catalog"]]
        self.assertEqual(len(ids), len(set(ids)))
        for r in cat["risk_catalog"]:
            self.assertIn(r["category"], DEFAULTS["risk_categories"], r["id"])
            self.assertIn(r["level"], DEFAULTS["risk_levels"], r["id"])
            self.assertTrue(r["norm_ref"], r["id"])
            self.assertTrue(r["description"], r["id"])
        self.assertEqual({r["category"] for r in RISK_CATALOG}, set(DEFAULTS["risk_categories"]))

    def test_catalog_is_not_visible_to_investors(self):
        self.assertEqual(self.call("GET", "/api/catalog", None, "inversor")[0], 403)


if __name__ == "__main__":
    unittest.main()
