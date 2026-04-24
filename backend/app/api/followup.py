"""
Follow-up API
--------------
POST /api/followup/run      — Run the proactive citizen follow-up job.
                              Protected by X-Followup-Secret header.
                              Call every 6 hours from a cron service.

POST /api/followup/preview  — Dry-run: show what would be sent without sending.

Both endpoints accept an optional JSON body:
  { "office_id": "<uuid>" }   — scope to one office (default: all offices)
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import get_db
from app.services.followup import run_followup_job

router = APIRouter(prefix="/api/followup", tags=["followup"])
logger = logging.getLogger(__name__)


class FollowupRequest(BaseModel):
    office_id: str | None = None   # if None, runs for all offices


def _check_secret(provided: str) -> None:
    if settings.FOLLOWUP_SECRET and provided != settings.FOLLOWUP_SECRET:
        raise HTTPException(status_code=401, detail="Invalid followup secret")


def _get_all_office_ids() -> list[str]:
    db = get_db()
    resp = db.table("offices").select("id").execute()
    return [r["id"] for r in (resp.data or [])]


async def _run(office_id: str | None, dry_run: bool) -> dict:
    office_ids = [office_id] if office_id else _get_all_office_ids()
    all_results = []
    total_sent = 0

    for oid in office_ids:
        result = await run_followup_job(oid, dry_run=dry_run)
        all_results.append(result)
        total_sent += result.get("sent", 0)

    return {
        "dry_run": dry_run,
        "offices_processed": len(office_ids),
        "total_sent": total_sent,
        "results": all_results,
    }


@router.post("/run")
async def trigger_followup(
    body: FollowupRequest = FollowupRequest(),
    x_followup_secret: str = Header(default=""),
):
    """
    Run the follow-up job for all offices (or a specific one).
    Set FOLLOWUP_SECRET in Railway env vars and pass as X-Followup-Secret header.
    Schedule via cron-job.org every 6 hours.
    """
    _check_secret(x_followup_secret)
    return await _run(body.office_id, dry_run=False)


@router.post("/preview")
async def preview_followup(
    body: FollowupRequest = FollowupRequest(),
    x_followup_secret: str = Header(default=""),
):
    """
    Dry-run: shows which follow-ups would be sent and their message content.
    Nothing is actually sent or logged.
    """
    _check_secret(x_followup_secret)
    return await _run(body.office_id, dry_run=True)
