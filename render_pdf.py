"""Render a MusicXML file to a multi-page A4/A3 PDF via Verovio.

Verovio produces SVG with <use> glyph references that MuPDF cannot resolve, so each
page is flattened and drawn onto a ReportLab canvas page.

Paper sizes below are in PDF points (1/72 inch):
  A4 = 595 x 842,  A3 = 842 x 1191
"""
from __future__ import annotations

import io
import sys
import xml.etree.ElementTree as ET

from reportlab.graphics import renderPDF
from reportlab.pdfgen import canvas as pdfcanvas
from svglib.svglib import svg2rlg

import verovio

from render_musicxml import _flatten_uses

PAPER = {"a4": (595, 842), "a3": (842, 1191), "a4l": (842, 595), "a3l": (1191, 842)}


def render_pdf(musicxml: str, out_pdf: str, paper: str = "a4", scale: int = 45,
               margin: float = 18.0) -> int:
    pw, ph = PAPER.get(paper.lower(), PAPER["a4"])
    tk = verovio.toolkit()
    if not tk.loadFile(musicxml):
        raise SystemExit(f"verovio could not read {musicxml}")
    tk.setOptions({
        "pageWidth": 2100,
        "pageHeight": 2970,
        "scale": scale,
        "adjustPageHeight": False,
        "breaks": "auto",
        "footer": "none",
        "header": "none",
    })
    total = tk.getPageCount()

    canvas = pdfcanvas.Canvas(out_pdf, pagesize=(pw, ph))
    written = 0
    for pg in range(1, total + 1):
        root = ET.fromstring(tk.renderToSVG(pg))
        _flatten_uses(root)
        drawing = svg2rlg(io.BytesIO(ET.tostring(root, encoding="utf-8")))
        if drawing is None:
            continue
        avail_w = pw - 2 * margin
        avail_h = ph - 2 * margin
        s = min(avail_w / max(drawing.width, 1e-6), avail_h / max(drawing.height, 1e-6))
        drawing.scale(s, s)
        drawing.width *= s
        drawing.height *= s
        # reportlab origin is bottom-left, so place from the top margin
        renderPDF.draw(drawing, canvas, margin, ph - margin - drawing.height)
        canvas.showPage()
        written += 1
    canvas.save()
    return written


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit("usage: render_pdf.py input.musicxml output.pdf [paper=a4|a3] [scale]")
    src, dst = args[0], args[1]
    paper = args[2] if len(args) > 2 else "a4"
    sc = int(args[3]) if len(args) > 3 else 45
    n = render_pdf(src, dst, paper=paper, scale=sc)
    print(f"wrote {dst} ({n} pages, {paper})")
