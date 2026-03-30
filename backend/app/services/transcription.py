"""
Audio Transcription Service
-----------------------------
Transcribes WhatsApp voice messages using OpenAI Whisper.
WhatsApp sends audio as audio/ogg (Opus codec), which Whisper supports natively.

Requires OPENAI_API_KEY in environment. If not set, raises RuntimeError
so the caller can send a graceful "voice not supported" reply to the citizen.

No openai SDK needed — calls the REST endpoint directly via httpx.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"

# Whisper language hints for common Indian languages (ISO 639-1)
# Passing the hint improves accuracy significantly for accented speech.
_WHISPER_LANG_HINTS = {"hi", "mr", "ta", "te", "kn", "gu", "pa", "en"}


async def transcribe_audio(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    language_hint: str | None = None,
) -> str:
    """
    Transcribe audio bytes to text using OpenAI Whisper.

    Parameters
    ----------
    audio_bytes : bytes
        Raw audio file (OGG/Opus from WhatsApp, or MP3/WAV/M4A).
    mime_type : str
        MIME type of the audio. Used to determine file extension for the upload.
    language_hint : str | None
        ISO 639-1 code to hint Whisper (e.g. "hi", "mr"). Pass None for auto-detect.

    Returns
    -------
    str
        Transcribed text.

    Raises
    ------
    RuntimeError
        If OPENAI_API_KEY is not configured.
    httpx.HTTPStatusError
        If the Whisper API returns an error.
    """
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured — voice transcription unavailable.")

    # Map MIME type to a filename extension Whisper accepts
    _ext_map = {
        "audio/ogg": "voice.ogg",
        "audio/mpeg": "voice.mp3",
        "audio/mp4": "voice.mp4",
        "audio/wav": "voice.wav",
        "audio/webm": "voice.webm",
        "audio/m4a": "voice.m4a",
    }
    # Strip codec suffix (e.g. "audio/ogg; codecs=opus" → "audio/ogg")
    base_mime = mime_type.split(";")[0].strip()
    filename = _ext_map.get(base_mime, "voice.ogg")

    data: dict = {"model": "whisper-1"}
    if language_hint and language_hint in _WHISPER_LANG_HINTS:
        data["language"] = language_hint

    headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _WHISPER_URL,
            headers=headers,
            data=data,
            files={"file": (filename, audio_bytes, base_mime)},
        )
        resp.raise_for_status()

    text = resp.json().get("text", "").strip()
    logger.info("Whisper transcribed %d chars from %d bytes of audio", len(text), len(audio_bytes))
    return text
