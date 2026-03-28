"""
Grievance Service
------------------
Orchestrates the full intake pipeline:
  1. Look up which office owns this WhatsApp phone number ID
  2. Fetch recent open grievances for duplicate detection
  3. Call the classifier
  4. Persist to Supabase
  5. Return the grievance record
"""

import logging
import uuid
from datetime import datetime, timezone

from app.core.database import get_db
from app.models.grievance import (
    Grievance,
    GrievanceChannel,
    GrievanceStatus,
    IncomingMessage,
)
from app.services.classifier import classify_grievance

logger = logging.getLogger(__name__)


def _generate_grievance_id(office_short: str, sequence: int, year_month: str) -> str:
    """
    Human-readable ID: GR-{OFFICE}-{YYYYMM}-{SEQ:05d}
    e.g. GR-DMO-202603-00042
    Monthly sequence reset prevents 4-digit cap; 5 digits supports 99,999/month.
    """
    return f"GR-{office_short.upper()}-{year_month}-{sequence:05d}"


async def process_whatsapp_message(msg: IncomingMessage) -> Grievance | None:
    """
    Full intake pipeline for one incoming WhatsApp message.
    Returns the persisted Grievance or None if the office is not found.
    """
    db = get_db()

    # ── 1. Resolve office ────────────────────────────────────────────────────
    office_resp = (
        db.table("offices")
        .select("id, short_code, sequence_counter")
        .eq("wa_phone_number_id", msg.office_phone_id)
        .single()
        .execute()
    )
    if not office_resp.data:
        logger.warning("No office found for phone_number_id=%s", msg.office_phone_id)
        return None

    office = office_resp.data
    office_id: str = office["id"]
    short_code: str = office["short_code"]

    # ── 2. Fetch recent open summaries for duplicate detection ────────────────
    recent_resp = (
        db.table("grievances")
        .select("id, summary")
        .eq("office_id", office_id)
        .neq("status", "closed")
        .order("filed_at", desc=True)
        .limit(20)
        .execute()
    )
    existing_summaries = recent_resp.data or []

    # ── 3. Classify ───────────────────────────────────────────────────────────
    classification = await classify_grievance(msg.body, existing_summaries)

    # ── 4. Increment monthly sequence counter (atomic, resets each month) ────
    year_month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    seq_resp = (
        db.rpc("increment_monthly_counter", {
            "office_id_param": office_id,
            "year_month_param": year_month,
        })
        .execute()
    )
    sequence: int = seq_resp.data if seq_resp.data else 1
    grievance_id = _generate_grievance_id(short_code, sequence, year_month)

    # ── 5. Persist ────────────────────────────────────────────────────────────
    now = datetime.now(tz=timezone.utc).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "grievance_id": grievance_id,
        "office_id": office_id,
        "citizen_contact": msg.from_number,
        "channel": GrievanceChannel.WHATSAPP.value,
        "raw_text": msg.body,
        "category": classification.category.value,
        "urgency": classification.urgency.value,
        "summary": classification.summary,
        "language_detected": classification.language_detected,
        "status": GrievanceStatus.REGISTERED.value,
        "is_duplicate": classification.is_duplicate,
        "duplicate_of_id": classification.duplicate_of_id,
        "filed_at": now,
        "updated_at": now,
    }

    insert_resp = db.table("grievances").insert(row).execute()
    if not insert_resp.data:
        logger.error("Failed to insert grievance: %s", insert_resp)
        return None

    logger.info("Grievance registered: %s [%s/%s]", grievance_id, classification.category.value, classification.urgency.value)
    return Grievance(**insert_resp.data[0])


async def process_walkin_grievance(
    office_id: str,
    citizen_name: str | None,
    citizen_contact: str,
    channel: "GrievanceChannel",
    raw_text: str,
) -> Grievance | None:
    """
    Intake pipeline for walk-in, phone, or letter grievances logged by office staff.
    Runs through the same Claude Haiku classification as WhatsApp intake.
    Returns the persisted Grievance or None if the office is not found.
    """
    db = get_db()

    # ── 1. Resolve office ────────────────────────────────────────────────────
    office_resp = (
        db.table("offices")
        .select("id, short_code, sequence_counter")
        .eq("id", office_id)
        .single()
        .execute()
    )
    if not office_resp.data:
        logger.warning("Walk-in: no office found for office_id=%s", office_id)
        return None

    office = office_resp.data
    short_code: str = office["short_code"]

    # ── 2. Fetch recent open summaries for duplicate detection ────────────────
    recent_resp = (
        db.table("grievances")
        .select("id, summary")
        .eq("office_id", office_id)
        .neq("status", "closed")
        .order("filed_at", desc=True)
        .limit(20)
        .execute()
    )
    existing_summaries = recent_resp.data or []

    # ── 3. Classify ───────────────────────────────────────────────────────────
    classification = await classify_grievance(raw_text, existing_summaries)

    # ── 4. Increment monthly sequence counter (atomic, resets each month) ────
    year_month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    seq_resp = (
        db.rpc("increment_monthly_counter", {
            "office_id_param": office_id,
            "year_month_param": year_month,
        })
        .execute()
    )
    sequence: int = seq_resp.data if seq_resp.data else 1
    grievance_id = _generate_grievance_id(short_code, sequence, year_month)

    # ── 5. Persist ────────────────────────────────────────────────────────────
    now = datetime.now(tz=timezone.utc).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "grievance_id": grievance_id,
        "office_id": office_id,
        "citizen_name": citizen_name,
        "citizen_contact": citizen_contact,
        "channel": channel.value,
        "raw_text": raw_text,
        "category": classification.category.value,
        "urgency": classification.urgency.value,
        "summary": classification.summary,
        "language_detected": classification.language_detected,
        "status": GrievanceStatus.REGISTERED.value,
        "is_duplicate": classification.is_duplicate,
        "duplicate_of_id": classification.duplicate_of_id,
        "filed_at": now,
        "updated_at": now,
    }

    insert_resp = db.table("grievances").insert(row).execute()
    if not insert_resp.data:
        logger.error("Failed to insert walk-in grievance: %s", insert_resp)
        return None

    logger.info("Walk-in grievance registered: %s [%s/%s]", grievance_id, classification.category.value, classification.urgency.value)
    return Grievance(**insert_resp.data[0])
