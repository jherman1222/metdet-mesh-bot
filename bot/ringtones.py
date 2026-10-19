"""Short RTTTL notification SFX for Meshtastic buzzers (SenseCap T1000-E, etc.).

Alert blips only — not songs. Meshtastic max: 230 chars.
CRITICAL: set Nag Timeout to 0 (not 1). While nagging, firmware restarts the
RTTTL when it finishes — a short coin will play ~3× if Nag Timeout is 1 second.
"""

from __future__ import annotations

# Keep each short. Max 230 chars (device limit).
RINGTONES: dict[str, str] = {
    # --- Meshtastic / Nintendo SFX ---
    "mesh_default": (
        "Mesh:d=32,o=5,b=565:f6,p,f6,4p,p,f6,p,f6,2p,p,b6,p,b6,p,b6,p,b6,p,"
        "b,p,b,p,b,p,b,p,b,p,b,p,b,p,b,1p.,2p.,p"
    ),
    "mario_coin": "Coin:d=32,o=6,b=320:b,e7",
    "mario_short": "Mario:d=16,o=5,b=200:e6,e6,32p,8e6,16c6,8e6,8g6,8p,8g",
    "mario_powerup": "PowerUp:d=32,o=5,b=280:g,a,b,c6,d6,e6,f#6,g6,a6,b6,4c7",
    "mario_1up": "1Up:d=32,o=5,b=280:e6,g6,e7,c7,d7,g7",
    "zelda_item": "Item:d=32,o=5,b=200:g,c6,d6,4g6",
    "zelda_secret": "Secret:d=32,o=5,b=220:f,a,c6,4f6",
    # --- Pager / phone ---
    # Classic Grande Valse — full phrase (fun, but long for every DM)
    "nokia": "Nokia:d=4,o=4,b=210:8e5,8d5,f#,g#,8c#5,8b,d,e,8b,8a,c#,e,2a",
    # Just the opening hook — ~1s, still “ha that’s Nokia”
    "nokia_short": "NokiaShort:d=16,o=4,b=260:e5,d5,f#,g#,c#5,8b",
    "pager": "Pager:d=16,o=4,b=180:a,32p,4a,32p,a,32p,4a",
    "pager_low": "PageLo:d=16,o=4,b=140:e,32p,4e,32p,e,32p,4e",
    "double_beep": "Beep:d=32,o=5,b=240:a,16p,a",
    "double_beep_lo": "BeepLo:d=32,o=4,b=220:e,16p,e",
    "soft_beep": "SoftBeep:d=32,o=4,b=180:8e",
    "soft_beep2": "SoftBeep2:d=32,o=4,b=160:e,16p,8g",
    "triple_beep": "TriBeep:d=32,o=5,b=280:a,p,a,p,a",
    "sms_chirp": "SMS:d=32,o=5,b=240:c,e,g,8c6,16p,g,e,c",
    "ringtone_classic": "Ring:d=16,o=4,b=180:e,g,e,g,e,g,8p,e,g,e,g",
    "trim_phone": "Trim:d=32,o=4,b=320:a,b,a,b,a,b,a,8p,a,b,a,b,a,b,a",
    "morse_cq": "CQ:d=32,o=4,b=160:8c,p,c,p,8c,p,c,8p,8c,p,8c,p,c,p,8c,16p",
    # --- Short low / mid blips (o=4 is about as low as PWM buzzers go cleanly) ---
    "ping": "Ping:d=32,o=5,b=260:8a",
    "ping_lo": "PingLo:d=32,o=4,b=240:8e",
    "thud": "Thud:d=16,o=4,b=120:4c,8p,8c",
    "knock": "Knock:d=16,o=4,b=150:c,32p,c,32p,4c",
    "tick": "Tick:d=32,o=4,b=300:8c",
    "tock": "Tock:d=32,o=4,b=280:8g",
    "blip": "Blip:d=32,o=4,b=260:c,e,8g",
    "bloops": "Bloops:d=16,o=4,b=200:c,8p,e,8p,g",
    "drop": "Drop:d=16,o=4,b=180:g,e,4c",
    "rise": "Rise:d=16,o=4,b=180:c,e,4g",
    "soft_chime": "Chime:d=16,o=4,b=140:e,g,c5,8p,c5",
    "chord_up": "Chord:d=32,o=4,b=180:c,e,g,8c5",
    "chord_down": "Fall:d=32,o=4,b=180:c5,g,e,8c",
    "marimba": "Marimba:d=16,o=4,b=200:c,e,g,c5,16p,g,e,c",
    "ripple": "Ripple:d=32,o=4,b=200:e,g,b,e5,16p,b,g,e",
    "radar": "Radar:d=32,o=4,b=220:c,e,g,c5,g,e,c,16p,c,e,g,c5",
    "urgent": "Urgent:d=32,o=5,b=320:a,a,a,16p,a,a,a",
    "urgent_lo": "UrgLo:d=32,o=4,b=300:e,e,e,16p,e,e,e",
    "low_batt": "LowBatt:d=16,o=4,b=100:c,8p,c,8p,4c",
    "heartbeat": "Heart:d=16,o=4,b=110:8c,16p,8c,4p,8c,16p,8c",
    "sonar": "Sonar:d=8,o=4,b=90:c,4p,c,4p,2c",
    "whistle_lo": "Whistle:d=32,o=4,b=200:c,d,e,g,8c5",
    # --- Camp / MetDet ---
    "metdet_ping": "MetDet:d=32,o=4,b=200:c,e,g,8c5",
    "short_alert": "Alert:d=32,o=4,b=240:c,e,g,c5,16p,c5",
    "dust_blip": "Dust:d=32,o=4,b=150:c,e,g,c5,16p,g",
    "sunrise_blip": "Sun:d=32,o=4,b=140:c,e,g,c5,8e5",
    "happy": "Happy:d=32,o=4,b=220:c,e,g,c5,g,e,c",
    "sad": "Sad:d=16,o=4,b=90:e,c,a4,4e4",
}


def list_presets() -> list[dict[str, str]]:
    out = []
    for key, rtttl in RINGTONES.items():
        name = rtttl.split(":", 1)[0]
        out.append({"id": key, "name": name, "rtttl": rtttl, "chars": str(len(rtttl))})
    return out


def get_preset(preset_id: str) -> str | None:
    return RINGTONES.get((preset_id or "").strip().lower())


# Short names for radio DMs (not in menu — say "tone coin" etc.)
RINGTONE_ALIASES: dict[str, str] = {
    "coin": "mario_coin",
    "mario": "mario_short",
    "powerup": "mario_powerup",
    "1up": "mario_1up",
    "zelda": "zelda_item",
    "nokia": "nokia_short",
    "beep": "double_beep",
    "pager": "pager",
    "ping": "ping",
    "metdet": "metdet_ping",
    "alert": "short_alert",
    "mesh": "mesh_default",
    "chime": "soft_chime",
    "urgent": "urgent",
}

RINGTONE_RADIO_HINT = (
    "Paste in Meshtastic app → Settings → External Notification → Ringtone. "
    "Set Nag Timeout to 0."
)


def resolve_ringtone(token: str) -> str | None:
    key = (token or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not key:
        return None
    if key in RINGTONES:
        return RINGTONES[key]
    alias = RINGTONE_ALIASES.get(key)
    if alias:
        return RINGTONES.get(alias)
    return None


def radio_ringtone_help() -> str:
    keys = "coin ping pager metdet nokia beep alert mesh chime urgent"
    return (
        f"RTTTL buzzer (T1000-E etc): tone <name>. Try: {keys}. "
        f"tone list = all names. {RINGTONE_RADIO_HINT}"
    )


def validate_rtttl(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        raise ValueError("empty ringtone")
    if len(cleaned) > 230:
        raise ValueError(f"too long ({len(cleaned)} chars; max 230)")
    if ":" not in cleaned:
        raise ValueError("RTTTL needs Name:d=…,o=…,b=…:notes")
    return cleaned
