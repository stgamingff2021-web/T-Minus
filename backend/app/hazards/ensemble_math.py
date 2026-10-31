"""The "51 futures" maths (spec §6.2, §6.3). Pure functions, no I/O, unit-tested.

Intuition
---------
Weather is chaotic, so ECMWF runs its model 51 times from slightly perturbed
starting states. Each run ("member") is one plausible future. The probability of an
event is simply the share of futures in which it happens.

Definitions (member m, hourly precipitation p_m(t))
---------------------------------------------------
* Rolling 24-h accumulation:   R_m(t) = p_m(t-23) + ... + p_m(t)
* Member m exceeds X within W:  max_{t in W} R_m(t) >= X
* Probability:                  P(X, W) = (#valid members that exceed) / (#valid members)

Missing data is never treated as zero: a member with any missing value in the window
is excluded from both numerator and denominator, and N (the number of valid members)
is always reported next to the probability ("74.5% (38 of 51 forecasts)").

Lead time
---------
* t*  (expected impact): the hour at which the share of members with R >= X is highest
  (the hour the futures agree on most). Ties resolve to the earliest hour (cautious).
* Earliest plausible onset: the first hour at which at least 10% of members exceed X.
* T-minus = t* - now (hours).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

HOUR = 3600


@dataclass(frozen=True)
class Exceedance:
    """Result of P(X, W) for one threshold and one window."""

    threshold: float
    within_h: float
    n_exceed: int
    n_valid: int
    n_members: int  # members present in the response
    window_start: int | None  # unix seconds
    window_end: int | None
    truncated: bool  # window extends past the model horizon

    @property
    def probability(self) -> float | None:
        return None if self.n_valid == 0 else self.n_exceed / self.n_valid

    def as_dict(self) -> dict:
        p = self.probability
        return {
            "threshold_mm": self.threshold,
            "within_h": self.within_h,
            "n_exceed": self.n_exceed,
            "n_valid": self.n_valid,
            "n_members": self.n_members,
            "probability": None if p is None else round(p, 6),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "truncated": self.truncated,
        }


def floor_hour(ts: float) -> int:
    return int(ts // HOUR * HOUR)


def rolling_accumulation(members: np.ndarray, window_h: int = 24) -> np.ndarray:
    """Rolling ``window_h``-hour sums per member.

    ``members`` is (n_members, n_hours) of hourly precipitation with NaN for missing.
    Returns an array of the same shape where column t holds the sum of the
    ``window_h`` hours ending at t (inclusive). A sum is NaN when any of its hours is
    missing or when fewer than ``window_h`` hours of history exist.
    """
    m = np.asarray(members, dtype=float)
    if m.ndim != 2:
        raise ValueError("members must be 2-D (n_members, n_hours)")
    n_mem, n_hours = m.shape
    out = np.full((n_mem, n_hours), np.nan)
    if n_hours < window_h:
        return out
    missing = np.isnan(m)
    filled = np.where(missing, 0.0, m)
    # Prefix sums with a leading zero column so that sum(t-w+1..t) = cs[t+1] - cs[t+1-w].
    cs = np.concatenate([np.zeros((n_mem, 1)), np.cumsum(filled, axis=1)], axis=1)
    cmiss = np.concatenate([np.zeros((n_mem, 1)), np.cumsum(missing, axis=1)], axis=1)
    t = np.arange(window_h - 1, n_hours)
    sums = cs[:, t + 1] - cs[:, t + 1 - window_h]
    nmiss = cmiss[:, t + 1] - cmiss[:, t + 1 - window_h]
    sums[nmiss > 0] = np.nan
    # Guard tiny negative values from floating-point cancellation.
    sums = np.where(np.isnan(sums), np.nan, np.maximum(sums, 0.0))
    out[:, t] = sums
    return out


def horizon_index(acc: np.ndarray) -> int | None:
    """Index of the last hour at which any member has a valid accumulation."""
    valid_cols = np.where(~np.all(np.isnan(acc), axis=0))[0]
    return int(valid_cols[-1]) if valid_cols.size else None


def _window_indices(times: np.ndarray, now: float, within_h: float) -> np.ndarray:
    start = floor_hour(now)
    end = start + within_h * HOUR
    return np.where((times >= start) & (times <= end))[0]


def exceedance(
    acc: np.ndarray, times: np.ndarray, threshold: float, now: float, within_h: float
) -> Exceedance:
    """P(X, W): share of valid members whose max accumulation in W reaches X."""
    times = np.asarray(times)
    n_mem = acc.shape[0]
    idx = _window_indices(times, now, within_h)
    hz = horizon_index(acc)
    if idx.size == 0 or hz is None:
        return Exceedance(threshold, within_h, 0, 0, n_mem, None, None, True)
    # The window is truncated when the model's last valid hour comes before its end.
    truncated = bool(times[hz] < floor_hour(now) + within_h * HOUR)
    idx = idx[idx <= hz]
    if idx.size == 0:
        return Exceedance(threshold, within_h, 0, 0, n_mem, None, None, True)
    window = acc[:, idx]
    valid = ~np.any(np.isnan(window), axis=1)
    exceed = valid & (np.nanmax(np.where(np.isnan(window), -np.inf, window), axis=1) >= threshold)
    return Exceedance(
        threshold=threshold,
        within_h=within_h,
        n_exceed=int(exceed.sum()),
        n_valid=int(valid.sum()),
        n_members=n_mem,
        window_start=int(times[idx[0]]),
        window_end=int(times[idx[-1]]),
        truncated=bool(truncated),
    )


def share_series(acc: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-hour (share, n_exceed, n_valid) of members with R >= X.

    Members with a missing accumulation at hour t are excluded at that hour.
    Share is NaN where no member is valid.
    """
    valid = ~np.isnan(acc)
    n_valid = valid.sum(axis=0)
    n_exceed = (valid & (np.where(valid, acc, -np.inf) >= threshold)).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(n_valid > 0, n_exceed / np.maximum(n_valid, 1), np.nan)
    return share, n_exceed, n_valid


@dataclass(frozen=True)
class LeadTime:
    threshold: float
    t_star: int | None  # unix seconds
    t_star_share: float | None
    t_star_n_exceed: int | None
    t_star_n_valid: int | None
    onset: int | None
    onset_share: float | None
    t_minus_h: float | None
    onset_h: float | None

    def as_dict(self) -> dict:
        return {
            "threshold_mm": self.threshold,
            "t_star": self.t_star,
            "t_star_share": None if self.t_star_share is None else round(self.t_star_share, 6),
            "t_star_n_exceed": self.t_star_n_exceed,
            "t_star_n_valid": self.t_star_n_valid,
            "onset": self.onset,
            "onset_share": None if self.onset_share is None else round(self.onset_share, 6),
            "t_minus_h": self.t_minus_h,
            "onset_h": self.onset_h,
        }


def lead_time(
    acc: np.ndarray,
    times: np.ndarray,
    threshold: float,
    now: float,
    horizon_h: float,
    onset_min_share: float = 0.10,
) -> LeadTime:
    """Expected impact time t*, T-minus and earliest plausible onset for threshold X."""
    times = np.asarray(times)
    idx = _window_indices(times, now, horizon_h)
    hz = horizon_index(acc)
    if hz is not None:
        idx = idx[idx <= hz]
    if idx.size == 0:
        return LeadTime(threshold, None, None, None, None, None, None, None, None)
    share, n_ex, n_val = share_series(acc[:, idx], threshold)
    share_filled = np.where(np.isnan(share), -1.0, share)
    best = int(np.argmax(share_filled))  # argmax returns the earliest maximum
    t_star = t_share = t_ne = t_nv = None
    t_minus = None
    if share_filled[best] > 0:
        t_star = int(times[idx[best]])
        t_share = float(share[best])
        t_ne, t_nv = int(n_ex[best]), int(n_val[best])
        t_minus = round((t_star - now) / HOUR, 1)
    onset = onset_share = onset_h = None
    hits = np.where(share_filled >= onset_min_share)[0]
    if hits.size and share_filled[hits[0]] > 0:
        onset = int(times[idx[hits[0]]])
        onset_share = float(share[hits[0]])
        onset_h = round((onset - now) / HOUR, 1)
    return LeadTime(threshold, t_star, t_share, t_ne, t_nv, onset, onset_share, t_minus, onset_h)


def accumulate_past(values: np.ndarray, times: np.ndarray, now: float, hours: int) -> tuple[float | None, int]:
    """Sum of a single deterministic hourly series over the ``hours`` before ``now``.

    Used for antecedent rainfall (Forecast API with past_days). Returns (sum, n_hours)
    or (None, n) if any hour in the window is missing — never treats missing as zero.
    """
    times = np.asarray(times)
    vals = np.asarray(values, dtype=float)
    end = floor_hour(now)
    sel = (times > end - hours * HOUR) & (times <= end)
    n = int(sel.sum())
    if n < hours or np.any(np.isnan(vals[sel])):
        return None, n
    return float(vals[sel].sum()), n
