"""RunPod Serverless handler: self-run Qwen3-VL via vLLM with schema-constrained output.

Request shape:
  {
    "input": {
      "inspection_id": "<uuid>",
      "frames": [{"frame_id": "<uuid>", "zone_hint": "seats"|null, "image_b64": "<jpeg b64>"}],
      "taxonomy": {"zones": [...], "issues": [...]}   # optional, informational
    }
  }

Response: the validated scoring JSON plus latency. The handler never persists frames and
never logs image contents; it logs only ids, timings, and score summaries.
"""

from __future__ import annotations

import json
import logging
import os
import time

import runpod
from vllm import LLM, SamplingParams
from vllm.sampling_params import GuidedDecodingParams

from prompt import system_prompt
from schema import PROMPT_VERSION, SCORING_SCHEMA

logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","msg":"%(message)s"}')
logger = logging.getLogger("blucheck.vlm")

MODEL_ID = os.environ.get("VLM_MODEL_ID", "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8")
MAX_MODEL_LEN = int(os.environ.get("VLM_MAX_MODEL_LEN", "32768"))
MAX_FRAMES = int(os.environ.get("VLM_MAX_FRAMES", "12"))
GPU_MEM_FRACTION = float(os.environ.get("VLM_GPU_MEM_FRACTION", "0.92"))

# Load the model once per worker (cold start). Weights come from the mounted network
# volume so warm starts are fast.
logger.info(f"loading model {MODEL_ID}")
_llm = LLM(
    model=MODEL_ID,
    trust_remote_code=True,
    max_model_len=MAX_MODEL_LEN,
    gpu_memory_utilization=GPU_MEM_FRACTION,
    limit_mm_per_prompt={"image": MAX_FRAMES},
)
_guided = GuidedDecodingParams(json=SCORING_SCHEMA)
_sampling = SamplingParams(temperature=0.0, max_tokens=1536, guided_decoding=_guided)
_SYSTEM = system_prompt()


def _build_messages(frames: list[dict]) -> list[dict]:
    content: list[dict] = [
        {
            "type": "text",
            "text": "Assess the cleanliness of this vehicle from the following frames. "
            "Return one JSON object matching the required schema.",
        }
    ]
    for f in frames[:MAX_FRAMES]:
        hint = f.get("zone_hint")
        if hint:
            content.append({"type": "text", "text": f"(frame likely shows zone: {hint})"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{f['image_b64']}"}}
        )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": content},
    ]


def _score(frames: list[dict]) -> dict:
    messages = _build_messages(frames)
    outputs = _llm.chat(messages, _sampling)
    text = outputs[0].outputs[0].text
    return json.loads(text)  # guided decoding guarantees valid JSON against the schema


def handler(job: dict) -> dict:
    started = time.time()
    data = job.get("input", {}) or {}
    inspection_id = data.get("inspection_id", "unknown")
    frames = data.get("frames") or []
    if not frames:
        return {"error": "no frames provided", "inspection_id": inspection_id}

    try:
        result = _score(frames)
    except Exception as err:  # noqa: BLE001 - report failure so the caller fails closed
        logger.error(f"scoring failed inspection={inspection_id}: {err}")
        return {"error": f"scoring failed: {err}", "inspection_id": inspection_id}

    latency_ms = int((time.time() - started) * 1000)
    # Log only ids, timing, and a summary. Never log image contents.
    logger.info(
        f"scored inspection={inspection_id} frames={len(frames)} "
        f"overall={result.get('overall_score')} conf={result.get('overall_confidence')} "
        f"latency_ms={latency_ms}"
    )
    return {
        "inspection_id": inspection_id,
        "prompt_version": PROMPT_VERSION,
        "model_id": MODEL_ID,
        "latency_ms": latency_ms,
        "result": result,
    }


runpod.serverless.start({"handler": handler})
