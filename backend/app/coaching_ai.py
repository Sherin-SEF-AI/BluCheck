"""Driver coaching agent.

Reads a driver's own recent inspection history and writes a short, friendly, specific coaching
tip ("your floor mats are the recurring issue -- a quick vacuum before your shift fixes it").
Fully autonomous, delivered in the app. Never raises: returns None on any failure.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

from .config import get_settings

logger = logging.getLogger("blucheck.coaching_ai")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "tip": {"type": "string"},
        "focus_zone": {"type": "string"},
    },
    "required": ["headline", "tip"],
}

SYSTEM = """You are a friendly, encouraging cleanliness coach for a vehicle fleet driver. Given a
summary of the driver's recent inspections (passes, rejections, and which areas keep getting
flagged), write brief, practical, motivating coaching. Be warm and specific, never scolding.

- headline: a short, upbeat one-liner (max ~8 words).
- tip: 1-2 sentences of concrete, actionable advice tied to their actual recurring issues. If they
  are doing well, praise them and give one small maintenance tip.
- focus_zone: the single area they should focus on next (or "" if all good).

Output only the JSON object."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


def coach(summary: dict) -> dict | None:
    """Generate a coaching tip from a driver-history summary. Returns
    {"headline", "tip", "focus_zone"} or None."""
    cfg = _cfg()
    key = cfg.get("groq_api_key")
    if not key:
        return None
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("groq_sop_model") or "openai/gpt-oss-120b"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Driver history summary:\n{json.dumps(summary)[:1200]}\n\nWrite the coaching."},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "coaching", "schema": SCHEMA}},
        "temperature": 0.4,
        "max_tokens": 300,
    }
    try:
        r = requests.post(
            f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=25
        )
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as err:  # noqa: BLE001
        logger.warning("coaching failed: %s", err)
        return None
    if not isinstance(out, dict) or "tip" not in out:
        return None
    return {
        "headline": str(out.get("headline", ""))[:120],
        "tip": str(out.get("tip", ""))[:400],
        "focus_zone": str(out.get("focus_zone", ""))[:40],
    }
