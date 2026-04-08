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


def _build_wa_alert(
    grievance_id: str, category: str, summary: str,
    citizen_contact: str, channel: str, location_text: str | None = None,
) -> str:
    category_display = category.replace("_", " ").title()
    location_line = f"Location: {location_text}\n" if location_text else ""
    return (
        f"🚨 *Urgent grievance filed — {grievance_id}*\n\n"
        f"Category: {category_display}\n"
        f"{location_line}"
        f"Summary: {summary}\n"
        f"Contact: {citizen_contact}\n"
        f"Channel: {channel}\n\n"
        f"Immediate attention required."
    )


def _build_email_html(
    grievance_id: str, category: str, summary: str,
    citizen_contact: str, channel: str, location_text: str | None = None,
) -> str:
    category_display = category.replace("_", " ").title()
    location_row = (
        f"<tr><td style='padding:6px 0;color:#555;width:130px;'>Location</td>"
        f"<td style='padding:6px 0;'>{location_text}</td></tr>"
        if location_text else ""
    )
    return f"""
    <div style="font-family:sans-serif;max-width:560px;margin:auto;padding:24px;border:1px solid #c0392b;border-radius:8px;">
      <p style="margin:0 0 16px 0;font-size:13px;color:#555;">Jan Sunn · NetaWork.in — Urgent Grievance Alert</p>
      <h2 style="color:#c0392b;margin:0 0 16px 0;font-size:18px;">Urgent: Grievance {grievance_id} Requires Immediate Attention</h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:6px 0;color:#555;width:130px;">Reference ID</td>
            <td style="padding:6px 0;font-weight:bold;">{grievance_id}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Category</td>
            <td style="padding:6px 0;">{category_display}</td></tr>
        {location_row}
        <tr><td style="padding:6px 0;color:#555;">Summary</td>
            <td style="padding:6px 0;">{summary}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Citizen Contact</td>
            <td style="padding:6px 0;">{citizen_contact}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Channel</td>
            <td style="padding:6px 0;">{channel}</td></tr>
      </table>
      <p style="margin-top:20px;font-size:12px;color:#999;">
        You are receiving this because you are listed as an alert contact for this constituency office.
      </p>
    </div>
    """


def _build_email_text(
    grievance_id: str, category: str, summary: str,
    citizen_contact: str, channel: str, location_text: str | None = None,
) -> str:
    category_display = category.replace("_", " ").title()
    location_line = f"Location     : {location_text}\n" if location_text else ""
    return (
        f"Jan Sunn Alert — NetaWork.in\n\n"
        f"Urgent: Grievance {grievance_id} requires immediate attention.\n\n"
        f"Reference ID : {grievance_id}\n"
        f"Category     : {category_display}\n"
        f"{location_line}"
        f"Summary      : {summary}\n"
        f"Contact      : {citizen_contact}\n"
        f"Channel      : {channel}\n\n"
        f"You are receiving this because you are listed as an alert contact for this constituency office."
    )


async def fire_critical_alerts(
    grievance_id: str,
    category: str,
    summary: str,
    citizen_contact: str,
    channel: str,
    alert_whatsapp: str | None,
    alert_emails: list[str] | None,
    location_text: str | None = None,
) -> None:
    """
    Send WhatsApp + email alerts for a CRITICAL grievance.
    Called from grievance_service after classification; never raises.
    """
    wa_text    = _build_wa_alert(grievance_id, category, summary, citizen_contact, channel, location_text)
    email_html = _build_email_html(grievance_id, category, summary, citizen_contact, channel, location_text)
    email_text = _build_email_text(grievance_id, category, summary, citizen_contact, channel, location_text)
    subject = f"[Jan Sunn] Urgent grievance filed — {grievance_id}"

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
                            "from": "Jan Sunn Alerts <alerts@netawork.in>",
                            "to": email,
                            "subject": subject,
                            "html": email_html,
                            "text": email_text,
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


async def fire_cluster_alert(
    location_text: str,
    category: str,
    count: int,
    latest_grievance_id: str,
    latest_summary: str,
    alert_whatsapp: str | None,
    alert_emails: list[str] | None,
) -> None:
    """
    Send a single consolidated alert when ≥ N critical grievances arrive
    from the same location within one hour. Replaces individual alerts
    to prevent alert fatigue during accidents or disasters.
    """
    category_display = category.replace("_", " ").title()
    subject = f"[Jan Sunn] ⚠️ {count} critical grievances from {location_text}"

    wa_text = (
        f"🚨 *CLUSTER ALERT — {count} critical reports from {location_text}*\n\n"
        f"Category: {category_display}\n"
        f"Latest ({latest_grievance_id}): {latest_summary}\n\n"
        f"Multiple citizens are reporting the same emergency from this area. "
        f"Immediate field response recommended."
    )

    email_html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:auto;padding:24px;
                border:2px solid #b71c1c;border-radius:8px;background:#fff8f8;">
      <p style="margin:0 0 8px;font-size:12px;color:#888;">Jan Sunn · NetaWork.in — Cluster Alert</p>
      <h2 style="color:#b71c1c;margin:0 0 16px;font-size:18px;">
        ⚠️ {count} Critical Reports from the Same Location
      </h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:6px 0;color:#555;width:130px;">Location</td>
            <td style="padding:6px 0;font-weight:700;">{location_text}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Category</td>
            <td style="padding:6px 0;">{category_display}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Reports (1h)</td>
            <td style="padding:6px 0;font-weight:700;color:#b71c1c;">{count}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Latest ID</td>
            <td style="padding:6px 0;">{latest_grievance_id}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Latest Summary</td>
            <td style="padding:6px 0;">{latest_summary}</td></tr>
      </table>
      <p style="margin-top:20px;font-size:13px;color:#b71c1c;font-weight:600;">
        Multiple citizens are reporting the same emergency from this area.
        Immediate field response is recommended.
      </p>
      <p style="margin-top:12px;font-size:11px;color:#bbb;">Jan Sunn — NetaWork.in</p>
    </div>
    """

    email_text = (
        f"Jan Sunn — CLUSTER ALERT\n\n"
        f"{count} critical reports from {location_text} in the last hour.\n\n"
        f"Category    : {category_display}\n"
        f"Reports (1h): {count}\n"
        f"Latest ID   : {latest_grievance_id}\n"
        f"Summary     : {latest_summary}\n\n"
        f"Multiple citizens are reporting the same emergency. Immediate field response recommended."
    )

    if alert_whatsapp:
        try:
            await send_text(alert_whatsapp, wa_text)
            logger.info("Cluster WA alert sent for %s × %d", location_text, count)
        except Exception:
            logger.exception("Failed to send cluster WA alert")

    if alert_emails and settings.RESEND_API_KEY:
        async with httpx.AsyncClient(timeout=10) as client:
            for email in alert_emails:
                try:
                    resp = await client.post(
                        RESEND_URL,
                        headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                        json={
                            "from":    "Jan Sunn Alerts <alerts@netawork.in>",
                            "to":      email,
                            "subject": subject,
                            "html":    email_html,
                            "text":    email_text,
                        },
                    )
                    if resp.status_code in (200, 201):
                        logger.info("Cluster email alert sent for %s → %s", location_text, email)
                    else:
                        logger.error("Resend cluster error %s: %s", resp.status_code, resp.text)
                except Exception:
                    logger.exception("Failed cluster email alert → %s", email)
