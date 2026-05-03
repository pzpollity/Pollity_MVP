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

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.core.database import get_db
from app.models.grievance import GrievanceChannel, GrievanceStatus
from app.services.action_advisor import suggest_action
from app.services.grievance_service import process_walkin_grievance
from app.services.letter_generator import generate_birthday_letter, generate_do_letter
from app.services.ocr import extract_text_from_image
from app.services.sms import build_sms_status_message, send_sms
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

    # ── Status notification to citizen ───────────────────────────────────────
    if body.status in _NOTIFY_STATUSES:
        citizen_contact = row.get("citizen_contact", "")
        channel         = row.get("channel", "")
        language        = row.get("language_detected", "en")
        grievance_id    = row["grievance_id"]

        if citizen_contact and citizen_contact not in ("WALK-IN", ""):
            try:
                if channel == "phone":
                    # Phone-filed grievances: notify via SMS (citizen has no WhatsApp)
                    msg = build_sms_status_message(grievance_id, body.status.value, language)
                    if msg:
                        await send_sms(citizen_contact, msg)
                else:
                    # WhatsApp / email / other: notify via WhatsApp
                    msg = build_status_update_message(grievance_id, body.status.value, language)
                    if msg:
                        await send_text(citizen_contact, msg)
            except Exception:
                logger.exception("Failed to send status notification for %s", grievance_id)

    return row


@router.post("/{grievance_uuid}/notify-citizen")
async def notify_citizen(grievance_uuid: str):
    """
    Manually send a WhatsApp status-update message to the citizen.
    Uses the grievance's current status and detected language.
    Raises 422 if no valid contact is on file or no template exists for the status.
    """
    db = get_db()
    resp = db.table("grievances").select("*").eq("id", grievance_uuid).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Grievance not found")

    row = resp.data
    citizen_contact = row.get("citizen_contact", "")

    if not citizen_contact or citizen_contact in ("WALK-IN", ""):
        raise HTTPException(status_code=422, detail="No WhatsApp contact on file for this grievance.")

    language     = row.get("language_detected", "en") or "en"
    grievance_id = row["grievance_id"]
    status       = row.get("status", "")

    msg = build_status_update_message(grievance_id, status, language)
    if not msg:
        raise HTTPException(
            status_code=422,
            detail=f"No notification template for status '{status}'.",
        )

    await send_text(citizen_contact, msg)
    logger.info("Manual WA notification sent for %s to %s", grievance_id, citizen_contact)
    return {"sent": True, "to": citizen_contact, "grievance_id": grievance_id}


# ── Action Advisor ────────────────────────────────────────────────────────────

@router.get("/{grievance_uuid}/suggest-action")
async def suggest_action_endpoint(grievance_uuid: str):
    """
    Generate an AI action recommendation for this grievance via Claude Sonnet.
    Saves the result to grievances.suggested_action.
    Response: { action_type, action_text, target_dept, draft_message }
    """
    db = get_db()
    resp = db.table("grievances").select("*").eq("id", grievance_uuid).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Grievance not found")

    result = await suggest_action(resp.data)
    return result


class ActionTakenBody(BaseModel):
    action_taken: str


@router.patch("/{grievance_uuid}/action-taken")
def log_action_taken(grievance_uuid: str, body: ActionTakenBody):
    """
    Record what action the staff actually took on this grievance.
    Stored in grievances.action_taken for audit trail and briefing analytics.
    """
    db = get_db()
    resp = db.table("grievances").update({
        "action_taken": body.action_taken,
    }).eq("id", grievance_uuid).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return {"saved": True}


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


# ── Letter / OCR intake ───────────────────────────────────────────────────────

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


@router.post("/letter-ocr")
async def letter_ocr_intake(
    office_id: str = Form(...),
    citizen_name: str | None = Form(None),
    citizen_contact: str | None = Form(None),
    image: UploadFile = File(...),
):
    """
    Scan a typed/handwritten letter image → OCR → classify → register grievance.

    Accepts multipart/form-data with:
      - office_id       (required)
      - citizen_name    (optional)
      - citizen_contact (optional, E.164 phone)
      - image           (required, JPEG/PNG/GIF/WEBP, ≤5 MB)

    Returns grievance_id, internal id, and the OCR-extracted text.
    """
    if image.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{image.content_type}'. Upload JPEG, PNG, GIF, or WEBP.",
        )

    image_bytes = await image.read()
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 5 MB limit.")

    # ── OCR ──────────────────────────────────────────────────────────────────
    try:
        raw_text = await extract_text_from_image(image_bytes, media_type=image.content_type)
    except Exception:
        logger.exception("OCR failed for uploaded letter image")
        raise HTTPException(status_code=500, detail="OCR processing failed. Try a clearer image.")

    if not raw_text or raw_text.startswith("[UNREADABLE"):
        raise HTTPException(
            status_code=422,
            detail=f"Could not read text from image: {raw_text or 'empty response'}",
        )

    # ── Classification + persist (same pipeline as walk-in) ──────────────────
    grievance = await process_walkin_grievance(
        office_id=office_id,
        citizen_name=citizen_name,
        citizen_contact=citizen_contact or "WALK-IN",
        channel=GrievanceChannel.LETTER,
        raw_text=raw_text,
    )
    if grievance is None:
        raise HTTPException(status_code=404, detail="Office not found")

    logger.info("Letter-OCR grievance registered: %s", grievance.grievance_id)
    return {
        "grievance_id": grievance.grievance_id,
        "id": grievance.id,
        "ocr_text": raw_text,
    }


# ── D.O. Letter Generation ────────────────────────────────────────────────────

_VALID_LETTER_TYPES = {"do_standard", "do_inspection_request", "railway_quota"}


@router.post("/{grievance_uuid}/generate-letter")
async def generate_letter_endpoint(
    grievance_uuid: str,
    letter_type: Annotated[str | None, Query()] = None,
):
    """
    Generate a D.O. (Demi-Official) letter for a grievance.

    Optional query param:
      ?letter_type=railway_quota   — force a specific template
      ?letter_type=do_standard
      ?letter_type=do_inspection_request
    If omitted, the template is auto-detected from grievance category + keywords.

    Returns { html, do_number, letter_type }.
    The 'html' field is a complete, print-ready A4 HTML document.
    """
    if letter_type and letter_type not in _VALID_LETTER_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid letter_type '{letter_type}'. Choose from: {sorted(_VALID_LETTER_TYPES)}",
        )

    db = get_db()

    # 1. Fetch grievance
    g_resp = db.table("grievances").select("*").eq("id", grievance_uuid).single().execute()
    if not g_resp.data:
        raise HTTPException(status_code=404, detail="Grievance not found")
    grievance = g_resp.data

    # 2. Fetch office (with letter_profile)
    office_id = grievance.get("office_id")
    if not office_id:
        raise HTTPException(status_code=422, detail="Grievance has no office_id")

    o_resp = db.table("offices").select("*").eq("id", office_id).single().execute()
    if not o_resp.data:
        raise HTTPException(status_code=404, detail="Office not found")
    office = o_resp.data

    # 3. Generate letter
    try:
        result = await generate_do_letter(grievance, office, letter_type_override=letter_type)
    except Exception:
        logger.exception("Letter generation failed for grievance %s", grievance_uuid)
        raise HTTPException(status_code=500, detail="Letter generation failed. Check server logs.")

    return {
        "html":        result["html"],
        "pdf_b64":     result.get("pdf_b64", ""),
        "docx_b64":    result.get("docx_b64", ""),
        "do_number":   result["do_number"],
        "letter_type": result["letter_type"],
    }
