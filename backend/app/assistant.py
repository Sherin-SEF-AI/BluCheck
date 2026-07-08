"""Agentic admin assistant.

Not a generic chatbot: the admin asks in plain English and the assistant uses TOOLS to look up
real fleet data and to take actions (cadence, mode, thresholds, notifications, PIN reset, ...).

Safety model: READ tools run inline. WRITE tools are NEVER executed by the model -- when the model
calls one it is only a PROPOSAL. ask() returns the proposed actions to the client, which shows a
Confirm card; on confirm the client calls execute(), which runs the single whitelisted action.
So every state change requires an explicit human click. Page context lets "this inspection" resolve
without the admin repeating an id.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import boto3
import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import push
from .auth import hash_password
from .config import get_settings
from .models import Inspection, ModelVersion, Review, ScoringResult, User, Vehicle, ZoneScore

logger = logging.getLogger("blucheck.assistant")
_settings = get_settings()
_secrets = boto3.client("secretsmanager", region_name=_settings.aws_region)
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", f"{_settings.resource_prefix}/runpod")
IST = timezone(timedelta(hours=5, minutes=30))

READ_TOOLS = {"fleet_overview", "overdue_vehicles", "model_status", "driver_stats", "recent_inspections", "inspection_lookup"}
WRITE_TOOLS = {
    "set_cadence", "send_overdue_reminders", "set_mode", "reset_driver_pin", "send_notification",
    "set_thresholds", "activate_policy", "toggle_full_autonomy", "reprocess_inspection", "set_driver_active",
}

TOOLS = [
    {"type": "function", "function": {"name": "fleet_overview", "description": "Fleet snapshot: inspection counts by status, pass rate, overdue count, agent mode, active policy, cadence.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "overdue_vehicles", "description": "List vehicles whose driver has not passed an inspection within the cadence window.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "model_status", "description": "Agent decision settings: mode, thresholds, cadence hours, full autonomy, human-override count.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "driver_stats", "description": "Look up one driver by car number or name: totals, pass rate, recurring flagged areas.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "recent_inspections", "description": "Recent inspections, optionally filtered by status.", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "approved", "rejected", "failed"]}, "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "inspection_lookup", "description": "Details of one inspection by its id (use the current inspection id when the admin says 'this inspection') or the latest for a plate.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "plate": {"type": "string"}}}}},
    # ---- write / action tools (proposal only; require confirmation) ----
    {"type": "function", "function": {"name": "set_cadence", "description": "ACTION: set the required inspection cadence in hours.", "parameters": {"type": "object", "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 8760}}, "required": ["hours"]}}},
    {"type": "function", "function": {"name": "send_overdue_reminders", "description": "ACTION: push a reminder to every overdue driver now.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_mode", "description": "ACTION: set agent mode (shadow, assist, auto, disabled).", "parameters": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["shadow", "assist", "auto", "disabled"]}}, "required": ["mode"]}}},
    {"type": "function", "function": {"name": "reset_driver_pin", "description": "ACTION: reset a driver's 4-digit login PIN to a new random one.", "parameters": {"type": "object", "properties": {"car_number": {"type": "string"}}, "required": ["car_number"]}}},
    {"type": "function", "function": {"name": "send_notification", "description": "ACTION: send a push notification. audience: 'driver' (needs car_number), 'all_active', 'overdue', or 'rejected_today'.", "parameters": {"type": "object", "properties": {"audience": {"type": "string", "enum": ["driver", "all_active", "overdue", "rejected_today"]}, "car_number": {"type": "string"}, "title": {"type": "string"}, "message": {"type": "string"}}, "required": ["audience", "title", "message"]}}},
    {"type": "function", "function": {"name": "set_thresholds", "description": "ACTION: set the auto-approve and auto-reject score thresholds (0-100).", "parameters": {"type": "object", "properties": {"auto_approve": {"type": "integer"}, "auto_reject": {"type": "integer"}}, "required": ["auto_approve", "auto_reject"]}}},
    {"type": "function", "function": {"name": "activate_policy", "description": "ACTION: activate a saved cleanliness policy by name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "toggle_full_autonomy", "description": "ACTION: turn full autonomy on or off.", "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}}},
    {"type": "function", "function": {"name": "reprocess_inspection", "description": "ACTION: reprocess/re-score an inspection by id or latest for a plate.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "plate": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "set_driver_active", "description": "ACTION: enable or disable a driver's account by car number.", "parameters": {"type": "object", "properties": {"car_number": {"type": "string"}, "active": {"type": "boolean"}}, "required": ["car_number", "active"]}}},
]

SYSTEM = """You are BluCheck's fleet operations assistant for a fleet admin. BluCheck is an
autonomous vehicle-cleanliness inspection platform.

Rules:
- ALWAYS use tools for real data; never invent numbers, names, or statuses.
- ACTION tools do NOT execute -- calling one PROPOSES it, and the admin then confirms with a
  button. When you propose an action, tell them in ONE line what will happen; do not claim it is
  done.
- Formatting: lead with the answer, NO preamble. Use tight GitHub-flavored markdown: an optional
  bold header, then <=6 short bullets, key numbers in **bold**. Use a table only for a real
  row-by-row comparison. One-line confirmations. Never show raw JSON or tool names."""


def _cfg() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])


# ---------- read tools ----------
def _pass_rate(a: int, decided: int) -> str:
    return f"{round(100 * a / decided)}%" if decided else "n/a"


def _fleet_overview(db: Session) -> dict:
    counts = {s: c for s, c in db.execute(select(Inspection.status, func.count()).group_by(Inspection.status)).all()}
    decided = counts.get("approved", 0) + counts.get("rejected", 0)
    mv = db.execute(select(ModelVersion).where(ModelVersion.active.is_(True))).scalar_one_or_none()
    active_policy = None
    if mv and isinstance(mv.scoring_config, dict):
        pid = mv.scoring_config.get("_active_policy")
        for p in (mv.scoring_config.get("_policies") or []):
            if isinstance(p, dict) and p.get("id") == pid:
                active_policy = p.get("name")
    from .routers.metrics import _cadence_hours, _overdue_rows

    _, overdue = _overdue_rows(db)
    return {"counts_by_status": counts, "pass_rate": _pass_rate(counts.get("approved", 0), decided),
            "overdue_vehicles": len(overdue), "agent_mode": mv.mode if mv else None,
            "active_policy": active_policy, "cadence_hours": _cadence_hours(db)}


def _overdue_vehicles(db: Session) -> dict:
    from .routers.metrics import _overdue_rows

    cadence, rows = _overdue_rows(db)
    return {"cadence_hours": cadence, "count": len(rows), "vehicles": [
        {"plate": r["plate"], "driver": r["name"], "severity": r["severity"], "never_passed": r["never"], "hours_overdue": r["hours_overdue"]}
        for r in rows[:25]]}


def _model_status(db: Session) -> dict:
    from .routers.metrics import _cadence_hours

    mv = db.execute(select(ModelVersion).where(ModelVersion.active.is_(True))).scalar_one_or_none()
    if mv is None:
        return {"error": "no active model"}
    overrides = db.execute(select(func.count()).where(Review.source == "model_overridden")).scalar_one()
    return {"mode": mv.mode, "thresholds": (mv.thresholds or {}).get("overall", {}), "cadence_hours": _cadence_hours(db),
            "full_autonomy": bool((mv.thresholds or {}).get("full_autonomy")), "human_overrides_total": overrides}


def _find_driver(db: Session, query: str) -> User | None:
    q = (query or "").strip()
    u = db.execute(select(User).where(User.car_number.ilike(q), User.role == "driver")).scalars().first()
    return u or db.execute(select(User).where(User.name.ilike(f"%{q}%"), User.role == "driver")).scalars().first()


def _driver_stats(db: Session, query: str) -> dict:
    d = _find_driver(db, query)
    if d is None:
        return {"error": f"No driver found for '{query}'."}
    insps = db.execute(select(Inspection).where(Inspection.driver_id == d.id)).scalars().all()
    approved = sum(1 for i in insps if i.status == "approved")
    rejected = [i for i in insps if i.status == "rejected"]
    zone_counts: dict[str, int] = {}
    for i in rejected:
        sr = db.execute(select(ScoringResult).where(ScoringResult.inspection_id == i.id).order_by(ScoringResult.created_at.desc()).limit(1)).scalar_one_or_none()
        if sr:
            for z in db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id == sr.id)).scalars():
                if z.issues:
                    zone_counts[z.zone_key] = zone_counts.get(z.zone_key, 0) + 1
    return {"driver": d.name, "car_number": d.car_number, "active": d.active, "total_inspections": len(insps),
            "approved": approved, "rejected": len(rejected), "pass_rate": _pass_rate(approved, approved + len(rejected)),
            "recurring_flagged_zones": dict(sorted(zone_counts.items(), key=lambda kv: -kv[1]))}


def _recent_inspections(db: Session, status: str | None = None, limit: int = 10) -> dict:
    stmt = select(Inspection).order_by(Inspection.created_at.desc()).limit(min(int(limit or 10), 25))
    if status:
        stmt = stmt.where(Inspection.status == status)
    out = []
    for i in db.execute(stmt).scalars().all():
        v = db.get(Vehicle, i.vehicle_id)
        out.append({"plate": v.registration_plate if v else "?", "status": i.status,
                    "created_at": i.created_at.isoformat() if i.created_at else None, "reject_reason": (i.reject_reason or "")[:160] or None})
    return {"count": len(out), "inspections": out}


def _inspection_lookup(db: Session, id: str | None = None, plate: str | None = None) -> dict:
    insp = None
    if id:
        try:
            insp = db.get(Inspection, uuid.UUID(id))
        except (ValueError, TypeError):
            insp = None
    if insp is None and plate:
        v = db.execute(select(Vehicle).where(Vehicle.registration_plate.ilike(plate.strip()))).scalars().first()
        if v:
            insp = db.execute(select(Inspection).where(Inspection.vehicle_id == v.id).order_by(Inspection.created_at.desc()).limit(1)).scalar_one_or_none()
    if insp is None:
        return {"error": "inspection not found"}
    v = db.get(Vehicle, insp.vehicle_id)
    sr = db.execute(select(ScoringResult).where(ScoringResult.inspection_id == insp.id).order_by(ScoringResult.created_at.desc()).limit(1)).scalar_one_or_none()
    zones = []
    if sr:
        for z in db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id == sr.id)).scalars():
            zones.append({"zone": z.zone_key, "score": z.score, "issues": [i.get("issue_key") for i in (z.issues or [])]})
    return {"id": str(insp.id), "plate": v.registration_plate if v else "?", "status": insp.status,
            "reject_reason": insp.reject_reason, "overall_score": sr.overall_score if sr else None, "zones": zones}


def _run_read(db: Session, name: str, args: dict) -> dict:
    if name == "fleet_overview":
        return _fleet_overview(db)
    if name == "overdue_vehicles":
        return _overdue_vehicles(db)
    if name == "model_status":
        return _model_status(db)
    if name == "driver_stats":
        return _driver_stats(db, args.get("query", ""))
    if name == "recent_inspections":
        return _recent_inspections(db, args.get("status"), args.get("limit", 10))
    if name == "inspection_lookup":
        return _inspection_lookup(db, args.get("id"), args.get("plate"))
    return {"error": f"unknown read tool {name}"}


# ---------- write tools (run only via execute(), after confirmation) ----------
def _audience_drivers(db: Session, audience: str, car_number: str | None) -> list[User]:
    if audience == "driver":
        d = _find_driver(db, car_number or "")
        return [d] if d else []
    if audience == "all_active":
        return list(db.execute(select(User).where(User.role == "driver", User.active.is_(True))).scalars())
    if audience == "overdue":
        from .routers.metrics import _overdue_rows

        _, rows = _overdue_rows(db)
        ids = [uuid.UUID(r["driver_id"]) for r in rows]
        return [db.get(User, i) for i in ids if db.get(User, i)]
    if audience == "rejected_today":
        start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        dids = set(db.execute(select(Inspection.driver_id).where(Inspection.status == "rejected", Inspection.created_at >= start)).scalars())
        return [db.get(User, i) for i in dids if db.get(User, i)]
    return []


def _run_write(db: Session, name: str, args: dict) -> dict:
    from .modelcfg import ensure_active_model_version

    if name == "set_cadence":
        mv = ensure_active_model_version(db)
        h = max(1, min(8760, int(args.get("hours", 24))))
        mv.thresholds = {**(mv.thresholds or {}), "cadence_hours": h}
        db.commit()
        return {"ok": True, "message": f"Inspection cadence set to {h} hours."}
    if name == "send_overdue_reminders":
        from .routers.metrics import overdue_sweep

        r = overdue_sweep(db)
        return {"ok": True, "message": f"Reminded {r['reminded']} of {r['overdue']} overdue drivers."}
    if name == "set_mode":
        mode = args.get("mode", "")
        if mode not in ("shadow", "assist", "auto", "disabled"):
            return {"ok": False, "message": "Invalid mode."}
        mv = ensure_active_model_version(db)
        mv.mode = mode
        db.commit()
        return {"ok": True, "message": f"Agent mode set to {mode}."}
    if name == "reset_driver_pin":
        d = _find_driver(db, args.get("car_number", ""))
        if d is None:
            return {"ok": False, "message": "Driver not found."}
        pin = f"{secrets.randbelow(10000):04d}"
        d.password_hash = hash_password(pin)
        db.commit()
        return {"ok": True, "message": f"New PIN for {d.name} ({d.car_number}): {pin}"}
    if name == "send_notification":
        audience = args.get("audience", "")
        drivers = [d for d in _audience_drivers(db, audience, args.get("car_number")) if d]
        title, msg = args.get("title", "BluCheck")[:80], args.get("message", "")[:300]
        sent = 0
        for d in drivers:
            if push.send_to_driver(db, d, title, msg, {"type": "admin_message"}) == push.OK:
                sent += 1
        return {"ok": True, "message": f"Notification sent to {sent} of {len(drivers)} driver(s)."}
    if name == "set_thresholds":
        mv = ensure_active_model_version(db)
        a, rj = int(args.get("auto_approve", 85)), int(args.get("auto_reject", 40))
        a = max(0, min(100, a)); rj = max(0, min(100, rj))
        if rj >= a:
            rj = max(0, a - 10)
        th = {**(mv.thresholds or {})}
        th["overall"] = {**(th.get("overall") or {}), "auto_approve": a, "auto_reject": rj}
        mv.thresholds = th
        db.commit()
        return {"ok": True, "message": f"Thresholds set: auto-approve >= {a}, auto-reject <= {rj}."}
    if name == "activate_policy":
        mv = ensure_active_model_version(db)
        from .routers.model import _apply_config_to_mv, _policies_of, _set_library

        target = (args.get("name") or "").strip().lower()
        pol = next((p for p in _policies_of(mv) if (p.get("name") or "").lower() == target), None)
        if pol is None:
            return {"ok": False, "message": f"No saved policy named '{args.get('name')}'."}
        _apply_config_to_mv(mv, pol.get("scoring_config") or {}, pol.get("thresholds") or {}, pol.get("sop") or "")
        _set_library(mv, _policies_of(mv), pol["id"])
        db.commit()
        return {"ok": True, "message": f"Policy '{pol.get('name')}' is now active."}
    if name == "toggle_full_autonomy":
        mv = ensure_active_model_version(db)
        on = bool(args.get("on"))
        mv.thresholds = {**(mv.thresholds or {}), "full_autonomy": on}
        db.commit()
        return {"ok": True, "message": f"Full autonomy turned {'ON' if on else 'OFF'}."}
    if name == "reprocess_inspection":
        from . import storage

        insp = None
        if args.get("id"):
            try:
                insp = db.get(Inspection, uuid.UUID(args["id"]))
            except (ValueError, TypeError):
                insp = None
        if insp is None and args.get("plate"):
            v = db.execute(select(Vehicle).where(Vehicle.registration_plate.ilike(args["plate"].strip()))).scalars().first()
            if v:
                insp = db.execute(select(Inspection).where(Inspection.vehicle_id == v.id).order_by(Inspection.created_at.desc()).limit(1)).scalar_one_or_none()
        if insp is None:
            return {"ok": False, "message": "Inspection not found."}
        if insp.status in ("failed", "processing", "pending"):
            insp.status = "processing"
        db.commit()
        storage.enqueue_extraction(str(insp.id))
        return {"ok": True, "message": f"Reprocessing inspection {str(insp.id)[:8]}."}
    if name == "set_driver_active":
        d = _find_driver(db, args.get("car_number", ""))
        if d is None:
            return {"ok": False, "message": "Driver not found."}
        d.active = bool(args.get("active"))
        db.commit()
        return {"ok": True, "message": f"Driver {d.name} ({d.car_number}) {'enabled' if d.active else 'disabled'}."}
    return {"ok": False, "message": f"Unknown action {name}."}


def _describe(db: Session, name: str, args: dict) -> dict:
    """Human-readable confirm-card text for a proposed write action."""
    if name == "set_cadence":
        return {"title": f"Set inspection cadence to {args.get('hours')}h", "detail": "Every vehicle must pass an inspection at least this often."}
    if name == "send_overdue_reminders":
        from .routers.metrics import _overdue_rows

        _, rows = _overdue_rows(db)
        return {"title": "Send reminders to all overdue drivers", "detail": f"Pushes a reminder to {len(rows)} overdue driver(s)."}
    if name == "set_mode":
        return {"title": f"Switch agent mode to {args.get('mode')}", "detail": "Changes how the agent acts on new inspections."}
    if name == "reset_driver_pin":
        d = _find_driver(db, args.get("car_number", ""))
        who = f"{d.car_number} ({d.name})" if d else args.get("car_number", "?")
        return {"title": f"Reset PIN for {who}", "detail": "A new 4-digit PIN will be generated and shown to you."}
    if name == "send_notification":
        aud = args.get("audience")
        drivers = _audience_drivers(db, aud, args.get("car_number"))
        labels = {"driver": "1 driver", "all_active": "ALL active drivers", "overdue": "all overdue drivers", "rejected_today": "everyone rejected today"}
        return {"title": f"Notify {labels.get(aud, aud)} ({len([d for d in drivers if d])})", "detail": f"“{args.get('title', '')}: {args.get('message', '')}”"}
    if name == "set_thresholds":
        return {"title": f"Set thresholds: approve ≥{args.get('auto_approve')}, reject ≤{args.get('auto_reject')}", "detail": "Changes the auto approve/reject score bands."}
    if name == "activate_policy":
        return {"title": f"Activate policy “{args.get('name')}”", "detail": "Makes it the live cleanliness criteria for new inspections."}
    if name == "toggle_full_autonomy":
        return {"title": f"Turn full autonomy {'ON' if args.get('on') else 'OFF'}", "detail": "When ON, the agent decides everything with no human and no calibration gate."}
    if name == "reprocess_inspection":
        return {"title": "Reprocess this inspection", "detail": f"Re-runs extraction and scoring for {args.get('id') or args.get('plate')}."}
    if name == "set_driver_active":
        return {"title": f"{'Enable' if args.get('active') else 'Disable'} driver {args.get('car_number')}", "detail": "Controls whether they can sign in."}
    return {"title": name.replace("_", " "), "detail": ""}


def execute(db: Session, tool: str, args: dict) -> dict:
    """Run one confirmed write action. Whitelisted to write tools only."""
    if tool not in WRITE_TOOLS:
        return {"ok": False, "message": "Not an executable action."}
    try:
        return _run_write(db, tool, args or {})
    except Exception as err:  # noqa: BLE001
        logger.warning("execute %s failed: %s", tool, err)
        return {"ok": False, "message": f"Action failed: {str(err)[:160]}"}


def ask(db: Session, messages: list[dict], context: dict | None = None) -> dict:
    """Tool-calling loop. Reads run inline; writes are proposed (not run). Returns
    {answer, pending_actions:[{tool,args,title,detail}]}."""
    cfg = _cfg()
    key = cfg.get("groq_api_key")
    if not key:
        return {"answer": "The assistant is not configured right now.", "pending_actions": []}
    base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("groq_sop_model") or "openai/gpt-oss-120b"

    sys = SYSTEM
    if context:
        page = context.get("page")
        iid = context.get("inspection_id")
        if page:
            sys += f"\n\nThe admin is currently on the '{page}' page."
        if iid:
            sys += f" The current inspection id is {iid}; 'this inspection' refers to it."
    convo = [{"role": "system", "content": sys}]
    for m in messages[-12:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            convo.append({"role": m["role"], "content": str(m["content"])[:4000]})

    pending: list[dict] = []
    for _ in range(6):
        body = {"model": model, "messages": convo, "tools": TOOLS, "tool_choice": "auto", "temperature": 0.2, "max_tokens": 900}
        try:
            r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=45)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
        except Exception as err:  # noqa: BLE001
            logger.warning("assistant chat failed: %s", err)
            return {"answer": "Sorry, I hit an error reaching the model. Please try again.", "pending_actions": pending}

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return {"answer": msg.get("content") or "(no answer)", "pending_actions": pending}

        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "")
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            if name in WRITE_TOOLS:
                desc = _describe(db, name, args)
                pending.append({"tool": name, "args": args, "title": desc["title"], "detail": desc["detail"]})
                result = {"status": "awaiting_confirmation", "summary": desc["title"],
                          "note": "This action is proposed only. A Confirm button is shown to the admin; do NOT say it is done."}
            else:
                result = _run_read(db, name, args)
            convo.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)[:4000]})

    return {"answer": "I ran several steps but couldn't finish. Try narrowing the question.", "pending_actions": pending}
