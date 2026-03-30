"""
Email Intake Endpoint
---------------------
Receives inbound emails via Mailgun's Inbound Routing webhook.

Flow:
  Citizen emails grievances@<your-domain>
    → Mailgun receives it
    → POSTs multipart form to POST /email/inbound
    → Signature verified
    → Subject + body → classifier → grievance registered
    → Acknowledgment email sent back to citizen via Resend

One-time Mailgun setup (done by operator, not in code):
  1. Sign up at mailgun.com, verify a domain (or use sandbox for testing).
  2. Add an Inbound Route:
       Match: match_recipient("grievances@yourdomain.com")
       Action: forward("https://your-backend.up.railway.app/email/inbound")
  3. Copy Webhooks → Signing Key → set as MAILGUN_WEBHOOK_SIGNING_KEY env var.
  4. Set EMAIL_INTAKE_OFFICE_ID to the UUID of the receiving office in Supabase.
"""

import hashlib
import hmac
import logging
import re

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.core.config import settings
from app.models.grievance import GrievanceChannel
from app.services.grievance_service import process_walkin_grievance

router = APIRouter(prefix="/email", tags=["email"])
logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


# ── Signature verification ─────────────────────────────────────────────────────

def _verify_signature(timestamp: str, token: str, signature: str) -> bool:
    """
    Validate Mailgun webhook signature.
    HMAC-SHA256(timestamp + token, signing_key) must match signature.
    If MAILGUN_WEBHOOK_SIGNING_KEY is not configured, skip verification (dev only).
    """
    if not settings.MAILGUN_WEBHOOK_SIGNING_KEY:
        logger.warning("MAILGUN_WEBHOOK_SIGNING_KEY not set — skipping signature check")
        return True
    expected = hmac.new(
        key=settings.MAILGUN_WEBHOOK_SIGNING_KEY.encode(),
        msg=f"{timestamp}{token}".encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Text cleaning ──────────────────────────────────────────────────────────────

def _clean_body(text: str) -> str:
    """Collapse excessive whitespace; truncate to 4000 chars for classifier."""
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text[:4000]


def _extract_name(from_header: str) -> str | None:
    """
    Pull display name from a 'From' header like 'Ramesh Kumar <ramesh@gmail.com>'.
    Returns None if only an address is present.
    """
    if "<" in from_header:
        name = from_header.split("<")[0].strip().strip('"').strip("'")
        return name if name else None
    return None


# ── Acknowledgment email ───────────────────────────────────────────────────────

async def _send_ack(to: str, grievance_id: str, category: str, urgency: str, summary: str) -> None:
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping ack email to %s", to)
        return

    category_display = category.replace("_", " ").title()
    urgency_color = {
        "critical": "#c62828", "high": "#e65100",
        "medium":   "#1565C0", "low":  "#2e7d32",
    }.get(urgency, "#333")

    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:auto;
                padding:24px;border:1px solid #E3F2FD;border-radius:8px;">
      <p style="margin:0 0 8px;font-size:12px;color:#888;">
        Jan-Sunwai &nbsp;·&nbsp; Pollity.in
      </p>
      <h2 style="color:#1565C0;margin:0 0 16px;font-size:18px;">
        ✅ Your grievance has been registered
      </h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr>
          <td style="padding:6px 0;color:#555;width:130px;">Reference ID</td>
          <td style="padding:6px 0;font-weight:700;">{grievance_id}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#555;">Category</td>
          <td style="padding:6px 0;">{category_display}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#555;">Priority</td>
          <td style="padding:6px 0;font-weight:600;color:{urgency_color};">
            {urgency.upper()}
          </td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#555;">Summary</td>
          <td style="padding:6px 0;">{summary}</td>
        </tr>
      </table>
      <p style="margin-top:20px;font-size:13px;color:#444;line-height:1.6;">
        Your grievance has been received and will be reviewed by the constituency office.
        You will receive a status update at this email address when action is taken.<br><br>
        Please quote your Reference ID <strong>{grievance_id}</strong>
        in any future correspondence.
      </p>
      <p style="margin-top:16px;font-size:11px;color:#bbb;">
        Powered by Jan-Sunwai &mdash; Pollity.in
      </p>
    </div>
    """

    text = (
        f"Jan-Sunwai — Pollity.in\n\n"
        f"Your grievance has been registered.\n\n"
        f"Reference ID : {grievance_id}\n"
        f"Category     : {category_display}\n"
        f"Priority     : {urgency.upper()}\n"
        f"Summary      : {summary}\n\n"
        f"You will receive a status update at this email address.\n"
        f"Quote Reference ID {grievance_id} in future correspondence.\n\n"
        f"Jan-Sunwai — Pollity.in"
    )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={
                "from":    "Jan-Sunwai <grievances@pollity.in>",
                "to":      to,
                "subject": f"[Jan-Sunwai] Grievance registered — {grievance_id}",
                "html":    html,
                "text":    text,
            },
        )
        if resp.status_code in (200, 201):
            logger.info("Ack email sent to %s for %s", to, grievance_id)
        else:
            logger.error("Resend ack failed %s: %s", resp.status_code, resp.text)


# ── Background handler ─────────────────────────────────────────────────────────

async def _handle_email(
    sender: str,
    citizen_name: str | None,
    subject: str,
    body: str,
    office_id: str,
) -> None:
    raw_text = f"Subject: {subject}\n\n{body}" if subject else body

    grievance = await process_walkin_grievance(
        office_id=office_id,
        citizen_name=citizen_name,
        citizen_contact=sender,
        channel=GrievanceChannel.EMAIL,
        raw_text=raw_text,
    )

    if grievance is None:
        logger.error("Email intake: office %s not found", office_id)
        return

    logger.info("Email grievance registered: %s from %s", grievance.grievance_id, sender)

    await _send_ack(
        to=sender,
        grievance_id=grievance.grievance_id,
        category=grievance.category.value,
        urgency=grievance.urgency.value,
        summary=grievance.summary,
    )


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("/inbound")
async def email_inbound(request: Request, background_tasks: BackgroundTasks):
    """
    Mailgun Inbound Routing webhook.
    Returns 200 immediately so Mailgun does not retry; processing runs in background.
    """
    form = await request.form()

    # Mailgun signature fields
    timestamp = form.get("timestamp", "")
    token     = form.get("token", "")
    signature = form.get("signature", "")

    if not _verify_signature(str(timestamp), str(token), str(signature)):
        logger.warning("Invalid Mailgun webhook signature — rejected")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Extract email fields (Mailgun sends both capitalised and lowercase variants)
    sender      = str(form.get("sender") or form.get("Sender") or "")
    from_header = str(form.get("from")   or form.get("From")   or "")
    subject     = str(form.get("subject") or form.get("Subject") or "").strip()

    # Prefer stripped-text (removes quoted replies/signatures) over full body
    body = _clean_body(
        str(form.get("stripped-text") or form.get("body-plain") or "")
    )

    if not sender:
        logger.warning("Email inbound: missing sender — skipping")
        return {"status": "skipped", "reason": "no sender"}

    if not body and not subject:
        logger.warning("Email inbound: empty body from %s — skipping", sender)
        return {"status": "skipped", "reason": "empty content"}

    office_id = settings.EMAIL_INTAKE_OFFICE_ID
    if not office_id:
        logger.error("EMAIL_INTAKE_OFFICE_ID not configured")
        raise HTTPException(status_code=500, detail="Email intake office not configured.")

    citizen_name = _extract_name(from_header)

    background_tasks.add_task(
        _handle_email, sender, citizen_name, subject, body, office_id
    )

    return {"status": "ok"}
