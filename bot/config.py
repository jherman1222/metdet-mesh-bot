from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "metdet.db"


def _csv_ids(value: str) -> set[str]:
    return {x.strip() for x in value.split(",") if x.strip()}


@dataclass(frozen=True)
class Config:
    meshtastic_mode: str  # "tcp" | "usb"
    meshtastic_host: str
    meshtastic_port: int
    meshtastic_serial: str
    gemini_api_key: str
    gemini_model: str
    unlock_passphrase: str
    admin_node_ids: set[str]
    admin_http_host: str
    admin_http_port: int
    max_chunk_chars: int
    max_chunks: int
    chunk_delay_sec: float
    rate_limit_seconds: float
    system_prompt: str
    default_wx_place: str


def config_needs_setup() -> bool:
    """True when first-run wizard should open (no API key yet)."""
    return not os.getenv("GEMINI_API_KEY", "").strip()


def load_config() -> Config:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY missing — run: python -m bot.setup")

    mode = os.getenv("MESHTASTIC_MODE", "tcp").strip().lower()
    serial = os.getenv("MESHTASTIC_SERIAL", "").strip()
    if serial and mode != "usb":
        # Serial path set ⇒ prefer USB even if MODE omitted
        mode = "usb"
    if mode not in ("tcp", "usb"):
        mode = "tcp"
    if mode == "usb" and not serial:
        raise SystemExit(
            "MESHTASTIC_MODE=usb but MESHTASTIC_SERIAL is empty — run: python -m bot.setup"
        )

    return Config(
        meshtastic_mode=mode,
        meshtastic_host=os.getenv("MESHTASTIC_HOST", "127.0.0.1").strip(),
        meshtastic_port=int(os.getenv("MESHTASTIC_PORT", "4403")),
        meshtastic_serial=serial,
        gemini_api_key=key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        unlock_passphrase=os.getenv("UNLOCK_PASSPHRASE", "metdet").strip().lower(),
        admin_node_ids=_csv_ids(os.getenv("ADMIN_NODE_IDS", "")),
        admin_http_host=os.getenv("ADMIN_HTTP_HOST", "127.0.0.1").strip(),
        admin_http_port=int(os.getenv("ADMIN_HTTP_PORT", "8787")),
        max_chunk_chars=int(os.getenv("MAX_CHUNK_CHARS", "200")),
        max_chunks=int(os.getenv("MAX_CHUNKS", "3")),
        # LoRa often delivers multi-packet DMs out of order if sent too quickly
        chunk_delay_sec=float(os.getenv("CHUNK_DELAY_SEC", "15")),
        rate_limit_seconds=float(os.getenv("RATE_LIMIT_SECONDS", "20")),
        system_prompt=os.getenv(
            "SYSTEM_PROMPT",
            "You are METDET, a mesh radio helper. Keep answers extremely short.",
        ).strip(),
        default_wx_place=_default_wx_place(),
    )


def _default_wx_place() -> str:
    place = os.getenv("DEFAULT_WX_PLACE", "brc").strip().lower()
    if place not in ("reno", "brc", "both"):
        place = "brc"
    return place
