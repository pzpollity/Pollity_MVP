"""
Text-to-Speech Service
-----------------------
Generates natural-sounding speech audio using OpenAI TTS (tts-1).
Supports Hindi, Marathi, and English — OpenAI's multilingual model handles
Devanagari script natively, making it far better than Twilio's built-in
<Say> for Indian languages.

Returns raw MP3 bytes; callers are responsible for serving/caching.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TTS_URL = "https://api.openai.com/v1/audio/speech"

# Maps ISO-639-1 language code → OpenAI TTS voice
# "nova" has the best multilingual quality for Indian languages
_VOICE_MAP = {
    "hi": "nova",
    "mr": "nova",
    "en": "nova",
}
_DEFAULT_VOICE = "nova"


async def synthesize_speech(text: str, language: str = "en") -> bytes:
    """
    Convert text to speech audio (MP3).

    Parameters
    ----------
    text : str
        Text to synthesize. Can be Hindi (Devanagari or Roman), Marathi, or English.
    language : str
        ISO-639-1 language code hint ("hi", "mr", "en"). Used for voice selection.

    Returns
    -------
    bytes
        Raw MP3 audio bytes.

    Raises
    ------
    RuntimeError
        If OPENAI_API_KEY is not configured.
    httpx.HTTPStatusError
        If the TTS API returns an error.
    """
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured — TTS unavailable.")

    voice = _VOICE_MAP.get(language, _DEFAULT_VOICE)

    payload = {
        "model": "tts-1",          # fast, low-latency model
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }

    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_TTS_URL, json=payload, headers=headers)
        resp.raise_for_status()

    logger.info("TTS generated %d bytes for %d chars [lang=%s]", len(resp.content), len(text), language)
    return resp.content
