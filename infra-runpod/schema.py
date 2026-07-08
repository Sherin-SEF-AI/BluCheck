"""Fixed output schema and taxonomy for the BluCheck VLM scoring stage.

This is the single source of truth for the structured JSON the model must return. The
AWS worker validates every response against SCORING_SCHEMA; the RunPod handler uses it
for guided (schema-constrained) decoding so responses are always valid.
"""

from __future__ import annotations

PROMPT_VERSION = "v2"

ZONE_KEYS = [
    "exterior_body",
    "windows_glass",
    "seats",
    "floor_mats",
    "dashboard_console",
    "boot",
]
ISSUE_KEYS = ["trash", "stain", "dust", "smudge", "spill", "mud"]
SEVERITY_KEYS = ["minor", "moderate", "severe"]

# JSON Schema (draft 2020-12 compatible) the model output must satisfy.
SCORING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
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
                                "bbox": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "minItems": 4,
                                    "maxItems": 4,
                                },
                            },
                            "required": ["issue_key", "severity", "description", "confidence"],
                        },
                    },
                },
                "required": ["zone_key", "score", "confidence", "issues"],
            },
        },
    },
    "required": ["reasoning", "overall_score", "overall_confidence", "zones"],
}
