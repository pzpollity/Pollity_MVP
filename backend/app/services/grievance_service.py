"""
Grievance Service
------------------
Orchestrates the full intake pipeline:
  1. Look up office
  2. Fetch recent open grievances for duplicate detection
  3. Resolve raw text (text / OCR / transcription / location pin)
  4. Classify with Claude Haiku (category, urgency, summary, location_text)
  5. Reverse-geocode GPS coords if provided (WhatsApp location pin)
  6. Persist to Supabase
  7. Check for location cluster before firing critical alert
  8. Fire critical alert (individual or cluster-bundled)
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.core.database import get_db
from app.models.grievance import (
    Grievance,
    GrievanceChannel,
    GrievanceStatus,
    IncomingMessage,
    UrgencyLevel,
)
from app.services.classifier import classify_grievance
from app.services.geocoding import reverse_geocode
from app.services.notifications import fire_critical_alerts, fire_cluster_alert
from app.services.ocr import extract_text_from_image
from app.services.transcription import transcribe_audio
from app.services.whatsapp import download_media

logger = logging.getLogger(__name__)

_anthropic_client: AsyncAnthropic | None = None


def _get_anthropic_client() -> AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


_RELEVANCE_SYSTEM = (
    "You are a filter for a citizen grievance helpline in India. "
    "Respond with ONLY 'yes' or 'no' — nothing else.\n"
    "'yes' = the message is a genuine civic complaint, grievance, or request for help "
    "about government services, infrastructure, welfare, public utilities, healthcare, schools, roads, water, electricity, sanitation, permits, pensions, etc.\n"
    "'no' = the message is a test, greeting, random text, joke, spam, or clearly unrelated to civic issues."
)


async def is_grievance_message(raw_text: str) -> bool:
    """
    Lightweight relevance gate: returns True if the text is a genuine citizen grievance.
    Fails open (returns True) if ANTHROPIC_API_KEY is missing or Claude errors out,
    so real grievances are never silently dropped.
    """
    if not settings.ANTHROPIC_API_KEY:
        return True

    try:
        client = _get_anthropic_client()
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            system=_RELEVANCE_SYSTEM,
            messages=[{"role": "user", "content": raw_text[:500]}],
        )
        answer = resp.content[0].text.strip().lower()
        result = answer.startswith("y")
        logger.info("Relevance gate: %r → %s", raw_text[:60], "ACCEPT" if result else "REJECT")
        return result
    except Exception:
        logger.exception("Relevance gate Claude call failed — defaulting to accept")
        return True


# Cluster detection: window and minimum count
_CLUSTER_WINDOW_MINUTES = 60
_CLUSTER_THRESHOLD      = 3   # ≥ this many criticals from same area → cluster alert


def _generate_grievance_id(office_short: str, sequence: int, year_month: str) -> str:
    return f"GR-{office_short.upper()}-{year_month}-{sequence:05d}"


def _recent_cluster_count(office_id: str, category: str, location_text: str) -> int:
    """
    Count critical grievances from the same location+category in the last hour.
    Uses a case-insensitive prefix match on location_text (first 30 chars).
    """
    db = get_db()
    since = (
        datetime.now(tz=timezone.utc) - timedelta(minutes=_CLUSTER_WINDOW_MINUTES)
    ).isoformat()
    # Match on first ~25 chars of location to handle slight phrasing differences
    prefix = location_text[:25]
    resp = (
        db.table("grievances")
        .select("id", count="exact")
        .eq("office_id", office_id)
        .eq("urgency", "critical")
        .eq("category", category)
        .ilike("location_text", f"{prefix}%")
        .gte("filed_at", since)
        .execute()
    )
    return resp.count or 0


async def process_whatsapp_message(msg: IncomingMessage) -> Grievance | None:
    """
    Full intake pipeline for one incoming WhatsApp message.
    Handles text, image (OCR), audio (transcription), and location pin.
    Returns the persisted Grievance or None on unrecoverable error.
    """
    db = get_db()

    # ── 1. Resolve office ────────────────────────────────────────────────────
    office_resp = (
        db.table("offices")
        .select("id, short_code, sequence_counter, alert_whatsapp, alert_emails")
        .eq("wa_phone_number_id", msg.office_phone_id)
        .single()
        .execute()
    )
    if not office_resp.data:
        logger.warning("No office found for phone_number_id=%s", msg.office_phone_id)
        return None

    office    = office_resp.data
    office_id = office["id"]
    short_code = office["short_code"]

    # ── 2. Fetch recent open summaries for duplicate detection ────────────────
    recent_resp = (
        db.table("grievances")
        .select("id, summary")
        .eq("office_id", office_id)
        .neq("status", "closed")
        .order("filed_at", desc=True)
        .limit(20)
        .execute()
    )
    existing_summaries = recent_resp.data or []

    # ── 3. Resolve raw text + GPS ─────────────────────────────────────────────
    channel          = GrievanceChannel.WHATSAPP
    raw_text         = msg.body
    lat, lon         = msg.latitude, msg.longitude
    _geocoded_place  = None   # cache to avoid calling Nominatim twice

    if msg.media_type == "location":
        if lat is not None and lon is not None:
            _geocoded_place = await reverse_geocode(lat, lon)
        raw_text = (
            f"Citizen shared their location: "
            f"{_geocoded_place or msg.location_name or 'unknown area'}. "
            f"GPS: {lat:.4f}, {lon:.4f}"
        ) if lat is not None else (msg.body or "Citizen shared a location pin.")

    elif msg.media_id and msg.media_type == "image":
        try:
            media_bytes, mime = await download_media(msg.media_id)
            raw_text = await extract_text_from_image(
                media_bytes, media_type=mime.split(";")[0].strip()
            )
            channel = GrievanceChannel.LETTER
        except Exception:
            logger.exception("Image OCR failed for media_id=%s", msg.media_id)
            raw_text = msg.body or "[Image — could not extract text]"

    elif msg.media_id and msg.media_type == "audio":
        try:
            media_bytes, mime = await download_media(msg.media_id)
            raw_text = await transcribe_audio(media_bytes, mime_type=mime)
        except RuntimeError:
            logger.warning("Voice message received but OPENAI_API_KEY not configured")
            return None
        except Exception:
            logger.exception("Audio transcription failed for media_id=%s", msg.media_id)
            raw_text = "[Voice message — could not transcribe]"

    if not raw_text or raw_text.startswith("["):
        logger.warning("Empty/unreadable content from media_id=%s, skipping", msg.media_id)
        return None

    # ── 4. Classify ───────────────────────────────────────────────────────────
    classification = await classify_grievance(raw_text, existing_summaries)

    # ── 5. Resolve location_text (use cached geocode — no second Nominatim call)
    location_text = None
    if _geocoded_place:
        location_text = _geocoded_place
    elif lat is not None and lon is not None:
        location_text = await reverse_geocode(lat, lon)
    if not location_text:
        location_text = classification.location_text
    if not location_text and msg.location_name:
        location_text = msg.location_name

    # ── 6. Increment monthly sequence counter ─────────────────────────────────
    year_month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    seq_resp = db.rpc("increment_monthly_counter", {
        "office_id_param": office_id,
        "year_month_param": year_month,
    }).execute()
    sequence     = seq_resp.data if seq_resp.data else 1
    grievance_id = _generate_grievance_id(short_code, sequence, year_month)

    # ── 7. Persist ────────────────────────────────────────────────────────────
    now = datetime.now(tz=timezone.utc).isoformat()
    row = {
        "id":                str(uuid.uuid4()),
        "grievance_id":      grievance_id,
        "office_id":         office_id,
        "citizen_contact":   msg.from_number,
        "channel":           channel.value,
        "raw_text":          raw_text,
        "category":          classification.category.value,
        "urgency":           classification.urgency.value,
        "summary":           classification.summary,
        "language_detected": classification.language_detected,
        "location_text":     location_text,
        "latitude":          lat,
        "longitude":         lon,
        "status":            GrievanceStatus.REGISTERED.value,
        "is_duplicate":      classification.is_duplicate,
        "duplicate_of_id":   classification.duplicate_of_id,
        "filed_at":          now,
        "updated_at":        now,
    }

    insert_resp = db.table("grievances").insert(row).execute()
    if not insert_resp.data:
        logger.error("Failed to insert grievance: %s", insert_resp)
        return None

    logger.info(
        "Grievance registered: %s [%s/%s] location=%s",
        grievance_id, classification.category.value,
        classification.urgency.value, location_text,
    )
    grievance = Grievance(**insert_resp.data[0])

    # ── 8. Critical alert with cluster detection ──────────────────────────────
    if classification.urgency == UrgencyLevel.CRITICAL:
        cluster_count = 0
        if location_text:
            cluster_count = _recent_cluster_count(
                office_id, classification.category.value, location_text
            )

        if cluster_count >= _CLUSTER_THRESHOLD:
            await fire_cluster_alert(
                location_text=location_text,
                category=classification.category.value,
                count=cluster_count,
                latest_grievance_id=grievance_id,
                latest_summary=classification.summary,
                alert_whatsapp=office.get("alert_whatsapp"),
                alert_emails=office.get("alert_emails") or [],
            )
        else:
            await fire_critical_alerts(
                grievance_id=grievance_id,
                category=classification.category.value,
                summary=classification.summary,
                citizen_contact=msg.from_number,
                channel=channel.value,
                alert_whatsapp=office.get("alert_whatsapp"),
                alert_emails=office.get("alert_emails") or [],
                location_text=location_text,
            )

    return grievance


async def process_walkin_grievance(
    office_id: str,
    citizen_name: str | None,
    citizen_contact: str,
    channel: "GrievanceChannel",
    raw_text: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> Grievance | None:
    """
    Intake pipeline for walk-in, phone, letter, or email grievances.
    Optionally accepts GPS coordinates (e.g. from a future mobile intake form).
    """
    db = get_db()

    # ── 1. Resolve office ────────────────────────────────────────────────────
    office_resp = (
        db.table("offices")
        .select("id, short_code, sequence_counter, alert_whatsapp, alert_emails")
        .eq("id", office_id)
        .single()
        .execute()
    )
    if not office_resp.data:
        logger.warning("Walk-in: no office found for office_id=%s", office_id)
        return None

    office     = office_resp.data
    short_code = office["short_code"]

    # ── 2. Fetch recent open summaries ───────────────────────────────────────
    recent_resp = (
        db.table("grievances")
        .select("id, summary")
        .eq("office_id", office_id)
        .neq("status", "closed")
        .order("filed_at", desc=True)
        .limit(20)
        .execute()
    )
    existing_summaries = recent_resp.data or []

    # ── 3. Classify ───────────────────────────────────────────────────────────
    classification = await classify_grievance(raw_text, existing_summaries)

    # ── 4. Resolve location_text ──────────────────────────────────────────────
    location_text = None
    if latitude is not None and longitude is not None:
        location_text = await reverse_geocode(latitude, longitude)
    if not location_text:
        location_text = classification.location_text

    # ── 5. Increment monthly sequence counter ─────────────────────────────────
    year_month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    seq_resp = db.rpc("increment_monthly_counter", {
        "office_id_param": office_id,
        "year_month_param": year_month,
    }).execute()
    sequence     = seq_resp.data if seq_resp.data else 1
    grievance_id = _generate_grievance_id(short_code, sequence, year_month)

    # ── 6. Persist ────────────────────────────────────────────────────────────
    now = datetime.now(tz=timezone.utc).isoformat()
    row = {
        "id":                str(uuid.uuid4()),
        "grievance_id":      grievance_id,
        "office_id":         office_id,
        "citizen_name":      citizen_name,
        "citizen_contact":   citizen_contact,
        "channel":           channel.value,
        "raw_text":          raw_text,
        "category":          classification.category.value,
        "urgency":           classification.urgency.value,
        "summary":           classification.summary,
        "language_detected": classification.language_detected,
        "location_text":     location_text,
        "latitude":          latitude,
        "longitude":         longitude,
        "status":            GrievanceStatus.REGISTERED.value,
        "is_duplicate":      classification.is_duplicate,
        "duplicate_of_id":   classification.duplicate_of_id,
        "filed_at":          now,
        "updated_at":        now,
    }

    insert_resp = db.table("grievances").insert(row).execute()
    if not insert_resp.data:
        logger.error("Failed to insert walk-in grievance: %s", insert_resp)
        return None

    logger.info(
        "Walk-in grievance registered: %s [%s/%s] location=%s",
        grievance_id, classification.category.value,
        classification.urgency.value, location_text,
    )
    grievance = Grievance(**insert_resp.data[0])

    # ── 7. Critical alert with cluster detection ──────────────────────────────
    if classification.urgency == UrgencyLevel.CRITICAL:
        cluster_count = 0
        if location_text:
            cluster_count = _recent_cluster_count(
                office_id, classification.category.value, location_text
            )

        if cluster_count >= _CLUSTER_THRESHOLD:
            await fire_cluster_alert(
                location_text=location_text,
                category=classification.category.value,
                count=cluster_count,
                latest_grievance_id=grievance_id,
                latest_summary=classification.summary,
                alert_whatsapp=office.get("alert_whatsapp"),
                alert_emails=office.get("alert_emails") or [],
            )
        else:
            await fire_critical_alerts(
                grievance_id=grievance_id,
                category=classification.category.value,
                summary=classification.summary,
                citizen_contact=citizen_contact,
                channel=channel.value,
                alert_whatsapp=office.get("alert_whatsapp"),
                alert_emails=office.get("alert_emails") or [],
                location_text=location_text,
            )

    return grievance
