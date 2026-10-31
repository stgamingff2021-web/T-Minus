"""Embedding backends for the vector half of hybrid retrieval.

* ``fastembed`` (default when available): local ONNX model (BAAI/bge-small-en-v1.5),
  free, no API key. The model is downloaded once (pre-fetched in the Docker build).
* ``tfidf`` (fallback): character n-gram TF-IDF fitted per retrieval scope. Not
  semantic, but robust to spelling variants of place names, and needs no download.

The active backend is reported in every ``retrieve_plan`` result and in the trace,
so nobody mistakes keyword-ish similarity for semantic search.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from app.config import get_settings

log = logging.getLogger("tminus.embed")
_lock = threading.Lock()
_model = None
_model_error: str | None = None


def _load_fastembed():
    global _model, _model_error
    if _model is not None or _model_error is not None:
        return _model
    with _lock:
        if _model is not None or _model_error is not None:
            return _model
        try:
            from fastembed import TextEmbedding

            _model = TextEmbedding(model_name=get_settings().fastembed_model)
            list(_model.embed(["warm-up"]))
        except Exception as exc:  # noqa: BLE001 - any failure -> documented fallback
            _model_error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("fastembed unavailable, using TF-IDF fallback: %s", _model_error)
            _model = None
    return _model


def backend_name() -> str:
    pref = get_settings().embedding_backend.lower()
    if pref == "tfidf":
        return "tfidf"
    return "fastembed" if _load_fastembed() is not None else "tfidf"


def backend_error() -> str | None:
    return _model_error


def embed_texts(texts: list[str]) -> np.ndarray | None:
    """Dense, L2-normalised embeddings, or None when only the TF-IDF fallback exists."""
    if backend_name() != "fastembed":
        return None
    model = _load_fastembed()
    vecs = np.array(list(model.embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-12)


def to_bytes(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_bytes(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
