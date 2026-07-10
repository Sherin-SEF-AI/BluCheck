"""Weekly fleet digest agent.

Turns a week of fleet stats into a short, plain-English summary an admin can skim: how the fleet
did, what the recurring problems are, who's overdue, and anything flagged. Generated on a weekly
schedule and on demand. Never raises: returns None on failure.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

from .config import get_settings

logger = logging.getLogger("blucheck.digest_ai")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")

SYSTEM = """You write a weekly cleanliness-fleet digest for a fleet admin. Given the week's stats,
write a concise, skimmable summary in GitHub-flavored markdown:
- a one-line **headline** with the key number (pass rate or trend),
- a short **Highlights** bullet list (volumes, pass rate, best area),
- a **Needs attention** bullet list (recurring issues, overdue vehicles, any fraud flags),
- one friendly closing line of advice.
Keep it under ~150 words, use **bold** for key numbers, and never invent data not in the stats."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


def generate(stats: dict) -> str | None:
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
            {"role": "user", "content": f"This week's fleet stats:\n{json.dumps(stats)[:2000]}\n\nWrite the digest."},
        ],
        "temperature": 0.3,
        "max_tokens": 600,
    }
    try:
        r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=40)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"][:3000]
    except Exception as err:  # noqa: BLE001
        logger.warning("digest generation failed: %s", err)
        return None
