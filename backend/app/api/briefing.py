"""
Briefing API
-------------
POST /api/briefing/trigger  — Generate and send the weekly briefing.
                              Protected by X-Briefing-Secret header.
                              Call this from a cron service every Monday 8 AM IST.

POST /api/briefing/preview  — Generate briefing and return JSON (no send).
                              Useful for testing.
"""

import logging

import httpx
from fastapi import APIRouter, Header, HTTPException

from app.core.config import settings
from app.core.database import get_db
from app.services.briefing import generate_weekly_briefing
from app.services.whatsapp import send_text

router = APIRouter(prefix="/api/briefing", tags=["briefing"])
logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


async def _send_briefing(office_id: str) -> dict:
    """Generate the briefing and deliver via WhatsApp + email."""
    # Fetch office alert contacts
    db = get_db()
    resp = (
        db.table("offices")
        .select("name, alert_whatsapp, alert_emails")
        .eq("id", office_id)
        .single()
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail=f"Office {office_id} not found")

    office      = resp.data
    wa_number   = office.get("alert_whatsapp")
    alert_emails = office.get("alert_emails") or []

    briefing = await generate_weekly_briefing(office_id)

    results = {"whatsapp": None, "emails": []}

    # ── WhatsApp ──────────────────────────────────────────────────────────────
    if wa_number:
        try:
            await send_text(wa_number, briefing["whatsapp_message"])
            results["whatsapp"] = "sent"
            logger.info("Weekly briefing sent via WhatsApp to %s", wa_number)
        except Exception:
            logger.exception("Failed to send briefing WhatsApp to %s", wa_number)
            results["whatsapp"] = "failed"

    # ── Email (Resend) ────────────────────────────────────────────────────────
    if alert_emails and settings.RESEND_API_KEY:
        async with httpx.AsyncClient(timeout=15) as client:
            for email in alert_emails:
                try:
                    resp_email = await client.post(
                        RESEND_URL,
                        headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                        json={
                            "from":    "Jan-Sunwai <alerts@pollity.in>",
                            "to":      email,
                            "subject": briefing["email_subject"],
                            "html":    briefing["email_html"],
                        },
                    )
                    if resp_email.status_code in (200, 201):
                        results["emails"].append({"to": email, "status": "sent"})
                        logger.info("Weekly briefing email sent to %s", email)
                    else:
                        results["emails"].append({"to": email, "status": f"error {resp_email.status_code}"})
                except Exception:
                    logger.exception("Failed to send briefing email to %s", email)
                    results["emails"].append({"to": email, "status": "failed"})

    return {
        "office_id":  office_id,
        "week_label": briefing["week_label"],
        "stats":      briefing["stats"],
        "delivery":   results,
    }


@router.post("/trigger")
async def trigger_briefing(x_briefing_secret: str = Header(default="")):
    """
    Trigger the weekly briefing for all configured offices.
    Called by cron-job.org every Monday at 8:00 AM IST (2:30 AM UTC).

    Set BRIEFING_SECRET in Railway env vars and pass it as X-Briefing-Secret header.
    """
    if settings.BRIEFING_SECRET and x_briefing_secret != settings.BRIEFING_SECRET:
        raise HTTPException(status_code=401, detail="Invalid briefing secret")

    office_id = settings.VOICE_OFFICE_ID  # for now: single office
    if not office_id:
        raise HTTPException(status_code=400, detail="No office configured (VOICE_OFFICE_ID not set)")

    result = await _send_briefing(office_id)
    return result


@router.post("/preview")
async def preview_briefing(x_briefing_secret: str = Header(default="")):
    """
    Generate and return the briefing as JSON without sending it.
    Useful for testing the output before going live.
    """
    if settings.BRIEFING_SECRET and x_briefing_secret != settings.BRIEFING_SECRET:
        raise HTTPException(status_code=401, detail="Invalid briefing secret")

    office_id = settings.VOICE_OFFICE_ID
    if not office_id:
        raise HTTPException(status_code=400, detail="VOICE_OFFICE_ID not set")

    briefing = await generate_weekly_briefing(office_id)
    return {
        "week_label":       briefing["week_label"],
        "stats":            briefing["stats"],
        "narrative":        briefing["narrative"],
        "whatsapp_preview": briefing["whatsapp_message"],
    }
