"""Concert harp 7-pedal state solver with enharmonic respelling.

A concert harp has 7 strings per octave (D, C, B | E, F, G, A), each controlled
by a 3-position pedal (flat / natural / sharp). This module finds, per measure,
a valid 7-pedal state that can spell every pitch in that measure, minimising
pedal changes from the previous measure, and respells collisions
(e.g. F# -> Gb when F-natural is already required).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from models import Note, Track
from pitch_theory import (DEFAULT_SHARP_SPELLING,  # noqa: F401 (re-exported)
                          HARP_STEPS as STEPS, PC_SPELLINGS, STEP_SEMITONE)
from measure_grid import measure_index


def _idx_to_state(idx: int) -> Dict[str, int]:
    state: Dict[str, int] = {}
    for step in STEPS:
        state[step] = (idx % 3) - 1
        idx //= 3
    return state


def _state_covers(state: Dict[str, int], pitch_classes) -> bool:
    return all(any(state.get(step) == alter for step, alter in PC_SPELLINGS[pc]) for pc in pitch_classes)


NATURAL_STATE: Dict[str, int] = {s: 0 for s in STEPS}


def _state_changes(a: Dict[str, int], b: Dict[str, int]) -> int:
    return sum(1 for s in STEPS if a.get(s) != b.get(s))


def _choose_spelling(pc: int, state: Dict[str, int]) -> Tuple[str, int]:
    for step, alter in PC_SPELLINGS.get(pc, []):
        if state.get(step) == alter:
            return step, alter
    return DEFAULT_SHARP_SPELLING[pc]


def _set_spelling(note: Note, step: str, alter: int) -> None:
    note.step = step
    note.alter = alter
    note.octave = round((note.pitch - STEP_SEMITONE[step] - alter) / 12) - 1


def solve_harp(track: Track, measures: List[Tuple[float, int, int]]) -> None:
    """Solve harp pedal states per measure.

    `measures` is the ordered (start_beat, numerator, denominator) measure map so
    pedal changes land on real measure boundaries even across time-signature changes.
    """
    if not track.notes:
        return
    groups: Dict[int, List[Note]] = {}
    for n in track.notes:
        mi = measure_index(n.start, measures)
        groups.setdefault(mi, []).append(n)

    prev: Optional[Dict[str, int]] = None
    track.harp_pedal_changes = []
    for mi in sorted(groups):
        notes = groups[mi]
        pcs = set(n.pitch % 12 for n in notes)
        best: Optional[Tuple[int, Dict[str, int]]] = None
        ref = prev if prev is not None else NATURAL_STATE
        for idx in range(3 ** 7):
            state = _idx_to_state(idx)
            if _state_covers(state, pcs):
                changes = _state_changes(ref, state)
                if best is None or changes < best[0]:
                    best = (changes, state)
        if best is None:
            # No consistent state (rare); keep the previous one or go all-natural.
            state = dict(prev) if prev is not None else {s: 0 for s in STEPS}
        else:
            state = best[1]
        for n in notes:
            step, alter = _choose_spelling(n.pitch % 12, state)
            _set_spelling(n, step, alter)
        if prev is None or state != prev:
            track.harp_pedal_changes.append((measures[mi][0], dict(state)))
        prev = state
