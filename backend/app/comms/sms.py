"""SMS length and segment counting (spec §6.9, AC-10).

* GSM-7 (3GPP TS 23.038): 160 characters in one SMS, 153 per segment when
  concatenated. Characters from the extension table (^ { } \\ [ ~ ] | €) cost 2.
* UCS-2 (any character outside GSM-7, e.g. Tamil): 70 characters in one SMS, 67 per
  segment when concatenated. Characters outside the Basic Multilingual Plane (emoji)
  cost 2 UTF-16 code units.

English public messages must fit one GSM-7 SMS (<= 160); Tamil messages must fit two
UCS-2 segments (<= 134 characters).
"""
from __future__ import annotations

from dataclasses import dataclass

GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = set("^{}\\[~]|€\f")


@dataclass(frozen=True)
class SmsCount:
    encoding: str  # GSM-7 | UCS-2
    units: int  # characters as the network counts them
    segments: int
    per_segment: int
    single_limit: int
    non_gsm_chars: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"encoding": self.encoding, "units": self.units, "segments": self.segments,
                "per_segment": self.per_segment, "single_limit": self.single_limit,
                "non_gsm_chars": list(self.non_gsm_chars)}


def count(text: str) -> SmsCount:
    non_gsm = tuple(sorted({c for c in text if c not in GSM7_BASIC and c not in GSM7_EXT}))
    if not non_gsm:
        units = sum(2 if c in GSM7_EXT else 1 for c in text)
        single, per = 160, 153
        enc = "GSM-7"
    else:
        units = len(text.encode("utf-16-le")) // 2
        single, per = 70, 67
        enc = "UCS-2"
    if units == 0:
        segs = 0
    elif units <= single:
        segs = 1
    else:
        segs = -(-units // per)
    return SmsCount(enc, units, segs, per, single, non_gsm)


def check_english(text: str, limit: int = 160) -> list[str]:
    c = count(text)
    problems = []
    if not text.strip():
        problems.append("English message is empty.")
    if c.encoding != "GSM-7":
        problems.append(f"English message uses non-GSM characters {''.join(c.non_gsm_chars)!r}, which forces UCS-2 (70 chars).")
    if c.units > limit:
        problems.append(f"English message is {c.units} characters; the limit is {limit} (one SMS).")
    return problems


def check_tamil(text: str, limit: int = 134) -> list[str]:
    c = count(text)
    problems = []
    if not text.strip():
        problems.append("Tamil message is empty.")
    if not any("஀" <= ch <= "௿" for ch in text):
        problems.append("Tamil message contains no Tamil script.")
    if c.units > limit:
        problems.append(f"Tamil message is {c.units} characters; the limit is {limit} (two Unicode SMS segments).")
    return problems
