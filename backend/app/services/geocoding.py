"""
Reverse Geocoding Service
--------------------------
Converts GPS coordinates (latitude, longitude) to a human-readable
area name using OpenStreetMap Nominatim — free, no API key required.

Used when a citizen shares a WhatsApp location pin instead of typing their address.
The result is stored as location_text on the grievance row.

Rate limit: Nominatim asks for max 1 req/sec and a descriptive User-Agent.
At grievance intake volumes this is never a concern.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT    = "JanSunwai-Pollity/1.0 (grievance-intake; contact@pollity.in)"

# Address fields to try in priority order (most specific → least specific)
_FIELD_PRIORITY = [
    "neighbourhood", "suburb", "quarter",
    "village", "town", "city_district",
    "county", "city", "state_district",
]


async def reverse_geocode(lat: float, lon: float) -> str | None:
    """
    Return a short, human-readable area name for (lat, lon).

    Tries to return the most specific named locality available —
    e.g. "Dharavi, Mumbai" rather than just "Maharashtra".

    Returns None if the request fails or no useful result is found.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                _NOMINATIM_URL,
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 14},
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        address = data.get("address", {})

        # Build a short label: most specific field + city/state context
        specific = next(
            (address[f] for f in _FIELD_PRIORITY if f in address), None
        )
        city = address.get("city") or address.get("town") or address.get("state_district")
        state = address.get("state", "")

        if specific and city and specific != city:
            label = f"{specific}, {city}"
        elif specific:
            label = f"{specific}, {state}" if state else specific
        elif city:
            label = f"{city}, {state}" if state else city
        else:
            # Fall back to the full display_name trimmed to 80 chars
            label = data.get("display_name", "")[:80] or None

        logger.info("Geocoded (%.4f, %.4f) → %s", lat, lon, label)
        return label or None

    except Exception:
        logger.exception("Reverse geocoding failed for (%.4f, %.4f)", lat, lon)
        return None
