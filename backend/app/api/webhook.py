"""
WhatsApp Webhook Endpoints
---------------------------
GET  /webhook  — Meta verification challenge
POST /webhook  — Incoming messages
"""

import logging

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from app.core.config import settings
from app.services.followup import (
    advance_to_verified,
    find_pending_verify,
    is_no_reply,
    is_yes_reply,
    mark_followup_replied,
    reopen_grievance,
)
from app.services.grievance_service import is_grievance_message, process_whatsapp_message
from app.core.database import get_db
from app.services.notifications import fire_critical_alerts
from app.services.whatsapp import (
    build_ack_message,
    build_help_reply,
    build_not_found_reply,
    build_status_inquiry_reply,
    is_help_inquiry,
    is_status_inquiry,
    parse_incoming,
    send_text,
    verify_signature,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/webhook")
async def verify_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
):
    """
    Meta sends a GET request to verify the webhook endpoint.
    We confirm the verify token and echo back the challenge.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.WA_VERIFY_TOKEN:
        logger.info("Webhook verified by Meta")
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """
    Receive incoming WhatsApp messages.
    - Validate HMAC signature
    - Parse message(s)
    - Process each in background (so we return 200 to Meta within 20 s)
    """
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(body_bytes, signature):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()

    # Meta sends a "statuses" update (delivery receipts etc.) — acknowledge and ignore
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("value", {}).get("statuses"):
                return {"status": "ok"}

    messages = parse_incoming(payload)
    for msg in messages:
        background_tasks.add_task(_handle_message, msg)

    return {"status": "ok"}


_NOT_GRIEVANCE_REPLY = {
    "en": (
        "Thank you for reaching out to Jan Sunn. "
        "To register a complaint, please describe your civic issue — for example: "
        "'There is no water supply in Ward 7 for 3 days' or 'Streetlights are broken on MG Road'.\n\n"
        "Type HELP for more options."
    ),
    "hi": (
        "जन सुन में आपका स्वागत है। "
        "शिकायत दर्ज करने के लिए कृपया अपनी समस्या स्पष्ट रूप से बताएं — जैसे: "
        "'वार्ड 7 में 3 दिन से पानी नहीं आ रहा' या 'एमजी रोड पर स्ट्रीटलाइट बंद है'।\n\n"
        "अधिक जानकारी के लिए HELP टाइप करें।"
    ),
}

_VOICE_NOT_SUPPORTED = {
    "en": "We received your voice message but voice processing is not yet enabled. Please send your grievance as a text message or photograph of a letter.",
    "hi": "हमें आपका वॉइस संदेश मिला, लेकिन अभी वॉइस प्रोसेसिंग उपलब्ध नहीं है। कृपया अपनी शिकायत टेक्स्ट संदेश या पत्र की फ़ोटो के रूप में भेजें।",
    "mr": "आम्हाला तुमचा व्हॉइस मेसेज मिळाला, पण आत्ता व्हॉइस प्रोसेसिंग उपलब्ध नाही. कृपया तुमची तक्रार मजकूर संदेश किंवा पत्राच्या फोटोद्वारे पाठवा.",
}


async def _handle_message(msg):
    if msg.body:
        body = msg.body.strip()

        # ── HELP command ──────────────────────────────────────────────────────
        if is_help_inquiry(body):
            await send_text(msg.from_number, build_help_reply("en"))
            return

        # ── Status inquiry: "STATUS GR-DMO-..." / "GR-DMO-..." / "स्थिति ..." ─
        inquiry, grievance_id = is_status_inquiry(body)
        if inquiry:
            db = get_db()
            resp = (
                db.table("grievances")
                .select("grievance_id,category,urgency,status,filed_at,language_detected")
                .ilike("grievance_id", grievance_id)
                .limit(1)
                .execute()
            )
            if resp.data:
                row = resp.data[0]
                lang = row.get("language_detected", "en")
                reply = build_status_inquiry_reply(row, lang)
            else:
                # Detect language from keyword prefix to reply in right language
                prefix = body.split()[0].lower() if body.split() else ""
                lang = "hi" if prefix in {"स्थिति", "sthiti", "स्टेटस"} else "en"
                reply = build_not_found_reply(grievance_id, lang)
            await send_text(msg.from_number, reply)
            return

    # ── Resolution YES / NO reply ────────────────────────────────────────────
    if msg.body and not msg.media_id:
        body_text = msg.body.strip()
        if is_no_reply(body_text) or is_yes_reply(body_text):
            grievance = find_pending_verify(msg.from_number)
            if grievance:
                lang = grievance.get("language_detected", "en") or "en"
                gid  = grievance["grievance_id"]
                is_no = is_no_reply(body_text)
                mark_followup_replied(grievance["id"], "NO" if is_no else "YES")

                if is_no:
                    reopen_grievance(grievance["id"])
                    # Notify staff that citizen disputed resolution
                    db = get_db()
                    office_resp = (
                        db.table("offices")
                        .select("alert_whatsapp, alert_emails")
                        .eq("id", grievance["office_id"])
                        .single()
                        .execute()
                    )
                    if office_resp.data:
                        await fire_critical_alerts(
                            grievance_id=gid,
                            category=grievance.get("category", "others"),
                            summary=f"Citizen disputed resolution: {grievance.get('summary', '')}",
                            citizen_contact=msg.from_number,
                            channel="whatsapp",
                            alert_whatsapp=office_resp.data.get("alert_whatsapp"),
                            alert_emails=office_resp.data.get("alert_emails") or [],
                            location_text=grievance.get("location_text"),
                        )
                    _REOPEN_REPLY = {
                        "en": f"We have noted that your issue for *{gid}* is still unresolved. The case has been reopened and escalated. You will receive an update shortly.",
                        "hi": f"हमने नोट किया कि *{gid}* की समस्या अभी भी है। मामला पुनः खोला और एस्केलेट किया गया है। जल्द अपडेट मिलेगा।",
                        "mr": f"आम्ही नोंद केली की *{gid}* ची समस्या अजूनही आहे. प्रकरण पुन्हा उघडले व एस्केलेट केले आहे.",
                    }
                    reply_lang = lang if lang in _REOPEN_REPLY else "en"
                    await send_text(msg.from_number, _REOPEN_REPLY[reply_lang])
                else:
                    advance_to_verified(grievance["id"])
                    _VERIFY_REPLY = {
                        "en": f"Thank you for confirming! Grievance *{gid}* has been verified and closed. We are glad the issue was resolved.",
                        "hi": f"पुष्टि के लिए धन्यवाद! शिकायत *{gid}* सत्यापित और बंद कर दी गई है।",
                        "mr": f"पुष्टीबद्दल धन्यवाद! तक्रार *{gid}* सत्यापित व बंद केली आहे.",
                    }
                    reply_lang = lang if lang in _VERIFY_REPLY else "en"
                    await send_text(msg.from_number, _VERIFY_REPLY[reply_lang])
                return

    # ── Relevance gate (text-only messages) ─────────────────────────────────
    # Skip gate for media (image/audio/location) — those are inherently civic
    if msg.body and not msg.media_id:
        body_text = msg.body.strip()
        if not await is_grievance_message(body_text):
            # Guess language from script: Devanagari → Hindi reply
            lang = "hi" if any("\u0900" <= ch <= "\u097F" for ch in body_text) else "en"
            await send_text(msg.from_number, _NOT_GRIEVANCE_REPLY[lang])
            return

    # ── Normal grievance intake ──────────────────────────────────────────────
    grievance = await process_whatsapp_message(msg)

    if grievance is None:
        # Voice message arrived but OPENAI_API_KEY not set — tell the citizen
        if msg.media_type == "audio":
            reply = _VOICE_NOT_SUPPORTED.get("en")
            await send_text(msg.from_number, reply)
        return

    ack = build_ack_message(
        grievance.grievance_id,
        grievance.urgency.value,
        grievance.category.value,
        grievance.language_detected,
    )
    await send_text(msg.from_number, ack)
