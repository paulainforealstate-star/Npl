"""Pruebas de la v0.5: acceso, sesiones, reglas de cobros y ofertas, versiones, auditoría y operación."""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from unittest import mock

from npldesk import admin, auth
from npldesk import api as api_module
from npldesk.api import App, make_handler
from npldesk.db import Database, StaleVersion
from tests.test_app import INPUT, NOTE, PASSWORD, TODAY, AppTestCase, provision

START = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)
BAD = "contraseña-incorrecta-x"


class ClockedTestCase(AppTestCase):
    """Igual que AppTestCase, pero con un reloj controlable para probar caducidades."""

    def setUp(self):
        self.db = Database(":memory:")
        self.now = START
        self.app = App(self.db, today=lambda: TODAY, clock=lambda: self.now)
        self.tokens = provision(self.app)

    def login(self, user, password=PASSWORD, ip="10.1.1.1"):
        return self.app.handle("POST", "/api/login", {"user": user, "password": password}, None, ip=ip)

    def confirm_all(self, cid, saldo=118500.25, fecha="20/10/2026"):
        for field, value in [("titular", "Eduardo Gabarri Céspedes"), ("finca", "93296"),
                             ("saldo_pendiente", saldo), ("fecha_nota", fecha)]:
            s, b = self.call("POST", f"/api/cases/{cid}/diligence/confirm", {"field": field, "value": value}, "analista")
            self.assertEqual(s, 200, b)


class AuthTests(ClockedTestCase):
    def test_login_returns_session_and_me_works(self):
        s, b = self.login("analista")
        self.assertEqual(s, 200)
        self.assertEqual(b["role"], "analista")
        s, me = self.app.handle("GET", "/api/me", None, b["token"])
        self.assertEqual((s, me["user"], me["role"]), (200, "analista", "analista"))

    def test_wrong_password_and_unknown_user_give_same_message(self):
        s1, b1 = self.login("analista", BAD)
        s2, b2 = self.login("no_existe", BAD)
        self.assertEqual((s1, s2), (401, 401))
        self.assertEqual(b1["error"], b2["error"])

    def test_account_locks_after_five_failures_even_with_right_password(self):
        for _ in range(5):
            self.assertEqual(self.login("analista", BAD, ip="5.5.5.5")[0], 401)
        self.assertEqual(self.login("analista", PASSWORD, ip="6.6.6.6")[0], 429)
        self.now += timedelta(minutes=16)
        self.assertEqual(self.login("analista", PASSWORD, ip="6.6.6.6")[0], 200)

    def test_address_is_limited_across_accounts(self):
        for i in range(20):
            self.login(f"desconocido{i}", BAD, ip="7.7.7.7")
        self.assertEqual(self.login("analista", PASSWORD, ip="7.7.7.7")[0], 429)
        self.now += timedelta(minutes=16)
        self.assertEqual(self.login("analista", PASSWORD, ip="7.7.7.7")[0], 200)

    def test_logout_revokes_token(self):
        token = self.login("analista")[1]["token"]
        self.assertEqual(self.app.handle("POST", "/api/logout", {}, token)[0], 200)
        self.assertEqual(self.app.handle("GET", "/api/me", None, token)[0], 401)

    def test_session_expires_after_eight_hours(self):
        token = self.login("analista")[1]["token"]
        self.now += timedelta(hours=7)
        self.assertEqual(self.app.handle("GET", "/api/me", None, token)[0], 200)
        self.now += timedelta(hours=2)
        self.assertEqual(self.app.handle("GET", "/api/me", None, token)[0], 401)

    def test_deactivated_user_loses_access_and_cannot_log_in(self):
        token = self.login("analista")[1]["token"]
        user = self.db.get_user_by_name("analista")
        self.db.update_user(user["id"], active=0)
        self.assertEqual(self.app.handle("GET", "/api/me", None, token)[0], 401)
        self.assertEqual(self.login("analista")[0], 401)

    def test_token_is_stored_only_as_digest(self):
        token = self.login("analista")[1]["token"]
        stored = [r["token_hash"] for r in self.db.conn.execute("SELECT token_hash FROM sessions")]
        self.assertIn(auth.token_digest(token), stored)
        self.assertNotIn(token, stored)

    def test_passwords_are_scrypt_hashed(self):
        stored = self.db.get_user_by_name("analista")["password_hash"]
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, stored)
        self.assertTrue(auth.verify_password(PASSWORD, stored))
        self.assertFalse(auth.verify_password(BAD, stored))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            auth.hash_password("corta")

    def test_wrong_role_is_403_and_no_session_is_401(self):
        self.assertEqual(self.call("GET", "/api/audit", None, "inversor")[0], 403)
        self.assertEqual(self.app.handle("GET", "/api/audit")[0], 401)


class PermissionMatrixTests(AppTestCase):
    def test_key_endpoints_by_role(self):
        cid = self.new_case()
        expectations = [
            ("GET", "/api/portfolio", ["admin", "responsable", "inversor", "auditor"]),
            ("GET", "/api/audit", ["admin", "auditor"]),
            ("GET", f"/api/cases/{cid}/report", ["admin", "analista", "responsable", "inversor", "auditor"]),
            ("POST", f"/api/cases/{cid}/offer", ["admin", "responsable"]),
            ("POST", f"/api/cases/{cid}/recoveries", ["admin", "responsable", "gestor_campo"]),
            ("POST", f"/api/cases/{cid}/visits", ["admin", "analista", "gestor_campo"]),
        ]
        body = {"amount": 1}
        for method, path, allowed in expectations:
            for role in auth.ROLES:
                status, _ = self.call(method, path, body if method == "POST" else None, role)
                if role in allowed:
                    self.assertNotIn(status, (401, 403), f"{role} {method} {path}")
                else:
                    self.assertEqual(status, 403, f"{role} {method} {path}")


class ValidationTests(AppTestCase):
    def valuation(self, **overrides):
        payload = {"rate": 0.05, "safety_margin": 0.1, "routes": json.loads(json.dumps(INPUT["routes"]))}
        payload.update(overrides)
        return self.call("POST", "/api/valuation", payload, "analista")

    def test_negative_sale_price_rejected(self):
        route = {"name": "negociación", "scenarios": [
            {"name": "base", "probability": 1.0, "sale_price": -50000, "time_years": 1}]}
        s, b = self.valuation(routes=[route])
        self.assertEqual(s, 400)
        self.assertIn("sale_price", b["error"])

    def test_boolean_is_not_a_number(self):
        route = {"name": "x", "scenarios": [{"name": "b", "probability": 1.0, "sale_price": True, "time_years": 1}]}
        self.assertEqual(self.valuation(routes=[route])[0], 400)

    def test_probability_above_one_rejected(self):
        route = {"name": "x", "scenarios": [{"name": "b", "probability": 1.5, "sale_price": 1, "time_years": 1}]}
        self.assertEqual(self.valuation(routes=[route])[0], 400)

    def test_time_bounds(self):
        for years in (0, 51):
            route = {"name": "x", "scenarios": [{"name": "b", "probability": 1.0, "sale_price": 1, "time_years": years}]}
            self.assertEqual(self.valuation(routes=[route])[0], 400, years)

    def test_rate_and_margin_bounds(self):
        self.assertEqual(self.valuation(rate=1.5)[0], 400)
        self.assertEqual(self.valuation(rate=-0.01)[0], 400)
        self.assertEqual(self.valuation(safety_margin=0.995)[0], 400)

    def test_negative_expected_value_is_flagged(self):
        route = {"name": "pérdida", "scenarios": [
            {"name": "b", "probability": 1.0, "sale_price": 1000, "costs": 5000, "time_years": 1}]}
        s, b = self.valuation(routes=[route])
        self.assertEqual(s, 200)
        self.assertEqual(b["ranking"][0]["max_price"], 0.0)
        self.assertTrue(b["ranking"][0]["max_price_floored"])
        self.assertIn("no genera valor", " ".join(b["warnings"]))
        self.assertEqual(b["analysis"]["pérdida"]["base"]["max_price"], 0.0)

    def test_case_creation_returns_value_warning(self):
        route = {"name": "venta ordenada", "scenarios": [
            {"name": "b", "probability": 1.0, "sale_price": 1000, "costs": 5000, "time_years": 1,
             "source": "s", "reviewed_at": "2026-10-01"}]}
        s, b = self.call("POST", "/api/cases", {"name": "P", "phase": "precontenciosa",
                                                 "input": {**INPUT, "routes": [route]}}, "analista")
        self.assertEqual(s, 201)
        self.assertTrue(any("no genera valor" in w for w in b["warnings"]))


class VersionTests(AppTestCase):
    def test_stale_version_is_409_and_current_version_passes(self):
        cid = self.new_case()
        s, case = self.call("GET", f"/api/cases/{cid}", None, "analista")
        v = case["version"]
        s, b = self.call("POST", f"/api/cases/{cid}/risks",
                         {"category": "registral", "level": "medio", "description": "a", "version": v}, "analista")
        self.assertEqual(s, 201, b)
        s, b = self.call("POST", f"/api/cases/{cid}/risks",
                         {"category": "registral", "level": "medio", "description": "b", "version": v}, "analista")
        self.assertEqual(s, 409)
        self.assertIn("modificado", b["error"])
        s, case = self.call("GET", f"/api/cases/{cid}", None, "analista")
        self.assertEqual(case["version"], v + 1)

    def test_writes_without_version_still_work(self):
        cid = self.new_case()
        s, _ = self.call("POST", f"/api/cases/{cid}/risks",
                         {"category": "registral", "level": "medio", "description": "c"}, "analista")
        self.assertEqual(s, 201)

    def test_database_rejects_lost_update(self):
        cid = self.new_case()
        version = self.db.get_case(cid)["version"]
        self.db.save_case(cid, expected_version=version, state={})
        with self.assertRaises(StaleVersion):
            self.db.save_case(cid, expected_version=version, state={})


class RecoveryRuleTests(ClockedTestCase):
    def offered_case(self, amount=80000):
        cid = self.ready_case()
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": amount}, "responsable")
        self.assertEqual(s, 201, b)
        return cid

    def test_recovery_before_offer_date_rejected(self):
        cid = self.offered_case()
        s, b = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 10, "date": "2026-10-01"}, "responsable")
        self.assertEqual(s, 409)
        self.assertIn("anterior", b["error"])

    def test_future_recovery_rejected(self):
        cid = self.offered_case()
        s, b = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 10, "date": "2026-12-01"}, "responsable")
        self.assertEqual(s, 400)
        self.assertIn("futura", b["error"])

    def test_recoveries_cannot_exceed_offer(self):
        cid = self.offered_case()
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/recoveries",
                                   {"amount": 60000, "date": "2026-10-30"}, "responsable")[0], 201)
        s, b = self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 30000, "date": "2026-10-30"}, "responsable")
        self.assertEqual(s, 409)
        self.assertIn("superarían", b["error"])

    def test_void_needs_reason_keeps_history_and_frees_room(self):
        cid = self.offered_case()
        self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 60000, "date": "2026-10-30"}, "responsable")
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/recoveries/0/void", {}, "responsable")[0], 400)
        s, _ = self.call("POST", f"/api/cases/{cid}/recoveries/0/void", {"motivo": "duplicado"}, "responsable")
        self.assertEqual(s, 200)
        s, case = self.call("GET", f"/api/cases/{cid}", None, "analista")
        rec = case["state"]["recoveries"][0]
        self.assertTrue(rec["anulado"])
        self.assertEqual(rec["motivo_anulacion"], "duplicado")
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/recoveries/0/void", {"motivo": "otra vez"}, "responsable")[0], 409)
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/recoveries", {"amount": 80000, "date": "2026-10-30"}, "responsable")[0], 201)
        s, k = self.call("GET", "/api/portfolio", None, "inversor")
        self.assertEqual(k["monetizado"], 80000)


class OfferHistoryTests(ClockedTestCase):
    def test_changing_offer_needs_reason_and_keeps_history(self):
        cid = self.ready_case()
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/offer", {"amount": 60000}, "responsable")[0], 201)
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 65000}, "responsable")
        self.assertEqual(s, 400)
        self.assertIn("motivo", b["error"])
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 65000, "motivo": "contraoferta"}, "responsable")
        self.assertEqual(s, 201)
        self.assertEqual(b["version"], 2)
        s, case = self.call("GET", f"/api/cases/{cid}", None, "analista")
        self.assertEqual([o["amount"] for o in case["state"]["offers"]], [60000, 65000])
        self.assertEqual(case["state"]["offer"]["amount"], 65000)

    def test_offer_limit_for_responsable_still_applies(self):
        cid = self.ready_case()
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 600000}, "responsable")
        self.assertEqual(s, 409)
        self.assertIn("límite de aprobación", b["error"])

    def test_irr_not_calculable_is_explained(self):
        cid = self.ready_case()
        s, b = self.call("POST", f"/api/cases/{cid}/offer", {"amount": 1_000_000_000}, "admin")
        self.assertEqual(s, 201, b)
        self.assertIsNone(b["planned_irr"])
        self.assertIn("no calculable", b["planned_irr_note"])
        self.assertTrue(any("no calculable" in w for w in b["warnings"]))


class PhaseRuleTests(ClockedTestCase):
    def test_moving_back_needs_reason(self):
        cid = self.new_case()
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/phase", {"phase": "judicial_sin_adjudicar"}, "analista")[0], 200)
        s, b = self.call("POST", f"/api/cases/{cid}/phase", {"phase": "precontenciosa"}, "analista")
        self.assertEqual(s, 400)
        s, _ = self.call("POST", f"/api/cases/{cid}/phase",
                         {"phase": "precontenciosa", "motivo": "el deudor retoma el pago"}, "analista")
        self.assertEqual(s, 200)
        s, case = self.call("GET", f"/api/cases/{cid}", None, "analista")
        last = case["state"]["phase_history"][-1]
        self.assertTrue(last["retroceso"])
        self.assertEqual(last["motivo"], "el deudor retoma el pago")
        self.assertEqual(last["by"], "analista")


class AuditTests(ClockedTestCase):
    def test_chain_verifies_and_records_who_and_where(self):
        self.app.handle("POST", "/api/cases", {"name": "A", "phase": "precontenciosa", "input": INPUT},
                        self.tokens["analista"], ip="203.0.113.9", request_id="req-123")
        s, v = self.app.handle("GET", "/api/audit/verify", None, self.tokens["auditor"])
        self.assertEqual(s, 200)
        self.assertTrue(v["ok"])
        self.assertGreater(v["comprobadas"], 0)
        row = self.db.list_audit(1)[0]
        self.assertEqual(row["ip"], "203.0.113.9")
        self.assertEqual(row["request_id"], "req-123")
        self.assertIsNotNone(row["user_id"])

    def test_rows_cannot_be_updated_or_deleted(self):
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.db.conn.execute("UPDATE audit SET user = 'nadie'")
        self.assertIn("inmutable", str(cm.exception))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("DELETE FROM audit")

    def test_tampering_is_detected(self):
        self.app.handle("POST", "/api/cases", {"name": "A", "phase": "precontenciosa", "input": INPUT},
                        self.tokens["analista"])
        self.app.handle("POST", "/api/cases", {"name": "B", "phase": "precontenciosa", "input": INPUT},
                        self.tokens["analista"])
        self.db.conn.execute("DROP TRIGGER audit_no_update")   # simula un manipulador con acceso a la base
        self.db.conn.execute("UPDATE audit SET user = 'otro' WHERE id = 1")
        self.db.conn.commit()
        result = self.db.verify_audit()
        self.assertFalse(result["ok"])
        self.assertEqual(result["primera_fila_alterada"], 1)

    def test_login_attempts_are_audited(self):
        self.login("analista", BAD)
        actions = [r["action"] for r in self.db.list_audit(10)]
        self.assertIn("login_fallido", actions)


class HealthAndHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        cls.app = App(cls.db, today=lambda: TODAY)
        cls.tokens = provision(cls.app)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, require_https=True))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.db.close()

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_health_checks_database(self):
        status, _, raw = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["db"], "ok")

    def test_plain_http_is_refused_but_health_is_open(self):
        self.assertEqual(self.get("/app.js")[0], 403)
        self.assertEqual(self.get("/api/cases", {"Authorization": "Bearer x"})[0], 403)
        self.assertEqual(self.get("/health")[0], 200)

    def test_https_requests_pass_and_carry_security_headers(self):
        status, headers, raw = self.get("/", {"X-Forwarded-Proto": "https"})
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("max-age", headers["Strict-Transport-Security"])
        self.assertIn("X-Request-Id", headers)
        self.assertNotIn(b"<script>", raw)          # nada de JavaScript en línea
        self.assertIn(b'src="/app.js"', raw)

    def test_static_files_have_their_types(self):
        _, headers, _ = self.get("/app.js", {"X-Forwarded-Proto": "https"})
        self.assertIn("javascript", headers["Content-Type"])
        _, headers, _ = self.get("/app.css", {"X-Forwarded-Proto": "https"})
        self.assertIn("text/css", headers["Content-Type"])

    def test_api_responses_are_not_cached(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/me",
                                     headers={"X-Forwarded-Proto": "https",
                                              "Authorization": f"Bearer {self.tokens['analista']}"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers["Cache-Control"], "no-store")


class HealthFailureTests(unittest.TestCase):
    def test_health_is_503_when_database_is_down(self):
        db = Database(":memory:")
        app = App(db, today=lambda: TODAY)
        db.close()
        self.assertEqual(app.handle("GET", "/health"), (503, {"status": "error", "db": "error"}))


class DemoGuardTests(unittest.TestCase):
    def test_demo_refuses_persistent_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"NPLDESK_DEMO": "1"}
            with mock.patch.dict(os.environ, env), self.assertRaises(SystemExit) as cm:
                api_module.run(host="127.0.0.1", port=0, db_path=os.path.join(tmp, "real.db"))
            self.assertIn(":memory:", str(cm.exception))
            self.assertFalse(os.path.exists(os.path.join(tmp, "real.db")))


class AdminCliTests(unittest.TestCase):
    def test_create_list_deactivate_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "npl.db")
            pw = "contraseña-de-administrador-2026"
            out = io.StringIO()
            with mock.patch("npldesk.admin.getpass.getpass", side_effect=[pw, pw]), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(admin.main(["--db", db_path, "create-user", "--user", "ana",
                                             "--name", "Ana", "--role", "analista"]), 0)
            with contextlib.redirect_stdout(out):
                self.assertEqual(admin.main(["--db", db_path, "list-users"]), 0)
                self.assertEqual(admin.main(["--db", db_path, "verify-audit"]), 0)
            self.assertIn("ana", out.getvalue())

            db = Database(db_path)
            app = App(db, today=lambda: TODAY)
            self.assertEqual(app.handle("POST", "/api/login", {"user": "ana", "password": pw}, None, ip="1.1.1.1")[0], 200)
            db.close()
            with mock.patch("sys.stdout", io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(admin.main(["--db", db_path, "deactivate", "--user", "ana"]), 0)
            db = Database(db_path)
            app = App(db, today=lambda: TODAY)
            self.assertEqual(app.handle("POST", "/api/login", {"user": "ana", "password": pw}, None, ip="1.1.1.1")[0], 401)
            db.close()

    def test_weak_password_is_refused_without_creating_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "npl.db")
            with mock.patch("npldesk.admin.getpass.getpass", side_effect=["corta", "corta"]), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(admin.main(["--db", db_path, "create-user", "--user", "x",
                                             "--name", "X", "--role", "auditor"]), 1)
            db = Database(db_path)
            self.assertEqual(db.count_users(), 0)
            db.close()

    def test_backup_copies_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "npl.db")
            dest = os.path.join(tmp, "copia.db")
            db = Database(db_path)
            db.create_user("u", "U", None, "auditor", auth.hash_password("contraseña-larga-123"))
            db.backup_to(dest)
            db.close()
            copy = Database(dest)
            self.assertEqual(copy.count_users(), 1)
            copy.close()


class CalendarTests(ClockedTestCase):
    def test_deadlines_show_whether_they_are_validated(self):
        cid = self.new_case()
        self.assertEqual(self.call("POST", f"/api/cases/{cid}/phase", {"phase": "judicial_sin_adjudicar"}, "analista")[0], 200)
        s, b = self.call("GET", f"/api/cases/{cid}/calendar", None, "analista")
        self.assertEqual(s, 200)
        self.assertTrue(b["calendar"])
        self.assertTrue(all(item["validado"] is False for item in b["calendar"]))
        self.assertTrue(all("EJEMPLO" in item["title"] for item in b["calendar"]))


if __name__ == "__main__":
    unittest.main()
