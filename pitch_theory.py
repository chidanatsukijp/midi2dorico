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


#: order the sharps / flats appear in a key signature
SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"

#: scale degrees (0 = tonic) normally raised (4th, 1st) or lowered (7th, 3rd, 6th, 2nd)
RAISE_DEGREES = {0, 3}
LOWER_DEGREES = {1, 2, 5, 6}
LETTER_ORDER = "CDEFGAB"
CIRCLE_OF_FIFTHS = "FCGDAEB"


def tonic_letter(fifths: int) -> str:
    """Letter of the tonic for a key signature."""
    return CIRCLE_OF_FIFTHS[(1 + fifths) % 7]


def degree_of(letter: str, fifths: int) -> int:
    """Scale degree of a letter in this key (0 = tonic)."""
    tonic = LETTER_ORDER.index(tonic_letter(fifths))
    return (LETTER_ORDER.index(letter) - tonic) % 7


def key_accidentals(fifths: int) -> Dict[str, int]:
    """Accidental carried by each letter in the key signature."""
    acc = {letter: 0 for letter in "CDEFGAB"}
    if fifths > 0:
        for letter in SHARP_ORDER[:min(fifths, 7)]:
            acc[letter] += 1
    elif fifths < 0:
        for letter in FLAT_ORDER[:min(-fifths, 7)]:
            acc[letter] -= 1
    return acc


def spell_note(pitch: int, fifths: int,
               prev_pitch: int | None = None,
               next_pitch: int | None = None) -> Tuple[str, int]:
    """Pick the spelling a musician would write in this key.

    Ranking is tiered, because these cases must not be traded against each other:

    1. a note that is diatonic in the key always wins;
    2. otherwise, returning a letter the key signature alters back to its natural
       state (B-flat major: B natural, not C-flat);
    3. otherwise the usual chromatic conventions decide - raise the 4th and 1st
       degree, lower the 7th, 3rd, 6th and 2nd - and the melodic direction breaks
       what is left: an ascending note is written sharp, a descending one flat.
    """
    key_acc = key_accidentals(fifths)

    def rank(candidate: Tuple[str, int]) -> Tuple[int, float, int]:
        step, alter = candidate
        base = key_acc[step]
        diff = alter - base

        if diff == 0:
            return (3, 0.0, alter)
        if base != 0 and diff == -base:
            return (2, 0.0, alter)

        merit = 0.0
        degree = degree_of(step, fifths)
        if diff == 1 and degree in RAISE_DEGREES:
            merit += 1.0
        if diff == -1 and degree in LOWER_DEGREES:
            merit += 1.0
        if prev_pitch is not None and prev_pitch != pitch:
            rising = pitch > prev_pitch
            if rising and diff > 0:
                merit += 1.2
            elif not rising and diff < 0:
                merit += 1.2
        if next_pitch is not None and next_pitch != pitch:
            rising = next_pitch > pitch
            if rising and diff > 0:
                merit += 1.0
            elif not rising and diff < 0:
                merit += 1.0
        if fifths > 0 and alter > 0:
            merit += 0.3
        elif fifths < 0 and alter < 0:
            merit += 0.3
        return (1, merit, alter)

    return max(PC_SPELLINGS[pitch % 12], key=rank)
