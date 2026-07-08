# BluCheck VLM inference (RunPod)

Self-run vision-language scoring for BluCheck, in India. The AWS worker sends the
right-sized selected frames of an inspection to a RunPod endpoint and gets back
schema-constrained per-zone cleanliness scores.

## What is deployed

- RunPod Serverless endpoint `38n9yxvmebtbd5`, name `blucheck-vlm`, India only
  (`locations = AP-IN-1, AP-IN-2`), scale to zero, max 1 worker, official vLLM worker
  image `runpod/worker-v1-vllm:v2.22.5`, model `Qwen/Qwen3-VL-30B-A3B-Instruct-FP8`.
- The RunPod API key and endpoint id live in AWS Secrets Manager at `blucheck/runpod`.
  The worker task role can read only that secret.

## How scoring is called

The AWS worker (`worker/score.py`) calls the endpoint's OpenAI-compatible route with the
frames inline as base64 and enforces structured output. Key finding: with this vLLM
build, structured JSON must use `response_format: {type: "json_schema", json_schema: ...}`,
not the older `guided_json`. Every response is validated against `schema.py`; on malformed
output the worker retries once then fails closed to human review.

Only right-sized JPEG copies (long side ~1280) are sent, over TLS. Full-resolution PNG
frames never leave S3.

## Region and privacy

- Inference runs only in India (`AP-IN-1` / `AP-IN-2`). `deploy.py` refuses any non-India
  data center.
- The handler and worker log only ids, timings, and score summaries, never image contents,
  secrets, or the API key.

## Capacity note (important)

India serverless GPU capacity for 30B-class pools is intermittent. When no worker can be
allocated, a scoring job stays queued; the worker times out (`SCORE_TIMEOUT_S`, default
180s), cancels the job, and leaves the inspection for human review (fail closed). This is
correct shadow-mode behavior. For development and prompt tuning, run a persistent Pod on an
H100 in `AP-IN-1` (`create_pod` in the SDK), which is reliably in stock; tear it down after.

## Files

- `schema.py` - fixed output JSON schema + taxonomy (source of truth).
- `prompt.py` - versioned system prompt including the zone/issue taxonomy.
- `handler.py` - custom RunPod handler (vLLM offline) for a fully custom image path.
- `Dockerfile` - builds that custom handler image (needs a registry to push to).
- `deploy.py` - creates a network volume + template + endpoint, India-gated.

## Cost knobs

- Scale to zero (`workers_min = 0`): no GPU cost when idle; pay per second while scoring.
- `workers_max`: caps concurrency and spend.
- `idle_timeout`: how long a warm worker lingers after the last job.
- Batch: all selected frames of one inspection go in a single request.
- Frame right-size (`FRAME_RESIZE_LONG_SIDE`) and selection top-N control tokens per call.

## Enabling automation (only after reviewing shadow agreement)

Default mode is `shadow` (scores only, no action). In the dashboard Model page an admin can
move to `assist`, then `auto`, tune the `auto_approve` / `auto_reject` thresholds, and hit
`disabled` (the kill switch) to revert to human-only instantly. Every mode and threshold
change is audited.
