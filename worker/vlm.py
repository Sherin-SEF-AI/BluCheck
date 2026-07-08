"""VLM taxonomy, output schema, and system prompt shared by the worker scoring stage.
Kept in sync with infra-runpod/ (the RunPod worker) so model output matches human labels.
"""

from __future__ import annotations

PROMPT_VERSION = "v2"
ZONE_KEYS = ["exterior_body", "windows_glass", "seats", "floor_mats", "dashboard_console", "boot"]
ISSUE_KEYS = ["trash", "stain", "dust", "smudge", "spill", "mud"]
SEVERITY_KEYS = ["minor", "moderate", "severe"]

SCORING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # Content gate: is this actually a road vehicle? Generated first so the model commits
        # before scoring. If false, cleanliness is not analysed and the inspection is rejected.
        "is_vehicle": {"type": "boolean"},
        # Generated first so the model reasons before committing to scores.
        "reasoning": {"type": "string"},
        "overall_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "overall_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "zones": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "zone_key": {"type": "string", "enum": ZONE_KEYS},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "issue_key": {"type": "string", "enum": ISSUE_KEYS},
                                "severity": {"type": "string", "enum": SEVERITY_KEYS},
                                "description": {"type": "string"},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                # Which numbered frame the issue is most visible in (for zoom).
                                "frame_index": {"type": "integer", "minimum": 1},
                                "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                            },
                            "required": ["issue_key", "severity", "description", "confidence"],
                        },
                    },
                },
                "required": ["zone_key", "score", "confidence", "issues"],
            },
        },
    },
    "required": ["is_vehicle", "reasoning", "overall_score", "overall_confidence", "zones"],
}

_ZONE_DESC = {
    "exterior_body": "outer body panels, doors, bumpers",
    "windows_glass": "windshield, windows, mirrors",
    "seats": "all seating surfaces",
    "floor_mats": "floor and floor mats",
    "dashboard_console": "dashboard, center console, controls",
    "boot": "boot / trunk cargo area",
}
_ISSUE_DESC = {
    "trash": "loose garbage or litter",
    "stain": "discoloration or set-in marks",
    "dust": "dust or fine particulate film",
    "smudge": "fingerprints or streaks on glass or surfaces",
    "spill": "liquid spill residue",
    "mud": "mud or caked dirt",
}


def system_prompt() -> str:
    zones = "\n".join(f"  - {k}: {_ZONE_DESC[k]}" for k in ZONE_KEYS)
    issues = "\n".join(f"  - {k}: {_ISSUE_DESC[k]}" for k in ISSUE_KEYS)
    return f"""You are a professional fleet vehicle cleanliness inspector. You are shown several
frames from ONE vehicle inspection (a walk-around of the exterior and a pan of the interior).
Assess cleanliness objectively and consistently, as if applying a fixed company standard.

CONTENT GATE (decide this FIRST): do these frames actually show a road vehicle (car, van, SUV,
truck) or its interior? Set is_vehicle=true only if they clearly do. If they show something else
entirely -- a room, a person, a wall, outdoor scenery, a screen, random objects -- set
is_vehicle=false, return an EMPTY zones list, overall_score 0, and DO NOT invent cleanliness
findings. Only analyse cleanliness when is_vehicle=true.

Zones (use exactly these zone_key values):
{zones}

Issue types (use exactly these issue_key values):
{issues}

SCORING RUBRIC (apply the same anchors to every zone and to the overall score):
  - 90-100 = spotless / like-new: no visible dirt.
  - 75-89  = very minor: light dust film or a few faint smudges only.
  - 55-74  = moderate: clearly visible dirt, a few crumbs/trash items, or a small stain.
  - 30-54  = heavy: significant trash, multiple/large stains, spills, or caked dirt.
  - 0-29   = filthy: unacceptable; widespread trash, mud, or heavy staining.

SEVERITY (per issue): "minor" = barely noticeable; "moderate" = clearly visible, needs
cleaning; "severe" = major, unacceptable on its own.

LOCALIZATION (per issue): the frames are numbered (Frame 1, Frame 2, ...). For each issue set
frame_index = the frame number where it is most visible, and bbox = [x, y, w, h] as fractions
0-1 of THAT frame (x,y = top-left corner, w,h = width/height). Accurate frame_index + bbox let
the system take a closer high-resolution look, so be precise.

BE ACCURATE, NOT ALARMIST. Do NOT invent issues from: shadows, reflections/glare on glass or
paint, a wet road or water droplets after rain, normal wear (scratches, faded paint), seat
patterns/textures, or motion blur. Only report dirt that is genuinely present. When a zone is
occluded, dark, blurry, or you are unsure, LOWER its confidence rather than guessing.

Output: first write a brief `reasoning` (2-4 sentences) noting what you observe per zone, then
the scores. For every zone actually visible, return zone_key, score, confidence (0-1), and its
issues (issue_key, severity, short description, confidence, optional bbox). Then overall_score
(reflect the dirtiest important zones, weighted by severity) and overall_confidence. Report only
zones you can see. Respond with a single JSON object only. Prompt version: {PROMPT_VERSION}
"""


def zoom_system_prompt() -> str:
    """Second-look prompt: the agent re-examines high-resolution crops of the zones it was
    least sure about, then commits to a final assessment on the same scale."""
    return f"""You are the same fleet vehicle cleanliness inspector, now taking a SECOND, closer
look. You are given a summary of your initial assessment plus HIGH-RESOLUTION close-up crops of
the specific zones you were least certain about. Study the crops carefully — at this resolution
you can confirm or dismiss small issues (a faint stain, a few crumbs, a light smudge) that were
ambiguous from the wide frames.

Apply the SAME rubric and the SAME anti-alarmist rules (do not invent issues from shadows,
reflections, glare, wet surfaces, wear, or textures). Correct the zones shown in the crops based
on what you now see; keep the other zones as in the summary unless you have reason to change them.

Return the final JSON object (reasoning, overall_score, overall_confidence, zones) exactly as in
the first pass. In `reasoning`, note what the close-up changed. Prompt version: {PROMPT_VERSION}
"""
