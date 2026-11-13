"""Common music-theory tables: pitch classes, spellings, key signatures, ranges.

Everything here is pure data + pure functions; no MIDI, no XML, no config model.
(The harp solver and the exporter used to keep private copies of these.)
"""
from __future__ import annotations

from typing import Dict, List, Tuple

#: Diatonic step -> semitones above C
STEP_SEMITONE: Dict[str, int] = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

#: pitch class -> every legal (step, alter) spelling within a single accidental
PC_SPELLINGS: Dict[int, List[Tuple[str, int]]] = {
    0: [("C", 0), ("B", 1)],
    1: [("C", 1), ("D", -1)],
    2: [("D", 0)],
    3: [("D", 1), ("E", -1)],
    4: [("E", 0), ("F", -1)],
    5: [("E", 1), ("F", 0)],
    6: [("F", 1), ("G", -1)],
    7: [("G", 0)],
    8: [("G", 1), ("A", -1)],
    9: [("A", 0)],
    10: [("A", 1), ("B", -1)],
    11: [("B", 0), ("C", -1)],
}

#: default (sharp) spelling for non-harp parts
DEFAULT_SHARP_SPELLING: Dict[int, Tuple[str, int]] = {
    0: ("C", 0), 1: ("C", 1), 2: ("D", 0), 3: ("D", 1), 4: ("E", 0), 5: ("F", 0),
    6: ("F", 1), 7: ("G", 0), 8: ("G", 1), 9: ("A", 0), 10: ("A", 1), 11: ("B", 0),
}

#: enharmonic spelling for flat keys (F, Bb, Eb, Ab, ...)
DEFAULT_FLAT_SPELLING: Dict[int, Tuple[str, int]] = {
    0: ("C", 0), 1: ("D", -1), 2: ("D", 0), 3: ("E", -1), 4: ("E", 0), 5: ("F", 0),
    6: ("G", -1), 7: ("G", 0), 8: ("A", -1), 9: ("A", 0), 10: ("B", -1), 11: ("B", 0),
}

#: key name -> circle-of-fifths position
KEY_TO_FIFTHS: Dict[str, int] = {
    "Cb": -7, "Gb": -6, "Db": -5, "Ab": -4, "Eb": -3, "Bb": -2, "F": -1,
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "Abm": -7, "Ebm": -6, "Bbm": -5, "Fm": -4, "Cm": -3, "Gm": -2, "Dm": -1,
    "Am": 0, "Em": 1, "Bm": 2, "F#m": 3, "C#m": 4, "G#m": 5, "D#m": 6, "A#m": 7,
}

#: the harp's seven strings, in pedal order
HARP_STEPS: List[str] = ["D", "C", "B", "E", "F", "G", "A"]


def octave_of(pitch: int, step: str, alter: int) -> int:
    """Scientific-pitch octave number of a spelled note."""
    return round((pitch - STEP_SEMITONE[step] - alter) / 12) - 1
