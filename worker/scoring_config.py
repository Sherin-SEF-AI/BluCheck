"""Tunable, versioned scoring configuration.

Every knob that used to be a module-level constant in score.py lives here as a default. At
scoring time the worker loads the active ModelVersion.scoring_config (JSONB) and merges it over
these defaults, so an admin can retune the scoring math per model version with no redeploy. The
exact resolved config is stamped into ScoringResult.raw_json so every score is reproducible.

Absent keys fall back to the defaults below (which equal the historical hardcoded values), so a
null scoring_config reproduces today's behavior byte-for-byte.
"""

from __future__ import annotations

from typing import Any

# Historical constants, now the fallback defaults. Keep in sync with the backend's copy used
# for the /model/scoring-config GET default.
DEFAULTS: dict[str, Any] = {
    # _finalize: overall = blend_mean_weight*weighted_mean + (1-blend_mean_weight)*weighted_worst
    "blend_mean_weight": 0.5,
    # Per-zone importance multiplier on shortfall-from-clean (mean + worst terms).
    "zone_weight": {
        "seats": 1.4,
        "floor_mats": 1.3,
        "windows_glass": 1.2,
        "dashboard_console": 1.1,
        "exterior_body": 0.8,
        "boot": 0.6,
    },
    # A zone cannot score above the band-top implied by its worst issue's severity.
    "severity_cap": {"minor": 89, "moderate": 74, "severe": 54},
    # Adaptive-zoom triggers: below this confidence, or a zone score inside the borderline band.
    "zoom_conf": 0.8,
    "borderline": [45, 82],
    # Vision-model per-request image cap and zoom limits.
    "max_images_per_call": 5,
    "max_zooms": 4,
    # Cross-frame ensembling. Default aggregates the verdicts already produced (per-capture
    # overview + zoom). ensemble_per_frame=true would issue one call per frame (5x cost) and is
    # off by default; trim_frac drops the most extreme votes before averaging.
    "ensemble_per_frame": False,
    "ensemble_trim_frac": 0.0,
}

# Keys whose value is a dict and should be shallow-merged rather than replaced wholesale, so a
# partial override (e.g. bumping only seats) keeps the other defaults.
_DICT_KEYS = {"zone_weight", "severity_cap"}


def resolve(cfg: dict | None) -> dict:
    """Merge a stored scoring_config over DEFAULTS. Never mutates the input. Unknown keys are
    ignored; dict-valued keys are shallow-merged so partial overrides are allowed."""
    out: dict[str, Any] = {}
    for k, default in DEFAULTS.items():
        if isinstance(default, dict):
            out[k] = dict(default)
        elif isinstance(default, list):
            out[k] = list(default)
        else:
            out[k] = default
    if not cfg:
        return out
    for k, v in cfg.items():
        if k not in DEFAULTS:
            continue  # ignore stray keys; never let config inject unknown behavior
        if k in _DICT_KEYS and isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out
