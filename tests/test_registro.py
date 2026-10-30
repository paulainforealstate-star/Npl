import unittest

from npldesk.api import App
from npldesk.db import Database


class RegistroTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def _app(self, open_registration):
        app = App(self.db)
        app.open_registration = open_registration
        return app

    def test_registro_cerrado_responde_404(self):
        app = self._app(False)
        status, _ = app.handle("POST", "/api/register", {"user": "ana_r", "password": "clave-larga-123"})
        self.assertEqual(status, 404)
        status, body = app.handle("GET", "/api/config")
        self.assertEqual(body, {"registro": False})

    def test_registro_abierto_crea_analista_que_puede_entrar(self):
        app = self._app(True)
        status, body = app.handle("POST", "/api/register",
                                  {"user": "ana_r", "name": "Ana R", "password": "clave-larga-123"})
        self.assertEqual(status, 201, body)
        status, body = app.handle("POST", "/api/login", {"user": "ana_r", "password": "clave-larga-123"})
        self.assertEqual(status, 200)
        self.assertEqual(body["role"], "analista")
        self.assertEqual(body["name"], "Ana R")
        status, body = app.handle("GET", "/api/config")
        self.assertEqual(body, {"registro": True})

    def test_registro_no_permite_elegir_rol(self):
        app = self._app(True)
        status, _ = app.handle("POST", "/api/register",
                               {"user": "root_x", "role": "admin", "password": "clave-larga-123"})
        self.assertEqual(status, 201)
        _, body = app.handle("POST", "/api/login", {"user": "root_x", "password": "clave-larga-123"})
        self.assertEqual(body["role"], "analista")

    def test_registro_valida_datos(self):
        app = self._app(True)
        cases = [
            {"user": "ab", "password": "clave-larga-123"},            # usuario corto
            {"user": "Ana Ruiz", "password": "clave-larga-123"},      # caracteres no válidos
            {"user": "ana_v", "password": "corta"},                   # contraseña corta
        ]
        for body in cases:
            status, _ = app.handle("POST", "/api/register", body)
            self.assertEqual(status, 400, body)

    def test_registro_no_repite_usuario(self):
        app = self._app(True)
        app.handle("POST", "/api/register", {"user": "ana_r", "password": "clave-larga-123"})
        status, _ = app.handle("POST", "/api/register", {"user": "ana_r", "password": "otra-clave-456"})
        self.assertEqual(status, 400)

    def test_registro_queda_en_auditoria(self):
        app = self._app(True)
        app.handle("POST", "/api/register", {"user": "ana_r", "password": "clave-larga-123"})
        acciones = [r["action"] for r in self.db.conn.execute("SELECT action FROM audit").fetchall()]
        self.assertIn("registro", acciones)


if __name__ == "__main__":
    unittest.main()
