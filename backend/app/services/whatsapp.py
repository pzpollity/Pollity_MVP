"""
WhatsApp Business API helpers
------------------------------
- Verify GET challenge from Meta
- Validate HMAC-SHA256 signature on POST payloads
- Send text messages back to citizens
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.models.grievance import IncomingMessage

logger = logging.getLogger(__name__)

WA_API_BASE = "https://graph.facebook.com/v20.0"


# ── Signature Verification ─────────────────────────────────────────────────

def verify_signature(payload_bytes: bytes, x_hub_signature: str) -> bool:
    """
    Validate the X-Hub-Signature-256 header sent by Meta.
    Returns True if the signature matches.
    """
    expected = hmac.new(
        settings.WA_APP_SECRET.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    provided = x_hub_signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


# ── Webhook payload parsing ────────────────────────────────────────────────

def parse_incoming(payload: dict) -> list[IncomingMessage]:
    """
    Extract text messages from a Meta WhatsApp webhook payload.
    Skips non-text messages (images, audio, etc.) silently.
    """
    messages: list[IncomingMessage] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                messages.append(
                    IncomingMessage(
                        wa_message_id=msg["id"],
                        from_number=msg["from"],
                        office_phone_id=phone_number_id,
                        body=msg["text"]["body"],
                        timestamp=datetime.fromtimestamp(
                            int(msg["timestamp"]), tz=timezone.utc
                        ),
                    )
                )

    return messages


# ── Outbound messages ──────────────────────────────────────────────────────

async def send_text(to: str, body: str) -> None:
    """
    Send a plain text WhatsApp message to `to` (E.164 format).
    Uses the phone number ID from env (single-office Phase 1 assumption).
    """
    url = f"{WA_API_BASE}/{settings.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.error("WA send failed %s: %s", resp.status_code, resp.text)
        else:
            logger.info("WA message sent to %s", to)


def build_ack_message(grievance_id: str, urgency: str, category: str) -> str:
    """
    Build the acknowledgement message sent to the citizen after registration.
    """
    urgency_note = {
        "critical": "⚠️ This has been flagged as CRITICAL and will be escalated immediately.",
        "high":     "This has been marked HIGH priority.",
        "medium":   "This has been marked MEDIUM priority.",
        "low":      "This has been registered for review.",
    }.get(urgency, "")

    return (
        f"✅ Your grievance has been registered.\n\n"
        f"Reference ID: *{grievance_id}*\n"
        f"Category: {category.replace('_', ' ').title()}\n"
        f"{urgency_note}\n\n"
        f"You will receive a status update once it is reviewed by the office. "
        f"Please quote your Reference ID in future communications."
    )
