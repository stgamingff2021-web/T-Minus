"""Unit tests for level rules, resolution (floors/escalation/cap) and the lifecycle (§6.4–6.6)."""
import datetime as dt

from app.triggers.escalation import resolve_level
from app.triggers.levels import evaluate_rain_rules, max_level, prob_key, rank
from app.triggers.lifecycle import Observation, TriggerSnapshot, same_event, step

LEVELS_CFG = {
    "WATCH": [{"category": "very_heavy", "min_prob": 0.30, "within_h": 120}],
    "PREPARE": [{"category": "very_heavy", "min_prob": 0.50, "within_h": 72}],
    "ACT": [
        {"category": "very_heavy", "min_prob": 0.70, "within_h": 48},
        {"category": "extremely_heavy", "min_prob": 0.40, "within_h": 72},
    ],
}
FLOOR = {"orange": "PREPARE", "red": "ACT"}


def probs(vh120=0.0, vh72=0.0, vh48=0.0, eh72=0.0):
    return {
        prob_key("very_heavy", 120): vh120,
        prob_key("very_heavy", 72): vh72,
        prob_key("very_heavy", 48): vh48,
        prob_key("extremely_heavy", 72): eh72,
    }


# ---------------- base levels ----------------

def test_worked_example_meets_act():
    level, met = evaluate_rain_rules(probs(0.9, 0.8, 38 / 51, 0.1), LEVELS_CFG)
    assert level == "ACT" and met[0]["category"] == "very_heavy"


def test_extremely_heavy_alone_can_fire_act():
    level, _ = evaluate_rain_rules(probs(0.35, 0.45, 0.2, 0.41), LEVELS_CFG)
    assert level == "ACT"


def test_levels_and_quiet():
    assert evaluate_rain_rules(probs(0.31), LEVELS_CFG)[0] == "WATCH"
    assert evaluate_rain_rules(probs(0.6, 0.5), LEVELS_CFG)[0] == "PREPARE"
    assert evaluate_rain_rules(probs(0.29), LEVELS_CFG)[0] is None


def test_missing_probability_never_fires():
    assert evaluate_rain_rules({prob_key("very_heavy", 120): None}, LEVELS_CFG)[0] is None


def test_margin_lowers_thresholds_for_hysteresis():
    p = probs(0.5, 0.40, 0.3)  # PREPARE not met (0.40 < 0.50) but met with margin (>= 0.35)
    assert evaluate_rain_rules(p, LEVELS_CFG)[0] == "WATCH"
    assert evaluate_rain_rules(p, LEVELS_CFG, margin=0.15)[0] == "PREPARE"


def test_rank_and_max():
    assert rank(None) == 0 and rank("ACT") == 3
    assert max_level(None, "WATCH", "PREPARE") == "PREPARE"


# ---------------- resolution ----------------

def test_official_floor_orange_and_red():
    assert resolve_level(None, official_color="orange", floor_cfg=FLOOR).level == "PREPARE"
    assert resolve_level("WATCH", official_color="red", floor_cfg=FLOOR).level == "ACT"
    assert resolve_level("WATCH", official_color="yellow", floor_cfg=FLOOR).level == "WATCH"


def test_escalation_is_capped_at_plus_one_in_total():
    r = resolve_level("WATCH", official_color=None, floor_cfg=FLOOR, saturated_ground=True, compound=True)
    assert r.level == "PREPARE"
    assert set(r.escalations) == {"saturated_ground", "compound_with_river"}


def test_escalation_never_lifts_quiet():
    r = resolve_level(None, official_color=None, floor_cfg=FLOOR, saturated_ground=True)
    assert r.level is None


def test_cap_at_act():
    r = resolve_level("ACT", official_color="red", floor_cfg=FLOOR, saturated_ground=True)
    assert r.level == "ACT"


def test_disagreement_flag_two_levels():
    r = resolve_level(None, official_color="red", floor_cfg=FLOOR)
    assert r.disagreement and r.level == "ACT"
    r2 = resolve_level("ACT", official_color=None, floor_cfg=FLOOR)
    assert r2.disagreement and r2.level == "ACT"
    r3 = resolve_level("PREPARE", official_color="orange", floor_cfg=FLOOR)
    assert not r3.disagreement


def test_no_disagreement_when_official_unavailable():
    r = resolve_level("ACT", official_color=None, floor_cfg=FLOOR, official_available=False)
    assert not r.disagreement and r.level == "ACT"


# ---------------- lifecycle ----------------

def snap(level="PREPARE", status="active", below=None):
    return TriggerSnapshot(id="t1", level=level, status=status, below_runs=below or [])


def test_open_on_first_crossing():
    d = step(None, Observation("PREPARE", "PREPARE", "run1"))
    assert d.action == "open" and d.cap_msg_type == "Alert"


def test_no_new_trigger_on_stale_data():
    d = step(None, Observation("ACT", "ACT", "run1", can_open=False))
    assert d.action == "hold"


def test_same_level_next_scan_is_not_a_duplicate():
    d = step(snap("PREPARE"), Observation("PREPARE", "PREPARE", "run1"))
    assert d.action == "none"


def test_upgrade_is_immediate_update():
    d = step(snap("PREPARE"), Observation("ACT", "ACT", "run2"))
    assert d.action == "upgrade" and d.level == "ACT" and d.cap_msg_type == "Update"


def test_hysteresis_counts_new_model_runs_not_scans():
    t = snap("PREPARE")
    d1 = step(t, Observation(None, None, "run7"))
    assert d1.action == "count_below" and d1.below_runs == ["run7"]
    t.below_runs = d1.below_runs
    # re-scan of the SAME model run must not count twice
    d2 = step(t, Observation(None, None, "run7"))
    assert d2.action == "none"
    d3 = step(t, Observation(None, None, "run8"))
    assert d3.action == "stand_down" and d3.status == "stand_down_recommended"


def test_stand_down_is_never_an_automatic_close_for_prepare_or_act():
    t = snap("ACT", below=["r1"])
    d = step(t, Observation(None, None, "r2"))
    assert d.status == "stand_down_recommended"
    assert d.action != "auto_close"


def test_watch_returns_to_quiet_after_two_new_runs():
    t = snap("WATCH", below=["r1"])
    d = step(t, Observation(None, None, "r2"))
    assert d.action == "auto_close"


def test_downgrade_act_to_prepare_follows_hysteresis():
    t = snap("ACT")
    d1 = step(t, Observation("PREPARE", "PREPARE", "r1"))
    assert d1.action == "count_below"
    t.below_runs = d1.below_runs
    d2 = step(t, Observation("PREPARE", "PREPARE", "r2"))
    assert d2.action == "downgrade" and d2.level == "PREPARE" and d2.cap_msg_type == "Update"


def test_within_margin_does_not_count_below():
    # level dips under the entry threshold but stays above (entry - margin)
    d = step(snap("PREPARE"), Observation("WATCH", "PREPARE", "r1"))
    assert d.action == "none"


def test_counter_resets_when_back_above():
    d = step(snap("PREPARE", below=["r1"]), Observation("PREPARE", "PREPARE", "r2"))
    assert d.action == "reset_below"


def test_stand_down_reactivates_when_risk_rises():
    t = snap("PREPARE", status="stand_down_recommended", below=["a", "b"])
    assert step(t, Observation("WATCH", "WATCH", "c")).action == "none"
    d = step(t, Observation("PREPARE", "PREPARE", "c"))
    assert d.action == "reactivate" and d.status == "active"


def test_same_event_overlap():
    t = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    h = dt.timedelta(hours=1)
    assert same_event(t, t + 24 * h, t + 30 * h, t + 50 * h)  # within 24 h slack
    assert not same_event(t, t + 24 * h, t + 100 * h, t + 120 * h)
    assert same_event(None, None, t, t)
