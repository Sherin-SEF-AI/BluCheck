"""Pure, dependency-free re-implementation of the worker's overall-score math.

The worker (worker/score.py:_finalize) owns scoring at inference time. This module lets the
backend recompute an overall score from already-stored per-zone scores + a candidate
scoring_config, WITHOUT re-calling the VLM — which is what powers the offline threshold/blend
sweep in the validation harness, and the reproducibility test. Kept in lock-step with
worker/score.py:_effective_zone_score + _finalize; a unit test asserts they agree.
"""

from __future__ import annotations


def effective_zone_score(score: float | None, issues: list | None, severity_cap: dict) -> float | None:
    """Zone score after the severity floor (worst issue's severity caps the zone score)."""
    if score is None:
        return None
    caps = [severity_cap[i["severity"]] for i in (issues or []) if i.get("severity") in severity_cap]
    return min(score, min(caps)) if caps else score


def overall_from_zones(zones: list[dict], scfg: dict) -> int | None:
    """Recompute overall = blend*weighted_mean + (1-blend)*weighted_worst from per-zone
    {score, issues, zone_key}. Mirrors worker _finalize exactly. Returns None if no scores."""
    zone_weight = scfg["zone_weight"]
    blend = scfg["blend_mean_weight"]
    sev_cap = scfg["severity_cap"]
    contribs: list[tuple[float, float]] = []
    for z in zones:
        es = effective_zone_score(z.get("score"), z.get("issues"), sev_cap)
        if es is None:
            continue
        contribs.append((zone_weight.get(z.get("zone_key"), 1.0), es))
    if not contribs:
        return None
    wsum = sum(w for w, _ in contribs)
    wmean = sum(w * s for w, s in contribs) / wsum
    worst = min(max(0.0, 100 - w * (100 - s)) for w, s in contribs)
    return round(blend * wmean + (1.0 - blend) * worst)
