"""Tokenisation shared by keyword search, coverage scoring and the verifier."""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset(
    """a an and are as at be been by can do for from has have if in into is it its of on or such that the their
    then there these they this to was were will with within which who whom whose shall should would may must
    all any each other than also not no nor so very per via etc i ii iii iv v vi""".split()
)
_TOKEN = re.compile(r"[a-z0-9஀-௿]+")


def stem(w: str) -> str:
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and not w.endswith("ses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    if len(w) > 5 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 6 and w.endswith("ion"):
        return w[:-3]
    return w


def tokens(text: str, keep_stopwords: bool = False) -> list[str]:
    t = unicodedata.normalize("NFKC", text).lower()
    out = []
    for w in _TOKEN.findall(t):
        if not keep_stopwords and (w in STOPWORDS or len(w) < 2):
            continue
        out.append(stem(w))
    return out


def norm_ws(text: str) -> str:
    """Whitespace-insensitive form used by the verbatim-quote check."""
    t = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", t).strip()


NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)(?![\w])")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUMBER_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out
