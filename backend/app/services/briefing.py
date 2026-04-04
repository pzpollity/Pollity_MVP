"""
Constituency Intelligence Briefing Service
-------------------------------------------
Generates a weekly AI briefing for the representative using Claude Sonnet.

Pipeline:
  1. Pull this week's and last week's grievances from Supabase
  2. Compute stats: volume, category breakdown, SLA breaches, location patterns
  3. Send structured stats to Claude Sonnet for narrative + recommendations
  4. Return formatted WhatsApp message + HTML email

Triggered every Monday at 8 AM IST via POST /api/briefing/trigger
"""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Mirror the SLA targets from the dashboard
SLA_HOURS = {"critical": 24, "high": 72, "medium": 168, "low": 504}

CATEGORY_LABELS = {
    "infrastructure":  "Infrastructure",
    "welfare_schemes": "Welfare Schemes",
    "public_safety":   "Public Safety",
    "healthcare":      "Healthcare",
    "education":       "Education",
    "land_revenue":    "Land & Revenue",
    "corruption":      "Corruption",
    "others":          "Others",
}


def _is_breached(row: dict, now: datetime) -> bool:
    try:
        filed = datetime.fromisoformat(row["filed_at"].replace("Z", "+00:00"))
        hours_open = (now - filed).total_seconds() / 3600
        return hours_open > SLA_HOURS.get(row.get("urgency", "low"), 504)
    except Exception:
        return False


def _compute_stats(rows: list[dict], now: datetime) -> dict:
    total        = len(rows)
    by_category  = Counter(r.get("category", "others") for r in rows)
    by_urgency   = Counter(r.get("urgency", "low") for r in rows)
    by_status    = Counter(r.get("status", "registered") for r in rows)
    open_rows    = [r for r in rows if r.get("status") not in ("resolved", "verified", "closed")]
    resolved     = total - len(open_rows)
    sla_breaches = [r for r in open_rows if _is_breached(r, now)]
    breach_cats  = Counter(r.get("category", "others") for r in sla_breaches)
    critical_open = [r for r in open_rows if r.get("urgency") == "critical"]

    # Top locations (non-null location_text)
    locations = [r.get("location_text") for r in rows if r.get("location_text")]
    top_locations = Counter(locations).most_common(5)

    return {
        "total": total,
        "open": len(open_rows),
        "resolved": resolved,
        "by_category": dict(by_category.most_common(5)),
        "by_urgency": dict(by_urgency),
        "sla_breaches": len(sla_breaches),
        "breach_categories": dict(breach_cats),
        "critical_open": len(critical_open),
        "top_locations": top_locations,
    }


async def generate_weekly_briefing(office_id: str) -> dict:
    """
    Pull data, compute stats, call Claude, return briefing dict with
    'whatsapp_message' and 'email_html' keys.
    """
    db  = get_db()
    now = datetime.now(tz=timezone.utc)
    week_start      = now - timedelta(days=7)
    prev_week_start = now - timedelta(days=14)

    # ── Fetch this week's grievances ─────────────────────────────────────────
    resp_this = (
        db.table("grievances")
        .select("category,urgency,status,filed_at,location_text,summary")
        .eq("office_id", office_id)
        .gte("filed_at", week_start.isoformat())
        .execute()
    )
    this_week = resp_this.data or []

    # ── Fetch previous week's grievances (for trend comparison) ──────────────
    resp_prev = (
        db.table("grievances")
        .select("category,urgency,status,filed_at")
        .eq("office_id", office_id)
        .gte("filed_at", prev_week_start.isoformat())
        .lt("filed_at", week_start.isoformat())
        .execute()
    )
    prev_week = resp_prev.data or []

    this_stats = _compute_stats(this_week, now)
    prev_stats = _compute_stats(prev_week, now)

    # ── Volume change ────────────────────────────────────────────────────────
    prev_total = prev_stats["total"] or 1  # avoid div/0
    volume_pct = round((this_stats["total"] - prev_stats["total"]) / prev_total * 100)

    # ── Category spikes (this week vs last week) ─────────────────────────────
    spikes = []
    for cat, count in this_stats["by_category"].items():
        prev_count = prev_stats["by_category"].get(cat, 0) or 1
        pct = round((count - prev_count) / prev_count * 100)
        if pct >= 50 and count >= 3:
            spikes.append({"category": CATEGORY_LABELS.get(cat, cat), "count": count, "pct": pct})
    spikes.sort(key=lambda x: x["pct"], reverse=True)

    # ── Recent summaries for Claude context ──────────────────────────────────
    sample_summaries = [r["summary"] for r in this_week if r.get("summary")][:10]

    # ── Build Claude prompt ──────────────────────────────────────────────────
    now_ist = now.astimezone(IST)
    week_label = now_ist.strftime("Week of %d %b %Y")

    stats_block = f"""
WEEK: {week_label}

GRIEVANCES THIS WEEK: {this_stats['total']} ({"+" if volume_pct >= 0 else ""}{volume_pct}% vs last week)
LAST WEEK: {prev_stats['total']}

BY CATEGORY (this week):
{chr(10).join(f"  - {CATEGORY_LABELS.get(k,k)}: {v}" for k,v in this_stats['by_category'].items())}

BY URGENCY: Critical={this_stats['by_urgency'].get('critical',0)}, High={this_stats['by_urgency'].get('high',0)}, Medium={this_stats['by_urgency'].get('medium',0)}, Low={this_stats['by_urgency'].get('low',0)}

OPEN: {this_stats['open']} | RESOLVED THIS WEEK: {this_stats['resolved']}
SLA BREACHES: {this_stats['sla_breaches']} (categories: {this_stats['breach_categories']})
CRITICAL UNRESOLVED: {this_stats['critical_open']}

CATEGORY SPIKES VS LAST WEEK:
{chr(10).join(f"  - {s['category']}: +{s['pct']}% ({s['count']} cases)" for s in spikes) or "  None significant"}

TOP LOCATIONS MENTIONED:
{chr(10).join(f"  - {loc}: {cnt} complaints" for loc, cnt in this_stats['top_locations']) or "  No location data"}

SAMPLE GRIEVANCE SUMMARIES (for context):
{chr(10).join(f"  • {s}" for s in sample_summaries) or "  None"}
"""

    system_prompt = """You are the Digital Chief of Staff for an elected representative in India.
You receive weekly grievance data from the Jan-Sunwai platform and write a concise, actionable intelligence brief.

Your brief must:
1. Open with a one-line headline capturing the most important insight
2. Summarize key trends (what changed, what spiked, what improved)
3. Flag the 2-3 most urgent attention items
4. End with 2-3 concrete, specific recommendations the representative can act on this week

Tone: professional, direct, like a trusted advisor. Not bureaucratic. No fluff.
Keep the total brief under 350 words.
Write in English.
Do NOT mention AI or that this was generated automatically."""

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": stats_block}],
    )
    narrative = response.content[0].text.strip()

    # ── Format WhatsApp message ───────────────────────────────────────────────
    wa_message = (
        f"🏛️ *Jan-Sunwai Weekly Brief — {now_ist.strftime('%d %b %Y')}*\n\n"
        f"{narrative}\n\n"
        f"_Jan-Sunwai · Pollity.in_"
    )

    # ── Format email HTML ─────────────────────────────────────────────────────
    narrative_html = narrative.replace("\n", "<br>")
    email_html = f"""
<div style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;border:1px solid #E3F2FD;border-radius:8px;">
  <p style="margin:0 0 4px;font-size:12px;color:#888;">Jan-Sunwai · Pollity.in</p>
  <h2 style="color:#1565C0;margin:0 0 20px;font-size:20px;">
    🏛️ Weekly Constituency Brief — {now_ist.strftime('%d %b %Y')}
  </h2>
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:20px;background:#F8FAFF;border-radius:6px;">
    <tr>
      <td style="padding:8px 12px;color:#555;">Grievances this week</td>
      <td style="padding:8px 12px;font-weight:700;">{this_stats['total']} ({"+" if volume_pct>=0 else ""}{volume_pct}% vs last week)</td>
    </tr>
    <tr>
      <td style="padding:8px 12px;color:#555;">Open / Resolved</td>
      <td style="padding:8px 12px;font-weight:700;">{this_stats['open']} open · {this_stats['resolved']} resolved</td>
    </tr>
    <tr>
      <td style="padding:8px 12px;color:#555;">SLA Breaches</td>
      <td style="padding:8px 12px;font-weight:700;color:{'#c62828' if this_stats['sla_breaches'] > 0 else '#2e7d32'};">{this_stats['sla_breaches']}</td>
    </tr>
    <tr>
      <td style="padding:8px 12px;color:#555;">Critical Unresolved</td>
      <td style="padding:8px 12px;font-weight:700;color:{'#c62828' if this_stats['critical_open'] > 0 else '#2e7d32'};">{this_stats['critical_open']}</td>
    </tr>
  </table>
  <div style="font-size:14px;line-height:1.8;color:#222;">{narrative_html}</div>
  <p style="margin-top:24px;font-size:11px;color:#bbb;">Jan-Sunwai — Pollity.in · Auto-generated weekly briefing</p>
</div>
"""

    logger.info("Weekly briefing generated for office %s: %d grievances", office_id, this_stats["total"])

    return {
        "office_id":       office_id,
        "week_label":      week_label,
        "stats":           this_stats,
        "narrative":       narrative,
        "whatsapp_message": wa_message,
        "email_html":      email_html,
        "email_subject":   f"Jan-Sunwai Weekly Brief — {now_ist.strftime('%d %b %Y')} ({this_stats['total']} grievances)",
    }
