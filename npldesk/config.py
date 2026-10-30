"""Parámetros por defecto. Todos son supuestos y deben revisarse con fuente y fecha."""

DEFAULTS = {
    "discount_rate": 0.05,
    "screening": {
        "ltv_max": 0.80,
        "provinces": ["Madrid"],
        "amount_min": 20_000.0,
        "amount_max": 1_500_000.0,
        "allow_occupied": False,
    },
    # Límite de aprobación de ofertas por rol. El admin no tiene límite.
    "approval_limits": {"responsable": 500_000.0},
    "note_max_age_days": 90,
    "required_diligence": ["titular", "saldo_pendiente", "fecha_nota", "finca"],
    "risk_categories": ["registral", "procesal", "ocupacion", "proteccion_deudor", "documental"],
    "risk_levels": ["bajo", "medio", "alto", "bloqueante"],
    "phases": ["precontenciosa", "judicial_sin_adjudicar", "judicial_adjudicada"],
}
