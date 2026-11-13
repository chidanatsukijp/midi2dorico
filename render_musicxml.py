"""Render a MusicXML file to PNG/PDF so we can actually look at the engraving.

Verovio produces SVG that references glyphs through <use xlink:href="#id">; MuPDF
does not resolve those, so we inline every <use> before rasterising.

This module owns the one and only Verovio setup; the PDF and part-preview scripts
import `open_score()` / `page_svg()` instead of re-implementing it.
"""
from __future__ import annotations

import copy
import io
import sys
import xml.etree.ElementTree as ET
from typing import Iterable, Tuple

import pymupdf
import verovio

SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "{http://www.w3.org/1999/xlink}href"

#: page geometry used for previews (Verovio units)
DEFAULT_PAGE_WIDTH = 2200
DEFAULT_PAGE_HEIGHT = 4000


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


def open_score(musicxml: str, page_width: int = DEFAULT_PAGE_WIDTH,
               page_height: int = DEFAULT_PAGE_HEIGHT, scale: int = 45,
               adjust_page_height: bool = True,
               breaks: str = "auto") -> Tuple[verovio.toolkit, int]:
    """Load a score once and configure the layout. Returns (toolkit, page count)."""
    tk = verovio.toolkit()
    if not tk.loadFile(musicxml):
        raise SystemExit(f"verovio could not read {musicxml}")
    tk.setOptions({
        "pageWidth": page_width,
        "pageHeight": page_height,
        "scale": scale,
        "adjustPageHeight": adjust_page_height,
        "breaks": breaks,
        "footer": "none",
        "header": "none",
    })
    return tk, tk.getPageCount()


def page_svg(tk: verovio.toolkit, page: int) -> ET.Element:
    """Render one page to an SVG tree with every <use> inlined."""
    root = ET.fromstring(tk.renderToSVG(page))
    _flatten_uses(root)
    return root


def svg_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render(musicxml: str, out_png: str, pages: int = 1, dpi: int = 130,
           page_width: int = DEFAULT_PAGE_WIDTH, scale: int = 45) -> None:
    tk, count = open_score(musicxml, page_width=page_width, scale=scale)
    for page in range(1, min(pages, count) + 1):
        root = page_svg(tk, page)
        doc = pymupdf.open(stream=svg_bytes(root), filetype="svg")
        pix = doc[0].get_pixmap(dpi=dpi)
        out = out_png if pages == 1 else out_png.replace(".png", f"_p{page}.png")
        pix.save(out)
        print(f"page {page}: wrote {out} ({pix.width}x{pix.height})")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: render_musicxml.py input.musicxml out.png [pages]")
    render(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1)
