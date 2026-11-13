"""MusicXML <direction> builders: words, tempo, dynamics, harp pedals, tuplets."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from notation_values import DIVISIONS


def _direction_words(measure: ET.Element, text: str, offset: Optional[int] = None) -> None:
    d = ET.SubElement(measure, "direction", placement="above")
    dt = ET.SubElement(d, "direction-type")
    ET.SubElement(dt, "words").text = text
    if offset:
        ET.SubElement(d, "offset").text = str(offset)


def _tempo_direction(measure: ET.Element, bpm: float) -> None:
    d = ET.SubElement(measure, "direction")
    dt = ET.SubElement(d, "direction-type")
    metro = ET.SubElement(dt, "metronome")
    ET.SubElement(metro, "beat-unit").text = "quarter"
    ET.SubElement(metro, "per-minute").text = str(int(round(bpm)))
    ET.SubElement(d, "sound", tempo=str(int(round(bpm))))


def _plan_dynamics(part: Part) -> None:
    """Derive a small number of dynamic markings from MIDI velocity.

    Velocity is smoothed with a 4-beat median window; a level is only printed when
    it is actually sustained, so the score gets a handful of markings instead of
    one per note.
    """
    levels = ["pp", "p", "mp", "mf", "f", "ff"]
    bounds = [0, 45, 62, 78, 95, 112, 128]

    def level_of(v: float) -> str:
        for i in range(len(bounds) - 1):
            if bounds[i] <= v < bounds[i + 1]:
                return levels[i]
        return levels[-1]

    notes = [n for n in sorted(part.notes, key=lambda n: n.start) if n.velocity]
    if not notes:
        part.dynamic_marks = []
        return

    marks: List[Tuple[float, str]] = []
    current = None
    i = 0
    while i < len(notes):
        beat = notes[i].start
        window = [n.velocity for n in notes if abs(n.start - beat) <= 2.0]
        window.sort()
        med = window[len(window) // 2]
        lvl = level_of(med)
        # how long does this level persist?
        j = i
        while j < len(notes) and abs(notes[j].start - beat) <= 4.0:
            w = sorted(n.velocity for n in notes if abs(n.start - notes[j].start) <= 2.0)
            if level_of(w[len(w) // 2]) != lvl:
                break
            j += 1
        sustained = (notes[j - 1].start - beat) if j > i else 0.0
        if (current is None or lvl != current) and (current is None or sustained >= 2.0):
            marks.append((beat, lvl))
            current = lvl
        i = j if j > i else i + 1
    part.dynamic_marks = marks


DYNAMIC_VELOCITY = {"pp": 33, "p": 50, "mp": 70, "mf": 85, "f": 100, "ff": 115}


def _dynamic_direction(measure: ET.Element, level: str, offset: Optional[int] = None) -> None:
    d = ET.SubElement(measure, "direction", placement="below")
    dt = ET.SubElement(d, "direction-type")
    dyn = ET.SubElement(dt, "dynamics")
    ET.SubElement(dyn, level)
    if offset:
        ET.SubElement(d, "offset").text = str(offset)
    ET.SubElement(d, "sound", dynamics=str(DYNAMIC_VELOCITY.get(level, 80)))


def _harp_pedal_direction(measure: ET.Element, state: Dict[str, int], offset: Optional[int] = None) -> None:
    """Emit native MusicXML <harp-pedals> so the notation software draws a real
    pedal diagram (a run of text glyphs renders as boxes when the font lacks
    U+266D/U+266E)."""
    d = ET.SubElement(measure, "direction", placement="above")
    dt = ET.SubElement(d, "direction-type")
    hp = ET.SubElement(dt, "harp-pedals")
    for step in ("D", "C", "B", "E", "F", "G", "A"):
        pt = ET.SubElement(hp, "pedal-tuning")
        ET.SubElement(pt, "pedal-step").text = step
        ET.SubElement(pt, "pedal-alter").text = str(int(state.get(step, 0)))
    if offset:
        ET.SubElement(d, "offset").text = str(offset)


def _add_tuplet_brackets(measure: ET.Element) -> None:
    """Wrap each run of tuplet notes in <tuplet type="start"/"stop"> notation.

    <time-modification> alone gives the ratio but no bracket, so the score would
    show bare notes with nothing telling the reader it is a triplet. Engraving
    practice always brackets (or beams) the group.
    """
    voices: Dict[str, List[Tuple[float, ET.Element]]] = {}
    pos = 0.0
    for el in measure:
        if el.tag == "backup":
            pos -= float(el.find("duration").text) / DIVISIONS
            continue
        if el.tag == "forward":
            pos += float(el.find("duration").text) / DIVISIONS
            continue
        if el.tag != "note":
            continue
        d = float(el.find("duration").text) / DIVISIONS
        if el.find("chord") is not None:
            continue
        v = el.find("voice").text if el.find("voice") is not None else "1"
        voices.setdefault(v, []).append((pos, el))
        pos += d

    def _insert(note: ET.Element, type_: str) -> None:
        notations = note.find("notations")
        if notations is None:
            notations = ET.SubElement(note, "notations")
        idx = 0
        for i, child in enumerate(list(notations)):
            if child.tag in ("footnote", "level", "tied", "slur"):
                idx = i + 1
        tup = ET.Element("tuplet", number="1", type=type_)
        notations.insert(idx, tup)

    for _v, seq in voices.items():
        i = 0
        while i < len(seq):
            el = seq[i][1]
            tm = el.find("time-modification")
            if tm is None:
                i += 1
                continue
            ratio = (tm.find("actual-notes").text, tm.find("normal-notes").text)
            group = [seq[i]]
            end = seq[i][0] + float(el.find("duration").text) / DIVISIONS
            j = i + 1
            while j < len(seq):
                nxt = seq[j][1]
                ntm = nxt.find("time-modification")
                if ntm is None:
                    break
                if (ntm.find("actual-notes").text, ntm.find("normal-notes").text) != ratio:
                    break
                if abs(seq[j][0] - end) > 1e-6:
                    break
                group.append(seq[j])
                end = seq[j][0] + float(nxt.find("duration").text) / DIVISIONS
                j += 1
            if len(group) >= 2:
                _insert(group[0][1], "start")
                _insert(group[-1][1], "stop")
            i = j
