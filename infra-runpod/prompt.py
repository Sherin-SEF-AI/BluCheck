"""Versioned system prompt for cleanliness scoring. Includes the zone and issue taxonomy
so the model output aligns exactly with the human labels captured in the dashboard.
"""

from __future__ import annotations

from schema import ISSUE_KEYS, PROMPT_VERSION, ZONE_KEYS

ZONE_DESCRIPTIONS = {
    "exterior_body": "outer body panels, doors, bumpers",
    "windows_glass": "windshield, windows, mirrors",
    "seats": "all seating surfaces",
    "floor_mats": "floor and floor mats",
    "dashboard_console": "dashboard, center console, controls",
    "boot": "boot / trunk cargo area",
}
ISSUE_DESCRIPTIONS = {
    "trash": "loose garbage or litter",
    "stain": "discoloration or set-in marks",
    "dust": "dust or fine particulate film",
    "smudge": "fingerprints or streaks on glass or surfaces",
    "spill": "liquid spill residue",
    "mud": "mud or caked dirt",
}


def system_prompt() -> str:
    zones = "\n".join(f"  - {k}: {ZONE_DESCRIPTIONS[k]}" for k in ZONE_KEYS)
    issues = "\n".join(f"  - {k}: {ISSUE_DESCRIPTIONS[k]}" for k in ISSUE_KEYS)
    return f"""You are a fleet vehicle cleanliness inspector. You are shown several frames from a
single vehicle inspection (exterior and interior). Assess cleanliness objectively.

Zones (use exactly these zone_key values):
{zones}

Issue types (use exactly these issue_key values):
{issues}

For every zone that is visible in the frames, return:
  - score: 0 to 100, where 100 is spotless and 0 is very dirty
  - confidence: 0.0 to 1.0, how sure you are given image quality and coverage
  - issues: the specific problems you see, each with an issue_key, a short plain-language
    description, a confidence, and an optional bounding box [x, y, w, h] in pixels

Then return an overall_score and overall_confidence for the whole vehicle.

Rules:
  - Only report zones you can actually see. Do not invent zones or issues.
  - Be conservative: if unsure, lower the confidence rather than guessing.
  - Respond with a single JSON object only, matching the required schema. No prose.

Prompt version: {PROMPT_VERSION}
"""
