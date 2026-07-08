"""Unit tests for the pure scoring math and calibration (no DB / no AWS required).

These cover the functions that decide a score or a gate: the mean/worst blend, zone weights,
severity caps (scoring_math), the confidence calibration curve + fail-safe lookup (calibration),
and the reproducibility guarantee (a default config reproduces the historical numbers). Also
asserts the backend and worker scoring defaults stay in sync.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import calibration, scoring_defaults, scoring_math  # noqa: E402


def _clean(k):
    return {"zone_key": k, "score": 90, "issues": []}


def _z(k, score, sev=None):
    return {"zone_key": k, "score": score, "issues": ([{"severity": sev}] if sev else [])}


ZK = ["exterior_body", "windows_glass", "seats", "floor_mats", "dashboard_console", "boot"]


def test_default_config_reproduces_historical_scores():
    cfg = scoring_defaults.resolve(None)
    dusty_boot = [_clean(k) for k in ZK if k != "boot"] + [_z("boot", 55, "moderate")]
    stained_seat = [_clean(k) for k in ZK if k != "seats"] + [_z("seats", 55, "moderate")]
    severe_seat = [_clean(k) for k in ZK if k != "seats"] + [_z("seats", 82, "severe")]
    assert scoring_math.overall_from_zones(dusty_boot, cfg) == 80
    assert scoring_math.overall_from_zones(stained_seat, cfg) == 60
    assert scoring_math.overall_from_zones(severe_seat, cfg) == 59  # severity floor bites


def test_config_changes_score_without_redeploy():
    stained = [_clean(k) for k in ZK if k != "seats"] + [_z("seats", 55, "moderate")]
    base = scoring_math.overall_from_zones(stained, scoring_defaults.resolve(None))
    mean_weighted = scoring_math.overall_from_zones(stained, scoring_defaults.resolve({"blend_mean_weight": 0.8}))
    seat_heavier = scoring_math.overall_from_zones(stained, scoring_defaults.resolve({"zone_weight": {"seats": 2.0}}))
    assert mean_weighted > base   # favoring the mean lifts a single-bad-zone score
    assert seat_heavier < base    # weighting the seat harder lowers it


def test_severity_floor_only_lowers():
    cfg = scoring_defaults.resolve(None)
    assert scoring_math.effective_zone_score(90, [{"severity": "severe"}], cfg["severity_cap"]) == 54
    assert scoring_math.effective_zone_score(30, [{"severity": "severe"}], cfg["severity_cap"]) == 30  # never raises
    assert scoring_math.effective_zone_score(90, [], cfg["severity_cap"]) == 90


def test_partial_config_merge_keeps_other_defaults():
    cfg = scoring_defaults.resolve({"zone_weight": {"seats": 2.0}})
    assert cfg["zone_weight"]["seats"] == 2.0
    assert cfg["zone_weight"]["floor_mats"] == 1.3  # untouched default preserved


def test_calibration_fail_safe_on_sparse_data():
    curve = calibration.build_curve([(0.9, True), (0.88, True)])  # 2 samples, under support
    assert calibration.lookup(curve, 0.9) is None  # -> caller routes to human


def test_calibration_monotonic_and_lookup():
    pairs = [(0.95, True)] * 18 + [(0.95, False)] * 2
    pairs += [(0.75, True)] * 7 + [(0.75, False)] * 5
    pairs += [(0.55, True)] * 5 + [(0.55, False)] * 7
    curve = calibration.build_curve(pairs)
    cals = [b["calibrated"] for b in curve["bins"] if b["calibrated"] is not None]
    assert all(cals[i] <= cals[i + 1] for i in range(len(cals) - 1))  # isotonic
    assert calibration.lookup(curve, 0.97) == 0.9      # high raw -> calibrated 0.9 (not 0.97)
    assert calibration.lookup(curve, 0.55) < 0.5       # low raw -> low calibrated


def test_backend_and_worker_scoring_defaults_in_sync():
    worker_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "worker"))
    sys.path.insert(0, worker_dir)
    import scoring_config as worker_cfg  # noqa: E402

    assert worker_cfg.DEFAULTS == scoring_defaults.DEFAULTS


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
