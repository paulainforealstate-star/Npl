"""Informe HTML para inversores (imprimible a PDF desde el navegador).

Con `anonymized=True` se oculta cualquier dato identificativo: nombre del
expediente, titular, finca y coordenadas de las visitas.
"""

from __future__ import annotations

import hashlib
import html


def anonymized_id(name: str) -> str:
    return "EXP-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8].upper()


def _rows(pairs: list[tuple[str, str]]) -> str:
    return "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in pairs)


def case_report(case: dict, anonymized: bool) -> str:
    st = case["state"]
    name = anonymized_id(case["name"]) if anonymized else case["name"]
    scr = st.get("screening", {})
    decision = st.get("decision", {})
    offer = st.get("offer", {})
    confirmed = st.get("diligence", {}).get("confirmed", {})

    dd_pairs = []
    for field, item in confirmed.items():
        if anonymized and field in ("titular", "finca"):
            continue
        dd_pairs.append((field, str(item["value"])))

    risks = st.get("risks", [])
    risk_rows = "".join(
        f"<tr><td>{html.escape(r['category'])}</td><td>{html.escape(r['level'])}</td>"
        f"<td>{html.escape(r['description']) if not anonymized else '—'}</td></tr>"
        for r in risks
    ) or "<tr><td colspan='3'>Sin riesgos registrados</td></tr>"

    rank_rows = ""
    if case.get("result"):
        for r in case["result"]["ranking"]:
            rank_rows += (f"<tr><td>{html.escape(r['route'])}</td>"
                          f"<td>{r['expected_value']:,.0f} €</td>"
                          f"<td>{r['max_price']:,.0f} €</td></tr>")

    phase_hist = " → ".join(h["phase"] for h in st.get("phase_history", [])) or case["phase"]

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Informe {html.escape(name)}</title>
<style>
 body {{ font-family: system-ui, sans-serif; max-width: 860px; margin: 2rem auto; color: #111; }}
 table {{ border-collapse: collapse; width: 100%; margin: .5rem 0 1.5rem; }}
 th, td {{ text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #ccc; vertical-align: top; }}
 h2 {{ margin-top: 2rem; border-bottom: 2px solid #333; }}
 .aviso {{ font-size: .85rem; color: #555; }}
 @media print {{ body {{ margin: 0; }} }}
</style></head><body>
<h1>Informe de expediente: {html.escape(name)}</h1>
<p class="aviso">Fase actual: {html.escape(case['phase'])} · Recorrido: {html.escape(phase_hist)}</p>

<h2>Cribado</h2>
<table>{_rows([("Estado", scr.get("status", "pendiente")),
                ("LTV", f"{scr['ltv']:.1%}" if "ltv" in scr else "—"),
                ("Motivos", "; ".join(scr.get("reasons", [])) or "—")])}</table>

<h2>Diligencia confirmada</h2>
<table>{_rows(dd_pairs) if dd_pairs else "<tr><td>Sin datos confirmados</td></tr>"}</table>

<h2>Riesgos</h2>
<table><tr><th>Categoría</th><th>Nivel</th><th>Descripción</th></tr>{risk_rows}</table>

<h2>Valoración por vía</h2>
<table><tr><th>Vía</th><th>Valor esperado</th><th>Precio máximo</th></tr>
{rank_rows or "<tr><td colspan='3'>Sin valoración</td></tr>"}</table>

<h2>Decisión y oferta</h2>
<table>{_rows([
    ("Vía recomendada", decision.get("recommended", "—")),
    ("Vía elegida", decision.get("chosen", "—")),
    ("Justificación", decision.get("justification") or "—"),
    ("Importe ofertado", f"{offer['amount']:,.0f} €" if offer else "—"),
    ("TIR prevista", f"{offer['planned_irr']:.2%}" if offer and offer.get("planned_irr") is not None else "—"),
])}</table>

<p class="aviso">Informe generado a partir de datos registrados en NPLDesk. No constituye asesoramiento jurídico ni certificación registral.</p>
</body></html>"""
