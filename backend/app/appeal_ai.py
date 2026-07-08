"""Agentic appeal resolution.

When a driver disputes an automated rejection, an independent agent re-examines the evidence
(per-zone scores + detected issues) against the active policy and rules UPHOLD / REVERSE /
ESCALATE, so most appeals close themselves and only genuinely borderline cases reach a human.
Never raises: returns None on any failure so the caller can fall back to human review.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

from .config import get_settings

logger = logging.getLogger("blucheck.appeal_ai")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ruling": {"type": "string", "enum": ["uphold", "reverse", "escalate"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["ruling", "confidence", "reason"],
}

SYSTEM = """You are an independent, fair appeals reviewer for a passenger-vehicle cleanliness
program. A driver is DISPUTING an automated rejection. You are given the per-zone cleanliness
scores (0-100, where 100 = spotless) and the specific issues detected in each zone, the active
policy (per-zone importance weights, severity caps, and the approve/reject thresholds), and the
original rejection reason. Rule fairly and independently:

- REVERSE (approve the vehicle) if the rejection was harsh: the only issues are minor, the vehicle
  is essentially clean for passenger service under this policy, or the rejection leaned mainly on a
  zone the policy treats as low-importance.
- UPHOLD (keep it rejected) if there is a clear, legitimate cleanliness problem a passenger would
  notice: trash, stains, spills, mud, or a clearly dirty high-importance zone.
- ESCALATE to a human ONLY when the evidence is genuinely conflicting or borderline and you truly
  cannot decide fairly.

Do NOT reverse clear, legitimate rejections just because the driver disputed. Give the driver a
short, respectful, specific reason for your ruling. Output only the JSON object."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


def resolve(scoring: dict, policy: dict, prior_reason: str | None) -> dict | None:
    """Rule on an appeal. `scoring` = {overall, zones:[{zone_key, score, issues:[...]}]}.
    `policy` = {zone_weight, severity_cap, thresholds}. Returns
    {"ruling": uphold|reverse|escalate, "confidence": float, "reason": str} or None."""
    cfg = _cfg()
    key = cfg.get("groq_api_key")
    if not key:
        return None
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("groq_sop_model") or "openai/gpt-oss-120b"
    user = (
        f"Evidence (per-zone scores + issues):\n{json.dumps(scoring)[:2000]}\n\n"
        f"Active policy:\n{json.dumps(policy)[:900]}\n\n"
        f'Original rejection reason:\n"""\n{(prior_reason or "").strip()[:600]}\n"""\n\n'
        "Rule on this appeal."
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "appeal", "schema": SCHEMA}},
        "temperature": 0.2,
        "max_tokens": 400,
    }
    try:
        r = requests.post(
            f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=30
        )
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as err:  # noqa: BLE001
        logger.warning("appeal resolve failed: %s", err)
        return None
    if not isinstance(out, dict) or out.get("ruling") not in ("uphold", "reverse", "escalate"):
        return None
    return {
        "ruling": out["ruling"],
        "confidence": float(out.get("confidence", 0.5) or 0.5),
        "reason": str(out.get("reason", ""))[:1000],
    }
