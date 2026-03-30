"""
OCR Service
-----------
Extracts text from scanned / photographed letter images using Claude Vision.
Handles Devanagari (Hindi / Marathi), English, and other Indian scripts.

Supports: image/jpeg, image/png, image/gif, image/webp (Claude Vision limits).
Max recommended size: 5 MB (base64 overhead adds ~33%).
"""

import base64
import logging

from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


_OCR_SYSTEM = """\
You are an OCR engine for a civic grievance management system in India.
Your task is to extract ALL text from the provided letter or document image.

RULES:
1. Extract text exactly as written — preserve paragraph structure and line breaks.
2. Handle any script: Devanagari (Hindi / Marathi), English, Tamil, Telugu, Gujarati, etc.
3. For typed letters, transcribe every word, including dates, addresses, and salutations.
4. For handwritten content, do your best and mark uncertain words with [?].
5. Do NOT summarize, translate, reformat, or interpret — raw text ONLY.
6. If the image is unreadable (too blurry, empty, non-document), respond with exactly:
   [UNREADABLE: <one-line reason>]
7. Output ONLY the extracted text — no preamble, no explanation.
"""


async def extract_text_from_image(
    image_bytes: bytes,
    media_type: str = "image/jpeg",
) -> str:
    """
    Extract text from a letter / document image using Claude Vision.

    Parameters
    ----------
    image_bytes : bytes
        Raw image bytes.
    media_type : str
        MIME type — one of: image/jpeg, image/png, image/gif, image/webp.

    Returns
    -------
    str
        Extracted text, or "[UNREADABLE: ...]" if the image cannot be parsed.
    """
    client = _get_client()

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=_OCR_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract all text from this letter or document.",
                    },
                ],
            }
        ],
    )

    extracted = response.content[0].text.strip()
    logger.info("OCR extracted %d characters (media_type=%s)", len(extracted), media_type)
    return extracted
