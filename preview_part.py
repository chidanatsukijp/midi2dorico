"""Render one part of a score (a measure range) to a PNG preview."""
from __future__ import annotations

import copy
import io
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pymupdf
from render_musicxml import open_score, page_svg, svg_bytes
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg


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
    tk, _ = open_score(src, page_width=width, page_height=900, scale=scale,
                       adjust_page_height=True, breaks="none")
    drawing = svg2rlg(io.BytesIO(svg_bytes(page_svg(tk, 1))))
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
    import argparse

    ap = argparse.ArgumentParser(
        description="Render one part of a score (a measure range) to a PNG preview.")
    ap.add_argument("score", help="input .musicxml")
    ap.add_argument("part", help='part name exactly as in <part-name>, e.g. "Violin I"')
    ap.add_argument("first", type=int, help="first measure number")
    ap.add_argument("last", type=int, help="last measure number")
    ap.add_argument("--out", default=None,
                    help="output PNG (default: preview_<first>_<last>.png next to this script)")
    a = ap.parse_args()

    tag = f"{a.first}_{a.last}"
    sliced = os.path.join(HERE, f"preview_{tag}.musicxml")
    png = a.out or os.path.join(HERE, f"preview_{tag}.png")
    extract(a.score, sliced, a.part, a.first, a.last)
    render(sliced, png)
