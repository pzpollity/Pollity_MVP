from __future__ import annotations
from enum import Enum
from datetime import datetime
from pydantic import BaseModel


class GrievanceStatus(str, Enum):
    REGISTERED = "registered"
    ACKNOWLEDGED = "acknowledged"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    VERIFIED = "verified"
    CLOSED = "closed"


class GrievanceCategory(str, Enum):
    INFRASTRUCTURE = "infrastructure"
    WELFARE_SCHEMES = "welfare_schemes"
    PUBLIC_SAFETY = "public_safety"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    LAND_REVENUE = "land_revenue"
    CORRUPTION = "corruption"
    OTHERS = "others"


class UrgencyLevel(str, Enum):
    CRITICAL = "critical"    # life/safety threat
    HIGH = "high"            # time-sensitive deadlines
    MEDIUM = "medium"        # ongoing hardship
    LOW = "low"              # general improvement


class GrievanceChannel(str, Enum):
    WHATSAPP = "whatsapp"
    WALK_IN = "walk_in"
    PHONE = "phone"
    LETTER = "letter"
    EMAIL = "email"
    SOCIAL_MEDIA = "social_media"
    CPGRAMS = "cpgrams"


# ── Inbound (from WhatsApp webhook) ──────────────────────────────────────────

class IncomingMessage(BaseModel):
    wa_message_id: str
    from_number: str      # citizen's WhatsApp number (E.164)
    office_phone_id: str  # WA Business phone number ID (maps to an office)
    body: str             # text body, or empty string for pure media messages
    timestamp: datetime
    # Media fields — set for image / audio messages, None for plain text
    media_id: str | None = None    # Meta media object ID (used to download)
    media_type: str | None = None  # "image" | "audio" | "location"
    media_mime: str | None = None  # e.g. "image/jpeg", "audio/ogg; codecs=opus"
    # Location fields — set when citizen shares a WhatsApp location pin
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None  # WhatsApp-provided place name (optional)


# ── Classification result from Claude ────────────────────────────────────────

class ClassificationResult(BaseModel):
    category: GrievanceCategory
    urgency: UrgencyLevel
    summary: str              # 1–2 sentence summary in English
    language_detected: str    # ISO 639-1 (e.g. "hi", "mr", "en")
    location_text: str | None = None  # extracted area/landmark from message text
    is_duplicate: bool
    duplicate_of_id: str | None = None


# ── DB row (grievances table) ─────────────────────────────────────────────────

class Grievance(BaseModel):
    id: str                         # UUID, set by Supabase
    grievance_id: str               # human-readable e.g. GR-2024-0001
    office_id: str                  # FK → offices.id
    citizen_name: str | None = None
    citizen_contact: str            # WhatsApp E.164 or walk-in placeholder
    channel: GrievanceChannel
    raw_text: str
    category: GrievanceCategory
    urgency: UrgencyLevel
    summary: str
    language_detected: str
    status: GrievanceStatus = GrievanceStatus.REGISTERED
    assigned_to: str | None = None  # staff member name / dept
    next_action: str | None = None
    is_duplicate: bool = False
    duplicate_of_id: str | None = None
    location_text: str | None = None  # human-readable area (from text or geocoding)
    latitude: float | None = None
    longitude: float | None = None
    filed_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
