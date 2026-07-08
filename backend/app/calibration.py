"""Confidence calibration.

The VLM's raw `overall_confidence` is not calibrated: 0.8 does not mean "80% likely correct".
This module turns historical (raw confidence, was-the-model-right) pairs into a reliability
curve — an empirical mapping from raw confidence to the probability the model agreed with the
human — so the decision layer can gate auto actions on *calibrated* confidence.

Pure functions only (no DB, no AWS), so they are unit-testable. `build_curve` bins the pairs and
applies isotonic regression (pool-adjacent-violators) so the calibrated rate is non-decreasing
in confidence. `lookup` fails safe: it returns None where there is not enough empirical evidence
at that confidence, which the caller treats as "do not auto-act, route to a human".
"""

from __future__ import annotations

N_BINS = 10
MIN_BIN_SUPPORT = 5  # need at least this many labeled samples in a bin to trust its rate


def _pav(rates: list[float], weights: list[float]) -> list[float]:
    """Pool-adjacent-violators isotonic fit: return a non-decreasing value per input point,
    minimizing weighted squared error. Standard calibration smoothing."""
    stack: list[list[float]] = []  # each block: [value, weight, count]
    for r, w in zip(rates, weights):
        stack.append([r, w, 1])
        while len(stack) >= 2 and stack[-2][0] > stack[-1][0]:
            v2, w2, c2 = stack.pop()
            v1, w1, c1 = stack.pop()
            merged_w = w1 + w2
            stack.append([(v1 * w1 + v2 * w2) / merged_w, merged_w, c1 + c2])
    out: list[float] = []
    for v, _w, c in stack:
        out.extend([v] * int(c))
    return out


def build_curve(pairs: list[tuple[float, bool]], n_bins: int = N_BINS) -> dict:
    """Build a reliability curve from (raw_confidence, correct) pairs.

    Returns a JSON-serializable dict: per-bin n / correct / raw rate / isotonic `calibrated`
    rate, the overall base rate, and totals. Empty bins carry calibrated=None.
    """
    bins = [
        {"lo": round(i / n_bins, 4), "hi": round((i + 1) / n_bins, 4), "n": 0, "correct": 0}
        for i in range(n_bins)
    ]
    for conf, correct in pairs:
        c = min(max(float(conf), 0.0), 1.0)
        idx = min(int(c * n_bins), n_bins - 1)
        bins[idx]["n"] += 1
        bins[idx]["correct"] += 1 if correct else 0
    for b in bins:
        b["rate"] = round(b["correct"] / b["n"], 4) if b["n"] else None

    non_empty = [b for b in bins if b["n"]]
    fitted = _pav([b["rate"] for b in non_empty], [float(b["n"]) for b in non_empty]) if non_empty else []
    j = 0
    for b in bins:
        if b["n"]:
            b["calibrated"] = round(fitted[j], 4)
            j += 1
        else:
            b["calibrated"] = None

    n_total = sum(b["n"] for b in bins)
    n_correct = sum(b["correct"] for b in bins)
    return {
        "n_bins": n_bins,
        "n_samples": n_total,
        "base_rate": round(n_correct / n_total, 4) if n_total else None,
        "min_bin_support": MIN_BIN_SUPPORT,
        "bins": bins,
    }


def lookup(calibration: dict | None, confidence: float | None, min_support: int | None = None) -> float | None:
    """Map a raw confidence to its calibrated correctness probability, or None when there is not
    enough empirical evidence at that confidence (fail-safe: the caller must route to a human).
    """
    if not calibration or not calibration.get("bins"):
        return None
    n_bins = calibration.get("n_bins", N_BINS)
    support = min_support if min_support is not None else calibration.get("min_bin_support", MIN_BIN_SUPPORT)
    c = min(max(float(confidence or 0.0), 0.0), 1.0)
    idx = min(int(c * n_bins), n_bins - 1)
    b = calibration["bins"][idx]
    if b.get("n", 0) < support or b.get("calibrated") is None:
        return None
    return float(b["calibrated"])
