"""Unit tests for the 51-futures maths (§6.2–6.3).

Small inline arrays only (allowed by §10.2); nothing here ships in application code.
"""
import numpy as np
import pytest

from app.hazards.ensemble_math import (
    HOUR,
    accumulate_past,
    exceedance,
    lead_time,
    rolling_accumulation,
    share_series,
)

T0 = 1_800_000_000 // HOUR * HOUR  # an arbitrary whole hour


def hours(n, start=T0):
    return np.arange(n) * HOUR + start


def test_rolling_accumulation_simple_window():
    m = np.array([[1.0] * 30])
    acc = rolling_accumulation(m, 24)
    assert np.isnan(acc[0, :23]).all()  # not enough history
    assert acc[0, 23] == pytest.approx(24.0)
    assert acc[0, 29] == pytest.approx(24.0)


def test_rolling_accumulation_missing_is_never_zero():
    row = [2.0] * 30
    row[10] = np.nan
    acc = rolling_accumulation(np.array([row]), 24)
    # every window containing hour 10 (t = 23..33 clipped to 29) is missing
    assert np.isnan(acc[0, 23:30]).all()


def test_rolling_accumulation_window_values():
    row = list(range(1, 31))  # 1..30
    acc = rolling_accumulation(np.array([row], dtype=float), 24)
    assert acc[0, 23] == pytest.approx(sum(range(1, 25)))
    assert acc[0, 29] == pytest.approx(sum(range(7, 31)))


def test_probability_worked_example_38_of_51():
    """Spec §6.2 worked example: 38 of 51 members exceed 115.6 mm within 48 h -> 74.5%."""
    n_hours = 24 + 48
    members = np.zeros((51, n_hours))
    # 38 members: 6 mm/h for 24 h somewhere inside the window = 144 mm >= 115.6
    members[:38, 30:54] = 6.0
    # 13 members: 4 mm/h for 24 h = 96 mm < 115.6
    members[38:, 30:54] = 4.0
    times = hours(n_hours)
    now = times[23]
    acc = rolling_accumulation(members, 24)
    ex = exceedance(acc, times, 115.6, now, 48)
    assert ex.n_valid == 51
    assert ex.n_exceed == 38
    assert ex.probability == pytest.approx(38 / 51)
    assert round(ex.probability * 100, 1) == 74.5


def test_members_with_missing_values_are_excluded_from_both_sides():
    n_hours = 24 + 48
    members = np.zeros((10, n_hours))
    members[:6, 30:54] = 6.0  # 6 exceed
    members[6, 40] = np.nan  # an exceeding-sized member? no: a dry member with a gap
    members[0, 45] = np.nan  # one exceeding member also has a gap -> invalid
    times = hours(n_hours)
    acc = rolling_accumulation(members, 24)
    ex = exceedance(acc, times, 115.6, times[23], 48)
    assert ex.n_valid == 8
    assert ex.n_exceed == 5
    assert ex.probability == pytest.approx(5 / 8)


def test_window_is_part_of_the_trigger():
    n_hours = 24 + 120
    members = np.zeros((4, n_hours))
    members[:, 100:124] = 6.0  # event ~ 77-100 h after now
    times = hours(n_hours)
    now = times[23]
    acc = rolling_accumulation(members, 24)
    assert exceedance(acc, times, 115.6, now, 48).probability == 0.0
    assert exceedance(acc, times, 115.6, now, 120).probability == 1.0


def test_window_truncated_at_model_horizon():
    n_hours = 60
    members = np.ones((3, n_hours))
    members[:, 50:] = np.nan  # model horizon ends
    times = hours(n_hours)
    acc = rolling_accumulation(members, 24)
    ex = exceedance(acc, times, 10, times[23], 72)
    assert ex.truncated is True
    assert ex.n_valid == 3


def test_no_valid_members_gives_none():
    members = np.full((5, 40), np.nan)
    times = hours(40)
    acc = rolling_accumulation(members, 24)
    ex = exceedance(acc, times, 10, times[23], 12)
    assert ex.probability is None


def test_lead_time_t_star_is_hour_of_max_agreement_and_onset_10pct():
    n_hours = 24 + 72
    members = np.zeros((10, n_hours))
    # member 0 crosses early, members 0..7 all overlap around hour 60
    members[0, 30:54] = 6.0
    members[1:8, 40:64] = 6.0
    times = hours(n_hours)
    now = times[23]
    acc = rolling_accumulation(members, 24)
    lt = lead_time(acc, times, 115.6, now, 72, onset_min_share=0.10)
    share, _, _ = share_series(acc, 115.6)
    assert lt.t_star is not None
    best_idx = int(np.nanargmax(share))
    assert lt.t_star == times[best_idx]
    # onset = first hour where >= 10% (1 of 10) members exceed
    first = int(np.where(share >= 0.1)[0][0])
    assert lt.onset == times[first]
    assert lt.onset <= lt.t_star
    assert lt.t_minus_h == pytest.approx((lt.t_star - now) / HOUR, abs=0.05)


def test_lead_time_none_when_no_member_exceeds():
    members = np.zeros((5, 60))
    times = hours(60)
    acc = rolling_accumulation(members, 24)
    lt = lead_time(acc, times, 64.5, times[23], 36)
    assert lt.t_star is None and lt.onset is None and lt.t_minus_h is None


def test_accumulate_past_requires_complete_window():
    times = hours(80)
    vals = np.full(80, 2.0)
    total, n = accumulate_past(vals, times, times[79], 72)
    assert n == 72 and total == pytest.approx(144.0)
    vals[50] = np.nan
    total, _ = accumulate_past(vals, times, times[79], 72)
    assert total is None
