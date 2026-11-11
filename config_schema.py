"""Pydantic schema for the engraving configuration (the "AI rule layer").

The pipeline runs fully without this: `midi_inspector` builds a heuristic
default config automatically. This schema lets an external LLM (or a
hand-written JSON file) steer engraving decisions explicitly.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# Default keyswitch layout: the common "C0 = 24" convention used by most
# orchestral libraries. Override per score (ScoreEngravingConfig.keyswitch_map) or
# per track (TrackEngravingRule.keyswitch_map) when a library sits elsewhere.
DEFAULT_KEYSWITCH_MAP: Dict[str, str] = {
    "24": "arco",
    "25": "pizz.",
    "26": "pizz.",
    "27": "staccato",
    "28": "spiccato",
    "29": "legato",
    "30": "tremolo",
    "31": "sul pont.",
}


InstrumentType = Literal["strings", "winds", "brass", "piano", "harp", "percussion", "other"]


class TrackEngravingRule(BaseModel):
    track_index: int = Field(..., description="0-based MIDI track index")
    original_name: str = Field("", description="Original track name from the MIDI file")
    part_name: str = Field("", description="Clean part name shown in Dorico")
    instrument_type: InstrumentType = Field("other", description="Instrument family controlling engraving logic")
    merge_group_id: Optional[str] = Field(
        default=None,
        description="Tracks sharing the same merge_group_id are merged into one score part",
    )
    min_playable_pitch: int = Field(
        default=36,
        description="MIDI notes below this pitch are treated as keyswitches and stripped",
    )
    keyswitch_map: Dict[int, str] = Field(
        default_factory=dict,
        description="Mapping of keyswitch MIDI note numbers to Dorico playing-technique text",
    )
    auto_vst_delay_compensation: bool = Field(
        default=True,
        description="Estimate and remove negative track pre-delay before quantization",
    )
    manual_delay_offset_beats: Optional[float] = Field(
        default=None,
        description="Optional manual forward shift in beats (positive = compensate for "
                    "early notes) overriding auto-detection.",
    )
    transpose_semitones: int = Field(
        default=0,
        description="Written-pitch transposition. Positive = notate higher "
                    "(e.g. Horn in F = +7, Trumpet in Bb = +2). 0 = concert pitch.",
    )
    pitch_remap: Dict[int, int] = Field(
        default_factory=dict,
        description="Remap source MIDI pitches to real pitches before engraving "
                    "(e.g. a timpani VST that uses triggers 0/1 -> {0: 38, 1: 45}).",
    )
    unpitched: bool = Field(
        default=False,
        description="Use unpitched percussion notation (<unpitched>) instead of a pitched staff.",
    )
    staff_lines: int = Field(
        default=5,
        description="Staff line count for unpitched percussion (1 = single line, 5 = full staff).",
    )


class ScoreEngravingConfig(BaseModel):
    title: str = Field(default="Orchestral Full Score")
    score_order: Literal["track", "standard"] = Field(
        default="track",
        description="Part order: 'track' keeps DAW/MIDI track order; 'standard' sorts "
                    "by orchestral family (winds, brass, percussion, harp/piano, strings).",
    )
    keyswitch_map: Dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_KEYSWITCH_MAP),
        description="Default keyswitch pitch (as text) -> technique text. A track's own "
                    "keyswitch_map takes precedence; unmapped pitches are dropped as silent "
                    "triggers. Change this when the library's keyswitches are not at C0=24.",
    )
    quantize_grid_beats: float = Field(default=0.25, description="Base quantization grid (0.25 = 16th note)")
    arpeggio_window_min_beats: float = Field(default=0.03, description="Min inter-note onset spread for rolled chords")
    arpeggio_window_max_beats: float = Field(default=0.15, description="Max inter-note onset spread for rolled chords")
    max_overlap_trim_beats: float = Field(default=0.5, description="Max legato tail overlap (beats) to clip")
    program_techniques: Dict[str, str] = Field(
        default_factory=dict,
        description="Map a MIDI program number (as a string) to the technique text it "
                    "means in this DAW, e.g. {'45': 'pizz.', '44': 'trem.'}. The text is "
                    "written as a direction when that program is switched on.",
    )
    glissando_max_step_semitones: int = Field(
        default=24,
        description="Largest interval (semitones) allowed between adjacent notes of a "
                    "glissando sweep. 24 keeps every sweep (a harp glissando through a "
                    "chord moves in thirds); set 2 to notate only stepwise scale slides "
                    "and leave chord sweeps as written-out arpeggios.",
    )
    rest_style: Literal["standard", "merged"] = Field(
        default="standard",
        description="How surviving rests are spelled. 'standard' follows engraving rules "
                    "(show each beat, never cross the middle of a 4/4 bar). 'merged' writes "
                    "the longest single rest symbol possible, which removes 8th/16th rests "
                    "but is non-standard.",
    )
    tracks: List[TrackEngravingRule] = Field(default_factory=list)
