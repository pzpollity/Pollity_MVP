"""
Proactive Citizen Follow-up Service
--------------------------------------
Sends time-triggered messages to citizens when their grievance is approaching
or has breached its SLA, and asks for resolution verification.

Three triggers:
  50pct_sla         : SLA is 50% elapsed, case still open — reassurance update
  90pct_sla         : SLA is 90% elapsed, case still open — escalation notice
  resolution_verify : Case marked 'resolved' > 7 days ago, citizen unverified

Channel priority:
  WhatsApp-filed  → send via WhatsApp (subject to Meta 24h window; best-effort)
  phone-filed     → send via SMS (Twilio)
  walk_in / other → skip (no reliable contact)

Called from POST /api/followup/run (protected endpoint, triggered by cron).
"""

import logging
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.services.sms import send_sms
from app.services.whatsapp import send_text

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

SLA_HOURS = {"critical": 24, "high": 72, "medium": 168, "low": 504}

# How long after "resolved" before we ask the citizen to verify
RESOLUTION_VERIFY_DAYS = 7

# ── Message templates ──────────────────────────────────────────────────────────

_50PCT_TEMPLATES: dict[str, str] = {
    "en": (
        "Update on your grievance *{gid}*: we are actively working on it and will resolve it "
        "within the committed timeframe. Thank you for your patience.\n\n_Jan Sunn · NetaWork.in_"
    ),
    "hi": (
        "आपकी शिकायत *{gid}* अपडेट: हम इसे प्राथमिकता से हल कर रहे हैं। "
        "निर्धारित समय के भीतर समाधान किया जाएगा।\n\n_जन सुन · NetaWork.in_"
    ),
    "mr": (
        "तुमच्या तक्रारीवर *{gid}* अपडेट: आम्ही यावर सक्रियपणे काम करत आहोत. "
        "ठरलेल्या वेळेत निराकरण केले जाईल।\n\n_जन सुन · NetaWork.in_"
    ),
}

_90PCT_TEMPLATES: dict[str, str] = {
    "en": (
        "Important update on grievance *{gid}*: the resolution deadline is approaching. "
        "We are escalating this to ensure it is resolved on priority. "
        "You will receive an update very soon.\n\n_Jan Sunn · NetaWork.in_"
    ),
    "hi": (
        "शिकायत *{gid}* महत्वपूर्ण अपडेट: समय-सीमा समाप्त होने वाली है। "
        "हम इसे तत्काल हल करने के लिए वरिष्ठ अधिकारियों को सूचित कर रहे हैं।\n\n_जन सुन · NetaWork.in_"
    ),
    "mr": (
        "तक्रार *{gid}* महत्त्वाचा अपडेट: मुदत जवळ येत आहे. "
        "आम्ही हे प्राधान्याने सोडवण्यासाठी वरिष्ठ अधिकाऱ्यांना कळवत आहोत.\n\n_जन सुन · NetaWork.in_"
    ),
}

_VERIFY_TEMPLATES: dict[str, str] = {
    "en": (
        "Your grievance *{gid}* was marked as resolved. Was your issue actually resolved?\n\n"
        "Reply *YES* to confirm and close this case.\n"
        "Reply *NO* if the problem remains — we will reopen and escalate immediately.\n\n"
        "_Jan Sunn · NetaWork.in_"
    ),
    "hi": (
        "आपकी शिकायत *{gid}* को हल किया गया बताया गया है। क्या आपकी समस्या वास्तव में हल हुई?\n\n"
        "पुष्टि के लिए *YES* टाइप करें — मामला बंद किया जाएगा।\n"
        "यदि समस्या अभी भी है तो *NO* टाइप करें — हम तुरंत पुनः एस्केलेट करेंगे।\n\n"
        "_जन सुन · NetaWork.in_"
    ),
    "mr": (
        "तुमची तक्रार *{gid}* निराकरण झाली असे सांगण्यात आले आहे. प्रत्यक्षात समस्या सुटली का?\n\n"
        "पुष्टीसाठी *YES* टाइप करा — प्रकरण बंद केले जाईल.\n"
        "समस्या अजूनही असल्यास *NO* टाइप करा — आम्ही तातडीने पुन्हा एस्केलेट करू.\n\n"
        "_जन सुन · NetaWork.in_"
    ),
}

# SMS versions (shorter, no WhatsApp formatting)
_50PCT_SMS: dict[str, str] = {
    "en": "Update on {gid}: we are actively working on your complaint and will resolve it soon. Jan Sunn",
    "hi": "{gid} अपडेट: आपकी शिकायत पर काम जारी है। Jan Sunn",
}
_90PCT_SMS: dict[str, str] = {
    "en": "Urgent update on {gid}: deadline approaching. We are escalating for immediate resolution. Jan Sunn",
    "hi": "{gid}: समय-सीमा समाप्त होने वाली है। तत्काल समाधान के लिए एस्केलेट किया जा रहा है। Jan Sunn",
}
_VERIFY_SMS: dict[str, str] = {
    "en": "Grievance {gid} was marked resolved. Was your issue fixed? Reply YES to close or NO to reopen. Jan Sunn",
    "hi": "शिकायत {gid} हल बताई गई है। समस्या सुलझी? हाँ के लिए YES, नहीं के लिए NO टाइप करें। Jan Sunn",
}


def _pick_lang(lang: str) -> str:
    return lang if lang in ("en", "hi", "mr") else "en"


def _wa_template(trigger: str, gid: str, lang: str) -> str:
    l = _pick_lang(lang)
    if trigger == "50pct_sla":
        return _50PCT_TEMPLATES.get(l, _50PCT_TEMPLATES["en"]).format(gid=gid)
    if trigger == "90pct_sla":
        return _90PCT_TEMPLATES.get(l, _90PCT_TEMPLATES["en"]).format(gid=gid)
    return _VERIFY_TEMPLATES.get(l, _VERIFY_TEMPLATES["en"]).format(gid=gid)


def _sms_template(trigger: str, gid: str, lang: str) -> str:
    l = _pick_lang(lang)
    if trigger == "50pct_sla":
        return _50PCT_SMS.get(l, _50PCT_SMS["en"]).format(gid=gid)
    if trigger == "90pct_sla":
        return _90PCT_SMS.get(l, _90PCT_SMS["en"]).format(gid=gid)
    return _VERIFY_SMS.get(l, _VERIFY_SMS["en"]).format(gid=gid)


# ── Candidate queries ──────────────────────────────────────────────────────────

def _already_sent(office_id: str, trigger: str) -> set[str]:
    """Return the set of grievance UUIDs that already have a followup for this trigger."""
    db = get_db()
    resp = (
        db.table("citizen_followups")
        .select("grievance_uuid")
        .eq("office_id", office_id)
        .eq("trigger", trigger)
        .execute()
    )
    return {r["grievance_uuid"] for r in (resp.data or [])}


def _open_grievances_with_contact(office_id: str) -> list[dict]:
    """All open grievances for this office that have a real citizen contact."""
    db = get_db()
    resp = (
        db.table("grievances")
        .select("id,grievance_id,urgency,status,filed_at,citizen_contact,channel,language_detected")
        .eq("office_id", office_id)
        .not_.in_("status", ["resolved", "verified", "closed"])
        .not_.in_("citizen_contact", ["WALK-IN", ""])
        .execute()
    )
    return resp.data or []


def _resolved_grievances_with_contact(office_id: str) -> list[dict]:
    """Grievances in 'resolved' status with a real contact, for verification follow-up."""
    db = get_db()
    cutoff = (datetime.now(tz=UTC) - timedelta(days=RESOLUTION_VERIFY_DAYS)).isoformat()
    resp = (
        db.table("grievances")
        .select("id,grievance_id,urgency,status,updated_at,citizen_contact,channel,language_detected")
        .eq("office_id", office_id)
        .eq("status", "resolved")
        .not_.in_("citizen_contact", ["WALK-IN", ""])
        .lte("updated_at", cutoff)
        .execute()
    )
    return resp.data or []


def _pct_elapsed(row: dict) -> float:
    try:
        filed = datetime.fromisoformat(row["filed_at"].replace("Z", "+00:00"))
        hours_open = (datetime.now(tz=UTC) - filed).total_seconds() / 3600
        sla = SLA_HOURS.get(row.get("urgency", "medium"), 168)
        return hours_open / sla
    except Exception:
        return 0.0


def _log_followup(office_id: str, grievance: dict, trigger: str, channel: str) -> None:
    db = get_db()
    db.table("citizen_followups").insert({
        "grievance_id":   grievance["grievance_id"],
        "grievance_uuid": grievance["id"],
        "office_id":      office_id,
        "trigger":        trigger,
        "channel":        channel,
    }).execute()


# ── Send helpers ───────────────────────────────────────────────────────────────

async def _dispatch(
    grievance: dict,
    trigger: str,
    office_id: str,
    dry_run: bool = False,
) -> dict:
    """
    Send the follow-up message via WhatsApp or SMS (phone fallback).
    Returns a result dict describing what happened.
    """
    contact = grievance["citizen_contact"]
    channel = grievance.get("channel", "whatsapp")
    lang = grievance.get("language_detected", "en") or "en"
    gid = grievance["grievance_id"]

    use_sms = channel == "phone"
    sent_channel = "sms" if use_sms else "whatsapp"
    msg = _sms_template(trigger, gid, lang) if use_sms else _wa_template(trigger, gid, lang)

    result = {
        "grievance_id": gid,
        "trigger": trigger,
        "channel": sent_channel,
        "contact": contact,
        "dry_run": dry_run,
    }

    if dry_run:
        result["message_preview"] = msg
        logger.info("[DRY RUN] Would send %s via %s to %s", trigger, sent_channel, contact)
        return result

    try:
        if use_sms:
            await send_sms(contact, msg)
        else:
            await send_text(contact, msg)
        _log_followup(office_id, grievance, trigger, sent_channel)
        result["status"] = "sent"
        logger.info("Follow-up %s sent via %s to %s for %s", trigger, sent_channel, contact, gid)
    except Exception:
        logger.exception("Failed to send %s follow-up for %s", trigger, gid)
        result["status"] = "failed"

    return result


# ── Main job ───────────────────────────────────────────────────────────────────

async def run_followup_job(office_id: str, dry_run: bool = False) -> dict:
    """
    Run all three follow-up triggers for one office.
    Returns a summary dict of what was sent.
    """
    results: list[dict] = []

    # ── Trigger 1: 50% SLA ────────────────────────────────────────────────────
    sent_50 = _already_sent(office_id, "50pct_sla")
    open_cases = _open_grievances_with_contact(office_id)

    for g in open_cases:
        if g["id"] in sent_50:
            continue
        pct = _pct_elapsed(g)
        if pct >= 0.5:
            results.append(await _dispatch(g, "50pct_sla", office_id, dry_run))

    # ── Trigger 2: 90% SLA ────────────────────────────────────────────────────
    sent_90 = _already_sent(office_id, "90pct_sla")
    for g in open_cases:
        if g["id"] in sent_90:
            continue
        pct = _pct_elapsed(g)
        if pct >= 0.9:
            results.append(await _dispatch(g, "90pct_sla", office_id, dry_run))

    # ── Trigger 3: Resolution verification ────────────────────────────────────
    sent_verify = _already_sent(office_id, "resolution_verify")
    resolved_cases = _resolved_grievances_with_contact(office_id)

    for g in resolved_cases:
        if g["id"] in sent_verify:
            continue
        results.append(await _dispatch(g, "resolution_verify", office_id, dry_run))

    sent_count = sum(1 for r in results if r.get("status") == "sent")
    skipped = sum(1 for r in results if r.get("dry_run"))

    return {
        "office_id": office_id,
        "dry_run": dry_run,
        "total_processed": len(results),
        "sent": sent_count,
        "dry_run_previews": skipped,
        "details": results,
    }


# ── "NO" reply handling ────────────────────────────────────────────────────────

_NO_WORDS = {"no", "nahi", "nahin", "नहीं", "नही", "naa", "na"}
_YES_WORDS = {"yes", "haan", "haan ji", "हाँ", "हां", "ha", "ji haan", "yes hai"}


def is_no_reply(text: str) -> bool:
    return text.strip().lower() in _NO_WORDS


def is_yes_reply(text: str) -> bool:
    return text.strip().lower() in _YES_WORDS


def find_pending_verify(citizen_contact: str) -> dict | None:
    """
    Find an unresolved resolution_verify follow-up for this citizen.
    Returns the grievance row or None.
    """
    db = get_db()
    # Find follow-ups sent to this citizen that haven't been replied to
    followups = (
        db.table("citizen_followups")
        .select("grievance_uuid, grievance_id")
        .eq("trigger", "resolution_verify")
        .is_("citizen_replied", "null")
        .execute()
    )
    if not followups.data:
        return None

    uuids = [f["grievance_uuid"] for f in followups.data]

    # Check which of those belong to this citizen
    for uuid in uuids:
        g_resp = (
            db.table("grievances")
            .select("*")
            .eq("id", uuid)
            .eq("citizen_contact", citizen_contact)
            .eq("status", "resolved")
            .limit(1)
            .execute()
        )
        if g_resp.data:
            return g_resp.data[0]
    return None


def mark_followup_replied(grievance_uuid: str, reply: str) -> None:
    db = get_db()
    db.table("citizen_followups").update({
        "citizen_replied": reply,
        "replied_at": datetime.now(tz=UTC).isoformat(),
    }).eq("grievance_uuid", grievance_uuid).eq("trigger", "resolution_verify").is_("citizen_replied", "null").execute()


def reopen_grievance(grievance_uuid: str) -> None:
    """Bump status back to in_progress and raise urgency one level."""
    db = get_db()
    g_resp = db.table("grievances").select("urgency").eq("id", grievance_uuid).single().execute()
    if not g_resp.data:
        return

    current_urgency = g_resp.data.get("urgency", "medium")
    bump = {"low": "medium", "medium": "high", "high": "critical", "critical": "critical"}
    new_urgency = bump.get(current_urgency, current_urgency)

    db.table("grievances").update({
        "status": "in_progress",
        "urgency": new_urgency,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }).eq("id", grievance_uuid).execute()


def advance_to_verified(grievance_uuid: str) -> None:
    """Citizen confirmed resolution — advance to verified."""
    db = get_db()
    now = datetime.now(tz=UTC).isoformat()
    db.table("grievances").update({
        "status": "verified",
        "updated_at": now,
    }).eq("id", grievance_uuid).execute()
