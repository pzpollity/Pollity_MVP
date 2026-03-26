"""
Grievance CRUD endpoints (called by the Streamlit dashboard)
-------------------------------------------------------------
GET  /grievances               — list, with filters
GET  /grievances/{id}          — single grievance
PATCH /grievances/{id}/status  — update status / assigned_to / next_action
"""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.database import get_db
from app.models.grievance import GrievanceStatus

router = APIRouter(prefix="/grievances", tags=["grievances"])
logger = logging.getLogger(__name__)


@router.get("")
def list_grievances(
    office_id: Annotated[str, Query()],
    status: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    urgency: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(le=200)] = 50,
    offset: Annotated[int, Query()] = 0,
):
    db = get_db()
    q = db.table("grievances").select("*").eq("office_id", office_id)
    if status:
        q = q.eq("status", status)
    if category:
        q = q.eq("category", category)
    if urgency:
        q = q.eq("urgency", urgency)
    resp = q.order("filed_at", desc=True).range(offset, offset + limit - 1).execute()
    return resp.data or []


@router.get("/{grievance_uuid}")
def get_grievance(grievance_uuid: str):
    db = get_db()
    resp = db.table("grievances").select("*").eq("id", grievance_uuid).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return resp.data


class StatusUpdate(BaseModel):
    status: GrievanceStatus
    assigned_to: str | None = None
    next_action: str | None = None


@router.patch("/{grievance_uuid}/status")
def update_status(grievance_uuid: str, body: StatusUpdate):
    db = get_db()
    from datetime import datetime, timezone

    patch = {"status": body.status.value, "updated_at": datetime.now(tz=timezone.utc).isoformat()}
    if body.assigned_to is not None:
        patch["assigned_to"] = body.assigned_to
    if body.next_action is not None:
        patch["next_action"] = body.next_action
    if body.status == GrievanceStatus.CLOSED:
        patch["closed_at"] = patch["updated_at"]

    resp = db.table("grievances").update(patch).eq("id", grievance_uuid).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return resp.data[0]
