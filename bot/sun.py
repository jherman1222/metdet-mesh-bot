from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.weather import BRC, RENO, Place, parse_place

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
SUN_LEAD_MINUTES = 15
# Sunrise/sunset from local astronomy — no Open-Meteo (avoids burning daily quota).
_SUN_CACHE: dict[tuple[float, float, str], tuple[datetime, datetime]] = {}


def _sun_times(lat: float, lon: float, day: datetime) -> tuple[datetime, datetime]:
    """NOAA-style sunrise/sunset for a civil day in America/Los_Angeles."""
    key = (round(lat, 4), round(lon, 4), day.strftime("%Y-%m-%d"))
    cached = _SUN_CACHE.get(key)
    if cached:
        return cached
    local_noon = day.replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=PT)
    rise = _solar_event(lat, lon, local_noon, rising=True)
    sett = _solar_event(lat, lon, local_noon, rising=False)
    _SUN_CACHE[key] = (rise, sett)
    return rise, sett


def _solar_event(lat: float, lon: float, local_noon: datetime, *, rising: bool) -> datetime:
    """
    Approximate sunrise/sunset (upper limb + refraction) using NOAA solar calc.
    Accurate enough for 15-minute lead reminders.

    event_min is always applied to the *same* UTC midnight of the local civil date
    so iteration cannot drift across days (the old bug that put sunset on day+2).
    """
    local_day = local_noon.date()
    base = datetime(local_day.year, local_day.month, local_day.day, tzinfo=UTC)
    utc_guess = base + timedelta(hours=12)
    for _ in range(3):
        jd = _julian_day(utc_guess)
        jc = (jd - 2451545.0) / 36525.0
        geom_mean_long = (280.46646 + jc * (36000.76983 + 0.0003032 * jc)) % 360.0
        geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
        eccent = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
        eq_of_ctr = (
            math.sin(math.radians(geom_mean_anom))
            * (1.914602 - jc * (0.004817 + 0.000014 * jc))
            + math.sin(math.radians(2 * geom_mean_anom)) * (0.019993 - 0.000101 * jc)
            + math.sin(math.radians(3 * geom_mean_anom)) * 0.000289
        )
        sun_true_long = geom_mean_long + eq_of_ctr
        sun_app_long = sun_true_long - 0.00569 - 0.00478 * math.sin(
            math.radians(125.04 - 1934.136 * jc)
        )
        mean_obliq = (
            23.0
            + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0)
            / 60.0
        )
        obliq = mean_obliq + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * jc))
        decl = math.degrees(
            math.asin(
                math.sin(math.radians(obliq)) * math.sin(math.radians(sun_app_long))
            )
        )
        y = math.tan(math.radians(obliq / 2)) ** 2
        eq_time = 4 * math.degrees(
            y * math.sin(2 * math.radians(geom_mean_long))
            - 2 * eccent * math.sin(math.radians(geom_mean_anom))
            + 4
            * eccent
            * y
            * math.sin(math.radians(geom_mean_anom))
            * math.cos(2 * math.radians(geom_mean_long))
            - 0.5 * y * y * math.sin(4 * math.radians(geom_mean_long))
            - 1.25 * eccent * eccent * math.sin(2 * math.radians(geom_mean_anom))
        )
        cos_ha = (
            math.cos(math.radians(90.833))
            / (math.cos(math.radians(lat)) * math.cos(math.radians(decl)))
            - math.tan(math.radians(lat)) * math.tan(math.radians(decl))
        )
        cos_ha = max(-1.0, min(1.0, cos_ha))
        ha = math.degrees(math.acos(cos_ha))
        # Minutes from UTC midnight of the local civil calendar date (NOAA).
        solar_noon_min = 720 - 4 * lon - eq_time
        event_min = solar_noon_min - 4 * ha if rising else solar_noon_min + 4 * ha
        utc_guess = base + timedelta(minutes=event_min)
    return utc_guess.astimezone(PT)


def _julian_day(dt: datetime) -> float:
    utc = dt.astimezone(UTC)
    y, m, d = utc.year, utc.month, utc.day
    hr = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5
    return jd + hr / 24.0


def today_sun_events(place: Place = "both") -> list[tuple[str, str, datetime]]:
    """
    Returns list of (place_label, event_name, when) for today.
    event_name is 'sunrise' or 'sunset'.
    """
    now = datetime.now(PT)
    day = now.replace(hour=12, minute=0, second=0, microsecond=0)
    out: list[tuple[str, str, datetime]] = []
    points: list[tuple[str, tuple[float, float]]] = []
    if place in ("reno", "both"):
        points.append(("Reno", RENO))
    if place in ("brc", "both"):
        points.append(("BRC", BRC))
    for label, (lat, lon) in points:
        rise, sett = _sun_times(lat, lon, day)
        out.append((label, "sunrise", rise))
        out.append((label, "sunset", sett))
    return out


def _clock(when: datetime) -> str:
    return when.strftime("%I:%M%p").lstrip("0").lower()


def due_sun_messages(
    *,
    place: Place,
    already_sent: set[str],
    lead_minutes: int = SUN_LEAD_MINUTES,
    window_seconds: int = 300,
) -> list[tuple[str, str]]:
    """
    Fire ~lead_minutes before sunrise/sunset.
    If place is both, Reno + BRC go in one DM per event.
    already_sent keys: 2026-08-01|Reno|sunrise  or  2026-08-01|both|sunset
    Returns list of (dedupe_key, message).
    """
    now = datetime.now(PT)
    lead = lead_minutes * 60
    grouped: dict[str, list[tuple[str, datetime]]] = {}
    for label, event, when in today_sun_events(place):
        # Ignore bad calc spill into other days
        if when.date() != now.date():
            continue
        grouped.setdefault(event, []).append((label, when))

    msgs: list[tuple[str, str]] = []
    for event, items in grouped.items():
        items.sort(key=lambda x: x[1])
        first = items[0][1]
        until = (first - now).total_seconds()
        if not ((lead - window_seconds) <= until <= lead):
            continue
        if len(items) > 1:
            key = f"{first.strftime('%Y-%m-%d')}|both|{event}"
        else:
            key = f"{first.strftime('%Y-%m-%d')}|{items[0][0]}|{event}"
        if key in already_sent:
            continue
        mins = max(1, int(round(until / 60)))
        if len(items) == 1:
            label, when = items[0]
            msg = f"SUN {label} {event} in ~{mins}m (at {_clock(when)})"
        else:
            bits = [f"{label} {_clock(when)}" for label, when in items]
            msg = f"SUN {event} in ~{mins}m — " + " · ".join(bits)
        msgs.append((key, msg))
    return msgs


def normalize_sun_place(text: str) -> Place:
    return parse_place(text) or "both"
