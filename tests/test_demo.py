import unittest
from datetime import date

from npldesk.api import App
from npldesk.db import Database
from npldesk.demo import provision, seed


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.app = App(self.db, today=date.today)
        self.tokens = provision(self.app)
        self.cid = seed(self.app)

    def tearDown(self):
        self.db.close()

    def test_seed_creates_full_case(self):
        status, case = self.app.handle("GET", f"/api/cases/{self.cid}", None, self.tokens["auditor"])
        self.assertEqual(status, 200)
        self.assertEqual(case["state"]["decision"]["chosen"], "negociación")
        self.assertEqual(set(case["state"]["diligence"]["confirmed"]),
                         {"titular", "finca", "saldo_pendiente", "fecha_nota"})

    def test_demo_case_can_be_offered(self):
        status, body = self.app.handle("POST", f"/api/cases/{self.cid}/offer",
                                       {"amount": 80000}, self.tokens["responsable"])
        self.assertEqual(status, 201, body)

    def test_investor_sees_anonymized_report(self):
        status, html = self.app.handle("GET", f"/api/cases/{self.cid}/report", None, self.tokens["inversor"])
        self.assertEqual(status, 200)
        self.assertNotIn("Titular Ficticio Demo", html)

    def test_demo_roles_grant_role_permissions(self):
        self.assertEqual(self.app.handle("GET", "/api/portfolio", None, self.tokens["inversor"])[0], 200)
        self.assertEqual(self.app.handle("GET", "/api/portfolio", None, self.tokens["analista"])[0], 403)

    def test_seed_is_idempotent_for_accounts(self):
        provision(self.app)
        self.assertEqual(len(self.db.list_users()), 6)


if __name__ == "__main__":
    unittest.main()
