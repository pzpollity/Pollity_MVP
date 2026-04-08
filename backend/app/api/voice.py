"""
Voice Intake API — Twilio Webhook Handlers
-------------------------------------------
Handles inbound phone calls via Twilio for the Jan-Sunwai grievance line.

Endpoints:
  POST /api/voice/incoming   — Twilio calls this when a new call arrives
  POST /api/voice/gather     — Twilio calls this after each recording
  POST /api/voice/status     — Twilio calls this when the call ends (status callback)

Flow:
  1. /incoming  → greet caller, start recording
  2. /gather    → transcribe (Whisper) → Claude response → Twilio <Say> → re-record
                  (loops until grievance complete or transfer requested)
  3. /status    → save finalized grievance to Supabase

TTS: Uses Twilio's built-in neural voices (Amazon Polly.Kajal for Hindi/English,
     standard <Say> for Marathi). No external TTS API required.

Call forwarding:
  When the citizen asks for a human, Twilio <Dial> forwards to VOICE_FORWARD_NUMBER.

Twilio configuration required:
  - Phone number → Voice Webhook (HTTP POST) → https://<your-host>/api/voice/incoming
  - Status Callback URL → https://<your-host>/api/voice/status
"""

import asyncio
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response as FastAPIResponse

from app.core.config import settings
from app.models.grievance import GrievanceChannel
from app.services import voice_agent
from app.services.grievance_service import process_walkin_grievance

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)


def _say_fragment(text: str, language: str) -> str:
    """
    Build a Twilio <Say> fragment using Amazon Polly neural voices.
      Hindi   → Polly.Kajal   (hi-IN, female, neural)
      English → Polly.Raveena (en-IN, female, Indian accent)
    """
    if language == "en":
        voice, twilio_lang = "Polly.Raveena", "en-IN"
    else:
        voice, twilio_lang = "Polly.Kajal", "hi-IN"
    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<Say voice="{voice}" language="{twilio_lang}">{safe_text}</Say>'


def _make_response_twiml(
    response_text: str,
    language: str,
    next_action: str,
    hangup: bool = False,
    forward_number: str | None = None,
) -> str:
    """
    Build a complete TwiML response string using Twilio neural <Say>.
    Then either: <Record> for next turn | <Dial> for transfer | <Hangup>
    """
    say_fragment = _say_fragment(response_text, language)

    if forward_number:
        safe_number = forward_number.replace("&", "&amp;")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"{say_fragment}"
            f"<Dial>{safe_number}</Dial>"
            "</Response>"
        )

    if hangup:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"{say_fragment}"
            "<Hangup/>"
            "</Response>"
        )

    # Continue conversation — gather next speech input via Twilio STT
    action_url = f"{settings.BASE_URL}{next_action}"
    gather_lang = "en-IN" if language == "en" else "hi-IN"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech" action="{action_url}" '
        f'language="{gather_lang}" '
        'speechTimeout="auto" '
        'timeout="10">'
        f"{say_fragment}"
        "</Gather>"
        # Fallback if no speech detected — loop back
        f'<Redirect>{action_url}</Redirect>'
        "</Response>"
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/incoming")
async def incoming_call(request: Request):
    """
    Entry point: Twilio calls this when someone dials the Jan-Sunwai number.
    Plays a trilingual language selection menu (1=Hindi, 2=Marathi, 3=English).
    """
    form = await request.form()
    call_sid    = form.get("CallSid", "unknown")
    from_number = form.get("From", "unknown")

    logger.info("Incoming call: call_sid=%s from=%s", call_sid, from_number)

    office_id = settings.VOICE_OFFICE_ID
    if not office_id:
        logger.error("VOICE_OFFICE_ID not configured — rejecting call")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Say voice="Polly.Kajal" language="hi-IN">Seva uplabdha nahi. Baad mein call karein.</Say>'
            "<Hangup/>"
            "</Response>"
        )
        return FastAPIResponse(content=twiml, media_type="application/xml")

    voice_agent.create_session(call_sid, office_id, from_number)

    action_url = f"{settings.BASE_URL}/api/voice/language"
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="dtmf" numDigits="1" timeout="8" action="{action_url}">'
        '<Say voice="Polly.Kajal" language="hi-IN">'
        "नमस्कार! जन सुन हेल्पलाइन में आपका स्वागत है। "
        "हिंदी के लिए 1 दबाएं। "
        "For English press 2."
        "</Say>"
        "</Gather>"
        # No digit pressed — default to Hindi
        f'<Redirect>{action_url}</Redirect>'
        "</Response>"
    )
    return FastAPIResponse(content=twiml, media_type="application/xml")


@router.post("/language")
async def select_language(request: Request):
    """
    Receives DTMF digit from language menu.
    Sets session language and redirects into the main conversation.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "unknown")
    digit    = form.get("Digits", "").strip()

    lang_map = {"1": "hi", "2": "en"}
    language = lang_map.get(digit, "hi")  # default Hindi if no/invalid digit

    session = voice_agent.get_session(call_sid)
    if session:
        session.language = language
        logger.info("Language selected: call_sid=%s digit=%s lang=%s", call_sid, digit, language)

    # Play confirmation + start conversation
    confirms = {
        "hi": "हिंदी चुना गया। कृपया अपनी समस्या बताइए।",
        "en": "English selected. Please tell me your problem.",
    }
    twiml = _make_response_twiml(
        response_text=confirms[language],
        language=language,
        next_action="/api/voice/gather",
    )
    return FastAPIResponse(content=twiml, media_type="application/xml")


@router.post("/gather")
async def gather(request: Request):
    """
    Called by Twilio after <Gather input="speech"> captures speech.
    Reads SpeechResult (Twilio STT) → Claude response → TwiML.
    No external transcription API needed.
    """
    form = await request.form()
    call_sid    = form.get("CallSid", "unknown")
    user_text   = form.get("SpeechResult", "").strip()
    confidence  = form.get("Confidence", "")

    logger.info("Gather: call_sid=%s confidence=%s speech=%r", call_sid, confidence, user_text[:80])

    session = voice_agent.get_session(call_sid)
    if session is None:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Say language="hi-IN">Maafi chahta hoon, koi gadbad ho gayi. Dobara call karein.</Say>'
            "<Hangup/>"
            "</Response>"
        )
        return FastAPIResponse(content=twiml, media_type="application/xml")

    # If nothing was said (silence / timeout), prompt again
    if not user_text:
        silence_msgs = {
            "hi": "Maafi karein, mujhe kuch sunai nahi diya. Kripya dobara bolein.",
            "mr": "Maafi kara, mala aikale nahi. Krupaya punha sanga.",
            "en": "Sorry, I did not hear anything. Please speak again.",
        }
        msg = silence_msgs.get(session.language, silence_msgs["en"])
        twiml = _make_response_twiml(
            response_text=msg,
            language=session.language,
            next_action="/api/voice/gather",
        )
        return FastAPIResponse(content=twiml, media_type="application/xml")

    # ── Feed to Claude voice agent ────────────────────────────────────────────
    result = await voice_agent.process_turn(call_sid, user_text)

    response_text      = result.get("response_text", "")
    language           = result.get("language", session.language)
    transfer_requested = result.get("transfer_requested", False)
    convo_complete     = result.get("conversation_complete", False)

    # ── Build TwiML response ──────────────────────────────────────────────────
    if transfer_requested and settings.VOICE_FORWARD_NUMBER:
        logger.info("Transferring call %s to %s", call_sid, settings.VOICE_FORWARD_NUMBER)
        twiml = _make_response_twiml(
            response_text=response_text,
            language=language,
            next_action="/api/voice/gather",
            forward_number=settings.VOICE_FORWARD_NUMBER,
        )
        # Save grievance before transferring (partial data is still useful)
        asyncio.create_task(_save_grievance(call_sid))

    elif convo_complete:
        twiml = _make_response_twiml(
            response_text=response_text,
            language=language,
            next_action="/api/voice/gather",
            hangup=True,
        )
        asyncio.create_task(_save_grievance(call_sid))

    else:
        twiml = _make_response_twiml(
            response_text=response_text,
            language=language,
            next_action="/api/voice/gather",
        )

    return FastAPIResponse(content=twiml, media_type="application/xml")


@router.post("/status")
async def call_status(request: Request):
    """
    Twilio calls this when a call ends (status callback).
    Saves the grievance if not already saved by /gather.
    """
    form = await request.form()
    call_sid    = form.get("CallSid", "unknown")
    call_status = form.get("CallStatus", "unknown")

    logger.info("Call status: call_sid=%s status=%s", call_sid, call_status)

    # Save grievance on any terminal status (completed, no-answer, busy, failed)
    terminal_statuses = {"completed", "no-answer", "busy", "failed", "canceled"}
    if call_status in terminal_statuses:
        session = voice_agent.get_session(call_sid)
        if session and session.transcript.strip():
            await _save_grievance(call_sid)

    return FastAPIResponse(content="", status_code=204)


# ── Internal helpers ──────────────────────────────────────────────────────────

_SMS_ACK = {
    "hi": "आपकी शिकायत दर्ज हो गई है। संदर्भ संख्या: {gid}। Jan-Sunwai - Pollity.in",
    "en": "Your grievance has been registered. Reference: {gid}. Jan-Sunwai - Pollity.in",
}


async def _send_sms_ack(to: str, grievance_id: str, language: str) -> None:
    """Send an SMS confirmation to the citizen with their grievance reference number."""
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
        logger.debug("SMS ACK skipped — TWILIO_FROM_NUMBER not configured")
        return
    if not to or to == "unknown":
        return

    body = _SMS_ACK.get(language, _SMS_ACK["hi"]).format(gid=grievance_id)
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                data={"From": settings.TWILIO_FROM_NUMBER, "To": to, "Body": body},
            )
            resp.raise_for_status()
        logger.info("SMS ACK sent to %s for grievance %s", to, grievance_id)
    except Exception:
        logger.exception("Failed to send SMS ACK to %s for %s", to, grievance_id)


async def _save_grievance(call_sid: str) -> None:
    """
    Finalize and persist the grievance from a completed/ended call.
    Removes the session after saving, then sends SMS ACK to citizen.
    """
    session = voice_agent.remove_session(call_sid)
    if not session:
        return

    raw_text = session.transcript.strip()
    if not raw_text:
        logger.info("Skipping grievance save — empty transcript for call_sid=%s", call_sid)
        return

    # Use issue_summary as raw_text if available (cleaner signal for classifier)
    text_to_classify = session.issue_summary or raw_text

    try:
        grievance = await process_walkin_grievance(
            office_id=session.office_id,
            citizen_name=session.citizen_name,
            citizen_contact=session.from_number,
            channel=GrievanceChannel.PHONE,
            raw_text=text_to_classify,
        )
        if grievance:
            logger.info(
                "Phone grievance saved: %s from call_sid=%s",
                grievance.grievance_id, call_sid,
            )
            await _send_sms_ack(session.from_number, grievance.grievance_id, session.language)
        else:
            logger.warning("process_walkin_grievance returned None for call_sid=%s", call_sid)
    except Exception:
        logger.exception("Failed to save grievance for call_sid=%s", call_sid)
