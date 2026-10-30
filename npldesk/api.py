"""API de NPLDesk. La lógica vive en `App.handle`, que se puede probar sin servidor HTTP.

Códigos: 200/201 correcto · 400 datos no válidos · 401 sin sesión o credenciales incorrectas ·
403 sin permiso · 404 no encontrado · 409 regla de negocio o versión desactualizada ·
429 demasiados intentos de acceso · 503 base de datos no disponible.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from npldesk import auth, diligence, guide, pdftext, risks, tasks
from npldesk import validation as V
from npldesk.config import DEFAULTS, RISK_CATALOG
from npldesk.db import Database, StaleVersion
from npldesk.engine import Route, Scenario, recommend
from npldesk.montecarlo import simulate_route
from npldesk.phases import allowed_routes, check_routes_for_phase
from npldesk.portfolio import kpis
from npldesk.report import case_report
from npldesk.scenarios import analyze, irr_for_price
from npldesk.screening import screen
from npldesk.workflow import (
    GateError,
    change_phase,
    check_offer_limit,
    check_recovery,
    offer_blockers,
    register_decision,
)

WEB_DIR = Path(__file__).parent / "web"
JSON = "application/json; charset=utf-8"
HTML = "text/html; charset=utf-8"
CSS = "text/css; charset=utf-8"
JS = "application/javascript; charset=utf-8"
STATIC = {"/": ("index.html", HTML), "/app.js": ("app.js", JS), "/app.css": ("app.css", CSS)}

# La página no admite JavaScript en línea: sin `unsafe-inline` en script-src.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
    "Cache-Control": "no-store",
}
MAX_BODY_BYTES = 1_000_000        # tamaño máximo del cuerpo de una petición
MAX_PDF_BODY_BYTES = 8_000_000   # un PDF de 5 MB en base64 y con JSON ocupa unos 6,8 MB
PDF_EXTRACT_PATH = "/diligence/extract-pdf"
MAX_MONTECARLO = 200_000          # iteraciones máximas por vía (evita bloquear el servidor)
_DUMMY = auth.DUMMY_HASH

log = logging.getLogger("npldesk")


class NotFound(Exception):
    pass


class TooManyAttempts(Exception):
    pass


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _payload(ctx: dict) -> dict:
    """Cuerpo sin el campo de control de versión."""
    return {k: v for k, v in ctx["body"].items() if k != "version"}


# ---- valoración ---------------------------------------------------------------

def parse_routes(payload: dict) -> list[Route]:
    if not isinstance(payload.get("routes"), list) or not payload["routes"]:
        raise ValueError("'routes' debe ser una lista no vacía")
    routes = []
    for r in payload["routes"]:
        if not isinstance(r, dict):
            raise ValueError("Cada vía debe ser un objeto")
        if not r.get("name"):
            raise ValueError("Cada vía necesita un 'name'")
        if not isinstance(r.get("scenarios"), list) or not r["scenarios"]:
            raise ValueError(f"La vía '{r['name']}' no tiene escenarios")
        for s in r["scenarios"]:
            if not isinstance(s, dict):
                raise ValueError(f"Los escenarios de '{r['name']}' deben ser objetos")
        scenarios = tuple(
            Scenario(
                name=str(s.get("name", "escenario")),
                probability=V.probability(s["probability"]),
                sale_price=V.amount(s["sale_price"], "sale_price"),
                costs=V.amount(s.get("costs", 0), "costs"),
                charges=V.amount(s.get("charges", 0), "charges"),
                debtor_share=V.amount(s.get("debtor_share", 0), "debtor_share"),
                time_years=V.years(s["time_years"]),
            )
            for s in r["scenarios"]
        )
        routes.append(Route(name=str(r["name"]), scenarios=scenarios))
    return routes


def _result_row(route: str, ve: float, max_price: float, warnings: list[str]) -> dict:
    """Si la vía no genera valor, el precio máximo se fija en 0 y se avisa."""
    if ve < 0:
        warnings.append(f"La vía '{route}' no genera valor a la tasa exigida")
        return {"route": route, "expected_value": ve, "max_price": 0.0, "max_price_floored": True}
    return {"route": route, "expected_value": ve, "max_price": max_price, "max_price_floored": False}


def valuate(payload: dict) -> dict:
    rate = V.rate(payload["rate"])
    margin = V.margin(payload.get("safety_margin", 0.0))
    phase = payload.get("phase")
    routes = parse_routes(payload)
    if phase:
        check_routes_for_phase(phase, [r.name for r in routes])
    rec = recommend(routes, rate=rate, safety_margin=margin)
    warnings: list[str] = []
    ranking = [_result_row(r.route, r.expected_value, r.max_price, warnings) for r in rec.ranking]
    analysis = {}
    for r in routes:
        per = analyze(r, rate, margin)
        for item in per.values():
            floored = item["expected_value"] < 0
            item["max_price"] = 0.0 if floored else item["max_price"]
            item["max_price_floored"] = floored
        analysis[r.name] = per
    response = {
        "phase": phase,
        "best": ranking[0],
        "ranking": ranking,
        "analysis": analysis,
        "warnings": warnings,
    }
    iterations = int(payload.get("montecarlo_iterations", 0))
    if not 0 <= iterations <= MAX_MONTECARLO:
        raise ValueError(f"'montecarlo_iterations' debe estar entre 0 y {MAX_MONTECARLO:,}")
    if iterations:
        seed = payload.get("seed", 42)
        response["montecarlo"] = {
            r.name: simulate_route(r, rate, iterations=iterations, seed=seed).__dict__
            for r in routes
        }
    return response


def source_warnings(routes: list[dict]) -> list[str]:
    """Cada parámetro debe llevar fuente y fecha de revisión (especificación)."""
    out = []
    for r in routes:
        for s in r.get("scenarios", []):
            if not s.get("source") or not s.get("reviewed_at"):
                out.append(f"Escenario '{s.get('name', '?')}' de '{r.get('name')}' sin fuente o fecha de revisión")
    return out


# ---- aplicación -----------------------------------------------------------------

class App:
    def __init__(self, db: Database, today=None, clock=None) -> None:
        self.db = db
        self.today = today or date.today
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # Las peticiones se serializan con este bloqueo porque la conexión SQLite es una sola.
        # Con PostgreSQL (fase 3.1) se sustituye por control de versiones puro.
        self._lock = threading.RLock()
        self._meta: dict = {"ip": None, "request_id": None}
        self._ip_failures: dict[str, list[datetime]] = {}
        self.routes = [
            ("POST", r"^/api/login$", self.login),
            ("POST", r"^/api/logout$", self.logout),
            ("GET", r"^/api/me$", self.me),
            ("GET", r"^/api/cases$", self.cases_list),
            ("POST", r"^/api/cases$", self.cases_create),
            ("GET", r"^/api/cases/(\d+)$", self.case_get),
            ("DELETE", r"^/api/cases/(\d+)$", self.case_delete),
            ("POST", r"^/api/cases/(\d+)/screen$", self.case_screen),
            ("POST", r"^/api/cases/(\d+)/diligence/extract$", self.diligence_extract),
            ("POST", r"^/api/cases/(\d+)/diligence/extract-pdf$", self.diligence_extract_pdf),
            ("POST", r"^/api/cases/(\d+)/diligence/confirm$", self.diligence_confirm),
            ("POST", r"^/api/cases/(\d+)/risks$", self.risk_add),
            ("POST", r"^/api/cases/(\d+)/risks/(\d+)/approve$", self.risk_approve),
            ("POST", r"^/api/cases/(\d+)/decision$", self.decision),
            ("POST", r"^/api/cases/(\d+)/offer$", self.offer),
            ("POST", r"^/api/cases/(\d+)/offer/preview$", self.offer_preview),
            ("POST", r"^/api/cases/(\d+)/phase$", self.phase),
            ("POST", r"^/api/cases/(\d+)/recoveries$", self.recovery),
            ("POST", r"^/api/cases/(\d+)/recoveries/(\d+)/void$", self.recovery_void),
            ("GET", r"^/api/cases/(\d+)/tasks$", self.task_list),
            ("POST", r"^/api/cases/(\d+)/tasks$", self.task_add),
            ("POST", r"^/api/cases/(\d+)/tasks/(\d+)/status$", self.task_status),
            ("GET", r"^/api/cases/(\d+)/calendar$", self.calendar),
            ("GET", r"^/api/cases/(\d+)/visits$", self.visit_list),
            ("POST", r"^/api/cases/(\d+)/visits$", self.visit_add),
            ("GET", r"^/api/cases/(\d+)/report$", self.report),
            ("GET", r"^/api/portfolio$", self.portfolio),
            ("GET", r"^/api/audit$", self.audit_list),
            ("GET", r"^/api/audit/verify$", self.audit_verify),
            ("POST", r"^/api/valuation$", self.valuation),
            ("GET", r"^/api/catalog$", self.catalog),
        ]

    # -- despacho -------------------------------------------------------------

    def handle(self, method: str, path: str, body: dict | None = None,
               token: str | None = None, ip: str | None = None,
               request_id: str | None = None) -> tuple[int, object]:
        if method == "GET" and path == "/":
            return 200, (WEB_DIR / "index.html").read_bytes()
        if method == "GET" and path == "/health":
            return self._health()
        ctx = {"token": token, "body": body or {}}
        with self._lock:
            self._meta = {"ip": ip, "request_id": request_id or uuid.uuid4().hex[:16]}
            return self._route(method, path, ctx)

    def _health(self) -> tuple[int, dict]:
        try:
            with self._lock:
                self.db.ping()
            return 200, {"status": "ok", "db": "ok"}
        except Exception:
            log.exception("Fallo de comprobación de la base de datos")
            return 503, {"status": "error", "db": "error"}

    def _route(self, method: str, path: str, ctx: dict) -> tuple[int, object]:
        for m, rx, fn in self.routes:
            if m != method:
                continue
            match = re.match(rx, path)
            if not match:
                continue
            try:
                return fn(ctx, *match.groups())
            except auth.Unauthorized as e:
                return 401, {"error": str(e)}
            except PermissionError as e:
                return 403, {"error": str(e)}
            except NotFound as e:
                return 404, {"error": str(e) or "no encontrado"}
            except TooManyAttempts as e:
                return 429, {"error": str(e)}
            except StaleVersion as e:
                return 409, {"error": str(e)}
            except GateError as e:   # antes que ValueError: es una subclase
                return 409, {"error": str(e)}
            except KeyError as e:
                return 400, {"error": f"Falta el campo {e}"}
            except LookupError as e:
                return 404, {"error": str(e) or "no encontrado"}
            except (ValueError, TypeError) as e:
                return 400, {"error": str(e)}
            except Exception:
                # Un fallo inesperado no debe cerrar la conexión sin respuesta.
                log.exception("Error no controlado en %s %s", method, path)
                return 500, {"error": "Error interno del servidor"}
        return 404, {"error": "no encontrado"}

    # -- sesiones y acceso ------------------------------------------------------

    def _session(self, ctx: dict) -> dict:
        token = ctx["token"]
        if not token:
            raise auth.Unauthorized("Sesión no válida o ausente")
        row = self.db.get_session(auth.token_digest(token))
        now = self.clock()
        if (row is None or row["revoked"] or not row["active"]
                or datetime.fromisoformat(row["expires_at"]) <= now):
            raise auth.Unauthorized("Sesión no válida o caducada")
        return {"id": row["user_id"], "user": row["username"],
                "name": row["full_name"], "role": row["role"]}

    def _auth(self, ctx: dict, perm: str) -> dict:
        user = self._session(ctx)
        auth.check_permission(user["role"], perm)
        return user

    def _ip_blocked(self, ip: str, now: datetime) -> bool:
        cfg = DEFAULTS["security"]
        window = timedelta(minutes=cfg["ip_window_minutes"])
        recent = [t for t in self._ip_failures.get(ip, []) if now - t < window]
        self._ip_failures[ip] = recent
        return len(recent) >= cfg["ip_max_failures"]

    def _audit_auth(self, action: str, username: str, user_id: int | None, detail: dict) -> None:
        self.db.add_audit(username, action, "auth", None, detail, user_id=user_id,
                          ip=self._meta["ip"], request_id=self._meta["request_id"])

    def login(self, ctx: dict) -> tuple[int, dict]:
        b = ctx["body"]
        username = str(b.get("user", "")).strip()[:100]
        password = str(b.get("password", ""))
        ip = self._meta["ip"] or "desconocida"
        cfg = DEFAULTS["security"]
        now = self.clock()
        if self._ip_blocked(ip, now):
            raise TooManyAttempts("Demasiados intentos fallidos desde esta dirección. Espera unos minutos.")
        row = self.db.get_user_by_name(username) if username else None
        locked_until = row["locked_until"] if row else None
        if locked_until and datetime.fromisoformat(locked_until) > now:
            raise TooManyAttempts("Demasiados intentos fallidos en esta cuenta. Espera unos minutos.")

        # Se verifica siempre una contraseña, exista o no el usuario.
        valid = auth.verify_password(password, row["password_hash"] if row else _DUMMY)
        if row is None or not row["active"] or not valid:
            self._ip_failures.setdefault(ip, []).append(now)
            if row is not None:
                fails = row["failed_attempts"] + 1
                lock = now + timedelta(minutes=cfg["account_lock_minutes"])
                if fails >= cfg["account_max_failures"]:
                    self.db.update_user(row["id"], failed_attempts=0, locked_until=_iso(lock))
                else:
                    self.db.update_user(row["id"], failed_attempts=fails)
            self._audit_auth("login_fallido", username, row["id"] if row else None, {})
            raise auth.Unauthorized("Usuario o contraseña incorrectos")

        self.db.update_user(row["id"], failed_attempts=0, locked_until=None)
        token = auth.new_session_token()
        expires = now + timedelta(hours=cfg["session_hours"])
        self.db.create_session(auth.token_digest(token), row["id"], _iso(now), _iso(expires))
        self._audit_auth("login", row["username"], row["id"], {})
        return 200, {"token": token, "expires_at": _iso(expires), "user": row["username"],
                     "name": row["full_name"], "role": row["role"]}

    def logout(self, ctx: dict) -> tuple[int, dict]:
        user = self._session(ctx)
        self.db.revoke_session(auth.token_digest(ctx["token"]))
        self._audit_auth("logout", user["user"], user["id"], {})
        return 200, {"ok": True}

    def me(self, ctx: dict) -> tuple[int, dict]:
        return 200, self._session(ctx)

    # -- utilidades -------------------------------------------------------------

    def _case(self, case_id: str) -> dict:
        case = self.db.get_case(int(case_id))
        if case is None:
            raise NotFound("expediente no encontrado")
        return case

    def _write_case(self, case_id: str, body: dict) -> dict:
        """Carga un expediente para modificarlo. Si la petición trae `version`, debe coincidir."""
        case = self._case(case_id)
        if "version" in body and int(body["version"]) != case["version"]:
            raise GateError("El expediente se ha modificado desde que lo abriste. Recárgalo y repite la acción")
        return case

    def _audit(self, user: dict, action: str, case_id: int | None, detail: dict | None = None) -> None:
        self.db.add_audit(user["user"], action, "case", str(case_id) if case_id else None, detail,
                          user_id=user["id"], ip=self._meta["ip"], request_id=self._meta["request_id"])

    def _save_state(self, case: dict, state: dict) -> None:
        self.db.save_case(case["id"], state=state, expected_version=case["version"])

    # -- expedientes ------------------------------------------------------------

    def cases_list(self, ctx):
        self._auth(ctx, "cases:read")
        rows = self.db.list_cases()
        for row in rows:
            full = self.db.get_case(row["id"])
            declared = full["input"].get("declared_saldo")
            row["estado"] = guide.status(full["state"], declared, self.today())
            offer = full["state"].get("offer")
            row["oferta"] = offer["amount"] if offer else None
            row["cobrado"] = guide.collected(full["state"])
        # Lo que pide atención primero: bloqueados, luego listos para ofertar, en curso y cerrados.
        rows.sort(key=lambda r: (guide.PRIORITY[r["estado"]], -r["id"]))
        return 200, {"cases": rows}

    def cases_create(self, ctx):
        user = self._auth(ctx, "cases:write")
        b = _payload(ctx)
        name = str(b.get("name", "")).strip()
        phase = str(b.get("phase", "")).strip()
        if not name:
            raise ValueError("'name' es obligatorio")
        if len(name) > 200:
            raise ValueError("'name' no puede superar 200 caracteres")
        if phase not in DEFAULTS["phases"]:
            raise ValueError(f"Fase desconocida '{phase}'")
        inp = b.get("input")
        if not isinstance(inp, dict):
            raise ValueError("'input' debe ser un objeto")
        result = valuate({**inp, "phase": phase})
        if "declared_saldo" in inp:
            V.amount(inp["declared_saldo"], "declared_saldo")
        warnings = source_warnings(inp["routes"]) + result["warnings"]
        state: dict = {}
        if "screening" in inp:
            state["screening"] = screen(inp["screening"])
        change_phase(state, phase, self.today())
        case_id = self.db.create_case(name, phase, inp, result, state)
        self._audit(user, "crear_expediente", case_id, {"name": name, "phase": phase})
        return 201, {"id": case_id, "warnings": warnings, "screening": state.get("screening")}

    def case_get(self, ctx, case_id):
        self._auth(ctx, "cases:read")
        case = self._case(case_id)
        cid = int(case_id)
        case["tasks"] = self.db.list_tasks(cid)
        case["visits"] = self.db.list_visits(cid)
        # Lo que falta para ofertar, para que la pantalla lo muestre sin tener que probar.
        declared = case["input"].get("declared_saldo")
        case["alerts"] = diligence.alerts(case["state"], declared, self.today())
        case["blockers"] = offer_blockers(case["state"], declared, self.today())
        case["steps"] = guide.steps(case["state"], declared, self.today())
        case["estado"] = guide.status(case["state"], declared, self.today())
        case["cobrado"] = guide.collected(case["state"])
        return 200, case

    def case_delete(self, ctx, case_id):
        user = self._auth(ctx, "cases:delete")
        if not self.db.delete_case(int(case_id)):
            raise NotFound("expediente no encontrado")
        self._audit(user, "borrar_expediente", int(case_id))
        return 200, {"deleted": True}

    # -- cribado ----------------------------------------------------------------

    def case_screen(self, ctx, case_id):
        user = self._auth(ctx, "screening:write")
        case = self._write_case(case_id, ctx["body"])
        data = _payload(ctx) or case["input"].get("screening")
        if not data:
            raise ValueError("No hay datos de cribado")
        state = case["state"]
        state["screening"] = screen(data)
        self._save_state(case, state)
        self._audit(user, "cribado", case["id"], state["screening"])
        return 200, state["screening"]

    # -- due diligence ------------------------------------------------------------

    def diligence_extract(self, ctx, case_id):
        user = self._auth(ctx, "diligence:write")
        b = _payload(ctx)
        case = self._write_case(case_id, ctx["body"])
        items = diligence.extract(str(b.get("document", "documento")), str(b.get("text", "")))
        state = case["state"]
        diligence.add_candidates(state, items)
        self._save_state(case, state)
        declared = case["input"].get("declared_saldo")
        self._audit(user, "extraer_diligencia", case["id"], {"campos": [i["field"] for i in items]})
        return 200, {
            "found": items,
            "alerts": diligence.alerts(state, declared, self.today()),
            "missing": diligence.missing_required(state),
        }

    def diligence_extract_pdf(self, ctx, case_id):
        user = self._auth(ctx, "diligence:write")
        b = _payload(ctx)
        case = self._write_case(case_id, ctx["body"])
        name = str(b.get("document") or "documento.pdf").strip()[:200]
        raw = b.get("pdf_base64")
        if not isinstance(raw, str) or not raw:
            raise ValueError("Falta 'pdf_base64' con el contenido del PDF")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("El contenido del PDF no está bien codificado (base64)")
        if not pdftext.available():
            return 503, {"error": "La lectura de PDF no está disponible en este servidor: falta la librería pypdf"}
        text = pdftext.extract_text(data)
        state = case["state"]
        items = diligence.extract(name, text)
        diligence.add_candidates(state, items)
        self._save_state(case, state)
        declared = case["input"].get("declared_saldo")
        self._audit(user, "extraer_diligencia", case["id"],
                    {"campos": [i["field"] for i in items], "fuente": "pdf", "documento": name})
        return 200, {
            "found": items,
            "text": text,
            "alerts": diligence.alerts(state, declared, self.today()),
            "missing": diligence.missing_required(state),
        }

    def diligence_confirm(self, ctx, case_id):
        user = self._auth(ctx, "diligence:write")
        b = _payload(ctx)
        case = self._write_case(case_id, ctx["body"])
        state = case["state"]
        if b.get("manual"):
            diligence.confirm_manual(state, b["field"], b.get("value"), user["user"], self.today())
        else:
            diligence.confirm(state, b["field"], b["value"], user["user"], self.today())
        self._save_state(case, state)
        self._audit(user, "confirmar_diligencia", case["id"], {"campo": b["field"]})
        return 200, {"missing": diligence.missing_required(state)}

    # -- riesgos --------------------------------------------------------------------

    def risk_add(self, ctx, case_id):
        user = self._auth(ctx, "risks:write")
        b = _payload(ctx)
        case = self._write_case(case_id, ctx["body"])
        state = case["state"]
        index = risks.add_risk(state, b["category"], str(b["description"]), b["level"], b.get("norm_ref"))
        self._save_state(case, state)
        self._audit(user, "añadir_riesgo", case["id"], {"nivel": b["level"]})
        return 201, {"index": index}

    def risk_approve(self, ctx, case_id, index):
        user = self._auth(ctx, "risks:approve")
        case = self._write_case(case_id, ctx["body"])
        state = case["state"]
        risks.approve(state, int(index), user["user"])
        self._save_state(case, state)
        self._audit(user, "aprobar_riesgo", case["id"], {"indice": int(index)})
        return 200, {"approved_by": user["user"]}

    # -- decisión, oferta, fase, cobros ------------------------------------------

    def decision(self, ctx, case_id):
        user = self._auth(ctx, "decision:write")
        b = _payload(ctx)
        case = self._write_case(case_id, ctx["body"])
        chosen = str(b["chosen"])
        result = case["result"] or {}
        ranked = [r["route"] for r in result.get("ranking", [])]
        if chosen not in ranked:
            raise ValueError(f"La vía '{chosen}' no está en la valoración")
        check_routes_for_phase(case["phase"], [chosen])
        state = case["state"]
        if state.get("offer"):
            raise GateError("Ya hay una oferta registrada: la decisión no puede cambiar")
        register_decision(state, result["best"]["route"], chosen,
                          str(b.get("justification", "")), user["user"], self.today())
        self._save_state(case, state)
        self._audit(user, "decision", case["id"], {"elegida": chosen})
        return 200, state["decision"]

    def offer(self, ctx, case_id):
        user = self._auth(ctx, "offer:write")
        b = _payload(ctx)
        amount = V.amount(b["amount"], "amount", positive=True)
        check_offer_limit(user["role"], amount)
        case = self._write_case(case_id, ctx["body"])
        state = case["state"]
        if state.get("recoveries"):
            raise GateError("Ya hay cobros registrados: la oferta no se puede cambiar")
        declared = case["input"].get("declared_saldo")
        blockers = offer_blockers(state, declared, self.today())
        if blockers:
            raise GateError("; ".join(blockers))
        chosen = state["decision"]["chosen"]
        route = next(r for r in parse_routes(case["input"]) if r.name == chosen)
        motivo = str(b.get("motivo", "")).strip()
        offers = state.setdefault("offers", [])
        if not offers and state.get("offer"):      # expedientes de la v0.4
            offers.append(state["offer"])
        if state.get("offer") and not motivo:
            raise ValueError("Para cambiar una oferta registrada debes indicar el motivo")

        planned = irr_for_price(amount, route)
        # Precio máximo de la vía según la valoración guardada. Superarlo no bloquea
        # (hay operaciones que lo justifican), pero queda marcado y avisado.
        max_price = next((r["max_price"] for r in (case["result"] or {}).get("ranking", [])
                          if r["route"] == chosen), None)
        over = max_price is not None and amount > max_price
        note = None
        warnings = []
        if over:
            warnings.append(f"La oferta supera el precio máximo de la vía ({max_price:,.0f} €)")
        if planned is None:
            note = "no calculable: los flujos no cambian de signo"
            warnings.append(f"TIR prevista {note}")
        entry = {
            "version": len(offers) + 1,
            "amount": amount,
            "route": chosen,
            "by": user["user"],
            "date": self.today().isoformat(),
            "planned_irr": planned,
            "planned_irr_note": note,
            "max_price": max_price,
            "over_max_price": over,
            "motivo": motivo or None,
        }
        offers.append(entry)
        state["offer"] = entry
        self._save_state(case, state)
        self._audit(user, "oferta", case["id"], {"importe": amount, "sobre_precio_maximo": over,
                                                  "version": entry["version"]})
        return 201, {**entry, "warnings": warnings}

    def offer_preview(self, ctx, case_id):
        """Calcula la TIR y la distancia al precio máximo de un importe sin guardar nada."""
        user = self._auth(ctx, "cases:read")
        b = _payload(ctx)
        amount = V.amount(b.get("amount"), "amount", positive=True)
        case = self._case(case_id)
        state = case["state"]
        chosen = (state.get("decision") or {}).get("chosen")
        declared = case["input"].get("declared_saldo")
        out = {"amount": amount, "route": chosen, "planned_irr": None, "planned_irr_note": None,
               "max_price": None, "max_price_floored": False, "distance": None, "over_max_price": False,
               "blockers": offer_blockers(state, declared, self.today()), "approval_ok": True,
               "message": None}
        try:
            check_offer_limit(user["role"], amount)
        except GateError:
            out["approval_ok"] = False
        if not chosen:
            out["message"] = "Registra la decisión para ver la TIR prevista y el precio máximo."
            return 200, out
        route = next((r for r in parse_routes(case["input"]) if r.name == chosen), None)
        if route is None:
            raise ValueError(f"La vía '{chosen}' ya no figura en la valoración")
        ranked = next((r for r in (case["result"] or {}).get("ranking", []) if r["route"] == chosen), None)
        max_price = ranked["max_price"] if ranked else None
        out["max_price_floored"] = bool(ranked and ranked.get("max_price_floored"))
        planned = irr_for_price(amount, route)
        out["planned_irr"] = planned
        if planned is None:
            out["planned_irr_note"] = "no calculable: los flujos no cambian de signo"
        if max_price is not None:
            out["max_price"] = max_price
            out["distance"] = max_price - amount
            out["over_max_price"] = amount > max_price
        return 200, out

    def catalog(self, ctx):
        self._auth(ctx, "cases:read")
        return 200, {"risk_catalog": RISK_CATALOG, "risk_categories": DEFAULTS["risk_categories"],
                     "risk_levels": DEFAULTS["risk_levels"], "phases": DEFAULTS["phases"]}

    def phase(self, ctx, case_id):
        user = self._auth(ctx, "phase:write")
        b = _payload(ctx)
        phase = str(b["phase"])
        if phase not in DEFAULTS["phases"]:
            raise ValueError(f"Fase desconocida '{phase}'")
        case = self._write_case(case_id, ctx["body"])
        if phase == case["phase"]:
            return 200, {"phase": phase}
        order = DEFAULTS["phases"]
        backwards = order.index(phase) < order.index(case["phase"])
        motivo = str(b.get("motivo", "")).strip()
        if backwards and not motivo:
            raise ValueError("Retroceder de fase exige un motivo")
        state = case["state"]
        decision = state.get("decision")
        if decision and decision["chosen"] not in allowed_routes(phase):
            raise GateError(
                f"La vía elegida ('{decision['chosen']}') no es viable en la fase '{phase}'. "
                "Registra antes una nueva decisión"
            )
        change_phase(state, phase, self.today(), motivo=motivo or None,
                     user=user["user"], backwards=backwards)
        self.db.save_case(case["id"], phase=phase, state=state, expected_version=case["version"])
        self._audit(user, "cambio_fase", case["id"], {"fase": phase, "retroceso": backwards,
                                                       "motivo": motivo or None})
        return 200, {"phase": phase}

    def recovery(self, ctx, case_id):
        user = self._auth(ctx, "recovery:write")
        b = _payload(ctx)
        amount = V.amount(b["amount"], "amount", positive=True)
        when = V.iso_date(b["date"], "date") if b.get("date") else self.today().isoformat()
        case = self._write_case(case_id, ctx["body"])
        state = case["state"]
        if not state.get("offer"):
            raise GateError("No hay oferta registrada: no se pueden registrar cobros")
        check_recovery(state, amount, when, self.today().isoformat())
        state.setdefault("recoveries", []).append(
            {"amount": amount, "date": when, "by": user["user"],
             "anulado": False, "motivo_anulacion": None})
        self._save_state(case, state)
        self._audit(user, "cobro", case["id"], {"importe": amount})
        return 201, {"recoveries": len(state["recoveries"])}

    def recovery_void(self, ctx, case_id, index):
        """Los cobros no se borran: se anulan con motivo y quedan en el historial."""
        user = self._auth(ctx, "recovery:write")
        motivo = str(_payload(ctx).get("motivo", "")).strip()
        if not motivo:
            raise ValueError("Anular un cobro exige un motivo")
        case = self._write_case(case_id, ctx["body"])
        state = case["state"]
        recs = state.get("recoveries", [])
        i = int(index)
        if not 0 <= i < len(recs):
            raise NotFound("cobro no encontrado")
        if recs[i].get("anulado"):
            raise GateError("El cobro ya está anulado")
        recs[i].update(anulado=True, motivo_anulacion=motivo, anulado_por=user["user"],
                       anulado_en=self.today().isoformat())
        self._save_state(case, state)
        self._audit(user, "anular_cobro", case["id"], {"indice": i, "motivo": motivo})
        return 200, {"anulado": i}

    # -- tareas y calendario ------------------------------------------------------

    def task_list(self, ctx, case_id):
        self._auth(ctx, "cases:read")
        self._case(case_id)
        items = self.db.list_tasks(int(case_id))
        return 200, {
            "tasks": items,
            "ready": [t["id"] for t in tasks.ready_tasks(items)],
            "overdue": [t["id"] for t in tasks.overdue_tasks(items, self.today())],
        }

    def task_add(self, ctx, case_id):
        user = self._auth(ctx, "tasks:write")
        self._case(case_id)
        b = _payload(ctx)
        title = str(b["title"]).strip()
        if not title:
            raise ValueError("'title' no puede estar vacío")
        due = V.iso_date(b["due_date"], "due_date") if b.get("due_date") else None
        deps = [int(d) for d in b.get("depends_on", [])]
        existing = {t["id"] for t in self.db.list_tasks(int(case_id))}
        unknown = [d for d in deps if d not in existing]
        if unknown:
            raise ValueError(f"Las tareas {unknown} no existen en este expediente")
        task_id = self.db.add_task(int(case_id), title, b.get("owner"), due, deps)
        self._audit(user, "añadir_tarea", int(case_id), {"tarea": task_id})
        return 201, {"id": task_id}

    def task_status(self, ctx, case_id, task_id):
        user = self._auth(ctx, "tasks:write")
        self._case(case_id)
        status = str(_payload(ctx)["status"])
        if status not in ("pendiente", "completada"):
            raise ValueError("Estado no válido")
        items = self.db.list_tasks(int(case_id))
        task = next((t for t in items if t["id"] == int(task_id)), None)
        if task is None:
            raise NotFound("tarea no encontrada")
        done = {t["id"] for t in items if t["status"] == "completada"}
        if status == "completada" and not all(d in done for d in task["depends_on"]):
            raise GateError("Hay tareas previas sin completar")
        self.db.set_task_status(int(task_id), int(case_id), status)
        self._audit(user, "estado_tarea", int(case_id), {"tarea": int(task_id), "estado": status})
        return 200, {"id": int(task_id), "status": status}

    def calendar(self, ctx, case_id):
        self._auth(ctx, "cases:read")
        case = self._case(case_id)
        return 200, {"calendar": tasks.deadline_calendar(case["phase"], self.today())}

    # -- visitas ----------------------------------------------------------------------

    def visit_list(self, ctx, case_id):
        self._auth(ctx, "cases:read")
        self._case(case_id)
        return 200, {"visits": self.db.list_visits(int(case_id))}

    def visit_add(self, ctx, case_id):
        user = self._auth(ctx, "visits:write")
        self._case(case_id)
        b = _payload(ctx)
        lat, lon = V.number(b["lat"], "lat"), V.number(b["lon"], "lon")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Coordenadas fuera de rango")
        when = V.iso_date(b["date"], "date") if b.get("date") else self.today().isoformat()
        visit_id = self.db.add_visit(int(case_id), when, lat, lon,
                                     b.get("photo_ref"), b.get("notes"))
        self._audit(user, "visita", int(case_id), {"visita": visit_id})
        return 201, {"id": visit_id}

    # -- informes, cartera, auditoría ---------------------------------------------

    def report(self, ctx, case_id):
        user = self._auth(ctx, "report:read")
        case = self._case(case_id)
        anonymized = user["role"] == "inversor"
        self._audit(user, "informe", int(case_id), {"anonimizado": anonymized})
        return 200, case_report(case, anonymized=anonymized)

    def portfolio(self, ctx):
        self._auth(ctx, "portfolio:read")
        return 200, kpis(self.db.all_cases(), self.today())

    def audit_list(self, ctx):
        self._auth(ctx, "audit:read")
        return 200, {"audit": self.db.list_audit()}

    def audit_verify(self, ctx):
        self._auth(ctx, "audit:read")
        return 200, self.db.verify_audit()

    def valuation(self, ctx):
        self._auth(ctx, "valuation:run")
        return 200, valuate(ctx["body"])


# ---- servidor HTTP ----------------------------------------------------------------

def make_handler(app: App, require_https: bool = False):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload, request_id: str, ctype: str | None = None) -> None:
            if isinstance(payload, bytes):
                data, ctype = payload, ctype or HTML
            elif isinstance(payload, str):
                data, ctype = payload.encode("utf-8"), ctype or HTML
            else:
                data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                ctype = JSON
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Request-Id", request_id)
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def _client_ip(self) -> str:
            # Detrás de un proxy de confianza (Render), el último valor de X-Forwarded-For
            # es la dirección que vio el proxy. Sin proxy, la del socket.
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded.strip():
                return forwarded.split(",")[-1].strip()
            return self.client_address[0]

        def _dispatch(self, method: str) -> None:
            request_id = uuid.uuid4().hex[:16]
            path = self.path.split("?")[0]
            if require_https and path != "/health" and \
                    self.headers.get("X-Forwarded-Proto", "").lower() != "https":
                self.close_connection = True
                self._send(403, {"error": "Se requiere HTTPS"}, request_id)
                return
            if method == "GET" and path in STATIC:
                name, ctype = STATIC[path]
                self._send(200, (WEB_DIR / name).read_bytes(), request_id, ctype)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._send(400, {"error": "Content-Length no válido"}, request_id)
                return
            limit = MAX_PDF_BODY_BYTES if path.endswith(PDF_EXTRACT_PATH) else MAX_BODY_BYTES
            if length > limit:
                self.close_connection = True   # no leemos un cuerpo tan grande
                self._send(413, {"error": f"El cuerpo supera el tamaño máximo ({limit // 1_000_000} MB)"}, request_id)
                return
            body = None
            if length:
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:   # incluye JSON mal formado y UTF-8 no válido
                    self._send(400, {"error": "JSON no válido"}, request_id)
                    return
                if not isinstance(body, dict):
                    self._send(400, {"error": "El cuerpo debe ser un objeto JSON"}, request_id)
                    return
            header = self.headers.get("Authorization", "")
            token = header[7:].strip() if header.startswith("Bearer ") else None
            status, payload = app.handle(method, path, body, token,
                                         ip=self._client_ip(), request_id=request_id)
            self._send(status, payload, request_id)

        def do_GET(self):  # noqa: N802
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_DELETE(self):  # noqa: N802
            self._dispatch("DELETE")

        def log_message(self, fmt, *args):  # menos ruido en consola
            pass

    return Handler


def run(host: str | None = None, port: int | None = None, db_path: str | None = None) -> None:
    """Arranca el servidor. Variables: HOST, PORT, NPLDESK_DB, NPLDESK_DEMO=1, NPLDESK_REQUIRE_HTTPS=1."""
    from npldesk.demo import seed

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8000"))
    db_path = db_path or os.environ.get("NPLDESK_DB", "data/npldesk.db")
    demo = os.environ.get("NPLDESK_DEMO") == "1"
    require_https = os.environ.get("NPLDESK_REQUIRE_HTTPS") == "1"

    if demo and db_path != ":memory:":
        raise SystemExit("NPLDESK_DEMO=1 solo puede usarse con NPLDESK_DB=:memory:. "
                         "La demo no puede arrancar sobre una base de datos en fichero.")

    db = Database(db_path)
    app = App(db)
    if demo:
        seed(app)
        mode = "MODO DEMO (datos ficticios, contraseñas públicas)"
    else:
        if db.count_users() == 0:
            print("Aviso: no hay usuarios. Créalos con: python -m npldesk.admin create-user --help")
        mode = f"base de datos {db_path}"
    server = ThreadingHTTPServer((host, port), make_handler(app, require_https=require_https))
    print(f"NPLDesk en http://{host}:{port} · {mode}")
    try:
        server.serve_forever()
    finally:
        db.close()


if __name__ == "__main__":
    run()
