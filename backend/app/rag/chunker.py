"""Chunk plan pages by heading while keeping page numbers exact (spec §5.2, §11).

Rules:
* A chunk never crosses a page boundary, so every citation points at one page.
* The current section heading is carried forward across pages.
* Text is stored verbatim apart from whitespace normalisation (runs of spaces/tabs
  collapsed, trailing spaces stripped) — never paraphrased.
* Sections longer than ``max_chars`` are split at paragraph / sentence boundaries
  with a small overlap so retrieval does not cut a sentence in half.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NUMBERED = re.compile(r"^(\d{1,2}(\.\d{1,2}){0,3}\.?|[IVXLC]{1,6}\.|[A-H][.)]|Chapter\s+\d+|CHAPTER\s+[\dIVXLC]+|Annexure[\s-]*[\dIVXLC]+|ANNEXURE[\s-]*[\dIVXLC]+)\s+\S")
_WS = re.compile(r"[ \t   ]+")


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("­", "")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def is_heading(line: str) -> bool:
    s = line.strip()
    if not (3 <= len(s) <= 90) or s.endswith((".", ",", ";")) and not NUMBERED.match(s):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    if NUMBERED.match(s) and len(s.split()) <= 12:
        return True
    upper_share = sum(c.isupper() for c in letters) / len(letters)
    return upper_share > 0.85 and len(s.split()) <= 12


@dataclass
class Chunk:
    page: int
    section: str | None
    text: str
    index: int


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    paras = re.split(r"\n{2,}", text)
    buf = ""
    for p in paras:
        if len(p) > max_chars:
            sentences = re.split(r"(?<=[.!?;:])\s+", p)
        else:
            sentences = [p]
        for sent in sentences:
            while len(sent) > max_chars:  # pathological: no sentence boundaries (tables)
                parts.append((buf + "\n" + sent[:max_chars]).strip() if buf else sent[:max_chars])
                buf = ""
                sent = sent[max_chars - overlap:]
            candidate = (buf + ("\n\n" if buf else "") + sent) if buf else sent
            if len(candidate) > max_chars and buf:
                parts.append(buf.strip())
                tail = buf[-overlap:]
                cut = tail.find(" ")
                buf = (tail[cut + 1:] if cut >= 0 else tail) + " " + sent
            else:
                buf = candidate
    if buf.strip():
        parts.append(buf.strip())
    return parts


def chunk_pages(pages: list[tuple[int, str]], max_chars: int = 1400, min_chars: int = 40,
                overlap: int = 160) -> list[Chunk]:
    """pages = [(page_number (1-based), raw text)] -> chunks."""
    chunks: list[Chunk] = []
    section: str | None = None
    idx = 0
    for page_no, raw in pages:
        text = normalise(raw)
        if not text:
            continue
        blocks: list[tuple[str | None, list[str]]] = []
        cur: list[str] = []
        cur_section = section
        for line in text.split("\n"):
            if is_heading(line):
                if cur:
                    blocks.append((cur_section, cur))
                cur = [line]
                cur_section = line.strip()
            else:
                cur.append(line)
        if cur:
            blocks.append((cur_section, cur))
        section = cur_section
        merged: list[tuple[str | None, str]] = []
        for sec, lines in blocks:
            body = "\n".join(lines).strip()
            if not body:
                continue
            if merged and len(body) < min_chars * 3 and len(merged[-1][1]) + len(body) < max_chars:
                merged[-1] = (merged[-1][0], merged[-1][1] + "\n" + body)
            else:
                merged.append((sec, body))
        for sec, body in merged:
            for piece in _split_long(body, max_chars, overlap):
                if len(piece) < min_chars and chunks and chunks[-1].page == page_no:
                    chunks[-1].text += "\n" + piece
                    continue
                chunks.append(Chunk(page=page_no, section=sec, text=piece, index=idx))
                idx += 1
    return chunks
