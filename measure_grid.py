"""Measure grid: where every bar starts and how long it is.

Pure functions over Score; no MIDI parsing, no MusicXML.
"""
from __future__ import annotations

from typing import List, Tuple

from models import Score


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
