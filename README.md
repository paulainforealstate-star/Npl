# NPLDesk (versión 0.5)

Gestor de expedientes de compra de créditos hipotecarios en impago: cribado, diligencia, riesgos, valoración por escenarios, decisión por fase procesal, oferta, ejecución, cartera e informes. Python 3.10 o superior. Única dependencia externa: `pypdf` (`pip install -r requirements.txt`), solo para leer PDF. Sin ella la app funciona y los PDF se rechazan con un aviso.

## Puesta en marcha

```bash
cd npldesk
python3 -m unittest discover -s tests -v          # 147 pruebas
python3 -m npldesk.admin create-user --user ana --name "Ana Ruiz" --role analista
python3 -m npldesk.api                            # http://127.0.0.1:8000
```

Cada persona tiene su cuenta. La contraseña se pide por teclado (mínimo 12 caracteres) y se guarda con scrypt, nunca en claro. Al entrar se obtiene una sesión de 8 horas que se cierra con "Salir" o al caducar. El token de sesión se guarda en la base de datos solo como hash SHA-256.

Variables de entorno: `HOST`, `PORT`, `NPLDESK_DB` (ruta de SQLite), `NPLDESK_DEMO=1` (solo con `NPLDESK_DB=:memory:`), `NPLDESK_REQUIRE_HTTPS=1` (rechaza peticiones que no lleguen por HTTPS según `X-Forwarded-Proto`; `/health` queda abierta).

### Administración

| Orden | Para qué |
|---|---|
| `python -m npldesk.admin create-user --user U --name N --role R [--email E]` | crear una cuenta |
| `python -m npldesk.admin set-password --user U` | cambiar contraseña (cierra sus sesiones) |
| `python -m npldesk.admin deactivate --user U` | desactivar cuenta (cierra sus sesiones) |
| `python -m npldesk.admin list-users` | listar cuentas |
| `python -m npldesk.admin backup --dest FICHERO` | copia de la base de datos con la API de SQLite |
| `python -m npldesk.admin verify-audit` | comprobar que la cadena de auditoría no se ha alterado |

Todas aceptan `--db RUTA` (o la variable `NPLDESK_DB`). Una copia de seguridad no cuenta hasta que se ha restaurado en un entorno de prueba: hazlo cada trimestre.

## Roles y permisos

| Rol | Puede |
|---|---|
| admin | Todo, sin límite de aprobación |
| analista | Crear expedientes, cribar, extraer y confirmar diligencia, registrar riesgos y decisiones, valorar |
| responsable | Aprobar riesgos bloqueantes, hacer ofertas hasta 500.000 €, registrar y anular cobros, tareas |
| gestor_campo | Visitas, tareas, fases y cobros |
| inversor | Cartera y **informe anonimizado** (sin nombres, titular ni finca) |
| auditor | Cartera, informes completos, el registro de auditoría y su verificación |

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


Cada escritura sobre un expediente lleva su `version`. Si otra persona lo ha cambiado mientras tanto, la respuesta es 409 y la pantalla vuelve a cargar el expediente.

## Endpoints

| Método | Ruta | Rol mínimo |
|---|---|---|
| GET | `/`, `/app.js`, `/app.css` | pública (pantalla web) |
| GET | `/health` | pública; comprueba la base de datos (503 si no responde) |
| POST | `/api/login` | pública (límites por cuenta y por dirección) |
| POST | `/api/logout` | cualquier sesión |
| GET | `/api/me` | cualquier sesión |
| POST | `/api/valuation` | analista |
| GET / POST | `/api/cases` | auditor / analista |
| GET / DELETE | `/api/cases/<id>` | auditor / admin |
| POST | `/api/cases/<id>/screen` | analista |
| POST | `/api/cases/<id>/diligence/extract` | analista |
| POST | `/api/cases/<id>/diligence/extract-pdf` | analista (PDF en base64, hasta 5 MB) |
| POST | `/api/cases/<id>/diligence/confirm` | analista |
| POST | `/api/cases/<id>/risks` | analista |
| POST | `/api/cases/<id>/risks/<i>/approve` | responsable |
| POST | `/api/cases/<id>/decision` | analista |
| POST | `/api/cases/<id>/offer` | responsable (cambiar una oferta exige `motivo`) |
| POST | `/api/cases/<id>/phase` | gestor de campo (retroceder exige `motivo`) |
| POST | `/api/cases/<id>/recoveries` | gestor de campo |
| POST | `/api/cases/<id>/recoveries/<i>/void` | responsable (exige `motivo`) |
| GET / POST | `/api/cases/<id>/tasks` | auditor / gestor de campo |
| POST | `/api/cases/<id>/tasks/<t>/status` | gestor de campo |
| GET | `/api/cases/<id>/calendar` | auditor |
| GET / POST | `/api/cases/<id>/visits` | gestor de campo |
| GET | `/api/cases/<id>/report` | inversor (anonimizado) / auditor |
| GET | `/api/portfolio` | inversor |
| GET | `/api/audit` | auditor |
| GET | `/api/audit/verify` | auditor |

Autenticación: `Authorization: Bearer <token>`, con el token que devuelve `/api/login`.

Códigos: 200/201 correcto · 400 datos no válidos · 401 sin sesión o credenciales incorrectas · 403 sin permiso · 404 no encontrado · 409 regla de negocio o versión desactualizada · 429 demasiados intentos · 503 base de datos no disponible.

Cada acción que modifica datos queda en `audit` con usuario, IP, identificador de petición y detalle. La tabla es inmutable (triggers que impiden `UPDATE` y `DELETE`) y cada fila incluye el hash de la anterior.

## Cambios en la 0.5

**Identidad y seguridad**
- Cuentas personales en la base de datos (ya no hay `users.json` ni tokens fijos por rol).
- Contraseñas con scrypt (biblioteca estándar). Sesiones de 8 horas con token opaco; en base de datos solo se guarda su hash. Cierre de sesión que revoca el token.
- Bloqueo de cuenta tras 5 fallos (15 minutos) y límite de 20 fallos por dirección en 15 minutos. Los errores de login no revelan si la cuenta existe.
- Cabeceras: Content-Security-Policy (`script-src 'self'`, sin JavaScript en línea), HSTS, `nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store`, `X-Request-Id`.
- Modo demo: no arranca con una base de datos en fichero.

**Reglas de negocio y validación**
- Validación centralizada (`npldesk/validation.py`): importes ≥ 0 y finitos, probabilidades en [0, 1], plazos en (0, 50] años, tasa en [0, 1], margen en [0, 0,99], fechas AAAA-MM-DD en un rango razonable. Un precio de venta de −50.000 € ya no se acepta.
- Si la vía no genera valor, el precio máximo queda en 0 con `max_price_floored: true` y la respuesta trae `warnings`.
- Cobros: no superan la oferta, no son anteriores a ella ni futuros. Se anulan con motivo; no se borran.
- Ofertas: `offers` guarda el historial; cambiar una oferta exige motivo; `offer` es la vigente.
- Fases: avanzar es libre dentro del orden; retroceder exige motivo y queda marcado en el historial.
- TIR no calculable: la respuesta lo dice y explica que los flujos no cambian de signo.
- Plazos en `npldesk/plazos.json` con norma, fuente, fecha de revisión y `validado_por`. Un plazo sin validar aparece como "sin validar". Siguen siendo ejemplos.

**Datos y operación**
- Control de versiones por expediente (`version`) con 409 ante ediciones concurrentes.
- Auditoría encadenada por hash, con IP e identificador de petición, y verificación (`verify-audit`).
- Copias de seguridad con `admin backup`.
- `/health` comprueba la base de datos.
- Migración automática desde bases de datos de la 0.4.

**Interfaz**
- Pantalla de entrada con usuario y contraseña; la sesión dura lo que la pestaña (sessionStorage).
- Anulación de cobros, nueva versión de oferta con motivo, retroceso de fase con motivo.
- Avisos de valoración y de TIR no calculable visibles en pantalla.

## Cambios en la 0.4

- **Fechas validadas** (`AAAA-MM-DD`) en cobros, tareas y visitas. Antes, una fecha mala dejaba inutilizable la cartera o la lista de tareas.
- **Nota simple con fecha imposible** (p. ej. 31/02/2026) se rechaza antes de guardarse.
- **Discrepancias resueltas al confirmar.** Una discrepancia solo vuelve a bloquear si llega un documento nuevo con otro valor. La nota que se usa para la caducidad es la confirmada.
- **Oferta por encima del precio máximo**: no se bloquea, pero la respuesta trae `warnings` y `over_max_price` queda registrado.
- **Reglas de orden**: no hay cobros sin oferta; la oferta no cambia tras cobros; la decisión no cambia tras la oferta; no se puede cambiar de fase si la vía elegida deja de ser viable.
- **Tareas**: las dependencias deben existir en el mismo expediente.
- **Límites de seguridad**: cuerpo máximo de 1 MB (413), iteraciones Monte Carlo máximas de 200.000, JSON y Content-Length no válidos devuelven 400, y los fallos inesperados devuelven 500 sin detalles internos.
- **Peticiones serializadas** con un bloqueo, porque la conexión SQLite es única.
- `GET /api/cases/<id>` incluye `alerts` y `blockers` (lo que falta para ofertar).
- **Pantalla web completa**: diligencia (pegar documento, extraer y confirmar), riesgos (alta y aprobación), decisión, oferta, cobros, tareas y fase, con los bloqueos visibles.

## Qué queda del plan de mejora

**Hecho en la 0.5**: fases 1.1–1.5, 2.1–2.6, 3.2, 3.3 (cadena e inmutabilidad), 3.5 (copias, sin prueba de restauración automatizada), 4.2 (salud y request id; registros en JSON pendientes), 1.3 (cabeceras y CSP), 7 (pruebas de permisos, validación, reglas, concurrencia, auditoría y HTTP).

**Pendiente**:
- 2.7 parcial: los plazos son versionados, pero no hay un flujo de validación con abogado ni un histórico de revisiones.
- 3.1 PostgreSQL: el esquema está preparado, la migración no.
- 3.2 sin bloqueo global: el `RLock` sigue; sin PostgreSQL no se puede quitar con seguridad.
- 3.4 cifrado de campos personales con AES-GCM: requiere una dependencia (`cryptography`). La pseudonimización de informes para inversores sí está.
- 4.1 framework (FastAPI o Flask): decisión pendiente; se mantiene `http.server`.
- 4.2 registros estructurados en JSON.
- 4.3 CI: `.github/workflows/ci.yml` ejecuta los tests. Ruff, mypy y pip-audit aún no están configurados.
- 5 React y Leaflet: no hecho.
- 6 extracción con OCR y modelo de lenguaje: no hecho. La extracción por reglas sigue siendo la única.
- 8 documentación de cumplimiento (EIPD, contratos de encargado, revisión jurídica, manual de operación): fuera del código; el manual de operación es esta sección.

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

**Incluido**: motor financiero, escenarios, Monte Carlo, fases procesales, cribado, diligencia con alertas, riesgos con bloqueo, decisión con justificación, oferta con historial y límites, cobros con controles, tareas con dependencias, visitas, calendario, cartera con KPIs, informes anonimizados, cuentas, sesiones y auditoría encadenada.

**Aproximado o de ejemplo**:
- La extracción de documentos usa reglas sobre texto. El OCR y el modelo de lenguaje de la especificación requieren un proveedor y una clave de API; el punto de entrada (`diligence.extract`) está preparado para sustituirlo.
- Los plazos procesales de `npldesk/plazos.json` son **ejemplos**. Hay que sustituirlos por los vigentes, validados por un abogado.
- El límite de 500.000 € para responsables y los escenarios de análisis son supuestos de `config.py`.

**No incluido en esta versión**: PostgreSQL, interfaz en React, mapa Leaflet, exportación a PDF real (el informe es HTML para imprimir), doble factor de autenticación, cifrado de campos personales, integración con el Registro de la Propiedad y simulación del árbol de decisión completo. Ver "Qué queda del plan de mejora".

## Límites legales

La app no da asesoramiento jurídico ni certifica datos registrales. Los riesgos citan la norma de referencia pero no concluyen sobre su validez. Las plantillas de comunicación con el deudor deben revisarse por un abogado antes de usarse.

## Demo pública

Para probarla sin datos reales hay un modo demo con un expediente ficticio precargado. Las cuentas y contraseñas son públicas. Solo debe usarse para probar la app.

Arranque local: `NPLDESK_DEMO=1 NPLDESK_DB=:memory: python3 -m npldesk.api` y abre http://127.0.0.1:8000.

| Usuario | Contraseña | Rol |
|---|---|---|
| `demo_admin` | `demo-admin-npl-2026` | admin |
| `demo_analista` | `demo-analista-npl-2026` | analista |
| `demo_responsable` | `demo-responsable-npl-2026` | responsable |
| `demo_gestor` | `demo-gestor-npl-2026` | gestor de campo |
| `demo_inversor` | `demo-inversor-npl-2026` | inversor (informe anonimizado) |
| `demo_auditor` | `demo-auditor-npl-2026` | auditor |


## Cambios en la interfaz (revisión de usabilidad)

- **Vías y escenarios en formulario**: cada vía es una tarjeta con una tabla editable (nombre, probabilidad, precio de venta, costes, cargas, plazo, fuente, fecha de revisión). Un indicador muestra en vivo si las probabilidades suman 100 %. El JSON queda en "Ver como JSON" para usuarios avanzados.
- **Oferta con retroalimentación**: al escribir el importe se muestran la TIR prevista y la distancia al precio máximo, sin guardar nada (`POST /api/cases/<id>/offer/preview`).
- **Ficha como flujo de seis pasos** (Cribado, Diligencia, Riesgos, Decisión, Oferta, Ejecución), cada uno completado, pendiente o bloqueado, con su motivo y un botón a la sección. Viene en `steps` de `GET /api/cases/<id>`.
- **Diligencia**: arrastre de PDF digitales (con texto, máximo 5 MB y 40 páginas) o `.txt`, fragmento del documento junto a cada dato, discrepancias en un bloque que hay que resolver antes de seguir y corrección manual de cada dato (`manual: true` en `/diligence/confirm`). El PDF se lee en el servidor con `POST /diligence/extract-pdf`; un PDF escaneado se rechaza, porque haría falta OCR.
- **Riesgos desde catálogo**: plantillas por categoría con norma de referencia (`GET /api/catalog`). Las normas son orientativas y deben validarlas los asesores.
- **Lista de expedientes**: búsqueda por nombre, filtros por fase y estado, y orden por prioridad (bloqueados primero).
- **Accesibilidad**: variables de diseño, etiquetas visibles, foco visible, avisos con `aria-live`, notificaciones tras cada acción, anulación de cobros con motivo, y siglas (VE, TIR, P10, vía) con explicación al pasar el cursor.

Corrección: los importes se comparan como números al confirmar, así que un valor `150000` (JSON) reconoce a `150000.0` extraído del documento.
