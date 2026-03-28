"""
Critical Grievance Notifications
----------------------------------
Fires when Claude Haiku classifies an incoming grievance as CRITICAL urgency.
Sends simultaneously to:
  - alert_whatsapp  (office row) — WhatsApp message via existing send_text
  - alert_emails    (office row) — Email via Resend API

Both channels are best-effort: a failure in one does not block the other
or the grievance intake pipeline.
"""

import logging

import httpx

from app.core.config import settings
from app.services.whatsapp import send_text

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


def _build_wa_alert(grievance_id: str, category: str, summary: str, citizen_contact: str, channel: str) -> str:
    category_display = category.replace("_", " ").title()
    return (
        f"🚨 *CRITICAL grievance filed*\n\n"
        f"Ref: *{grievance_id}*\n"
        f"Category: {category_display}\n"
        f"Summary: {summary}\n"
        f"Contact: {citizen_contact}\n"
        f"Channel: {channel}"
    )


def _build_email_html(grievance_id: str, category: str, summary: str, citizen_contact: str, channel: str) -> str:
    category_display = category.replace("_", " ").title()
    return f"""
    <div style="font-family:sans-serif;max-width:560px;margin:auto;padding:24px;border:1px solid #e74c3c;border-radius:8px;">
      <h2 style="color:#e74c3c;margin-top:0;">🚨 CRITICAL Grievance Filed</h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:6px 0;color:#555;width:120px;">Reference ID</td>
            <td style="padding:6px 0;font-weight:bold;">{grievance_id}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Category</td>
            <td style="padding:6px 0;">{category_display}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Summary</td>
            <td style="padding:6px 0;">{summary}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Citizen Contact</td>
            <td style="padding:6px 0;">{citizen_contact}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Channel</td>
            <td style="padding:6px 0;">{channel}</td></tr>
      </table>
      <p style="margin-top:20px;font-size:12px;color:#999;">
        Sent by Jan-Sunwai · Pollity.in — this grievance requires immediate attention.
      </p>
    </div>
    """


async def fire_critical_alerts(
    grievance_id: str,
    category: str,
    summary: str,
    citizen_contact: str,
    channel: str,
    alert_whatsapp: str | None,
    alert_emails: list[str] | None,
) -> None:
    """
    Send WhatsApp + email alerts for a CRITICAL grievance.
    Called from grievance_service after classification; never raises.
    """
    wa_text = _build_wa_alert(grievance_id, category, summary, citizen_contact, channel)
    email_html = _build_email_html(grievance_id, category, summary, citizen_contact, channel)
    subject = f"🚨 CRITICAL Grievance — {grievance_id}"

    # ── WhatsApp alert ────────────────────────────────────────────────────────
    if alert_whatsapp:
        try:
            await send_text(alert_whatsapp, wa_text)
            logger.info("Critical WA alert sent for %s → %s", grievance_id, alert_whatsapp)
        except Exception:
            logger.exception("Failed to send critical WA alert for %s", grievance_id)

    # ── Email alerts (Resend) ─────────────────────────────────────────────────
    if alert_emails and settings.RESEND_API_KEY:
        async with httpx.AsyncClient(timeout=10) as client:
            for email in alert_emails:
                try:
                    resp = await client.post(
                        RESEND_URL,
                        headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                        json={
                            "from": "Jan-Sunwai Alerts <alerts@pollity.in>",
                            "to": email,
                            "subject": subject,
                            "html": email_html,
                        },
                    )
                    if resp.status_code in (200, 201):
                        logger.info("Critical email alert sent for %s → %s", grievance_id, email)
                    else:
                        logger.error("Resend error %s for %s: %s", resp.status_code, grievance_id, resp.text)
                except Exception:
                    logger.exception("Failed to send critical email alert for %s → %s", grievance_id, email)
    elif alert_emails and not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email alert for %s", grievance_id)
