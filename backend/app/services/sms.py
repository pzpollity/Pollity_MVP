"""
SMS Service — Twilio REST API
------------------------------
Sends SMS to citizens who filed via phone call (no WhatsApp).
Used for status update notifications and ACKs.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_STATUS_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "acknowledged": "Update on {gid}: your case has been acknowledged. Jan-Sunwai - Pollity.in",
        "assigned":     "Update on {gid}: your case has been assigned for action. Jan-Sunwai - Pollity.in",
        "in_progress":  "Update on {gid}: your case is being worked on. Jan-Sunwai - Pollity.in",
        "resolved":     "Your grievance {gid} has been resolved. Thank you. Jan-Sunwai - Pollity.in",
        "verified":     "Resolution of {gid} has been verified. Case closed. Jan-Sunwai - Pollity.in",
        "closed":       "Your grievance {gid} is now closed. Thank you. Jan-Sunwai - Pollity.in",
    },
    "hi": {
        "acknowledged": "{gid} अपडेट: आपकी शिकायत स्वीकार कर ली गई है। Jan-Sunwai - Pollity.in",
        "assigned":     "{gid} अपडेट: शिकायत कार्रवाई के लिए सौंप दी गई है। Jan-Sunwai - Pollity.in",
        "in_progress":  "{gid} अपडेट: आपकी शिकायत पर काम चल रहा है। Jan-Sunwai - Pollity.in",
        "resolved":     "आपकी शिकायत {gid} का समाधान हो गया है। Jan-Sunwai - Pollity.in",
        "verified":     "शिकायत {gid} सत्यापित हो गई है। Jan-Sunwai - Pollity.in",
        "closed":       "शिकायत {gid} बंद कर दी गई है। धन्यवाद। Jan-Sunwai - Pollity.in",
    },
}


def build_sms_status_message(grievance_id: str, status: str, language: str = "en") -> str | None:
    notify_statuses = {"acknowledged", "assigned", "in_progress", "resolved", "verified", "closed"}
    if status not in notify_statuses:
        return None
    lang = language if language in _STATUS_TEMPLATES else "en"
    template = _STATUS_TEMPLATES[lang].get(status)
    return template.format(gid=grievance_id) if template else None


async def send_sms(to: str, body: str) -> None:
    """Send an SMS via Twilio. Silently skips if Twilio is not configured."""
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
        logger.debug("SMS skipped — Twilio not fully configured")
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                data={"From": settings.TWILIO_FROM_NUMBER, "To": to, "Body": body},
            )
            resp.raise_for_status()
        logger.info("SMS sent to %s", to)
    except Exception:
        logger.exception("Failed to send SMS to %s", to)
