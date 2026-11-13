"""Regression suite for the MIDI -> MusicXML pipeline.

Run:  python regression_test.py [--quick]

Covers the bugs fixed in the 2026-11 audit plus end-to-end output validation:
  * track classification (bassoon / bass trombone / false percussion names)
  * <transpose> octave handling
  * tuplet ratios for any note count
  * two-voice split on a single staff (held note + moving line)
  * merge_group_id tracks merged BEFORE cleanup
  * every generated score: XSD, measure sums, tie/slur balance, R1-R7 rules
"""
from __future__ import annotations

import glob
import os
import sys
import traceback
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import midi_inspector as MI
import timing_cleaner as TC
import xml_exporter as XE
from config_schema import ScoreEngravingConfig
from models import Note, Score, Track

from lxml import etree

DIVISIONS = XE.DIVISIONS
_results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        _results.append((name, True, str(detail)))
        print(f"  PASS  {name}" + (f"   [{detail}]" if detail else ""))
    except Exception as exc:                       # noqa: BLE001
        _results.append((name, False, f"{exc}"))
        print(f"  FAIL  {name}: {exc}")
        traceback.print_exc(limit=3)


def eq(actual, expected, label=""):
    if actual != expected:
        raise AssertionError(f"{label}: got {actual!r}, expected {expected!r}")


def build(tracks, title="t", key_fifths=0):
    score = Score(tracks=tracks, title=title, ticks_per_beat=480,
                  time_sigs=[(0.0, 4, 4)], key_fifths=key_fifths)
    return XE.build_musicxml(score, ScoreEngravingConfig(title=title))


# ---------------------------------------------------------------- unit tests
def test_classify() -> str:
    expect = {
        "Bassoon": "winds", "Contrabassoon": "winds", "Bass Clarinet": "winds",
        "Bass Trombone": "brass", "Double Bass": "strings", "Bass Drum": "percussion",
        "Custom FX": "other", "Bottom Layer": "other", "Automation": "other",
        "Horn in F": "brass", "Piccolo": "winds", "Cello": "strings",
        "Timpani": "percussion", "Snare Drum": "percussion",
    }
    for name, family in expect.items():
        got = MI.classify_track(name)[0]
        eq(got, family, f"classify({name})")
    # bassoon must not lose its low register to the keyswitch stripper
    if MI.classify_track("Bassoon")[1] > 34:
        raise AssertionError("bassoon min_playable is too high (low notes would be stripped)")
    return f"{len(expect)} names"


def test_transpose() -> str:
    cases = [
        ("Horn in F", 7, "<diatonic>-4</diatonic>", "<chromatic>-7</chromatic>", None),
        ("Trumpet in Bb", 2, "<diatonic>-1</diatonic>", "<chromatic>-2</chromatic>", None),
        ("Double Bass", 12, "<diatonic>0</diatonic>", "<chromatic>0</chromatic>", "<octave-change>-1</octave-change>"),
        ("Piccolo", -12, "<diatonic>0</diatonic>", "<chromatic>0</chromatic>", "<octave-change>1</octave-change>"),
        ("Bass Clarinet", 14, "<diatonic>-1</diatonic>", "<chromatic>-2</chromatic>", "<octave-change>-1</octave-change>"),
    ]
    for name, tr, dia, chrom, octv in cases:
        track = Track(index=0, name=name, part_name=name, instrument_type="brass",
                      transpose_semitones=tr, min_playable_pitch=21)
        track.notes = [Note(pitch=60, start=0.0, end=4.0)]
        xml = build([track])
        flat = xml.replace("\n", "").replace(" ", "")
        for frag in (dia, chrom) + ((octv,) if octv else ()):
            if frag not in flat:
                raise AssertionError(f"{name}: missing {frag}")
        if octv is None and "octave-change" in flat:
            raise AssertionError(f"{name}: unexpected octave-change")
    return f"{len(cases)} instruments"


def test_tuplets() -> str:
    expect = {2: None, 3: (3, 2), 4: None, 5: (5, 4), 6: (6, 4), 7: (7, 4),
              8: None, 9: (9, 8), 10: (10, 8), 11: (11, 8), 12: (12, 8)}
    for n, want in expect.items():
        eq(TC._tuplet_ratio(n), want, f"ratio({n})")
    return f"{len(expect)} counts"


def test_two_voices() -> str:
    track = Track(index=0, name="Vln", part_name="Vln", instrument_type="strings",
                  min_playable_pitch=21)
    track.notes = [Note(pitch=60, start=0.0, end=4.0),
                   Note(pitch=72, start=0.0, end=0.5),
                   Note(pitch=74, start=0.5, end=1.0),
                   Note(pitch=76, start=1.0, end=1.5)]
    root = ET.fromstring(build([track]).encode("utf-8"))
    measure = root.find("part/measure")
    voices = {v.text for v in measure.findall("note/voice")}
    eq(voices, {"1", "2"}, "voices")
    if len(measure.findall("backup")) != 1:
        raise AssertionError("expected exactly one <backup>")

    durs = [int(n.find("duration").text) for n in measure.findall("note") if n.find("rest") is None]
    if 6720 not in durs:
        raise AssertionError(f"held whole note was lost or altered: {durs}")

    # no overlap: single line stays single voice
    simple = Track(index=0, name="Vln", part_name="Vln", instrument_type="strings",
                   min_playable_pitch=21)
    simple.notes = [Note(pitch=60, start=0.0, end=1.0), Note(pitch=64, start=1.0, end=2.0)]
    root2 = ET.fromstring(build([simple]).encode("utf-8"))
    eq({v.text for v in root2.findall("part/measure/note/voice")}, {"1"}, "simple voices")
    return "held + moving line"


def test_merge_before_cleanup() -> str:
    a = Track(index=0, name="Vln1_Legato", part_name="Vln 1", instrument_type="strings",
              merge_group_id="vln1", min_playable_pitch=21)
    a.notes = [Note(pitch=60, start=0.0, end=1.0)]
    b = Track(index=1, name="Vln1_Shorts", part_name="Vln 1", instrument_type="strings",
              merge_group_id="vln1", min_playable_pitch=21)
    b.notes = [Note(pitch=62, start=1.0, end=2.0)]
    score = Score(tracks=[a, b], title="t", ticks_per_beat=480,
                  time_sigs=[(0.0, 4, 4)], key_fifths=0)
    MI.merge_tracks_by_group(score, {})
    if len(score.tracks) != 1:
        raise AssertionError(f"expected 1 merged track, got {len(score.tracks)}")
    if len(score.tracks[0].notes) != 2:
        raise AssertionError("merged track lost notes")
    xml = XE.build_musicxml(score, ScoreEngravingConfig(title="t"))
    root = ET.fromstring(xml.encode("utf-8"))
    if len(root.findall("part")) != 1:
        raise AssertionError("expected one <part> after merge")
    return "2 tracks -> 1 part"



def test_keyswitch_map() -> str:
    """The keyswitch pitch -> technique table must be a parameter, not a constant."""
    from config_schema import DEFAULT_KEYSWITCH_MAP
    from timing_cleaner import strip_keyswitches

    def probe(default_map, track_map=None):
        t = Track(index=0, name="X", part_name="X", instrument_type="strings",
                  min_playable_pitch=36)
        t.keyswitch_map = dict(track_map or {})
        t.notes = [Note(pitch=25, start=0.0, end=0.05),
                   Note(pitch=60, start=1.0, end=2.0)]
        strip_keyswitches(t, default_map)
        return [d[1] for d in t.directions], len(t.notes)

    eq(probe(DEFAULT_KEYSWITCH_MAP)[0], ["pizz."], "built-in table")
    eq(probe({"25": "spicc."})[0], ["spicc."], "custom score table")
    eq(probe({"25": "spicc."}, {25: "custom"})[0], ["custom"], "per-track override")
    eq(probe({})[0], [], "empty table disables mapping")
    if probe(DEFAULT_KEYSWITCH_MAP)[1] != 1:
        raise AssertionError("keyswitch note was not stripped")
    return "4 precedence cases"



def test_profiles() -> str:
    """A profile supplies library conventions; an explicit config still wins."""
    import profiles
    from config_schema import ScoreEngravingConfig

    base = ScoreEngravingConfig(title="t", tracks=[
        {"track_index": 0, "original_name": "Horn in F 1", "part_name": "Horn 1"},
        {"track_index": 1, "original_name": "Violin I", "part_name": "Vln I",
         "instrument_type": "strings", "min_playable_pitch": 55},
    ])
    prof = {
        "score": {"program_techniques": {"45": "pizz."}, "rest_style": "merged"},
        "tracks": [{"match": "horn*", "instrument_type": "brass",
                     "transpose_semitones": 7, "keyswitch_map": {"25": "pizz."}}],
    }
    merged = profiles.merge(base, prof)
    eq(merged.program_techniques, {"45": "pizz."}, "score block merged")
    eq(merged.rest_style, "merged", "score block merged")
    horn = merged.tracks[0]
    eq(horn.instrument_type, "brass", "pattern matched")
    eq(horn.transpose_semitones, 7, "track override")
    eq(horn.keyswitch_map, {25: "pizz."}, "keyswitch keys coerced to int")
    vln = merged.tracks[1]
    eq(vln.instrument_type, "strings", "non-matching track untouched")
    eq(vln.min_playable_pitch, 55, "non-matching track keeps its pitch floor")
    return "2 blocks, 1 pattern"



def test_beat_grouping() -> str:
    """Asymmetric meters break rests at the declared beat groups."""
    grouping = {"5/8": [3, 2], "7/8": [2, 2, 3]}
    eq(XE._measure_boundaries(7, 8, 3.5, grouping), [1.0, 2.0], "7/8 boundaries")
    eq(XE._measure_boundaries(5, 8, 2.5, grouping), [1.5], "5/8 boundaries")
    eq(XE._measure_boundaries(4, 4, 4.0, grouping), None, "4/4 keeps the midpoint rule")

    eq([p[0] for p in XE._decompose_rest(3.5, 0.0, 3.5, [1.0, 2.0])],
       [1.0, 1.0, 1.5], "7/8 spelled 2+2+3")
    eq([p[0] for p in XE._decompose_rest(2.5, 0.0, 2.5, [1.5])],
       [1.5, 1.0], "5/8 spelled 3+2")

    # a 7/8 bar whose only note is the first eighth: everything else is rests
    track = Track(index=0, name="Vln", part_name="Vln", instrument_type="strings",
                  min_playable_pitch=21)
    track.notes = [Note(pitch=60, start=0.0, end=0.5)]
    score = Score(tracks=[track], title="t", ticks_per_beat=480,
                  time_sigs=[(0.0, 7, 8)], key_fifths=0)
    xml = XE.build_musicxml(score, ScoreEngravingConfig(title="t",
                                                        beat_grouping=grouping))
    root = ET.fromstring(xml.encode("utf-8"))
    m = root.find("part/measure")
    total = 0
    for n in m.findall("note"):
        total += int(n.find("duration").text)
    eq(total, int(3.5 * DIVISIONS), "7/8 measure adds up")
    return "5/8 3+2, 7/8 2+2+3"



def test_abbreviations_and_percussion_voices() -> str:
    """Long part names get an abbreviation; a drum kit may use three voices."""
    # -- abbreviation table
    cases = {"Flute / Piccolo": "Fl.", "Trumpet in Bb / Flugelhorn": "Tpt.",
             "Violin II": "Vln. II", "Horn in F 1": "Hn.",
             "Percussion (Triangle, Bass Drum, Snare Drum)": "Perc."}
    for name, want in cases.items():
        eq(XE._abbreviation(name), want, f"abbreviation({name})")

    # -- it reaches the XML
    t = Track(index=0, name="Percussion (Triangle, Bass Drum)", part_name="Percussion (Triangle, Bass Drum)",
              instrument_type="percussion", unpitched=True, staff_lines=5, min_playable_pitch=0)
    t.notes = [Note(pitch=36, start=0.0, end=0.5)]
    root = ET.fromstring(build([t]).encode("utf-8"))
    abbr = root.find("part-list/score-part/part-abbreviation")
    eq(abbr.text if abbr is not None else None, "Perc.", "<part-abbreviation> emitted")

    # -- three simultaneous drum lines: ride eighths, snare roll, bass drum
    kit = Track(index=0, name="Drum kit", part_name="Drum kit", instrument_type="percussion",
                unpitched=True, staff_lines=5, min_playable_pitch=0)
    kit.notes = []
    for i in range(8):
        kit.notes.append(Note(pitch=51, start=i * 0.5, end=i * 0.5 + 0.5))       # ride
    for i in range(8):
        kit.notes.append(Note(pitch=38, start=i * 0.5 + 0.25, end=i * 0.5 + 0.4))  # snare roll
    for i in range(4):
        kit.notes.append(Note(pitch=36, start=i * 1.0, end=i * 1.0 + 0.5))       # kick
    root = ET.fromstring(build([kit]).encode("utf-8"))
    m = root.find("part/measure")
    voices = {v.text for v in m.findall("note/voice")}
    eq(voices, {"1", "2", "3"}, "three drum voices")
    eq(len(m.findall("backup")), 2, "two backups for three voices")
    per_voice = {}
    for n in m.findall("note"):
        v = n.find("voice").text
        per_voice[v] = per_voice.get(v, 0) + int(n.find("duration").text)
    for v, total in per_voice.items():
        eq(total, 4 * DIVISIONS, f"voice {v} fills the bar")
    return "5 abbreviations, 3-voice kit"



def test_key_aware_spelling() -> str:
    """Accidentals follow the key signature and the melodic direction."""
    from pitch_theory import spell_note

    cases = [
        (70, 0, None, None, ("B", -1)),   # C major: B-flat, not A-sharp
        (63, 0, None, None, ("E", -1)),   # C major: E-flat, not D-sharp
        (66, 0, None, None, ("F", 1)),    # C major: F-sharp
        (71, -2, 72, 70, ("B", 0)),       # B-flat major: B natural, not C-flat
        (63, 2, None, None, ("D", 1)),    # D major: D-sharp is diatonic
        (70, 4, None, None, ("A", 1)),    # E major: A-sharp, not B-flat
        (66, -2, 65, 67, ("F", 1)),       # G minor: F-sharp leading tone
        (61, 0, 60, 62, ("C", 1)),        # rising chromatic note: C-sharp
        (61, 0, 62, 60, ("D", -1)),       # falling chromatic note: D-flat
    ]
    for pitch, fifths, prev, nxt, want in cases:
        eq(spell_note(pitch, fifths, prev, nxt), want,
           f"spell({pitch}, fifths={fifths}, prev={prev}, next={nxt})")

    # and it reaches the XML: a B-flat in C major must be B-flat, not A-sharp
    track = Track(index=0, name="Vln", part_name="Vln", instrument_type="strings",
                  min_playable_pitch=21)
    track.notes = [Note(pitch=70, start=0.0, end=1.0)]
    root = ET.fromstring(build([track]).encode("utf-8"))
    pitch_el = root.find("part/measure/note/pitch")
    eq(pitch_el.find("step").text, "B", "XML step")
    eq(pitch_el.find("alter").text, "-1", "XML alter")
    return f"{len(cases)} spellings"


def measure_lengths(root) -> dict:
    """(part_id, measure_number) -> expected divisions, following <time> changes."""
    out = {}
    for part in root.findall("part"):
        beats, beat_type = 4, 4
        for m in part.findall("measure"):
            t = m.find("attributes/time")
            if t is not None:
                beats = int(t.find("beats").text)
                beat_type = int(t.find("beat-type").text)
            out[(part.get("id"), m.get("number"))] = round(beats * DIVISIONS * 4 / beat_type)
    return out


def test_measure_sums(path: str) -> str:
    root = ET.parse(path).getroot()
    expected = measure_lengths(root)
    bad = []
    for part in root.findall("part"):
        for m in part.findall("measure"):
            want = expected[(part.get("id"), m.get("number"))]
            totals: dict[str, int] = {}
            voice = "1"
            for el in m:
                if el.tag == "backup":
                    continue
                if el.tag == "forward":
                    d = int(el.find("duration").text)
                    totals[voice] = totals.get(voice, 0) + d
                    continue
                if el.tag != "note":
                    continue
                v = el.find("voice")
                if v is not None:
                    voice = v.text
                if el.find("chord") is not None:
                    continue
                totals[voice] = totals.get(voice, 0) + int(el.find("duration").text)
            for v, total in totals.items():
                if total != want:
                    bad.append(f"{part.get('id')} m{m.get('number')} v{v}: {total} != {want}")
    if bad:
        raise AssertionError(f"{len(bad)} measure(s) do not add up, e.g. {bad[:3]}")
    return "all voices fill every measure"


def test_balance(path: str) -> str:
    root = ET.parse(path).getroot()
    for tag in ("tie", "slur"):
        starts = sum(1 for e in root.iter(tag) if e.get("type") == "start")
        stops = sum(1 for e in root.iter(tag) if e.get("type") == "stop")
        if starts != stops:
            raise AssertionError(f"{tag}: {starts} starts vs {stops} stops")
    return "ties and slurs balanced"


def test_xsd(path: str) -> str:
    schema = etree.XMLSchema(etree.parse(os.path.join(HERE, "musicxml_local.xsd")))
    if not schema.validate(etree.parse(path)):
        raise AssertionError(str(schema.error_log.last_error)[:200])
    return "schema valid"


def test_rules(path: str) -> str:
    import io
    import contextlib
    import audit_notation
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        audit_notation.audit(path)
    text = buf.getvalue()
    bad = [ln for ln in text.splitlines() if " : 0" not in ln and ln.strip()]
    if bad:
        raise AssertionError("; ".join(bad))
    return "R1-R7 all zero"


# ---------------------------------------------------------------- integration
def discover_scores() -> list:
    """Every <name>.mid with a matching <name>_config.json, plus the built-in test.

    Drop your own MIDI files next to the project and the integration stage picks them
    up automatically; missing ones are skipped.
    """
    found = [("self-test", "test_orchestra.mid", "engraving_config.json")]
    seen = {"test_orchestra"}
    for cfg in sorted(glob.glob(os.path.join(HERE, "*_config.json"))):
        base = os.path.basename(cfg)
        stem = base[: -len("_config.json")]
        if stem in seen:
            continue
        mid = f"{stem}.mid"
        if os.path.exists(os.path.join(HERE, mid)):
            found.append((stem, mid, base))
            seen.add(stem)
    return found


def test_integration(quick: bool) -> str:
    import main as M
    ok = []
    for label, mid, cfg in discover_scores():
        mid_p = os.path.join(HERE, mid)
        cfg_p = os.path.join(HERE, cfg)
        if not (os.path.exists(mid_p) and os.path.exists(cfg_p)):
            continue
        out = os.path.join(HERE, f"_reg_{label}.musicxml")
        M.run(mid_p, out, cfg_p)
        test_xsd(out)
        test_measure_sums(out)
        test_balance(out)
        test_rules(out)
        root = ET.parse(out).getroot()
        ok.append(f"{label}:{len(root.findall('part'))}pt/{len(root.findall('part')[0].findall('measure'))}m")
        os.remove(out)
    return ", ".join(ok)


def main() -> int:
    quick = "--quick" in sys.argv
    print("unit tests")
    check("classify_track", test_classify)
    check("<transpose> octaves", test_transpose)
    check("tuplet ratios", test_tuplets)
    check("two voices per staff", test_two_voices)
    check("merge before cleanup", test_merge_before_cleanup)
    check("keyswitch map is configurable", test_keyswitch_map)
    check("library profiles", test_profiles)
    check("beat grouping", test_beat_grouping)
    check("abbreviations + 3-voice kit", test_abbreviations_and_percussion_voices)
    check("key-aware spelling", test_key_aware_spelling)

    print("integration")
    check("end-to-end scores", lambda: test_integration(quick))

    failed = [r for r in _results if not r[1]]
    print()
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} -> {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
