"""Level ordering and probability-rule evaluation (spec §5.4, §6.5 step 1).

Levels are ordered Quiet (None) < WATCH < PREPARE < ACT. A level fires when ANY of
its rules is met; the base level is the highest level that fires.

Rule (rain):  P(24-h total >= category threshold within the next `within_h` hours) >= min_prob

``margin`` lowers every min_prob; evaluating with the stand-down margin yields the
*hysteresis level*: the highest level still supported "with room to spare". A trigger
only steps down when its current level is no longer supported even with the margin.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

LEVELS: tuple[str, ...] = ("WATCH", "PREPARE", "ACT")
_RANK = {None: 0, "QUIET": 0, "WATCH": 1, "PREPARE": 2, "ACT": 3}


def rank(level: str | None) -> int:
    if level not in _RANK:
        raise ValueError(f"unknown level {level!r}")
    return _RANK[level]


def from_rank(r: int) -> str | None:
    r = max(0, min(3, int(r)))
    return None if r == 0 else LEVELS[r - 1]


def max_level(*levels: str | None) -> str | None:
    return from_rank(max((rank(lv) for lv in levels), default=0))


def prob_key(category: str, within_h: float) -> str:
    """Stable key for a (category, window) probability, e.g. ``very_heavy@72h``."""
    return f"{category}@{int(within_h)}h"


def rain_rules(levels_cfg: Mapping[str, Iterable[Mapping[str, Any]]]) -> list[tuple[str, dict]]:
    """Flatten the YAML rules into (level, rule) pairs, highest level first."""
    out: list[tuple[str, dict]] = []
    for level in reversed(LEVELS):
        for rule in levels_cfg.get(level, []) or []:
            out.append((level, dict(rule)))
    return out


def required_windows(levels_cfg: Mapping[str, Iterable[Mapping[str, Any]]]) -> set[tuple[str, float]]:
    """Every (category, within_h) pair any rule needs a probability for."""
    return {(r["category"], float(r["within_h"])) for _, r in rain_rules(levels_cfg)}


def evaluate_rain_rules(
    probabilities: Mapping[str, float | None],
    levels_cfg: Mapping[str, Iterable[Mapping[str, Any]]],
    margin: float = 0.0,
) -> tuple[str | None, list[dict]]:
    """Return (base level, list of rules met at that level).

    ``probabilities`` maps ``prob_key(category, within_h)`` to P (or None when there
    is insufficient data — a None never satisfies a rule).
    """
    for level in reversed(LEVELS):
        met = []
        for rule in levels_cfg.get(level, []) or []:
            p = probabilities.get(prob_key(rule["category"], rule["within_h"]))
            if p is not None and p >= float(rule["min_prob"]) - margin - 1e-12:
                met.append({**rule, "level": level, "probability": p, "margin": margin})
        if met:
            return level, met
    return None, []


def evaluate_river_rules(
    stats_exceed: Mapping[str, bool | None], levels_cfg: Mapping[str, Mapping[str, Any]]
) -> tuple[str | None, list[str]]:
    """River base level. ``stats_exceed`` maps a level name to whether its rule holds."""
    for level in reversed(LEVELS):
        if stats_exceed.get(level):
            return level, [level]
    return None, []
