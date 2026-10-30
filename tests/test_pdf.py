"""Lectura de PDF digitales: texto, escaneos, archivos dañados y el endpoint de extracción."""

import base64
import unittest
from unittest import mock

from npldesk import pdftext
from test_app import AppTestCase

NOTE_LINES = ["Titular: Eduardo Gabarri Céspedes", "Finca: 93296",
              "Saldo pendiente: 118.500,25 €", "Fecha de emisión: 20/10/2026"]


def make_pdf(lines):
    """PDF mínimo válido con una línea de texto por entrada (Helvetica, sin comprimir)."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["BT /F1 12 Tf 72 720 Td 14 TL"] + [f"({esc(l)}) Tj T*" for l in lines] + ["ET"]
    stream = "\n".join(ops).encode("cp1252")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


class ExtractTextTests(unittest.TestCase):
    def test_reads_text_of_a_digital_pdf(self):
        text = pdftext.extract_text(make_pdf(NOTE_LINES))
        self.assertIn("Titular: Eduardo Gabarri Céspedes", text)
        self.assertIn("Saldo pendiente: 118.500,25 €", text)

    def test_scanned_pdf_without_text_is_refused_with_ocr_hint(self):
        with self.assertRaises(pdftext.PdfError) as ctx:
            pdftext.extract_text(make_pdf([]))
        self.assertIn("OCR", str(ctx.exception))

    def test_non_pdf_and_damaged_files_are_refused(self):
        with self.assertRaises(pdftext.PdfError):
            pdftext.extract_text(b"Titular: no es un pdf")
        with self.assertRaises(pdftext.PdfError) as ctx:
            pdftext.extract_text(b"%PDF-1.4\nbasura que no es un pdf")
        self.assertIn("no se pudo leer", str(ctx.exception).lower())

    def test_size_limit(self):
        with mock.patch.object(pdftext, "MAX_PDF_BYTES", 100):
            with self.assertRaises(pdftext.PdfError):
                pdftext.extract_text(make_pdf(NOTE_LINES))


class ExtractEndpointTests(AppTestCase):
    def upload(self, cid, data, name="nota-simple.pdf", role="analista"):
        body = {"document": name, "pdf_base64": base64.b64encode(data).decode("ascii")}
        return self.call("POST", f"/api/cases/{cid}/diligence/extract-pdf", body, role)

    def test_pdf_fills_candidates_with_source_fragments(self):
        cid = self.new_case()
        s, b = self.upload(cid, make_pdf(NOTE_LINES))
        self.assertEqual(s, 200, b)
        fields = {f["field"]: f for f in b["found"]}
        self.assertEqual(fields["finca"]["value"], "93296")
        self.assertEqual(fields["saldo_pendiente"]["value"], 118500.25)
        self.assertEqual(fields["finca"]["document"], "nota-simple.pdf")
        self.assertIn("Finca: 93296", fields["finca"]["source_fragment"])
        self.assertIn("Titular:", b["text"])
        case = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        self.assertEqual(case["state"]["diligence"]["candidates"]["finca"][0]["value"], "93296")

    def test_bad_requests_are_400_and_nothing_is_saved(self):
        cid = self.new_case()
        for payload in [b"no es pdf", make_pdf([])]:
            s, b = self.upload(cid, payload)
            self.assertEqual(s, 400, b)
        s, b = self.call("POST", f"/api/cases/{cid}/diligence/extract-pdf", {"document": "x.pdf"}, "analista")
        self.assertEqual(s, 400)
        s, b = self.call("POST", f"/api/cases/{cid}/diligence/extract-pdf",
                         {"document": "x.pdf", "pdf_base64": "%%%no-base64%%%"}, "analista")
        self.assertEqual(s, 400)
        case = self.call("GET", f"/api/cases/{cid}", None, "analista")[1]
        self.assertEqual(case["state"].get("diligence", {}).get("candidates", {}), {})

    def test_unavailable_library_is_reported_not_crashing(self):
        cid = self.new_case()
        with mock.patch.object(pdftext, "available", return_value=False):
            s, b = self.upload(cid, make_pdf(NOTE_LINES))
        self.assertEqual(s, 503)
        self.assertIn("pypdf", b["error"])

    def test_pdf_route_needs_diligence_permission(self):
        cid = self.new_case()
        self.assertEqual(self.upload(cid, make_pdf(NOTE_LINES), role="gestor_campo")[0], 403)


if __name__ == "__main__":
    unittest.main()
