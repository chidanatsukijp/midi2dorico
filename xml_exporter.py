"""Build a multi-part score-partwise MusicXML 4.0 full score for Dorico."""
from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from models import ONSET_EPS, Score, group_by_onset
from score_model import Part, Piece
from part_catalog import _group_parts, _is_foot, build_part_list
from note_writer import _emit_piece
from config_schema import ScoreEngravingConfig
from pitch_theory import octave_of, spell_note
from measure_grid import build_measure_map, measure_index

from notation_values import (DEFAULT_REST_STYLE, DIVISIONS, _decompose_duration,
                              _measure_boundaries, _type_from_beats)
from directions import (_add_tuplet_brackets, _direction_words, _dynamic_direction,
                        _harp_pedal_direction, _plan_dynamics, _tempo_direction)

HERE = os.path.dirname(os.path.abspath(__file__))




#: Foot-operated pieces: down-stem voice in Dorico's default kit presentation.






#: MusicXML sound IDs so the importer matches a real instrument (Dorico ignores
#: <midi-program> and matches on <instrument-sound>).






#: Instrument order, MIDI programs and the percussion tables all come from
#: data/gm.json (display position, notehead and sound per drum, plus names).




#: Standard note durations (beats) -> (type, dotted, time-modification), largest first.








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




def _spell_part(part: Part, fifths: int) -> None:
    """Spell every unspelled note, giving each one its melodic context."""
    ordered = sorted(part.notes, key=lambda n: (n.start, n.pitch))
    prev: Optional[int] = None
    i = 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and abs(ordered[j].start - ordered[i].start) <= ONSET_EPS:
            j += 1
        nxt = ordered[j].pitch if j < len(ordered) else None
        for n in ordered[i:j]:
            if n.step:
                continue
            n.step, n.alter = spell_note(n.pitch, fifths, prev, nxt)
            n.octave = octave_of(n.pitch, n.step, n.alter)
        prev = ordered[j - 1].pitch
        i = j








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
    groups = [(g[0].start, g)
              for g in group_by_onset(sorted(pieces, key=lambda x: x.start))]

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




#: Values a rest may use when we want to avoid 8th/16th rests entirely.
























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




def _build_measure(part: Part, m: int, measures: List[Tuple[float, int, int]], key_fifths: int,
                   pieces_map: Dict[int, List[Piece]],
                   tempos: Optional[List[Tuple[float, float]]] = None,
                   grouping: Optional[Dict[str, List[int]]] = None,
                   rest_style: str = DEFAULT_REST_STYLE) -> ET.Element:
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
    bounds = _measure_boundaries(num, den, m_len, grouping)
    backup_dur = str(round(m_len * DIVISIONS))

    def _emit_voice(pieces: List[Piece], voice: int, staff_no: int,
                    perc: bool = False) -> None:
        for p in _fill_staff(pieces, m_len, voice, staff_no, percussion=perc):
            _emit_piece(measure, p, part.unpitched, part.staff_lines, part.part_id,
                        m_len, bounds, rest_style)

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
                _emit_piece(measure, p, True, part.staff_lines, part.part_id, m_len,
                            bounds, rest_style)

        # Hands can hold two independent lines (e.g. a ride pattern under a snare
        # roll). Split them the same way a pitched staff is split, and keep the
        # feet as their own down-stem voice.
        hands_a, hands_b = _split_voices(upper)

        voices = [(hands_a, 1)]
        if lower:
            voices.append((lower, 2))
        if hands_b:
            voices.append((hands_b, 3))

        if not any(pieces for pieces, _ in voices):
            # Silent bar: a single whole-measure rest in one voice only.
            _emit_drum([], 1)
        else:
            for idx, (pieces, voice) in enumerate(voices):
                if idx:
                    _backup()
                _emit_drum(pieces, voice)
    else:
        v1, v2 = _split_voices(all_pieces)
        _emit_voice(v1, 1, 0, part.unpitched)
        if v2:
            _backup()
            _emit_voice(v2, 2, 0, part.unpitched)

    _add_tuplet_brackets(measure)
    return measure


def build_musicxml(score: Score, config: ScoreEngravingConfig) -> str:
    rest_style = getattr(config, "rest_style", DEFAULT_REST_STYLE)
    # Turn program changes into technique texts here as well, so calling
    # build_musicxml() directly (as a library, without main.run) does not silently
    # lose pizz./arco markings. Idempotent: merge_tracks_by_group already did it.
    from techniques import attach_technique_directions
    for track in score.tracks:
        attach_technique_directions(track, getattr(config, "program_techniques", {}))
        track.directions.sort(key=lambda d: d[0])
    parts = _group_parts(score, config.score_order, getattr(config, 'program_techniques', {}))
    for part in parts:
        _plan_dynamics(part)
        if not part.unpitched:
            _spell_part(part, _part_fifths(score.key_fifths, part.transpose))

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

    build_part_list(root, parts)

    for part_idx, part in enumerate(parts):
        part_el = ET.SubElement(root, "part", id=part.part_id)
        pieces_map = _split_to_pieces(part, measures)
        tempos = score.tempos if part_idx == 0 else []
        for m in range(len(measures)):
            part_el.append(_build_measure(part, m, measures, score.key_fifths, pieces_map,
                                          tempos, getattr(config, "beat_grouping", None),
                                          rest_style))

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    header = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
              '"http://www.musicxml.org/dtds/partwise.dtd">\n')
    return header + body
