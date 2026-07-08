"""On-demand issue localization for a single frame: ask the VLM to detect cleanliness
issues with bounding boxes, then draw them on the frame and return an annotated JPEG.

Uses the same Groq vision model as the scoring stage (config from Secrets Manager).
Fails loudly (raises) so the API can return a clear error when inference is unavailable.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os

import boto3
import requests
from PIL import Image, ImageDraw, ImageFont

from .config import get_settings
from .storage import _s3  # reuse the regional S3 client

logger = logging.getLogger("blucheck.grounding")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)

RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")
GROUND_LONG_SIDE = 1280
TIMEOUT_S = 120

ISSUE_KEYS = ["trash", "stain", "dust", "smudge", "spill", "mud"]
# Normalized 0-1 boxes + a location phrase: far more reliable than absolute pixels across
# vision models (Llama-4 grounds much better this way than with pixel coordinates).
GROUND_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "detections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "issue_key": {"type": "string", "enum": ISSUE_KEYS},
                    "location": {"type": "string"},
                    "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                },
                "required": ["issue_key", "location", "box"],
            },
        }
    },
    "required": ["detections"],
}


class GroundingError(RuntimeError):
    pass


def _inference_config() -> dict:
    from botocore.exceptions import ClientError

    try:
        raw = _secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"]
    except ClientError as err:
        raise GroundingError(f"cannot read inference config: {err}") from err
    data = json.loads(raw)
    if not data.get("groq_api_key"):
        raise GroundingError("no Groq API key configured")
    return data


def _detect(image: Image.Image) -> list[dict]:
    w, h = image.size
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    prompt = (
        "Detect only genuinely visible cleanliness problems in this vehicle photo and localize "
        "each precisely. Issue types: dust (fine particulate film), mud (caked dirt), stain "
        "(set-in discoloration), trash (loose garbage), smudge (fingerprints/streaks on glass), "
        "spill (liquid residue). For each detection return: issue_key; a short 'location' phrase "
        "describing exactly where it is (e.g. 'driver-side floor mat', 'rear seat cushion', "
        "'lower windshield'); and box = [x1, y1, x2, y2] as FRACTIONS between 0 and 1 of the image "
        "(0,0 = top-left corner, 1,1 = bottom-right corner), drawn tightly around the dirt. Think "
        "about where the issue actually sits in the frame before giving the box. "
        "Do NOT box shadows, reflections or glare, a wet road or rain droplets, normal wear "
        "(scratches, faded paint), or seat patterns/textures. If the image is clean, return an "
        "empty detections list. Return JSON only."
    )
    body = {
        "model": None,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "g", "schema": GROUND_SCHEMA}},
        "temperature": 0,
        "max_tokens": 700,
    }
    cfg = _inference_config()
    body["model"] = cfg.get("groq_model", "meta-llama/llama-4-scout-17b-16e-instruct")
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['groq_api_key']}"},
        json=body,
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return json.loads(content).get("detections", [])


def annotate_frame(s3_key: str) -> bytes:
    """Fetch the frame, detect issues, draw boxes, return an annotated JPEG."""
    raw = _s3.get_object(Bucket=_settings.media_bucket, Key=s3_key)["Body"].read()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((GROUND_LONG_SIDE, GROUND_LONG_SIDE))  # resize long side, keep aspect
    w, h = img.size

    try:
        dets = _detect(img)
    except (requests.RequestException, KeyError, json.JSONDecodeError, GroundingError) as err:
        raise GroundingError(f"detection unavailable: {err}") from err

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=16)
    except Exception:
        font = ImageFont.load_default()
    for d in dets:
        box = d.get("box") or d.get("box_2d")  # tolerate the older key
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        # Expect fractions 0-1. If the model returned 0-1000 or pixels, normalize back to 0-1.
        m = max(abs(x1), abs(y1), abs(x2), abs(y2))
        if m > 1.5:
            div = 1000.0 if m <= 1000 else float(max(w, h))
            x1, y1, x2, y2 = x1 / div, y1 / div, x2 / div, y2 / div
        # order + clamp, then scale to the drawn image
        x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
        y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
        px1, py1, px2, py2 = int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)
        if px2 - px1 < 4 or py2 - py1 < 4:
            continue
        draw.rectangle([px1, py1, px2, py2], outline=(220, 90, 0), width=3)
        label = f"{d['issue_key']}: {d.get('location', d.get('label', ''))}"[:44]
        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.rectangle([px1, max(0, py1 - th - 6), px1 + tw + 6, py1], fill=(220, 90, 0))
        draw.text((px1 + 3, max(0, py1 - th - 4)), label, fill=(255, 255, 255), font=font)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    logger.info("annotated frame %s: %d detections", s3_key.rsplit("/", 1)[-1], len(dets))
    return out.getvalue()
