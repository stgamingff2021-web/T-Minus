"""Hybrid retrieval over plan chunks: BM25 keywords + vectors, fused with RRF (spec §6.7, §15).

Why hybrid: embeddings capture meaning ("relief camp" ~ "temporary shelter"), while
keyword search catches place and department names that embeddings miss
("Velachery", "PWD (WRD)").

Pipeline for ``search(scope, query, k)``:
  1. BM25 over the scope's chunks (own implementation, k1=1.5, b=0.75).
  2. Cosine similarity of query vs. chunk vectors (fastembed, or TF-IDF fallback).
  3. Reciprocal Rank Fusion: score = sum over rankers of 1 / (60 + rank).
  4. Relevance gate — a chunk counts as found only if it covers enough of the query's
     content words OR its vector similarity is high enough; otherwise ``not_found``
     (the agent then says "Not found in district plan" instead of guessing).

Scopes: ``district:<id>`` (the district's DDMP + city plans) and ``generic`` (NDMA
guidelines, always labelled generic). Indexes are built lazily and invalidated on ingest.
"""
from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select

from app.config import get_config
from app.db.models import PlanChunk, PlanDocument
from app.db.session import session_scope
from app.rag import embed
from app.rag.text import tokens

_lock = threading.Lock()
_indexes: dict[str, "ScopeIndex"] = {}


@dataclass
class ScopeIndex:
    scope: str
    ids: list[str]
    texts: list[str]
    meta: list[dict]
    toks: list[list[str]]
    df: Counter
    avgdl: float
    backend: str
    vectors: np.ndarray | None = None
    tfidf: object | None = None
    tfidf_matrix: object | None = None
    tf: list[Counter] = field(default_factory=list)


def invalidate(scope: str | None = None) -> None:
    with _lock:
        if scope is None:
            _indexes.clear()
        else:
            _indexes.pop(scope, None)


def scope_for_district(district_id: str) -> str:
    return f"district:{district_id}"


def _load_rows(scope: str) -> list[tuple[PlanChunk, PlanDocument]]:
    with session_scope() as s:
        q = select(PlanChunk, PlanDocument).join(PlanDocument, PlanChunk.document_id == PlanDocument.id)
        if scope == "generic":
            q = q.where(PlanDocument.kind == "generic")
        else:
            q = q.where(PlanDocument.district_id == scope.split(":", 1)[1], PlanDocument.kind != "generic")
        return list(s.execute(q.order_by(PlanChunk.document_id, PlanChunk.page, PlanChunk.id)).all())


def build_index(scope: str) -> ScopeIndex:
    rows = _load_rows(scope)
    ids = [c.id for c, _ in rows]
    texts = [c.text for c, _ in rows]
    meta = [{
        "chunk_id": c.id, "document_id": d.id, "document_title": d.title, "edition_year": d.edition_year,
        "source_url": d.source_url, "sha256": d.sha256, "page": c.page, "section": c.section, "kind": d.kind,
        "ocr": c.ocr,
    } for c, d in rows]
    toks = [tokens(t + " " + (m["section"] or "")) for t, m in zip(texts, meta)]
    df: Counter = Counter()
    for t in toks:
        df.update(set(t))
    avgdl = (sum(len(t) for t in toks) / len(toks)) if toks else 0.0
    backend = embed.backend_name()
    idx = ScopeIndex(scope, ids, texts, meta, toks, df, avgdl, backend, tf=[Counter(t) for t in toks])
    if backend == "fastembed" and rows and all(c.embedding for c, _ in rows):
        idx.vectors = np.vstack([embed.from_bytes(c.embedding) for c, _ in rows])
    else:
        idx.backend = "tfidf"
        if texts:
            from sklearn.feature_extraction.text import TfidfVectorizer

            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True,
                                  max_features=200_000)
            idx.tfidf_matrix = vec.fit_transform([t.lower() for t in texts])
            idx.tfidf = vec
    return idx


def get_index(scope: str) -> ScopeIndex:
    with _lock:
        if scope not in _indexes:
            _indexes[scope] = build_index(scope)
        return _indexes[scope]


def bm25_scores(idx: ScopeIndex, q_toks: list[str], k1: float = 1.5, b: float = 0.75) -> np.ndarray:
    n = len(idx.ids)
    scores = np.zeros(n)
    if not n or not q_toks:
        return scores
    for term in set(q_toks):
        df = idx.df.get(term, 0)
        if df == 0:
            continue
        idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
        for i, tf in enumerate(idx.tf):
            f = tf.get(term, 0)
            if f:
                dl = len(idx.toks[i])
                scores[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / (idx.avgdl or 1)))
    return scores


def cosine_scores(idx: ScopeIndex, query: str) -> np.ndarray:
    if not idx.ids:
        return np.zeros(0)
    if idx.vectors is not None:
        qv = embed.embed_texts([query])
        if qv is not None:
            return idx.vectors @ qv[0]
    if idx.tfidf is not None:
        qm = idx.tfidf.transform([query.lower()])
        return (idx.tfidf_matrix @ qm.T).toarray().ravel()
    return np.zeros(len(idx.ids))


def coverage(q_toks: list[str], doc_toks: list[str]) -> float:
    q = set(q_toks)
    if not q:
        return 0.0
    return len(q & set(doc_toks)) / len(q)


@dataclass
class SearchResult:
    found: bool
    chunks: list[dict]
    backend: str
    reason: str | None = None
    scope: str = ""
    n_chunks_in_scope: int = 0

    def as_dict(self) -> dict:
        return {"found": self.found, "chunks": self.chunks, "backend": self.backend, "reason": self.reason,
                "scope": self.scope, "n_chunks_in_scope": self.n_chunks_in_scope}


def search(scope: str, query: str, k: int = 6) -> SearchResult:
    agent_cfg = get_config().hazards.get("agent", {})
    idx = get_index(scope)
    if not idx.ids:
        return SearchResult(False, [], idx.backend, reason="no documents ingested for this scope", scope=scope)
    q_toks = tokens(query)
    bm = bm25_scores(idx, q_toks)
    cos = cosine_scores(idx, query)
    rrf = np.zeros(len(idx.ids))
    for scores in (bm, cos):
        order = np.argsort(-scores, kind="stable")
        for rank_pos, i in enumerate(order[:200]):
            if scores[i] > 0:
                rrf[i] += 1.0 / (60 + rank_pos + 1)
    min_cov = float(agent_cfg.get("retrieval_min_coverage", 0.34))
    min_cos = float(agent_cfg.get("retrieval_min_cosine" if idx.backend == "fastembed" else "retrieval_min_cosine_tfidf",
                                  0.45 if idx.backend == "fastembed" else 0.12))
    order = np.argsort(-rrf, kind="stable")
    out = []
    for i in order:
        if rrf[i] <= 0:
            break
        cov = coverage(q_toks, idx.toks[i])
        if cov < min_cov and cos[i] < min_cos:
            continue
        out.append({
            **idx.meta[i],
            "text": idx.texts[i],
            "score": round(float(rrf[i]), 5),
            "bm25": round(float(bm[i]), 3),
            "cosine": round(float(cos[i]), 3),
            "coverage": round(cov, 2),
        })
        if len(out) >= k:
            break
    if not out:
        return SearchResult(False, [], idx.backend, reason="no chunk passed the relevance gate", scope=scope,
                            n_chunks_in_scope=len(idx.ids))
    return SearchResult(True, out, idx.backend, scope=scope, n_chunks_in_scope=len(idx.ids))


def get_chunk(chunk_id: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(select(PlanChunk, PlanDocument).join(PlanDocument, PlanChunk.document_id == PlanDocument.id)
                        .where(PlanChunk.id == chunk_id)).first()
        if not row:
            return None
        c, d = row
        return {"chunk_id": c.id, "document_id": d.id, "document_title": d.title, "edition_year": d.edition_year,
                "source_url": d.source_url, "page": c.page, "section": c.section, "text": c.text, "kind": d.kind,
                "district_id": d.district_id, "ocr": c.ocr}


def district_has_plan(district_id: str) -> bool:
    with session_scope() as s:
        return s.execute(select(PlanDocument.id).where(PlanDocument.district_id == district_id,
                                                       PlanDocument.kind != "generic").limit(1)).first() is not None


def has_generic() -> bool:
    with session_scope() as s:
        return s.execute(select(PlanDocument.id).where(PlanDocument.kind == "generic").limit(1)).first() is not None
