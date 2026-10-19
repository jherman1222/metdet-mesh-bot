from __future__ import annotations

# Plain-English menus. Prefer short sentences over abbreviations.

MENU_INDEX = (
    "METDET — reply with a number: "
    "1 How to chat · "
    "2 Weather (ask anytime) · "
    "3 Alert texts setup · "
    "4 Set your name · "
    "5 Join MetalDetect · "
    "6 Other tips"
)

# Full welcome — only when user says intro / about / welcome (not auto-blasted).
INTRO = (
    "Welcome to METDET. I'm a helper on this mesh (not a public spam channel). "
    "I run on a computer with internet, so weather and search come through me, "
    "then back over LoRa to you. Say menu for the list. "
    "wx watch = on-demand weather flags. Menu 3 = optional sunrise and wind/rain texts. "
    "Menu 5 = private MetalDetect invite. Ask questions; chords then a song. "
    "DM me directly — I only reply to you."
)

# One-liner after name is set (keeps onboarding short).
WELCOME_SHORT = (
    "You're set. Say menu for options, or intro for the longer welcome."
)

# Combined sun + wind/rain opt-in (multi-step survey in the bot).
ALERTS_PLACE = (
    "ALERT TEXTS — these are optional DMs, not the same as wx watch. "
    "Where should they cover? "
    "On playa, pick 2 (BRC). "
    "1 Reno · 2 Black Rock City · 3 both · 4 turn ALL alert texts OFF. "
    "Or cancel."
)

ALERTS_KINDS = (
    "For {place}, what do you want? "
        "1 sunrise/sunset only (~15 min before) · "
    "2 wind/rain warnings only (when it looks genuinely bad) · "
    "3 both · "
    "4 neither / off. "
    "Or cancel."
)

# Sent when we ask someone to confirm they still want alert DMs.
ALERTS_KEEP = (
    "You're opted in to METDET sun/wind texts. "
    "Reply KEEP to stay on, OFF to stop, or SETUP to pick again."
)

MENUS: dict[str, str] = {
    "1": (
        "HOW TO CHAT: Type any question and send it. "
        "To continue the same conversation, start with .r then your follow-up. "
        "If a long answer cuts off, reply more for the next part. "
        "Say menu to come back here."
    ),
    "2": (
        "WEATHER (ask anytime — not a subscription): "
        "wx watch = dated flags (heat/wind/rain + weak solar/cloudy charge loss) "
        "with SERIOUS / HEADS-UP / FYI. "
        "wx = full numbers now. "
        "wx wind = live + next hours (g=gusts). "
        "wx rain = chance + inches next day. "
        "wx week = 7-day. "
        "Default place is brc. "
        "For automatic texts, say 3 or config. "
        "Say menu to go back."
    ),
    "3": ALERTS_PLACE,
    "4": (
        "SET YOUR NAME: Required before other features. "
        "Reply with just the name you want me to call you "
        "(example: Alex). "
        "That stays even if your radio display name changes. "
        "Or say cancel."
    ),
    "5": (
        "JOIN METALDETECT: I'll DM the private channel invite link now "
        "(open it in the Meshtastic app). "
        "You can also say join anytime."
    ),
    "6": (
        "OTHER TIPS: chords then a song name for guitar chords. "
        "invite then someone's radio name to send them the unlock tip. "
        "whoami shows what I have saved for you. "
        "Say intro or about for the longer welcome (optional). "
        "alerts off, config, or stop turns off sun + wind/rain texts. "
        "tone coin = RTTTL buzzer string for T1000-E (not in menu list). "
        "Say menu anytime."
    ),
}

for _alias, _key in (
    ("chat", "1"),
    ("weather", "2"),
    ("wx", "2"),
    ("alerts", "3"),
    ("alert", "3"),
    ("warn", "3"),
    ("warnings", "3"),
    ("sun", "3"),
    ("sunrise", "3"),
    ("sunset", "3"),
    ("texts", "3"),
    ("notify", "3"),
    ("you", "4"),
    ("name", "4"),
    ("setup", "4"),
    ("account", "4"),
    ("channel", "5"),
    ("channels", "5"),
    ("join", "5"),
    ("metaldetect", "5"),
    ("metdet", "5"),
    ("tips", "6"),
    ("intro", "6"),
    ("about", "6"),
):
    MENUS[_alias] = MENUS[_key]

PROMPT_MENUS = {
    "3": "alerts",
    "alerts": "alerts",
    "alert": "alerts",
    "warn": "alerts",
    "warnings": "alerts",
    "sun": "alerts",
    "sunrise": "alerts",
    "sunset": "alerts",
    "texts": "alerts",
    "notify": "alerts",
    "4": "name",
    "you": "name",
    "name": "name",
    "setup": "name",
    "account": "name",
    "5": "channel",  # handled specially — sends invite, no confirm prompt
    "channel": "channel",
    "channels": "channel",
    "join": "channel",
    "metaldetect": "channel",
    "metdet": "channel",
}


def resolve_menu(token: str) -> str | None:
    t = token.strip().lower()
    if not t:
        return None
    if t in MENUS and MENUS[t]:
        return MENUS[t]
    first = t.split()[0]
    if first in MENUS and MENUS[first]:
        return MENUS[first]
    return None


def prompt_kind_for_topic(token: str) -> str | None:
    t = token.strip().lower().split()[0] if token.strip() else ""
    return PROMPT_MENUS.get(t)
