"""Backend mirror of the worker's scoring defaults (worker/scoring_config.py).

The worker owns the authoritative scoring math; the backend needs the same defaults to (a) show
"effective vs. inherited" in the scoring-config admin UI and (b) reproduce/sweep scores in the
validation harness. Backend and worker ship as separate images, so this is a deliberate copy.
Keep it identical to worker/scoring_config.py:DEFAULTS. A unit test asserts they match.
"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "blend_mean_weight": 0.5,
    "zone_weight": {
        "seats": 1.4,
        "floor_mats": 1.3,
        "windows_glass": 1.2,
        "dashboard_console": 1.1,
        "exterior_body": 0.8,
        "boot": 0.6,
    },
    "severity_cap": {"minor": 89, "moderate": 74, "severe": 54},
    "zoom_conf": 0.8,
    "borderline": [45, 82],
    "max_images_per_call": 5,
    "max_zooms": 4,
    "ensemble_per_frame": False,
    "ensemble_trim_frac": 0.0,
}

_DICT_KEYS = {"zone_weight", "severity_cap"}


def resolve(cfg: dict | None) -> dict:
    """Merge a stored scoring_config over DEFAULTS (shallow-merge dict-valued keys). Mirrors
    worker/scoring_config.py:resolve."""
    out: dict[str, Any] = {}
    for k, default in DEFAULTS.items():
        out[k] = dict(default) if isinstance(default, dict) else (list(default) if isinstance(default, list) else default)
    if not cfg:
        return out
    for k, v in cfg.items():
        if k not in DEFAULTS:
            continue
        if k in _DICT_KEYS and isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out
