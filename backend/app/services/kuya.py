"""KUYA guest sync. Pulls who is in each suite from the booking concierge's
backend and turns the booking's free-text special requests into the standing
tags ATE already understands (allergy:x drives the kitchen autotag).

Config, all in backend/.env:
  KUYA_BASE_URL      e.g. https://kuya-backend.up.railway.app (empty disables sync)
  KUYA_SERVICE_KEY   matches SERVICE_API_KEY on the KUYA backend
"""

import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

KUYA_BASE_URL = os.environ.get("KUYA_BASE_URL", "").rstrip("/")
KUYA_SERVICE_KEY = os.environ.get("KUYA_SERVICE_KEY", "")

# KUYA speaks suite names; ATE speaks room ids. One mapping, extend per hotel.
SUITE_TO_ROOM = {
    "Family Suite": "4",
    "Honeymoon Suite": "honeymoon",
}

# Keyword families for tag derivation from free-text special requests.
# Deliberately coarse: a false positive allergy tag costs the cook one glance;
# a false negative costs much more. The raw note still reaches the brain
# verbatim via RoomState.notes, so nothing is lost to the keyword filter.
_ALLERGEN_PATTERNS: list[tuple[str, str]] = [
    ("shellfish", r"shellfish|shrimp|prawn|crab|lobster|hipon|alimango"),
    ("peanut", r"peanut|mani\b"),
    ("nut", r"\bnuts?\b|almond|cashew|walnut"),
    ("dairy", r"dairy|lactose|\bmilk\b|gatas"),
    ("gluten", r"gluten|celiac|coeliac"),
    ("egg", r"\beggs?\b|itlog"),
    ("soy", r"\bsoy\b|\bsoya\b"),
    ("fish", r"\bfish\b|isda"),
]

_OCCASION_PATTERNS: list[tuple[str, str]] = [
    ("anniversary", r"anniversar"),
    ("honeymoon", r"honeymoon|just married|newlywed"),
    ("birthday", r"birthday|bday|kaarawan"),
    ("proposal", r"propos"),
]


def sync_enabled() -> bool:
    return bool(KUYA_BASE_URL)


def derive_tags(special_requests: Optional[str]) -> list[str]:
    """Standing tags from the booking's special-requests text. Allergies only
    tag when an allergy word appears near the food word, so "we love shrimp"
    does not brand the room shellfish-allergic."""
    text = (special_requests or "").lower()
    if not text.strip():
        return []
    tags: list[str] = []
    mentions_allergy = bool(re.search(r"allerg|intoleran|cannot eat|can't eat|no\s", text))
    for name, pattern in _ALLERGEN_PATTERNS:
        if re.search(pattern, text) and mentions_allergy:
            tags.append(f"allergy:{name}")
    for name, pattern in _OCCASION_PATTERNS:
        if re.search(pattern, text):
            tags.append(f"occasion:{name}")
    return tags


async def fetch_current_guests() -> Optional[list[dict[str, Any]]]:
    """One pull of KUYA's current-guests endpoint. Returns the suites list, or
    None on any failure (network, auth, bad payload). Failures never raise:
    a sync miss must not take down a guest turn or the server."""
    if not sync_enabled():
        return None
    headers = {"X-Service-Key": KUYA_SERVICE_KEY} if KUYA_SERVICE_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{KUYA_BASE_URL}/api/service/current-guests", headers=headers
            )
        if resp.status_code != 200:
            logger.warning(
                "KUYA guest sync failed: status=%s body=%s",
                resp.status_code, resp.text[:200],
            )
            return None
        data = resp.json()
        suites = data.get("suites")
        if not isinstance(suites, list):
            logger.warning("KUYA guest sync: unexpected payload shape")
            return None
        return suites
    except Exception as exc:
        logger.warning("KUYA guest sync failed: %s", exc)
        return None
