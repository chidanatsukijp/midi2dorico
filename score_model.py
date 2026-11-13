"""Internal score model shared by the writing stages: Part and Piece."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from models import Note


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
