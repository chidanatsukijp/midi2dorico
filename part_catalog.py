"""Instrument catalog: GM tables, noteheads, names, abbreviations, part list."""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from models import Score
from score_model import Part

HERE = os.path.dirname(os.path.abspath(__file__))


FOOT_PITCHES = {35, 36, 44}


def _is_foot(pitch: int) -> bool:
    return pitch in FOOT_PITCHES


def _notehead_for(pitch: int, duration: float = 1.0) -> Optional[str]:
    # DAWs routinely use GM 46 (open hi-hat) for ordinary closed hats. An open
    # hat is a sustained sound, so only a long hit gets the circle-x head.
    if pitch in (46, 52) and duration < 0.5 - 1e-9:
        return "x"
    return GM_NOTEHEAD.get(pitch)


GM_SOUND: Dict[int, str] = {}
for _p in (35, 36):
    GM_SOUND[_p] = "drum.kick-drum"
for _p in (37, 38, 39, 40):
    GM_SOUND[_p] = "drum.snare-drum"
for _p in (41, 43, 45, 47, 48, 50):
    GM_SOUND[_p] = "drum.tom-tom"
for _p in (42, 44, 46):
    GM_SOUND[_p] = "drum.hi-hat"
for _p in (49, 52, 55, 57):
    GM_SOUND[_p] = "drum.crash-cymbal"
for _p in (51, 53, 59):
    GM_SOUND[_p] = "drum.ride-cymbal"


def _unpitched_display(pitch: int, staff_lines: int) -> Tuple[str, str]:
    if staff_lines <= 1:
        return "B", "4"          # everything sits on the single line
    return GM_PERCUSSION_DISPLAY.get(pitch, ("A", "5"))


def _load_gm_tables() -> Tuple[Dict[str, int], Dict[str, int], Dict[int, Tuple[str, str]],
                               Dict[int, str], Dict[int, str]]:
    """Instrument/program/percussion tables live in data/gm.json so they can be
    adapted without editing Python."""
    path = os.path.join(HERE, "data", "gm.json")
    with open(path, encoding="utf-8-sig") as f:
        d = json.load(f)
    return {
        "instrument_order": d["instrument_order"],
        "midi_programs": d["midi_programs"],
        "percussion_display": {int(k): (str(v[0]), str(v[1]))
                                for k, v in d["percussion_display"].items()},
        "noteheads": {int(k): v for k, v in d["noteheads"].items()},
        "percussion_names": {int(k): v for k, v in d["percussion_names"].items()},
    }


_GM = _load_gm_tables()


INSTRUMENT_ORDER = _GM["instrument_order"]


MIDI_PROGRAMS = _GM["midi_programs"]


GM_PERCUSSION_DISPLAY = _GM["percussion_display"]


GM_NOTEHEAD = _GM["noteheads"]


GM_PERCUSSION_NAMES = _GM["percussion_names"]


PITCHED_SOUNDS = (
    ("piccolo", "wind.flutes.piccolo"),
    ("flute", "wind.flutes.flute"),
    ("cor anglais", "wind.reed.english-horn"),
    ("oboe", "wind.reed.oboe"),
    ("english horn", "wind.reed.english-horn"),
    ("clarinet", "wind.reed.clarinet"),
    ("bassoon", "wind.reed.bassoon"),
    ("horn", "brass.french-horn"),
    ("trumpet", "brass.trumpet"),
    ("trombone", "brass.trombone"),
    ("tuba", "brass.tuba"),
    ("violin", "strings.violin"),
    ("viola", "strings.viola"),
    ("cello", "strings.cello"),
    ("double bass", "strings.contrabass"),
    ("contrabass", "strings.contrabass"),
    ("timpani", "drum.timpani"),
    ("bass drum", "drum.bass-drum"),
    ("cymbal", "cymbal.crash"),
    ("piatti", "cymbal.crash"),
    ("harp", "pluck.harp"),
    ("piano", "keyboard.piano"),
    ("banjo", "pluck.banjo"),
)


def _instrument_sound(part_name: str, instrument_type: str):
    key = (part_name or "").lower()
    for needle, sound in PITCHED_SOUNDS:
        if needle in key:
            return sound
    return None


ABBREVIATIONS = [
    # specific first where one name contains another ("flugelhorn" / "horn",
    # "violin ii" / "violin i"); the earliest match in the name wins.
    ("violin ii", "Vln. II"), ("violin i", "Vln. I"), ("violin", "Vln."),
    ("viola", "Vla."), ("violoncello", "Vc."), ("cello", "Vc."), ("double bass", "Db."),
    ("trumpet", "Tpt."), ("flugelhorn", "Flug."),
    ("bass trombone", "B. Tbn."), ("trombone", "Tbn."),
    ("bassoon", "Bsn."), ("clarinet", "Cl."), ("oboe", "Ob."),
    ("piccolo", "Picc."), ("flute", "Fl."),
    ("horn", "Hn."), ("tuba", "Tb."), ("euphonium", "Euph."),
    ("timpani", "Timp."), ("percussion", "Perc."), ("harp", "Hp."),
    ("piano", "Pno."), ("celesta", "Cel."), ("organ", "Org."),
]


def _abbreviation(part_name: str) -> str:
    """Short form shown at the start of later systems, so a long part name does
    not eat the left margin."""
    key = (part_name or "").strip().lower()
    best = None
    for needle, abbr in ABBREVIATIONS:
        idx = key.find(needle)
        if idx != -1 and (best is None or idx < best[0]):
            best = (idx, abbr)
    if best is not None:
        return best[1]
    if len(key) > 12:
        first = key.split()[0][:6]
        return first[:1].upper() + first[1:] + "."
    return ""


def _group_parts(score: Score, score_order: str = "track",
                 program_techniques: Optional[Dict[str, str]] = None) -> List[Part]:
    groups: Dict[str, Part] = {}
    order: List[str] = []
    for track in sorted(score.tracks, key=lambda t: t.index):
        key = track.merge_group_id or f"__track_{track.index}"
        if key not in groups:
            groups[key] = Part(
                part_id=f"P{len(groups) + 1}",
                part_name=track.part_name or track.name,
                instrument_type=track.instrument_type,
                unpitched=track.unpitched,
                staff_lines=track.staff_lines,
                transpose=track.transpose_semitones,
            )
            order.append(key)
        part = groups[key]
        # Technique texts are attached in merge_tracks_by_group so that merged
        # tracks keep every member's markings.
        part.notes.extend(track.notes)
        part.directions.extend(track.directions)
        part.harp_pedal_changes.extend(track.harp_pedal_changes)
        part.is_grand_staff = part.instrument_type in ("piano", "harp")
        if track.part_name and not part.part_name:
            part.part_name = track.part_name
    parts = [groups[k] for k in order]
    if score_order == "standard":
        parts.sort(key=lambda p: INSTRUMENT_ORDER.get(p.instrument_type, 6))
    for i, p in enumerate(parts):
        p.part_id = f"P{i + 1}"
    return parts


def build_part_list(root: ET.Element, parts: List[Part]) -> ET.Element:
    """Write <part-list> with one score-part per part, including its instruments."""
    part_list = ET.SubElement(root, "part-list")
    for part in parts:
        sp = ET.SubElement(part_list, "score-part", id=part.part_id)
        ET.SubElement(sp, "part-name").text = part.part_name
        abbr = _abbreviation(part.part_name)
        if abbr:
            ET.SubElement(sp, "part-abbreviation").text = abbr
        if part.unpitched:
            # MusicXML requires ALL <score-instrument> elements before any
            # <midi-instrument>, so build them in two passes.
            if part.staff_lines <= 1:
                # Single orchestral percussion (bass drum, cymbal, ...): exactly
                # one instrument named after the part.
                pitches = sorted({n.pitch for n in part.notes}) or [36]
                iid = f"{part.part_id}-I1"
                si = ET.SubElement(sp, "score-instrument", id=iid)
                ET.SubElement(si, "instrument-name").text = part.part_name
                ET.SubElement(si, "instrument-sound").text = "drum.group"
                mi = ET.SubElement(sp, "midi-instrument", id=iid)
                ET.SubElement(mi, "midi-channel").text = "10"
                ET.SubElement(mi, "midi-program").text = "1"
                ET.SubElement(mi, "midi-unpitched").text = str(pitches[0] + 1)
            else:
                # Drum kit: one <score-instrument> per drum so the importer maps
                # every unpitched note to a real instrument.
                pitches = sorted({n.pitch for n in part.notes})
                for pitch in pitches:
                    iid = f"{part.part_id}-I{pitch + 1}"
                    si = ET.SubElement(sp, "score-instrument", id=iid)
                    ET.SubElement(si, "instrument-name").text = GM_PERCUSSION_NAMES.get(
                        pitch, f"Percussion {pitch}")
                    sound = GM_SOUND.get(pitch)
                    if sound:
                        ET.SubElement(si, "instrument-sound").text = sound
                for pitch in pitches:
                    iid = f"{part.part_id}-I{pitch + 1}"
                    mi = ET.SubElement(sp, "midi-instrument", id=iid)
                    ET.SubElement(mi, "midi-channel").text = "10"
                    ET.SubElement(mi, "midi-program").text = "1"
                    ET.SubElement(mi, "midi-unpitched").text = str(pitch + 1)
        else:
            si = ET.SubElement(sp, "score-instrument", id=f"{part.part_id}-I1")
            ET.SubElement(si, "instrument-name").text = part.part_name
            sound = _instrument_sound(part.part_name, part.instrument_type)
            if sound:
                ET.SubElement(si, "instrument-sound").text = sound
            mi = ET.SubElement(sp, "midi-instrument", id=f"{part.part_id}-I1")
            ET.SubElement(mi, "midi-channel").text = str((int(part.part_id[1:]) - 1) % 16 + 1)
            # MusicXML midi-program is 1-128 (not 0-127).
            ET.SubElement(mi, "midi-program").text = str(min(128, int(MIDI_PROGRAMS.get(part.instrument_type, 0)) + 1))
    return part_list
