"""Agentic SOP generator.

An admin describes the fleet's cleanliness policy in plain English ("prioritise the interior
passenger area; small exterior dirt is fine but flag big exterior dirt"). An LLM translates that
into the tunable scoring configuration -- per-zone importance weights, severity caps, the
mean/worst blend, and the approve/reject thresholds. The result is returned for review and only
applied when the admin confirms; nothing is auto-applied.

Never raises: on any failure it returns None so the endpoint can report a clean error.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

from .config import get_settings

logger = logging.getLogger("blucheck.sop")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")

ZONES = ["seats", "floor_mats", "dashboard_console", "windows_glass", "exterior_body", "boot"]

# Starter policies the admin can load into the SOP box and tune. Kept here (not in the DB) so
# they always reflect the current zone/knob vocabulary.
RECOMMENDED = [
    {
        "title": "Passenger-first (rideshare / taxi)",
        "sop": (
            "Prioritise the interior passenger experience -- seats, floor mats and dashboard must "
            "be clean. Small dirt or dust on the exterior body is acceptable, but flag heavy mud, "
            "big stains or major exterior dirt. Windows must be clear enough to see through. Be "
            "lenient on the boot."
        ),
    },
    {
        "title": "Premium / luxury fleet (strict)",
        "sop": (
            "This is a premium fleet. Every zone must be spotless -- interior and exterior. Reject "
            "anything beyond very light dust. No trash, no stains, no smudges on glass, clean "
            "seats, clean floor mats, spotless dashboard, and a clean polished exterior. Hold a "
            "high bar everywhere."
        ),
    },
    {
        "title": "Delivery / cargo vans (utility)",
        "sop": (
            "These are cargo delivery vans. The cargo area (boot) and floor must be clean and free "
            "of spills or debris. The driver seat and dashboard should be tidy. Exterior "
            "appearance is low priority -- tolerate dust, road grime and minor scuffs. Only flag "
            "exterior issues if they are severe, like thick mud or spills."
        ),
    },
    {
        "title": "Safety & visibility focus",
        "sop": (
            "Safety first: windows, windshield and mirrors must be clean and clear -- smudges or "
            "dirt on glass are not acceptable. Interior seats and floor should be reasonably "
            "clean. Be moderately lenient on exterior body panels and the boot as long as "
            "visibility is good."
        ),
    },
    {
        "title": "Balanced daily standard",
        "sop": (
            "Apply a fair everyday standard. Approve vehicles that are generally clean with only "
            "light dust or a single very-minor issue. Reject visible trash, stains, spills, mud, "
            "or a clearly dirty seat or floor. Treat all zones roughly equally, with a slight lean "
            "toward the interior."
        ),
    },
    {
        "title": "Monsoon / muddy season",
        "sop": (
            "During the rainy season, tolerate wet exteriors and light mud splatter on the body "
            "and wheels -- do not reject for that. But the interior must stay clean and dry: flag "
            "muddy floor mats, wet dirty seats, or mud tracked inside. Keep the passenger area to "
            "a high standard."
        ),
    },
]

SOP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "zone_weight": {
            "type": "object", "additionalProperties": False,
            "properties": {z: {"type": "number"} for z in ZONES},
            "required": ZONES,
        },
        "severity_cap": {
            "type": "object", "additionalProperties": False,
            "properties": {"minor": {"type": "integer"}, "moderate": {"type": "integer"}, "severe": {"type": "integer"}},
            "required": ["minor", "moderate", "severe"],
        },
        "blend_mean_weight": {"type": "number", "minimum": 0, "maximum": 1},
        "auto_approve": {"type": "integer", "minimum": 0, "maximum": 100},
        "auto_reject": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "priorities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["zone_weight", "severity_cap", "blend_mean_weight", "auto_approve", "auto_reject", "summary"],
}

SYSTEM = """You configure the cleanliness scoring engine for a passenger-vehicle fleet from a
plain-English policy written by a fleet admin. Translate their intent into these tunable knobs.

The six zones (a vehicle is scored 0-100 per zone; 100 = spotless):
  - seats, floor_mats, dashboard_console  -> the INTERIOR passenger experience
  - windows_glass                         -> visibility / interior-ish
  - exterior_body                         -> outer panels, doors, bumpers
  - boot                                  -> trunk / cargo

Knobs you must output:
  - zone_weight[zone]: how much dirt in that zone matters, both in the average and in the
    worst-zone term. 1.0 = neutral. Higher (up to ~2.0) = dirt there hurts more and is more
    likely to cause a reject. Lower (down to ~0.4) = dirt there is tolerated. If the admin says
    "prioritise interior", raise seats/floor_mats/dashboard and lower exterior_body/boot.
  - severity_cap.{minor,moderate,severe}: a zone with an issue of that severity cannot score
    above this cap (0-100). This is how "small dirt is OK but big dirt must be flagged" works:
    keep `minor` HIGH (e.g. 90) so small dirt barely lowers the score, but keep `severe` LOW
    (e.g. 40-50) so a big/severe issue in ANY zone forces that zone low and flags it. Typical
    defaults: minor 89, moderate 74, severe 54.
  - blend_mean_weight (0..1): overall = blend*average + (1-blend)*worst_zone. Lower it (e.g. 0.4)
    when one badly-dirty zone should pull the whole score down even if the rest is clean.
  - auto_approve / auto_reject (0..100): scores at/above auto_approve pass automatically; at/below
    auto_reject fail; in between goes to review. Loosen (lower auto_approve, lower auto_reject) for
    a lenient policy; tighten for a strict one.

Guidance: honour the admin's priorities faithfully. "Small exterior dirt OK" => low
exterior_body weight AND high minor cap. "Big exterior dirt flagged" => keep severe cap low and
blend_mean_weight lowish so a severe exterior issue still bites via the worst-zone term.

Also return a one-paragraph `summary` of the policy in your own words, and a short `priorities`
list. Output only the JSON object."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _to_config(out: dict) -> dict | None:
    """Validate + clamp the model output into a safe scoring_config + thresholds proposal."""
    if not isinstance(out, dict) or "zone_weight" not in out:
        return None
    zw = out.get("zone_weight") or {}
    zone_weight = {z: round(_clamp(zw.get(z), 0.2, 2.5, 1.0), 2) for z in ZONES}
    sc = out.get("severity_cap") or {}
    severity_cap = {
        "minor": int(_clamp(sc.get("minor"), 0, 100, 89)),
        "moderate": int(_clamp(sc.get("moderate"), 0, 100, 74)),
        "severe": int(_clamp(sc.get("severe"), 0, 100, 54)),
    }
    blend = round(_clamp(out.get("blend_mean_weight"), 0.0, 1.0, 0.5), 2)
    approve = int(_clamp(out.get("auto_approve"), 0, 100, 85))
    reject = int(_clamp(out.get("auto_reject"), 0, 100, 40))
    if reject >= approve:  # keep a sane band
        reject = max(0, approve - 10)
    return {
        "scoring_config": {"zone_weight": zone_weight, "severity_cap": severity_cap, "blend_mean_weight": blend},
        "thresholds": {"overall": {"auto_approve": approve, "auto_reject": reject}},
        "summary": str(out.get("summary", ""))[:1200],
        "priorities": [str(p)[:120] for p in (out.get("priorities") or []) if isinstance(p, str)][:8],
    }


def generate(sop_text: str, current: dict | None = None) -> dict | None:
    """Translate a natural-language cleanliness SOP into a scoring_config + thresholds proposal."""
    cfg = _cfg()
    key = cfg.get("groq_api_key")
    if not key:
        return None
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    # gpt-oss reliably honours json_schema; the vision brain model may not.
    model = cfg.get("groq_sop_model") or "openai/gpt-oss-120b"
    user = f"Fleet cleanliness policy:\n\"\"\"\n{sop_text.strip()[:2000]}\n\"\"\"\n"
    if current:
        user += f"\nCurrent config (adjust from here if helpful): {json.dumps(current)[:800]}"
    user += "\nProduce the scoring configuration that best encodes this policy."
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "sop", "schema": SOP_SCHEMA}},
        "temperature": 0.2,
        "max_tokens": 900,
    }
    try:
        r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=40)
        r.raise_for_status()
        return _to_config(json.loads(r.json()["choices"][0]["message"]["content"]))
    except Exception as err:  # noqa: BLE001
        logger.warning("SOP generation failed: %s", err)
        return None
