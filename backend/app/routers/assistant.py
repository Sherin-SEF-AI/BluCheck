"""Agentic admin assistant endpoints: ask (proposes) + execute (runs a confirmed action)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import assistant as assistant_agent
from .. import audit
from ..auth import require_admin
from ..db import get_db
from ..models import User
from ..schemas import (
    AssistantAskRequest,
    AssistantAskResponse,
    AssistantExecuteRequest,
    AssistantExecuteResponse,
    AssistantPendingAction,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])
logger = logging.getLogger("blucheck.assistant")


@router.post("/ask", response_model=AssistantAskResponse)
def ask(
    body: AssistantAskRequest, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AssistantAskResponse:
    """Ask the assistant. Reads run inline; write actions are returned as pending_actions that the
    admin must confirm via /assistant/execute."""
    out = assistant_agent.ask(db, [m.model_dump() for m in body.messages], body.context)
    return AssistantAskResponse(
        answer=out["answer"],
        pending_actions=[AssistantPendingAction(**p) for p in out.get("pending_actions", [])],
    )


@router.post("/execute", response_model=AssistantExecuteResponse)
def execute(
    body: AssistantExecuteRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AssistantExecuteResponse:
    """Run a single write action the admin confirmed. Whitelisted + audited."""
    result = assistant_agent.execute(db, body.tool, body.args)
    audit.record(
        db, actor_id=admin.id, action="assistant_execute", entity="system",
        entity_id=body.tool, detail={"tool": body.tool, "args": body.args, "ok": result.get("ok")},
    )
    db.commit()
    return AssistantExecuteResponse(ok=bool(result.get("ok")), message=result.get("message", ""))
