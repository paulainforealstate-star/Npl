# NPLDesk (versión 0.3)

Gestor de expedientes de compra de créditos hipotecarios en impago: cribado, diligencia, riesgos, valoración por escenarios, decisión por fase procesal, oferta, ejecución, cartera e informes. Sin dependencias externas: Python 3.10 o superior.

## Puesta en marcha

```bash
cd npldesk
python3 -m unittest discover -s tests -v   # 53 pruebas
python3 -m npldesk.api                     # http://127.0.0.1:8000
```

En la primera ejecución se crea `data/users.json` con un token por rol. Ese fichero es secreto (permisos 600 y excluido de git). Cada petición debe llevar `Authorization: Bearer <token>`.

## Roles y permisos

| Rol | Puede |
|---|---|
| admin | Todo, sin límite de aprobación |
| analista | Crear expedientes, cribar, extraer y confirmar diligencia, registrar riesgos y decisiones, valorar |
| responsable | Aprobar riesgos bloqueantes, hacer ofertas hasta 500.000 €, registrar cobros y tareas |
| gestor_campo | Visitas, tareas, fases y cobros |
| inversor | Cartera y **informe anonimizado** (sin nombres, titular ni finca) |
| auditor | Cartera, informes completos y el registro de auditoría |

Los permisos están en `npldesk/auth.py`. Los límites de aprobación están en `npldesk/config.py`.

## Flujo de un expediente

1. **Crear** (`POST /api/cases`): nombre, fase, escenarios y datos de cribado. Devuelve la valoración, el cribado y avisos si algún escenario no tiene fuente o fecha de revisión.
2. **Diligencia**: `POST /diligence/extract` con el texto de un documento; `POST /diligence/confirm` con el valor que una persona valida. Sin los cuatro campos confirmados no hay oferta.
3. **Riesgos**: `POST /risks`. Un riesgo `bloqueante` impide ofertar hasta que el responsable lo aprueba (`POST /risks/<i>/approve`).
4. **Decisión**: `POST /decision` con la vía elegida. Si no es la recomendada, la justificación es obligatoria.
5. **Oferta** (`POST /offer`): comprueba cribado, diligencia, alertas (nota caducada, discrepancias), riesgos y decisión. Calcula la TIR prevista a ese precio.
6. **Ejecución**: fases (`POST /phase`), tareas con dependencias (`/tasks`), visitas geolocalizadas (`/visits`) y calendario de plazos (`/calendar`).
7. **Cobros y cartera**: `POST /recoveries`, `GET /api/portfolio` (capital comprometido, monetizado, pendiente, TIR realizada y prevista, días por fase).
8. **Informe**: `GET /api/cases/<id>/report` (HTML imprimible a PDF desde el navegador).

## Endpoints

| Método | Ruta | Rol mínimo |
|---|---|---|
| GET | `/` | pública (pantalla web) |
| GET | `/health` | pública |
| POST | `/api/valuation` | analista |
| GET / POST | `/api/cases` | auditor / analista |
| GET / DELETE | `/api/cases/<id>` | auditor / admin |
| POST | `/api/cases/<id>/screen` | analista |
| POST | `/api/cases/<id>/diligence/extract` | analista |
| POST | `/api/cases/<id>/diligence/confirm` | analista |
| POST | `/api/cases/<id>/risks` | analista |
| POST | `/api/cases/<id>/risks/<i>/approve` | responsable |
| POST | `/api/cases/<id>/decision` | analista |
| POST | `/api/cases/<id>/offer` | responsable |
| POST | `/api/cases/<id>/phase` | gestor de campo |
| POST | `/api/cases/<id>/recoveries` | gestor de campo |
| GET / POST | `/api/cases/<id>/tasks` | auditor / gestor de campo |
| POST | `/api/cases/<id>/tasks/<t>/status` | gestor de campo |
| GET | `/api/cases/<id>/calendar` | auditor |
| GET / POST | `/api/cases/<id>/visits` | gestor de campo |
| GET | `/api/cases/<id>/report` | inversor (anonimizado) / auditor |
| GET | `/api/portfolio` | inversor |
| GET | `/api/audit` | auditor |

Códigos: 200/201 correcto · 400 datos no válidos · 401 sin token · 403 sin permiso · 404 no encontrado · 409 regla de negocio (p. ej. oferta bloqueada).

Cada acción que modifica datos queda registrada en `audit` con usuario, fecha y detalle.

## Cálculo

- Valor neto: `VN = P_venta − costes − cargas − participación de la deudora`.
- Valor esperado: `VE = Σ p_s · VN_s / (1 + r)^t_s`.
- Precio máximo: `P_max = VE · (1 − m)`.
- Escenarios de análisis: base, optimista (+10 % precio, −10 % plazo), pesimista (−15 % precio, +30 % plazo) y estrés (−30 % precio, +50 % plazo). Son supuestos a revisar.
- Punto de equilibrio: el precio al que la operación no genera ni pierde valor a la tasa exigida (igual al VE).
- TIR prevista: la tasa que iguala el precio de compra a los flujos probabilizados.
- Monte Carlo: opcional (`montecarlo_iterations`), con percentiles P10, P50 y P90 y semilla fija.

## Ejemplo: valoración

```bash
curl -s -X POST localhost:8000/api/valuation \
  -H "Authorization: Bearer $TOKEN_ANALISTA" -H 'Content-Type: application/json' -d '{
  "phase": "precontenciosa", "rate": 0.05, "safety_margin": 0.1,
  "montecarlo_iterations": 5000,
  "routes": [
    {"name": "negociación", "scenarios": [
      {"name": "base", "probability": 1.0, "sale_price": 90000, "time_years": 0.5,
       "source": "tasación", "reviewed_at": "2026-10-01"}]},
    {"name": "venta ordenada", "scenarios": [
      {"name": "favorable", "probability": 0.7, "sale_price": 110000, "costs": 8000, "time_years": 1,
       "source": "mercado", "reviewed_at": "2026-10-01"},
      {"name": "desfavorable", "probability": 0.3, "sale_price": 70000, "costs": 8000, "time_years": 1.5,
       "source": "mercado", "reviewed_at": "2026-10-01"}]}
  ]
}'
```

## Lo que hay y lo que no

**Incluido**: motor financiero, escenarios, Monte Carlo, fases procesales, cribado, diligencia con alertas, riesgos con bloqueo, decisión con justificación, oferta con límites, tareas con dependencias, visitas, calendario, cartera con KPIs, informes anonimizados, roles, tokens y auditoría.

**Aproximado o de ejemplo**:
- La extracción de documentos usa reglas sobre texto. El OCR y el modelo de lenguaje con esquema JSON de la especificación requieren un proveedor y una clave de API; el punto de entrada (`diligence.extract`) está preparado para sustituirlo.
- Los plazos procesales de `npldesk/tasks.py` son **ejemplos** para probar el calendario. Hay que sustituirlos por los vigentes, validados por un abogado.
- El límite de 500.000 € para responsables y los escenarios de análisis son supuestos de `config.py`.

**No incluido en esta versión**:
- PostgreSQL (se usa SQLite, con un esquema pensado para migrar).
- Interfaz en React (la pantalla es HTML y JavaScript sin dependencias) y mapa Leaflet.
- Exportación a PDF real (el informe es HTML para imprimir desde el navegador).
- Doble factor de autenticación, cifrado en reposo y copias de seguridad: requieren infraestructura y se deben configurar en el despliegue.
- Integración automática con el Registro de la Propiedad (queda fuera de la primera versión, como indica la especificación).
- Simulación del árbol de decisión completo por fase: el analista elige las vías a comparar y el motor valida que sean viables.

## Límites legales

La app no da asesoramiento jurídico ni certifica datos registrales. Los riesgos citan la norma de referencia pero no concluyen sobre su validez. Las plantillas de comunicación con el deudor deben revisarse por un abogado antes de usarse.
