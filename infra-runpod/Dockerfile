# BluCheck VLM scoring worker for RunPod Serverless.
# vLLM serves Qwen3-VL; the handler enforces schema-constrained JSON output.
# Build and push to a registry RunPod can pull, then point the endpoint template at it.
# (Pin the vLLM image tag to a version that supports Qwen3-VL.)
FROM vllm/vllm-openai:latest

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/runpod-volume/hf \
    VLM_MODEL_ID=Qwen/Qwen3-VL-30B-A3B-Instruct-FP8

WORKDIR /app

# runpod SDK for the serverless handler loop. vLLM is already in the base image.
RUN pip install --no-cache-dir runpod==1.7.7

COPY schema.py prompt.py handler.py ./

# Serverless worker entrypoint (overrides the base image's OpenAI server).
ENTRYPOINT []
CMD ["python", "handler.py"]
