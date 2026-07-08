"""Frame selection: score each extracted frame for sharpness and exposure, compute a
perceptual hash, drop near-duplicates, and pick the best top-N per capture. These frames
are the ones the reviewer sees first and the ones sent to the VLM scoring stage.

Blur:     variance of the Laplacian (higher is sharper).
Exposure: a 0..1 score that rewards mid-brightness with good tonal spread.
Dedup:    perceptual hash (pHash) with a Hamming-distance threshold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import imagehash
import numpy as np
from PIL import Image

logger = logging.getLogger("blucheck.select")


@dataclass
class FrameMetrics:
    seq: int
    blur: float
    exposure: float
    phash: str


def compute_metrics(png_path: str, seq: int) -> FrameMetrics:
    img = cv2.imread(png_path, cv2.IMREAD_COLOR)
    if img is None:
        # Unreadable frame: worst possible metrics so it is never selected.
        return FrameMetrics(seq=seq, blur=0.0, exposure=0.0, phash="0" * 16)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    mean = float(gray.mean())
    std = float(gray.std())
    # Reward brightness near the middle and a healthy spread of tones.
    brightness = 1.0 - abs(mean - 128.0) / 128.0
    spread = min(std / 64.0, 1.0)
    exposure = max(0.0, brightness) * spread

    phash = str(imagehash.phash(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))))
    return FrameMetrics(seq=seq, blur=blur, exposure=exposure, phash=phash)


def _quality(m: FrameMetrics) -> float:
    # Combine sharpness (log-scaled, unbounded) with exposure (0..1).
    return float(np.log1p(m.blur)) * (0.5 + 0.5 * m.exposure)


def select(
    metrics: list[FrameMetrics],
    top_n: int,
    phash_threshold: int = 6,
    zone_hint: dict[int, str] | None = None,
    required_zones: set[str] | None = None,
) -> set[int]:
    """Return the seq numbers to mark selected, guaranteeing coverage of the whole capture.

    A capture is a continuous walk-around (exterior) or pan (interior), so temporal position
    tracks spatial position: the start, middle, and end of the clip show different sides /
    zones. Picking only the globally sharpest frames tends to cluster on one spot and miss
    whole zones (e.g. all front shots, no boot). We split the clip into `top_n` contiguous time
    segments and take the sharpest, non-duplicate frame from EACH segment, so the frames sent to
    the VLM span every part of the vehicle the driver actually filmed.

    Zone awareness (optional): when a per-frame `zone_hint` (seq -> zone_key) and the set of
    `required_zones` are available, we first guarantee at least one good frame per required zone,
    then fill the remaining slots with the temporal sharp+diverse picks. Without a hint (the
    default, since zones are assigned by the VLM after selection) the behavior is unchanged.
    """
    if not metrics:
        return set()
    ordered = sorted(metrics, key=lambda m: m.seq)  # temporal order
    if len(ordered) <= top_n:
        return {m.seq for m in ordered}

    selected: list[FrameMetrics] = []
    picked: set[int] = set()

    def _is_dup(m: FrameMetrics) -> bool:
        h = imagehash.hex_to_hash(m.phash)
        return any(h - imagehash.hex_to_hash(s.phash) <= phash_threshold for s in selected)

    # 1) Zone coverage first (only when a hint is provided): best non-duplicate frame per zone.
    if zone_hint and required_zones:
        by_zone: dict[str, list[FrameMetrics]] = {}
        for m in ordered:
            z = zone_hint.get(m.seq)
            if z in required_zones:
                by_zone.setdefault(z, []).append(m)
        for z in required_zones:
            if len(picked) >= top_n:
                break
            cands = sorted(by_zone.get(z, []), key=_quality, reverse=True)
            chosen = next((m for m in cands if m.seq not in picked and not _is_dup(m)), None)
            if chosen is None:  # all dups/taken: keep the sharpest fresh one for coverage
                chosen = next((m for m in cands if m.seq not in picked), None)
            if chosen is not None:
                selected.append(chosen)
                picked.add(chosen.seq)

    # 2) Fill remaining slots with the temporal sharp+diverse picks (unchanged algorithm).
    segments = np.array_split(ordered, top_n)  # top_n contiguous temporal bins
    for seg in segments:
        if len(picked) >= top_n:
            break
        cands = sorted(list(seg), key=_quality, reverse=True)
        chosen = next((m for m in cands if m.seq not in picked and not _is_dup(m)), None)
        if chosen is None:
            # Every candidate duplicates a prior pick: keep the sharpest fresh one so segment
            # (i.e. zone) coverage is never sacrificed.
            chosen = next((m for m in cands if m.seq not in picked), None)
        if chosen is not None:
            selected.append(chosen)
            picked.add(chosen.seq)
    return picked
