"""Render a MusicXML file to PNG so we can actually look at the engraving.

Verovio produces SVG that references glyphs through <use xlink:href="#id">; MuPDF
does not resolve those, so we inline every <use> before rasterising.
"""
from __future__ import annotations

import copy
import io
import sys
import xml.etree.ElementTree as ET

import pymupdf
import verovio

SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "{http://www.w3.org/1999/xlink}href"


def _flatten_uses(root: ET.Element) -> int:
    defs: dict[str, ET.Element] = {}
    for el in root.iter():
        if el.tag == f"{{{SVG_NS}}}g" and el.get("id"):
            defs[el.get("id")] = el

    count = 0
    changed = True
    while changed:
        changed = False
        for parent in list(root.iter()):
            for idx, child in enumerate(list(parent)):
                if child.tag != f"{{{SVG_NS}}}use":
                    continue
                href = child.get(XLINK) or child.get("href") or ""
                target = defs.get(href.lstrip("#"))
                if target is None:
                    parent.remove(child)
                    continue
                wrapper = ET.Element(f"{{{SVG_NS}}}g")
                if child.get("transform"):
                    wrapper.set("transform", child.get("transform"))
                for sub in list(target):
                    wrapper.append(copy.deepcopy(sub))
                parent.remove(child)
                parent.insert(idx, wrapper)
                count += 1
                changed = True
                break
            if changed:
                break
    return count


def render(musicxml: str, out_png: str, pages: int = 1, dpi: int = 130,
           page_width: int = 2200, scale: int = 45) -> None:
    tk = verovio.toolkit()
    if not tk.loadFile(musicxml):
        raise SystemExit(f"verovio could not read {musicxml}")
    tk.setOptions({
        "pageWidth": page_width,
        "pageHeight": 4000,
        "scale": scale,
        "adjustPageHeight": True,
        "breaks": "auto",
        "footer": "none",
        "header": "none",
    })
    for page in range(1, min(pages, tk.getPageCount()) + 1):
        svg = tk.renderToSVG(page)
        root = ET.fromstring(svg)
        n = _flatten_uses(root)
        flat = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        doc = pymupdf.open(stream=flat, filetype="svg")
        pix = doc[0].get_pixmap(dpi=dpi)
        out = out_png if pages == 1 else out_png.replace(".png", f"_p{page}.png")
        pix.save(out)
        print(f"page {page}: inlined {n} <use>, wrote {out} ({pix.width}x{pix.height})")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: render_musicxml.py input.musicxml out.png [pages]")
    render(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1)
