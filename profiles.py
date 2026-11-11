"""Reusable sample-library profiles.

A profile records what a particular DAW / sample library means by its program
changes and keyswitches, plus optional per-instrument conventions (family,
transposition). It is the part of a hand-tuned engraving config that is *not*
about one particular piece of music, so it can be reused for every score coming
out of the same template.

Precedence
----------
* without ``--config`` the profile *wins* over the automatic detection;
* with ``--config`` the profile only fills in what the config does not specify.
"""
from __future__ import annotations

import fnmatch
import json
import os
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, "profiles")


def resolve(name_or_path: str) -> str:
    """Return the path of a profile given a file path or a bare name."""
    if os.path.exists(name_or_path):
        return name_or_path
    for candidate in (name_or_path, name_or_path + ".json"):
        path = os.path.join(PROFILE_DIR, candidate)
        if os.path.exists(path):
            return path
    raise SystemExit(
        f"profile not found: {name_or_path}\n"
        f"looked next to the project and in {PROFILE_DIR}")


def load(name_or_path: str) -> Dict[str, Any]:
    with open(resolve(name_or_path), encoding="utf-8") as f:
        return json.load(f)


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """Recursive merge where `over` wins."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def apply(config: Dict[str, Any], profile: Dict[str, Any],
          profile_wins: bool = True) -> Dict[str, Any]:
    """Apply a profile to a config dict."""
    data = dict(config)
    score_block = dict(profile.get("score") or {})

    if score_block:
        data = (_deep_merge(data, score_block) if profile_wins
                else _deep_merge(score_block, data))

    patterns = profile.get("tracks") or []
    if patterns:
        rules = data.get("tracks") or []
        for rule in rules:
            name = (rule.get("original_name") or rule.get("part_name") or "").lower()
            for pat in patterns:
                if not fnmatch.fnmatch(name, str(pat.get("match", "*")).lower()):
                    continue
                for key, value in pat.items():
                    if key == "match":
                        continue
                    if not profile_wins and key in rule:
                        continue
                    if key == "keyswitch_map" and isinstance(value, dict):
                        merged = dict(rule.get("keyswitch_map") or {})
                        merged.update({int(k): v for k, v in value.items()})
                        rule["keyswitch_map"] = merged
                    else:
                        rule[key] = value
    return data


def merge(config_model: Any, profile: Dict[str, Any]) -> Any:
    """Profile applied *over* a detected config (no explicit --config)."""
    from config_schema import ScoreEngravingConfig

    return ScoreEngravingConfig(**apply(config_model.model_dump(), profile,
                                        profile_wins=True))


def merge_raw(raw_config: Dict[str, Any], profile: Dict[str, Any]) -> Any:
    """Profile applied *under* an explicit --config."""
    from config_schema import ScoreEngravingConfig

    return ScoreEngravingConfig(**apply(raw_config, profile, profile_wins=False))
