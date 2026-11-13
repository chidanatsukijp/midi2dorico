"""Shared data model for the MIDI -> MusicXML pipeline.

All timing values are expressed in quarter-note *beats* (tempo independent),
so the quantization grid and the VST-delay / arpeggio / overlap thresholds stay
stable regardless of the source file's BPM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Note:
    pitch: int                       # 0-127 MIDI pitch
    start: float                     # onset, in quarter-note beats
    end: float                       # offset, in quarter-note beats
    velocity: int = 64
    staff: Optional[int] = None      # 1 = right/upper, 2 = left/lower (grand staff)
    is_arpeggio: bool = False
    arpeggio_dir: Optional[str] = None   # "up" or "down"
    staccato: bool = False           # consolidated staccato (short note lengthened + staccato dot)
    glissando: str = ""              # "", "start", "continue", "stop" for glissando runs
    time_mod: Optional[Tuple[int, int]] = None   # tuplet (actual, normal) e.g. (3,2), (5,4), (7,4)
    is_run: bool = False             # micro-run note (already positioned; skip quantization)
    # pitch spelling (populated before XML export)
    step: str = ""
    alter: int = 0
    octave: int = 4

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Track:
    index: int
    name: str = ""
    channel: int = 0
    notes: List[Note] = field(default_factory=list)
    cc: Dict[int, List[Tuple[float, int]]] = field(default_factory=dict)

    # Engraving configuration (filled by the config file or the heuristic inspector)
    part_name: str = ""
    instrument_type: str = "other"   # strings|winds|brass|piano|harp|percussion|other
    merge_group_id: Optional[str] = None
    min_playable_pitch: int = 36
    keyswitch_map: Dict[int, str] = field(default_factory=dict)
    auto_vst_delay_compensation: bool = True
    manual_delay_beats: Optional[float] = None
    transpose_semitones: int = 0
    pitch_remap: Dict[int, int] = field(default_factory=dict)
    unpitched: bool = False           # use unpitched percussion notation
    staff_lines: int = 5
    program_changes: List[Tuple[float, int]] = field(default_factory=list)              # staff line count for unpitched percussion (1 = single line)

    # Derived state produced by the pipeline
    vst_delay_beats: float = 0.0
    directions: List[Tuple[float, str]] = field(default_factory=list)   # (beat, technique text)
    harp_pedal_changes: List[Tuple[float, Dict[str, int]]] = field(default_factory=list)  # (beat, {step: alter})


ONSET_EPS = 1e-6
"""Tolerance used everywhere two onsets are considered simultaneous."""


def same_onset(a: float, b: float, eps: float = ONSET_EPS) -> bool:
    return abs(a - b) <= eps


def group_by_onset(items, eps: float = ONSET_EPS):
    """Group objects exposing ``.start`` that share an onset, preserving order.

    The caller is responsible for sorting when the input is not already ordered.
    """
    groups = []
    for item in items:
        if groups and same_onset(item.start, groups[-1][0].start, eps):
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


@dataclass
class Score:
    title: str = "Full Score"
    tracks: List[Track] = field(default_factory=list)
    ticks_per_beat: int = 480
    time_sig_beats: int = 4
    time_sig_beat_type: int = 4
    time_sigs: List[Tuple[float, int, int]] = field(default_factory=list)  # (beat, numerator, denominator)
    tempos: List[Tuple[float, float]] = field(default_factory=list)        # (beat, BPM) — deduped
    tempo_bpm: float = 120.0
    key_fifths: int = 0
