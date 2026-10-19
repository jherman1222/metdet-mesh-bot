"""Single source for bot release version (admin UI + user update DMs)."""

from __future__ import annotations

# Bump when you deploy playa fixes users should know about.
VERSION = "2026.9.3"

# One short line — shown in admin + optional user update DM.
RELEASE_HEADLINE = "wx cached; incomplete replies fixed"


def user_update_message() -> str:
    """One radio-friendly DM for playa hotfix + version (single packet)."""
    return (
        f"v{VERSION}: wx was a weather-API daily cap, not Starlink. "
        f"Forecast cached for camp; leftover … replies fixed. "
        f"Try wx then .r — menu lists what I can do."
    )


def playa_hotfix_dm_setting_key() -> str:
    return f"playa_hotfix_dm:{VERSION}"
