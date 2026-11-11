"""Preview the drum part: extract a measure range and render it to PNG."""
from __future__ import annotations

import copy
import io
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pymupdf
import verovio
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg

from render_musicxml import _flatten_uses


def extract(src: str, out: str, part_name: str, first: int, last: int) -> None:
    root = ET.parse(src).getroot()
    target = None
    for sp in root.findall("part-list/score-part"):
        if sp.find("part-name").text == part_name:
            target = sp.get("id")
    if target is None:
        raise SystemExit(f"part {part_name!r} not found")
    pl = root.find("part-list")
    for sp in list(pl.findall("score-part")):
        if sp.get("id") != target:
            pl.remove(sp)
    for p in list(root.findall("part")):
        if p.get("id") != target:
            root.remove(p)
    part = root.find("part")
    attrs = copy.deepcopy(part.find("measure/attributes"))
    for m in list(part.findall("measure")):
        num = int(m.get("number"))
        if not (first <= num <= last):
            part.remove(m)
    part.find("measure").insert(0, attrs)
    ET.indent(root)
    io.open(out, "w", encoding="utf-8").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode"))


def render(src: str, out_png: str, width: int = 2600, scale: int = 70) -> None:
    tk = verovio.toolkit()
    tk.loadFile(src)
    tk.setOptions({
        "pageWidth": width, "pageHeight": 900, "scale": scale,
        "adjustPageHeight": True, "footer": "none", "header": "none", "breaks": "none",
    })
    root = ET.fromstring(tk.renderToSVG(1))
    _flatten_uses(root)
    drawing = svg2rlg(io.BytesIO(ET.tostring(root, encoding="utf-8")))
    s = 2.2
    drawing.scale(s, s)
    drawing.width *= s
    drawing.height *= s
    pdf = out_png.replace(".png", ".pdf")
    renderPDF.drawToFile(drawing, pdf)
    doc = pymupdf.open(pdf)
    pix = doc[0].get_pixmap(dpi=150)
    pix.save(out_png)
    print("wrote", out_png, pix.width, "x", pix.height)


if __name__ == "__main__":
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 36
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 41
    part = sys.argv[3] if len(sys.argv) > 3 else "Drum set"
    tag = f"{first}_{last}"
    xml = os.path.join(HERE, f"preview_{tag}.musicxml")
    png = os.path.join(HERE, f"preview_{tag}.png")
    extract(os.path.join(HERE, "123_full_score.musicxml"), xml, part, first, last)
    render(xml, png)
