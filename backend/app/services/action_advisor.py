"""
Action Advisor Service
-----------------------
Uses Claude Sonnet to generate a specific, actionable next-step recommendation
for a single grievance. Called on-demand from the dashboard.

Returns:
  action_type    : escalate_to_dept | draft_letter | call_official | field_visit | other
  action_text    : 1-2 sentence recommendation in English (shown to staff)
  target_dept    : which department / official to contact
  draft_message  : ready-to-send draft text to the responsible department

Also persists the suggestion to grievances.suggested_action so the dashboard
can show a cached version without re-calling Sonnet.
"""

import json
import logging
from datetime import datetime, timezone

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)

SLA_HOURS = {"critical": 24, "high": 72, "medium": 168, "low": 504}

_SYSTEM_PROMPT = """\
You are an Action Advisor for Jan Sunn, the AI governance platform used by India's elected representatives.

You receive details of a citizen grievance. Your job: produce ONE specific, actionable next-step \
recommendation for the representative's office staff.

CATEGORY → RESPONSIBLE AUTHORITY (default, override with location context if known):
  infrastructure  : Public Works Department (PWD), Municipal Corporation (civic body), NHAI (highways)
  welfare_schemes : District Collector, Block Development Officer (BDO), NREGA office, CSC
  public_safety   : Police Superintendent / DCP, District Magistrate (DM), SDM
  healthcare      : Chief Medical Officer (CMO), District Health Officer (DHO), NRHM/NHM office
  education       : District Education Officer (DEO), Block Education Officer (BEO)
  land_revenue    : Tehsildar, Sub-Divisional Magistrate (SDM), Revenue Divisional Officer
  corruption      : State Vigilance Commission, Anti-Corruption Bureau (ACB), DM
  others          : District Collector, SDM

URGENCY → RECOMMENDED APPROACH:
  critical : Immediate field visit + call the responsible head directly. Mark as top priority.
  high     : Direct call or WhatsApp to the responsible officer. Expect response within 24h.
  medium   : Formal letter / forwarded complaint to the department. Track response.
  low      : Batch with similar cases or include in the weekly departmental review.

REQUIRED JSON SCHEMA — respond ONLY with valid JSON, no prose, no markdown:
{
  "action_type": "<escalate_to_dept | draft_letter | call_official | field_visit | other>",
  "action_text": "<1-2 sentences: specific recommendation for staff, mentioning the dept and what to do>",
  "target_dept": "<full department name or official title to contact>",
  "draft_message": "<2-3 sentence formal message to send to the department — cite the grievance ID, describe the issue, mention location if given, request resolution within the SLA deadline>"
}

RULES:
1. Be specific. Not 'contact the department' — say 'Contact Executive Engineer, PWD [Location] Division'.
2. draft_message must be professional, suitable for a WhatsApp to a senior government official.
3. Include the grievance_id and location_text (if provided) in draft_message.
4. Match the urgency: CRITICAL cases need immediate escalation language.
5. The citizen_contact line in draft_message should say 'Reference: <grievance_id>' not expose the citizen number.
"""


async def suggest_action(grievance: dict) -> dict:
    """
    Generate an AI action recommendation for a grievance.

    Parameters
    ----------
    grievance : dict
        A grievance row from Supabase (must have id, grievance_id, category,
        urgency, summary, location_text, status, filed_at).

    Returns
    -------
    dict with keys: action_type, action_text, target_dept, draft_message
    """
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    now = datetime.now(tz=timezone.utc)
    filed_raw = grievance.get("filed_at", "")
    try:
        filed_dt = datetime.fromisoformat(filed_raw.replace("Z", "+00:00"))
        hours_open = (now - filed_dt).total_seconds() / 3600
    except Exception:
        hours_open = 0.0

    sla_h = SLA_HOURS.get(grievance.get("urgency", "medium"), 168)
    sla_remaining = max(0.0, sla_h - hours_open)
    sla_status = (
        "BREACHED" if hours_open > sla_h
        else "AT RISK" if hours_open > sla_h * 0.75
        else "ON TIME"
    )

    user_content = f"""
GRIEVANCE DETAILS:
  Reference ID  : {grievance.get('grievance_id', 'N/A')}
  Category      : {grievance.get('category', 'others')}
  Urgency       : {grievance.get('urgency', 'medium').upper()}
  Status        : {grievance.get('status', 'registered')}
  Location      : {grievance.get('location_text') or 'Not specified'}
  Hours open    : {hours_open:.1f}h
  SLA deadline  : {sla_h}h  ({sla_remaining:.0f}h remaining — {sla_status})
  Assigned to   : {grievance.get('assigned_to') or 'Not yet assigned'}
  Summary       : {grievance.get('summary', '')}

Produce the JSON action recommendation now.
"""

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Action advisor returned non-JSON: %s", raw)
        result = {
            "action_type": "other",
            "action_text": "Review this grievance and escalate to the responsible department.",
            "target_dept": "District Collector",
            "draft_message": (
                f"Please take note of grievance {grievance.get('grievance_id', '')} "
                f"regarding: {grievance.get('summary', '')}. Kindly advise on action."
            ),
        }

    # Persist to DB so dashboard can show cached version
    _save_suggestion(grievance.get("id"), result["action_text"])
    return result


def _save_suggestion(grievance_uuid: str | None, action_text: str) -> None:
    if not grievance_uuid:
        return
    try:
        db = get_db()
        db.table("grievances").update({
            "suggested_action": action_text,
            "suggested_action_at": datetime.now(tz=timezone.utc).isoformat(),
        }).eq("id", grievance_uuid).execute()
    except Exception:
        logger.exception("Failed to persist suggested_action for %s", grievance_uuid)
