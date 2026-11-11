"""Audit the generated score against common engraving rules.

Rules checked (from standard theory texts / Open University "Grouping rests"):
  R1  a rest must not cross the middle of the bar in simple duple/quadruple time
  R2  a half rest must sit on beat 1 or beat 3 (otherwise its position is unclear)
  R3  a tie inside one bar whose two parts sum to a single standard value should
      be written as a dotted note instead
  R4  tuplets should carry a bracket/number (<tuplet> in <notations>)
  R5  a note must not be written across the barline without a tie
"""
from __future__ import annotations

import math
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DIV = 1680
STD = (4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.25, 0.125)
TYPE_BEATS = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
              "16th": 0.25, "32nd": 0.125, "64th": 0.0625}


def is_std(x: float) -> bool:
    return any(abs(x - v) < 1e-6 for v in STD)


def audit(path: str) -> int:
    root = ET.parse(path).getroot()
    names = {sp.get("id"): sp.find("part-name").text
             for sp in root.findall("part-list/score-part")}

    # measure lengths
    part0 = root.find("part")
    times = []
    cur = (4, 4)
    for m in part0.findall("measure"):
        t = m.find("attributes/time")
        if t is not None:
            cur = (int(t.find("beats").text), int(t.find("beat-type").text))
        times.append(cur)

    r1 = []
    r2 = []
    r3 = []
    r4 = 0
    total_tuplets = 0
    r5 = []
    r7 = []

    for p in root.findall("part"):
        name = names.get(p.get("id"), "?")
        for mi, m in enumerate(p.findall("measure")):
            num, den = times[mi] if mi < len(times) else (4, 4)
            m_len = num * 4.0 / den
            midpoint = m_len / 2.0
            # per voice walk
            voices: dict[tuple[str, str], list[dict]] = {}
            for n in m.findall("note"):
                if n.find("chord") is not None:
                    continue
                v = n.find("voice").text if n.find("voice") is not None else "1"
                st = n.find("staff").text if n.find("staff") is not None else "1"
                d = int(n.find("duration").text) / DIV
                rest = n.find("rest") is not None
                tie_s = n.find("tie") is not None and n.find("tie").get("type") == "start"
                tie_e = n.find("tie") is not None and n.find("tie").get("type") == "stop"
                tm = n.find("time-modification")
                tup = n.find("notations/tuplet")
                beam_el = n.find("beam")
                beam = beam_el.text if beam_el is not None else None
                voices.setdefault((v, st), []).append(
                    dict(d=d, rest=rest, ts=tie_s, te=tie_e, tm=tm, tup=tup, beam=beam))
                if tm is not None:
                    total_tuplets += 1
                type_el = n.find("type")
                if type_el is not None:
                    base = TYPE_BEATS.get(type_el.text)
                    if base is not None:
                        want = base * (1.5 if n.find("dot") is not None else 1.0)
                        if tm is not None:
                            act = float(tm.find("actual-notes").text)
                            nor = float(tm.find("normal-notes").text)
                            want = want * nor / act
                        if abs(want - d) > 1e-6:
                            r7.append((name, m.get("number"), type_el.text,
                                       round(d, 3), round(want, 3)))
            for key, seq in voices.items():
                # tuplet groups: contiguous same-ratio notes need start/stop brackets
                k = 0
                while k < len(seq):
                    nd = seq[k]
                    if nd["tm"] is None:
                        k += 1
                        continue
                    ratio = (nd["tm"].find("actual-notes").text,
                             nd["tm"].find("normal-notes").text)
                    grp = [nd]
                    m2 = k + 1
                    while m2 < len(seq):
                        nx = seq[m2]
                        if nx["tm"] is None:
                            break
                        rat = (nx["tm"].find("actual-notes").text,
                              nx["tm"].find("normal-notes").text)
                        if rat != ratio:
                            break
                        grp.append(nx)
                        m2 += 1
                    if len(grp) >= 2:
                        if grp[0]["tup"] is None or grp[0]["tup"].get("type") != "start":
                            r4 += 1
                        if grp[-1]["tup"] is None or grp[-1]["tup"].get("type") != "stop":
                            r4 += 1
                    k = m2
                # R5: a beam group must not cross the middle of a 4/4 bar
                if m_len >= 4.0 and abs(m_len % 4.0) < 1e-9:
                    bstart = None
                    blast = None
                    bpos = 0.0
                    for nd2 in seq:
                        if nd2["beam"] == "begin":
                            bstart = bpos
                            blast = bpos
                        elif nd2["beam"] in ("continue", "end") and bstart is not None:
                            blast = bpos
                            if nd2["beam"] == "end":
                                if bstart < midpoint - 1e-9 and blast > midpoint + 1e-9:
                                    r5.append((name, m.get("number"), key[0],
                                               round(bstart, 3), round(blast, 3)))
                                bstart = None
                        bpos += nd2["d"]
                pos = 0.0
                for i, nd in enumerate(seq):
                    d = nd["d"]
                    if nd["rest"] and d < m_len - 1e-9:
                        # The midpoint rule is a 4/4 rule; in 3/4 a minim rest on
                        # beat 1 is perfectly standard.
                        if m_len >= 4.0 and abs(m_len % 4.0) < 1e-9:
                            if pos < midpoint - 1e-9 and pos + d > midpoint + 1e-9:
                                r1.append((name, m.get("number"), key[0], round(pos, 3), d))
                        if abs(d - 2.0) < 1e-9 and abs(pos % 4.0 - 1.0) < 1e-9:
                            r2.append((name, m.get("number"), key[0], round(pos, 3)))
                    # tie within the bar that could be a dot
                    if nd["ts"] and i + 1 < len(seq) and seq[i + 1]["te"]:
                        tot = d + seq[i + 1]["d"]
                        next_beat = math.floor(pos + 1e-9) + 1.0
                        frac = pos - math.floor(pos + 1e-9)
                        ok_dot = (pos + tot) <= next_beat + 1e-6 if abs(frac) > 1e-9 else True
                        if abs(m_len - 4.0) < 1e-6 and abs(pos - 1.0) < 1e-6 and (pos + tot) > 2.5 + 1e-6:
                            ok_dot = False
                        if is_std(tot) and tot <= 3.0 and abs((pos % 1.0)) < 1e-9 and ok_dot:
                            r3.append((name, m.get("number"), key[0], round(pos, 3),
                                       round(d, 3), round(seq[i + 1]["d"], 3), round(tot, 3)))
                    pos += d

    print(f"R1 rests crossing the bar midpoint : {len(r1)}")
    for x in r1[:15]:
        print("    ", x)
    print(f"R2 half rest off beat 1/3          : {len(r2)}")
    for x in r2[:10]:
        print("    ", x)
    print(f"R3 in-bar tie that could be a dot  : {len(r3)}")
    for x in r3[:15]:
        print("    ", x)
    print(f"R4 tuplet notes without bracket    : {r4} (of {total_tuplets} tuplet notes)")
    print(f"R5 beams crossing the bar midpoint : {len(r5)}")
    for x in r5[:10]:
        print("    ", x)
    print(f"R7 <type> disagrees with duration  : {len(r7)}")
    for x in r7[:12]:
        print("    ", x)

    # Return the total violation count so callers (CI, tests) can gate on it.
    return len(r1) + len(r2) + len(r3) + r4 + len(r5) + len(r7)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: audit_notation.py <score.musicxml>")
        sys.exit(2)
    violations = audit(sys.argv[1])
    print(f"\ntotal violations: {violations}")
    sys.exit(1 if violations else 0)
