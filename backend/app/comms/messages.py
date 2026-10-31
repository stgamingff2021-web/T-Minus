"""Public-message rules computed by code (spec §6.9).

Probability wording, always shown with the number:
  >= 70%  "very likely";  > 50%  "likely";  30–50%  "possible";  < 30%  no public message.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.config import get_config
from app.rag.text import numbers_in

TAMIL_WORDING = {"very likely": "மிக அதிக வாய்ப்பு", "likely": "அதிக வாய்ப்பு", "possible": "வாய்ப்பு உள்ளது"}


def pct_int(p: float) -> int:
    """Round-half-up integer percentage for public messages (74.5 -> 75)."""
    return int(Decimal(str(p * 100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def probability_wording(p: float | None) -> str | None:
    if p is None:
        return None
    for band in get_config().hazards.get("public_message", {}).get("wording", []):
        lo = float(band["min_prob"])
        if (p >= lo) if band.get("inclusive", True) else (p > lo):
            return band["phrase"]
    return None


def wording_with_number(p: float) -> str:
    phrase = probability_wording(p)
    return f"{phrase} ({pct_int(p)}%)" if phrase else ""


def message_mentions_probability(text: str, p: float) -> bool:
    target = p * 100
    return any(abs(x - target) <= 0.5 + 1e-9 for x in numbers_in(text))
