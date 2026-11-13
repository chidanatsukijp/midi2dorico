"""Interactive engraving setup: ask the user about uncertain track settings.

Instead of guessing, this walks through every track and asks the user to confirm
or override the auto-detected instrument type, part name, transposition,
keyswitch threshold, and percussion pitch remap — then writes an
`engraving_config.json` for the conversion.

Run via `python main.py --interactive --input foo.mid --output foo.musicxml`.
"""
from __future__ import annotations

import json
from typing import Dict, List

from config_schema import ScoreEngravingConfig, TrackEngravingRule
from midi_inspector import (
    build_default_config,
    clean_part_name,
    classify_track,
    infer_unpitched,
    parse_midi,
    suggest_timpani_pitches,
)


def _ask(prompt: str, default) -> str:
    if default in (None, ""):
        s = input(f"{prompt}: ").strip()
    else:
        s = input(f"{prompt} [{default}]: ").strip()
    return s if s != "" else str(default)


def _ask_int(prompt: str, default: int) -> int:
    s = _ask(prompt, default)
    try:
        return int(s)
    except ValueError:
        return default


def _parse_remap(s: str) -> Dict[int, int]:
    remap: Dict[int, int] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            a, b = part.split(":", 1)
            try:
                remap[int(a.strip())] = int(b.strip())
            except ValueError:
                continue
    return remap


def _pitch_range(track) -> str:
    if not track.notes:
        return "-"
    return f"{min(n.pitch for n in track.notes)}..{max(n.pitch for n in track.notes)}"


def build_config_interactive(input_path: str, output_config_path: str) -> ScoreEngravingConfig:
    score = parse_midi(input_path)
    base = build_default_config(score)
    # Apply so heuristic instrument types / min pitches are visible as defaults.
    for track in score.tracks:
        for r in base.tracks:
            if r.track_index == track.index:
                track.instrument_type = r.instrument_type
                track.part_name = clean_part_name(track.name)
                track.min_playable_pitch = r.min_playable_pitch
                break

    print("=" * 70)
    print("Interactive engraving setup")
    print(f"Tempo {score.tempo_bpm:.1f} BPM | {score.time_sig_beats}/{score.time_sig_beat_type} "
          f"| key signature fifths = {score.key_fifths}")
    print("Press Enter to accept the suggested default for each question.")
    print("=" * 70)

    title = _ask("\nScore title", base.title)
    score_order = _ask("Part order (track = DAW order / standard = orchestral)", "track")

    timpani_suggestion = suggest_timpani_pitches(score)

    rules: List[TrackEngravingRule] = []
    for track in score.tracks:
        itype, minp = classify_track(track.name)
        print(f"\n--- Track {track.index}: {track.name!r} ---")
        print(f"    {len(track.notes)} notes, pitch {_pitch_range(track)}")

        part_name = _ask("  part name", clean_part_name(track.name))
        instrument = _ask("  instrument type (strings/winds/brass/piano/harp/percussion/other)", itype)

        transpose = 0
        if instrument in ("brass", "winds"):
            transpose = _ask_int("  transposition in semitones (+7 = Horn in F, +2 = Trumpet in Bb, 0 = concert)", 0)

        pitch_remap: Dict[int, int] = {}
        if instrument == "percussion":
            low = sorted({n.pitch for n in track.notes if n.pitch < 12})
            if low:
                if "timpani" in track.name.lower() and timpani_suggestion:
                    default_remap = ",".join(f"{k}:{v}" for k, v in timpani_suggestion.items())
                    print(f"    note: low trigger pitches {low} -> suggesting timpani tonic/dominant remap.")
                else:
                    default_remap = ""
                    print(f"    note: low trigger pitches {low} (likely unpitched percussion triggers).")
                remap_str = _ask("  remap triggers to real pitches (e.g. 0:38,1:45; empty = keep as-is)", default_remap)
                pitch_remap = _parse_remap(remap_str)

        min_playable = _ask_int("  min playable pitch (notes below are stripped as keyswitches)", minp)
        unpitched, staff_lines = infer_unpitched(part_name, instrument)

        rules.append(TrackEngravingRule(
            track_index=track.index,
            original_name=track.name,
            part_name=part_name,
            instrument_type=instrument,
            min_playable_pitch=min_playable,
            keyswitch_map={},
            auto_vst_delay_compensation=True,
            transpose_semitones=transpose,
            pitch_remap=pitch_remap,
            unpitched=unpitched,
            staff_lines=staff_lines,
        ))

    config = ScoreEngravingConfig(title=title, score_order=score_order, tracks=rules)
    with open(output_config_path, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)
    print(f"\nWrote {output_config_path}")
    return config


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", default="engraving_config.json")
    args = p.parse_args()
    build_config_interactive(args.input, args.output)
