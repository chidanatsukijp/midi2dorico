"""Build a multi-part score-partwise MusicXML 4.0 full score for Dorico."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from models import Note, Score, Track
from config_schema import ScoreEngravingConfig
from harp_solver import DEFAULT_SHARP_SPELLING, DEFAULT_FLAT_SPELLING, STEP_SEMITONE
from midi_inspector import build_measure_map, measure_index


DIVISIONS = 1680

#: Rest spelling style, set from the engraving config at export time.
_REST_STYLE = {"style": "standard"}  # quarter = 1680; 16th = 420; 32nd = 210; 64th = 105; divisible by 3/5/7/16

INSTRUMENT_ORDER = {"strings": 0, "winds": 1, "brass": 2, "piano": 3, "harp": 4, "percussion": 5, "other": 6}

MIDI_PROGRAMS = {"strings": 48, "winds": 73, "brass": 61, "piano": 0, "harp": 46, "percussion": 0, "other": 0}


@dataclass
class Part:
    part_id: str
    part_name: str
    instrument_type: str
    notes: List[Note] = field(default_factory=list)
    directions: List[Tuple[float, str]] = field(default_factory=list)
    harp_pedal_changes: List[Tuple[float, Dict[str, int]]] = field(default_factory=list)
    is_grand_staff: bool = False
    unpitched: bool = False
    staff_lines: int = 5
    transpose: int = 0           # written-pitch interval (semitones), concert -> written
    dynamic_marks: List[Tuple[float, str]] = field(default_factory=list)


#: Standard GM percussion display positions on a 5-line staff:
#: MIDI pitch -> (display-step, display-octave). Covers GM kit + Steven Slate Drums.
GM_PERCUSSION_DISPLAY: Dict[int, Tuple[str, str]] = {
    36: ("F", "4"), 35: ("F", "4"),                                   # kick
    44: ("D", "4"),                                                   # pedal hi-hat
    37: ("C", "5"), 38: ("C", "5"), 39: ("C", "5"), 40: ("C", "5"),   # snare / rim / clap
    41: ("A", "4"), 43: ("B", "4"),                                   # floor toms
    45: ("D", "5"), 47: ("E", "5"), 48: ("F", "5"), 50: ("G", "5"),   # rack toms
    42: ("G", "5"), 46: ("G", "5"),                                   # hi-hat
    49: ("A", "5"), 57: ("A", "5"), 52: ("A", "5"), 55: ("A", "5"),   # crash / china / splash
    51: ("F", "5"), 59: ("F", "5"), 53: ("F", "5"),                   # ride / ride bell
    54: ("G", "5"), 56: ("E", "5"), 58: ("A", "5"),                   # tambourine / cowbell
    60: ("E", "5"), 61: ("C", "5"), 62: ("E", "5"), 63: ("E", "5"),
    64: ("C", "5"), 65: ("E", "5"), 66: ("A", "5"),
}

#: Notehead per drum: cymbals use "x", open hi-hat "circle-x", ride bell "diamond".
GM_NOTEHEAD: Dict[int, str] = {
    42: "x", 46: "circle-x", 44: "x", 49: "x", 57: "x", 55: "x", 52: "circle-x",
    51: "x", 59: "x", 53: "diamond", 54: "diamond", 56: "triangle", 37: "x",
    39: "slash", 58: "triangle", 69: "triangle", 70: "triangle", 80: "triangle",
    81: "triangle", 75: "triangle", 76: "triangle", 77: "triangle",
}

#: Foot-operated pieces: down-stem voice in Dorico's default kit presentation.
FOOT_PITCHES = {35, 36, 44}


def _is_foot(pitch: int) -> bool:
    return pitch in FOOT_PITCHES


def _notehead_for(pitch: int, duration: float = 1.0) -> Optional[str]:
    # DAWs routinely use GM 46 (open hi-hat) for ordinary closed hats. An open
    # hat is a sustained sound, so only a long hit gets the circle-x head.
    if pitch in (46, 52) and duration < 0.5 - 1e-9:
        return "x"
    return GM_NOTEHEAD.get(pitch)


#: MusicXML sound IDs so the importer matches a real instrument (Dorico ignores
#: <midi-program> and matches on <instrument-sound>).
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


#: GM percussion instrument names, used for <score-instrument> so that the
#: notation software maps each drum to a real instrument instead of guessing.
GM_PERCUSSION_NAMES: Dict[int, str] = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1", 37: "Side Stick", 38: "Acoustic Snare",
    39: "Hand Clap", 40: "Electric Snare", 41: "Low Floor Tom", 42: "Closed Hi-Hat",
    43: "High Floor Tom", 44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat",
    47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash Cymbal 1", 50: "High Tom",
    51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell", 54: "Tambourine",
    55: "Splash Cymbal", 56: "Cowbell", 57: "Crash Cymbal 2", 58: "Vibraslap",
    59: "Ride Cymbal 2", 60: "Hi Bongo", 61: "Low Bongo", 62: "Mute Hi Conga",
    63: "Open Hi Conga", 64: "Low Conga", 65: "High Timbale", 66: "Low Timbale",
    67: "High Agogo", 68: "Low Agogo", 69: "Cabasa", 70: "Maracas",
    71: "Short Whistle", 72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro",
    75: "Claves", 76: "Hi Wood Block", 77: "Low Wood Block", 78: "Mute Cuica",
    79: "Open Cuica", 80: "Mute Triangle", 81: "Open Triangle",
}


@dataclass
class Piece:
    start: float            # within measure, beats
    duration: float         # beats
    pitch: int = 0
    step: str = ""
    alter: int = 0
    octave: int = 0
    staff: int = 0          # 0 = single staff; 1/2 = grand staff
    voice: int = 1
    is_rest: bool = False
    is_chord: bool = False  # additional chord tone (needs MusicXML <chord/>)
    in_chord: bool = False  # part of a chord (stem or tone); skip duration decomposition
    is_arpeggio: bool = False
    arpeggio_dir: str = ""
    staccato: bool = False
    glissando: str = ""     # "", "start", "continue", "stop"
    beam: str = ""          # "", "begin", "continue", "end"
    time_mod: Optional[Tuple[int, int]] = None   # tuplet (actual, normal)
    is_run: bool = False    # micro-run note (skip duration decomposition)
    tie_start: bool = False
    tie_stop: bool = False
    measure_rest: bool = False   # whole-measure rest -> <rest measure="yes"/>


#: Standard note durations (beats) -> (type, dotted, time-modification), largest first.
STANDARD_DURATIONS = [
    (4.0, "whole", False, None),
    (3.0, "half", True, None),
    (2.0, "half", False, None),
    (1.5, "quarter", True, None),
    (1.0, "quarter", False, None),
    (0.75, "eighth", True, None),
    (0.5, "eighth", False, None),
    (0.25, "16th", False, None),
    (0.125, "32nd", False, None),
]


def _decompose_duration(beats: float) -> List[Tuple[float, str, bool, Optional[Tuple[int, int]]]]:
    """Greedily split a duration into standard note values (largest first).

    A note whose duration is not a single standard value (e.g. 2.5 beats = half +
    eighth, or 6.0 = whole + half) must be emitted as tied standard notes.
    """
    parts: List[Tuple[float, str, bool, Optional[Tuple[int, int]]]] = []
    remaining = beats
    guard = 0
    while remaining > 1e-6 and guard < 64:
        guard += 1
        for bv, t, d, tmod in STANDARD_DURATIONS:
            if bv <= remaining + 1e-6:
                parts.append((bv, t, d, tmod))
                remaining -= bv
                break
        else:
            parts.append((0.25, "16th", False, None))
            remaining -= 0.25
    return parts


def _type_from_beats(b: float) -> Tuple[Optional[str], bool, Optional[Tuple[int, int]]]:
    table = [
        (4.0, "whole", False), (3.0, "half", True), (2.0, "half", False),
        (1.5, "quarter", True), (1.0, "quarter", False), (0.75, "eighth", True),
        (0.5, "eighth", False), (0.25, "16th", False), (0.125, "32nd", False),
        (0.0625, "64th", False),
    ]
    for bv, t, d in table:
        if abs(b - bv) < 1e-6:
            return t, d, None
    if abs(b - 4.0 / 3.0) < 1e-3:
        return "quarter", False, (3, 2)
    if abs(b - 2.0 / 3.0) < 1e-3:
        return "eighth", False, (3, 2)
    if abs(b - 1.0 / 3.0) < 1e-3:
        return "16th", False, (3, 2)
    # Other tuplet ratios (5:4, 6:4, 7:4) over the usual bases, so a quintuplet or
    # septuplet note never falls back to a wrong plain <type>.
    for act in (3, 5, 6, 7):
        norm = 2 if act == 3 else 4
        for base_b, base_t in ((0.5, "eighth"), (0.25, "16th"), (0.125, "32nd")):
            if abs(b - base_b * norm / act) < 1e-3:
                return base_t, False, (act, norm)
    return None, False, None  # non-standard duration


def _nearest_type(b: float):
    """Closest standard note value, used only as a last-resort <type> fallback."""
    best = min(STANDARD_DURATIONS, key=lambda row: abs(math.log2(max(b, 1e-6)) - math.log2(row[0])))
    return best[1], best[2]


def _part_fifths(key_fifths: int, transpose: int) -> int:
    """Key signature (in fifths) of the written key for a transposing part."""
    if not transpose:
        return key_fifths
    delta = ((transpose * 7 + 6) % 12) - 6
    fifths = key_fifths + delta
    # Do not fold +6/+7 back into flats: F#/C# major are legal written keys.
    while fifths > 7:
        fifths -= 12
    while fifths < -7:
        fifths += 12
    return fifths


#: MusicXML sound IDs for the pitched instruments we emit. Dorico matches
#: instruments on <instrument-sound> (it ignores <midi-program>).
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


def _spell_note(n: Note, flat: bool = False) -> None:
    if not n.step:
        table = DEFAULT_FLAT_SPELLING if flat else DEFAULT_SHARP_SPELLING
        step, alter = table[n.pitch % 12]
        n.step = step
        n.alter = alter
        n.octave = round((n.pitch - STEP_SEMITONE[step] - alter) / 12) - 1


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


def _split_to_pieces(part: Part, measures: List[Tuple[float, int, int]]) -> Dict[int, List[Piece]]:
    result: Dict[int, List[Piece]] = defaultdict(list)
    notes = list(part.notes)
    if part.unpitched and part.staff_lines <= 1:
        # A single-line percussion part draws EVERY pitch on the same line, so two
        # simultaneous notes would look like one doubled notehead. Keep only one.
        by_onset: Dict[float, object] = {}
        kept = []
        for n in sorted(notes, key=lambda x: x.start):
            key = round(n.start, 6)
            cur = by_onset.get(key)
            if cur is None:
                by_onset[key] = n
                kept.append(n)
            elif (n.end - n.start) > (cur.end - cur.start):
                cur.end = n.end
        notes = kept
    for n in sorted(notes, key=lambda n: n.start):
        if n.end - n.start < 1e-9:
            continue
        mi = measure_index(n.start, measures)
        seg_start = n.start
        segments: List[Tuple[int, float, float]] = []
        while seg_start < n.end - 1e-9 and mi < len(measures):
            m_start, num, den = measures[mi]
            m_end = m_start + num * 4.0 / den
            seg_end = min(n.end, m_end)
            segments.append((mi, seg_start - m_start, seg_end - seg_start))
            seg_start = seg_end
            mi += 1
        # A drum hit must not ring over the barline; drop a tiny overhang rather
        # than emitting a tie (or a stray 16th rest at the next downbeat).
        if part.unpitched and len(segments) >= 2 and segments[-1][2] < 0.5 - 1e-9:
            segments = [segments[0]]
        for pi, (m, rel_start, dur) in enumerate(segments):
            is_first = pi == 0
            is_last = pi == len(segments) - 1
            result[m].append(Piece(
                start=rel_start, duration=dur,
                pitch=n.pitch, step=n.step, alter=n.alter, octave=n.octave,
                staff=n.staff or 0,
                is_arpeggio=(n.is_arpeggio and is_first),
                arpeggio_dir=(n.arpeggio_dir or "") if is_first else "",
                staccato=(n.staccato and is_first),
                glissando=(n.glissando if is_first else ""),
                # A tuplet note split by a barline keeps its ratio on BOTH sides:
                # each half is then a valid (smaller) tuplet value tied across
                # the barline instead of an illegal <type>/duration pair.
                time_mod=n.time_mod,
                is_run=n.is_run,
                tie_start=(not is_last), tie_stop=(not is_first),
            ))
    return result


def _is_valid_syncopation(start: float, end: float, beats_per_measure: float) -> bool:
    """True when one untied note from `start` to `end` keeps the beat visible.

    Rule (Gould, Behind Bars): a note that begins on an off-beat subdivision may
    not run past the next beat boundary, and in 4/4 a note on beat 2 may not run
    across the middle of the bar.
    """
    dur = end - start
    if _type_from_beats(dur)[0] is None:
        return False
    frac = start - math.floor(start + 1e-9)
    next_beat = math.floor(start + 1e-9) + 1.0
    if abs(frac - 0.25) < 1e-6 or abs(frac - 0.75) < 1e-6 or abs(frac - 0.5) < 1e-6:
        return end <= next_beat + 1e-6
    if abs(beats_per_measure - 4.0) < 1e-6 and abs(start - 1.0) < 1e-6 and end > 2.5 + 1e-6:
        return False
    return True


def _split_at_beats(start: float, dur: float, beats_per_measure: float):
    """Split [start, start+dur) at the next beat (and the 4/4 midpoint)."""
    parts = []
    cur = start
    rem = dur
    guard = 0
    mid = beats_per_measure / 2.0
    while rem > 1e-6 and guard < 32:
        guard += 1
        on_beat = abs(cur - round(cur)) < 1e-6
        if not on_beat:
            chunk = min(rem, math.ceil(cur - 1e-9) - cur)
        elif (abs(beats_per_measure - 4.0) < 1e-6 and cur < mid - 1e-6
              and cur + rem > mid + 1e-6 and cur > 1e-6):
            chunk = mid - cur
        else:
            chunk = rem
        parts.append((cur, chunk))
        cur += chunk
        rem -= chunk
    return parts


def _split_voices(pieces: List[Piece]) -> Tuple[List[Piece], List[Piece]]:
    """Greedy two-voice split for one staff.

    Notes that share an onset (a chord) always stay together. A group that starts
    while voice 1 is still sounding is moved to voice 2 - that is exactly the case
    (held note + moving line) that used to truncate the long note. At most two
    voices are produced, which is what Dorico needs on one staff.
    """
    groups: List[Tuple[float, List[Piece]]] = []
    for p in sorted(pieces, key=lambda x: x.start):
        if groups and abs(p.start - groups[-1][0]) < 1e-9:
            groups[-1][1].append(p)
        else:
            groups.append((p.start, [p]))

    v1: List[Piece] = []
    v2: List[Piece] = []
    v1_end = 0.0
    v2_end = 0.0
    for start, grp in groups:
        end = max(q.start + q.duration for q in grp)
        if start >= v1_end - 1e-9:
            v1.extend(grp)
            v1_end = max(v1_end, end)
        elif start >= v2_end - 1e-9:
            v2.extend(grp)
            v2_end = max(v2_end, end)
        else:                      # both voices busy: keep it with voice 1
            v1.extend(grp)
            v1_end = max(v1_end, end)
    return v1, v2


def _fill_staff(pieces: List[Piece], beats_per_measure: float, voice: int, staff: int = 0,
                beat_beaming: bool = False, percussion: bool = False) -> List[Piece]:
    """Insert rests and normalize chords so a voice sums exactly to the measure.

    Simultaneous notes (chords) are equalized to a common duration (longest) so
    the MusicXML voice timeline stays consistent; rests carry the right <staff>
    for grand-staff parts.
    """
    ordered = sorted(pieces, key=lambda p: p.start)

    # Equalize chord tones to a common duration.
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and abs(ordered[j].start - ordered[i].start) < 1e-9:
            j += 1
        group = ordered[i:j]
        if len(group) >= 2:
            longest = max(p.duration for p in group)
            for p in group:
                p.duration = longest
        i = j

    events: List[Piece] = []
    cursor = 0.0
    last_group: List[Piece] = []
    for p in ordered:
        if last_group and abs(p.start - last_group[0].start) < 1e-9:
            # Another chord tone at the same onset: it shares the group's fate.
            last_group.append(p)
        else:
            # A new onset that begins before the previous chord has finished would
            # push this voice past the barline, so trim the whole previous chord.
            if last_group and p.start < cursor - 1e-9:
                for q in last_group:
                    q.duration = max(1e-9, p.start - q.start)
                cursor = p.start
            last_group = [p]
        if p.start > cursor + 1e-9:
            gap = p.start - cursor
            absorbed = False
            if gap < 1.0 - 1e-9 and events and not events[-1].is_rest:
                # A sub-quarter rest inside a phrase is exactly what we want to
                # avoid: swallow it into the preceding chord when that keeps the
                # note value clean (no tie).
                last_start = events[-1].start
                durs = [q.duration + gap for q in events
                        if not q.is_rest and abs(q.start - last_start) < 1e-9 and not q.glissando]
                if durs and all(_type_from_beats(d)[0] is not None for d in durs):
                    for q in events:
                        if not q.is_rest and abs(q.start - last_start) < 1e-9 and not q.glissando:
                            q.duration += gap
                    cursor = p.start
                    absorbed = True
            if not absorbed:
                events.append(Piece(start=cursor, duration=p.start - cursor,
                                    is_rest=True, voice=voice, staff=staff))
        p.voice = voice
        events.append(p)
        cursor = max(cursor, p.start + p.duration)
    if cursor < beats_per_measure - 1e-9:
        trailing = beats_per_measure - cursor
        # A trailing rest is clean only if it starts on a beat and spans whole
        # beats, so push the last note to the next beat (or over the whole gap
        # when it is shorter than a quarter).
        beat = math.ceil(cursor - 1e-9)
        rem = beats_per_measure - beat
        if trailing < 1.0 - 1e-9:
            target = beats_per_measure
        elif rem >= 1.0 - 1e-9 and abs(rem - round(rem)) < 1e-9:
            target = beat
        else:
            target = cursor
        extend_by = target - cursor
        last_start = None
        for p in reversed(events):
            if not p.is_rest and not p.glissando:
                last_start = p.start
                break
        if extend_by > 1e-9 and last_start is not None:
            dur = None
            for p in events:
                if not p.is_rest and not p.glissando and abs(p.start - last_start) < 1e-9:
                    dur = p.duration + extend_by
            if dur is not None and _type_from_beats(dur)[0] is not None:
                for p in events:
                    if not p.is_rest and not p.glissando and abs(p.start - last_start) < 1e-9:
                        p.duration += extend_by
                cursor += extend_by
    if cursor < beats_per_measure - 1e-9:
        events.append(Piece(start=cursor, duration=beats_per_measure - cursor,
                            is_rest=True, voice=voice, staff=staff,
                            measure_rest=(cursor < 1e-9)))

    # Decompose non-standard chord groups into tied standard sub-chords in
    # time-slice order, so every <chord/> member lines up with the right slice
    # (emitting all slices of pitch 1 first would attach pitch 2's <chord/> to a
    # later onset and corrupt the rhythm).
    expanded: List[Piece] = []
    ei = 0
    while ei < len(events):
        p = events[ei]
        if p.is_rest:
            expanded.append(p)
            ei += 1
            continue
        ej = ei
        while (ej < len(events) and not events[ej].is_rest
               and abs(events[ej].start - p.start) < 1e-9):
            ej += 1
        group = events[ei:ej]
        dur = max(g.duration for g in group)
        beats_ok = percussion or _is_valid_syncopation(p.start, p.start + dur, beats_per_measure)
        if any(g.is_run for g in group) or (_type_from_beats(dur)[0] is not None and beats_ok):
            expanded.extend(group)
            ei = ej
            continue
        if _type_from_beats(dur)[0] is not None and not beats_ok:
            # Standard value, but it hides the beat: split at the beat and tie.
            pieces = _split_at_beats(p.start, dur, beats_per_measure)
            np_ = len(pieces)
            for k, (at, bv) in enumerate(pieces):
                for gi, g in enumerate(group):
                    expanded.append(Piece(
                        start=at, duration=bv,
                        pitch=g.pitch, step=g.step, alter=g.alter, octave=g.octave,
                        staff=g.staff, voice=g.voice,
                        is_chord=(gi > 0), in_chord=True,
                        is_arpeggio=(g.is_arpeggio and k == 0),
                        arpeggio_dir=g.arpeggio_dir if k == 0 else "",
                        staccato=(g.staccato and k == 0),
                        glissando=(g.glissando if k == 0 else ""),
                        beam=(g.beam if k == 0 else ""),
                        time_mod=(g.time_mod if k == 0 else None),
                        tie_start=(not percussion) and ((k < np_ - 1) or (g.tie_start and k == np_ - 1)),
                        tie_stop=(not percussion) and ((k > 0) or (g.tie_stop and k == 0)),
                    ))
            ei = ej
            continue
        # Split at beat / midpoint boundaries first, then spell each chunk with a
        # single standard value. Splitting purely by note value (greedy largest
        # first) would start a dotted half at an off-beat position and hide the beat.
        pieces: List[Tuple[float, float]] = []
        for at0, chunk in _split_at_beats(p.start, dur, beats_per_measure):
            for bv, _t, _d, _tmod in _decompose_duration(chunk):
                pieces.append((at0, bv))
                at0 += bv
        np_ = len(pieces)
        for k, (at, bv) in enumerate(pieces):
            for gi, g in enumerate(group):
                if percussion and k > 0:
                    # A drum hit is never tied: turn the decomposed tail into a rest
                    # on the first chord member only.
                    if gi == 0:
                        expanded.append(Piece(start=at, duration=bv, is_rest=True,
                                              voice=g.voice, staff=g.staff))
                    continue
                expanded.append(Piece(
                    start=at, duration=bv,
                    pitch=g.pitch, step=g.step, alter=g.alter, octave=g.octave,
                    staff=g.staff, voice=g.voice,
                    is_chord=(gi > 0), in_chord=True,
                    is_arpeggio=(g.is_arpeggio and k == 0),
                    arpeggio_dir=g.arpeggio_dir if k == 0 else "",
                    staccato=(g.staccato and k == 0),
                    glissando=(g.glissando if k == 0 else ""),
                    beam=(g.beam if k == 0 else ""),
                    time_mod=(g.time_mod if k == 0 else None),
                    tie_start=(not percussion) and ((k < np_ - 1) or (g.tie_start and k == np_ - 1)),
                    tie_stop=(not percussion) and ((k > 0) or (g.tie_stop and k == 0)),
                ))
            at += bv
        ei = ej
    events = expanded

    # Mark additional chord tones (simultaneous notes within this voice/staff).
    last_start = None
    prev_note = None
    for p in events:
        if p.is_rest:
            last_start = None
            prev_note = None
            continue
        if last_start is not None and abs(p.start - last_start) < 1e-9:
            p.is_chord = True
            p.in_chord = True
            if prev_note is not None:
                prev_note.in_chord = True
        last_start = p.start
        prev_note = p

    # Beam eighth/16th notes: group by beat, breaking at rests and quarter notes.
    positions: List[List[Piece]] = []
    cur: List[Piece] = []
    for p in events:
        if p.is_rest:
            if cur:
                positions.append(cur)
                cur = []
            positions.append([p])
            continue
        if cur and abs(p.start - cur[-1].start) < 1e-9:
            cur.append(p)
        else:
            if cur:
                positions.append(cur)
            cur = [p]
    if cur:
        positions.append(cur)

    # Imaginary-barline rule: beams must not cross the midpoint of the measure.
    # 8th notes beam in half-measure groups; 16th notes (and shorter) beam per beat.
    midpoint = beats_per_measure / 2.0
    beam_groups: List[List[List[Piece]]] = []
    cur_group: List[List[Piece]] = []
    for pos in positions:
        p0 = pos[0]
        if p0.is_rest or p0.duration >= 1.0 - 1e-9:
            if cur_group:
                beam_groups.append(cur_group)
                cur_group = []
            continue
        if cur_group:
            prev_start = cur_group[-1][0].start
            if beat_beaming:
                # Drum notation beams per beat, never across a beat.
                if int(p0.start + 1e-9) != int(prev_start + 1e-9):
                    beam_groups.append(cur_group)
                    cur_group = []
            elif p0.duration >= 0.5 - 1e-9:
                # 8th note (or longer): break only at the imaginary barline.
                if prev_start < midpoint <= p0.start:
                    beam_groups.append(cur_group)
                    cur_group = []
            else:
                # 16th note or shorter: break at every beat.
                if int(p0.start + 1e-9) != int(prev_start + 1e-9):
                    beam_groups.append(cur_group)
                    cur_group = []
        cur_group.append(pos)
    if cur_group:
        beam_groups.append(cur_group)

    for g in beam_groups:
        if len(g) >= 2:
            for i, pos in enumerate(g):
                b = "begin" if i == 0 else ("end" if i == len(g) - 1 else "continue")
                for p in pos:
                    p.beam = b
    return events


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


#: Values a rest may use when we want to avoid 8th/16th rests entirely.
_BIG_REST_VALUES = ((16, 4.0), (12, 3.0), (8, 2.0), (6, 1.5), (4, 1.0))


def _quarter_only_split(total: float):
    """Express `total` beats using quarter-or-longer values only, else None."""
    n = int(round(total * 4))
    if n <= 0:
        return None
    best = {0: None}
    for i in range(1, n + 1):
        for val, beats in _BIG_REST_VALUES:
            if i - val in best:
                best[i] = (i - val, beats)
                break
    if n not in best:
        return None
    out = []
    k = n
    while k:
        prev, beats = best[k]
        out.append(beats)
        k = prev
    return out


def _decompose_rest(beats: float, start: float,
                    measure_len: Optional[float] = None) -> List[Tuple[float, str, bool, Optional[Tuple[int, int]]]]:
    """Split a rest at beat boundaries, shortest piece first.

    A 1.25-beat rest beginning at beat 2.75 becomes "16th + quarter" (short piece
    on the beat's tail, then the full beat) instead of "quarter + 16th", which is
    the standard, beat-aligned engraving convention.
    """
    if _REST_STYLE.get("style") == "merged":
        single = _type_from_beats(beats)
        if single[0] is not None:
            return [(beats, single[0], single[1], single[2])]
    # In 4/4 a rest must not cross the middle of the bar: crotchets at each end of
    # the bar are separated by two crotchet rests, never by a minim rest.
    if measure_len and abs(measure_len % 4.0) < 1e-9 and measure_len >= 4.0:
        mid = measure_len / 2.0
        if start < mid - 1e-9 and start + beats > mid + 1e-9:
            return (_decompose_rest_segment(mid - start, start)
                    + _decompose_rest_segment(start + beats - mid, mid))
    return _decompose_rest_segment(beats, start)


def _decompose_rest_segment(beats: float, start: float) -> List[Tuple[float, str, bool, Optional[Tuple[int, int]]]]:
    """Spell one rest segment that does not cross the bar midpoint."""
    parts: List[Tuple[float, str, bool, Optional[Tuple[int, int]]]] = []
    # A rest whose total length is already one standard value stays a SINGLE
    # symbol (a one-beat rest at 1.5 is a quarter rest, not two 8th rests), except
    # for a half rest on an odd beat, whose position would be ambiguous.
    if _REST_STYLE.get("style") == "merged":
        single = _type_from_beats(beats)
        if single[0] is not None:
            return [(beats, single[0], single[1], single[2])]
    single = _type_from_beats(beats)
    if single[0] is not None and not (abs(beats - 2.0) < 1e-9 and abs(start % 2.0) > 1e-9):
        return [(beats, single[0], single[1], single[2])]
    remaining = beats
    cur = start
    guard = 0
    while remaining > 1e-6 and guard < 64:
        guard += 1
        on_beat = abs(cur - round(cur)) < 1e-6
        if on_beat:
            # Starting on a beat: keep the whole span as the largest resting value
            # (3 beats -> one dotted half rest, 4 beats -> one whole rest), so we
            # never chop a long rest into a row of quarter rests.
            chunk = remaining
        else:
            # Starting off the beat: fill to the next beat first (short piece
            # first), then the remainder is beat-aligned.
            chunk = min(math.ceil(cur - 1e-9) - cur, remaining)
        if abs(chunk - 2.0) < 1e-9 and abs(cur % 2.0) > 1e-9:
            # a minim rest on beat 2/4 is ambiguous -> two crotchet rests
            chunk = 1.0
        big = _quarter_only_split(chunk)
        if big is not None:
            for v in big:
                parts.extend(_decompose_duration(v))
        else:
            parts.extend(_decompose_duration(chunk))
        remaining -= chunk
        cur += chunk
    return parts


def _emit_piece(parent: ET.Element, piece: Piece, unpitched: bool = False, staff_lines: int = 5,
                part_id: str = "", measure_len: Optional[float] = None) -> None:
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
        for bv, _t, _d, _tmod in _decompose_rest(piece.duration, piece.start, measure_len):
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


def _clef_for(instrument_type: str, part_name: str) -> Tuple[str, str]:
    n = (part_name or "").lower()
    # Timpani is pitched percussion and reads bass clef, not a percussion clef.
    if "timpani" in n:
        return "F", "4"
    if instrument_type == "percussion":
        return "percussion", "0"
    if any(k in n for k in ("bass", "cello", "trombone", "tuba", "bassoon")):
        return "F", "4"
    if "viola" in n:
        return "C", "3"
    return "G", "2"


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


def _build_measure(part: Part, m: int, measures: List[Tuple[float, int, int]], key_fifths: int,
                   pieces_map: Dict[int, List[Piece]],
                   tempos: Optional[List[Tuple[float, float]]] = None) -> ET.Element:
    measure = ET.Element("measure", number=str(m + 1))
    m_start, num, den = measures[m]
    m_len = num * 4.0 / den

    # Emit attributes (divisions/key/clef) only in the first measure; <time> on
    # the first measure and whenever the time signature changes.
    emit_time = (m == 0) or (measures[m][1] != measures[m - 1][1] or measures[m][2] != measures[m - 1][2])
    if m == 0 or emit_time:
        attrs = ET.SubElement(measure, "attributes")
        # MusicXML <attributes> order: divisions, key, time, staves, clef,
        # staff-details, transpose, ...
        if m == 0:
            ET.SubElement(attrs, "divisions").text = str(DIVISIONS)
            key = ET.SubElement(attrs, "key")
            ET.SubElement(key, "fifths").text = str(_part_fifths(key_fifths, part.transpose))
        if emit_time:
            tm = ET.SubElement(attrs, "time")
            ET.SubElement(tm, "beats").text = str(num)
            ET.SubElement(tm, "beat-type").text = str(den)
        if m == 0:
            if part.is_grand_staff:
                ET.SubElement(attrs, "staves").text = "2"
                c1 = ET.SubElement(attrs, "clef", number="1")
                ET.SubElement(c1, "sign").text = "G"
                ET.SubElement(c1, "line").text = "2"
                c2 = ET.SubElement(attrs, "clef", number="2")
                ET.SubElement(c2, "sign").text = "F"
                ET.SubElement(c2, "line").text = "4"
            elif part.unpitched:
                clef = ET.SubElement(attrs, "clef")
                ET.SubElement(clef, "sign").text = "percussion"
                # Always spell out the line count so the importer never guesses
                # (a drum kit is a 5-line staff, single orchestral percussion 1).
                sd = ET.SubElement(attrs, "staff-details")
                ET.SubElement(sd, "staff-lines").text = str(part.staff_lines)
            else:
                sign, line = _clef_for(part.instrument_type, part.part_name)
                clef = ET.SubElement(attrs, "clef")
                ET.SubElement(clef, "sign").text = sign
                ET.SubElement(clef, "line").text = line
            if part.transpose:
                # MusicXML: notes are in WRITTEN pitch and <transpose> is
                # written -> sounding, i.e. the negative of the written interval
                # (Horn in F is written a 5th higher, so sounding = written - 7).
                delta = -part.transpose
                oct_change = int(round(delta / 12)) if abs(delta) >= 12 else 0
                chromatic = delta - 12 * oct_change
                tr = ET.SubElement(attrs, "transpose")
                # diatonic is the staff-step part of the interval only; the
                # octave itself goes in <octave-change>, so derive it from the
                # chromatic remainder (a pure octave gives diatonic 0).
                ET.SubElement(tr, "diatonic").text = str(int(round(chromatic * 7 / 12)))
                ET.SubElement(tr, "chromatic").text = str(chromatic)
                if oct_change:
                    ET.SubElement(tr, "octave-change").text = str(oct_change)

    # Tempo markings (emitted in the first part only, at tempo changes).
    if tempos:
        for beat, bpm in tempos:
            if m_start <= beat < m_start + m_len:
                _tempo_direction(measure, bpm)

    # Dynamics derived from MIDI velocity.
    for beat, level in part.dynamic_marks:
        if m_start <= beat < m_start + m_len:
            _dynamic_direction(measure, level, int(round((beat - m_start) * DIVISIONS)))

    # Technique / pedal directions placed at their exact beat inside the measure.
    for beat, text in part.directions:
        if m_start <= beat < m_start + m_len:
            _direction_words(measure, text, int(round((beat - m_start) * DIVISIONS)))
    for beat, state in part.harp_pedal_changes:
        if m_start <= beat < m_start + m_len:
            _harp_pedal_direction(measure, state, int(round((beat - m_start) * DIVISIONS)))

    all_pieces = pieces_map.get(m, [])
    backup_dur = str(round(m_len * DIVISIONS))

    def _emit_voice(pieces: List[Piece], voice: int, staff_no: int,
                    perc: bool = False) -> None:
        for p in _fill_staff(pieces, m_len, voice, staff_no, percussion=perc):
            _emit_piece(measure, p, part.unpitched, part.staff_lines, part.part_id, m_len)

    def _backup() -> None:
        b = ET.SubElement(measure, "backup")
        ET.SubElement(b, "duration").text = backup_dur

    if part.is_grand_staff:
        for staff_no, base_voice in ((1, 1), (2, 5)):
            staff_pieces = [p for p in all_pieces if p.staff == staff_no]
            v1, v2 = _split_voices(staff_pieces)
            _emit_voice(v1, base_voice, staff_no)
            if v2:
                _backup()
                _emit_voice(v2, base_voice + 1, staff_no)
            if staff_no == 1:
                _backup()
    elif part.unpitched and part.staff_lines >= 5:
        # Drum kit: hands (up-stem voice 1) vs feet (down-stem voice 2), matching
        # Dorico's own kit presentation. Each voice gets our own rests so the
        # importer never invents any.
        upper = [p for p in all_pieces if not p.is_rest and not _is_foot(p.pitch)]
        lower = [p for p in all_pieces if not p.is_rest and _is_foot(p.pitch)]
        def _emit_drum(pieces: List[Piece], voice: int) -> None:
            for p in _fill_staff(pieces, m_len, voice, 0, beat_beaming=True, percussion=True):
                _emit_piece(measure, p, True, part.staff_lines, part.part_id, m_len)

        if not upper and not lower:
            # Silent bar: a single whole-measure rest in one voice only.
            _emit_drum([], 1)
        elif upper and not lower:
            _emit_drum(upper, 1)
        elif lower and not upper:
            _emit_drum(lower, 2)
        else:
            _emit_drum(upper, 1)
            _backup()
            _emit_drum(lower, 2)
    else:
        v1, v2 = _split_voices(all_pieces)
        _emit_voice(v1, 1, 0, part.unpitched)
        if v2:
            _backup()
            _emit_voice(v2, 2, 0, part.unpitched)

    _add_tuplet_brackets(measure)
    return measure


def build_musicxml(score: Score, config: ScoreEngravingConfig) -> str:
    _REST_STYLE["style"] = getattr(config, "rest_style", "standard")
    # Turn program changes into technique texts here as well, so calling
    # build_musicxml() directly (as a library, without main.run) does not silently
    # lose pizz./arco markings. Idempotent: merge_tracks_by_group already did it.
    from midi_inspector import attach_technique_directions
    for track in score.tracks:
        attach_technique_directions(track, getattr(config, "program_techniques", {}))
        track.directions.sort(key=lambda d: d[0])
    parts = _group_parts(score, config.score_order, getattr(config, 'program_techniques', {}))
    for part in parts:
        _plan_dynamics(part)
        if not part.unpitched:
            flat_key = _part_fifths(score.key_fifths, part.transpose) < 0
            for n in part.notes:
                _spell_note(n, flat_key)

    measures = build_measure_map(score)

    root = ET.Element("score-partwise", version="4.0")
    work = ET.SubElement(root, "work")
    ET.SubElement(work, "work-title").text = config.title

    # Reasonable page defaults for a balanced layout (Dorico still does final spacing).
    defaults = ET.SubElement(root, "defaults")
    scaling = ET.SubElement(defaults, "scaling")
    ET.SubElement(scaling, "millimeters").text = "7"
    ET.SubElement(scaling, "tenths").text = "40"
    system_layout = ET.SubElement(defaults, "system-layout")
    ET.SubElement(system_layout, "system-distance").text = "12"

    part_list = ET.SubElement(root, "part-list")
    for part in parts:
        sp = ET.SubElement(part_list, "score-part", id=part.part_id)
        ET.SubElement(sp, "part-name").text = part.part_name
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

    for part_idx, part in enumerate(parts):
        part_el = ET.SubElement(root, "part", id=part.part_id)
        pieces_map = _split_to_pieces(part, measures)
        tempos = score.tempos if part_idx == 0 else []
        for m in range(len(measures)):
            part_el.append(_build_measure(part, m, measures, score.key_fifths, pieces_map, tempos))

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    header = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
              '"http://www.musicxml.org/dtds/partwise.dtd">\n')
    return header + body
