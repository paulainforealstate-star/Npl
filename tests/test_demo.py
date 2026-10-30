import unittest
from datetime import date

from npldesk.api import App
from npldesk.db import Database
from npldesk.demo import DEMO_TOKENS, seed


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.app = App(self.db, dict(DEMO_TOKENS), today=date.today)
        self.cid = seed(self.app)

    def tearDown(self):
        self.db.close()

    def test_seed_creates_full_case(self):
        status, case = self.app.handle("GET", f"/api/cases/{self.cid}", None, "demo-auditor")
        self.assertEqual(status, 200)
        self.assertEqual(case["state"]["decision"]["chosen"], "negociación")
        self.assertEqual(set(case["state"]["diligence"]["confirmed"]),
                         {"titular", "finca", "saldo_pendiente", "fecha_nota"})

    def test_demo_case_can_be_offered(self):
        status, body = self.app.handle("POST", f"/api/cases/{self.cid}/offer",
                                       {"amount": 80000}, "demo-responsable")
        self.assertEqual(status, 201, body)

    def test_investor_sees_anonymized_report(self):
        status, html = self.app.handle("GET", f"/api/cases/{self.cid}/report", None, "demo-inversor")
        self.assertEqual(status, 200)
        self.assertNotIn("Titular Ficticio Demo", html)

    def test_demo_tokens_grant_role_permissions(self):
        self.assertEqual(self.app.handle("GET", "/api/portfolio", None, "demo-inversor")[0], 200)
        self.assertEqual(self.app.handle("GET", "/api/portfolio", None, "demo-analista")[0], 403)


if __name__ == "__main__":
    unittest.main()
