"""License-plate OCR via AWS Rekognition (always-on, in-region). Reads text from a plate
photo and correlates it to the driver's registered car number for a soft integrity check.
"""

from __future__ import annotations

import logging
import re

import boto3

from .config import get_settings

logger = logging.getLogger("blucheck.plateocr")
_settings = get_settings()
_rekog = boto3.client("rekognition", region_name=_settings.aws_region)


def normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _plate_like(s: str) -> bool:
    n = normalize(s)
    return 5 <= len(n) <= 12 and any(c.isdigit() for c in n) and any(c.isalpha() for c in n)


def _similar(a: str, b: str) -> bool:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # tolerate up to 1 OCR error for same-length reads (transposition/substitution)
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) <= 1
    return False


def similar(a: str, b: str) -> bool:
    """Public tolerant plate comparison (1 OCR error / substring)."""
    return _similar(a, b)


_VEHICLE_LABELS = {
    "Car", "Vehicle", "Automobile", "Transportation", "Machine", "Suv", "Truck", "Van", "Sedan",
    "Sports Car", "Pickup Truck", "Coupe", "Bus", "Wheel", "Tire", "Car - Interior",
    "Vehicle Interior", "Car Seat", "Car Wheel", "Hardtop", "Bumper", "License Plate", "Windshield",
}


def detect_vehicle(image_bytes: bytes) -> dict:
    """Is this a photo of a car/vehicle? Uses Rekognition DetectLabels. Returns is_vehicle plus
    the top confidence and labels. Fails OPEN (is_vehicle True) on a Rekognition error so an
    outage never blocks all inspections; the on-device gate is a first filter, not the last word."""
    try:
        resp = _rekog.detect_labels(Image={"Bytes": image_bytes}, MaxLabels=25, MinConfidence=55)
    except Exception as err:  # noqa: BLE001
        logger.error("rekognition detect_labels failed: %s", err)
        return {"is_vehicle": True, "confidence": None, "labels": []}
    labels = [(lbl["Name"], lbl["Confidence"]) for lbl in resp.get("Labels", [])]
    best = max((c for n, c in labels if n in _VEHICLE_LABELS), default=0.0)
    logger.info("detect_vehicle best=%.1f labels=%s", best, [n for n, _ in labels[:6]])
    return {"is_vehicle": best >= 70.0, "confidence": round(best, 1), "labels": [n for n, _ in labels[:8]]}


def read_candidates(image_bytes: bytes) -> list[str]:
    """Best-effort: return normalized plate-like text reads, longest first. Empty on no-read.
    Used by plate-scan login where there is no expected plate to correlate against."""
    try:
        resp = _rekog.detect_text(Image={"Bytes": image_bytes})
    except Exception as err:  # noqa: BLE001 - OCR failure returns no candidates
        logger.error("rekognition detect_text failed: %s", err)
        return []
    lines = [
        d["DetectedText"]
        for d in resp.get("TextDetections", [])
        if d.get("Type") == "LINE" and d.get("Confidence", 0) >= 80
    ]
    return sorted({normalize(l) for l in lines if _plate_like(l)}, key=len, reverse=True)


def read_plate(image_bytes: bytes, expected_car_number: str) -> dict:
    """Return {read_plate, matched, candidates}. Never raises on a no-read; returns a
    negative match so the caller can soft-flag.
    """
    try:
        resp = _rekog.detect_text(Image={"Bytes": image_bytes})
    except Exception as err:  # noqa: BLE001 - OCR failure must not block; soft-flag instead
        logger.error("rekognition detect_text failed: %s", err)
        return {"read_plate": None, "matched": False, "candidates": []}

    lines = [
        d["DetectedText"]
        for d in resp.get("TextDetections", [])
        if d.get("Type") == "LINE" and d.get("Confidence", 0) >= 80
    ]
    candidates = [normalize(l) for l in lines if _plate_like(l)]

    # Prefer a candidate that matches the expected car number; else the best plate-like read.
    best = None
    for c in candidates:
        if _similar(c, expected_car_number):
            best = c
            break
    if best is None and candidates:
        best = max(candidates, key=len)

    matched = bool(best and _similar(best, expected_car_number))
    logger.info("plate_ocr read=%s expected=%s matched=%s", best, normalize(expected_car_number), matched)
    return {"read_plate": best, "matched": matched, "candidates": candidates}
