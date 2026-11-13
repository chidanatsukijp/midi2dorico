"""CLI entry point: orchestral MIDI -> Dorico-optimized MusicXML full score."""
from __future__ import annotations

import argparse
import json
import os

import midi_inspector
import profiles
import timing_cleaner
import grand_staff_splitter
import harp_solver
import xml_exporter
from config_schema import ScoreEngravingConfig


def resolve_input(path: str) -> str:
    """Fall back to a local copy next to this script when the given path is gone
    (e.g. an external drive that keeps disconnecting)."""
    if os.path.exists(path):
        return path
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(path))
    return local if os.path.exists(local) else path


def _read_json(path: str, what: str) -> dict:
    """Read a JSON file with an error message a musician can act on."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"{what} not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{what} is not valid JSON: {path}\n  {exc.msg} "
                         f"(line {exc.lineno}, column {exc.colno})")
    except OSError as exc:
        raise SystemExit(f"cannot read {what}: {path}\n  {exc.strerror}")


def build_config(score, config_path: str = None,
                 profile_path: str = None) -> ScoreEngravingConfig:
    """Resolve the engraving config: heuristics, an explicit file, and/or a profile."""
    from pydantic import ValidationError

    if config_path:
        raw = _read_json(config_path, "config file")
        try:
            return (profiles.merge_raw(raw, profiles.load(profile_path))
                    if profile_path else ScoreEngravingConfig(**raw))
        except ValidationError as exc:
            raise SystemExit(f"config file is invalid: {config_path}\n{exc}")
    config = midi_inspector.build_default_config(score)
    if profile_path:
        # No explicit config: the profile overrides the automatic detection.
        config = profiles.merge(config, profiles.load(profile_path))
    return config


def run(input_path: str, output_path: str, config_path: str = None, manifest_path: str = None,
        profile_path: str = None):
    input_path = resolve_input(input_path)
    if not os.path.exists(input_path):
        raise SystemExit(f"input MIDI not found: {input_path}")
    try:
        score = midi_inspector.parse_midi(input_path)
    except Exception as exc:                    # mido raises a variety of errors
        raise SystemExit(f"cannot read MIDI: {input_path}\n  {exc}")
    if not score.tracks:
        raise SystemExit(f"no tracks in {input_path}")
    config = build_config(score, config_path, profile_path)
    midi_inspector.apply_config(score, config)
    # Merge multi-track parts before any cleanup work.
    midi_inspector.merge_tracks_by_group(score, getattr(config, "program_techniques", {}))

    # Stage 1 diagnostic manifest is produced before any processing so it shows
    # the *original* keyswitch notes and the detected VST offset.
    if manifest_path:
        manifest = midi_inspector.inspect(score, config)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    measures = midi_inspector.build_measure_map(score)
    for track in score.tracks:
        midi_inspector.apply_pitch_transforms(track)
        timing_cleaner.process_track(track, config, measures)
        if track.instrument_type in ("piano", "harp"):
            grand_staff_splitter.split_grand_staff(track)
        if track.instrument_type == "harp":
            harp_solver.solve_harp(track, measures)

    xml_text = xml_exporter.build_musicxml(score, config)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_text)
    return score, config


def main() -> None:
    p = argparse.ArgumentParser(description="Convert multi-track orchestral MIDI to a Dorico MusicXML full score.")
    p.add_argument("--input", required=True, help="Input .mid file")
    p.add_argument("--output", default="full_score.musicxml", help="Output .musicxml file")
    p.add_argument("--config", default=None, help="Optional JSON engraving config (Pydantic schema)")
    p.add_argument("--manifest", default=None, help="Optional output path for the track manifest JSON")
    p.add_argument("--profile", default=None,
                   help="Sample-library profile: a name in profiles/ or a path to a JSON file")
    p.add_argument("--interactive", action="store_true",
                   help="Interactively ask about uncertain track settings, then convert")
    args = p.parse_args()

    if args.interactive:
        import interactive
        config_path = args.config or "engraving_config.json"
        interactive.build_config_interactive(args.input, config_path)
        args.config = config_path

    score, _ = run(args.input, args.output, args.config, args.manifest, args.profile)

    if args.profile:
        print(f"profile: {profiles.resolve(args.profile)}")
    print(f"Wrote {args.output}")
    for track in score.tracks:
        extra = []
        if track.vst_delay_beats:
            extra.append(f"VST shift +{track.vst_delay_beats:.3f} beat")
        ks = len(track.directions)
        if ks:
            extra.append(f"{ks} keyswitch(es)")
        arp = sum(1 for n in track.notes if n.is_arpeggio)
        if arp:
            extra.append(f"{arp} arpeggiated note(s)")
        if track.harp_pedal_changes:
            extra.append(f"{len(track.harp_pedal_changes)} harp pedal change(s)")
        suffix = f"  ({'; '.join(extra)})" if extra else ""
        print(f"  [{track.part_name or track.name}] {track.instrument_type}{suffix}")


if __name__ == "__main__":
    main()
