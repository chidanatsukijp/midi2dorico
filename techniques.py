"""Turn MIDI program changes into playing-technique directions."""
from __future__ import annotations

from typing import Dict, Optional

from models import Track


def attach_technique_directions(track: Track,
                                program_techniques: Optional[Dict[str, str]] = None) -> None:
    """Turn program changes into technique texts (pizz. / arco / trem. ...)."""
    if not program_techniques:
        return
    if getattr(track, "_techniques_attached", False):
        return
    for beat, prog in sorted(track.program_changes):
        text = program_techniques.get(str(prog))
        if not text:
            continue
        # An initial "arco" is the default and needs no marking, but a piece that
        # starts pizzicato must say so.
        if beat < 1e-9 and text.strip().lower() in ("arco", "ord.", "ordinario"):
            continue
        track.directions.append((beat, text))
    track._techniques_attached = True
