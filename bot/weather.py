from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

Place = Literal["reno", "brc", "both"]

RENO = (39.5296, -119.8138)  # Reno metro
BRC = (40.7864, -119.2065)  # Black Rock City / Gerlach area
PT = ZoneInfo("America/Los_Angeles")

# Rough PV: % of STC / bright-noon (~1000 W/m² GHI). Not panel-specific.
SOLAR_STC_WM2 = 1000.0

# Proactive alert thresholds (desert / BM useful)
GUST_ALERT_MPH = 18  # playa: alert when next-hour gusts hit this
GUST_JUMP_MPH = 12  # sudden rise vs current
RAIN_POP_PCT = 50
RAIN_MM = 0.2
ALERT_LOOKAHEAD_HOURS = 2  # "next hour" focus (+ a little slack)

WMO = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "showers",
    81: "showers",
    82: "heavy showers",
    95: "thunder",
    96: "thunder hail",
    99: "thunder hail",
}

STORMY_CODES = {61, 63, 65, 80, 81, 82, 95, 96, 99}

# One shared 7-day blob per lat/lon — all users + wx/wind/week/alerts reuse it.
_FETCH_CACHE: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}
_FETCH_CACHE_TTL_SEC = 600  # 10 min; background refresh keeps this warm
_STALE_OK_SEC = 6 * 3600
_FORECAST_DAYS = 7
# Shared Open-Meteo cooldown (Starlink CGNAT IPs get 429'd if we hammer).
_OM_COOLDOWN_UNTIL = 0.0
_OM_DAILY_LIMIT = False
_OM_UA = "metdet-mesh-bot/2026 (camp wx; playa ops)"
_NWS_UA = "(metdet-mesh-bot, metdet-playa-ops)"
# Main free host shares CGNAT quota; previous-runs often still works when primary is 429'd.
_OM_FORECAST_HOSTS = (
    "https://api.open-meteo.com/v1/forecast",
    "https://previous-runs-api.open-meteo.com/v1/forecast",
)


def _wx_error_hint(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return "weather API daily limit (until tomorrow)" if _OM_DAILY_LIMIT else "weather API rate-limited"
        if exc.code >= 500:
            return "forecast server busy"
        return f"forecast HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "internet hiccup"
    if isinstance(exc, TimeoutError):
        return "timed out"
    msg = str(exc).lower()
    if "daily" in msg and "limit" in msg:
        return "weather API daily limit (until tomorrow)"
    if "rate-limit" in msg or "cooldown" in msg:
        return "weather API rate-limited"
    return "try again"


def om_on_cooldown() -> bool:
    return time.time() < _OM_COOLDOWN_UNTIL


def _seconds_until_tomorrow_pt() -> float:
    now = datetime.now(PT)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return max(300.0, (tomorrow - now).total_seconds())


def open_meteo_get(url: str) -> dict[str, Any]:
    """GET Open-Meteo JSON. Tries alternate forecast host if primary is 429'd."""
    global _OM_COOLDOWN_UNTIL, _OM_DAILY_LIMIT

    # Rewrite known forecast URLs across hosts (primary → previous-runs fallback).
    urls = [url]
    if "open-meteo.com/v1/forecast?" in url:
        qs = url.split("?", 1)[1]
        urls = [f"{host}?{qs}" for host in _OM_FORECAST_HOSTS]
        if _OM_DAILY_LIMIT:
            urls = [u for u in urls if "previous-runs-api" in u]

    if om_on_cooldown() and not any("previous-runs-api" in u for u in urls):
        raise RuntimeError(
            "open-meteo daily limit" if _OM_DAILY_LIMIT else "open-meteo cooldown"
        )

    last_exc: BaseException | None = None
    for try_url in urls:
        if om_on_cooldown() and "api.open-meteo.com" in try_url and "previous-runs" not in try_url:
            continue
        req = urllib.request.Request(try_url, headers={"User-Agent": _OM_UA})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if "previous-runs-api" in try_url:
                    data["_om_host"] = "previous-runs"
                else:
                    data["_om_host"] = "primary"
                return data
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 429:
                    body = ""
                    try:
                        body = exc.read().decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        body = ""
                    if "api.open-meteo.com" in try_url and "daily" in body.lower():
                        _OM_DAILY_LIMIT = True
                        _OM_COOLDOWN_UNTIL = time.time() + _seconds_until_tomorrow_pt()
                        break  # try next host
                    _OM_COOLDOWN_UNTIL = time.time() + 180
                    break
                if exc.code >= 500 and attempt < 1:
                    time.sleep(0.75)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < 1:
                    time.sleep(0.75)
                    continue
                break
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("open-meteo unavailable")


def _http_json(url: str, *, headers: dict[str, str], timeout: float = 12) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_mph(text: Any) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str):
        return None
    # "10 mph", "10 to 15 mph", "NW 12 mph"
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", text)]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _nws_cond_code(short_forecast: str) -> int:
    t = (short_forecast or "").lower()
    if "thunder" in t:
        return 95
    if "snow" in t:
        return 71
    if "rain" in t or "shower" in t:
        return 61
    if "fog" in t:
        return 45
    if "cloud" in t or "overcast" in t:
        return 3
    if "partly" in t:
        return 2
    if "mostly sunny" in t or "mostly clear" in t:
        return 1
    if "sun" in t or "clear" in t:
        return 0
    return 2


def _nws_interval_hours(valid: str) -> list[datetime]:
    """Expand NWS '2026-09-04T12:00:00+00:00/PT6H' into local hour starts."""
    start_s, _, dur = (valid or "").partition("/")
    if not start_s:
        return []
    try:
        start = datetime.fromisoformat(start_s.replace("Z", "+00:00")).astimezone(PT)
    except Exception:  # noqa: BLE001
        return []
    start = start.replace(minute=0, second=0, microsecond=0)
    hours = 1
    if dur.startswith("PT") and dur.endswith("H"):
        try:
            hours = max(1, int(dur[2:-1] or "1"))
        except ValueError:
            hours = 1
    elif dur.startswith("P") and dur.endswith("D") and "T" not in dur:
        try:
            hours = max(1, int(dur[1:-1] or "1") * 24)
        except ValueError:
            hours = 1
    return [start + timedelta(hours=i) for i in range(hours)]


def _compass(deg: float | None) -> str:
    if not isinstance(deg, (int, float)):
        return ""
    dirs = (
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    )
    return dirs[int((float(deg) % 360) / 22.5 + 0.5) % 16]


def _dir_token(text: Any = None, deg: float | None = None) -> str:
    if isinstance(text, str) and text.strip():
        return text.strip().upper()
    return _compass(deg)


def _nws_grid_values_by_hour(
    values: list[Any],
    *,
    divide: bool = False,
    scale: float = 1.0,
) -> dict[str, float]:
    """Map NWS grid intervals onto local hours. divide=True for 6h QPF totals."""
    out: dict[str, float] = {}
    for row in values or []:
        if not isinstance(row, dict):
            continue
        val = row.get("value")
        if not isinstance(val, (int, float)):
            continue
        hours = _nws_interval_hours(str(row.get("validTime") or ""))
        if not hours:
            continue
        v = float(val) * scale
        if divide:
            v = v / len(hours)
        for dt in hours:
            out[dt.strftime("%Y-%m-%dT%H")] = v
    return out


def _nws_grid_periods(
    values: list[Any],
    *,
    scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Keep NWS interval resolution (3–6h) — do not fake hourly copies."""
    out: list[dict[str, Any]] = []
    for row in values or []:
        if not isinstance(row, dict):
            continue
        val = row.get("value")
        if not isinstance(val, (int, float)):
            continue
        hours = _nws_interval_hours(str(row.get("validTime") or ""))
        if not hours:
            continue
        out.append(
            {
                "start": hours[0],
                "end": hours[-1],
                "value": float(val) * scale,
            }
        )
    return out


def _nws_load_grid(grid_url: str | None, headers: dict[str, str]) -> dict[str, Any]:
    """QPF + wind/gust/dir from forecastGridData. One HTTP."""
    empty: dict[str, Any] = {
        "qpf_mm": {},
        "gust_mph": {},
        "wind_mph": {},
        "dir_deg": {},
        "gust_periods": [],
        "wind_periods": [],
        "dir_periods": [],
    }
    if not grid_url:
        return empty
    try:
        grid = _http_json(grid_url, headers=headers, timeout=15)
    except Exception:  # noqa: BLE001
        return empty
    props = grid.get("properties") or {}
    kmh = 0.621371
    return {
        "qpf_mm": _nws_grid_values_by_hour(
            (props.get("quantitativePrecipitation") or {}).get("values") or [],
            divide=True,
        ),
        "gust_mph": _nws_grid_values_by_hour(
            (props.get("windGust") or {}).get("values") or [],
            scale=kmh,
        ),
        "wind_mph": _nws_grid_values_by_hour(
            (props.get("windSpeed") or {}).get("values") or [],
            scale=kmh,
        ),
        "dir_deg": _nws_grid_values_by_hour(
            (props.get("windDirection") or {}).get("values") or [],
        ),
        "gust_periods": _nws_grid_periods(
            (props.get("windGust") or {}).get("values") or [],
            scale=kmh,
        ),
        "wind_periods": _nws_grid_periods(
            (props.get("windSpeed") or {}).get("values") or [],
            scale=kmh,
        ),
        "dir_periods": _nws_grid_periods(
            (props.get("windDirection") or {}).get("values") or [],
        ),
    }


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _mean_dir_deg(degs: list[float]) -> float | None:
    if not degs:
        return None
    x = sum(math.cos(math.radians(d)) for d in degs) / len(degs)
    y = sum(math.sin(math.radians(d)) for d in degs) / len(degs)
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _nws_raws_consensus(
    stations_url: str | None, headers: dict[str, str]
) -> dict[str, Any] | None:
    """
    Live wind from nearest ridge RAWS stations (not on the playa).
    Median of up to 4 stations — better than trusting one hilltop.
    """
    if not stations_url:
        return None
    try:
        listing = _http_json(stations_url, headers=headers, timeout=12)
        feats = listing.get("features") or []
        if not feats:
            return None
        rows: list[dict[str, Any]] = []
        for feat in feats[:5]:
            props = feat.get("properties") or {}
            sid = props.get("stationIdentifier")
            name = (props.get("name") or sid or "obs").strip()
            if not sid:
                continue
            try:
                obs = _http_json(
                    f"https://api.weather.gov/stations/{sid}/observations/latest",
                    headers=headers,
                    timeout=10,
                )
            except Exception:  # noqa: BLE001
                continue
            op = obs.get("properties") or {}
            ws = (op.get("windSpeed") or {}).get("value")
            if not isinstance(ws, (int, float)):
                continue
            wg = (op.get("windGust") or {}).get("value")
            wd = (op.get("windDirection") or {}).get("value")
            short = name.split()[0].title() if name else sid
            rows.append(
                {
                    "id": sid,
                    "name": short,
                    "wind_mph": float(ws) * 0.621371,
                    "gust_mph": float(wg) * 0.621371 if isinstance(wg, (int, float)) else None,
                    "dir_deg": float(wd) if isinstance(wd, (int, float)) else None,
                    "ts": op.get("timestamp"),
                }
            )
            if len(rows) >= 4:
                break
        if not rows:
            return None
        winds = [r["wind_mph"] for r in rows]
        gusts = [r["gust_mph"] for r in rows if isinstance(r.get("gust_mph"), (int, float))]
        dirs = [r["dir_deg"] for r in rows if isinstance(r.get("dir_deg"), (int, float))]
        med_w = _median(winds)
        med_g = _median(gusts) if gusts else None
        if isinstance(med_g, float) and med_g < med_w:
            med_g = med_w
        dir_deg = _mean_dir_deg(dirs)
        return {
            "id": "RAWS",
            "name": "ridge",
            "wind_mph": med_w,
            "gust_mph": med_g,
            "dir_deg": dir_deg,
            "ts": rows[0].get("ts"),
            "n_stations": len(rows),
            "stations": rows,
            "note": "ridge RAWS ~15-35mi off playa; open playa often gustier",
        }
    except Exception:  # noqa: BLE001
        return None


# Back-compat alias
def _nws_nearest_obs(
    stations_url: str | None, headers: dict[str, str]
) -> dict[str, Any] | None:
    return _nws_raws_consensus(stations_url, headers)


def _hour_key_pt(start: str) -> str:
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(PT)
        return dt.strftime("%Y-%m-%dT%H")
    except Exception:  # noqa: BLE001
        return ""


def _merge_wind_periods(
    wind_periods: list[dict[str, Any]],
    gust_periods: list[dict[str, Any]],
    dir_periods: list[dict[str, Any]],
    *,
    hours: int = 18,
) -> list[dict[str, Any]]:
    """Keep NWS bin starts/ends intact; attach overlapping gust + direction."""
    now = datetime.now(PT).replace(minute=0, second=0, microsecond=0)
    until = now + timedelta(hours=hours)

    def lookup(periods: list[dict[str, Any]], when: datetime) -> float | None:
        for p in periods:
            if p["start"] <= when <= p["end"]:
                return p["value"]
        return None

    out: list[dict[str, Any]] = []
    for wp in wind_periods:
        start = wp["start"]
        end = wp["end"]
        # end is the last included hour-start (11am-1pm covers through 1:59).
        if end + timedelta(hours=1) <= now or start > until:
            continue
        # Sample mid-bin for gust/dir so a 6h gust block covers the wind block.
        mid = start + (end - start) / 2
        w = wp["value"]
        g = lookup(gust_periods, mid)
        d = lookup(dir_periods, mid)
        if isinstance(g, (int, float)) and isinstance(w, (int, float)):
            g = max(g, w)
        elif not isinstance(g, (int, float)):
            g = None
        out.append(
            {
                "start": start,
                "end": end,
                "wind": w,
                "gust": g,
                "dir": _compass(d) if isinstance(d, (int, float)) else "",
                "dir_deg": d,
            }
        )
    return out


def _nws_fetch_point(lat: float, lon: float, *, forecast_days: int = 1) -> dict[str, Any]:
    """
    NOAA/NWS fallback when Open-Meteo is rate-limited.
    Returns a dict shaped like Open-Meteo forecast JSON so formatters keep working.
    """
    headers = {"User-Agent": _NWS_UA, "Accept": "application/geo+json"}
    points = _http_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", headers=headers)
    props = points.get("properties") or {}
    hourly_url = props.get("forecastHourly")
    daily_url = props.get("forecast")
    if not hourly_url or not daily_url:
        raise RuntimeError("nws points missing forecast urls")

    hourly_j = _http_json(hourly_url, headers=headers)
    daily_j = _http_json(daily_url, headers=headers)
    h_periods = (hourly_j.get("properties") or {}).get("periods") or []
    d_periods = (daily_j.get("properties") or {}).get("periods") or []
    if not h_periods:
        raise RuntimeError("nws hourly empty")

    now_p = h_periods[0]
    hours_needed = max(24, forecast_days * 24)
    h_slice = h_periods[:hours_needed]

    times: list[str] = []
    winds: list[float | None] = []
    gusts: list[float | None] = []
    dirs: list[float | None] = []
    dir_txt: list[str] = []
    pops: list[float | None] = []
    codes: list[int] = []
    for p in h_slice:
        start = p.get("startTime") or ""
        times.append(start)
        w = _parse_mph(p.get("windSpeed"))
        g = _parse_mph(p.get("windGust")) if p.get("windGust") else None
        winds.append(w)
        gusts.append(g)
        dtxt = _dir_token(p.get("windDirection"))
        dir_txt.append(dtxt)
        # Approximate degrees from compass for blending
        dirs.append(None)
        pop = p.get("probabilityOfPrecipitation") or {}
        pop_v = pop.get("value") if isinstance(pop, dict) else None
        pops.append(float(pop_v) if isinstance(pop_v, (int, float)) else None)
        codes.append(_nws_cond_code(str(p.get("shortForecast") or "")))

    grid = _nws_load_grid(props.get("forecastGridData"), headers)
    precips: list[float | None] = []
    for i, start in enumerate(times):
        key = _hour_key_pt(start)
        precips.append(grid["qpf_mm"].get(key))
        g = gusts[i]
        w = winds[i]
        grid_g = grid["gust_mph"].get(key)
        grid_w = grid["wind_mph"].get(key)
        grid_d = grid["dir_deg"].get(key)
        if not isinstance(w, (int, float)) and isinstance(grid_w, (int, float)):
            w = grid_w
            winds[i] = w
        # Gusts only from grid / hourly — never invent 1.5x copies as truth.
        if isinstance(grid_g, (int, float)):
            g = grid_g
        if isinstance(g, (int, float)) and isinstance(w, (int, float)):
            g = max(g, w)
        gusts[i] = g
        if isinstance(grid_d, (int, float)):
            dirs[i] = grid_d
            if not dir_txt[i]:
                dir_txt[i] = _compass(grid_d)

    # Daily highs/lows from NWS 12-hour periods (daytime + nighttime pairs).
    day_times: list[str] = []
    tmax: list[float | None] = []
    tmin: list[float | None] = []
    d_pop: list[float | None] = []
    d_codes: list[int] = []
    d_wind_max: list[float | None] = []
    d_gust_max: list[float | None] = []
    i = 0
    while i < len(d_periods) and len(day_times) < forecast_days:
        day_p = d_periods[i]
        night_p = d_periods[i + 1] if i + 1 < len(d_periods) else None
        is_day = bool(day_p.get("isDaytime"))
        if not is_day and night_p is None:
            # Odd leading nighttime — skip
            i += 1
            continue
        if is_day:
            hi = day_p.get("temperature")
            lo = night_p.get("temperature") if night_p else None
            label_p = day_p
            wind_candidates = [day_p, night_p] if night_p else [day_p]
            i += 2 if night_p else 1
        else:
            lo = day_p.get("temperature")
            hi = None
            label_p = day_p
            wind_candidates = [day_p]
            i += 1
        start = str(label_p.get("startTime") or "")[:10]
        day_times.append(start)
        tmax.append(float(hi) if isinstance(hi, (int, float)) else None)
        tmin.append(float(lo) if isinstance(lo, (int, float)) else None)
        pop = label_p.get("probabilityOfPrecipitation") or {}
        pop_v = pop.get("value") if isinstance(pop, dict) else None
        d_pop.append(float(pop_v) if isinstance(pop_v, (int, float)) else None)
        d_codes.append(_nws_cond_code(str(label_p.get("shortForecast") or "")))
        wm, gm = None, None
        for wp in wind_candidates:
            if not wp:
                continue
            w = _parse_mph(wp.get("windSpeed"))
            g = _parse_mph(wp.get("windGust")) if wp.get("windGust") else None
            if isinstance(w, float):
                wm = w if wm is None else max(wm, w)
            if isinstance(g, float):
                gm = g if gm is None else max(gm, g)
        d_wind_max.append(wm)
        d_gust_max.append(gm)

    # Prefer hourly (with grid gust overlay) for daily wind/gust maxima.
    if times:
        by_day_w: dict[str, float] = {}
        by_day_g: dict[str, float] = {}
        for start, w, g in zip(times, winds, gusts):
            day = str(start)[:10]
            if isinstance(w, (int, float)):
                by_day_w[day] = w if day not in by_day_w else max(by_day_w[day], w)
            if isinstance(g, (int, float)):
                by_day_g[day] = g if day not in by_day_g else max(by_day_g[day], g)
        for i, day in enumerate(day_times):
            if day in by_day_w:
                d_wind_max[i] = by_day_w[day]
            if day in by_day_g:
                d_gust_max[i] = by_day_g[day]

    cur_temp = now_p.get("temperature")
    cur_wind = winds[0] if winds else _parse_mph(now_p.get("windSpeed"))
    cur_gust = gusts[0] if gusts else None
    cur_dir = dir_txt[0] if dir_txt else _dir_token(now_p.get("windDirection"))
    cur_dir_deg = dirs[0] if dirs else None
    obs = _nws_nearest_obs(props.get("observationStations"), headers)
    wind_periods = _merge_wind_periods(
        grid.get("wind_periods") or [],
        grid.get("gust_periods") or [],
        grid.get("dir_periods") or [],
        hours=max(18, forecast_days * 24),
    )
    # Prefer hourly compass labels on periods when available
    for period in wind_periods:
        for t, dtxt in zip(times, dir_txt):
            try:
                dt = datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(PT)
            except Exception:  # noqa: BLE001
                continue
            if period["start"] <= dt <= period["end"] and dtxt:
                period["dir"] = dtxt
                break

    return {
        "current": {
            "temperature_2m": float(cur_temp) if isinstance(cur_temp, (int, float)) else None,
            "weather_code": _nws_cond_code(str(now_p.get("shortForecast") or "")),
            "wind_speed_10m": cur_wind,
            "wind_gusts_10m": cur_gust,
            "wind_direction_10m": cur_dir_deg,
            "wind_dir": cur_dir,
            "shortwave_radiation": None,
            "is_day": 1 if now_p.get("isDaytime") else 0,
        },
        "hourly": {
            "time": times,
            "shortwave_radiation": [None] * len(times),
            "wind_speed_10m": winds,
            "wind_gusts_10m": gusts,
            "wind_direction_10m": dirs,
            "wind_dir": dir_txt,
            "precipitation_probability": pops,
            "precipitation": precips,
            "weather_code": codes,
        },
        "daily": {
            "time": day_times,
            "temperature_2m_max": tmax,
            "temperature_2m_min": tmin,
            "precipitation_probability_max": d_pop,
            "precipitation_sum": [None] * len(day_times),
            "weather_code": d_codes,
            "wind_speed_10m_max": d_wind_max,
            "wind_gusts_10m_max": d_gust_max,
        },
        "_source": "nws",
        "_gust_resolution": "3-6h",
        "_wind_periods": wind_periods,
        "_obs": obs,
    }


def _fetch_point(lat: float, lon: float, *, forecast_days: int = 7) -> dict[str, Any]:
    """Shared 7-day blob per location. Prefers Open-Meteo; NWS when OM capped."""
    _ = forecast_days
    cache_key = (round(lat, 4), round(lon, 4))
    now = time.time()
    cached = _FETCH_CACHE.get(cache_key)
    if cached and now - cached[0] < _FETCH_CACHE_TTL_SEC:
        return cached[1]

    days = _FORECAST_DAYS
    qs = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "current": (
                "temperature_2m,weather_code,wind_speed_10m,wind_gusts_10m,"
                "wind_direction_10m,shortwave_radiation,is_day"
            ),
            "hourly": (
                "shortwave_radiation,wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                "precipitation_probability,precipitation,weather_code"
            ),
            "daily": (
                "temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                "precipitation_sum,weather_code,wind_speed_10m_max,wind_gusts_10m_max"
            ),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "America/Los_Angeles",
            "forecast_days": days,
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{qs}"
    data: dict[str, Any] | None = None
    om_err: BaseException | None = None
    # Always try Open-Meteo (primary or previous-runs fallback) for true hourly.
    try:
        data = open_meteo_get(url)
        host = data.pop("_om_host", "primary")
        data["_source"] = "open-meteo" if host == "primary" else "open-meteo-prev"
        data["_gust_resolution"] = "hourly"
        cur = data.get("current") or {}
        if isinstance(cur.get("wind_direction_10m"), (int, float)):
            cur["wind_dir"] = _compass(cur["wind_direction_10m"])
        oh = data.get("hourly") or {}
        dirs = oh.get("wind_direction_10m") or []
        if dirs:
            oh["wind_dir"] = [_compass(d) if isinstance(d, (int, float)) else "" for d in dirs]
    except Exception as exc:  # noqa: BLE001
        om_err = exc
    try:
        nws = _nws_fetch_point(lat, lon, forecast_days=days)
    except Exception:
        nws = None

    if data is None:
        if nws is None:
            if cached and now - cached[0] < _STALE_OK_SEC:
                return cached[1]
            if om_err:
                raise om_err
            raise RuntimeError("no weather source available")
        data = nws
    elif nws is not None:
        # Blend: OM hourly + NWS period bins + ridge RAWS consensus for NOW.
        data["_nws"] = {
            "periods": nws.get("_wind_periods") or [],
            "obs": nws.get("_obs"),
            "gust_resolution": nws.get("_gust_resolution"),
        }
        data["_obs"] = nws.get("_obs") or data.get("_obs")
        # Prefer NWS periods for radio wind when OM host is previous-runs
        # (can lag); always keep them for comparison / honesty.
        if data.get("_source") == "open-meteo-prev" and nws.get("_wind_periods"):
            data["_wind_periods"] = nws.get("_wind_periods")
            data["_gust_resolution"] = "3-6h+model"

    _FETCH_CACHE[cache_key] = (now, data)
    return data


def refresh_forecasts() -> None:
    """Pull BRC + Reno once; every wx/alert user shares the result."""
    for lat, lon in (BRC, RENO):
        try:
            _fetch_point(lat, lon)
        except Exception:
            continue


def forecast_context(place: Place = "brc") -> str:
    """Short cached snapshot for Gemini — no extra HTTP if cache is warm."""
    bits: list[str] = []
    for label, (lat, lon) in _points_for(place):
        cached = _FETCH_CACHE.get((round(lat, 4), round(lon, 4)))
        if not cached:
            continue
        data = cached[1]
        bits.append(_fmt_point(label, data))
        rows = _upcoming_hours(data, hours=12)
        wind_bits: list[str] = []
        for r in rows[:8]:
            w, g = r.get("wind"), r.get("gust")
            if not isinstance(w, (int, float)):
                continue
            when = _hour_label(r["time"])
            if isinstance(g, (int, float)) and g > w + 0.5:
                wind_bits.append(f"{when} {w:.0f}g{g:.0f}")
            else:
                wind_bits.append(f"{when} {w:.0f}")
        if wind_bits:
            bits.append(f"{label} next hours: " + ", ".join(wind_bits))
    if not bits:
        return ""
    return (
        "Cached forecast (answer wx follow-ups from this; do not invent): "
        + " | ".join(bits)
    )


def _day_label(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone(PT)
        return dt.strftime("%a")
    except Exception:  # noqa: BLE001
        return iso[:10]


def _points_for(place: Place) -> list[tuple[str, tuple[float, float]]]:
    pts: list[tuple[str, tuple[float, float]]] = []
    if place in ("reno", "both"):
        pts.append(("Reno", RENO))
    if place in ("brc", "both"):
        pts.append(("BRC", BRC))
    return pts


def _solar_pct(wm2: float | None) -> int | None:
    if not isinstance(wm2, (int, float)):
        return None
    return int(max(0, min(100, round(100.0 * float(wm2) / SOLAR_STC_WM2))))


def _hour_label(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone(PT)
        return dt.strftime("%I%p").lstrip("0").lower()
    except Exception:  # noqa: BLE001
        return iso


def _solar_bits(data: dict[str, Any]) -> str | None:
    """
    Estimate array output vs bright-noon STC (~1000 W/m²).
    Example: solar ~0% now, peak ~100%@2pm
    """
    now_pct, peak_pct, peak_t = _solar_stats(data)
    if now_pct is None and peak_pct is None:
        return None
    if now_pct is not None and peak_pct is not None and peak_t:
        # Night: still useful to know daytime peak
        if now_pct == 0 and peak_pct > 0:
            return f"solar peak apx {peak_pct}%@{peak_t}"
        return f"solar apx {now_pct}% now, peak {peak_pct}%@{peak_t}"
    if now_pct is not None:
        return f"solar apx {now_pct}% now"
    return f"solar peak apx {peak_pct}%@{peak_t}" if peak_t else None


def _solar_stats(data: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    """Return (now_pct, peak_pct, peak_hour_label) from GHI vs ~1000 W/m²."""
    cur = data.get("current") or {}
    hourly = data.get("hourly") or {}
    now_pct = _solar_pct(cur.get("shortwave_radiation"))
    times = hourly.get("time") or []
    rads = hourly.get("shortwave_radiation") or []
    peak_pct = None
    peak_t = None
    # Prefer today's daytime peak (not tomorrow's hours if forecast_days>1)
    today = datetime.now(PT).date()
    for t, r in zip(times, rads):
        try:
            dt = datetime.fromisoformat(t).astimezone(PT)
        except Exception:  # noqa: BLE001
            continue
        if dt.date() != today:
            continue
        if dt.hour < 7 or dt.hour > 19:
            continue
        p = _solar_pct(r)
        if p is None:
            continue
        if peak_pct is None or p > peak_pct:
            peak_pct, peak_t = p, _hour_label(t)
    # Fallback: any hour in the series
    if peak_pct is None:
        for t, r in zip(times, rads):
            p = _solar_pct(r)
            if p is None:
                continue
            if peak_pct is None or p > peak_pct:
                peak_pct, peak_t = p, _hour_label(t)
    return now_pct, peak_pct, peak_t


def _obs_now_bit(obs: dict[str, Any]) -> str:
    """Radio token for live ridge consensus / single station."""
    if not isinstance(obs.get("wind_mph"), (int, float)):
        return ""
    odir = _compass(obs.get("dir_deg"))
    og = obs.get("gust_mph")
    n = obs.get("n_stations")
    label = "ridge" if n and int(n) > 1 else (obs.get("name") or "RAWS")
    if isinstance(og, (int, float)) and og > obs["wind_mph"] + 0.5:
        core = f"{odir} {obs['wind_mph']:.0f}g{og:.0f}".strip()
    else:
        core = f"{odir} {obs['wind_mph']:.0f}".strip()
    # Use "apx" not "~" — many mesh UIs treat paired ~ as strikethrough.
    if n and int(n) > 1:
        return f"now {label} apx {core} ({n}st)"
    return f"now {label} {core}"


def _fmt_point(label: str, data: dict[str, Any]) -> str:
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    code = int(cur.get("weather_code") or 0)
    cond = WMO.get(code, f"code{code}")
    temp = cur.get("temperature_2m")
    wind = cur.get("wind_speed_10m")
    gust = cur.get("wind_gusts_10m")
    wdir = cur.get("wind_dir") or _compass(cur.get("wind_direction_10m"))
    tmax = (daily.get("temperature_2m_max") or [None])[0]
    tmin = (daily.get("temperature_2m_min") or [None])[0]
    pop = (daily.get("precipitation_probability_max") or [None])[0]

    bits = [f"{label}: {temp:.0f}F {cond}" if isinstance(temp, (int, float)) else f"{label}: {cond}"]
    obs = data.get("_obs") or {}
    now_bit = _obs_now_bit(obs)
    if now_bit:
        bits.append(now_bit)
    elif isinstance(wind, (int, float)):
        dir_s = f"{wdir} " if wdir else ""
        if isinstance(gust, (int, float)) and gust > wind + 0.5:
            bits.append(f"{dir_s}fcst {wind:.0f}mph gusts {gust:.0f}")
        else:
            bits.append(f"{dir_s}fcst {wind:.0f}mph")
    if isinstance(tmin, (int, float)) and isinstance(tmax, (int, float)):
        bits.append(f"today {tmin:.0f}-{tmax:.0f}F")
    if isinstance(pop, (int, float)):
        bits.append(f"precip {pop:.0f}%")
    solar = _solar_bits(data)
    if solar:
        bits.append(solar)
    return ", ".join(bits)


def parse_place(text: str) -> Place | None:
    t = text.lower().strip()
    if t in ("reno", "rno", "town"):
        return "reno"
    if t in ("brc", "gerlach", "playa", "burn"):
        return "brc"
    if t in ("both", "all", "corridor", ""):
        return "both"
    return None


def weather_brief(place: Place = "both") -> str:
    """Dumb, reliable corridor weather + rough solar %. No GPS requests."""
    try:
        parts: list[str] = []
        for label, (lat, lon) in _points_for(place):
            parts.append(_fmt_point(label, _fetch_point(lat, lon, forecast_days=1)))
        text = " | ".join(parts)
        # Slightly longer now that solar is included
        return text if len(text) <= 420 else text[:417] + "…"
    except Exception as exc:  # noqa: BLE001
        hint = _wx_error_hint(exc)
        return f"WX unavailable ({hint}). Try wx again in a few minutes."


def _period_clock(dt: datetime) -> str:
    return dt.strftime("%I%p").lstrip("0").lower()


def _fmt_wind_period_bit(p: dict[str, Any], *, now: datetime | None = None) -> str:
    start = p["start"]
    end = p["end"]
    if now is not None and start < now <= end:
        start = now
    w = p.get("wind")
    g = p.get("gust")
    d = p.get("dir") or ""
    if start.hour == end.hour and start.date() == end.date():
        when = _period_clock(start)
    else:
        when = f"{_period_clock(start)}-{_period_clock(end)}"
    if isinstance(w, (int, float)) and isinstance(g, (int, float)) and g > w + 0.5:
        core = f"{w:.0f}g{g:.0f}"
    elif isinstance(w, (int, float)):
        core = f"{w:.0f}"
    elif isinstance(g, (int, float)):
        core = f"g{g:.0f}"
    else:
        core = "?"
    return f"{when} {d} {core}".replace("  ", " ").strip()


def _collapse_hourly_wind_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical consecutive hours so copied bins don't look hourly."""
    if not rows:
        return []
    periods: list[dict[str, Any]] = []
    cur = {
        "start": rows[0]["dt"],
        "end": rows[0]["dt"],
        "wind": rows[0].get("wind"),
        "gust": rows[0].get("gust"),
        "dir": rows[0].get("dir") or "",
    }
    for r in rows[1:]:
        same = (
            r.get("wind") == cur["wind"]
            and r.get("gust") == cur["gust"]
            and (r.get("dir") or "") == cur["dir"]
        )
        if same:
            cur["end"] = r["dt"]
            continue
        periods.append(cur)
        cur = {
            "start": r["dt"],
            "end": r["dt"],
            "wind": r.get("wind"),
            "gust": r.get("gust"),
            "dir": r.get("dir") or "",
        }
    periods.append(cur)
    return periods


def _fmt_wind_hourly(label: str, data: dict[str, Any], hours: int = 12) -> tuple[str, str]:
    """
    Best practical playa wind report:
      NOW = ridge RAWS consensus (live)
      FCST = NWS 3–6h bins and/or hourly model (honest resolution)
    """
    detail_lines: list[str] = [f"{label} wind (mph; g=gusts):"]
    now = datetime.now(PT)
    periods = list(data.get("_wind_periods") or [])
    src = str(data.get("_source") or "")
    # Primary Open-Meteo = true hourly; don't replace with NWS bins.
    if not periods and src != "open-meteo":
        periods = list((data.get("_nws") or {}).get("periods") or [])

    if periods:
        until = now + timedelta(hours=hours)
        use = [
            p
            for p in periods
            if p["end"] + timedelta(hours=1) >= now and p["start"] <= until
        ]
    else:
        rows = _upcoming_hours(data, hours=hours)
        use = _collapse_hourly_wind_rows(rows)

    obs = data.get("_obs") or (data.get("_nws") or {}).get("obs") or {}
    now_bit = _obs_now_bit(obs)
    if now_bit:
        detail_lines.append(f"  LIVE {now_bit}")

    if not use and not now_bit:
        return f"{label}: no wind data yet", f"{label}: no wind data."

    bits = [_fmt_wind_period_bit(p, now=now) for p in use]
    peak_g = None
    peak_when = None
    for p in use:
        g = p.get("gust")
        if isinstance(g, (int, float)) and (peak_g is None or g > peak_g):
            peak_g = g
            peak_when = _fmt_wind_period_bit(p, now=now).split()[0]

    for p in use:
        detail_lines.append(f"  FCST {_fmt_wind_period_bit(p, now=now)}")

    parts: list[str] = []
    if now_bit:
        parts.append(now_bit)
    if bits:
        parts.append("fcst " + " · ".join(bits[:5]))
    brief = f"{label} wind: " + " | ".join(parts)
    if peak_g is not None and peak_when:
        brief += f" | peak {peak_g:.0f}@{peak_when}"
        detail_lines.append(f"Peak gust {peak_g:.0f} mph around {peak_when}.")
    if len(brief) > 480:
        brief = brief[:477] + "…"
    return brief, "\n".join(detail_lines)


def wind_hourly(place: Place = "brc", *, hours: int = 12) -> tuple[str, str]:
    """Hour-by-hour wind/gusts. Returns (radio brief, detail for chat delve)."""
    try:
        briefs: list[str] = []
        details: list[str] = []
        for label, (lat, lon) in _points_for(place):
            data = _fetch_point(lat, lon, forecast_days=2)
            b, d = _fmt_wind_hourly(label, data, hours=hours)
            briefs.append(b)
            details.append(d)
        brief = " | ".join(briefs)
        if len(brief) > 480:
            brief = brief[:477] + "…"
        detail = "\n\n".join(details)
        return brief, detail
    except Exception as exc:  # noqa: BLE001
        hint = _wx_error_hint(exc)
        msg = f"WIND unavailable ({hint}). Try again in a minute."
        return msg, msg


def _inch_token(mm: float | None) -> str:
    """Radio-short inches: t, .01, 0.12 — empty if dry."""
    if not isinstance(mm, (int, float)) or mm <= 0:
        return ""
    inch = float(mm) / 25.4
    if inch < 0.005:
        return "t"
    s = f"{inch:.2f}"
    return s[1:] if s.startswith("0.") else s


def _hour_is_wet(row: dict[str, Any]) -> bool:
    pop = row.get("pop")
    precip = row.get("precip")
    if isinstance(pop, (int, float)) and pop >= 10:
        return True
    if isinstance(precip, (int, float)) and precip >= 0.05:
        return True
    return False


def _fmt_rain_hour(row: dict[str, Any]) -> tuple[str, str]:
    when = _hour_label(row["time"])
    pop = row.get("pop")
    amt = _inch_token(row.get("precip") if isinstance(row.get("precip"), (int, float)) else None)
    pop_s = f"{pop:.0f}%" if isinstance(pop, (int, float)) else ""
    if pop_s and amt:
        rest = f"{pop_s}{amt}"
    elif pop_s:
        rest = pop_s
    elif amt:
        rest = amt
    else:
        rest = "0"
    return when, rest


def _join_hour_range(start: str, end: str, rest: str) -> str:
    if start == end:
        return f"{start} {rest}"
    return f"{start}-{end} {rest}"


def _collapse_rain_span(items: list[tuple[str, str]]) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    start_when, rest = items[0]
    end_when = start_when
    for when, r in items[1:]:
        if r == rest:
            end_when = when
            continue
        out.append(_join_hour_range(start_when, end_when, rest))
        start_when, end_when, rest = when, when, r
    out.append(_join_hour_range(start_when, end_when, rest))
    return out


def _fmt_rain_hourly(label: str, data: dict[str, Any], hours: int = 24) -> tuple[str, str]:
    """Hourly rain chance + amount (inches). Collapses dry stretches for radio."""
    rows = _upcoming_hours(data, hours=hours)
    if not rows:
        return f"{label}: no hourly rain yet", f"{label}: no hourly rain data."

    detail_lines = [f"{label} hourly rain (chance, inches):"]
    bits: list[str] = []
    wet_run: list[tuple[str, str]] = []
    dry_start = dry_end = None
    max_pop = None
    max_pop_t = None
    total_mm = 0.0
    any_amount = False

    def flush_wet() -> None:
        nonlocal wet_run
        if wet_run:
            bits.extend(_collapse_rain_span(wet_run))
            wet_run = []

    def flush_dry() -> None:
        nonlocal dry_start, dry_end
        if dry_start is None or dry_end is None:
            return
        if dry_start == dry_end:
            bits.append(f"{dry_start} 0")
        else:
            bits.append(f"dry {dry_start}-{dry_end}")
        dry_start = dry_end = None

    for r in rows:
        pop = r.get("pop")
        precip = r.get("precip")
        when = _hour_label(r["time"])
        if isinstance(pop, (int, float)) and (max_pop is None or pop > max_pop):
            max_pop, max_pop_t = pop, when
        if isinstance(precip, (int, float)) and precip > 0:
            total_mm += precip
            any_amount = True
        pop_s = f"{pop:.0f}%" if isinstance(pop, (int, float)) else "—"
        amt = _inch_token(precip if isinstance(precip, (int, float)) else None) or "0"
        detail_lines.append(f"  {when}: {pop_s} chance, {amt} in")

        if _hour_is_wet(r):
            flush_dry()
            wet_run.append(_fmt_rain_hour(r))
        else:
            flush_wet()
            if dry_start is None:
                dry_start = when
            dry_end = when
    flush_wet()
    flush_dry()

    if not any(_hour_is_wet(r) for r in rows):
        extra = f" (max {max_pop:.0f}% @{max_pop_t})" if max_pop and max_pop_t else ""
        brief = f"{label} rain: none next {hours}h{extra}"
        detail_lines.append("No meaningful rain in this window.")
        return brief, "\n".join(detail_lines)

    brief = f"{label} rain: " + ", ".join(bits)
    if any_amount:
        tot = _inch_token(total_mm) or "t"
        brief += f" | apx {tot}\" next {hours}h"
        detail_lines.append(f"Total apx {tot} inches over {hours}h (very light if under .05).")
    elif max_pop:
        brief += f" | amounts tiny/unknown, peak {max_pop:.0f}%"
    return brief, "\n".join(detail_lines)


def rain_hourly(place: Place = "brc", *, hours: int = 24) -> tuple[str, str]:
    """Hour-by-hour rain chance + amount. Returns (radio brief, detail for .r)."""
    try:
        briefs: list[str] = []
        details: list[str] = []
        for label, (lat, lon) in _points_for(place):
            data = _fetch_point(lat, lon, forecast_days=2)
            b, d = _fmt_rain_hourly(label, data, hours=hours)
            briefs.append(b)
            details.append(d)
        brief = " | ".join(briefs)
        if len(brief) > 480:
            brief = brief[:477] + "…"
        detail = "\n\n".join(details)
        detail += "\n\nFollow-ups: when it starts, how much total, or vs wind."
        return brief, detail
    except Exception as exc:  # noqa: BLE001
        hint = _wx_error_hint(exc)
        msg = f"RAIN unavailable ({hint}). Try again in a minute."
        return msg, msg


def _fmt_week_day(i: int, daily: dict[str, Any]) -> tuple[str, str] | None:
    times = daily.get("time") or []
    if i >= len(times):
        return None
    day = _day_label(times[i])
    tmax = (daily.get("temperature_2m_max") or [None])[i] if i < len(daily.get("temperature_2m_max") or []) else None
    tmin = (daily.get("temperature_2m_min") or [None])[i] if i < len(daily.get("temperature_2m_min") or []) else None
    pop = (
        (daily.get("precipitation_probability_max") or [None])[i]
        if i < len(daily.get("precipitation_probability_max") or [])
        else None
    )
    precip = (
        (daily.get("precipitation_sum") or [None])[i]
        if i < len(daily.get("precipitation_sum") or [])
        else None
    )
    code = (daily.get("weather_code") or [None])[i] if i < len(daily.get("weather_code") or []) else None
    wmax = (
        (daily.get("wind_speed_10m_max") or [None])[i]
        if i < len(daily.get("wind_speed_10m_max") or [])
        else None
    )
    gmax = (
        (daily.get("wind_gusts_10m_max") or [None])[i]
        if i < len(daily.get("wind_gusts_10m_max") or [])
        else None
    )
    cond = WMO.get(int(code or 0), "—")
    hi_lo = ""
    if isinstance(tmin, (int, float)) and isinstance(tmax, (int, float)):
        hi_lo = f"{tmin:.0f}-{tmax:.0f}F"
    wind_s = ""
    if isinstance(wmax, (int, float)):
        if isinstance(gmax, (int, float)) and gmax > wmax + 0.5:
            wind_s = f"wind{wmax:.0f}g{gmax:.0f}"
        else:
            wind_s = f"wind{wmax:.0f}"
    pop_s = f"rain{pop:.0f}%" if isinstance(pop, (int, float)) and pop >= 20 else ""
    brief_bits = [x for x in (day, hi_lo, cond, wind_s, pop_s) if x]
    brief = " ".join(brief_bits)

    detail_bits = [f"{day} ({times[i][:10]}): {cond}"]
    if hi_lo:
        detail_bits.append(f"temps {hi_lo}")
    if isinstance(wmax, (int, float)):
        if isinstance(gmax, (int, float)):
            detail_bits.append(f"max wind {wmax:.0f} mph, max gusts {gmax:.0f} mph")
        else:
            detail_bits.append(f"max wind {wmax:.0f} mph")
    if isinstance(pop, (int, float)):
        detail_bits.append(f"precip chance {pop:.0f}%")
    if isinstance(precip, (int, float)) and precip > 0:
        detail_bits.append(f"precip ~{precip:.1f} mm")
    return brief, "; ".join(detail_bits)


def _fmt_week(label: str, data: dict[str, Any]) -> tuple[str, str]:
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return f"{label}: no week data", f"{label}: no week data."
    briefs: list[str] = []
    details: list[str] = [f"{label} 7-day forecast:"]
    for i in range(min(7, len(times))):
        row = _fmt_week_day(i, daily)
        if not row:
            continue
        b, d = row
        briefs.append(b)
        details.append(f"  {d}")
    brief = f"{label} week: " + " · ".join(briefs)
    return brief, "\n".join(details)


def weather_week(place: Place = "brc") -> tuple[str, str]:
    """7-day overview. Returns (radio brief, detail for chat delve)."""
    try:
        briefs: list[str] = []
        details: list[str] = []
        for label, (lat, lon) in _points_for(place):
            data = _fetch_point(lat, lon, forecast_days=7)
            b, d = _fmt_week(label, data)
            briefs.append(b)
            details.append(d)
        brief = " | ".join(briefs)
        if len(brief) > 520:
            brief = brief[:517] + "…"
        detail = "\n\n".join(details)
        return brief, detail
    except Exception as exc:  # noqa: BLE001
        hint = _wx_error_hint(exc)
        msg = f"WEEK unavailable ({hint}). Try again in a minute."
        return msg, msg


def parse_wx_request(text: str, default_place: Place = "brc") -> tuple[str, Place] | None:
    """
    Parse wx / weather command rest (or full command).
    Returns (mode, place) where mode is 'now' | 'wind' | 'rain' | 'week' | 'watch'.
    None if not a weather command.
    """
    t = text.lower().strip()
    if t in ("wx", "weather"):
        return "now", default_place
    if not (t.startswith("wx ") or t.startswith("weather ")):
        # bare shortcuts
        if t in ("wind", "winds"):
            return "wind", default_place
        if t in ("rain", "precip", "precipitation"):
            return "rain", default_place
        if t in ("week", "weekly"):
            return "week", default_place
        if t in ("watch", "tip", "insight", "smart", "beware"):
            return "watch", default_place
        return None

    rest = t.split(None, 1)[1].strip()
    parts = rest.split()
    mode = "now"
    place_bits: list[str] = []
    for p in parts:
        if p in ("wind", "winds", "hourly", "hour", "gust", "gusts"):
            mode = "wind"
        elif p in ("rain", "precip", "precipitation", "pop", "shower", "showers"):
            mode = "rain"
        elif p in ("week", "weekly", "7day", "7d", "forecast"):
            mode = "week"
        elif p in ("watch", "tip", "insight", "smart", "beware", "heads"):
            mode = "watch"
        elif p in ("now", "today", "daily"):
            mode = "now"
        elif p in ("push", "all", "broadcast") or p.startswith("push"):
            return None  # admin push handled elsewhere
        else:
            place_bits.append(p)

    place_raw = " ".join(place_bits).strip()
    if not place_raw:
        place: Place = default_place
    else:
        parsed = parse_place(place_raw)
        if parsed is None:
            return None
        place = parsed
    return mode, place


def _date_label(offset_days: int = 0) -> str:
    d = datetime.now(PT) + timedelta(days=offset_days)
    # e.g. Sat 8/1
    return d.strftime("%a %-m/%-d")


def _severity(score: int) -> str:
    """How urgent a WATCH flag is — stops mild desert weather sounding like an emergency."""
    if score >= 85:
        return "SERIOUS"
    if score >= 60:
        return "HEADS-UP"
    return "FYI"


def _insight_concerns(label: str, data: dict[str, Any]) -> list[tuple[int, str]]:
    """Ranked (score, message) concerns for the next ~36h. Higher score = worse."""
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    concerns: list[tuple[int, str]] = []
    today = _date_label(0)
    tom = _date_label(1)

    tmax = (daily.get("temperature_2m_max") or [None])[0]
    tmin = (daily.get("temperature_2m_min") or [None])[0]
    pop = (daily.get("precipitation_probability_max") or [None])[0]
    code0 = int((daily.get("weather_code") or [0])[0] or 0)

    tmax2 = (daily.get("temperature_2m_max") or [None, None])[1] if len(daily.get("temperature_2m_max") or []) > 1 else None
    pop2 = (
        (daily.get("precipitation_probability_max") or [None, None])[1]
        if len(daily.get("precipitation_probability_max") or []) > 1
        else None
    )
    gmax = (daily.get("wind_gusts_10m_max") or [None])[0]
    gmax2 = (
        (daily.get("wind_gusts_10m_max") or [None, None])[1]
        if len(daily.get("wind_gusts_10m_max") or []) > 1
        else None
    )

    rows = _upcoming_hours(data, hours=24)
    peak_g = None
    peak_t = None
    for r in rows:
        g = r.get("gust")
        if isinstance(g, (int, float)) and (peak_g is None or g > peak_g):
            peak_g, peak_t = g, _hour_label(r["time"])

    # Heat — 95–99 is normal desert FYI, not an emergency
    if isinstance(tmax, (int, float)):
        if tmax >= 105:
            concerns.append((95, f"extreme heat ~{tmax:.0f}F — shade, water, limit midday"))
        elif tmax >= 100:
            concerns.append((80, f"hot ~{tmax:.0f}F — shade/water, careful midday"))
        elif tmax >= 95:
            concerns.append((42, f"warm ~{tmax:.0f}F — normal desert; hydrate"))
    if isinstance(tmax2, (int, float)) and tmax2 >= 100 and (not isinstance(tmax, (int, float)) or tmax2 > tmax + 2):
        concerns.append((70, f"{tom}: hotter ~{tmax2:.0f}F — plan shade early"))

    if isinstance(tmin, (int, float)) and isinstance(tmax, (int, float)):
        swing = tmax - tmin
        if tmin <= 40:
            concerns.append((65, f"night cold ~{tmin:.0f}F — layers / warm batteries"))
        elif swing >= 40:
            concerns.append((42, f"big swing {tmin:.0f}-{tmax:.0f}F — pack layers"))

    gust_ref = peak_g if isinstance(peak_g, (int, float)) else gmax
    when = peak_t or "today"
    if isinstance(gust_ref, (int, float)):
        if gust_ref >= 40:
            concerns.append(
                (98, f"strong gusts ~{gust_ref:.0f}mph by {when} — stake shade, secure gear")
            )
        elif gust_ref >= 35:
            concerns.append(
                (85, f"gusty ~{gust_ref:.0f}mph by {when} — stake down / watch shade")
            )
        elif gust_ref >= 28:
            concerns.append(
                (62, f"breezy ~{gust_ref:.0f}mph by {when} — check stakes (common)")
            )
        elif gust_ref >= 22:
            concerns.append((40, f"light wind ~{gust_ref:.0f}mph by {when} — routine"))
    if isinstance(gmax2, (int, float)) and gmax2 >= 35:
        if not isinstance(gust_ref, (int, float)) or gmax2 > float(gust_ref) + 5:
            concerns.append((75, f"{tom}: windier — gusts up to ~{gmax2:.0f}mph"))

    stormy = code0 in STORMY_CODES
    for r in rows:
        if int(r.get("code") or 0) in STORMY_CODES:
            stormy = True
            break
    if stormy:
        concerns.append((90, "storms possible — cover gear, charge early"))
    elif isinstance(pop, (int, float)) and pop >= 50:
        concerns.append((70, f"rain chance ~{pop:.0f}% — cover gear"))
    elif isinstance(pop, (int, float)) and pop >= 30:
        concerns.append((45, f"some rain chance ~{pop:.0f}% — cover handy"))
    if isinstance(pop2, (int, float)) and pop2 >= 50 and (not isinstance(pop, (int, float)) or pop2 > pop):
        concerns.append((65, f"{tom}: wetter (apx {pop2:.0f}% rain)"))

    cur_gust = cur.get("wind_gusts_10m")
    if isinstance(cur_gust, (int, float)) and cur_gust >= 30:
        concerns.append((88, f"gusting {cur_gust:.0f}mph NOW — secure camp"))

    # Cloudy / weak solar day — rough charge loss vs clear-noon (~1000 W/m²)
    _, peak_pct, peak_hour = _solar_stats(data)
    if isinstance(peak_pct, int) and peak_hour:
        loss = max(0, 100 - peak_pct)
        if peak_pct <= 35:
            concerns.append(
                (
                    78,
                    f"weak solar peak apx {peak_pct}%@{peak_hour} (apx {loss}% less than clear) — charge early, conserve",
                )
            )
        elif peak_pct <= 55:
            concerns.append(
                (
                    64,
                    f"cloudy charge apx {peak_pct}%@{peak_hour} (apx {loss}% less than clear) — top up when bright",
                )
            )
        elif peak_pct <= 70:
            concerns.append(
                (
                    48,
                    f"hazy solar apx {peak_pct}%@{peak_hour} (apx {loss}% less than clear) — slower charge",
                )
            )

    concerns.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for score, msg in concerns:
        low = msg.lower()
        theme = (
            "solar" if "solar" in low or "charge" in low or "hazy" in low or "cloudy charge" in low
            else "heat" if "heat" in low or "hot" in low or "warm" in low
            else "wind" if "gust" in low or "wind" in low or "breezy" in low
            else "rain" if "rain" in low or "storm" in low or "wet" in low
            else "cold" if "cold" in low or "swing" in low
            else msg[:8]
        )
        if theme in seen:
            continue
        seen.add(theme)
        out.append((score, msg))
    return out


def weather_insight(place: Place = "brc") -> tuple[str, str]:
    """
    Smart 'what to watch' brief — dated + severity tagged.
    Returns (radio brief, detail for .r).
    """
    try:
        today = _date_label(0)
        briefs: list[str] = []
        details: list[str] = [f"Forecast window around {today} (local PT)."]
        for label, (lat, lon) in _points_for(place):
            data = _fetch_point(lat, lon, forecast_days=3)
            concerns = _insight_concerns(label, data)
            details.append(f"{label}:")
            notable = [c for c in concerns if c[0] >= 55]
            mild = [c for c in concerns if c[0] < 55]
            if not notable and not mild:
                briefs.append(f"{label} {today}: quiet — no heat/wind/rain flags")
                details.append("  (none)")
            elif not notable:
                # Only mild FYI — say so clearly
                parts = [f"{_severity(s)} {m}" for s, m in mild[:2]]
                briefs.append(
                    f"{label} {today}: mild only — " + "; ".join(parts) + " (not urgent)"
                )
                for score, msg in mild[:4]:
                    details.append(f"  [{_severity(score)} {score}] {msg}")
            else:
                parts = [f"{_severity(s)} {m}" for s, m in notable[:2]]
                briefs.append(f"{label} {today}: " + "; ".join(parts))
                for score, msg in concerns[:5]:
                    details.append(f"  [{_severity(score)} {score}] {msg}")
        brief = " | ".join(briefs)
        # One-line legend once
        if "SERIOUS" in brief or "HEADS-UP" in brief or "FYI" in brief:
            brief += " · SERIOUS=act, HEADS-UP=plan, FYI=mild"
        if len(brief) > 460:
            brief = brief[:457] + "…"
        return brief, "\n".join(details)
    except Exception as exc:  # noqa: BLE001
        hint = _wx_error_hint(exc)
        msg = f"WATCH unavailable ({hint}). Try again in a minute."
        return msg, msg


def _upcoming_hours(data: dict[str, Any], hours: int = ALERT_LOOKAHEAD_HOURS) -> list[dict[str, Any]]:
    """Rows for the next `hours` from now (inclusive of current hour)."""
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return []
    now = datetime.now(PT).replace(minute=0, second=0, microsecond=0)
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(times):
        try:
            dt = datetime.fromisoformat(t).astimezone(PT)
        except Exception:  # noqa: BLE001
            continue
        delta_h = (dt - now).total_seconds() / 3600.0
        if -0.1 <= delta_h <= hours:
            rows.append(
                {
                    "time": t,
                    "dt": dt,
                    "gust": (hourly.get("wind_gusts_10m") or [None])[i]
                    if i < len(hourly.get("wind_gusts_10m") or [])
                    else None,
                    "wind": (hourly.get("wind_speed_10m") or [None])[i]
                    if i < len(hourly.get("wind_speed_10m") or [])
                    else None,
                    "dir": (
                        (hourly.get("wind_dir") or [None])[i]
                        if i < len(hourly.get("wind_dir") or [])
                        else _compass(
                            (hourly.get("wind_direction_10m") or [None])[i]
                            if i < len(hourly.get("wind_direction_10m") or [])
                            else None
                        )
                    ),
                    "pop": (hourly.get("precipitation_probability") or [None])[i]
                    if i < len(hourly.get("precipitation_probability") or [])
                    else None,
                    "precip": (hourly.get("precipitation") or [None])[i]
                    if i < len(hourly.get("precipitation") or [])
                    else None,
                    "code": (hourly.get("weather_code") or [None])[i]
                    if i < len(hourly.get("weather_code") or [])
                    else None,
                }
            )
    return rows


def detect_alerts(label: str, data: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Returns list of (dedupe_key, message) for sudden wind/rain in the next few hours.
    Keys are stable per day+place+kind+hour so later gust windows can still alert.
    """
    cur = data.get("current") or {}
    cur_gust = cur.get("wind_gusts_10m")
    if not isinstance(cur_gust, (int, float)):
        cur_gust = 0.0
    rows = _upcoming_hours(data)
    day = datetime.now(PT).strftime("%Y-%m-%d")
    out: list[tuple[str, str]] = []

    # Also consider NWS 3–6h bins (hourly rows may miss peak if bins weren't expanded).
    now = datetime.now(PT)
    for p in data.get("_wind_periods") or []:
        try:
            start, end = p["start"], p["end"]
            g = p.get("gust")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(g, (int, float)):
            continue
        if end + timedelta(hours=1) < now or start > now + timedelta(hours=ALERT_LOOKAHEAD_HOURS):
            continue
        rows.append(
            {
                "time": start.isoformat(),
                "dt": start,
                "gust": g,
                "wind": p.get("wind"),
                "pop": None,
                "precip": None,
                "code": None,
            }
        )

    if not rows:
        return out

    # Wind: gusts in the next ~2h at/above threshold, or a sharp jump
    wind_hits = []
    for r in rows:
        g = r.get("gust")
        if not isinstance(g, (int, float)):
            continue
        if g >= GUST_ALERT_MPH or (g >= cur_gust + GUST_JUMP_MPH and g >= 18):
            wind_hits.append(r)
    if wind_hits:
        best = max(wind_hits, key=lambda r: r.get("gust") or 0)
        g = best["gust"]
        when = _hour_label(best["time"])
        hour_key = str(best.get("time") or when)[:13]
        key = f"{day}|{label}|wind|{hour_key}"
        out.append(
            (
                key,
                f"WX ALERT {label}: gusts {g:.0f}mph by {when} — stake down / secure shade",
            )
        )

    # Rain / storms
    rain_hits = []
    for r in rows:
        pop = r.get("pop")
        precip = r.get("precip")
        code = int(r.get("code") or 0)
        wet = False
        if isinstance(pop, (int, float)) and pop >= RAIN_POP_PCT:
            wet = True
        if isinstance(precip, (int, float)) and precip >= RAIN_MM:
            wet = True
        if code in STORMY_CODES:
            wet = True
        if wet:
            rain_hits.append(r)
    if rain_hits:
        first = rain_hits[0]
        when = _hour_label(first["time"])
        code = int(first.get("code") or 0)
        cond = WMO.get(code, "rain")
        pop = first.get("pop")
        pop_s = f", {pop:.0f}% chance" if isinstance(pop, (int, float)) else ""
        key = f"{day}|{label}|rain|{str(first.get('time') or when)[:13]}"
        out.append(
            (
                key,
                f"WX ALERT {label}: {cond} possible by {when}{pop_s} — cover gear / charge now",
            )
        )

    return out


def due_weather_alerts(*, place: Place) -> list[tuple[str, str]]:
    """Fetch corridor forecast and return alert messages (caller dedupes per user)."""
    msgs: list[tuple[str, str]] = []
    points: list[tuple[str, tuple[float, float]]] = []
    if place in ("reno", "both"):
        points.append(("Reno", RENO))
    if place in ("brc", "both"):
        points.append(("BRC", BRC))
    for label, (lat, lon) in points:
        try:
            data = _fetch_point(lat, lon)
        except Exception:  # noqa: BLE001
            continue
        msgs.extend(detect_alerts(label, data))
    return msgs


def normalize_alert_place(text: str) -> Place:
    return parse_place(text) or "both"
