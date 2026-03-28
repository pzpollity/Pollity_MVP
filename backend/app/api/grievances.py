"""
Grievance CRUD endpoints (called by the Streamlit dashboard)
-------------------------------------------------------------
GET   /grievances               — list, with filters
GET   /grievances/{id}          — single grievance
PATCH /grievances/{id}/status   — update status / assigned_to / next_action
POST  /grievances/walkin        — log a walk-in / phone / letter grievance
"""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.database import get_db
from app.models.grievance import GrievanceChannel, GrievanceStatus
from app.services.grievance_service import process_walkin_grievance
from app.services.whatsapp import build_status_update_message, send_text

router = APIRouter(prefix="/grievances", tags=["grievances"])
logger = logging.getLogger(__name__)

# Statuses that trigger a WhatsApp notification to the citizen
_NOTIFY_STATUSES = {
    GrievanceStatus.ACKNOWLEDGED,
    GrievanceStatus.ASSIGNED,
    GrievanceStatus.IN_PROGRESS,
    GrievanceStatus.RESOLVED,
    GrievanceStatus.VERIFIED,
    GrievanceStatus.CLOSED,
}


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
async def update_status(grievance_uuid: str, body: StatusUpdate):
    db = get_db()

    patch = {
        "status": body.status.value,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if body.assigned_to is not None:
        patch["assigned_to"] = body.assigned_to
    if body.next_action is not None:
        patch["next_action"] = body.next_action
    if body.status == GrievanceStatus.CLOSED:
        patch["closed_at"] = patch["updated_at"]

    resp = db.table("grievances").update(patch).eq("id", grievance_uuid).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Grievance not found")

    row = resp.data[0]

    # ── WhatsApp status notification to citizen ───────────────────────────────
    if body.status in _NOTIFY_STATUSES:
        citizen_contact = row.get("citizen_contact", "")
        if citizen_contact and citizen_contact != "WALK-IN":
            message = build_status_update_message(
                grievance_id=row["grievance_id"],
                status=body.status.value,
                language=row.get("language_detected", "en"),
            )
            if message:
                try:
                    await send_text(citizen_contact, message)
                except Exception:
                    # Log but do not fail the status update if WA send fails
                    logger.exception(
                        "Failed to send WA status notification for %s", row["grievance_id"]
                    )

    return row


# ── Walk-in / Phone / Letter intake ──────────────────────────────────────────

class WalkInRequest(BaseModel):
    office_id: str
    citizen_name: str | None = None
    citizen_contact: str | None = None   # phone number if known, else omit
    channel: GrievanceChannel = GrievanceChannel.WALK_IN
    raw_text: str


@router.post("/walkin")
async def walkin_intake(body: WalkInRequest):
    """
    Log a grievance received in person, by phone, or by letter.
    Runs through the same Claude Haiku classification pipeline as WhatsApp intake.
    """
    grievance = await process_walkin_grievance(
        office_id=body.office_id,
        citizen_name=body.citizen_name,
        citizen_contact=body.citizen_contact or "WALK-IN",
        channel=body.channel,
        raw_text=body.raw_text,
    )
    if grievance is None:
        raise HTTPException(status_code=404, detail="Office not found")

    logger.info("Walk-in grievance registered: %s", grievance.grievance_id)
    return {"grievance_id": grievance.grievance_id, "id": grievance.id}
