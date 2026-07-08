"""Self-tuning policy agent.

Reads where human reviewers OVERTURNED the agent (the override log) and proposes a modest,
targeted adjustment to the scoring config + thresholds that would reduce those disagreements --
turning model tuning from a manual chore into a data-driven suggestion. Preview only: the admin
reviews and applies (or the dashboard can auto-apply within guardrails). Never raises.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

from . import sop
from .config import get_settings

logger = logging.getLogger("blucheck.tuning_ai")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")

# Same knobs as the SOP schema, plus a no_change flag and a confidence.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "no_change": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        **sop.SOP_SCHEMA["properties"],
    },
    "required": ["no_change", "confidence", "summary"],
}

SYSTEM = """You tune a passenger-vehicle cleanliness scoring engine from HUMAN OVERRIDE data: the
agent's automated decisions are being overturned by human reviewers, and you must adjust the
policy so the agent agrees with humans more often.

You are given the current config and a summary of recent overrides:
- TOO STRICT: the agent REJECTED but a human APPROVED. The zones/issues listed are what the agent
  over-flagged. To fix: lower those zones' weights, and/or raise the relevant severity caps,
  and/or lower auto_reject.
- TOO LENIENT: the agent APPROVED but a human REJECTED. To fix: raise those zones' weights, and/or
  lower the relevant severity caps, and/or raise auto_approve.

Make MODEST, targeted changes from the current values -- never overhaul the policy. Keep every
zone weight in 0.2..2.5 and keep auto_reject below auto_approve. If the overrides are too few (say
under 4) or show no clear, consistent pattern, set no_change=true and leave the config near
current. Always return the full set of knobs (all six zone weights, the three severity caps, the
blend, and both thresholds) reflecting your adjusted policy. Also give a one-paragraph summary of
what you changed and why. Output only the JSON object."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


def suggest(evidence: dict, current: dict) -> dict | None:
    """Propose a tuning adjustment. `evidence` summarizes the overrides; `current` is the current
    effective config + thresholds. Returns {no_change, confidence, scoring_config, thresholds,
    summary} or None on failure."""
    cfg = _cfg()
    key = cfg.get("groq_api_key")
    if not key:
        return None
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("groq_sop_model") or "openai/gpt-oss-120b"
    user = (
        f"Current config:\n{json.dumps(current)[:900]}\n\n"
        f"Recent overrides (humans overturning the agent):\n{json.dumps(evidence)[:1500]}\n\n"
        "Propose the adjusted policy."
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "tuning", "schema": SCHEMA}},
        "temperature": 0.2,
        "max_tokens": 900,
    }
    try:
        r = requests.post(
            f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=40
        )
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as err:  # noqa: BLE001
        logger.warning("tuning suggest failed: %s", err)
        return None
    if not isinstance(out, dict):
        return None
    no_change = bool(out.get("no_change", False))
    confidence = float(out.get("confidence", 0.5) or 0.5)
    summary = str(out.get("summary", ""))[:1200]
    # Reuse the SOP validator to clamp the config + thresholds safely.
    cfg_out = sop._to_config(out) if not no_change and "zone_weight" in out else None
    result = {"no_change": no_change, "confidence": confidence, "summary": summary}
    if cfg_out:
        result["scoring_config"] = cfg_out["scoring_config"]
        result["thresholds"] = cfg_out["thresholds"]
    return result
