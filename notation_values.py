"""Note values, rest spelling and bar arithmetic.

Pure functions over quarter-note beats: no MIDI, no XML, no config model.
"""
from __future__ import annotations

import math

from typing import Dict, List, Optional, Tuple


DIVISIONS = 1680


_REST_STYLE = {"style": "standard"}  # quarter = 1680; 16th = 420; 32nd = 210; 64th = 105; divisible by 3/5/7/16


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


def _measure_boundaries(num: int, den: int, measure_len: float,
                        grouping: Optional[Dict[str, List[int]]]) -> Optional[List[float]]:
    """Group boundaries (in beats) of an asymmetric meter, e.g. 7/8 "2+2+3"."""
    groups = (grouping or {}).get(f"{num}/{den}")
    if not groups or sum(groups) != num:
        return None
    unit = 4.0 / den
    out: List[float] = []
    acc = 0.0
    for g in groups[:-1]:
        acc += g * unit
        if 1e-9 < acc < measure_len - 1e-9:
            out.append(acc)
    return out or None


def _decompose_rest(beats: float, start: float,
                    measure_len: Optional[float] = None,
                    boundaries: Optional[List[float]] = None) -> List[Tuple[float, str, bool, Optional[Tuple[int, int]]]]:
    """Split a rest at beat boundaries, shortest piece first.

    A 1.25-beat rest beginning at beat 2.75 becomes "16th + quarter" (short piece
    on the beat's tail, then the full beat) instead of "quarter + 16th", which is
    the standard, beat-aligned engraving convention.
    """
    # Asymmetric meters: break at the declared beat groups (5/8 = 3+2, 7/8 = 2+2+3).
    if boundaries:
        cuts = [b for b in boundaries if start + 1e-9 < b < start + beats - 1e-9]
        if cuts:
            parts: List[Tuple[float, str, bool, Optional[Tuple[int, int]]]] = []
            cursor = start
            for b in cuts + [start + beats]:
                parts += _decompose_rest_segment(b - cursor, cursor)
                cursor = b
            return parts

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
