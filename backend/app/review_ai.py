"""Agentic review assistant.

A human reviewer types a short, rough note about why a vehicle fails (e.g. "back seat has
crumbs and the mats are muddy"). An LLM turns that into (1) a clear, professional rejection
message addressed to the driver, and (2) structured (zone, issue) labels from our fixed taxonomy
so targeted re-clean and training data keep working. Never raises: returns None on any failure.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

from .config import get_settings
from .models import ISSUE_KEYS, ZONE_KEYS

logger = logging.getLogger("blucheck.review_ai")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")

ZONES = list(ZONE_KEYS)
ISSUES = list(ISSUE_KEYS)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reason": {"type": "string"},
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "zone_key": {"type": "string", "enum": ZONES},
                    "issue_key": {"type": "string", "enum": ISSUES},
                },
                "required": ["zone_key", "issue_key"],
            },
        },
    },
    "required": ["reason", "labels"],
}

SYSTEM = f"""You assist a fleet vehicle-cleanliness reviewer. The reviewer typed a short, rough
note explaining why a vehicle should be rejected. Do two things:

1) reason: rewrite the note as a clear, specific, professional rejection message ADDRESSED TO THE
   DRIVER, telling them exactly what to clean. 1-3 sentences, neutral and respectful. Do not
   invent problems the reviewer did not mention. Do not scold.

2) labels: extract EVERY (zone, issue) pair the note implies -- be thorough and exhaustive. A
   single zone can appear multiple times with different issues, and every distinct problem
   mentioned must become its own label. Use ONLY this taxonomy:
   zones: {", ".join(ZONES)}
   issues: {", ".join(ISSUES)}
   Map everyday words to the taxonomy: "mats" -> floor_mats; "seat"/"seats" -> seats;
   "dash"/"console" -> dashboard_console; "windows"/"glass"/"windshield"/"mirror" ->
   windows_glass; "body"/"exterior"/"paint"/"door"/"bumper" -> exterior_body; "trunk"/"cargo" ->
   boot; "crumbs"/"wrapper"/"bottle"/"litter"/"rubbish" -> trash; "muddy"/"mud" -> mud;
   "streaks on glass"/"fingerprints"/"smudge" -> smudge; "coffee"/"drink spill"/"wet liquid" ->
   spill; "dirty"/"dusty"/"grime" -> dust; "stain"/"mark"/"blotch" -> stain.
   Example note: "back seat has crumbs and a drink stain, floor mats are muddy, boot dusty"
   -> labels: [{{"zone_key":"seats","issue_key":"trash"}}, {{"zone_key":"seats","issue_key":"stain"}}, {{"zone_key":"floor_mats","issue_key":"mud"}}, {{"zone_key":"boot","issue_key":"dust"}}].
   Only include pairs clearly implied by the note. If none are clear, return an empty list.

Output only the JSON object."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


def rephrase(text: str, context: list | None = None) -> dict | None:
    """Turn a rough reviewer note into a polished reason + structured labels. Returns
    {"reason": str, "labels": [{"zone_key", "issue_key"}]} or None on failure."""
    cfg = _cfg()
    key = cfg.get("groq_api_key")
    if not key:
        return None
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("groq_sop_model") or "openai/gpt-oss-120b"
    user = f'Reviewer note:\n"""\n{text.strip()[:1500]}\n"""\n'
    if context:
        user += f"\nFor reference, the automated system flagged: {json.dumps(context)[:600]}"
    user += "\nRewrite the rejection reason for the driver and extract the labels."
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "review", "schema": SCHEMA}},
        "temperature": 0.2,
        "max_tokens": 500,
    }
    try:
        r = requests.post(
            f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=30
        )
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as err:  # noqa: BLE001
        logger.warning("review rephrase failed: %s", err)
        return None
    if not isinstance(out, dict) or "reason" not in out:
        return None
    seen = set()
    labels = []
    for l in out.get("labels") or []:
        if not isinstance(l, dict):
            continue
        zk, ik = l.get("zone_key"), l.get("issue_key")
        if zk in ZONE_KEYS and ik in ISSUE_KEYS and (zk, ik) not in seen:
            seen.add((zk, ik))
            labels.append({"zone_key": zk, "issue_key": ik})
    return {"reason": str(out.get("reason", ""))[:1000], "labels": labels}
