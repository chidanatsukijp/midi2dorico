"""Render a MusicXML file to a multi-page A4/A3 PDF via Verovio.

Verovio produces SVG with <use> glyph references that MuPDF cannot resolve, so each
page is flattened and drawn onto a ReportLab canvas page.

Preview-only adjustment: Verovio does not wrap part names, so a 60-character track
name (e.g. "Percussion (Triangle, Bass Drum, ...)") consumes the whole system width
and leaves no room for music. For rendering we shorten such names; the exported
score itself is left untouched.

Paper sizes below are in PDF points (1/72 inch):
  A4 = 595 x 842,  A3 = 842 x 1191
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

from reportlab.graphics import renderPDF
from reportlab.pdfgen import canvas as pdfcanvas
from svglib.svglib import svg2rlg

import verovio

from render_musicxml import _flatten_uses

PAPER = {"a4": (595, 842), "a3": (842, 1191), "a4l": (842, 595), "a3l": (1191, 842)}


def _shorten_name(name: str, limit: int) -> str:
    """Keep the head of a long part name for the preview."""
    if len(name) <= limit:
        return name
    head = name.split("(")[0].strip().rstrip(",")
    if head and len(head) <= limit:
        return head
    return (name[:limit].rsplit(" ", 1)[0] or name[:limit]).rstrip(" ,") + "\u2026"


def _preview_copy(musicxml: str, limit: int = 26) -> tuple[str, list[str]]:
    """Return a path safely renderable by Verovio, plus the renames applied."""
    tree = ET.parse(musicxml)
    renames = []
    for sp in tree.getroot().iter("score-part"):
        pn = sp.find("part-name")
        if pn is None or not (pn.text or "").strip():
            continue
        short = _shorten_name(pn.text.strip(), limit)
        if short != pn.text.strip():
            renames.append(f"{pn.text.strip()[:40]}... -> {short}")
            pn.text = short
    if not renames:
        return musicxml, []
    tmp = tempfile.NamedTemporaryFile("wb", suffix=".musicxml", delete=False)
    tmp.close()
    tree.write(tmp.name, encoding="utf-8", xml_declaration=True)
    return tmp.name, renames


def render_pdf(musicxml: str, out_pdf: str, paper: str = "a4", scale: int = 45,
               margin: float = 18.0) -> int:
    pw, ph = PAPER.get(paper.lower(), PAPER["a4"])
    src, renames = _preview_copy(musicxml)
    for line in renames:
        print(f"preview: shortened part name  {line}")
    tk = verovio.toolkit()
    if not tk.loadFile(src):
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
    if src != musicxml:
        os.unlink(src)
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
