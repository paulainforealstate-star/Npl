"""Texto de un PDF digital, con pypdf: la única dependencia externa de la aplicación.

Sin pypdf instalado la aplicación arranca igual; solo se rechazan los PDF.
Un PDF escaneado (sin texto seleccionable) no se puede leer sin OCR, y se avisa en vez de inventar datos.
"""

from __future__ import annotations

import io
import logging

MAX_PDF_BYTES = 5_000_000   # tamaño máximo del PDF, antes de codificarlo en base64
MAX_PDF_PAGES = 40
MIN_TEXT_CHARS = 20         # por debajo de esto se considera que no hay texto (escaneo)


class PdfError(ValueError):
    """El PDF no se puede usar: la petición es incorrecta (HTTP 400)."""


def available() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def extract_text(data: bytes) -> str:
    if len(data) > MAX_PDF_BYTES:
        raise PdfError("El PDF supera el tamaño máximo (5 MB)")
    if not data.startswith(b"%PDF-"):
        raise PdfError("El archivo no es un PDF válido")
    from pypdf import PdfReader
    # pypdf avisa en el log de cada PDF dañado; el usuario ya recibe el motivo en la pantalla.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise PdfError("El PDF está protegido con contraseña: quítala antes de subirlo")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise PdfError(f"El PDF tiene más de {MAX_PDF_PAGES} páginas")
        parts = [(page.extract_text() or "").strip() for page in reader.pages]
    except PdfError:
        raise
    except Exception:
        raise PdfError("No se pudo leer el PDF: está dañado o usa un formato no compatible")

    text = "\n".join(p for p in parts if p).strip()
    if len(text) < MIN_TEXT_CHARS:
        raise PdfError("El PDF no tiene texto seleccionable: parece un escaneo y haría falta OCR, "
                       "que esta versión no hace. Usa la versión digital del documento.")
    return text
