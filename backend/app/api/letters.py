"""
Letters API — birthday wishes and citizen birthday management
-------------------------------------------------------------
POST /letters/birthday                — generate a birthday letter for a citizen
GET  /letters/birthdays/today         — list citizens with birthdays today
GET  /letters/birthdays/upcoming?days=7 — upcoming birthdays in the next N days
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.database import get_db
from app.services.letter_generator import generate_birthday_letter

router = APIRouter(prefix="/letters", tags=["letters"])
logger = logging.getLogger(__name__)


class BirthdayLetterRequest(BaseModel):
    office_id: str
    citizen_name: str
    dob: str                          # ISO date: YYYY-MM-DD
    salutation: str = "Shri"          # Shri / Smt / Dr / etc.
    designation: str = ""
    address_lines: list[str] = []


@router.post("/birthday")
async def birthday_letter(body: BirthdayLetterRequest):
    """
    Generate a birthday wishes letter for a citizen.
    Returns { html, pdf_b64, docx_b64, letter_type, do_number }.
    """
    db = get_db()
    o_resp = db.table("offices").select("*").eq("id", body.office_id).single().execute()
    if not o_resp.data:
        raise HTTPException(status_code=404, detail="Office not found")

    citizen = {
        "name":          body.citizen_name,
        "dob":           body.dob,
        "salutation":    body.salutation,
        "designation":   body.designation,
        "address_lines": body.address_lines,
    }

    try:
        result = await generate_birthday_letter(citizen, o_resp.data)
    except Exception:
        logger.exception("Birthday letter generation failed for %s", body.citizen_name)
        raise HTTPException(status_code=500, detail="Birthday letter generation failed.")

    return result


@router.get("/birthdays/today")
def birthdays_today(office_id: Annotated[str, Query()]):
    """
    Return all citizens whose birthday (MM-DD) matches today.
    Requires a 'citizens' table with columns: id, name, dob, salutation,
    designation, address, office_id.
    """
    today = date.today()
    mm_dd = today.strftime("%m-%d")

    db = get_db()
    try:
        resp = (
            db.table("citizens")
            .select("*")
            .eq("office_id", office_id)
            .execute()
        )
        all_citizens = resp.data or []
    except Exception:
        logger.exception("Failed to query citizens table")
        return []

    # Filter by month-day (database-agnostic)
    return [
        c for c in all_citizens
        if c.get("dob") and str(c["dob"])[5:10] == mm_dd
    ]


@router.get("/birthdays/upcoming")
def birthdays_upcoming(
    office_id: Annotated[str, Query()],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Return citizens with birthdays in the next N days (default 7).
    """
    today    = date.today()
    upcoming = {(today + timedelta(d)).strftime("%m-%d") for d in range(days + 1)}

    db = get_db()
    try:
        resp = (
            db.table("citizens")
            .select("*")
            .eq("office_id", office_id)
            .execute()
        )
        all_citizens = resp.data or []
    except Exception:
        logger.exception("Failed to query citizens table")
        return []

    results = [
        c for c in all_citizens
        if c.get("dob") and str(c["dob"])[5:10] in upcoming
    ]
    results.sort(key=lambda c: str(c.get("dob", ""))[5:10])
    return results
