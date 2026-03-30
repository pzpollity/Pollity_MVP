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
from app.services.grievance_service import process_whatsapp_message
from app.services.whatsapp import (
    build_ack_message,
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


_VOICE_NOT_SUPPORTED = {
    "en": "We received your voice message but voice processing is not yet enabled. Please send your grievance as a text message or photograph of a letter.",
    "hi": "हमें आपका वॉइस संदेश मिला, लेकिन अभी वॉइस प्रोसेसिंग उपलब्ध नहीं है। कृपया अपनी शिकायत टेक्स्ट संदेश या पत्र की फ़ोटो के रूप में भेजें।",
    "mr": "आम्हाला तुमचा व्हॉइस मेसेज मिळाला, पण आत्ता व्हॉइस प्रोसेसिंग उपलब्ध नाही. कृपया तुमची तक्रार मजकूर संदेश किंवा पत्राच्या फोटोद्वारे पाठवा.",
}


async def _handle_message(msg):
    grievance = await process_whatsapp_message(msg)

    if grievance is None:
        # Voice message arrived but OPENAI_API_KEY not set — tell the citizen
        if msg.media_type == "audio":
            reply = _VOICE_NOT_SUPPORTED.get("en")  # default; language unknown at this point
            await send_text(msg.from_number, reply)
        return

    ack = build_ack_message(
        grievance.grievance_id,
        grievance.urgency.value,
        grievance.category.value,
        grievance.language_detected,
    )
    await send_text(msg.from_number, ack)
