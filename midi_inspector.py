"""Raw MIDI ingestion and track inspection.

Reads a multi-track .mid file and produces a compact per-track manifest:
track name, pitch range, keyswitch candidates, median onset-grid offset, and
active CC lanes. This is the deterministic "Stage 1" that keeps the AI/LLM
layer from having to parse thousands of raw MIDI events.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import OrderedDict
import statistics
from collections import Counter
from typing import Dict, List, Tuple

import mido

from models import Note, Score, Track
from config_schema import ScoreEngravingConfig, TrackEngravingRule


log = logging.getLogger(__name__)


def classify_track(name: str) -> Tuple[str, int]:
    """Return (instrument_family, lowest playable pitch) for a track name.

    Order matters: bassoon / bass clarinet / bass trombone / contrabassoon all
    contain "bass", so the woodwind and brass tests must run before the string
    "bass" rule. Short ambiguous keys ("tom", "tam") are matched as whole words so
    "custom", "bottom" or "automation" are not mistaken for percussion.
    """
    n = (name or "").lower()

    def has(*keys: str) -> bool:
        return any(k in n for k in keys)

    # ---- percussion (first, so "bass drum" never reaches the "bass" rule) ----
    if (has("timpani", "perc", "drum", "glock", "xylo", "marimba", "vibra", "snare",
            "cymb", "tamb", "piatti", "crotale", "castanet", "woodblock", "wood block",
            "triangle", "shaker", "conga", "bongo", "djembe")
            or re.search(r"\btoms?\b", n) or re.search(r"\btam(-?tam)?\b", n)):
        return "percussion", 0
    if "harp" in n:
        return "harp", 0
    if has("piano", "keyboard", "celesta", "keys", "harpsichord"):
        return "piano", 0

    # ---- low woodwinds / low brass (their names contain "bass") ----
    if has("contrabassoon", "bassoon", "bass clarinet", "bass clarinette", "basset horn"):
        return "winds", 26
    if has("bass trombone", "contrabass trombone", "tuba", "euphonium", "cimbasso"):
        return "brass", 26

    # ---- the rest of the woodwinds and brass ----
    if has("piccolo", "flute", "oboe", "clarinet", "sax", "cor anglais", "english horn",
           "recorder", "tin whistle", "pan flute"):
        return "winds", 48
    if has("horn", "trumpet", "trombone", "cornet", "tpt", "tbn", "flugel"):
        return "brass", 36

    # ---- strings (only now may "bass" mean double bass) ----
    if has("violin", "vln"):
        return "strings", 55
    if has("viola", "vla"):
        return "strings", 48
    if has("cello", "violoncello", "vlc"):
        return "strings", 36
    if has("double bass", "contrabass", "upright bass", "acoustic bass", "bass"):
        return "strings", 28

    # Nothing matched: say so instead of silently engraving a horn at concert pitch
    # on a single treble staff.
    log.warning("track %r: unknown instrument family -> 'other' (treble clef, no "
                "transposition, one staff). Set instrument_type / transpose_semitones "
                "in the config or a profile.", name)
    return "other", 36


def clean_part_name(name: str) -> str:
    return (name or "").strip() or "Track"


def infer_unpitched(name: str, instrument_type: str) -> Tuple[bool, int]:
    """Decide whether a percussion track is unpitched and how many staff lines it
    needs. Timpani stays pitched (5-line bass clef); single drums/cymbals become a
    1-line staff; a full drum kit becomes a 5-line percussion staff."""
    if instrument_type != "percussion":
        return False, 5
    n = (name or "").lower()
    if "timpani" in n:
        return False, 5
    if any(k in n for k in ("drum set", "drumset", "drum kit", "kit", "ssd", "drums", "battery")):
        return True, 5
    return True, 1


#: mido's key_signature meta message carries a key NAME (e.g. "Bb", "F#m"),
#: not a fifths count. Map it to the circle-of-fifths integer.
KEY_TO_FIFTHS = {
    "Cb": -7, "Gb": -6, "Db": -5, "Ab": -4, "Eb": -3, "Bb": -2, "F": -1,
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "Abm": -7, "Ebm": -6, "Bbm": -5, "Fm": -4, "Cm": -3, "Gm": -2, "Dm": -1,
    "Am": 0, "Em": 1, "Bm": 2, "F#m": 3, "C#m": 4, "G#m": 5, "D#m": 6, "A#m": 7,
}

#: Instruments that sound an octave away from their written pitch. The value is
#: the written-pitch offset (semitones) applied when the config leaves it at 0.
OCTAVE_TRANSPOSING = {
    "piccolo": -12,
    "double bass": 12,
    "contrabass": 12,
    "contrabassoon": 12,
    "bass clarinet": 14,
}


def octave_transposition_for(part_name: str) -> int:
    key = (part_name or "").strip().lower()
    for name, off in OCTAVE_TRANSPOSING.items():
        if name in key:
            return off
    return 0


def parse_midi(path: str) -> Score:
    """Read a .mid file into a Score of Track/Note objects (timing in beats)."""
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat
    score = Score(ticks_per_beat=tpb)

    # Read global meta (tempo / time signature / key) — normally on track 0.
    tempo_us = 500000  # 120 BPM default
    time_sigs: List[Tuple[float, int, int]] = []
    tempos_raw: List[Tuple[float, float]] = []
    for tr_idx, tr in enumerate(mid.tracks):
        tick = 0
        for msg in tr:
            tick += msg.time
            if msg.type == "set_tempo":
                tempo_us = msg.tempo
                tempos_raw.append((tick / tpb, 60_000_000.0 / msg.tempo))
            elif msg.type == "time_signature":
                score.time_sig_beats = msg.numerator
                score.time_sig_beat_type = msg.denominator
                time_sigs.append((tick / tpb, msg.numerator, msg.denominator))
            elif msg.type == "program_change":
                if not hasattr(score, "_program_changes"):
                    score._program_changes = {}
                score._program_changes.setdefault(tr_idx, []).append(
                    (tick / tpb, msg.program))
            elif msg.type == "key_signature":
                raw = msg.key
                score.key_fifths = KEY_TO_FIFTHS.get(raw, 0) if isinstance(raw, str) else int(raw)
    score.time_sigs = time_sigs
    # Dedupe tempo changes (round to integer BPM; skip redundant/negligible changes).
    # Two tempo events on the SAME beat (common in DAW exports) must collapse to
    # one marking — keep the last, which is the one that actually plays back.
    by_beat: dict = {}
    order = []
    for beat, bpm in tempos_raw:
        key = round(beat, 6)
        if key not in by_beat:
            order.append(key)
        by_beat[key] = round(bpm)
    tempos: List[Tuple[float, float]] = []
    for key in order:
        bpm_r = by_beat[key]
        if not tempos or abs(bpm_r - tempos[-1][1]) >= 1:
            tempos.append((key, float(bpm_r)))
    score.tempos = tempos
    score.tempo_bpm = 60_000_000.0 / tempo_us

    for i, tr in enumerate(mid.tracks):
        track = Track(index=i, name=tr.name or f"Track {i}")
        tick = 0
        # A pitch can be retriggered while the previous note still sounds (trills,
        # tremolo patches, doubled hits). Keep a FIFO queue per pitch so no note is
        # silently dropped.
        open_notes: Dict[int, List[Tuple[int, int, int]]] = {}
        for msg in tr:
            tick += msg.time
            if msg.type == "track_name":
                track.name = msg.name or track.name
            elif msg.type == "note_on":
                if msg.velocity == 0:
                    queue = open_notes.get(msg.note)
                    if queue:
                        st, vel, ch = queue.pop(0)
                        if not queue:
                            open_notes.pop(msg.note, None)
                        track.notes.append(Note(pitch=msg.note, start=st / tpb, end=tick / tpb, velocity=vel))
                        track.channel = ch
                else:
                    open_notes.setdefault(msg.note, []).append((tick, msg.velocity, msg.channel))
            elif msg.type == "note_off":
                queue = open_notes.get(msg.note)
                if queue:
                    st, vel, ch = queue.pop(0)
                    if not queue:
                        open_notes.pop(msg.note, None)
                    track.notes.append(Note(pitch=msg.note, start=st / tpb, end=tick / tpb, velocity=vel))
                    track.channel = ch
            elif msg.type == "control_change":
                track.cc.setdefault(msg.control, []).append((tick / tpb, msg.value))
        # Close any notes left hanging (missing note_off).
        for pitch, queue in open_notes.items():
            for st, vel, ch in queue:
                track.notes.append(Note(pitch=pitch, start=st / tpb, end=tick / tpb, velocity=vel))
        if track.notes:
            score.tracks.append(track)

    return score


def estimate_vst_delay(track: Track, grid: float = 0.25) -> float:
    """Median signed onset error vs the quantization grid, in beats.

    Keyswitch/sub-range notes are excluded (they sit on the grid and would skew
    the median). Returns the forward shift (positive) to apply when the track
    is consistently early by between 0.015 and 0.10 beats, else 0.0.
    """
    notes = [n for n in track.notes if n.pitch >= track.min_playable_pitch]
    if not notes:
        return 0.0
    errors = [((n.start + grid / 2.0) % grid) - grid / 2.0 for n in notes]
    med = statistics.median(errors)
    if -0.10 <= med <= -0.015:
        return -med
    return 0.0


def inspect(score: Score, config: ScoreEngravingConfig) -> List[dict]:
    """Produce a human/AI readable track manifest."""
    manifest: List[dict] = []
    for track in score.tracks:
        pitches = [n.pitch for n in track.notes]
        ks = sorted({n.pitch for n in track.notes if n.pitch < track.min_playable_pitch})
        active_cc = [c for c, evs in track.cc.items() if evs]
        offset = track.manual_delay_beats if track.manual_delay_beats is not None else estimate_vst_delay(track)
        manifest.append({
            "track_index": track.index,
            "name": track.name,
            "instrument_type": track.instrument_type,
            "part_name": track.part_name,
            "min_pitch": min(pitches) if pitches else None,
            "max_pitch": max(pitches) if pitches else None,
            "note_count": len(pitches),
            "keyswitch_notes": ks,
            "median_onset_offset_beats": round(offset, 4),
            "active_cc": active_cc,
        })
    return manifest


def build_default_config(score: Score) -> ScoreEngravingConfig:
    rules = []
    for track in score.tracks:
        itype, minp = classify_track(track.name)
        unpitched, staff_lines = infer_unpitched(track.name, itype)
        rules.append(TrackEngravingRule(
            track_index=track.index,
            original_name=track.name,
            part_name=clean_part_name(track.name),
            instrument_type=itype,
            min_playable_pitch=minp,
            keyswitch_map={},
            auto_vst_delay_compensation=True,
            unpitched=unpitched,
            staff_lines=staff_lines,
        ))
    return ScoreEngravingConfig(title=score.title, tracks=rules)


def apply_config(score: Score, config: ScoreEngravingConfig) -> None:
    score.title = config.title
    rules = {r.track_index: r for r in config.tracks}
    for track in score.tracks:
        rule = rules.get(track.index)
        if rule is None:
            for r in config.tracks:
                if r.original_name and r.original_name == track.name:
                    rule = r
                    break
        if rule is not None:
            track.part_name = rule.part_name or clean_part_name(track.name)
            track.instrument_type = rule.instrument_type
            track.merge_group_id = rule.merge_group_id
            track.min_playable_pitch = rule.min_playable_pitch
            track.keyswitch_map = dict(rule.keyswitch_map)
            track.auto_vst_delay_compensation = rule.auto_vst_delay_compensation
            track.manual_delay_beats = rule.manual_delay_offset_beats
            track.transpose_semitones = rule.transpose_semitones
            track.pitch_remap = dict(rule.pitch_remap)
            track.unpitched = rule.unpitched
            track.staff_lines = rule.staff_lines
            track.program_changes = list(
                getattr(score, "_program_changes", {}).get(track.index, []))
            if not track.transpose_semitones and not track.unpitched:
                # Piccolo / double bass etc. are octave-transposing even when the
                # config does not spell it out.
                track.transpose_semitones = octave_transposition_for(track.part_name)
        else:
            itype, minp = classify_track(track.name)
            track.instrument_type = itype
            track.part_name = clean_part_name(track.name)
            track.min_playable_pitch = minp


def apply_pitch_transforms(track: Track) -> None:
    """Apply per-track pitch remap (percussion trigger notes -> real pitches).

    Instrument transposition (`transpose_semitones`) is deliberately NOT applied to
    the note pitches: notes stay in concert pitch, and the exporter writes a
    MusicXML <transpose> element so Dorico handles transposed display/playback.
    Applying it here would shift low keyswitch notes into the playable range
    before `strip_keyswitches` could remove them.
    """
    for n in track.notes:
        if n.pitch in track.pitch_remap:
            n.pitch = track.pitch_remap[n.pitch]


def attach_technique_directions(track: Track,
                                program_techniques: Optional[Dict[str, str]] = None) -> None:
    """Turn program changes into technique texts (pizz. / arco / trem. ...)."""
    if not program_techniques:
        return
    if getattr(track, "_techniques_attached", False):
        return
    for beat, prog in sorted(track.program_changes):
        text = program_techniques.get(str(prog))
        if not text:
            continue
        # An initial "arco" is the default and needs no marking, but a piece that
        # starts pizzicato must say so.
        if beat < 1e-9 and text.strip().lower() in ("arco", "ord.", "ordinario"):
            continue
        track.directions.append((beat, text))
    track._techniques_attached = True


def merge_tracks_by_group(score: Score,
                          program_techniques: Optional[Dict[str, str]] = None) -> None:
    """Fold tracks that share a merge_group_id into a single processing unit.

    This has to happen before timing_cleaner runs: rest merging, staccato
    consolidation, harp pedals and grand-staff splitting all make decisions about
    what is happening at a given beat, and running them on half of a merged part
    produces overlaps (and, for two harp tracks, two conflicting pedal diagrams).
    """
    groups: "OrderedDict[str, List[Track]]" = OrderedDict()
    for tr in sorted(score.tracks, key=lambda t: t.index):
        key = tr.merge_group_id or f"__track_{tr.index}"
        groups.setdefault(key, []).append(tr)

    merged: List[Track] = []
    for key, members in groups.items():
        base = members[0]
        if len(members) > 1:
            notes: List[Note] = []
            for tr in members:
                notes.extend(tr.notes)
            notes.sort(key=lambda n: (n.start, n.pitch))
            base.notes = notes
            base.directions = [d for tr in members for d in tr.directions]
            base.program_changes = [d for tr in members for d in tr.program_changes]
            base.harp_pedal_changes = [d for tr in members for d in tr.harp_pedal_changes]
            base.merged_from = [tr.index for tr in members]
        attach_technique_directions(base, program_techniques)
        # One sorted list, no duplicate markings for the same gesture.
        base.directions.sort(key=lambda d: d[0])
        deduped: List[Tuple[float, str]] = []
        for beat, text in base.directions:
            if deduped and deduped[-1][1] == text and beat - deduped[-1][0] < 1e-6:
                continue
            deduped.append((beat, text))
        base.directions = deduped
        merged.append(base)

    score.tracks = merged


def build_measure_map(score: Score) -> List[Tuple[float, int, int]]:
    """Build the ordered list of measures as (start_beat, numerator, denominator)."""
    total = max((n.end for t in score.tracks for n in t.notes), default=0.0)
    sigs = sorted(score.time_sigs)
    if not sigs:
        sigs = [(0.0, score.time_sig_beats, score.time_sig_beat_type)]
    if sigs[0][0] > 1e-9:
        sigs = [(0.0, score.time_sig_beats, score.time_sig_beat_type)] + sigs

    measures: List[Tuple[float, int, int]] = []
    beat = 0.0
    while beat < total - 1e-9:
        num, den = sigs[0][1], sigs[0][2]
        for b, n, d in sigs:
            if b <= beat + 1e-9:
                num, den = n, d
            else:
                break
        measures.append((beat, num, den))
        beat += num * 4.0 / den
        if num <= 0 or den <= 0 or len(measures) > 10000:
            break
    if not measures:
        measures = [(0.0, score.time_sig_beats, score.time_sig_beat_type)]
    return measures


def measure_index(beat: float, measures: List[Tuple[float, int, int]]) -> int:
    idx = 0
    for i, (b, _, _) in enumerate(measures):
        if b <= beat + 1e-9:
            idx = i
        else:
            break
    return idx


def suggest_timpani_pitches(score: Score) -> Dict[int, int]:
    """Suggest a tonic/dominant remap for a timpani track whose notes are VST
    trigger pitches (e.g. 0 and 1) rather than real pitches.

    The tonic is estimated from the pitch-class histogram of the pitched tracks;
    the low drum is tuned to the tonic and the high drum to the dominant (a
    perfect fifth above). Returns {low_trigger: pitch, high_trigger: pitch}.
    """
    pc: Counter = Counter()
    for t in score.tracks:
        itype, _ = classify_track(t.name)
        if itype in ("percussion", "other"):
            continue
        for n in t.notes:
            if 36 <= n.pitch <= 96:
                pc[n.pitch % 12] += 1
    if not pc:
        return {}
    tonic_pc = pc.most_common(1)[0][0]
    low = 38 - (38 % 12) + tonic_pc          # ~D2 reference
    while low < 36:
        low += 12
    while low > 48:
        low -= 12
    high = low + 7                            # perfect fifth up
    return {0: low, 1: high}


def main() -> None:
    p = argparse.ArgumentParser(description="Inspect a MIDI file and emit a track manifest.")
    p.add_argument("--input", required=True)
    p.add_argument("--output", default="track_manifest.json")
    args = p.parse_args()

    score = parse_midi(args.input)
    config = build_default_config(score)
    apply_config(score, config)
    manifest = inspect(score, config)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
