"""Agentic scoring stage.

Two-look inspection agent:
  1. OVERVIEW - right-size the selected frames (long side ~1280), number them, and ask the
     VLM for per-zone scores + issues, each issue tagged with the frame it is most visible in
     and a bounding box.
  2. ZOOM (adaptive) - for the zones the model was least certain about, crop the FULL-resolution
     frame around the issue and ask the model to take a second, close-up look and commit to a
     final assessment. Zoom only runs when there is genuine uncertainty, so clean, confident
     inspections cost a single pass; ambiguous ones get the extra look where small issues hide.

Fails closed (raises ScoringError) so the caller leaves the inspection to human review; never
guesses on malformed output. Only right-sized JPEG copies are sent, over TLS; full-resolution
frames stay in S3 and are only read locally to build crops. Config is read from Secrets Manager.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time

import boto3
import cv2
import requests
from jsonschema import validate as jsonschema_validate

import scoring_config as scoring_cfg
from vlm import ISSUE_KEYS, SCORING_SCHEMA, SEVERITY_KEYS, ZONE_KEYS, system_prompt, zoom_system_prompt

logger = logging.getLogger("blucheck.score")

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
# The AWS secret is still named "<prefix>/runpod" for backward compatibility; it now holds
# only the Groq config (groq_api_key, groq_base_url, groq_model). Env var name unchanged so
# the ECS task definition keeps working.
INFERENCE_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", "blucheck/runpod")
RESIZE_LONG_SIDE = int(os.environ.get("FRAME_RESIZE_LONG_SIDE", "1280"))
SCORE_TIMEOUT_S = int(os.environ.get("SCORE_TIMEOUT_S", "180"))
AGENTIC = os.environ.get("AGENTIC_SCORING", "1") == "1"
CROP_MAX_SIDE = int(os.environ.get("CROP_MAX_SIDE", "1024"))

# Scoring math (blend weight, zone weights, severity caps, borderline/zoom, image caps) is no
# longer hardcoded here: it comes from the active ModelVersion.scoring_config, merged over
# scoring_config.DEFAULTS. See scoring_config.py. Functions below take the resolved `cfg` dict.

_secrets = boto3.client("secretsmanager", region_name=AWS_REGION)


class ScoringError(RuntimeError):
    pass


def inference_config() -> dict:
    """Groq inference config from Secrets Manager. Groq exposes an OpenAI-compatible chat API,
    so scoring is a single base_url + api_key + model."""
    raw = _secrets.get_secret_value(SecretId=INFERENCE_SECRET_NAME)["SecretString"]
    data = json.loads(raw)
    if not data.get("groq_api_key"):
        raise ScoringError("no Groq API key configured (groq_api_key)")
    return data


def _model_id(cfg: dict, fallback: str) -> str:
    return cfg.get("groq_model") or fallback or "meta-llama/llama-4-scout-17b-16e-instruct"


# ---- image helpers ----
def rightsize_b64(frame_path: str) -> str | None:
    """Downscale a frame so its long side is RESIZE_LONG_SIDE, encode JPEG, base64."""
    img = cv2.imread(frame_path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(1.0, RESIZE_LONG_SIDE / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None


def crop_b64(frame_path: str, bbox, pad: float = 0.15) -> str | None:
    """Crop the FULL-resolution frame around a normalized [x,y,w,h] bbox (with padding) and
    encode as JPEG base64. Returns None on any bad input so a bad box just skips that zoom."""
    img = cv2.imread(frame_path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    try:
        x, y, bw, bh = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    x0 = int(max(0, (x - pad) * w))
    y0 = int(max(0, (y - pad) * h))
    x1 = int(min(w, (x + bw + pad) * w))
    y1 = int(min(h, (y + bh + pad) * h))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    crop = img[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    scale = min(1.0, CROP_MAX_SIDE / max(ch, cw))
    if scale < 1.0:
        crop = cv2.resize(crop, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _img(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


# ---- model call layer ----
_ZONE_FIELDS = {"zone_key", "score", "confidence", "issues"}
_ISSUE_FIELDS = {"issue_key", "severity", "description", "confidence", "frame_index", "bbox"}


def _sanitize(result: dict) -> dict:
    """Coerce the model output to our exact schema before validating. Managed APIs (e.g. Groq)
    enforce json_schema loosely: the model may return an out-of-taxonomy issue_key (e.g.
    'scratch'), omit severity, or add extra keys (e.g. a zone-level 'bbox'). Since the schema is
    strict (additionalProperties: False), we rebuild each object with only allowed, valid keys so
    a usable result is always produced instead of failing the whole inspection."""
    if not isinstance(result, dict):
        return result
    zones = []
    for z in result.get("zones") or []:
        if not isinstance(z, dict) or z.get("zone_key") not in ZONE_KEYS:
            continue
        issues = []
        for iss in z.get("issues") or []:
            if not isinstance(iss, dict) or iss.get("issue_key") not in ISSUE_KEYS:
                continue  # drop issues outside our fixed taxonomy
            clean = {k: iss[k] for k in _ISSUE_FIELDS if k in iss}
            if clean.get("severity") not in SEVERITY_KEYS:
                clean["severity"] = "moderate"
            if not isinstance(clean.get("confidence"), (int, float)):
                clean["confidence"] = 0.6
            clean.setdefault("description", clean["issue_key"])
            issues.append(clean)
        cz = {k: z[k] for k in _ZONE_FIELDS if k in z}
        if isinstance(cz.get("score"), (int, float)):
            cz["score"] = max(0, min(100, int(cz["score"])))
        if not isinstance(cz.get("confidence"), (int, float)):
            cz["confidence"] = 0.6
        cz["issues"] = issues
        zones.append(cz)
    return {
        # Default False (fail-safe): is_vehicle is a required schema field, so a missing value means
        # malformed output -- treat that as "not verified to be a vehicle" rather than waving it
        # through. A real car returns an explicit True.
        "is_vehicle": bool(result.get("is_vehicle", False)),
        "reasoning": result.get("reasoning") if isinstance(result.get("reasoning"), str) else "",
        "overall_score": result.get("overall_score"),
        "overall_confidence": result.get("overall_confidence"),
        "zones": zones,
    }


def _chat(cfg: dict, model_id: str, messages: list, schema: dict, max_tokens: int = 1600) -> dict:
    """Schema-constrained chat against Groq's OpenAI-compatible endpoint."""
    body = {
        "model": model_id,
        "messages": messages,
        "response_format": {"type": "json_schema", "json_schema": {"name": "scoring", "schema": schema}},
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['groq_api_key']}"},
        json=body,
        timeout=SCORE_TIMEOUT_S,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    result = _sanitize(json.loads(content))
    jsonschema_validate(result, schema)
    return result


def _with_retry(fn):
    last = None
    for attempt in range(2):  # retry once on malformed output
        try:
            return fn()
        except (json.JSONDecodeError, ScoringError, KeyError) as err:
            last = err
            logger.warning("model call attempt %d failed: %s", attempt + 1, err)
    raise ScoringError(f"model call failed after retries: {last}")


# ---- agentic stages ----
def _overview_call(paths: list[str], start_index: int, model_id: str, cfg: dict) -> dict:
    """Score one capture's frames. Frames are numbered from `start_index` so issue
    frame_index values are unique across the exterior and interior calls."""
    content = [_text("Assess this vehicle's cleanliness from these numbered frames.")]
    for j, p in enumerate(paths):
        b = rightsize_b64(p)
        if b:
            content.append(_text(f"Frame {start_index + j}:"))
            content.append(_img(b))
    if len(content) <= 1:
        raise ScoringError("no usable frames in capture")
    messages = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": content}]
    return _chat(cfg, model_id, messages, SCORING_SCHEMA)


def _attach_frame_ids(res: dict, frame_ids: list[str], start_index: int, kind: str) -> None:
    """Tag each detected issue with the real frame id it belongs to, resolved WITHIN this call.

    A capture is scored in its own call showing only that clip's frames, labelled start_index..
    So an interior issue can only ever resolve to an interior frame -- the model cannot make us
    point at an exterior photo for an interior problem (the failure the driver saw). If the
    model's frame_index lands outside this call's range (a hallucinated index), we fall back to
    the call's first frame and mark frame_exact=False so the UI can be honest about it."""
    for z in res.get("zones", []) or []:
        for iss in z.get("issues") or []:
            fi = iss.get("frame_index")
            local = (fi - start_index) if isinstance(fi, int) else None
            if local is not None and 0 <= local < len(frame_ids):
                iss["frame_id"] = frame_ids[local]
                iss["frame_exact"] = True
            elif frame_ids:
                iss["frame_id"] = frame_ids[0]
                iss["frame_exact"] = False
            iss["kind"] = kind


def _effective_zone_score(z: dict, scfg: dict) -> int | None:
    """Zone score after the severity floor: the worst issue's severity caps how high the zone
    can score. Only ever lowers a score, and only when the model was inconsistent (flagged a
    severe issue but scored the zone high). Caps come from scoring_config."""
    s = z.get("score")
    if s is None:
        return None
    sev_cap = scfg["severity_cap"]
    caps = [sev_cap[i["severity"]] for i in (z.get("issues") or []) if i.get("severity") in sev_cap]
    return min(s, min(caps)) if caps else s


def _finalize(merged: dict, reasons: list[str], scfg: dict) -> dict:
    """Assemble the final result from merged per-zone verdicts.

    Two policy adjustments over a plain average:
      1. Severity floor: each zone's score is capped by its worst issue's severity, so severity
         labels actually move the number (before, severity was collected but unused). The capped
         value is written back to the zone so the dashboard and the overall agree.
      2. Zone weighting: rider-facing zones count more, in both the average and the worst-zone
         term, so a dusty boot no longer hurts as much as a stained seat.
    Overall = 0.5 * weighted mean + 0.5 * weighted-worst zone. The model's own overall_score is
    intentionally not used; we derive it here for consistency and tunability. With uniform
    weights and no severity caps this reduces to the previous 0.5*mean + 0.5*min.
    """
    zone_weight = scfg["zone_weight"]
    blend = scfg["blend_mean_weight"]
    zones = list(merged.values())
    # Apply the severity floor in place so the stored/displayed zone score matches the overall.
    for z in zones:
        es = _effective_zone_score(z, scfg)
        if es is not None:
            z["score"] = es

    contribs = [(zone_weight.get(z.get("zone_key"), 1.0), z["score"]) for z in zones if z.get("score") is not None]
    if contribs:
        wsum = sum(w for w, _ in contribs)
        wmean = sum(w * s for w, s in contribs) / wsum
        # Weighted-worst: a low score in a high-weight zone counts as worse than the same score
        # in a low-weight zone. Clamped to 0; uniform weights reduce to plain min(score).
        worst = min(max(0.0, 100 - w * (100 - s)) for w, s in contribs)
        overall = round(blend * wmean + (1.0 - blend) * worst)
    else:
        overall = None

    confs = [z["confidence"] for z in zones if z.get("confidence") is not None]
    return {
        "reasoning": " | ".join(r for r in reasons if r)[:1200],
        "overall_score": overall,
        "overall_confidence": round(sum(confs) / len(confs), 2) if confs else None,
        "zones": zones,
    }


def _zoom_targets(result: dict, n_frames: int, scfg: dict) -> list[dict]:
    """Pick the least-certain zones that have a usable (frame_index, bbox) to crop. One per
    zone, capped at max_zooms. Uncertainty thresholds come from scoring_config."""
    zoom_conf = scfg["zoom_conf"]
    lo, hi = scfg["borderline"]
    max_zooms = scfg["max_zooms"]
    targets: list[dict] = []
    seen: set[str] = set()
    for z in result.get("zones", []):
        zk = z.get("zone_key")
        if zk in seen:
            continue
        zconf = z.get("confidence", 1.0) or 1.0
        score = z.get("score")
        borderline = score is not None and lo <= score <= hi
        for iss in z.get("issues") or []:
            fi, bb, iconf = iss.get("frame_index"), iss.get("bbox"), iss.get("confidence", 1.0) or 1.0
            uncertain = iconf < zoom_conf or zconf < zoom_conf or borderline
            if uncertain and isinstance(fi, int) and 1 <= fi <= n_frames and isinstance(bb, list) and len(bb) == 4:
                targets.append({"zone": zk, "frame_index": fi, "bbox": bb})
                seen.add(zk)
                break
        if len(targets) >= max_zooms:
            break
    return targets


def _zoom(flat_paths: list[str], targets: list[dict], merged: dict, model_id: str, cfg: dict, scfg: dict) -> dict | None:
    summary = "; ".join(f"{k}={z.get('score')}" for k, z in merged.items())
    content = [_text(
        f"Initial assessment (zone=score): {summary}. High-resolution close-ups of the uncertain "
        "zones follow; look carefully and give your final assessment of these zones."
    )]
    for t in targets[:scfg["max_images_per_call"]]:
        b = crop_b64(flat_paths[t["frame_index"] - 1], t["bbox"])
        if b:
            content.append(_text(f"Close-up of zone '{t['zone']}':"))
            content.append(_img(b))
    if len(content) <= 1:  # no crops could be built
        return None
    messages = [{"role": "system", "content": zoom_system_prompt()}, {"role": "user", "content": content}]
    return _chat(cfg, model_id, messages, SCORING_SCHEMA)


def _trimmed_mean(xs: list[float], frac: float) -> float | None:
    """Mean after dropping the `frac` most extreme values from each end. frac=0 => plain mean;
    a single value returns itself."""
    if not xs:
        return None
    s = sorted(xs)
    k = int(len(s) * frac)
    core = s[k: len(s) - k] if len(s) - 2 * k > 0 else s
    return sum(core) / len(core)


def _ensemble(zone_votes: list[dict], scfg: dict) -> dict:
    """Combine multiple per-zone verdicts (from the two captures and/or the zoom pass, or from
    per-frame calls) into one, so a single hallucinated detection can't flip a zone. Score is a
    trimmed mean across votes; confidence is the mean; issues come from the highest-confidence
    vote. Records the votes and an agreement count for auditability. A zone with a single vote is
    returned unchanged (trimmed mean of one value is itself)."""
    scores = [v["score"] for v in zone_votes if v.get("score") is not None]
    confs = [v["confidence"] for v in zone_votes if v.get("confidence") is not None]
    agg_score = _trimmed_mean(scores, scfg.get("ensemble_trim_frac", 0.0)) if scores else None
    best = max(zone_votes, key=lambda v: v.get("confidence") or 0.0)
    out = dict(best)  # base on the most confident vote (keeps zone_key + issues w/ frame_index)
    if agg_score is not None:
        out["score"] = round(agg_score)
    if confs:
        out["confidence"] = round(sum(confs) / len(confs), 2)
    # Agreement: how many votes land within 10 points of the aggregate (a variance signal).
    out["agreement"] = (
        sum(1 for s in scores if agg_score is not None and abs(s - agg_score) <= 10)
    )
    out["n_votes"] = len(zone_votes)
    out["votes"] = [
        {"score": v.get("score"), "confidence": v.get("confidence"), "source": v.get("source")}
        for v in zone_votes
    ]
    return out


def score_frames(
    frames_by_kind: dict[str, list[str]], vlm_model: str, scoring_config: dict | None = None,
    frame_ids_by_kind: dict[str, list[str]] | None = None,
) -> tuple[dict, int, dict]:
    """Score an inspection. Exterior and interior are scored in separate calls (each capped at
    scoring_config.max_images_per_call frames), merged per zone, then an adaptive high-res zoom
    refines the uncertain zones. `scoring_config` is the active ModelVersion's raw config (or
    None to use defaults). Returns (result_dict, latency_ms, stats) where stats carries the
    resolved config and the image count for auditability. Raises ScoringError on failure."""
    scfg = scoring_cfg.resolve(scoring_config)
    max_images = scfg["max_images_per_call"]
    per_frame = bool(scfg.get("ensemble_per_frame", False))
    cfg = inference_config()
    model_id = _model_id(cfg, vlm_model)
    started = time.time()
    image_count = 0

    # Collect one or more verdicts (votes) per zone, then ensemble, so a single call can't flip a
    # zone. Frames are numbered continuously so an issue's frame_index -> flat_paths is unique.
    flat_paths: list[str] = []
    flat_ids: list[str] = []
    votes: dict[str, list[dict]] = {}
    reasons: list[str] = []
    vehicle_votes: list[bool] = []
    idx = 1
    for kind in ("exterior", "interior"):
        ps = (frames_by_kind.get(kind) or [])[:max_images]
        if not ps:
            continue
        ids = ((frame_ids_by_kind or {}).get(kind) or [])[:max_images]
        # Default: one batched call per capture (1 vote/zone). ensemble_per_frame: one call per
        # frame (1 vote/zone/frame) -> stronger ensembling at ~N x the image cost, off by default.
        batches = [[p] for p in ps] if per_frame else [ps]
        for bi, batch in enumerate(batches):
            start_i = idx + (bi if per_frame else 0)
            res = _with_retry(lambda b=batch, si=start_i: _overview_call(b, si, model_id, cfg))
            image_count += len(batch)
            vehicle_votes.append(bool(res.get("is_vehicle", True)))
            reasons.append(res.get("reasoning", ""))
            # Tag issues with the real frame id, resolved within this call (see _attach_frame_ids).
            batch_ids = [ids[bi]] if per_frame and bi < len(ids) else ids
            _attach_frame_ids(res, batch_ids, start_i, kind)
            source = f"overview:{kind}" + (f":f{start_i}" if per_frame else "")
            for z in res.get("zones", []):
                zk = z.get("zone_key")
                if not zk:
                    continue
                v = dict(z)
                v["source"] = source
                votes.setdefault(zk, []).append(v)
        flat_paths += ps
        flat_ids += ids
        idx += len(ps)

    # Content gate: if ANY required capture does not show a vehicle (a room, a person, scenery, or
    # mismatched footage), the inspection does not fully show the car. Do not score cleanliness --
    # return a not_vehicle result the decision layer turns into a rejection. Rejecting on any
    # non-vehicle capture (not only when all of them are) closes the mixed-upload hole where a real
    # exterior is paired with non-vehicle interior footage. Checked before "no zones" so it can't stall.
    if vehicle_votes and not all(vehicle_votes):
        logger.info("content gate: a capture is not a vehicle; rejecting without cleanliness score")
        result = {
            "is_vehicle": False, "not_vehicle": True,
            "overall_score": None, "overall_confidence": None, "zones": [],
            "reasoning": " | ".join(r for r in reasons if r)[:500]
            or "One or more clips do not clearly show the vehicle.",
        }
        stats = {"image_count": image_count, "scoring_config": scfg, "not_vehicle": True, "per_frame": per_frame}
        return result, int((time.time() - started) * 1000), stats

    if not votes:
        raise ScoringError("no zones scored")

    if AGENTIC:
        preview = {zk: _ensemble(vs, scfg) for zk, vs in votes.items()}  # working summary for zoom
        targets = _zoom_targets({"zones": list(preview.values())}, len(flat_paths), scfg)
        if targets:
            try:
                refined = _zoom(flat_paths, targets, preview, model_id, cfg, scfg)
                if refined:
                    image_count += min(len(targets), max_images)
                    target_zones = {t["zone"] for t in targets}
                    tgt_by_zone = {t["zone"]: t for t in targets}
                    for z in refined.get("zones", []):
                        zk = z.get("zone_key")
                        if zk in target_zones:
                            # The zoom cropped a specific known frame; tag its issues with that
                            # frame id so a zoom-derived detection is also frame-accurate.
                            t = tgt_by_zone.get(zk) or {}
                            tfi = t.get("frame_index")
                            fid = flat_ids[tfi - 1] if isinstance(tfi, int) and 1 <= tfi <= len(flat_ids) else None
                            for iss in z.get("issues") or []:
                                if fid:
                                    iss["frame_id"] = fid
                                    iss["frame_exact"] = True
                            v = dict(z)
                            v["source"] = "zoom"
                            votes.setdefault(zk, []).append(v)  # zoom is another vote, not an override
                    reasons.append(refined.get("reasoning", ""))
                    logger.info("agentic zoom on zones=%s", list(target_zones))
            except (json.JSONDecodeError, ScoringError, KeyError, requests.RequestException) as err:
                logger.warning("zoom pass failed, using overview: %s", err)

    merged = {zk: _ensemble(vs, scfg) for zk, vs in votes.items()}
    latency_ms = int((time.time() - started) * 1000)
    stats = {
        "image_count": image_count,
        "scoring_config": scfg,
        "ensembled_zones": sum(1 for vs in votes.values() if len(vs) > 1),
        "per_frame": per_frame,
        "not_vehicle": False,
    }
    result = _finalize(merged, reasons, scfg)
    result["is_vehicle"] = True
    return result, latency_ms, stats
