"""Synchronous cleanliness scorer for the public /v1/score endpoint.

Takes 1-5 vehicle images (base64 or URL), runs a single Groq vision call to score each cleanliness
zone, then applies the active policy's zone weights / severity caps / thresholds (via scoring_math)
to produce an overall score and a clean/dirty/review decision. This mirrors the worker's scoring
math so the public API agrees with the fleet's own decisions. Raises ScoreError on failure.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import socket
from urllib.parse import urlparse

import boto3
import requests

from . import scoring_defaults, scoring_math
from .config import get_settings
from .models import ISSUE_KEYS, ZONE_KEYS

logger = logging.getLogger("blucheck.scoring_api")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")
TIMEOUT_S = 40
SEVERITIES = ["minor", "moderate", "severe"]

SCORING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_vehicle": {"type": "boolean"},
        "zones": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "zone_key": {"type": "string", "enum": list(ZONE_KEYS)},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "confidence": {"type": "number"},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "issue_key": {"type": "string", "enum": list(ISSUE_KEYS)},
                                "severity": {"type": "string", "enum": SEVERITIES},
                                "description": {"type": "string"},
                            },
                            "required": ["issue_key", "severity"],
                        },
                    },
                },
                "required": ["zone_key", "score"],
            },
        },
    },
    "required": ["is_vehicle", "zones"],
}

SYSTEM = (
    "You assess the cleanliness of a passenger vehicle from photos. First decide is_vehicle: true "
    "only if the images show a car/van. Then score each visible zone 0-100 (100 = spotless): "
    "seats, floor_mats, dashboard_console, windows_glass, exterior_body, boot. For each zone list "
    "the genuinely visible issues (trash, stain, dust, smudge, spill, mud) with a severity "
    "(minor/moderate/severe). Ignore shadows, reflections, glare, wet road, and normal wear. Only "
    "include zones you can actually see. Output JSON only."
)


class ScoreError(RuntimeError):
    pass


def _cfg() -> dict:
    data = json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])
    if not data.get("groq_api_key"):
        raise ScoreError("no Groq API key configured")
    return data


def _assert_public_url(url: str) -> None:
    """SSRF guard: reject URLs that resolve to a non-public address (loopback, private, link-local,
    the cloud metadata endpoint, etc.). Every resolved IP for the host must be global — so a hostname
    that resolves to a mix of public and internal addresses is refused, not just the first result."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ScoreError("image URL must be http(s)")
    host = parsed.hostname
    if not host:
        raise ScoreError("image URL has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as err:
        raise ScoreError(f"could not resolve image host: {err}") from err
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_reserved:
            raise ScoreError("image URL resolves to a non-public address")


def _to_b64(image: str) -> str:
    """Accept a base64 string (optionally a data: URL) or fetch an http(s) URL and return base64."""
    s = image.strip()
    if s.startswith("http://") or s.startswith("https://"):
        _assert_public_url(s)
        # A browser-like UA so common image hosts/CDNs don't 403 the default client. Redirects are
        # disabled so a public URL cannot 30x-bounce us into an internal address after the check.
        r = requests.get(
            s, timeout=15, allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BluCheck/1.0)"},
        )
        r.raise_for_status()
        return base64.b64encode(r.content).decode("ascii")
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    return s


def score(images: list[str], scoring_config: dict | None, thresholds: dict | None, vlm_model: str | None = None) -> dict:
    """Score images against the active policy. Returns
    {is_vehicle, overall_score, decision, zones:[{zone_key, score, issues}]}."""
    cfg = _cfg()
    # Match the worker's model resolution: explicit groq_model, else the active version's vlm_model.
    model = cfg.get("groq_model") or vlm_model or "meta-llama/llama-4-scout-17b-16e-instruct"
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    content: list[dict] = [{"type": "text", "text": "Assess this vehicle's cleanliness from these photos."}]
    for img in images[:5]:
        try:
            b64 = _to_b64(img)
        except Exception as err:  # noqa: BLE001
            raise ScoreError(f"could not read an image: {err}") from err
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "score", "schema": SCORING_SCHEMA}},
        "temperature": 0,
        "max_tokens": 1200,
    }
    try:
        r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {cfg['groq_api_key']}"}, json=body, timeout=TIMEOUT_S)
        if r.status_code >= 300:
            logger.warning("groq score %s: %s", r.status_code, r.text[:400])
            raise ScoreError(f"model call {r.status_code} ({model}): {r.text[:200]}")
        raw = json.loads(r.json()["choices"][0]["message"]["content"])
    except ScoreError:
        raise
    except Exception as err:  # noqa: BLE001
        raise ScoreError(f"scoring failed: {err}") from err

    # Fail-safe: is_vehicle is a required schema field, so a missing value is malformed output;
    # default it to False rather than scoring unverified content as a vehicle.
    is_vehicle = bool(raw.get("is_vehicle", False))
    scfg = scoring_defaults.resolve(scoring_config)
    if not is_vehicle:
        return {"is_vehicle": False, "overall_score": None, "decision": "review", "zones": []}

    zones = []
    for z in raw.get("zones") or []:
        if z.get("zone_key") not in ZONE_KEYS:
            continue
        issues = [i for i in (z.get("issues") or []) if i.get("issue_key") in ISSUE_KEYS]
        zones.append({"zone_key": z["zone_key"], "score": z.get("score"), "issues": issues})
    # Apply the severity floor per zone so the displayed score matches the overall.
    for z in zones:
        z["score"] = scoring_math.effective_zone_score(z.get("score"), z.get("issues"), scfg["severity_cap"])
    overall = scoring_math.overall_from_zones(zones, scfg)

    ov = (thresholds or {}).get("overall", {})
    approve = ov.get("auto_approve", 85)
    reject = ov.get("auto_reject", 40)
    if overall is None:
        decision = "review"
    elif overall >= approve:
        decision = "clean"
    elif overall <= reject:
        decision = "dirty"
    else:
        decision = "review"
    return {"is_vehicle": True, "overall_score": overall, "decision": decision, "zones": zones}
