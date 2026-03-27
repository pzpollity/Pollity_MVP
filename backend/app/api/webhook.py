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
    logger.info("DEBUG verify — received token: %r  settings token: %r", hub_verify_token, settings.WA_VERIFY_TOKEN)
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


async def _handle_message(msg):
    grievance = await process_whatsapp_message(msg)
    if grievance is None:
        return

    ack = build_ack_message(
        grievance.grievance_id,
        grievance.urgency.value,
        grievance.category.value,
    )
    await send_text(msg.from_number, ack)
