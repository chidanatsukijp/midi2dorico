"""Timing cleanup: VST pre-delay, legato/chord overlap trimming, rolled-arpeggio
consolidation, and grid quantization."""
from __future__ import annotations

import math
import statistics
from typing import List, Optional, Tuple

from models import Note, Track
from config_schema import ScoreEngravingConfig


#: Common Kontakt/CSS-style low-note keyswitches. Customize per library via config.
# Fallback only for callers that do not pass a config at all; the pipeline uses
# ScoreEngravingConfig.keyswitch_map, which is user-editable.
DEFAULT_KEYSWITCH_TECHNIQUES = {
    24: "arco", 25: "pizz.", 26: "pizz.", 27: "staccato", 28: "spiccato",
    29: "legato", 30: "tremolo", 31: "sul pont.",
}


def _group_chords(notes: List[Note], eps: float = 1e-6) -> List[List[Note]]:
    """Group simultaneous notes (same onset within `eps` beats) into chords."""
    ordered = sorted(notes, key=lambda n: n.start)
    chords: List[List[Note]] = []
    for n in ordered:
        if chords and abs(n.start - chords[-1][0].start) <= eps:
            chords[-1].append(n)
        else:
            chords.append([n])
    return chords


def _lookup_technique(pitch: int, track_map: Dict[int, str],
                      default_map: Optional[Dict[str, str]]) -> Optional[str]:
    """Per-track map wins, then the score-wide table (which uses text keys).

    `default_map is None` means "no configuration was supplied" and falls back to
    the built-in C0 table; an explicitly empty dict means "no automatic mapping"
    and drops the note instead.
    """
    if pitch in track_map:
        return track_map[pitch]
    if default_map is None:
        return DEFAULT_KEYSWITCH_TECHNIQUES.get(pitch)
    for key in (pitch, str(pitch)):
        if key in default_map:
            return default_map[key]
    return None


def strip_keyswitches(track: Track,
                      default_map: Optional[Dict[str, str]] = None) -> None:
    """Remove sub-range keyswitch notes and turn them into technique directions.

    For unpitched percussion, notes below MIDI 12 are VST trigger/junk notes and
    are dropped silently (they are not real drum hits and break consolidation).
    """
    keep: List[Note] = []
    for n in track.notes:
        if track.unpitched and n.pitch < 12:
            continue
        if n.pitch < track.min_playable_pitch:
            technique = _lookup_technique(n.pitch, track.keyswitch_map, default_map)
            if technique:
                # Keyswitches are often played a hair before the beat they belong
                # to; snap to the nearest 16th so the marking lands on the note.
                beat = round(n.start / 0.25) * 0.25
                # The same technique re-triggered within a bar is one gesture, not
                # two: keep only the first marking.
                last = track.directions[-1] if track.directions else None
                if not (last and last[1] == technique and beat - last[0] < 4.5 - 1e-9):
                    track.directions.append((beat, technique))
            # Unmapped sub-range notes are silent triggers: drop them instead of
            # printing junk text like "KS0" on the score.
        else:
            keep.append(n)
    track.notes = keep


def _shift_track(track: Track, shift: float) -> None:
    """Move everything that carries a beat position, not just the notes."""
    for n in track.notes:
        n.start += shift
        n.end += shift
    track.directions = [(b + shift, t) for b, t in track.directions]
    track.program_changes = [(b + shift, p) for b, p in track.program_changes]
    track.cc = {k: [(b + shift, v) for b, v in lane] for k, lane in track.cc.items()}


def detect_and_fix_vst_delay(track: Track, grid: float = 0.25) -> float:
    """Detect and remove a consistent negative pre-delay; returns the forward shift."""
    if track.manual_delay_beats is not None:
        # manual_delay_beats is a forward shift (positive = compensate for early notes),
        # consistent with the auto-detection return value.
        shift = track.manual_delay_beats
        _shift_track(track, shift)
        track.vst_delay_beats = shift
        return shift
    if not track.auto_vst_delay_compensation or not track.notes:
        return 0.0
    errors = [((n.start + grid / 2.0) % grid) - grid / 2.0 for n in track.notes]
    med = statistics.median(errors)
    if -0.10 <= med <= -0.015:
        shift = -med
        _shift_track(track, shift)
        track.vst_delay_beats = shift
        return shift
    return 0.0


def collapse_arpeggiated_chords(track: Track, min_beats: float = 0.03, max_beats: float = 0.15) -> None:
    """Detect rolled chords (3+ notes, onset spread 15-80ms, monotonic pitch order,
    and note-offs that sustain together) and merge them into one quantized chord
    marked with the MusicXML <arpeggiate/> tag."""
    notes = [n for n in sorted(track.notes, key=lambda n: n.start) if not n.glissando]
    i = 0
    while i < len(notes):
        first = notes[i]
        cluster = [first]
        j = i + 1
        while j < len(notes) and notes[j].start - first.start <= max_beats:
            cluster.append(notes[j])
            j += 1
        spread = max(n.start for n in cluster) - min(n.start for n in cluster)
        collapsed = False
        if len(cluster) >= 3 and spread >= min_beats:
            by_onset = sorted(cluster, key=lambda n: n.start)
            pitches = [n.pitch for n in by_onset]
            ascending = pitches == sorted(pitches)
            descending = pitches == sorted(pitches, reverse=True)
            # Notes of a rolled chord ring together: shared sustain region must be
            # at least 50% of the average duration.
            overlap = min(n.end for n in cluster) - max(n.start for n in cluster)
            avg_dur = sum(n.duration for n in cluster) / len(cluster)
            if (ascending or descending) and overlap >= 0.5 * avg_dur:
                direction = "up" if ascending else "down"
                target = first.start
                longest = max(n.duration for n in cluster)
                for n in cluster:
                    n.is_arpeggio = True
                    n.arpeggio_dir = direction
                    n.start = target
                    n.end = target + longest
                collapsed = True
        # Only skip the whole window when a chord was actually collapsed; otherwise
        # advance by one so a rolled chord starting later in the window is not missed.
        i = j if collapsed else i + 1


def dedupe_unisons(track: Track) -> None:
    """Collapse notes that share an onset AND a pitch.

    A DAW double-trigger (or two adjacent MIDI notes quantized onto the same grid
    slot) otherwise engraves as two noteheads side by side for the same drum,
    which reads as a mistake.
    """
    best: Dict[Tuple[float, int], Note] = {}
    order: List[Note] = []
    for n in track.notes:
        key = (round(n.start, 6), n.pitch)
        cur = best.get(key)
        if cur is None:
            best[key] = n
            order.append(n)
        elif (n.end - n.start) > (cur.end - cur.start):
            cur.end = n.end
    if len(order) != len(track.notes):
        track.notes = order


def trim_overlaps(track: Track, max_trim: float = 0.5) -> None:
    """Clip small legato tail overlaps (sloppy releases) between chords."""
    chords = _group_chords(track.notes)
    for ci in range(len(chords) - 1):
        if any(n.is_run or n.glissando for n in chords[ci]):
            continue
        cur_end = max(n.end for n in chords[ci])
        nxt_start = chords[ci + 1][0].start
        overlap = cur_end - nxt_start
        if 0 < overlap <= max_trim + 1e-9:
            for n in chords[ci]:
                n.end = nxt_start


def _snap_pitched(x: float) -> float:
    """Placeholder kept for compatibility; onset snapping is 16th-grid + isolated alignment."""
    return round(x / 0.25) * 0.25


def _align_isolated_sixteenths(track: Track) -> None:
    """Snap isolated 16th-position notes to the previous 8th.

    A note sitting alone on a 16th (not part of a 16th run) is usually a slightly
    late ensemble attack; snapping it down to the 8th aligns it with the rest of
    the orchestra without destroying genuine 16th-note runs.
    """
    notes = sorted(track.notes, key=lambda n: n.start)
    for i, n in enumerate(notes):
        if n.is_run or n.glissando:
            continue
        if abs((n.start % 0.5) - 0.25) < 1e-9:  # on a 16th (0.25 / 0.75)
            prev_gap = n.start - notes[i - 1].start if i > 0 else 10.0
            next_gap = notes[i + 1].start - n.start if i + 1 < len(notes) else 10.0
            if prev_gap > 0.3 and next_gap > 0.3:
                dur = n.end - n.start
                new_start = math.floor(n.start * 2) / 2
                # Do not snap earlier if it would collide with the previous note
                # (e.g. a held legato note still sounding).
                if i > 0 and notes[i - 1].end > new_start + 1e-9:
                    continue
                n.start = new_start
                n.end = n.start + dur


def quantize_notes(track: Track, grid: float = 0.25) -> None:
    for n in track.notes:
        if n.is_run:
            continue  # micro-run notes keep their exact positioning
        if n.glissando:
            # Snap the anchor's onset to the 8th grid, then fill up to the next
            # beat, so the endpoint is an 8th or quarter (never a tie) and no
            # sub-quarter rest is left after it.
            n.start = round(n.start / 0.5) * 0.5
            frac = n.start - math.floor(n.start)
            n.end = math.ceil(n.start) if frac > 1e-9 else n.start + 1.0
            continue
        n.start = round(n.start / grid) * grid
        dur = round(n.duration / grid) * grid
        if dur < grid:
            dur = grid
        n.end = n.start + dur
    if not track.unpitched:
        _align_isolated_sixteenths(track)


def _slot_end(x: float) -> float:
    """End of a note's natural rhythmic slot based on its grid position.

    On the beat -> quarter slot; anywhere else (8th or 16th) -> 8th slot. This
    merges the classic "ineffective rest" patterns: 16th + 16th rest -> 8th, and
    16th + 16th rest + 8th rest (on the beat) -> quarter.
    """
    if abs(x % 1.0) < 1e-9:
        return x + 1.0
    return x + 0.5


def mark_detached(track: Track) -> None:
    """Mark short detached notes as staccato *before* quantization flattens them.

    Sustained instruments (woodwind, strings, brass, guitar, piano, ...) take a
    staccato dot on the merged longer note; percussion (short sustain, including
    pitched timpani) just lengthens without a dot.
    """
    if track.instrument_type == "percussion":
        return  # percussion has no sustain tail; just lengthen, no staccato
    chords = _group_chords(track.notes)
    for ci in range(len(chords) - 1):
        chord = chords[ci]
        nxt = chords[ci + 1]
        if any(n.is_run or n.glissando for n in chord) or any(n.is_run or n.glissando for n in nxt):
            continue
        chord_end = max(n.end for n in chord)
        chord_dur = chord_end - chord[0].start
        gap = nxt[0].start - chord_end
        if 0 < chord_dur < 1.0 and 0 < gap:
            for n in chord:
                n.staccato = True


def finalize_staccato(track: Track) -> None:
    """Staccato only belongs on detached notes that end up a quarter or shorter.

    A "choppy MIDI" note (short + rest) merges to at most a quarter; if the merge
    produced a dotted quarter or longer, it was not a staccato gesture, so drop
    the dot.
    """
    for n in track.notes:
        if n.staccato and n.duration > 1.0 + 1e-9:
            n.staccato = False


#: Standard single-note durations in beats (used to keep note values clean).
_STD_BEATS = (4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.25, 0.125)


def _is_std(d: float) -> bool:
    return any(abs(d - v) < 1e-6 for v in _STD_BEATS)


def _clean_note_end(start: float, end: float, limit: float) -> float:
    """Pick the note end that leaves the cleanest possible rest before `limit`.

    A rest is only free of 8th/16th rests when it STARTS on a beat and spans a
    whole number of beats, so the note is extended to the next beat boundary (or
    over the whole gap when that gap is shorter than a quarter).
    """
    rest = limit - end
    if rest <= 1e-9:
        return end
    beat = math.ceil(end - 1e-9)
    candidates: List[float] = []
    if rest < 1.0 - 1e-9:
        candidates.append(limit)                 # absorb a sub-quarter gap
    else:
        rem = limit - beat
        if beat <= limit + 1e-9 and rem >= 1.0 - 1e-9 and abs(rem - round(rem)) < 1e-9:
            candidates.append(beat)              # rest = whole number of beats
        candidates.append(limit)
    for cand in candidates:
        if cand >= end - 1e-9 and _is_std(cand - start):
            return cand
    return candidates[0] if candidates else end


def consolidate_staccato(track: Track, max_note: float = 1.0,
                         measures: Optional[List[Tuple[float, int, int]]] = None) -> None:
    """Absorb every sub-quarter rest (8th / 16th / 32nd) into the preceding note.

    The DAW records detached notes with short rests between them. For engraving,
    only whole-quarter rests (and longer) should remain: any rest shorter than one
    beat is folded into the note before it, so the leftover rest is always a whole
    number of quarter notes. (The staccato dot is applied earlier by mark_detached.)
    """
    chords = _group_chords(track.notes)
    bar_ends = [m[0] + m[1] * 4.0 / m[2] for m in measures] if measures else []

    def _bar_end(beat: float) -> float:
        for e in bar_ends:
            if beat < e - 1e-9:
                return e
        return float("inf")

    for ci in range(len(chords)):
        chord = chords[ci]
        if any(n.is_run or n.glissando or n.is_arpeggio for n in chord):
            continue

        chord_end = max(n.end for n in chord)
        limit = _bar_end(chord[0].start)
        if ci < len(chords) - 1:
            nxt = chords[ci + 1]
            gap = nxt[0].start - chord_end
            if 0 < gap:
                extend_to = _clean_note_end(chord[0].start, chord_end, min(limit, nxt[0].start))
                if extend_to > chord_end + 1e-9:
                    for n in chord:
                        n.end = extend_to
        else:
            # Last note: fold the fractional trailing rest up to the next beat.
            extend_to = _clean_note_end(chord[0].start, chord_end, limit)
            if extend_to > chord_end + 1e-9:
                for n in chord:
                    n.end = extend_to


def _set_gliss(note: Note, t: str) -> None:
    """Append a glissando type; a turning point can carry both stop and start."""
    if not note.glissando:
        note.glissando = t
    elif t not in note.glissando.split(","):
        note.glissando += "," + t


#: Foot-operated kit pieces: these form the down-stem voice in Dorico's default
#: drum-set presentation. Everything else (snare, toms, cymbals, hi-hat by hand)
#: is the up-stem voice.
_FOOT_PITCHES = {35, 36, 44}


def consolidate_percussion(track: Track, measures: Optional[List[Tuple[float, int, int]]] = None) -> None:
    """Merge rests per drum-kit voice for unpitched percussion.

    Dorico engraves a kit as TWO voices: hands (up-stem: snare, toms, cymbals,
    hi-hat) and feet (down-stem: kick, pedal hi-hat). Rests only have to be clean
    inside each voice, so each voice is processed independently -- never taking a
    max across voices, which would stretch fast notes to the other voice's length.
    """
    ends = [m[0] + m[1] * 4.0 / m[2] for m in measures] if measures else []

    def _measure_end(beat: float) -> float:
        for e in ends:
            if beat < e - 1e-9:
                return e
        return float("inf")

    def _voice_of(n: Note) -> int:
        if track.staff_lines <= 1:
            return 0
        return 1 if n.pitch in _FOOT_PITCHES else 0

    by_voice_start: Dict[Tuple[int, float], List[Note]] = {}
    for n in track.notes:
        by_voice_start.setdefault((_voice_of(n), round(n.start, 9)), []).append(n)

    # A drum hit must never ring across a barline: clamp every hit to the end of
    # its measure so the score never shows a tie on a percussion staff.
    for n in track.notes:
        m_end = _measure_end(n.start)
        if n.end > m_end - 1e-9:
            n.end = m_end
    by_voice_start = {k: v for k, v in by_voice_start.items() if v}

    for vi in (0, 1):
        starts = sorted(st for (v, st) in by_voice_start if v == vi)
        for idx, st in enumerate(starts):
            chord = by_voice_start[(vi, st)]
            frac = st - math.floor(st)
            target = math.ceil(st) if frac > 1e-9 else st + 1.0
            target = min(target, _measure_end(st))
            if idx + 1 < len(starts):
                nxt = starts[idx + 1]
                chord_end = max(n.end for n in chord)
                # A gap shorter than a quarter would print as an 8th/16th rest, so
                # absorb it completely into this hit.
                target = nxt if (nxt - chord_end) < 1.0 - 1e-9 else min(target, nxt)
                # Never let the absorption cross the barline: a drum hit that
                # rings into the next measure becomes a tie on a percussion staff.
                target = min(target, _measure_end(st))
            for n in chord:
                if target > n.end + 1e-9:
                    n.end = target


def _mark_glissando(run: List[List[Note]], to_delete: List[Note], track: Track) -> None:
    """Mark a monotonic run as a glissando between its first and last note."""
    first = run[0][0]
    last = run[-1][0]
    _set_gliss(first, "start")
    _set_gliss(last, "stop")
    # Keep both endpoints as short (8th) anchors; the glissando line spans the sweep.
    first.end = first.start + 0.5
    last.end = last.start + 0.5
    for pos in run[1:-1]:
        to_delete.extend(pos)
    track.directions.append((first.start, "gliss."))


def detect_glissando(track: Track, min_run: int = 5, max_interval: float = 0.3,
                     max_step_semitones: int = 24) -> None:
    """Detect harp/piano glissandi and notate them as a glissando between the
    first and last note (the intermediate sweep notes are removed).

    Runs on RAW (pre-quantization) notes so the fine sweep structure is intact.
    Handles single-direction sweeps (with small local wobbles) and "up-then-down"
    (or down-then-up) waves by splitting at the peak/valley. Glissando notation is
    used ONLY for piano and harp.
    """
    if track.instrument_type not in ("piano", "harp"):
        return
    notes = sorted(track.notes, key=lambda n: n.start)
    positions: List[List[Note]] = []
    cur: List[Note] = []
    for n in notes:
        if cur and abs(n.start - cur[-1].start) < 1e-6:
            cur.append(n)
        else:
            if cur:
                positions.append(cur)
            cur = [n]
    if cur:
        positions.append(cur)

    to_delete: List[Note] = []
    i = 0
    while i < len(positions):
        run = [positions[i]]
        j = i + 1
        while j < len(positions):
            iv = positions[j][0].start - positions[j - 1][0].start
            if iv > max_interval:
                break
            run.append(positions[j])
            j += 1
        if len(run) >= min_run:
            means = [sum(n.pitch for n in pos) / len(pos) for pos in run]
            # A sweep whose adjacent steps are all small is a scale slide; a sweep
            # through a chord moves in thirds and is still written as a glissando
            # unless the caller restricts the interval.
            steps = [abs(means[k + 1] - means[k]) for k in range(len(means) - 1)]
            if steps and max(steps) > max_step_semitones + 1e-9:
                i = j if j > i else i + 1
                continue
            net = means[-1] - means[0]
            if abs(net) >= 12:
                # Single-direction sweep (wobbles allowed): first -> last.
                _mark_glissando(run, to_delete, track)
            else:
                # Up-then-down / down-then-up wave: split at the extreme.
                peak = max(range(len(means)), key=lambda k: means[k])
                valley = min(range(len(means)), key=lambda k: means[k])
                if peak != valley and means[peak] - means[valley] >= 12:
                    if 0 < peak < len(means) - 1:
                        # Ascending arm up to the peak, then descending arm from the
                        # same peak (the turning note is the arrival of the up-sweep
                        # AND the departure of the down-sweep).
                        _mark_glissando(run[:peak + 1], to_delete, track)
                        if len(run) - peak >= min_run:
                            _mark_glissando(run[peak:], to_delete, track)
                    elif 0 < valley < len(means) - 1:
                        _mark_glissando(run[:valley + 1], to_delete, track)
                        if len(run) - valley >= min_run:
                            _mark_glissando(run[valley:], to_delete, track)
        i = j if j > i else i + 1

    if to_delete:
        doomed = {id(n) for n in to_delete}
        track.notes = [n for n in track.notes if id(n) not in doomed]


def _snap_slot(span: float) -> float:
    """Snap a micro-run's total span up to a readable subdivision."""
    for s in (0.125, 0.25, 0.5, 1.0, 2.0):
        if s >= span - 1e-9:
            return s
    return span


def _tuplet_ratio(n: int) -> Optional[Tuple[int, int]]:
    """Return (actual, normal) for a run of n notes, or None for plain notes.

    n notes are played in the time of the largest power of two below n (3:2, 5:4,
    6:4, 7:4, 9:8, 10:8, ...). That keeps the notated base value = span/normal a
    power of two, so the note always gets a legal <type>.
    """
    if n < 3:
        return None
    if n & (n - 1) == 0:          # 3? no - 4, 8, 16 are plain values
        return None
    normal = 1 << int(math.floor(math.log2(n)))
    if normal == n:
        return None
    return (n, normal)


def _find_measure_end(beat: float, measures: Optional[List[Tuple[float, int, int]]]) -> Optional[float]:
    if not measures:
        return None
    for start, num, den in measures:
        end = start + num * 4.0 / den
        if start - 1e-9 <= beat < end - 1e-9:
            return end
    return None


def _notate_subrun(sr: List[Note], target: Optional[Note], grid: float = 0.25,
                   measure_end: Optional[float] = None) -> None:
    """Evenly distribute a micro-run's notes between the first note and the target."""
    n = len(sr)
    first = sr[0]
    end_beat = target.start if target is not None else sr[-1].start
    span = end_beat - first.start
    slot = _snap_slot(span)
    anchor = round(first.start / grid) * grid
    if measure_end is not None and anchor + slot > measure_end + 1e-9:
        # Only nudge a run that overhangs the barline by a 16th or less; a bigger
        # move would falsify the recorded timing, so it is left to tie across.
        fitted = max(0.0, round((measure_end - slot) / grid) * grid)
        if anchor - fitted <= 0.25 + 1e-9:
            anchor = fitted
    dur = slot / n
    ratio = _tuplet_ratio(n)
    for k, note in enumerate(sr):
        note.start = anchor + k * dur
        note.end = note.start + dur
        note.is_run = True
        note.time_mod = ratio


def detect_micro_runs(track: Track, gap_min: float = 0.02, gap_max: float = 0.2,
                       measures: Optional[List[Tuple[float, int, int]]] = None) -> None:
    """Detect rapid monotonic "scoop/rip" runs and notate them as tuplets/fast notes.

    Runs of >= 2 notes with clearly-distinct but very close onsets (a brass rip up
    to a target note) are re-notated as 32nds / triplets / quintuplets / septuplets
    instead of being collapsed into a chord by coarse quantization. A note held
    "unusually long" (the destination) splits the group and stays a normal note.
    """
    notes = sorted(track.notes, key=lambda n: n.start)
    i = 0
    while i < len(notes):
        cluster = [notes[i]]
        j = i + 1
        while j < len(notes) and gap_min < (notes[j].start - notes[j - 1].start) < gap_max:
            cluster.append(notes[j])
            j += 1
        if len(cluster) >= 2:
            pitches = [n.pitch for n in cluster]
            if pitches == sorted(pitches) or pitches == sorted(pitches, reverse=True):
                median_dur = statistics.median([n.duration for n in cluster])
                segments: List[Tuple[str, List[Note]]] = []
                cur: List[Note] = []
                for n in cluster:
                    if n.duration > 2 * median_dur + 1e-9:
                        if cur:
                            segments.append(("run", cur))
                            cur = []
                        segments.append(("long", [n]))
                    else:
                        cur.append(n)
                if cur:
                    segments.append(("run", cur))
                for si, (kind, seg) in enumerate(segments):
                    if kind == "run" and len(seg) >= 2:
                        target = None
                        if si + 1 < len(segments) and segments[si + 1][0] == "long":
                            target = segments[si + 1][1][0]
                        _notate_subrun(seg, target, measure_end=_find_measure_end(seg[0].start, measures))
        i = j if j > i else i + 1


def process_track(track: Track, config: ScoreEngravingConfig,
                  measures: Optional[List[Tuple[float, int, int]]] = None) -> None:
    strip_keyswitches(track, getattr(config, "keyswitch_map", None))
    detect_and_fix_vst_delay(track, config.quantize_grid_beats)
    # Detect glissandi on RAW notes, before arpeggio collapse / quantization flatten
    # the fine sweep structure.
    if track.instrument_type in ("piano", "harp"):
        detect_glissando(track, max_step_semitones=getattr(config, "glissando_max_step_semitones", 24))
    if track.instrument_type in ("strings", "winds", "brass"):
        detect_micro_runs(track, measures=measures)
    collapse_arpeggiated_chords(track, config.arpeggio_window_min_beats, config.arpeggio_window_max_beats)
    mark_detached(track)
    quantize_notes(track, config.quantize_grid_beats)
    # A DAW double-trigger (or two notes quantized onto the same grid slot) must
    # not engrave as two noteheads side by side for the same instrument.
    dedupe_unisons(track)
    trim_overlaps(track, config.max_overlap_trim_beats)
    # Transposing instruments: switch to written pitch only after keyswitches
    # have been stripped in concert pitch (so low triggers are removed first).
    if track.transpose_semitones:
        for n in track.notes:
            n.pitch += track.transpose_semitones

    # Extend short+rest into longer values for all tracks.
    consolidate_staccato(track, 1.0, measures)
    if track.unpitched:
        # Percussion is engraved one voice per instrument, so merge rests per pitch.
        consolidate_percussion(track, measures)
    finalize_staccato(track)
