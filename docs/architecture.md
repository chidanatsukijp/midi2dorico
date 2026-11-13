# Architecture

How a MIDI file becomes a Dorico-ready full score, and where each decision lives.

```
                    ┌──────────────────────────────────────────────────────────┐
   your.mid ───────▶│ midi_inspector.parse_midi  →  Score / Track / Note       │
                    └──────────────────────────────────────────────────────────┘
                                        │
                     config resolution  │  build_default_config (name heuristics)
                     (main.build_config)│  + --config (explicit, wins)
                                        │  + --profile (library conventions)
                                        ▼
                    ┌──────────────────────────────────────────────────────────┐
                    │ apply_config → merge_tracks_by_group → measure grid      │
                    └──────────────────────────────────────────────────────────┘
                                        │
                     per track (timing_cleaner.process_track)                │
                     strip keyswitches → VST delay → glissando → micro-runs   │
                     → arpeggio collapse → mark detached → quantize           │
                     → dedupe → trim → transpose → merge rests → staccato     │
                                        │
                     grand_staff_splitter (piano/harp) → harp_solver (pedals) │
                                        ▼
                    ┌──────────────────────────────────────────────────────────┐
                    │ xml_exporter.build_musicxml  →  MusicXML 4.0              │
                    │   voices → rest spelling → beams → notations → directions│
                    └──────────────────────────────────────────────────────────┘
                                        │
                     audit_notation (R1–R7) · audit_fidelity (vs MIDI) · XSD
```

## Modules

| Module | Responsibility | Must not |
|---|---|---|
| `models.py` | `Note`, `Track`, `Score` dataclasses; `group_by_onset` / `same_onset` shared by every stage | import anything from the pipeline |
| `pitch_theory.py` | pitch classes, spellings, key signatures, harp string order | parse MIDI |
| `config_schema.py` | Pydantic `ScoreEngravingConfig` / `TrackEngravingRule` | contain engraving logic |
| `midi_inspector.py` | MIDI → `Score`, track classification, config application, track merging | know about MusicXML |
| `measure_grid.py` | bar starts and lengths from the time signatures | parse MIDI |
| `techniques.py` | program changes → technique directions | know about XML |
| `timing_cleaner.py` | every *timing* decision: quantization, rest merging, staccato, arpeggio collapse, keyswitches, VST delay | emit XML |
| `grand_staff_splitter.py` | assign notes to staff 1/2 (Viterbi) | touch timing |
| `harp_solver.py` | 7-pedal search, enharmonic respelling, `<harp-pedals>` data | touch timing |
| `xml_exporter.py` | serialize: voice split, rest spelling, beams, notations, directions | parse MIDI |
| `profiles.py` | reusable per-library conventions | contain music-specific data |
| `main.py` | CLI, config resolution, error messages | encode engraving rules |

## Key decisions

**Measure grid.** `build_measure_map` walks the time signatures and produces
`(start_beat, numerator, denominator)` for every measure; all length math is in
quarter-note beats, never in ticks.

**Rest spelling.** `_decompose_rest` splits a rest at the bar midpoint (4/4) and
then chooses *standard* values near that split, so a 1.25-beat gap becomes
`eighth + eighth` rather than an illegal note. "Ineffective rests" (a short note
followed by a short rest) are merged into one longer value earlier, in
`timing_cleaner.consolidate_staccato`.

**Voices.** `_split_voices` is greedy: an onset group that starts while voice 1
still sounds goes to voice 2. Two voices per staff, no more. Chord members always
share a voice.

**Transposition.** Notes are transposed to written pitch *after* keyswitch
stripping (so trigger pitches below the playable range are removed in concert
pitch), and the exporter emits `diatonic` from the chromatic remainder with the
octave in `<octave-change>`.

**Techniques.** Two sources: program changes (`program_techniques`) and
keyswitch pitches (`keyswitch_map`, three-level precedence: track → score →
disabled). `attach_technique_directions` is idempotent, so both `main.run` and a
direct `build_musicxml` call produce the same directions.

## Extension points

| Want to… | Do this |
|---|---|
| Support a new sample library | add `profiles/<name>.json` (score block + track patterns) |
| Rename/retag instruments in a foreign language | add `tracks` patterns, or a `match` rule with `instrument_type` |
| Change rest style | `rest_style: "standard" \| "merged"` |
| Change glissando detection | `glissando_max_step_semitones` |
| Add a notation rule | `audit_notation.py` + a regression check |

## Known limitations

1. **Instruments are classified by English track names.** Non-English names fall
   back to other and log a warning; a profile 	racks pattern can override them.
2. **Pitched staves hold two voices; a drum kit holds three.** A pitched staff folds
   a third simultaneous line into the busiest voice; percussion gets hands (1),
   feet (2) and a second hand line (3) when the hands really overlap.
3. **eat_grouping only covers meters you declare.** Defaults are 5/8 = 3+2 and
   7/8 = 2+2+3; add e.g. "11/16": [3, 2, 3, 3] for anything else. Undeclared
   asymmetric meters keep the generic midpoint rule.
4. **Preview renderers do not wrap part names.** <part-abbreviation> is emitted
   for later systems and 
ender_pdf.py shortens over-long names in a temporary
   copy before rendering, so the preview stays readable; the exported score keeps
   the original names.


## History of this document

Decoupling work completed after the first architecture review:

- the measure grid and the technique mapping moved out of `midi_inspector.py`
  (`measure_grid.py`, `techniques.py`), so `xml_exporter.py` no longer imports the
  MIDI layer at all;
- the GM instrument/percussion tables moved to `data/gm.json`;
- asymmetric meters now break rests at declared beat groups.
