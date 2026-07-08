"""Create the BluCheck RunPod Serverless endpoint, pinned to the India data center.

Creates (idempotently, by name): a network volume for the model weights, a serverless
template pointing at the worker image, and a serverless endpoint with bounded workers and
scale to zero. Prints the endpoint id and URL.

Region safety: refuses to run unless the data center id is in India (AP-IN-*). This
enforces the hard constraint that inference never leaves India.

Usage:
  export RUNPOD_API_KEY=...            # or rely on ~/.runpod/config.toml
  python deploy.py --image <registry/blucheck-vlm:tag> --data-center AP-IN-1
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

import requests

API_URL = "https://api.runpod.io/graphql"


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        cfg = Path.home() / ".runpod" / "config.toml"
        if cfg.exists():
            key = tomllib.loads(cfg.read_text()).get("apikey")
    if not key:
        sys.exit("RunPod API key not found (set RUNPOD_API_KEY or ~/.runpod/config.toml)")
    return key


def _gql(query: str, variables: dict, key: str) -> dict:
    resp = requests.post(
        API_URL,
        params={"api_key": key},
        json={"query": query, "variables": variables},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="worker image, e.g. docker.io/you/blucheck-vlm:v1")
    ap.add_argument("--data-center", default="AP-IN-1", help="RunPod data center id (must be India)")
    ap.add_argument("--name", default="blucheck-vlm")
    ap.add_argument("--gpu", default="NVIDIA L40S", help="GPU type for the workers")
    ap.add_argument("--volume-gb", type=int, default=60)
    ap.add_argument("--min-workers", type=int, default=0)  # scale to zero
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--model-id", default="Qwen/Qwen3-VL-30B-A3B-Instruct-FP8")
    args = ap.parse_args()

    # Hard region gate: India only.
    if not args.data_center.upper().startswith("AP-IN"):
        sys.exit(f"REFUSING: data center {args.data_center} is not in India (AP-IN-*)")

    key = _api_key()

    # 1. Network volume for weights (India).
    vol = _gql(
        """
        mutation($input: CreateNetworkVolumeInput!) {
          createNetworkVolume(input: $input) { id name dataCenterId size }
        }
        """,
        {"input": {"name": f"{args.name}-weights", "size": args.volume_gb, "dataCenterId": args.data_center}},
        key,
    )["createNetworkVolume"]
    print(f"network volume: {vol['id']} ({vol['dataCenterId']}, {vol['size']}GB)")

    # 2. Serverless template pointing at the worker image.
    tmpl = _gql(
        """
        mutation($input: SaveTemplateInput!) {
          saveTemplate(input: $input) { id name imageName }
        }
        """,
        {
            "input": {
                "name": f"{args.name}-template",
                "imageName": args.image,
                "isServerless": True,
                "containerDiskInGb": 20,
                "volumeMountPath": "/runpod-volume",
                "env": [
                    {"key": "VLM_MODEL_ID", "value": args.model_id},
                    {"key": "HF_HOME", "value": "/runpod-volume/hf"},
                ],
            }
        },
        key,
    )["saveTemplate"]
    print(f"template: {tmpl['id']}")

    # 3. Serverless endpoint (bounded workers, scale to zero), pinned to India + volume.
    ep = _gql(
        """
        mutation($input: EndpointInput!) {
          saveEndpoint(input: $input) { id name }
        }
        """,
        {
            "input": {
                "name": args.name,
                "templateId": tmpl["id"],
                "gpuIds": args.gpu,
                "networkVolumeId": vol["id"],
                "dataCenterIds": args.data_center,
                "workersMin": args.min_workers,
                "workersMax": args.max_workers,
                "idleTimeout": 30,
                "scalerType": "QUEUE_DELAY",
                "scalerValue": 4,
            }
        },
        key,
    )["saveEndpoint"]
    print(f"endpoint id: {ep['id']}")
    print(f"endpoint url: https://api.runpod.ai/v2/{ep['id']}/runsync")
    print("Store the endpoint id in AWS Secrets Manager (blucheck/runpod) for the worker.")


if __name__ == "__main__":
    main()
