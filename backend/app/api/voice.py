"""
Voice Intake API — Twilio Webhook Handlers
-------------------------------------------
Handles inbound phone calls via Twilio for the Jan-Sunwai grievance line.

Endpoints:
  POST /api/voice/incoming   — Twilio calls this when a new call arrives
  POST /api/voice/gather     — Twilio calls this after each recording
  POST /api/voice/status     — Twilio calls this when the call ends (status callback)
  GET  /api/voice/tts/{token} — Serves generated TTS audio to Twilio <Play>

Flow:
  1. /incoming  → greet caller, start recording
  2. /gather    → transcribe (Whisper) → Claude response → TTS → play & re-record
                  (loops until grievance complete or transfer requested)
  3. /status    → save finalized grievance to Supabase
  4. /tts/{id}  → streams MP3 audio bytes for Twilio <Play>

Call forwarding:
  When the citizen asks for a human, Twilio <Dial> forwards to VOICE_FORWARD_NUMBER.

Twilio configuration required:
  - Phone number → Voice Webhook (HTTP POST) → https://<your-host>/api/voice/incoming
  - Status Callback URL → https://<your-host>/api/voice/status
"""

import asyncio
import logging
import secrets
import time

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import Response as FastAPIResponse
from twilio.twiml.voice_response import Dial, VoiceResponse

from app.core.config import settings
from app.models.grievance import GrievanceChannel
from app.services import voice_agent
from app.services.grievance_service import process_walkin_grievance
from app.services.tts import synthesize_speech
from app.services.transcription import transcribe_audio

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)

# ── TTS audio cache ─────────────────────────────────────────────────────────
# Maps short-lived token → (mp3_bytes, expires_at_unix)
# Tokens expire after 5 minutes — enough time for Twilio to fetch them.
_tts_cache: dict[str, tuple[bytes, float]] = {}
_TTS_TTL_SECONDS = 300


def _store_tts(audio_bytes: bytes) -> str:
    """Cache audio bytes and return a one-time token."""
    token = secrets.token_urlsafe(16)
    _tts_cache[token] = (audio_bytes, time.time() + _TTS_TTL_SECONDS)
    # Opportunistically clean up expired entries
    expired = [k for k, (_, exp) in _tts_cache.items() if time.time() > exp]
    for k in expired:
        _tts_cache.pop(k, None)
    return token


def _twiml_play_or_say(text: str, language: str, audio_token: str | None) -> str:
    """
    Return the XML fragment that either plays a TTS audio file or falls back
    to Twilio's built-in <Say> if TTS generation failed.
    """
    if audio_token:
        return f'<Play>{settings.BASE_URL}/api/voice/tts/{audio_token}</Play>'
    # Fallback: Twilio built-in TTS
    lang_map = {"hi": "hi-IN", "mr": "mr-IN", "en": "en-IN"}
    twilio_lang = lang_map.get(language, "hi-IN")
    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<Say language="{twilio_lang}">{safe_text}</Say>'


async def _make_response_twiml(
    response_text: str,
    language: str,
    next_action: str,
    hangup: bool = False,
    forward_number: str | None = None,
) -> str:
    """
    Build a complete TwiML response string:
      - Generate TTS audio (falls back to <Say> if OpenAI TTS fails)
      - Play audio
      - Then either: <Record> for next turn | <Dial> for transfer | <Hangup>
    """
    # Try to generate TTS audio
    audio_token: str | None = None
    if settings.OPENAI_API_KEY:
        try:
            audio_bytes = await synthesize_speech(response_text, language)
            audio_token = _store_tts(audio_bytes)
        except Exception:
            logger.warning("TTS generation failed, falling back to Twilio <Say>")

    play_fragment = _twiml_play_or_say(response_text, language, audio_token)

    if forward_number:
        safe_number = forward_number.replace("&", "&amp;")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"{play_fragment}"
            f"<Dial>{safe_number}</Dial>"
            "</Response>"
        )

    if hangup:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"{play_fragment}"
            "<Hangup/>"
            "</Response>"
        )

    # Continue conversation — record next user input
    action_url = f"{settings.BASE_URL}{next_action}"
    status_url = f"{settings.BASE_URL}/api/voice/status"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{play_fragment}"
        f'<Record action="{action_url}" '
        f'statusCallback="{status_url}" '
        'maxLength="60" '
        'timeout="5" '
        'playBeep="true" '
        'trim="trim-silence"/>'
        "</Response>"
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/incoming")
async def incoming_call(request: Request):
    """
    Entry point: Twilio calls this when someone dials the Jan-Sunwai number.
    Creates a session, plays the greeting, starts recording.
    """
    form = await request.form()
    call_sid   = form.get("CallSid", "unknown")
    from_number = form.get("From", "unknown")

    logger.info("Incoming call: call_sid=%s from=%s", call_sid, from_number)

    office_id = settings.VOICE_OFFICE_ID
    if not office_id:
        logger.error("VOICE_OFFICE_ID not configured — rejecting call")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Say language="hi-IN">Khed aahe, seva uplabdha nahi. Nantar call kara.</Say>'
            "<Hangup/>"
            "</Response>"
        )
        return FastAPIResponse(content=twiml, media_type="application/xml")

    voice_agent.create_session(call_sid, office_id, from_number)

    greeting = voice_agent.get_greeting()
    twiml = await _make_response_twiml(
        response_text=greeting["response_text"],
        language=greeting["language"],
        next_action="/api/voice/gather",
    )
    return FastAPIResponse(content=twiml, media_type="application/xml")


@router.post("/gather")
async def gather(request: Request):
    """
    Called by Twilio after each recording.
    Downloads the audio → Whisper transcription → Claude response → TTS → TwiML.
    """
    form = await request.form()
    call_sid      = form.get("CallSid", "unknown")
    recording_url = form.get("RecordingUrl")
    call_status   = form.get("CallStatus", "")

    logger.info("Gather: call_sid=%s status=%s recording_url=%s", call_sid, call_status, recording_url)

    session = voice_agent.get_session(call_sid)
    if session is None:
        # Session lost (restart or crash) — hang up gracefully
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Say language="hi-IN">Maafi chahta hoon, koi gadbad ho gayi. Dobara call karein.</Say>'
            "<Hangup/>"
            "</Response>"
        )
        return FastAPIResponse(content=twiml, media_type="application/xml")

    # ── Download and transcribe recording ────────────────────────────────────
    user_text = ""
    if recording_url:
        mp3_url = recording_url.rstrip("/") + ".mp3"
        try:
            # Twilio requires auth to download recordings
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    mp3_url,
                    auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                )
                resp.raise_for_status()
                audio_bytes = resp.content

            user_text = await transcribe_audio(
                audio_bytes,
                mime_type="audio/mpeg",
                language_hint=session.language if session.language in {"hi", "mr", "en"} else None,
            )
            logger.info("Transcribed: %r", user_text[:100])
        except Exception:
            logger.exception("Failed to transcribe recording for call_sid=%s", call_sid)
            user_text = ""

    # If nothing was said (silence / very short recording), prompt again
    if not user_text.strip():
        silence_msgs = {
            "hi": "Maafi karein, mujhe kuch sunai nahi diya. Kripya dobara bolein.",
            "mr": "Maafi kara, mala aikale nahi. Krupaya punha sanga.",
            "en": "Sorry, I did not hear anything. Please speak again.",
        }
        msg = silence_msgs.get(session.language, silence_msgs["en"])
        twiml = await _make_response_twiml(
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
        twiml = await _make_response_twiml(
            response_text=response_text,
            language=language,
            next_action="/api/voice/gather",
            forward_number=settings.VOICE_FORWARD_NUMBER,
        )
        # Save grievance before transferring (partial data is still useful)
        asyncio.create_task(_save_grievance(call_sid))

    elif convo_complete:
        twiml = await _make_response_twiml(
            response_text=response_text,
            language=language,
            next_action="/api/voice/gather",
            hangup=True,
        )
        asyncio.create_task(_save_grievance(call_sid))

    else:
        twiml = await _make_response_twiml(
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


@router.get("/tts/{token}")
async def serve_tts(token: str):
    """Serve a pre-generated TTS audio file to Twilio <Play>."""
    entry = _tts_cache.get(token)
    if not entry:
        return FastAPIResponse(content="Not found", status_code=404)
    audio_bytes, expires_at = entry
    if time.time() > expires_at:
        _tts_cache.pop(token, None)
        return FastAPIResponse(content="Expired", status_code=404)
    return FastAPIResponse(content=audio_bytes, media_type="audio/mpeg")


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _save_grievance(call_sid: str) -> None:
    """
    Finalize and persist the grievance from a completed/ended call.
    Removes the session after saving.
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
        else:
            logger.warning("process_walkin_grievance returned None for call_sid=%s", call_sid)
    except Exception:
        logger.exception("Failed to save grievance for call_sid=%s", call_sid)
