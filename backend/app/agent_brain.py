"""Autonomous supervisor agent (the decision "brain").

Given a completed inspection's per-zone cleanliness scores plus context (the driver's recent
record and, for a re-inspection, what was wrong last time), an LLM reasons like a fair-but-firm
fleet supervisor and returns a DECISION plus the ACTIONS to take: a tailored message for the
driver, which zones to re-clean, and whether to escalate a repeat offender to a human. The
caller (agent.apply_decision) carries out those actions.

Text reasoning only, so it uses a stronger text model (Llama-3.3-70B on Groq) than the vision
scorer. Fully API-based. Never raises: on any failure it returns None and the caller falls back
to the deterministic threshold decision, so autonomy degrades safely."""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

from .config import get_settings

logger = logging.getLogger("blucheck.brain")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reject", "escalate"]},
        "reasoning": {"type": "string"},
        "notify_title": {"type": "string"},
        "notify_body": {"type": "string"},
        "reclean_zones": {"type": "array", "items": {"type": "string"}},
        "escalate_reason": {"type": "string"},
        "priority": {"type": "string", "enum": ["low", "normal", "high"]},
    },
    "required": ["decision", "reasoning", "notify_title", "notify_body"],
}

SYSTEM = """You are the autonomous cleanliness supervisor for a passenger-vehicle fleet in India.
A vision model has already inspected a vehicle and scored each zone 0-100 (100 = spotless) with
detected issues and severity. Your job is to make the final call AND decide the actions to take,
like a fair but firm fleet supervisor who cares about passenger experience.

Decide one of:
- "approve": the vehicle is clean enough to carry passengers (light dust or a single very-minor
  issue is acceptable). Congratulate briefly.
- "reject": there is a real cleanliness problem (trash, stains, spills, mud, a dirty seat or
  floor). Require the driver to re-clean the specific zones and re-inspect. List those zones in
  reclean_zones and tell the driver clearly and respectfully what to fix in notify_body.
- "escalate": send to a human supervisor when this is a REPEAT failure (the driver already
  re-inspected and it is still dirty), when there is an integrity concern, or when the evidence
  is genuinely ambiguous/low-confidence. Put the reason in escalate_reason.

Rules:
- Be decisive but fair. Do not reject for trivial dust. Do not approve visibly dirty seats/floor.
- On a RE-INSPECTION: compare to what was wrong before. If the previously-flagged zones are now
  clean, approve and acknowledge the fix. If still dirty after a re-clean, escalate (do not loop
  the driver endlessly).
- notify_title/notify_body are sent as a push to the driver: short, specific, human, actionable.
- Set priority "high" for escalations and repeat/severe cases, else "normal".
Return only the JSON object."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


def _render(ctx: dict) -> str:
    lines = [
        f"Vehicle: {ctx.get('vehicle')}  Driver: {ctx.get('driver')}",
        f"Overall cleanliness score: {ctx.get('overall_score')}/100 (confidence {ctx.get('overall_confidence')})",
        f"Driver's rejected inspections in the last 30 days: {ctx.get('recent_rejects', 0)}",
    ]
    if ctx.get("is_reinspection"):
        lines.append(f"THIS IS A RE-INSPECTION. Previously flagged: {ctx.get('prior_issues') or 'unknown'}")
    lines.append("Per-zone results:")
    for z in ctx.get("zones", []):
        iss = ", ".join(f"{i.get('issue_key')}({i.get('severity','?')})" for i in (z.get("issues") or [])) or "clean"
        lines.append(f"  - {z.get('zone_key')}: score {z.get('score')} — {iss}")
    lines.append("Decide the outcome and the actions.")
    return "\n".join(lines)


def _sanitize(out: dict) -> dict | None:
    if not isinstance(out, dict) or out.get("decision") not in ("approve", "reject", "escalate"):
        return None
    return {
        "decision": out["decision"],
        "reasoning": str(out.get("reasoning", ""))[:1000],
        "notify_title": str(out.get("notify_title", ""))[:120] or "Inspection update",
        "notify_body": str(out.get("notify_body", ""))[:300],
        "reclean_zones": [z for z in (out.get("reclean_zones") or []) if isinstance(z, str)],
        "escalate_reason": str(out.get("escalate_reason", ""))[:300],
        "priority": out.get("priority") if out.get("priority") in ("low", "normal", "high") else "normal",
    }


def decide(ctx: dict) -> dict | None:
    """Ask the supervisor agent for a decision + actions. Returns None on any failure so the
    caller can fall back to deterministic thresholds."""
    cfg = _cfg()
    key = cfg.get("groq_api_key")
    if not key:
        return None
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("groq_brain_model", "llama-3.3-70b-versatile")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _render(ctx)}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "decision", "schema": DECISION_SCHEMA}},
        "temperature": 0.2,
        "max_tokens": 700,
    }
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return _sanitize(json.loads(r.json()["choices"][0]["message"]["content"]))
    except Exception as err:  # noqa: BLE001 - brain is best-effort; caller falls back
        logger.warning("supervisor agent failed: %s", err)
        return None
