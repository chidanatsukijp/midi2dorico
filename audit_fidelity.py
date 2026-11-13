"""Measure how much MIDI information the pipeline changes.

For every processed note we look for the source note with the same pitch and the
closest onset, then report onset error, duration delta, dropped and added notes.
"""
from __future__ import annotations

import bisect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import grand_staff_splitter
import harp_solver
import measure_grid
import midi_inspector
import timing_cleaner

TOL = 1e-6
EXACT = 1e-6
SMALL = 0.125          # a 32nd
MED = 0.5              # an 8th


def snapshot(tracks):
    return {t.index: [(n.start, n.end, n.pitch) for n in t.notes] for t in tracks}


def run(mid: str, cfg_path: str = None, profile_path: str = None):
    import main as pipeline
    score = midi_inspector.parse_midi(mid)
    cfg = pipeline.build_config(score, cfg_path, profile_path)
    midi_inspector.apply_config(score, cfg)
    raw = snapshot(score.tracks)
    measures = measure_grid.build_measure_map(score)
    for track in score.tracks:
        midi_inspector.apply_pitch_transforms(track)
        timing_cleaner.process_track(track, cfg, measures)
        if track.instrument_type in ("piano", "harp"):
            grand_staff_splitter.split_grand_staff(track)
        if track.instrument_type == "harp":
            harp_solver.solve_harp(track, measures)
    return score, raw, cfg


def audit(score, raw):
    print(f"{'track':16s} {'raw':>5s} {'out':>5s} {'dropped':>8s} {'onset ok':>9s} "
          f"{'<=32nd':>7s} {'<=8th':>6s} {'>8th':>6s} {'maxΔon':>7s} {'durΔ>beat':>9s}")
    tot = {"raw": 0, "out": 0, "dropped": 0, "exact": 0, "small": 0, "med": 0, "big": 0,
           "max": 0.0, "durbig": 0}
    for track in score.tracks:
        src = raw.get(track.index, [])
        out = track.notes
        # Match by onset first (transposition / pitch remap change the pitch), then
        # check the pitch delta so a real mismatch still shows up.
        # Bring source pitches into the same pitch space as the output notes
        # (pitch remap + written-pitch transposition), otherwise every transposing
        # part looks unmatched.
        remap = track.pitch_remap
        shift = track.transpose_semitones
        src = [(st, en, remap.get(pi, pi) + shift) for st, en, pi in src]
        src_sorted = sorted(src, key=lambda x: x[0])
        starts = [s for s, _e, _p in src_sorted]
        used: set[int] = set()
        exact = small = med = big = 0
        maxd = 0.0
        durbig = 0
        pitch_delta: dict[int, int] = {}
        for n in out:
            # candidate window around the note's onset
            lo = bisect.bisect_left(starts, n.start - 0.6)
            best_i, best_score = None, 1e9
            for i in range(lo, len(src_sorted)):
                s, e, p = src_sorted[i]
                if s > n.start + 0.6:
                    break
                if i in used:
                    continue
                score = abs(s - n.start) + (0.0 if p == n.pitch else 0.05)
                if score < best_score:
                    best_score, best_i = score, i
            if best_i is None:
                big += 1
                continue
            used.add(best_i)
            s, e, p = src_sorted[best_i]
            d = abs(s - n.start)
            pitch_delta[n.pitch - p] = pitch_delta.get(n.pitch - p, 0) + 1
            maxd = max(maxd, d)
            if d <= EXACT:
                exact += 1
            elif d <= SMALL:
                small += 1
            elif d <= MED:
                med += 1
            else:
                big += 1
            if abs((n.end - n.start) - (e - s)) > 1.0:
                durbig += 1
        dropped = len(src) - len(used)
        name = (track.part_name or track.name)[:16]
        main_delta = max(pitch_delta.items(), key=lambda kv: kv[1])[0] if pitch_delta else 0
        print(f"{name:16s} {len(src):5d} {len(out):5d} {dropped:8d} {exact:9d} "
              f"{small:7d} {med:6d} {big:6d} {maxd:7.3f} {main_delta:+3d} {durbig:9d}")
        for k, v in (("raw", len(src)), ("out", len(out)), ("dropped", dropped),
                     ("exact", exact), ("small", small), ("med", med), ("big", big),
                     ("durbig", durbig)):
            tot[k] += v
        tot["max"] = max(tot["max"], maxd)
    print()
    total = max(tot["out"], 1)
    print(f"TOTAL notes out {tot['out']}  (raw {tot['raw']}, dropped {tot['dropped']})")
    print(f"  onset exact       {tot['exact']:5d}  {100*tot['exact']/total:5.1f}%")
    print(f"  onset <= 32nd     {tot['small']:5d}  {100*tot['small']/total:5.1f}%")
    print(f"  onset <= 8th      {tot['med']:5d}  {100*tot['med']/total:5.1f}%")
    print(f"  onset > 8th/unmat {tot['big']:5d}  {100*tot['big']/total:5.1f}%")
    print(f"  max onset error   {tot['max']:.3f} beats")
    print(f"  duration changed > 1 beat on {tot['durbig']} notes")


if __name__ == "__main__":
    args = sys.argv[1:]
    profile = None
    if "--profile" in args:
        i = args.index("--profile")
        if i + 1 >= len(args):
            raise SystemExit("usage: audit_fidelity.py [input.mid] [config.json] [--profile PROFILE]")
        profile = args[i + 1]
        del args[i:i + 2]

    mid = args[0] if args else os.path.join(HERE, "test_orchestra.mid")
    if not os.path.exists(mid):
        raise SystemExit(f"file not found: {mid}\n"
                         "usage: audit_fidelity.py [input.mid] [config.json] [--profile PROFILE]")
    if len(args) > 1:
        cfg = args[1]
    elif profile:
        cfg = None                      # profile only: auto-detect the rest
    else:
        cfg = os.path.join(HERE, "engraving_config.json")
    if cfg and not os.path.exists(cfg):
        raise SystemExit(f"file not found: {cfg}")

    score, raw, _ = run(mid, cfg, profile)
    audit(score, raw)
