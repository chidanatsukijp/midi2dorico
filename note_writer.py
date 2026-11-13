"""Emit one Piece as MusicXML <note> elements (or rests)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from notation_values import (DEFAULT_REST_STYLE, DIVISIONS, _decompose_duration,
                              _decompose_rest, _nearest_type, _type_from_beats)
from part_catalog import _is_foot, _notehead_for, _unpitched_display
from score_model import Piece


def _note_element(parent: ET.Element, piece: Piece, unpitched: bool = False, staff_lines: int = 5,
                  part_id: str = "") -> None:
    note = ET.SubElement(parent, "note")
    if piece.is_chord:
        ET.SubElement(note, "chord")
    if piece.is_rest:
        if piece.measure_rest:
            ET.SubElement(note, "rest", measure="yes")
        else:
            ET.SubElement(note, "rest")
    elif unpitched:
        step, octave = _unpitched_display(piece.pitch, staff_lines)
        up = ET.SubElement(note, "unpitched")
        ET.SubElement(up, "display-step").text = step
        ET.SubElement(up, "display-octave").text = octave
    else:
        pitch = ET.SubElement(note, "pitch")
        ET.SubElement(pitch, "step").text = piece.step
        if piece.alter != 0:
            ET.SubElement(pitch, "alter").text = str(piece.alter)
        ET.SubElement(pitch, "octave").text = str(piece.octave)
    ET.SubElement(note, "duration").text = str(round(piece.duration * DIVISIONS))
    # A middle note of a tie chain carries both: the STOP must be written before
    # the START so the tie reads as previous->this->next.
    if piece.tie_stop:
        ET.SubElement(note, "tie", type="stop")
    if piece.tie_start:
        ET.SubElement(note, "tie", type="start")
    if unpitched and not piece.is_rest and part_id:
        # Tells the importer exactly which instrument this note is. A single-line
        # orchestral percussion part (bass drum, cymbal, ...) has just one.
        iid = f"{part_id}-I1" if staff_lines <= 1 else f"{part_id}-I{piece.pitch + 1}"
        ET.SubElement(note, "instrument", id=iid)
    ET.SubElement(note, "voice").text = str(piece.voice)
    if piece.time_mod is not None:
        # Tuplet note: <type> derives from the "normal" (un-tupleted) value.
        actual, normal = piece.time_mod
        base = piece.duration * actual / normal
        t, dotted, _ = _type_from_beats(base)
        if t is not None:
            ET.SubElement(note, "type").text = t
        tm = ET.SubElement(note, "time-modification")
        ET.SubElement(tm, "actual-notes").text = str(actual)
        ET.SubElement(tm, "normal-notes").text = str(normal)
    else:
        t, dotted, tmod = _type_from_beats(piece.duration)
        if t is None:
            t, dotted = _nearest_type(piece.duration)
        ET.SubElement(note, "type").text = t
        if dotted:
            ET.SubElement(note, "dot")
        if tmod:
            tm = ET.SubElement(note, "time-modification")
            ET.SubElement(tm, "actual-notes").text = str(tmod[0])
            ET.SubElement(tm, "normal-notes").text = str(tmod[1])
    if unpitched and not piece.is_rest and staff_lines >= 5:
        ET.SubElement(note, "stem").text = "down" if _is_foot(piece.pitch) else "up"
        head = _notehead_for(piece.pitch, piece.duration)
        if head:
            ET.SubElement(note, "notehead").text = head
    if piece.staff:
        ET.SubElement(note, "staff").text = str(piece.staff)
    if piece.beam and not piece.is_chord:
        # Only the first note of a chord carries <beam>; repeating it on chord
        # members makes importers treat them as separate voice starts.
        ET.SubElement(note, "beam", number="1").text = piece.beam
    if (piece.is_arpeggio or piece.staccato or piece.glissando or piece.tie_start
            or piece.tie_stop):
        notations = ET.SubElement(note, "notations")
        # MusicXML notations child order: tied, slur, tuplet, glissando, slide,
        # ornaments, technical, articulations, dynamics, fermata, arpeggiate.
        if piece.tie_stop:
            ET.SubElement(notations, "tied", type="stop")
        if piece.tie_start:
            ET.SubElement(notations, "tied", type="start")
        if piece.glissando:
            for g in piece.glissando.split(","):
                if g:
                    ET.SubElement(notations, "glissando", type=g, **{"line-type": "wavy"})
        if piece.staccato:
            art = ET.SubElement(notations, "articulations")
            ET.SubElement(art, "staccato")
        if piece.is_arpeggio:
            ET.SubElement(notations, "arpeggiate", direction=piece.arpeggio_dir or "up")


def _emit_piece(parent: ET.Element, piece: Piece, unpitched: bool = False, staff_lines: int = 5,
                part_id: str = "", measure_len: Optional[float] = None,
                boundaries: Optional[List[float]] = None,
                rest_style: str = DEFAULT_REST_STYLE) -> None:
    """Emit a piece as one or several notes/rests.

    Non-standard durations are decomposed into tied notes (or, for rests, into a
    run of standard rests without ties, split at beat boundaries).
    """
    if piece.is_rest:
        if piece.measure_rest:
            # A whole-measure rest must stay a single symbol (never decomposed
            # into one rest per beat).
            _note_element(parent, piece, unpitched, staff_lines, part_id)
            return
        for bv, _t, _d, _tmod in _decompose_rest(piece.duration, piece.start, measure_len,
                                                 boundaries, rest_style):
            sub = Piece(start=piece.start, duration=bv, is_rest=True, voice=piece.voice, staff=piece.staff)
            _note_element(parent, sub, unpitched, staff_lines, part_id)
        return
    if piece.in_chord or piece.is_run:
        # Chord notes stay synchronized; micro-run notes keep their exact tuplet
        # durations. But a chord whose duration is not a single standard value
        # (e.g. 1.75 / 2.5 beats after rest folding) must still be decomposed into
        # tied standard chords, otherwise it would have no <type> at all.
        if piece.is_run or _type_from_beats(piece.duration)[0] is not None:
            _note_element(parent, piece, unpitched, staff_lines, part_id)
            return
        parts = _decompose_duration(piece.duration)
        n = len(parts)
        cursor = piece.start
        for i, (bv, _t, _d, _tmod) in enumerate(parts):
            sub = Piece(
                start=cursor, duration=bv,
                pitch=piece.pitch, step=piece.step, alter=piece.alter, octave=piece.octave,
                staff=piece.staff, voice=piece.voice,
                # All chord members decompose identically, so the <chord/> flag is
                # kept as-is at every tie segment to stay synchronized.
                is_chord=piece.is_chord, in_chord=True,
                is_arpeggio=(piece.is_arpeggio and i == 0),
                arpeggio_dir=piece.arpeggio_dir if i == 0 else "",
                staccato=(piece.staccato and i == 0),
                glissando=(piece.glissando if i == 0 else ""),
                beam=(piece.beam if i == 0 else ""),
                time_mod=(piece.time_mod if i == 0 else None),
                tie_start=(i < n - 1) or (piece.tie_start and i == n - 1),
                tie_stop=(i > 0) or (piece.tie_stop and i == 0),
            )
            _note_element(parent, sub, unpitched, staff_lines, part_id)
            cursor += bv
        return
    parts = _decompose_duration(piece.duration)
    n = len(parts)
    for i, (bv, _t, _d, _tmod) in enumerate(parts):
        sub = Piece(
            start=piece.start, duration=bv,
            pitch=piece.pitch, step=piece.step, alter=piece.alter, octave=piece.octave,
            staff=piece.staff, voice=piece.voice,
            is_chord=(piece.is_chord and i == 0),
            is_arpeggio=(piece.is_arpeggio and i == 0),
            arpeggio_dir=piece.arpeggio_dir if i == 0 else "",
            staccato=(piece.staccato and i == 0),
            glissando=(piece.glissando if i == 0 else ""),
            beam=(piece.beam if i == 0 else ""),
            tie_start=(i < n - 1) or (piece.tie_start and i == n - 1),
            tie_stop=(i > 0) or (piece.tie_stop and i == 0),
        )
        _note_element(parent, sub, unpitched, staff_lines, part_id)
