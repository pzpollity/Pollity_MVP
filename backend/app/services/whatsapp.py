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
    Extract text, image, and audio messages from a Meta WhatsApp webhook payload.
    Silently skips unsupported types (stickers, documents, location, etc.).
    """
    messages: list[IncomingMessage] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

            for msg in value.get("messages", []):
                msg_type = msg.get("type")
                ts = datetime.fromtimestamp(int(msg["timestamp"]), tz=timezone.utc)

                if msg_type == "text":
                    messages.append(IncomingMessage(
                        wa_message_id=msg["id"],
                        from_number=msg["from"],
                        office_phone_id=phone_number_id,
                        body=msg["text"]["body"],
                        timestamp=ts,
                    ))

                elif msg_type == "image":
                    img = msg.get("image", {})
                    messages.append(IncomingMessage(
                        wa_message_id=msg["id"],
                        from_number=msg["from"],
                        office_phone_id=phone_number_id,
                        body=img.get("caption", ""),   # optional caption as body
                        timestamp=ts,
                        media_id=img.get("id"),
                        media_type="image",
                        media_mime=img.get("mime_type", "image/jpeg"),
                    ))

                elif msg_type == "audio":
                    audio = msg.get("audio", {})
                    messages.append(IncomingMessage(
                        wa_message_id=msg["id"],
                        from_number=msg["from"],
                        office_phone_id=phone_number_id,
                        body="",
                        timestamp=ts,
                        media_id=audio.get("id"),
                        media_type="audio",
                        media_mime=audio.get("mime_type", "audio/ogg; codecs=opus"),
                    ))

                # stickers, documents, location, contacts — silently ignored

    return messages


async def download_media(media_id: str) -> tuple[bytes, str]:
    """
    Download a media file from Meta's CDN.

    Step 1: GET /{media_id} → {"url": "...", "mime_type": "..."}
    Step 2: GET that URL with Authorization header → raw bytes

    Returns (file_bytes, mime_type).
    """
    headers = {"Authorization": f"Bearer {settings.WA_ACCESS_TOKEN}"}
    async with httpx.AsyncClient(timeout=30) as client:
        meta_resp = await client.get(f"{WA_API_BASE}/{media_id}", headers=headers)
        meta_resp.raise_for_status()
        media_info = meta_resp.json()

        dl_resp = await client.get(media_info["url"], headers=headers)
        dl_resp.raise_for_status()

    mime = media_info.get("mime_type", "application/octet-stream")
    logger.info("Downloaded media %s (%d bytes, %s)", media_id, len(dl_resp.content), mime)
    return dl_resp.content, mime


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


_ACK_TEMPLATES: dict[str, dict] = {
    "en": {
        "registered": "✅ Your grievance has been registered.",
        "ref_id":     "Reference ID: *{grievance_id}*",
        "category":   "Category: {category}",
        "urgency": {
            "critical": "⚠️ This has been flagged as CRITICAL and will be escalated immediately.",
            "high":     "This has been marked HIGH priority.",
            "medium":   "This has been marked MEDIUM priority.",
            "low":      "This has been registered for review.",
        },
        "footer": (
            "You will receive a status update once it is reviewed by the office. "
            "Please quote your Reference ID in future communications."
        ),
    },
    "hi": {
        "registered": "✅ आपकी शिकायत दर्ज हो गई है।",
        "ref_id":     "संदर्भ ID: *{grievance_id}*",
        "category":   "श्रेणी: {category}",
        "urgency": {
            "critical": "⚠️ इसे अत्यावश्यक माना गया है और तुरंत कार्रवाई की जाएगी।",
            "high":     "इसे उच्च प्राथमिकता दी गई है।",
            "medium":   "इसे मध्यम प्राथमिकता दी गई है।",
            "low":      "इसे समीक्षा के लिए दर्ज किया गया है।",
        },
        "footer": (
            "समीक्षा के बाद आपको अपडेट मिलेगा। "
            "भविष्य में संपर्क के लिए अपना संदर्भ ID उल्लेख करें।"
        ),
    },
    "mr": {
        "registered": "✅ तुमची तक्रार नोंदवली गेली आहे।",
        "ref_id":     "संदर्भ ID: *{grievance_id}*",
        "category":   "श्रेणी: {category}",
        "urgency": {
            "critical": "⚠️ ही तक्रार अत्यंत तातडीची आहे आणि त्वरित कार्यवाही केली जाईल।",
            "high":     "ही उच्च प्राधान्याची तक्रार आहे।",
            "medium":   "ही मध्यम प्राधान्याची तक्रार आहे।",
            "low":      "ही तक्रार आढाव्यासाठी नोंदवली गेली आहे।",
        },
        "footer": (
            "आढावा घेतल्यानंतर तुम्हाला अपडेट मिळेल। "
            "पुढील संपर्कासाठी तुमची संदर्भ ID नमूद करा।"
        ),
    },
}

_STATUS_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "acknowledged": "📋 Update on your grievance *{grievance_id}*: your case has been acknowledged by the office.",
        "assigned":     "📋 Update on your grievance *{grievance_id}*: your case has been assigned for action.",
        "in_progress":  "📋 Update on your grievance *{grievance_id}*: your case is currently being worked on.",
        "resolved":     "✅ Your grievance *{grievance_id}* has been resolved. Thank you for contacting our office.",
        "verified":     "✅ Resolution of your grievance *{grievance_id}* has been verified. The matter is closed.",
        "closed":       "✅ Your grievance *{grievance_id}* is now closed. Thank you for your patience.",
    },
    "hi": {
        "acknowledged": "📋 आपकी शिकायत *{grievance_id}* अपडेट: आपकी शिकायत कार्यालय द्वारा स्वीकार कर ली गई है।",
        "assigned":     "📋 आपकी शिकायत *{grievance_id}* अपडेट: आपकी शिकायत कार्रवाई के लिए सौंप दी गई है।",
        "in_progress":  "📋 आपकी शिकायत *{grievance_id}* अपडेट: आपकी शिकायत पर काम चल रहा है।",
        "resolved":     "✅ आपकी शिकायत *{grievance_id}* का समाधान हो गया है। हमारे कार्यालय से संपर्क करने के लिए धन्यवाद।",
        "verified":     "✅ आपकी शिकायत *{grievance_id}* का समाधान सत्यापित हो गया है। यह मामला बंद है।",
        "closed":       "✅ आपकी शिकायत *{grievance_id}* बंद कर दी गई है। आपके धैर्य के लिए धन्यवाद।",
    },
    "mr": {
        "acknowledged": "📋 तुमच्या तक्रारीवर *{grievance_id}* अपडेट: तुमची तक्रार कार्यालयाने स्वीकारली आहे।",
        "assigned":     "📋 तुमच्या तक्रारीवर *{grievance_id}* अपडेट: तुमची तक्रार कार्यवाहीसाठी सोपवली आहे।",
        "in_progress":  "📋 तुमच्या तक्रारीवर *{grievance_id}* अपडेट: तुमच्या तक्रारीवर काम सुरू आहे।",
        "resolved":     "✅ तुमची तक्रार *{grievance_id}* निराकरण झाली आहे। आमच्या कार्यालयाशी संपर्क साधल्याबद्दल धन्यवाद।",
        "verified":     "✅ तुमच्या तक्रारीचे *{grievance_id}* निराकरण सत्यापित झाले आहे। हे प्रकरण बंद आहे।",
        "closed":       "✅ तुमची तक्रार *{grievance_id}* बंद झाली आहे। तुमच्या संयमाबद्दल धन्यवाद।",
    },
}


def _get_lang(language: str) -> str:
    """Return a supported template language code, defaulting to English."""
    return language if language in _ACK_TEMPLATES else "en"


def build_ack_message(
    grievance_id: str,
    urgency: str,
    category: str,
    language: str = "en",
) -> str:
    """
    Build the acknowledgement message sent to the citizen after registration.
    Supports English (en), Hindi (hi), and Marathi (mr); falls back to English.
    """
    lang = _get_lang(language)
    t = _ACK_TEMPLATES[lang]
    urgency_note = t["urgency"].get(urgency, "")
    category_display = category.replace("_", " ").title()

    return "\n".join([
        t["registered"],
        "",
        t["ref_id"].format(grievance_id=grievance_id),
        t["category"].format(category=category_display),
        urgency_note,
        "",
        t["footer"],
    ])


def build_status_update_message(
    grievance_id: str,
    status: str,
    language: str = "en",
) -> str | None:
    """
    Build a status update message to send to the citizen when staff updates a grievance.
    Returns None for statuses that should not trigger a citizen notification.
    """
    notify_statuses = {"acknowledged", "assigned", "in_progress", "resolved", "verified", "closed"}
    if status not in notify_statuses:
        return None

    lang = _get_lang(language)
    templates = _STATUS_TEMPLATES.get(lang, _STATUS_TEMPLATES["en"])
    template = templates.get(status)
    if not template:
        return None

    return template.format(grievance_id=grievance_id)
