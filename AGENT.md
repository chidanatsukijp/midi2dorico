# AGENT.md — ground rules for working on this repository

Written for AI coding agents and new contributors. Everything here is a
constraint that existing tests and downstream scores depend on.

## What this project is

`midi2dorico` converts a multi-track orchestral DAW MIDI into a Dorico-ready
MusicXML full score. It is not a format converter: every note, rest, voice and
technique mark is the result of an explicit editorial rule. **If you cannot
measure a change, do not make it.**

## Hard invariants

1. **`DIVISIONS = 1680` in `xml_exporter.py` must not change.** It is divisible
   by 2, 3, 4, 5, 6, 7, 8 and 12, which keeps every tuplet and dotted value
   integral. Changing it invalidates every stored score.
2. **Generated MusicXML must validate** against `musicxml_local.xsd`.
3. **`python regression_test.py` must pass** (currently 8 checks). It is the CI
   gate (`.github/workflows/tests.yml`).
4. **`python audit_notation.py <score>` must report 0 violations** (rules R1–R7).
   The tool exits non-zero when a rule breaks, so it is a real gate.
5. **Percussion never receives ties or slurs.** Drum "notes" are hits.
6. **Phrase slurs are intentionally not generated.** There is no `slur_*` field
   on `Note`/`Piece`; do not reintroduce one without a decision from the owner.
7. **Every new config option must be added to `config_schema.py`** (Pydantic) and
   documented in the README tables. Options are read through `getattr(config, …)`
   with defaults so old config files keep working.
8. **No new required dependency.** Runtime is `mido`, `pydantic`, `lxml`.
   Rendering helpers (`verovio`, `pymupdf`, `reportlab`, `svglib`) stay optional
   in `requirements-render.txt` and must not be imported by the core path.
9. **`track.notes` stays sorted by `(start, pitch)`** between pipeline stages.
   Several stages move onsets in place; `timing_cleaner._ensure_sorted` restores
   the invariant at the end of `process_track`.
10. **Never silently drop musical material.** Unmapped keyswitches, unknown
    instrument families and unreadable files must produce a message, not silence.

## Before you commit

```bash
python regression_test.py
python main.py --input test_orchestra.mid --config engraving_config.json --output out.musicxml
python audit_notation.py out.musicxml
```

All three must be clean. If you touched timing logic, also run
`python audit_fidelity.py test_orchestra.mid engraving_config.json`.

## Where things live

| Concern | Module |
|---|---|
| MIDI parsing, config application, measure grid | `midi_inspector.py` |
| Timing/quantization/clean-up stages | `timing_cleaner.py` |
| Grand-staff split (Viterbi), harp pedals | `grand_staff_splitter.py`, `harp_solver.py` |
| MusicXML serialization | `xml_exporter.py` |
| Domain objects (dataclasses) | `models.py` |
| Configuration model (Pydantic) | `config_schema.py` |
| Sample-library profiles | `profiles.py`, `profiles/*.json` |
| Audits | `audit_notation.py`, `audit_fidelity.py` |
| Rendering previews | `render_pdf.py`, `render_musicxml.py`, `preview_part.py` |

## Adding a feature

1. Put the *decision* in `timing_cleaner.py` (before export), not in the exporter.
2. If it needs a knob, add it to `config_schema.py` **and** the README table.
3. Add a regression check to `regression_test.py` that fails when the rule is
   violated — not just a happy-path snapshot.
4. Run the three commands above, then update `docs/architecture.md` if the data
   flow changed.
