# BluCheck

**Autonomous fleet vehicle cleanliness inspection.** Drivers record a short walk‑around and interior clip of their vehicle from a mobile app; the platform extracts full‑resolution frames, scores cleanliness with a vision‑language model, and an autonomous agent decides — approve, reject, or escalate — and notifies the driver. A web dashboard gives operators full oversight, tuning, and audit.

Built for a 100+ vehicle fleet operating in India (region `ap‑south‑1`), with data encrypted at rest, least‑privilege IAM, and a full audit trail.

---

## What it does

- **Driver app (React Native / Expo).** Sign in by scanning your number plate + a 4‑digit PIN. Record a guided exterior + interior inspection; the app checks it's actually a vehicle before uploading, reads the plate automatically, and shows live analysis progress. Rejections come back with a re‑clean checklist and the flagged photos.
- **Frame extraction worker.** Pulls each uploaded clip from S3, extracts full‑resolution JPEG frames with embedded GPS/timestamp metadata, selects the sharpest zone‑diverse frames, and hands them to scoring. Idempotent and horizontally autoscaled on queue depth (scale‑to‑zero when idle).
- **Vision scoring.** A vision model scores six vehicle zones (exterior body, windows/glass, seats, floor mats, dashboard/console, boot) against a fixed rubric, with adaptive high‑resolution zoom on uncertain areas and cross‑frame ensembling to damp single‑frame hallucinations. A content gate first verifies the frames show a real vehicle — non‑vehicle footage is rejected, never scored.
- **Autonomous decision agent.** A separate decision layer maps scores to approve / reject / escalate under an explicit autonomy ladder (`shadow → assist → auto`, plus a `disabled` kill switch). It is gated on *calibrated* confidence by default, with an opt‑in **Full Autonomy** mode for fully unattended operation. Every decision is audited; the driver is notified automatically.
- **Operator dashboard (Next.js).** Review queue, per‑vehicle and fleet trends, daily compliance, a full audit log, and a model page to tune the scoring math, build calibration curves, run the validation harness, and monitor drift and fairness.

---

## Architecture

```
 ┌─────────────┐   presigned    ┌──────────┐   S3 event    ┌───────────────┐
 │  Mobile app │─ multipart ───▶│    S3    │──────────────▶│  SQS (extract)│
 │ (Expo / RN) │   upload       │  media   │               └──────┬────────┘
 └─────────────┘                └──────────┘                      │ long‑poll
        │  create inspection                                      ▼
        │  (plate OCR, GPS)                              ┌──────────────────┐
        ▼                                                │  Frame worker    │
 ┌─────────────┐   REST / JWT   ┌──────────────┐         │ (ffmpeg + select │
 │  FastAPI    │◀──────────────▶│  RDS Postgres│◀────────│  + VLM scoring)  │
 │  backend    │                │ (SQLAlchemy) │         └────────┬─────────┘
 └──────┬──────┘                └──────────────┘                  │ delegate
        │                                                         ▼
        │  agent decision, thresholds,                   ┌──────────────────┐
        │  calibration, notifications                    │ Decision agent   │
        │                                                │ (bands + LLM sup)│
        ▼                                                └──────────────────┘
 ┌─────────────┐   static / CDN
 │  Dashboard  │  (CloudFront)
 │  (Next.js)  │
 └─────────────┘
```

**Scoring is decoupled from deciding on purpose:** the worker produces a score and stores it; the backend is the single place that applies the mode/thresholds and acts. Vision inference is served by an OpenAI‑compatible API; the model config lives in AWS Secrets Manager.

---

## Tech stack

| Layer | Stack |
|---|---|
| Mobile | Expo / React Native, expo‑camera, expo‑file‑system, FCM push |
| Backend API | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Worker | Python, ffmpeg, OpenCV, perceptual‑hash frame selection |
| Vision + agent | Vision‑language scoring, LLM supervisor, isotonic confidence calibration |
| Data | PostgreSQL (RDS), S3 (SSE‑KMS), SQS |
| Web | Next.js (static export), CloudFront |
| Infra | AWS ECS Fargate, ALB, Terraform, CloudWatch |

---

## Repository layout

```
backend/     FastAPI API: auth, inspections, review, metrics, model/agent control
worker/      SQS-driven frame extraction + VLM scoring
mobile/      Expo driver app
dashboard/   Next.js operator dashboard (static export)
infra/       Terraform: VPC, RDS, S3, SQS, ECS, ALB, CloudFront, IAM, KMS
scripts/     Build/deploy helpers (images via CodeBuild, dashboard sync, seed)
Makefile     Common targets
```

---

## Getting started

### Prerequisites
- AWS account + credentials (region `ap-south-1`), Terraform, Docker (or CodeBuild), Node 20+, Python 3.12+, `ffmpeg`, `exiftool`.
- An OpenAI‑compatible vision inference endpoint + key (stored in Secrets Manager).
- A Firebase project for push notifications (service account in Secrets Manager; `google-services.json` in `mobile/`).

### Provision infrastructure
```bash
cd infra
terraform init
terraform plan      # review
terraform apply
```

### Deploy services
```bash
# from repo root
./scripts/seed.sh              # run migrations + create the admin user
./scripts/deploy-backend.sh    # build API image -> ECR -> ECS
./scripts/deploy-worker.sh     # build worker image -> ECR -> ECS
./scripts/deploy-dashboard.sh  # build + sync to S3 + invalidate CloudFront
```

### Build the mobile app
```bash
cd mobile
npm install
# Configure the API base URL + google-services.json, then build a release with Gradle/EAS.
```

Secrets (DB URL, JWT signing key, inference config, FCM service account) are read from AWS Secrets Manager at runtime — none are committed. Terraform state is git‑ignored.

---

## Autonomy & safety

BluCheck is designed so autonomy is **earned, not assumed**:

- **`shadow`** (default): the agent scores silently; humans decide everything.
- **`assist`**: the agent recommends; a human confirms with one click.
- **`auto`**: the agent decides, but only where its **calibrated** confidence clears an admin‑set floor — otherwise it routes to a human. With no calibration, it fails safe to human review.
- **Full Autonomy** (opt‑in): fully unattended — every valid inspection is decided by the agent with no human. Uncertain cases default to *reject* (re‑clean).
- **`disabled`**: instant kill switch — all agent activity stops.

The **validation harness** (agreement, confusion matrix, per‑zone precision/recall, false‑approve/reject rates, threshold sweep) and **drift + fairness monitors** are the instruments used to decide when it's safe to advance up the ladder. Non‑vehicle content is rejected by a content gate before any cleanliness judgment is made.

---

## Data & compliance

- Media is encrypted at rest (SSE‑KMS); media buckets are never public — the app and dashboard only ever receive short‑lived signed URLs.
- Least‑privilege IAM: each task role can touch only its own prefixes, queue, and secrets.
- Every review and agent decision writes to an append‑only audit log.
- Configurable retention on raw clips and frames for data minimisation.

---

## License

Proprietary — © BluRabbit Mobility. All rights reserved.
