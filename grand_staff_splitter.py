"""Dynamic Piano / Harp hand separation via Viterbi dynamic programming.

Replaces the naive fixed Middle-C (MIDI 60) split with a cost function over
hand-span, voice-crossing, register priors, and horizontal smoothness.
"""
from __future__ import annotations

from collections import defaultdict
from typing import List, Optional, Tuple

from models import Note, Track


def _frame_notes(notes: List[Note]) -> List[Tuple[float, List[Note]]]:
    """Bucket simultaneous notes (same quantized onset) into frames."""
    frames = defaultdict(list)
    for n in notes:
        frames[round(n.start, 6)].append(n)
    return [(t, sorted(frames[t], key=lambda n: n.pitch)) for t in sorted(frames)]


def split_grand_staff(track: Track) -> None:
    if not track.notes:
        return
    frames = _frame_notes(track.notes)

    # Candidate states per frame: (mean_L, mean_R, split_index, local_cost)
    states: List[List[Tuple[Optional[float], Optional[float], int, float]]] = []
    for _, notes in frames:
        pitches = [n.pitch for n in notes]
        # A rolled (arpeggiated) chord legitimately spans a 10th-12th in one hand,
        # so allow a wider span before penalising it.
        span_limit = 24 if any(getattr(n, "is_arpeggio", False) for n in notes) else 16
        cands: List[Tuple[Optional[float], Optional[float], int, float]] = []
        for i in range(len(pitches) + 1):
            L = pitches[:i]
            R = pitches[i:]
            cost = 0.0
            if L and (max(L) - min(L)) > span_limit:
                cost += 1000.0
            if R and (max(R) - min(R)) > span_limit:
                cost += 1000.0
            if L and R and (sum(L) / len(L)) > (sum(R) / len(R)):
                cost += 500.0
            if L and (sum(L) / len(L)) > 67:
                cost += 50.0
            if R and (sum(R) / len(R)) < 53:
                cost += 50.0
            cands.append((sum(L) / len(L) if L else None, sum(R) / len(R) if R else None, i, cost))
        states.append(cands)

    n_frames = len(states)
    dp: List[List[float]] = []
    back: List[List[int]] = []
    for f in range(n_frames):
        dp.append([0.0] * len(states[f]))
        back.append([0] * len(states[f]))
        for s, (mL, mR, _, local) in enumerate(states[f]):
            if f == 0:
                dp[f][s] = local
            else:
                best = float("inf")
                best_p = 0
                for p, (pL, pR, _, _) in enumerate(states[f - 1]):
                    trans = 0.0
                    if mL is not None and pL is not None:
                        trans += abs(mL - pL) * 1.5
                    if mR is not None and pR is not None:
                        trans += abs(mR - pR) * 1.5
                    cost = dp[f - 1][p] + trans + local
                    if cost < best:
                        best = cost
                        best_p = p
                dp[f][s] = best
                back[f][s] = best_p

    # Backtrace the minimum-cost path.
    path = [0] * n_frames
    path[-1] = min(range(len(states[-1])), key=lambda s: dp[-1][s])
    for f in range(n_frames - 1, 0, -1):
        path[f - 1] = back[f][path[f]]

    # Assign staff: lowest notes -> Left Hand (staff 2), highest -> Right Hand (staff 1).
    for f, (_, notes) in enumerate(frames):
        split_idx = states[f][path[f]][2]
        for k, n in enumerate(notes):
            n.staff = 2 if k < split_idx else 1
