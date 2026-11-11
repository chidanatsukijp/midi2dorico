"""Generate a synthetic multi-track orchestral MIDI to self-test the pipeline.

Simulates the four pain points:
  - Violin I: -45ms VST pre-delay + C0/D0 keyswitches + legato overlap
  - Piano: cross-register chords + 30ms rolled arpeggio + overlapping chord tails
  - Concert Harp: F-natural + F# (respelled G-flat) chord + a pedal-changing chord
"""
from __future__ import annotations

import json

import mido


TICKS_PER_BEAT = 480
BPM = 120
TICKS_PER_SECOND = TICKS_PER_BEAT * (BPM / 60)  # 960 ticks/s


def _add_events(track, events):
    """events: list of (abs_time_seconds, mido.Message); converts to delta ticks."""
    events = sorted(events, key=lambda e: e[0])
    last = 0.0
    for t, msg in events:
        msg.time = int(round((t - last) * TICKS_PER_SECOND))
        track.append(msg)
        last = t


def _note(pitch, start, dur, velocity=80):
    return [
        (start, mido.Message("note_on", note=pitch, velocity=velocity, channel=0)),
        (start + dur, mido.Message("note_off", note=pitch, velocity=0, channel=0)),
    ]


def generate(path="test_orchestra.mid"):
    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)

    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(meta)

    # Track 1: Violin I
    vln = mido.MidiTrack()
    vln.append(mido.MetaMessage("track_name", name="Violin I", time=0))
    ev = []
    ev += _note(24, 0.0, 0.05, 10)   # keyswitch C0
    ev += _note(26, 0.0, 0.05, 10)   # keyswitch D0
    ev += _note(72, 0.455, 0.40)     # beat 1.0 shifted -45ms early
    ev += _note(76, 0.955, 0.40)     # beat 2.0 shifted -45ms early
    ev += _note(79, 1.455, 0.40)     # beat 3.0 shifted -45ms early
    ev += _note(72, 2.0, 0.6)        # legato overhang
    ev += _note(76, 2.5, 0.4)
    _add_events(vln, ev)
    mid.tracks.append(vln)

    # Track 2: Piano
    pno = mido.MidiTrack()
    pno.append(mido.MetaMessage("track_name", name="Piano", time=0))
    ev = []
    ev += _note(60, 0.0, 0.5)        # rolled arpeggio C4-E4-G4 (30ms spacing)
    ev += _note(64, 0.03, 0.47)
    ev += _note(67, 0.06, 0.44)
    ev += _note(48, 1.0, 0.5)        # cross-register chord C3 + E5
    ev += _note(76, 1.0, 0.5)
    ev += _note(60, 2.0, 0.6)        # overlapping chord tails
    ev += _note(64, 2.0, 0.6)
    ev += _note(65, 2.5, 0.4)
    ev += _note(69, 2.5, 0.4)
    _add_events(pno, ev)
    mid.tracks.append(pno)

    # Track 3: Concert Harp
    harp = mido.MidiTrack()
    harp.append(mido.MetaMessage("track_name", name="Concert Harp", time=0))
    ev = []
    ev += _note(65, 0.0, 0.5)        # F4 + F#4/Gb4 + C5 -> F# respelled Gb
    ev += _note(66, 0.0, 0.5)
    ev += _note(72, 0.0, 0.5)
    ev += _note(60, 2.5, 0.5)        # measure 1: C4 + F4 + Bb4 (pedal change)
    ev += _note(65, 2.5, 0.5)
    ev += _note(70, 2.5, 0.5)
    _add_events(harp, ev)
    mid.tracks.append(harp)

    mid.save(path)
    print(f"Wrote {path}")


def generate_config(path="engraving_config.json"):
    config = {
        "title": "Orchestral Full Score",
        "quantize_grid_beats": 0.25,
        "arpeggio_window_min_beats": 0.03,
        "arpeggio_window_max_beats": 0.15,
        "max_overlap_trim_beats": 0.5,
        "tracks": [
            {
                "track_index": 1,
                "original_name": "Violin I",
                "part_name": "Violin I",
                "instrument_type": "strings",
                "min_playable_pitch": 55,
                "keyswitch_map": {"24": "legato", "26": "pizz."},
                "auto_vst_delay_compensation": True,
            },
            {
                "track_index": 2,
                "original_name": "Piano",
                "part_name": "Piano",
                "instrument_type": "piano",
                "min_playable_pitch": 0,
                "auto_vst_delay_compensation": True,
            },
            {
                "track_index": 3,
                "original_name": "Concert Harp",
                "part_name": "Concert Harp",
                "instrument_type": "harp",
                "min_playable_pitch": 0,
                "auto_vst_delay_compensation": True,
            },
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    print(f"Wrote {path}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--midi", default="test_orchestra.mid")
    p.add_argument("--config", default="engraving_config.json")
    args = p.parse_args()
    generate(args.midi)
    generate_config(args.config)
