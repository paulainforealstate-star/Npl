"""API de NPLDesk. La lógica vive en `App.handle`, que se puede probar sin servidor HTTP.

Códigos: 200/201 correcto · 400 datos no válidos · 401 sin token · 403 sin permiso ·
404 no encontrado · 409 regla de negocio (p. ej. oferta bloqueada).
"""

from __future__ import annotations

import json
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from npldesk import auth, diligence, risks, tasks
from npldesk.config import DEFAULTS
from npldesk.db import Database
from npldesk.engine import Route, Scenario, recommend
from npldesk.montecarlo import simulate_route
from npldesk.phases import check_routes_for_phase
from npldesk.report import case_report
from npldesk.portfolio import kpis
from npldesk.scenarios import analyze, irr_for_price
from npldesk.screening import screen
from npldesk.workflow import (
    GateError,
    change_phase,
    check_offer_limit,
    offer_blockers,
    register_decision,
)

WEB_DIR = Path(__file__).parent / "web"
JSON = "application/json; charset=utf-8"
HTML = "text/html; charset=utf-8"


class NotFound(Exception):
    pass


# ---- valoración ---------------------------------------------------------------

def parse_routes(payload: dict) -> list[Route]:
    if not isinstance(payload.get("routes"), list) or not payload["routes"]:
        raise ValueError("'routes' debe ser una lista no vacía")
    routes = []
    for r in payload["routes"]:
        if not r.get("name"):
            raise ValueError("Cada vía necesita un 'name'")
        if not r.get("scenarios"):
            raise ValueError(f"La vía '{r['name']}' no tiene escenarios")
        scenarios = tuple(
            Scenario(
                name=str(s.get("name", "escenario")),
                probability=float(s["probability"]),
                sale_price=float(s["sale_price"]),
                costs=float(s.get("costs", 0)),
                charges=float(s.get("charges", 0)),
                debtor_share=float(s.get("debtor_share", 0)),
                time_years=float(s["time_years"]),
            )
            for s in r["scenarios"]
        )
        routes.append(Route(name=r["name"], scenarios=scenarios))
    return routes


def valuate(payload: dict) -> dict:
    rate = float(payload["rate"])
    margin = float(payload.get("safety_margin", 0.0))
    phase = payload.get("phase")
    routes = parse_routes(payload)
    if phase:
        check_routes_for_phase(phase, [r.name for r in routes])
    rec = recommend(routes, rate=rate, safety_margin=margin)
    response = {
        "phase": phase,
        "best": rec.best.__dict__,
        "ranking": [r.__dict__ for r in rec.ranking],
        "analysis": {r.name: analyze(r, rate, margin) for r in routes},
    }
    iterations = int(payload.get("montecarlo_iterations", 0))
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
    def __init__(self, db: Database, users: dict[str, dict], today=None) -> None:
        self.db = db
        self.users = users
        self.today = today or date.today
        self.routes = [
            ("GET", r"^/api/cases$", self.cases_list),
            ("POST", r"^/api/cases$", self.cases_create),
            ("GET", r"^/api/cases/(\d+)$", self.case_get),
            ("DELETE", r"^/api/cases/(\d+)$", self.case_delete),
            ("POST", r"^/api/cases/(\d+)/screen$", self.case_screen),
            ("POST", r"^/api/cases/(\d+)/diligence/extract$", self.diligence_extract),
            ("POST", r"^/api/cases/(\d+)/diligence/confirm$", self.diligence_confirm),
            ("POST", r"^/api/cases/(\d+)/risks$", self.risk_add),
            ("POST", r"^/api/cases/(\d+)/risks/(\d+)/approve$", self.risk_approve),
            ("POST", r"^/api/cases/(\d+)/decision$", self.decision),
            ("POST", r"^/api/cases/(\d+)/offer$", self.offer),
            ("POST", r"^/api/cases/(\d+)/phase$", self.phase),
            ("POST", r"^/api/cases/(\d+)/recoveries$", self.recovery),
            ("GET", r"^/api/cases/(\d+)/tasks$", self.task_list),
            ("POST", r"^/api/cases/(\d+)/tasks$", self.task_add),
            ("POST", r"^/api/cases/(\d+)/tasks/(\d+)/status$", self.task_status),
            ("GET", r"^/api/cases/(\d+)/calendar$", self.calendar),
            ("GET", r"^/api/cases/(\d+)/visits$", self.visit_list),
            ("POST", r"^/api/cases/(\d+)/visits$", self.visit_add),
            ("GET", r"^/api/cases/(\d+)/report$", self.report),
            ("GET", r"^/api/portfolio$", self.portfolio),
            ("GET", r"^/api/audit$", self.audit_list),
            ("POST", r"^/api/valuation$", self.valuation),
        ]

    # -- despacho -------------------------------------------------------------

    def handle(self, method: str, path: str, body: dict | None = None,
               token: str | None = None) -> tuple[int, object]:
        if method == "GET" and path == "/":
            return 200, (WEB_DIR / "index.html").read_bytes()
        if method == "GET" and path == "/health":
            return 200, {"status": "ok"}
        ctx = {"token": token, "body": body or {}}
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
            except GateError as e:
                return 409, {"error": str(e)}
            except KeyError as e:
                return 400, {"error": f"Falta el campo {e}"}
            except LookupError as e:
                return 404, {"error": str(e) or "no encontrado"}
            except (ValueError, TypeError) as e:
                return 400, {"error": str(e)}
        return 404, {"error": "no encontrado"}

    # -- utilidades -------------------------------------------------------------

    def _auth(self, ctx: dict, perm: str) -> dict:
        return auth.authorize(self.users, ctx["token"], perm)

    def _case(self, case_id: str) -> dict:
        case = self.db.get_case(int(case_id))
        if case is None:
            raise NotFound("expediente no encontrado")
        return case

    def _audit(self, user: dict, action: str, case_id: int | None, detail: dict | None = None) -> None:
        self.db.add_audit(user["user"], action, "case", str(case_id) if case_id else None, detail)

    def _save_state(self, case_id: int, state: dict) -> None:
        self.db.save_case(case_id, state=state)

    # -- expedientes ------------------------------------------------------------

    def cases_list(self, ctx):
        self._auth(ctx, "cases:read")
        return 200, {"cases": self.db.list_cases()}

    def cases_create(self, ctx):
        user = self._auth(ctx, "cases:write")
        b = ctx["body"]
        name = str(b.get("name", "")).strip()
        phase = str(b.get("phase", "")).strip()
        if not name:
            raise ValueError("'name' es obligatorio")
        if phase not in DEFAULTS["phases"]:
            raise ValueError(f"Fase desconocida '{phase}'")
        inp = b["input"]
        warnings = source_warnings(inp["routes"])
        result = valuate({**inp, "phase": phase})
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
        case = self._case(case_id)
        data = ctx["body"] or case["input"].get("screening")
        if not data:
            raise ValueError("No hay datos de cribado")
        state = case["state"]
        state["screening"] = screen(data)
        self._save_state(int(case_id), state)
        self._audit(user, "cribado", int(case_id), state["screening"])
        return 200, state["screening"]

    # -- due diligence ------------------------------------------------------------

    def diligence_extract(self, ctx, case_id):
        user = self._auth(ctx, "diligence:write")
        case = self._case(case_id)
        b = ctx["body"]
        items = diligence.extract(str(b.get("document", "documento")), str(b.get("text", "")))
        state = case["state"]
        diligence.add_candidates(state, items)
        self._save_state(int(case_id), state)
        declared = case["input"].get("declared_saldo")
        self._audit(user, "extraer_diligencia", int(case_id), {"campos": [i["field"] for i in items]})
        return 200, {
            "found": items,
            "alerts": diligence.alerts(state, declared, self.today()),
            "missing": diligence.missing_required(state),
        }

    def diligence_confirm(self, ctx, case_id):
        user = self._auth(ctx, "diligence:write")
        case = self._case(case_id)
        b = ctx["body"]
        state = case["state"]
        diligence.confirm(state, b["field"], b["value"], user["user"], self.today())
        self._save_state(int(case_id), state)
        self._audit(user, "confirmar_diligencia", int(case_id), {"campo": b["field"]})
        return 200, {"missing": diligence.missing_required(state)}

    # -- riesgos --------------------------------------------------------------------

    def risk_add(self, ctx, case_id):
        user = self._auth(ctx, "risks:write")
        case = self._case(case_id)
        b = ctx["body"]
        state = case["state"]
        index = risks.add_risk(state, b["category"], str(b["description"]), b["level"], b.get("norm_ref"))
        self._save_state(int(case_id), state)
        self._audit(user, "añadir_riesgo", int(case_id), {"nivel": b["level"]})
        return 201, {"index": index}

    def risk_approve(self, ctx, case_id, index):
        user = self._auth(ctx, "risks:approve")
        case = self._case(case_id)
        state = case["state"]
        risks.approve(state, int(index), user["user"])
        self._save_state(int(case_id), state)
        self._audit(user, "aprobar_riesgo", int(case_id), {"indice": int(index)})
        return 200, {"approved_by": user["user"]}

    # -- decisión, oferta, fase, cobros ------------------------------------------

    def decision(self, ctx, case_id):
        user = self._auth(ctx, "decision:write")
        case = self._case(case_id)
        b = ctx["body"]
        chosen = str(b["chosen"])
        result = case["result"] or {}
        ranked = [r["route"] for r in result.get("ranking", [])]
        if chosen not in ranked:
            raise ValueError(f"La vía '{chosen}' no está en la valoración")
        check_routes_for_phase(case["phase"], [chosen])
        state = case["state"]
        register_decision(state, result["best"]["route"], chosen,
                          str(b.get("justification", "")), user["user"], self.today())
        self._save_state(int(case_id), state)
        self._audit(user, "decision", int(case_id), {"elegida": chosen})
        return 200, state["decision"]

    def offer(self, ctx, case_id):
        user = self._auth(ctx, "offer:write")
        case = self._case(case_id)
        amount = float(ctx["body"]["amount"])
        if amount <= 0:
            raise ValueError("El importe debe ser positivo")
        check_offer_limit(user["role"], amount)
        state = case["state"]
        declared = case["input"].get("declared_saldo")
        blockers = offer_blockers(state, declared, self.today())
        if blockers:
            raise GateError("; ".join(blockers))
        chosen = state["decision"]["chosen"]
        route = next(r for r in parse_routes(case["input"]) if r.name == chosen)
        planned = irr_for_price(amount, route)
        state["offer"] = {
            "amount": amount,
            "route": chosen,
            "by": user["user"],
            "date": self.today().isoformat(),
            "planned_irr": planned,
        }
        self._save_state(int(case_id), state)
        self._audit(user, "oferta", int(case_id), {"importe": amount})
        return 201, state["offer"]

    def phase(self, ctx, case_id):
        user = self._auth(ctx, "phase:write")
        phase = str(ctx["body"]["phase"])
        if phase not in DEFAULTS["phases"]:
            raise ValueError(f"Fase desconocida '{phase}'")
        case = self._case(case_id)
        state = case["state"]
        change_phase(state, phase, self.today())
        self.db.save_case(int(case_id), phase=phase, state=state)
        self._audit(user, "cambio_fase", int(case_id), {"fase": phase})
        return 200, {"phase": phase}

    def recovery(self, ctx, case_id):
        user = self._auth(ctx, "recovery:write")
        b = ctx["body"]
        amount = float(b["amount"])
        if amount <= 0:
            raise ValueError("El cobro debe ser positivo")
        case = self._case(case_id)
        state = case["state"]
        state.setdefault("recoveries", []).append({
            "amount": amount,
            "date": str(b.get("date") or self.today().isoformat()),
        })
        self._save_state(int(case_id), state)
        self._audit(user, "cobro", int(case_id), {"importe": amount})
        return 201, {"recoveries": len(state["recoveries"])}

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
        b = ctx["body"]
        task_id = self.db.add_task(
            int(case_id), str(b["title"]), b.get("owner"), b.get("due_date"),
            [int(d) for d in b.get("depends_on", [])],
        )
        self._audit(user, "añadir_tarea", int(case_id), {"tarea": task_id})
        return 201, {"id": task_id}

    def task_status(self, ctx, case_id, task_id):
        user = self._auth(ctx, "tasks:write")
        self._case(case_id)
        status = str(ctx["body"]["status"])
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
        b = ctx["body"]
        lat, lon = float(b["lat"]), float(b["lon"])
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Coordenadas fuera de rango")
        visit_id = self.db.add_visit(
            int(case_id), str(b.get("date") or self.today().isoformat()), lat, lon,
            b.get("photo_ref"), b.get("notes"),
        )
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

    def valuation(self, ctx):
        self._auth(ctx, "valuation:run")
        return 200, valuate(ctx["body"])



# ---- servidor HTTP ----------------------------------------------------------------

def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload) -> None:
            if isinstance(payload, bytes):
                data, ctype = payload, HTML
            elif isinstance(payload, str):
                data, ctype = payload.encode("utf-8"), HTML
            else:
                data, ctype = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"), JSON
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = None
            if length:
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._send(400, {"error": "JSON no válido"})
                    return
                if not isinstance(body, dict):
                    self._send(400, {"error": "El cuerpo debe ser un objeto JSON"})
                    return
            header = self.headers.get("Authorization", "")
            token = header[7:].strip() if header.startswith("Bearer ") else None
            status, payload = app.handle(method, self.path.split("?")[0], body, token)
            self._send(status, payload)

        def do_GET(self):  # noqa: N802
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_DELETE(self):  # noqa: N802
            self._dispatch("DELETE")

        def log_message(self, fmt, *args):  # menos ruido en consola
            pass

    return Handler


def run(host: str = "127.0.0.1", port: int = 8000, db_path: str = "data/npldesk.db",
        users_path: str = "data/users.json") -> None:
    db = Database(db_path)
    users = auth.load_users(users_path)
    app = App(db, users)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"NPLDesk en http://{host}:{port} · tokens en {users_path}")
    try:
        server.serve_forever()
    finally:
        db.close()


if __name__ == "__main__":
    run()
