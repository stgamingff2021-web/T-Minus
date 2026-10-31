"""Level resolution: official floors, escalation, cap, disagreement (spec §6.5).

Order matters:
  1. Base level per hazard from the probability rules.
  2. Official floor: orange alert => at least PREPARE; red => ACT.
  3. Escalation, at most +1 level in total:
       * saturated ground — rain over the past 72 h >= antecedent threshold
         (wet soil is a full sponge: new rain runs off instead of soaking in);
       * compound hazard — rain >= PREPARE and river >= WATCH in the same district.
     Escalation only amplifies an existing signal: it never lifts Quiet to WATCH.
  4. Cap at ACT.
  5. Disagreement flag: official alert and ensemble differ by >= 2 levels — keep the
     more cautious level (already guaranteed by the max in step 2) and flag it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from app.triggers.levels import from_rank, max_level, rank

# For the disagreement comparison only, a yellow official alert counts as WATCH.
# (Yellow is NOT a floor — only orange/red are, per config.)
OFFICIAL_COMPARE = {"yellow": "WATCH", "orange": "PREPARE", "red": "ACT"}


@dataclass
class Resolution:
    base_level: str | None
    official_color: str | None
    official_level: str | None  # level the official alert maps to (for comparison)
    floor_level: str | None  # level enforced by the floor (orange/red only)
    level: str | None  # final resolved level
    escalations: list[str] = field(default_factory=list)
    disagreement: bool = False
    official_available: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "base_level": self.base_level,
            "official_color": self.official_color,
            "official_level": self.official_level,
            "floor_level": self.floor_level,
            "level": self.level,
            "escalations": list(self.escalations),
            "disagreement": self.disagreement,
            "official_available": self.official_available,
            "notes": list(self.notes),
        }


def resolve_level(
    base_level: str | None,
    *,
    official_color: str | None,
    floor_cfg: Mapping[str, str],
    official_available: bool = True,
    saturated_ground: bool = False,
    compound: bool = False,
    max_escalation: int = 1,
) -> Resolution:
    """Apply steps 2–5 to a base level. Pure function."""
    notes: list[str] = []
    color = official_color.lower() if official_color else None
    floor_level = floor_cfg.get(color) if color else None
    level = max_level(base_level, floor_level)
    if floor_level and rank(floor_level) > rank(base_level):
        notes.append(f"Official {color} alert raises the level to at least {floor_level}.")

    escalations: list[str] = []
    if rank(level) >= 1 and max_escalation > 0:
        if saturated_ground:
            escalations.append("saturated_ground")
        if compound:
            escalations.append("compound_with_river")
        if escalations:
            before = level
            level = from_rank(min(3, rank(level) + max_escalation))  # +1 in total, however many reasons
            if level != before:
                notes.append(f"Escalated {before} -> {level} ({', '.join(escalations)}); at most +1 level.")
            else:
                notes.append(f"Escalation conditions present ({', '.join(escalations)}) but already at ACT (cap).")

    official_level = OFFICIAL_COMPARE.get(color) if color else None
    disagreement = False
    if official_available:
        if abs(rank(official_level) - rank(base_level)) >= 2:
            disagreement = True
            notes.append(
                f"Sources disagree: official alert ~{official_level or 'none'} vs ensemble {base_level or 'Quiet'}; "
                "keeping the more cautious level."
            )
    else:
        notes.append("Official alerts unavailable; resolved on the ensemble alone.")

    return Resolution(
        base_level=base_level,
        official_color=color,
        official_level=official_level,
        floor_level=floor_level,
        level=level,
        escalations=escalations,
        disagreement=disagreement,
        official_available=official_available,
        notes=notes,
    )
