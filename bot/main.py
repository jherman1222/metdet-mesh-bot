from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any

from meshtastic.tcp_interface import TCPInterface
from pubsub import pub

from bot.admin import create_admin_app, friendly_hw_model
from bot.chunking import chunk_text
from bot.config import DB_PATH, Config, config_needs_setup, load_config
from bot.db import Store
from bot.gemini_client import DEFAULT_SYSTEM, GeminiService
from bot.version import RELEASE_HEADLINE, VERSION, user_update_message
from bot.menu import (
    ALERTS_KEEP,
    ALERTS_KINDS,
    ALERTS_PLACE,
    INTRO,
    MENU_INDEX,
    WELCOME_SHORT,
    prompt_kind_for_topic,
    resolve_menu,
)

log = logging.getLogger("metdet")

# Default wx / on-demand forecast place (override with DEFAULT_WX_PLACE in .env).
DEFAULT_PLACE = "brc"

THREAD_IDLE_SEC = 20 * 60
MAX_HISTORY_TURNS = 6
PROMPT_IDLE_SEC = 5 * 60


def _strip_reply_prefix(text: str) -> tuple[bool, str]:
    """Return (is_continue, prompt). Supports .r / /r."""
    raw = text.strip()
    low = raw.lower()
    if low.startswith(".r ") or low.startswith("/r "):
        return True, raw[3:].strip()
    if low in (".r", "/r"):
        return True, ""
    return False, raw


def _is_chords(text: str) -> bool:
    from bot.gemini_client import is_chords_request

    return is_chords_request(text)


class MetdetBot:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = Store(DB_PATH)
        # Prefer rich 2026 prompt unless .env already has YEAR RULES
        system = cfg.system_prompt
        if "YEAR RULES" not in system:
            system = DEFAULT_SYSTEM
        self.gemini = GeminiService(cfg.gemini_api_key, cfg.gemini_model, system)
        self.interface: Any = None
        self.my_node_id: str | None = None
        self.started_at = time.time()
        self.connected = False
        self.last_inbound = "—"
        self._passphrase = (
            self.store.get_setting("unlock_passphrase", cfg.unlock_passphrase) or cfg.unlock_passphrase
        ).lower()
        self._pending_chunks: dict[str, list[str]] = {}
        self._last_ask: dict[str, float] = {}
        self._send_lock = threading.Lock()
        # node_id -> {"updated": ts, "turns": [{"role","text"}, ...]}
        self._threads: dict[str, dict[str, Any]] = {}
        self._sun_sent: set[str] = set()
        self._alert_sent: set[str] = set()
        self._stop = threading.Event()
        self._last_alert_poll = self.store.get_alert_poll_ts()
        self._dedupe_day = time.strftime("%Y-%m-%d")
        self._sun_sent = self.store.get_dedupe_keys("sun", self._dedupe_day)
        self._alert_sent = self.store.get_dedupe_keys("alert", self._dedupe_day)
        self._prompts: dict[str, dict[str, Any]] = {}
        self._traceroute_lock = threading.Lock()
        self._traceroute_busy = False
        self._traceroute_log: list[dict[str, Any]] = []  # newest first
        self._radio_log: deque[dict[str, Any]] = deque(maxlen=200)
        self._radio_log_lock = threading.Lock()
        self._default_place = cfg.default_wx_place

        for admin_id in cfg.admin_node_ids:
            self.store.unlock_user(admin_id, short_name=None, is_admin=True)

    def get_passphrase(self) -> str:
        return self._passphrase

    def set_passphrase(self, value: str) -> None:
        self._passphrase = value.strip().lower()
        self.store.set_setting("unlock_passphrase", self._passphrase)
        log.info("Passphrase updated")

    def status(self) -> dict[str, Any]:
        radio_long = ""
        radio_short = ""
        try:
            if self.interface:
                info = self.interface.getMyNodeInfo() or {}
                user = info.get("user") or {}
                radio_long = (user.get("longName") or "").strip()
                radio_short = (user.get("shortName") or "").strip()
        except Exception:  # noqa: BLE001
            pass
        radio_name = radio_long or radio_short or self.my_node_id or "—"
        return {
            "connected": self.connected,
            "uptime_sec": int(time.time() - self.started_at),
            "last_inbound": self.last_inbound,
            "my_node_id": self.my_node_id,
            "radio_name": radio_name,
            "radio_long": radio_long,
            "radio_short": radio_short,
            "passphrase": self._passphrase,
            "modem_preset": self.modem_preset_label(),
            "version": VERSION,
            "release_headline": RELEASE_HEADLINE,
        }

    def mesh_nodes(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.interface:
            return out
        nodes = self.interface.nodes or {}
        for node_id, node in nodes.items():
            user = node.get("user") or {}
            metrics = node.get("deviceMetrics") or {}
            pos = node.get("position") or {}
            lat = pos.get("latitude")
            lon = pos.get("longitude")
            if lat is None and pos.get("latitudeI") is not None:
                lat = pos["latitudeI"] / 1e7
            if lon is None and pos.get("longitudeI") is not None:
                lon = pos["longitudeI"] / 1e7
            hw = user.get("hwModel")
            out.append(
                {
                    "node_id": node_id,
                    "short_name": user.get("shortName"),
                    "long_name": user.get("longName"),
                    "hw_model": hw,
                    "device": friendly_hw_model(hw),
                    "role": user.get("role"),
                    "macaddr": user.get("macaddr"),
                    "public_key": user.get("publicKey"),
                    "snr": node.get("snr"),
                    "hops_away": node.get("hopsAway"),
                    "last_heard": node.get("lastHeard"),
                    "channel": node.get("channel"),
                    "battery": metrics.get("batteryLevel"),
                    "voltage": metrics.get("voltage"),
                    "uptime_sec": metrics.get("uptimeSeconds"),
                    "channel_util": metrics.get("channelUtilization"),
                    "air_util_tx": metrics.get("airUtilTx"),
                    "latitude": lat,
                    "longitude": lon,
                    "altitude": pos.get("altitude"),
                    "is_favorite_mesh": bool(node.get("isFavorite")),
                }
            )
        return out

    def weather_brief(self, place: str = "both") -> str:
        from bot.weather import weather_brief as wx, parse_place

        p = parse_place(place) or "both"
        return wx(p)

    def _identity_allowed(self, lower: str, text: str) -> bool:
        """Commands allowed before the user has set iam name."""
        if lower in (
            "help",
            "?",
            "menu",
            "whoami",
            "name",
            "setup",
            "set name",
            "set my name",
            "4",
            "cancel",
            "intro",
            "about",
            "welcome",
            "version",
            "ver",
            "v",
        ):
            return True
        if lower.startswith(("help ", "menu ", "iam ", "i am ")):
            return True
        return False

    def _send_weather(self, node_id: str, *, mode: str, place: str, cmd: str) -> None:
        from bot.weather import weather_brief, wind_hourly, rain_hourly, weather_week, weather_insight

        prefix = "WX"
        detail: str | None = None
        if mode == "wind":
            brief, detail = wind_hourly(place, hours=18)  # type: ignore[arg-type]
            prefix = "WIND"
            tip = " (.r digs in)"
        elif mode == "rain":
            brief, detail = rain_hourly(place, hours=24)  # type: ignore[arg-type]
            prefix = "RAIN"
            tip = " (.r digs in)"
        elif mode == "week":
            brief, detail = weather_week(place)  # type: ignore[arg-type]
            prefix = "WEEK"
            tip = " (.r digs in)"
        elif mode == "watch":
            brief, detail = weather_insight(place)  # type: ignore[arg-type]
            prefix = "WATCH"
            tip = ""
        else:
            brief = weather_brief(place)  # type: ignore[arg-type]
            tip = ""

        text = f"{prefix} {brief}{tip}"
        chunks = chunk_text(text, self.cfg.max_chunk_chars, min(4, self.cfg.max_chunks))
        # Send the full set — (1/2) without part 2 was confusing
        self._pending_chunks[node_id] = []
        for i, c in enumerate(chunks):
            if i:
                time.sleep(self.cfg.chunk_delay_sec)
            self._send(node_id, c)

        if detail:
            self._remember(
                node_id,
                f"{cmd} (forecast data for follow-ups)",
                detail + "\nAnswer follow-ups from this forecast briefly for radio.",
            )
        if (cmd or "").lower().startswith("push"):
            self.store.log_push(
                action=f"wx {mode}",
                target=node_id,
                node_id=node_id,
                detail=f"{cmd}: {brief}",
            )
        else:
            self.store.log_query(
                node_id=node_id,
                short_name=None,
                prompt=cmd,
                reply=brief,
                mode=mode,
                latency_ms=0,
                kind="weather",
            )

    def push_to_node(
        self,
        node_id: str,
        action: str,
        note: str = "",
        *,
        wx_mode: str | None = None,
        wx_place: str | None = None,
    ) -> str:
        """Admin/mesh push actions. Returns status string for UI."""
        node_id = node_id.strip()
        if not node_id:
            return "Missing node id"
        action = action.strip().lower()
        label = node_id
        lab = self.store.get_label(node_id)
        mesh_short = None
        for n in self.mesh_nodes():
            if n.get("node_id") == node_id:
                mesh_short = n.get("short_name")
                break
        label = Store.display_name(lab, mesh_short=mesh_short, node_id=node_id)

        if action == "invite":
            msg = (
                f"METDET invite: DM me '{self._passphrase}' to unlock. "
                "I'm a mesh helper with internet via a computer (sat on playa) — "
                "weather, search, menu. After unlock, set your name, then say menu."
            )
            self._send(node_id, msg)
            self.store.log_push(action="invite", target=label, node_id=node_id, detail=msg)
            return f"Invite sent to {label}"

        if action == "menu":
            self._send(node_id, MENU_INDEX)
            self.store.log_push(action="menu", target=label, node_id=node_id, detail="menu")
            return f"Menu sent to {label}"

        if action == "channel":
            return self.send_channel_invite(node_id)

        if action == "traceroute":
            return self.start_traceroute(node_id)

        if action == "weather":
            mode = (wx_mode or "watch").strip().lower()
            if mode not in ("now", "wind", "rain", "week", "watch"):
                mode = "watch"
            place = (wx_place or self._default_place).strip().lower()
            if place not in ("reno", "brc", "both"):
                place = self._default_place
            self._send_weather(
                node_id,
                mode=mode,
                place=place,
                cmd=f"push {mode} {place}",
            )
            return f"WX {mode}/{place} pushed to {label}"

        if action == "note":
            note = (note or "").strip()
            if not note:
                return "Empty note"
            self._send(node_id, note[:200])
            return f"Note sent to {label}"

        if action == "alerts_off":
            self._alerts_off(node_id, notify=True)
            self.store.log_push(action="alerts off", target=label, node_id=node_id)
            return f"Alert texts OFF for {label} (DM sent)"

        if action == "alerts_setup":
            self._start_prompt(node_id, "alerts")
            self._send(node_id, ALERTS_PLACE)
            self.store.log_push(action="alerts setup", target=label, node_id=node_id)
            return f"Alert setup started for {label}"

        if action == "alerts_recheck":
            self._nudge_alerts(node_id)
            self.store.log_push(action="alerts recheck", target=label, node_id=node_id)
            return f"Alert recheck sent to {label}"

        return f"Unknown action {action}"

    def push_weather_unlocked(
        self,
        mode: str = "watch",
        place: str | None = None,
    ) -> str:
        users = self.store.list_users()
        if not users:
            return "No unlocked users"
        mode = (mode or "watch").strip().lower()
        if mode not in ("now", "wind", "rain", "week", "watch"):
            mode = "watch"
        place = (place or self._default_place).strip().lower()
        if place not in ("reno", "brc", "both"):
            place = self._default_place
        sent = 0
        for u in users:
            nid = u.get("node_id")
            if not nid or nid == self.my_node_id:
                continue
            self._send_weather(
                nid,
                mode=mode,
                place=place,
                cmd=f"push-all {mode} {place}",
            )
            sent += 1
            time.sleep(3)
        self.store.log_push(
            action=f"wx {mode}",
            target=f"{sent} unlocked",
            detail=f"{mode}/{place} → all unlocked",
        )
        return f"WX {mode}/{place} pushed to {sent} unlocked user(s)"

    def _alerts_off(self, node_id: str, *, notify: bool = True) -> None:
        self.store.set_subscription(node_id, "sun", enabled=False)
        self.store.set_subscription(node_id, "alert", enabled=False)
        self._clear_prompt(node_id)
        if notify:
            self._send(
                node_id,
                "METDET sun/wind texts are OFF. Reply SETUP if you want them back, or ignore.",
            )

    def _nudge_alerts(self, node_id: str) -> None:
        self._start_prompt(node_id, "alerts_keep")
        self._send(node_id, ALERTS_KEEP)

    def nudge_alerts_subscribers(self) -> str:
        ids = {s["node_id"] for s in self.store.list_subscriptions("sun", enabled_only=True)}
        ids |= {s["node_id"] for s in self.store.list_subscriptions("alert", enabled_only=True)}
        if not ids:
            return "Nobody is opted into alert texts"
        n = 0
        for nid in sorted(ids):
            self._nudge_alerts(nid)
            n += 1
            time.sleep(max(2.0, self.cfg.chunk_delay_sec / 3))
        self.store.log_push(
            action="alerts recheck",
            target=f"{n} opted-in",
            detail="KEEP/OFF/SETUP to all alert subscribers",
        )
        return f"Recheck DM sent to {n} opted-in user{'s' if n != 1 else ''}"

    def alerts_off_subscribers(self, *, notify: bool = True) -> str:
        ids = {s["node_id"] for s in self.store.list_subscriptions("sun", enabled_only=True)}
        ids |= {s["node_id"] for s in self.store.list_subscriptions("alert", enabled_only=True)}
        if not ids:
            return "Nobody is opted into alert texts"
        n = 0
        for nid in sorted(ids):
            self._alerts_off(nid, notify=notify)
            n += 1
            time.sleep(max(2.0, self.cfg.chunk_delay_sec / 3))
        self.store.log_push(
            action="alerts off",
            target=f"{n} opted-in",
            detail="unsubscribed all alert subscribers",
        )
        return f"Turned OFF alert texts for {n} user{'s' if n != 1 else ''}"

    def apply_playa_hotfix(
        self, *, notify: bool = False, apply_alerts: bool = True
    ) -> str:
        """Optional BRC sun+wind ON for interacted users; optional one DM per version."""
        ids = self.store.list_interacted_user_ids()
        if apply_alerts:
            for nid in ids:
                self.store.apply_playa_alert_defaults(nid)
        dmed = 0
        skipped = 0
        if notify:
            msg = user_update_message()
            already = self.store.playa_hotfix_dm_sent()
            to_mark: list[str] = []
            for nid in ids:
                if nid in already:
                    skipped += 1
                    continue
                self._send(nid, msg)
                to_mark.append(nid)
                dmed += 1
                time.sleep(2)
            if to_mark:
                self.store.mark_playa_hotfix_dm(to_mark)
        self.store.log_push(
            action="playa hotfix" if apply_alerts else "announce update",
            target=f"{len(ids)} interacted",
            detail=(
                f"v{VERSION}: {RELEASE_HEADLINE}; notify={notify}; "
                f"alerts={apply_alerts}; dmed={dmed}; skipped={skipped}"
            ),
        )
        if notify:
            return (
                f"{'Hotfix' if apply_alerts else 'Announce'} for {len(ids)} user(s); "
                f"DMed {dmed}" + (f" ({skipped} already got this v)" if skipped else "")
            )
        return f"Playa hotfix applied for {len(ids)} user(s) (no DM)"

    def announce_update(self) -> str:
        """DM the current version line to interacted users. Does not change alerts."""
        return self.apply_playa_hotfix(notify=True, apply_alerts=False)

    def _refresh_dedupe_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today == self._dedupe_day:
            return
        self._dedupe_day = today
        self._sun_sent = self.store.get_dedupe_keys("sun", today)
        self._alert_sent = self.store.get_dedupe_keys("alert", today)

    def _mark_sun_sent(self, key: str) -> None:
        self._refresh_dedupe_day()
        self._sun_sent.add(key)
        self.store.save_dedupe_keys("sun", self._dedupe_day, self._sun_sent)

    def _mark_alert_sent(self, key: str) -> None:
        self._refresh_dedupe_day()
        self._alert_sent.add(key)
        self.store.save_dedupe_keys("alert", self._dedupe_day, self._alert_sent)

    def revoke_flagged_users(self, node_ids: list[str]) -> str:
        cleaned = 0
        for nid in node_ids:
            nid = (nid or "").strip()
            if not nid:
                continue
            self.store.revoke_user_full(nid)
            cleaned += 1
        return f"Removed {cleaned} stale/duplicate unlock(s)"

    def resolve_node(self, token: str) -> tuple[str | None, str | None]:
        """Resolve alias, short/long name, or !nodeid -> (node_id, display_name)."""
        token = token.strip()
        if not token:
            return None, None
        low = token.lower()
        # Admin alias / user iam first (stable even if mesh name changed)
        nid, dname = self.store.resolve_label_token(token)
        if nid:
            return nid, dname
        nodes = self.mesh_nodes()
        labels = self.store.labels_map()
        # Exact node id
        for n in nodes:
            if (n.get("node_id") or "").lower() == low:
                lab = labels.get(n["node_id"])
                return n["node_id"], Store.display_name(
                    lab, mesh_short=n.get("short_name"), node_id=n["node_id"]
                )
        # Exact short name
        for n in nodes:
            if (n.get("short_name") or "").lower() == low:
                lab = labels.get(n["node_id"])
                return n["node_id"], Store.display_name(
                    lab, mesh_short=n.get("short_name"), node_id=n["node_id"]
                )
        # Partial long/short
        matches = [
            n
            for n in nodes
            if low in (n.get("short_name") or "").lower()
            or low in (n.get("long_name") or "").lower()
        ]
        if len(matches) == 1:
            n = matches[0]
            lab = labels.get(n["node_id"])
            return n["node_id"], Store.display_name(
                lab, mesh_short=n.get("short_name"), node_id=n["node_id"]
            )
        return None, None

    def start(self) -> None:
        pub.subscribe(self._on_receive, "meshtastic.receive")
        pub.subscribe(self._on_connection, "meshtastic.connection.established")
        pub.subscribe(self._on_disconnected, "meshtastic.connection.lost")

        self._connect_radio()
        threading.Thread(target=self._sun_loop, name="metdet-sun", daemon=True).start()
        threading.Thread(target=self._alert_loop, name="metdet-alert", daemon=True).start()
        threading.Thread(target=self._wx_cache_loop, name="metdet-wx", daemon=True).start()
        threading.Thread(
            target=self._reconnect_loop, name="metdet-reconnect", daemon=True
        ).start()

    def _connect_radio(self) -> bool:
        """Open TCP or USB serial to the gateway radio. Returns True on success."""
        if self.cfg.meshtastic_mode == "usb":
            log.info("Connecting to Meshtastic USB %s", self.cfg.meshtastic_serial)
        else:
            log.info(
                "Connecting to Meshtastic TCP %s:%s",
                self.cfg.meshtastic_host,
                self.cfg.meshtastic_port,
            )
        try:
            old = self.interface
            self.interface = None
            if old is not None:
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.5)
            if self.cfg.meshtastic_mode == "usb":
                from meshtastic.serial_interface import SerialInterface

                self.interface = SerialInterface(devPath=self.cfg.meshtastic_serial)
            else:
                self.interface = TCPInterface(
                    hostname=self.cfg.meshtastic_host,
                    portNumber=self.cfg.meshtastic_port,
                )
            time.sleep(1.5)
            try:
                node = getattr(self.interface, "localNode", None)
                if node and hasattr(node, "waitForConfig"):
                    node.waitForConfig()
            except Exception:  # noqa: BLE001
                pass
            if self.interface.myInfo:
                info = self.interface.getMyNodeInfo()
                if info and info.get("user"):
                    self.my_node_id = info["user"].get("id")
            self.connected = True
            log.info("Connected as %s", self.my_node_id or "?")
            return True
        except Exception as exc:  # noqa: BLE001
            self.connected = False
            self.interface = None
            log.warning("Radio connect failed: %s", exc)
            return False

    def _reconnect_loop(self) -> None:
        """If Heltec drops TCP (MQTT reboot, WiFi blip), keep retrying."""
        while not self._stop.wait(15):
            if self.connected and self.interface is not None:
                continue
            log.info("Radio down — attempting reconnect…")
            if self._connect_radio():
                log.info("Radio reconnected")
            else:
                log.warning("Reconnect failed; will retry")

    def _mqtt_module(self):
        """Return (localNode, mqtt_module_config) using current meshtastic API."""
        if not self.interface or not self.interface.localNode:
            raise RuntimeError("Radio not connected")
        node = self.interface.localNode
        # Prefer camelCase API (meshtastic 2.x); fall back if an older wheel appears.
        mod = getattr(node, "moduleConfig", None) or getattr(node, "module_config", None)
        if mod is None:
            raise RuntimeError("This meshtastic library has no moduleConfig on Node")
        # Ask the device for MQTT settings if we haven't received them yet
        try:
            if not mod.HasField("mqtt"):
                field = mod.DESCRIPTOR.fields_by_name.get("mqtt")
                if field is not None:
                    node.requestConfig(field)
                    time.sleep(1.2)
        except Exception:  # noqa: BLE001
            # HasField can throw on empty protos; still try request
            try:
                field = mod.DESCRIPTOR.fields_by_name.get("mqtt")
                if field is not None:
                    node.requestConfig(field)
                    time.sleep(1.2)
            except Exception:  # noqa: BLE001
                pass
        return node, mod.mqtt

    def mqtt_status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"enabled": False, "address": "", "root": ""}
        try:
            _, mqtt = self._mqtt_module()
            out["enabled"] = bool(mqtt.enabled)
            out["address"] = mqtt.address or "mqtt.meshtastic.org"
            out["root"] = mqtt.root or ""
            out["encryption_enabled"] = bool(mqtt.encryption_enabled)
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)
        return out

    def set_mqtt_enabled(self, enabled: bool) -> str:
        """Toggle MQTT on the gateway radio. May reboot the node briefly."""
        if not self.interface:
            return "Radio not connected"
        try:
            node, mqtt = self._mqtt_module()
            mqtt.enabled = bool(enabled)
            if enabled and not mqtt.address:
                mqtt.address = "mqtt.meshtastic.org"
            node.writeConfig("mqtt")
            state = "ON" if enabled else "OFF"
            log.info("MQTT set to %s", state)
            return (
                f"MQTT {state} on gateway. Node may reboot briefly. "
                "Still need channel Uplink in the phone app on whatever public primary you use "
                "(Downlink optional — leave OFF to avoid flooding local LoRa)."
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("MQTT toggle failed")
            return (
                f"MQTT toggle failed: {exc}. "
                "You can also enable MQTT in the Meshtastic phone app → "
                "Settings → MQTT on the Heltec."
            )

    def _private_channel_names(self) -> list[str]:
        """Names of non-default private secondaries on the gateway."""
        names: list[str] = []
        for c in self.list_radio_channels():
            psk = (c.get("psk") or "").lower()
            name = (c.get("name") or "").strip()
            if "private" in psk and name and not name.startswith("("):
                names.append(name)
        return names

    def metaldetect_invite_url(self) -> str:
        """
        Share URL that ADDS only MetalDetect (secondary), without replacing the
        user's primary channel or LoRa modem/hops. Uses ?add=true.
        """
        import base64

        from meshtastic.protobuf import apponly_pb2, channel_pb2

        if not self.interface or not self.interface.localNode:
            stored = (self.store.get_setting("channel_invite_url") or "").strip()
            return stored
        node = self.interface.localNode
        settings = None
        for c in node.channels or []:
            name = (c.settings.name or "").strip()
            if c.role == channel_pb2.Channel.Role.SECONDARY and name.lower() in (
                "metaldetect",
                "metdet",
            ):
                settings = c.settings
                break
        if settings is None:
            return (self.store.get_setting("channel_invite_url") or "").strip()

        channel_set = apponly_pb2.ChannelSet()
        channel_set.settings.append(settings)
        # Intentionally omit lora_config so import doesn't rewrite modem/hops/region
        raw = channel_set.SerializeToString()
        s = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"https://meshtastic.org/e/?add=true#{s}"

    def get_channel_invite_url(self, *, include_all: bool = True) -> str:
        """
        Prefer MetalDetect add-only URL. Falls back to stored / full Heltec URL.
        """
        add_url = ""
        try:
            add_url = self.metaldetect_invite_url()
        except Exception as exc:  # noqa: BLE001
            log.warning("metaldetect_invite_url failed: %s", exc)

        if add_url.startswith("http") and "add=true" in add_url:
            return add_url

        stored = (self.store.get_setting("channel_invite_url") or "").strip()
        if stored.startswith("http"):
            # Upgrade old replace-style links to add=true when possible
            if "add=true" not in stored and "# " not in stored and "/e/#" in stored:
                stored = stored.replace("/e/#", "/e/?add=true#", 1)
            return stored

        # Last resort: full Heltec URL (includes LoRa — warn in logs)
        try:
            if self.interface and self.interface.localNode:
                live = (self.interface.localNode.getURL(includeAll=include_all) or "").strip()
                if live:
                    log.warning(
                        "Using full Heltec channel URL (includes LoRa settings). "
                        "Prefer MetalDetect on radio for add-only invites."
                    )
                    return live.replace("/e/#", "/e/?add=true#", 1)
        except Exception as exc:  # noqa: BLE001
            log.warning("getURL failed: %s", exc)
        return ""

    def send_channel_invite(self, node_id: str) -> str:
        """DM a clickable channel link. Creates MetalDetect on Heltec if missing."""
        priv = self._private_channel_names()
        stored = (self.store.get_setting("channel_invite_url") or "").strip()
        has_metal = any(n.lower() in ("metaldetect", "metdet") for n in priv)

        if not has_metal and not stored.startswith("http"):
            # Make it actually work: create private channel on gateway, then invite
            created = self.create_metdet_channel("MetalDetect")
            log.info("Auto-create MetalDetect before invite: %s", created)
            priv = self._private_channel_names()
            has_metal = any(n.lower() in ("metaldetect", "metdet") for n in priv)
            stored = (self.store.get_setting("channel_invite_url") or "").strip()
            if not has_metal and not stored.startswith("http"):
                self._send(
                    node_id,
                    "Couldn't set up MetalDetect on the gateway yet. "
                    "Ask an admin to check Settings → Create MetalDetect.",
                )
                return created

        url = self.get_channel_invite_url(include_all=True)
        if not url.startswith("http"):
            self._send(
                node_id,
                "No invite URL yet. Admin: Settings → Create MetalDetect or paste a share link.",
            )
            return "No invite URL"

        if url:
            try:
                self.set_channel_invite_url(url)
            except Exception:  # noqa: BLE001
                pass

        self._send(
            node_id,
            "MetalDetect invite (add-only) — open in Meshtastic; it should ADD the channel, "
            "not replace your LoRa/hops. If asked, choose Add:",
        )
        time.sleep(max(1.5, self.cfg.chunk_delay_sec))
        # URL alone (chunked by _send if needed) — no double-chunking wrapper
        self._send(node_id, url)
        return f"MetalDetect invite link sent to {node_id} ({len(url)} chars)"

    def _request_channel_invite(self, node_id: str) -> None:
        """Menu 5 / join — send invite immediately (no yes/join confirm)."""
        self._clear_prompt(node_id)
        msg = self.send_channel_invite(node_id)
        log.info("Channel invite for %s: %s", node_id, msg)

    def send_on_channel(self, text: str, *, channel: str = "MetalDetect") -> str:
        """Broadcast a short text on a named channel (camp announce)."""
        from meshtastic import BROADCAST_ADDR

        text = " ".join((text or "").strip().split())
        if not text:
            return "Empty message"
        if not self.interface or not self.connected:
            return "Radio not connected"
        want = channel.strip().lower()
        idx = None
        label = channel
        for c in self.list_radio_channels():
            name = (c.get("name") or "").strip()
            low = name.lower()
            if want == "public" and (
                int(c.get("index") or -1) == 0 or low in ("(primary)", "primary")
            ):
                idx = int(c["index"])
                label = name or "Public"
                break
            if low == want or (want == "metaldetect" and low in ("metaldetect", "metdet")):
                idx = int(c["index"])
                label = name or channel
                break
        if idx is None:
            return f"Channel '{channel}' not found on gateway"
        parts = chunk_text(text, self.cfg.max_chunk_chars, max(1, self.cfg.max_chunks))
        try:
            log_name = (
                "MetalDetect"
                if want in ("metaldetect", "metdet")
                else ("Public" if want == "public" else label)
            )
            with self._send_lock:
                for i, part in enumerate(parts):
                    if i:
                        time.sleep(self.cfg.chunk_delay_sec)
                    self.interface.sendText(
                        part, destinationId=BROADCAST_ADDR, channelIndex=idx, wantAck=True
                    )
                    self.store.log_channel_message(
                        channel=log_name,
                        channel_index=idx,
                        direction="tx",
                        node_id=self.my_node_id,
                        text=part,
                        ack_status="acked",
                    )
                    self._radio_event("tx", f"[{label}] {part[:100]}", node_id=self.my_node_id)
            self.store.log_push(
                action="announce",
                target=log_name,
                channel=log_name,
                detail=text,
            )
            return f"Sent on {label} (ch {idx}, {len(parts)} packet{'s' if len(parts)!=1 else ''})"
        except Exception as exc:  # noqa: BLE001
            log.exception("channel send failed")
            return f"Send failed: {exc}"

    def announce_weather(
        self,
        *,
        mode: str = "watch",
        place: str | None = None,
        channel: str = "MetalDetect",
    ) -> str:
        from bot.weather import weather_brief, weather_insight, wind_hourly, rain_hourly, weather_week

        mode = (mode or "watch").lower()
        place = (place or self._default_place).lower()
        if place not in ("reno", "brc", "both"):
            place = self._default_place
        if mode == "wind":
            brief, _ = wind_hourly(place, hours=18)  # type: ignore[arg-type]
            text = "WIND " + brief
        elif mode == "rain":
            brief, _ = rain_hourly(place, hours=24)  # type: ignore[arg-type]
            text = "RAIN " + brief
        elif mode == "week":
            brief, _ = weather_week(place)  # type: ignore[arg-type]
            text = "WEEK " + brief
        elif mode == "now":
            text = "WX " + weather_brief(place)  # type: ignore[arg-type]
        else:
            brief, _ = weather_insight(place)  # type: ignore[arg-type]
            text = "WATCH " + brief
        text = text if len(text) <= 200 else text[:197] + "…"
        return self.send_on_channel(text, channel=channel)

    def set_ringtone(self, rtttl: str, *, node_id: str | None = None) -> str:
        """DM the RTTTL string to a radio, or set it on the gateway Heltec."""
        from bot.ringtones import validate_rtttl

        try:
            cleaned = validate_rtttl(rtttl)
        except ValueError as exc:
            return f"Bad RTTTL: {exc}"
        if not self.interface or not self.connected:
            return "Radio not connected"

        if node_id:
            try:
                self._send(node_id, cleaned)
            except Exception as exc:  # noqa: BLE001
                log.exception("ringtone DM failed")
                return f"Ringtone DM failed: {exc}"
            return f"Sent RTTTL to {node_id} ({len(cleaned)} chars)."

        # Gateway Heltec only — direct write
        try:
            result = self.interface.localNode.set_ringtone(cleaned)
            if result is None:
                return "Gateway ringtone refused (External Notification missing on Heltec?)"
            return (
                f"Ringtone set on gateway Heltec ({len(cleaned)} chars). "
                "Pocket radios: pick the node and use DM → radio instead."
            )
        except SystemExit:
            log.error("set_ringtone gateway caught SystemExit")
            return "Gateway ringtone aborted — use DM → radio instead."
        except Exception as exc:  # noqa: BLE001
            log.exception("set_ringtone gateway failed")
            return f"Gateway ringtone failed: {exc}"

    def modem_preset_label(self) -> str:
        """LoRa modem preset as the app shows it (Short Turbo, Long Fast, …)."""
        try:
            if not self.interface or not self.interface.localNode:
                return "unknown"
            from meshtastic.protobuf import config_pb2

            lora = self.interface.localNode.localConfig.lora
            raw = int(lora.modem_preset)
            region = int(getattr(lora, "region", 0) or 0)
            chan = int(getattr(lora, "channel_num", 0) or 0)
            # Protobuf default is LONG_FAST=0. If nothing else is set, config likely never loaded.
            if raw == 0 and region == 0 and chan == 0:
                return "unknown (config not loaded)"
            enum_name = config_pb2.Config.LoRaConfig.ModemPreset.Name(raw)
            nice = enum_name.replace("_", " ").title()
            extra = f" · RF ch {chan}" if chan else ""
            if not getattr(lora, "use_preset", True):
                return f"custom{extra}"
            return f"{nice}{extra}"
        except Exception:  # noqa: BLE001
            return "unknown"

    def list_radio_channels(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            if not self.interface or not self.interface.localNode:
                return out
            from meshtastic.protobuf import channel_pb2

            for c in self.interface.localNode.channels or []:
                role = int(c.role)
                name = (c.settings.name or "").strip() or (
                    "(primary)" if role == channel_pb2.Channel.Role.PRIMARY else ""
                )
                psk = bytes(c.settings.psk) if c.settings.psk else b""
                if len(psk) == 1 and psk[0] == 1:
                    psk_label = "default public key"
                elif len(psk) == 1 and psk[0] == 0:
                    psk_label = "none (open)"
                elif len(psk) == 16:
                    psk_label = "128-bit private"
                elif len(psk) == 32:
                    psk_label = "256-bit private"
                elif not psk:
                    psk_label = "—"
                else:
                    psk_label = f"{len(psk)*8}-bit"
                role_name = channel_pb2.Channel.Role.Name(role)
                if role == channel_pb2.Channel.Role.DISABLED and not name:
                    continue
                out.append(
                    {
                        "index": c.index,
                        "name": name or f"ch{c.index}",
                        "role": role_name,
                        "psk": psk_label,
                        "uplink": bool(c.settings.uplink_enabled),
                        "downlink": bool(c.settings.downlink_enabled),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("list channels failed: %s", exc)
        return out

    def create_metdet_channel(self, name: str = "MetalDetect") -> str:
        """
        Create a SECONDARY private channel with a random 256-bit key on the Heltec.
        Does not replace your primary. Saves invite URL afterward.
        """
        from meshtastic.protobuf import channel_pb2
        from meshtastic.util import fromPSK

        if not self.interface or not self.interface.localNode:
            return "Radio not connected"
        name = "".join(ch for ch in name.strip() if ch.isalnum() or ch in "-_")[:12] or "MetalDetect"
        node = self.interface.localNode
        existing = node.getChannelByName(name)
        if existing and existing.role != channel_pb2.Channel.Role.DISABLED:
            url = self.metaldetect_invite_url() or node.getURL(includeAll=True)
            if url:
                self.set_channel_invite_url(url)
            return (
                f"Channel '{name}' already exists on the radio. "
                "Invite URL refreshed (add-only). Use Channel link / menu 5 to DM it."
            )
        ch = node.getDisabledChannel()
        if not ch:
            return "No free channel slot (max 8). Delete an unused secondary in the phone app first."
        ch.role = channel_pb2.Channel.Role.SECONDARY
        ch.settings.name = name
        ch.settings.psk = fromPSK("random")  # 32 random bytes = AES-256
        ch.settings.uplink_enabled = False
        ch.settings.downlink_enabled = False
        try:
            node.writeChannel(ch.index)
            time.sleep(1.0)
            url = self.metaldetect_invite_url() or node.getURL(includeAll=True)
            if url:
                self.set_channel_invite_url(url)
            self._radio_event(
                "sys",
                f"Created private channel '{name}' (256-bit) index {ch.index}",
            )
            return (
                f"Created '{name}' as secondary channel with a random 256-bit key. "
                "Invite URL saved as add-only (won't replace their LoRa/hops). "
                "DM via Nodes → Chan or radio menu 5."
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("create channel failed")
            return f"Create channel failed: {exc}"

    def radio_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._radio_log_lock:
            items = list(self._radio_log)
        return list(reversed(items[-limit:]))

    def _radio_event(self, direction: str, summary: str, *, node_id: str | None = None) -> None:
        with self._radio_log_lock:
            self._radio_log.append(
                {
                    "ts": time.time(),
                    "dir": direction,
                    "node_id": node_id or "",
                    "summary": summary[:240],
                }
            )

    def set_channel_invite_url(self, url: str) -> None:
        url = (url or "").strip()
        if url:
            self.store.set_setting("channel_invite_url", url)
        else:
            self.store.delete_setting("channel_invite_url")

    def traceroute_log(self) -> list[dict[str, Any]]:
        return list(self._traceroute_log)

    def start_traceroute(self, node_id: str, hop_limit: int = 5) -> str:
        """Kick off traceroute in background; results land on /traceroute."""
        if not self.interface or not self.connected:
            return "Radio not connected"
        node_id = node_id.strip()
        if not node_id:
            return "Missing node id"
        with self._traceroute_lock:
            if self._traceroute_busy:
                return "Traceroute already running — wait, then check /traceroute"
            self._traceroute_busy = True

        def worker() -> None:
            try:
                summary = self._run_traceroute(node_id, hop_limit)
                entry = {
                    "ts": time.time(),
                    "target": node_id,
                    "ok": summary.get("ok", False),
                    "toward": summary.get("toward") or "",
                    "back": summary.get("back") or "",
                    "error": summary.get("error"),
                }
                self._traceroute_log.insert(0, entry)
                self._traceroute_log[:] = self._traceroute_log[:20]
                log.info("Traceroute to %s: %s", node_id, entry)
            finally:
                with self._traceroute_lock:
                    self._traceroute_busy = False

        threading.Thread(target=worker, name="metdet-traceroute", daemon=True).start()
        return (
            f"Traceroute to {node_id} started (can take a while). "
            "Refresh /traceroute in ~15–60s."
        )

    def _node_label(self, node_id_or_num: Any) -> str:
        """Pretty name for !id or node num."""
        if node_id_or_num is None:
            return "?"
        # Unknown hop marker in newer firmware
        if node_id_or_num in (0xFFFFFFFF, 4294967295, "4294967295"):
            return "Unknown"
        nid = None
        if isinstance(node_id_or_num, str):
            nid = node_id_or_num if node_id_or_num.startswith("!") else None
        if nid is None and self.interface:
            try:
                nid = self.interface._nodeNumToId(int(node_id_or_num), False)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                nid = None
        if not nid:
            try:
                nid = f"!{int(node_id_or_num):08x}"
            except Exception:  # noqa: BLE001
                nid = str(node_id_or_num)
        lab = self.store.get_label(nid) if nid.startswith("!") else None
        mesh_short = None
        for n in self.mesh_nodes():
            if n.get("node_id") == nid:
                mesh_short = n.get("short_name")
                break
        return Store.display_name(lab, mesh_short=mesh_short, node_id=nid)

    def _run_traceroute(self, dest: str, hop_limit: int) -> dict[str, Any]:
        """Blocking traceroute; returns structured summary."""
        from meshtastic import mesh_pb2, portnums_pb2
        import google.protobuf.json_format

        if not self.interface:
            return {"ok": False, "error": "no interface"}

        box: dict[str, Any] = {"packet": None, "error": None}

        def on_response(p: dict) -> None:
            decoded = p.get("decoded") or {}
            if decoded.get("portnum") == "ROUTING_APP":
                err = (decoded.get("routing") or {}).get("errorReason") or "ROUTING"
                if err != "NONE":
                    box["error"] = err
                if self.interface:
                    self.interface._acknowledgment.receivedTraceRoute = True  # noqa: SLF001
                return
            box["packet"] = p
            if self.interface:
                self.interface._acknowledgment.receivedTraceRoute = True  # noqa: SLF001

        try:
            r = mesh_pb2.RouteDiscovery()
            self.interface.sendData(
                r,
                destinationId=dest,
                portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
                wantResponse=True,
                onResponse=on_response,
                hopLimit=hop_limit,
            )
            nodes_based = (len(self.interface.nodes) - 1) if self.interface.nodes else (hop_limit + 1)
            wait_factor = max(1, min(nodes_based, hop_limit + 1))
            self.interface.waitForTraceRoute(wait_factor)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

        if box.get("error") and not box.get("packet"):
            return {"ok": False, "error": box["error"]}
        p = box.get("packet")
        if not p:
            return {"ok": False, "error": box.get("error") or "No response (timeout or unreachable)"}

        UNK_SNR = -128
        route_discovery = mesh_pb2.RouteDiscovery()
        try:
            route_discovery.ParseFromString(p["decoded"]["payload"])
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Bad payload: {exc}"}
        as_dict = google.protobuf.json_format.MessageToDict(route_discovery)

        def fmt_snr(vals: list, idx: int, valid: bool) -> str:
            if not valid or idx >= len(vals):
                return "?"
            v = vals[idx]
            if v == UNK_SNR:
                return "?"
            return f"{v / 4:g}"

        # Toward destination (same orientation as meshtastic CLI)
        toward_parts = [self._node_label(p.get("to"))]
        route = as_dict.get("route") or []
        snr_t = as_dict.get("snrTowards") or []
        snr_t_ok = len(snr_t) == len(route) + 1
        for idx, node_num in enumerate(route):
            toward_parts.append(
                f"{self._node_label(node_num)} ({fmt_snr(snr_t, idx, snr_t_ok)} dB)"
            )
        toward_parts.append(
            f"{self._node_label(p.get('from'))} ({fmt_snr(snr_t, len(route), snr_t_ok)} dB)"
        )
        toward = " → ".join(toward_parts)

        back = ""
        route_back = as_dict.get("routeBack") or []
        snr_b = as_dict.get("snrBack") or []
        back_ok = "hopStart" in p and len(snr_b) == len(route_back) + 1
        if back_ok:
            back_parts = [self._node_label(p.get("from"))]
            for idx, node_num in enumerate(route_back):
                back_parts.append(
                    f"{self._node_label(node_num)} ({fmt_snr(snr_b, idx, True)} dB)"
                )
            back_parts.append(
                f"{self._node_label(p.get('to'))} ({fmt_snr(snr_b, len(route_back), True)} dB)"
            )
            back = " → ".join(back_parts)

        return {"ok": True, "toward": toward, "back": back, "error": box.get("error")}

    def _wx_cache_loop(self) -> None:
        """Keep one BRC+Reno forecast warm so wx/alerts don't fetch per user."""
        from bot.weather import refresh_forecasts

        refresh_forecasts()
        while not self._stop.wait(600):
            try:
                refresh_forecasts()
            except Exception:
                log.exception("WX cache refresh failed")

    def _sun_loop(self) -> None:
        from bot.sun import due_sun_messages, normalize_sun_place

        while not self._stop.wait(30):
            try:
                # Sun times are computed locally (no Open-Meteo) — safe during OM outages.
                subs = self.store.list_subscriptions("sun", enabled_only=True)
                if not subs:
                    continue
                self._refresh_dedupe_day()
                by_place: dict[str, list] = {}
                for sub in subs:
                    place = normalize_sun_place(sub.get("place") or "both")
                    by_place.setdefault(place, []).append(sub)
                for place, place_subs in by_place.items():
                    due = due_sun_messages(
                        place=place, already_sent=self._sun_sent, lead_minutes=15
                    )
                    for key, msg in due:
                        self._mark_sun_sent(key)
                        for sub in place_subs:
                            self._send(sub["node_id"], msg)
                            log.info("Sun reminder to %s: %s", sub["node_id"], msg)
                            time.sleep(2)
            except Exception:
                log.exception("Sun loop error")

    def _alert_loop(self) -> None:
        """Poll forecast ~every 3 min for gust/rain alerts (opt-in DMs)."""
        from bot.weather import due_weather_alerts, normalize_alert_place

        while not self._stop.wait(60):
            try:
                subs = self.store.list_subscriptions("alert", enabled_only=True)
                if not subs:
                    continue
                now = time.time()
                if now - self._last_alert_poll < 180:
                    continue
                self._last_alert_poll = now
                self.store.set_alert_poll_ts(now)
                self._refresh_dedupe_day()
                cache: dict[str, list[tuple[str, str]]] = {}
                channel_sent: set[str] = set()
                for sub in subs:
                    place = normalize_alert_place(sub.get("place") or "both")
                    if place not in cache:
                        cache[place] = due_weather_alerts(place=place)
                    for key, msg in cache[place]:
                        send_key = f"{key}|{sub['node_id']}"
                        if send_key in self._alert_sent:
                            continue
                        self._mark_alert_sent(send_key)
                        self._send(sub["node_id"], msg)
                        log.info("WX alert to %s: %s", sub["node_id"], msg)
                        # Also one MetalDetect channel copy per alert key (less DM spam feel)
                        if key not in channel_sent and "gusts" in msg.lower():
                            channel_sent.add(key)
                            ch_key = f"{key}|MetalDetect"
                            if ch_key not in self._alert_sent:
                                self._mark_alert_sent(ch_key)
                                try:
                                    self.send_on_channel(msg, channel="MetalDetect")
                                    log.info("WX alert on MetalDetect: %s", msg)
                                except Exception:  # noqa: BLE001
                                    log.exception("MetalDetect wx alert failed")
                        time.sleep(2)
            except Exception:
                log.exception("Alert loop error")

    def _on_connection(self, interface=None, topic=None):  # noqa: ANN001
        self.connected = True
        log.info("Meshtastic connection established")

    def _on_disconnected(self, interface=None, topic=None):  # noqa: ANN001
        self.connected = False
        log.warning("Meshtastic connection lost (will auto-reconnect)")

    def _clear_prompt(self, node_id: str) -> None:
        self._prompts.pop(node_id, None)

    def _start_prompt(self, node_id: str, kind: str) -> None:
        self._prompts[node_id] = {"kind": kind, "ts": time.time()}

    def _send_intro(self, node_id: str, *, force: bool = True) -> None:
        """Longer welcome — only on intro/about/welcome (not auto-blasted)."""
        key = f"intro_sent:{node_id}"
        if not force and self.store.get_setting(key):
            return
        # _send stages chunks with delay + wantAck
        self._send(node_id, INTRO)
        self.store.set_setting(key, str(int(time.time())))

    def _open_menu_topic(self, node_id: str, topic: str) -> None:
        t = topic.strip().lower()
        # MetalDetect invite: send immediately (no yes/join dance)
        if t in ("5", "channel", "channels", "join", "metaldetect", "metdet"):
            self._request_channel_invite(node_id)
            return
        body = resolve_menu(topic)
        if not body:
            self._send(node_id, "I don't know that topic. Say menu for the list.")
            return
        self._send(node_id, body)
        kind = prompt_kind_for_topic(topic)
        if kind and kind != "channel":
            self._start_prompt(node_id, kind)

    def _whoami_text(self, node_id: str, mesh_short: str | None) -> str:
        lab = self.store.get_label(node_id)
        disp = Store.display_name(lab, mesh_short=mesh_short, node_id=node_id)
        bits = [f"I have you as {disp}."]
        if mesh_short:
            bits.append(f"Radio short name right now: {mesh_short}.")
        if lab and lab.get("user_label"):
            bits.append(f"Name you chose: {lab['user_label']}.")
        if lab and lab.get("alias"):
            bits.append(f"Admin nickname on the dashboard: {lab['alias']}.")
        bits.append("Reply 4 or name to change your name.")
        return " ".join(bits)

    def _save_name(self, node_id: str, raw: str) -> None:
        try:
            cleaned = self.store.set_user_label(node_id, raw)
        except ValueError:
            self._send(
                node_id,
                "Send a real name (example: Alex), not a radio code. Or say cancel.",
            )
            return
        self._clear_prompt(node_id)
        lab = self.store.get_label(node_id)
        if lab and lab.get("alias"):
            self._send(
                node_id,
                f"Got it — saved you as {cleaned}. "
                f"(Dashboard also has admin nickname {lab['alias']}.)",
            )
        else:
            self._send(
                node_id,
                f"Got it — I'll call you {cleaned}. "
                "If your radio name changes later, you're still you.",
            )
        # Keep onboarding short: one tip + menu. Full INTRO only on "intro".
        time.sleep(self.cfg.chunk_delay_sec)
        self._send(node_id, WELCOME_SHORT)
        time.sleep(self.cfg.chunk_delay_sec)
        self._send(node_id, MENU_INDEX)

    def _consume_prompt(self, node_id: str, text: str, mesh_short: str | None) -> bool:
        """If user is mid guided prompt, handle reply. True = consumed."""
        prompt = self._prompts.get(node_id)
        if not prompt:
            return False
        if time.time() - float(prompt.get("ts") or 0) > PROMPT_IDLE_SEC:
            self._clear_prompt(node_id)
            return False

        lower = text.lower().strip()
        kind = prompt.get("kind")

        if lower in ("cancel", "stop", "nevermind", "never mind", "menu", "help", "?"):
            self._clear_prompt(node_id)
            if lower in ("menu", "help", "?"):
                self._send(node_id, MENU_INDEX)
            else:
                self._send(node_id, "Canceled. Say menu anytime.")
            return True

        # Don't steal chat/weather/etc. mid-prompt (but numbered choices below)
        if lower.startswith("wx") or lower.startswith("chords") or lower.startswith("invite"):
            self._clear_prompt(node_id)
            return False
        if lower in ("4", "5", "6") and kind in ("sun", "alert", "alerts", "alerts_kinds", "channel"):
            self._clear_prompt(node_id)
            return False

        if kind == "name":
            if lower in ("1", "2", "3", "4", "5", "6"):
                self._clear_prompt(node_id)
                return False
            raw = text.strip()
            for prefix in ("iam ", "i am ", "i'm ", "im ", "my name is ", "name "):
                if raw.lower().startswith(prefix):
                    raw = raw[len(prefix) :].strip()
                    break
            self._save_name(node_id, raw)
            return True

        if kind == "channel":
            # Legacy confirm prompt — just send the invite
            self._request_channel_invite(node_id)
            return True

        if kind == "alerts_keep":
            if lower in ("keep", "yes", "y", "1", "ok", "okay", "stay"):
                self._clear_prompt(node_id)
                self._send(node_id, "Keeping your alert texts. Say 3 to change, or OFF to stop.")
                return True
            if lower in (
                "off",
                "stop",
                "2",
                "no",
                "n",
                "unsubscribe",
                "optout",
                "opt out",
            ):
                self._alerts_off(node_id, notify=True)
                return True
            if lower in ("setup", "3", "redo", "change", "settings", "config", "configure"):
                self._start_prompt(node_id, "alerts")
                self._send(node_id, ALERTS_PLACE)
                return True
            self._send(node_id, ALERTS_KEEP)
            prompt["ts"] = time.time()
            return True

        # Combined alerts survey — step 1: place
        if kind in ("alerts", "sun", "alert"):
            if lower in ("1", "yes", "y", "on", "reno", "ok", "okay"):
                place = "reno"
            elif lower in ("2", "brc", "playa", "burn"):
                place = "brc"
            elif lower in ("3", "both", "all"):
                place = "both"
            elif lower in ("4", "no", "n", "off", "stop", "none"):
                self.store.set_subscription(node_id, "sun", enabled=False)
                self.store.set_subscription(node_id, "alert", enabled=False)
                self._clear_prompt(node_id)
                self._send(node_id, "All alert texts OFF (sunrise and wind/rain). Say menu anytime.")
                return True
            else:
                self._send(node_id, ALERTS_PLACE)
                prompt["ts"] = time.time()
                return True
            self._prompts[node_id] = {
                "kind": "alerts_kinds",
                "place": place,
                "ts": time.time(),
            }
            self._send(node_id, ALERTS_KINDS.format(place=place))
            return True

        if kind == "alerts_kinds":
            place = prompt.get("place") or self._default_place
            if lower in ("1", "sun", "sunrise", "sunset"):
                sun_on, alert_on = True, False
            elif lower in ("2", "wind", "rain", "warn", "warning", "warnings"):
                sun_on, alert_on = False, True
            elif lower in ("3", "both", "all", "yes", "y"):
                sun_on, alert_on = True, True
            elif lower in ("4", "no", "n", "off", "none", "neither"):
                sun_on, alert_on = False, False
            else:
                self._send(node_id, ALERTS_KINDS.format(place=place))
                prompt["ts"] = time.time()
                return True
            self.store.set_subscription(node_id, "sun", enabled=sun_on, place=place)
            self.store.set_subscription(node_id, "alert", enabled=alert_on, place=place)
            self._clear_prompt(node_id)
            bits = []
            if sun_on:
                bits.append(f"sunrise/sunset ON ({place})")
            if alert_on:
                bits.append(f"wind/rain warnings ON ({place})")
            if not bits:
                bits.append("all alert texts OFF")
            self._send(
                node_id,
                "Saved: " + " · ".join(bits) + ". Say config to change, or alerts off to stop. menu for more.",
            )
            return True

        self._clear_prompt(node_id)
        return False

    def _on_receive(self, packet: dict, interface=None):  # noqa: ANN001
        try:
            self._log_heard_packet(packet)
            self._handle_packet(packet)
        except Exception:
            log.exception("Error handling packet")

    def _channel_name_for_index(self, index: int) -> str:
        for c in self.list_radio_channels():
            if int(c.get("index") or -1) == int(index):
                return (c.get("name") or f"ch{index}").strip() or f"ch{index}"
        return f"ch{index}"

    def _metaldetect_index(self) -> int | None:
        for c in self.list_radio_channels():
            name = (c.get("name") or "").lower()
            if name in ("metaldetect", "metdet") and "private" in (c.get("psk") or "").lower():
                try:
                    return int(c["index"])
                except Exception:  # noqa: BLE001
                    return None
        return None

    def _is_broadcast_packet(self, packet: dict) -> bool:
        to_id = str(packet.get("toId") or "").lower()
        if to_id in ("^all", "!ffffffff", "4294967295"):
            return True
        try:
            to_num = packet.get("to")
            if to_num is not None and int(to_num) == 0xFFFFFFFF:
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _log_heard_packet(self, packet: dict) -> None:
        decoded = packet.get("decoded") or {}
        port = decoded.get("portnum")
        from_id = str(packet.get("fromId") or "")
        ch_idx = int(packet.get("channel") or 0)
        ch_name = self._channel_name_for_index(ch_idx)
        # Don't re-log our own TX echoes
        if from_id and self.my_node_id and from_id == str(self.my_node_id):
            return
        if port in ("TEXT_MESSAGE_APP", 1):
            text = decoded.get("text") or ""
            to_id = packet.get("toId") or packet.get("to")
            broadcast = self._is_broadcast_packet(packet)
            kind = "bcast" if broadcast else "dm"
            self._radio_event(
                "rx",
                f"[{ch_name}/{kind}] → {to_id}: {text[:100]}",
                node_id=from_id,
            )
            low = ch_name.lower()
            if not broadcast:
                self.store.log_channel_message(
                    channel="DM",
                    channel_index=ch_idx,
                    direction="rx",
                    node_id=from_id or None,
                    text=text,
                )
            elif low in ("metaldetect", "metdet"):
                self.store.log_channel_message(
                    channel="MetalDetect",
                    channel_index=ch_idx,
                    direction="rx",
                    node_id=from_id or None,
                    text=text,
                )
            elif ch_idx == 0 or low in ("(primary)", "primary", "everyone", "longfast", ""):
                self.store.log_channel_message(
                    channel="Public",
                    channel_index=ch_idx,
                    direction="rx",
                    node_id=from_id or None,
                    text=text,
                )
        elif port in ("POSITION_APP", "NODEINFO_APP", "TELEMETRY_APP", "TRACEROUTE_APP"):
            self._radio_event("rx", f"[{ch_name}] {port}", node_id=from_id)

    def _node_context(self, from_id: str) -> str | None:
        if not self.interface:
            return None
        node = (self.interface.nodes or {}).get(from_id) or {}
        bits = []
        pos = node.get("position") or {}
        if pos.get("latitude") is not None and pos.get("longitude") is not None:
            bits.append(f"gps={pos.get('latitude'):.5f},{pos.get('longitude'):.5f}")
        if node.get("snr") is not None:
            bits.append(f"snr={node.get('snr')}")
        return "; ".join(bits) if bits else None

    def _handle_packet(self, packet: dict) -> None:
        decoded = packet.get("decoded") or {}
        port = decoded.get("portnum")
        if port not in ("TEXT_MESSAGE_APP", 1):
            return

        text = (decoded.get("text") or "").strip()
        if not text:
            return

        from_id = packet.get("fromId")
        if not from_id:
            return

        to_num = packet.get("to")
        my_num = None
        try:
            if self.interface and self.interface.myInfo:
                my_num = self.interface.myInfo.my_node_num
        except Exception:  # noqa: BLE001
            my_num = None

        BROADCAST = 0xFFFFFFFF
        if to_num is None or to_num == BROADCAST:
            return
        if my_num is not None and to_num != my_num:
            return

        short = None
        try:
            node = (self.interface.nodes or {}).get(from_id) if self.interface else None
            if node and node.get("user"):
                short = node["user"].get("shortName")
        except Exception:  # noqa: BLE001
            short = None

        self.last_inbound = f"{short or from_id}: {text[:80]}"
        log.info("DM from %s (%s): %s", short or "?", from_id, text)

        lower = text.lower().strip()
        is_admin = from_id in self.cfg.admin_node_ids

        if lower == self._passphrase or lower == "metdet":
            self.store.unlock_user(from_id, short, is_admin=is_admin)
            if is_admin or self.store.has_user_identity(from_id):
                self._send(from_id, "Unlocked. Say intro for how I work, or menu for the list.")
                time.sleep(self.cfg.chunk_delay_sec)
                self._send(from_id, MENU_INDEX)
            else:
                self._start_prompt(from_id, "name")
                self._send(
                    from_id,
                    "Unlocked. First set your name — reply with a name (example: Alex). "
                    "Then you'll get the menu (say intro anytime for the longer welcome).",
                )
            return

        if not self.store.is_unlocked(from_id) and not is_admin:
            self._send(from_id, f"DM {self._passphrase} to unlock")
            return

        self.store.touch_user(from_id, short)

        # Active guided prompt (name / sun / alert) — handle before other commands
        if self._consume_prompt(from_id, text, short):
            return

        # Require self-ID before features (admins exempt)
        if not is_admin and not self.store.has_user_identity(from_id):
            if self._identity_allowed(lower, text):
                pass  # fall through to whoami / iam / name / menu helpers below
            else:
                self._start_prompt(from_id, "name")
                self._send(
                    from_id,
                    "Set your name first — reply with a name (example: Alex), "
                    "or iam Alex. Required before using METDET.",
                )
                return

        if lower in ("version", "ver", "v"):
            self._send(from_id, user_update_message())
            return

        if lower in ("help", "?", "menu"):
            self._clear_prompt(from_id)
            self._send(from_id, MENU_INDEX)
            return

        if lower.startswith("help ") or lower.startswith("menu "):
            topic = text.split(None, 1)[1].strip()
            self._open_menu_topic(from_id, topic)
            return

        if lower in ("1", "2", "3", "4", "5", "6"):
            self._open_menu_topic(from_id, lower)
            return

        if lower in ("intro", "about", "welcome"):
            self._send_intro(from_id, force=True)
            return

        if lower == "whoami":
            self._send(from_id, self._whoami_text(from_id, short))
            return

        if lower.startswith("iam ") or lower.startswith("i am "):
            raw = text.split(None, 1)[1] if " " in text else ""
            if lower.startswith("i am "):
                raw = text[5:].strip()
            self._save_name(from_id, raw)
            return

        if lower in ("name", "setup", "set name", "set my name"):
            self._open_menu_topic(from_id, "4")
            return

        if lower in (
            "channel",
            "channels",
            "join",
            "join metaldetect",
            "metaldetect",
            "private channel",
        ):
            self._request_channel_invite(from_id)
            return

        # Alert texts — combined survey (sun + wind/rain). wx watch is separate (on-demand).
        if lower in ("config", "configure"):
            self._start_prompt(from_id, "alerts")
            self._send(from_id, ALERTS_PLACE)
            return
        if lower in (
            "alerts off",
            "alert off",
            "texts off",
            "notify off",
            "unsubscribe",
            "optout",
            "opt out",
            "stop texts",
            "stop alerts",
        ):
            self._alerts_off(from_id, notify=True)
            return
        if lower in ("setup", "redo"):
            self._open_menu_topic(from_id, "3")
            return
        if lower in (
            "sun",
            "sun?",
            "alert",
            "alert?",
            "alerts",
            "texts",
            "notify",
            "warnings",
        ):
            self._open_menu_topic(from_id, "3")
            return
        # Power-user shortcuts still work
        if lower == "sun on":
            self.store.set_subscription(from_id, "sun", enabled=True, place=self._default_place)
            self._send(from_id, f"Sunrise/sunset ON for {self._default_place}. Say 3 to change all alerts.")
            return
        if lower == "sun both":
            self.store.set_subscription(from_id, "sun", enabled=True, place="both")
            self._send(from_id, "Sunrise/sunset ON for Reno and BRC. Say 3 to change.")
            return
        if lower == "sun off":
            self.store.set_subscription(from_id, "sun", enabled=False)
            self._send(from_id, "Sunrise/sunset texts OFF.")
            return
        if lower.startswith("sun "):
            from bot.sun import normalize_sun_place

            place = normalize_sun_place(lower[4:].strip())
            self.store.set_subscription(from_id, "sun", enabled=True, place=place)
            self._send(from_id, f"Sunrise/sunset ON for {place}. Say 3 to change.")
            return
        if lower in ("alert on", "alerts on"):
            self.store.set_subscription(from_id, "alert", enabled=True, place=self._default_place)
            self._send(from_id, f"Wind/rain warnings ON for {self._default_place}. Say 3 to change.")
            return
        if lower in ("alert both", "alerts both"):
            self.store.set_subscription(from_id, "alert", enabled=True, place="both")
            self._send(from_id, "Wind/rain warnings ON for Reno and BRC. Say 3 to change.")
            return
        if lower.startswith("alert ") or lower.startswith("alerts "):
            from bot.weather import normalize_alert_place

            rest = text.split(None, 1)[1].strip()
            low_rest = rest.lower()
            if low_rest in ("off",):
                self.store.set_subscription(from_id, "alert", enabled=False)
                self._send(from_id, "Wind/rain warnings OFF.")
                return
            place = normalize_alert_place(rest)
            self.store.set_subscription(from_id, "alert", enabled=True, place=place)
            self._send(from_id, f"Wind/rain warnings ON for {place}. Say 3 to change.")
            return

        if lower == "more":
            chunks = self._pending_chunks.get(from_id) or []
            if not chunks:
                self._send(from_id, "No more text queued.")
                return
            nxt = chunks.pop(0)
            self._pending_chunks[from_id] = chunks
            self._send(from_id, nxt)
            return

        if lower.startswith("invite "):
            target = text.split(None, 1)[1].strip()
            node_id, tshort = self.resolve_node(target)
            if not node_id:
                self._send(
                    from_id,
                    f"Can't find '{target}'. Use short name from mesh (see admin /nodes).",
                )
                return
            msg = (
                f"METDET invite from {short or from_id}: DM me '{self._passphrase}' "
                "to unlock. Mesh helper with internet via computer/sat — "
                "set your name, then menu."
            )
            self._send(node_id, msg)
            self._send(from_id, f"Invite sent to {tshort or node_id}.")
            return

        if lower in ("tone", "ringtone", "rtttl") or lower.startswith(
            ("tone ", "ringtone ", "rtttl ")
        ):
            from bot.ringtones import RINGTONE_ALIASES, radio_ringtone_help, resolve_ringtone

            if lower in ("tone", "ringtone", "rtttl"):
                self._send(from_id, radio_ringtone_help())
                return
            rest = text.split(None, 1)[1].strip().lower()
            if rest in ("help", "?", "list"):
                names = ", ".join(sorted(RINGTONE_ALIASES.keys()))
                self._send(from_id, f"Tones: {names}. Say tone coin (etc).")
                return
            rtttl = resolve_ringtone(rest)
            if not rtttl:
                self._send(from_id, f"Unknown '{rest}'. {radio_ringtone_help()}")
                return
            self._send(from_id, rtttl)
            return

        # Weather: fixed points (not GPS)
        #   wx / weather              -> now @ DEFAULT_PLACE
        #   wx watch [place]          -> smart beware flags
        #   wx wind / wx week …
        #   wx push (admin) …
        if lower in (
            "wx",
            "weather",
            "wind",
            "winds",
            "week",
            "weekly",
            "watch",
            "tip",
            "insight",
        ) or lower.startswith("wx ") or lower.startswith("weather "):
            if lower.startswith("wx ") or lower.startswith("weather "):
                rest = text.split(None, 1)[1].strip()
                low_rest = rest.lower()
                if is_admin and low_rest in ("push", "all", "broadcast"):
                    self._send(from_id, "Pushing WATCH to unlocked users…")
                    self._send(from_id, self.push_weather_unlocked())
                    return
                if is_admin and low_rest.startswith("push "):
                    target = rest[5:].strip()
                    node_id, tshort = self.resolve_node(target)
                    if not node_id:
                        self._send(from_id, f"Can't find '{target}' for wx push.")
                        return
                    self._send(from_id, self.push_to_node(node_id, "weather"))
                    return

            from bot.weather import parse_wx_request

            parsed = parse_wx_request(lower, default_place=self._default_place)  # type: ignore[arg-type]
            if parsed is None:
                self._send(
                    from_id,
                    "WX? Try: wx | wx watch | wx wind | wx rain | wx week | wx brc",
                )
                return
            mode, place = parsed
            self._send_weather(from_id, mode=mode, place=place, cmd=lower)
            return

        continue_thread, prompt = _strip_reply_prefix(text)
        long_mode = False
        if prompt.lower().startswith("ask long "):
            long_mode = True
            prompt = prompt[9:].strip()
        elif prompt.lower().startswith("long "):
            long_mode = True
            prompt = prompt[5:].strip()

        if continue_thread and not prompt:
            self._send(from_id, "Usage: .r your follow-up (keeps last chat).")
            return

        if not prompt:
            self._send(from_id, "Ask a question after unlock.")
            return

        history: list[dict[str, str]] | None = None
        if continue_thread:
            history = self._get_history(from_id)
            if not history:
                self._send(from_id, "No active chat. Ask normally first, then .r to continue.")
                return
        else:
            # Normal message starts a fresh thread
            self._threads.pop(from_id, None)

        chords = _is_chords(prompt)
        kind = "chords" if chords else ("reply" if continue_thread else "chat")
        mode = "chords" if chords else ("long" if long_mode else "short")
        if continue_thread and not chords:
            mode = "reply"

        now = time.time()
        last = self._last_ask.get(from_id, 0)
        if now - last < self.cfg.rate_limit_seconds and not is_admin:
            self._send(from_id, "Wait a few seconds, then ask again.")
            return
        self._last_ask[from_id] = now

        extra_bits = [self._node_context(from_id), None]
        try:
            from bot.weather import forecast_context

            extra_bits[1] = forecast_context(self._default_place)  # type: ignore[arg-type]
        except Exception:
            pass
        extra = "; ".join(b for b in extra_bits if b)

        # Don't send a "…" ping — it looks like a broken answer on radio.
        t0 = time.time()
        try:
            wx_follow = bool(
                continue_thread
                and history
                and any("forecast" in (t.get("text") or "").lower() for t in history)
            )
            answer = self.gemini.answer(
                prompt,
                long_mode=long_mode,
                extra_context=extra or None,
                history=history,
                chords=chords,
                use_search=False if (chords or wx_follow) else None,
            )
            latency = int((time.time() - t0) * 1000)
            self._remember(from_id, prompt, answer)
            chunks = chunk_text(
                answer,
                max_chars=self.cfg.max_chunk_chars,
                max_chunks=self.cfg.max_chunks if long_mode else min(2, self.cfg.max_chunks),
            )
            first, rest = chunks[0], chunks[1:]
            self._pending_chunks[from_id] = rest
            self.store.log_query(
                node_id=from_id,
                short_name=short,
                prompt=prompt,
                reply=answer,
                mode=mode,
                latency_ms=latency,
                kind=kind,
            )
            self._send(from_id, first)
            if rest:
                threading.Thread(
                    target=self._send_rest,
                    args=(from_id, list(rest)),
                    daemon=True,
                ).start()
                self._pending_chunks[from_id] = []
        except Exception as exc:  # noqa: BLE001
            log.exception("Gemini failed")
            self.store.log_query(
                node_id=from_id,
                short_name=short,
                prompt=prompt,
                reply=None,
                mode=mode,
                latency_ms=int((time.time() - t0) * 1000),
                error=str(exc),
                kind=kind,
            )
            self._send(from_id, "Search failed. Try again shortly.")

    def _get_history(self, node_id: str) -> list[dict[str, str]]:
        thread = self._threads.get(node_id)
        if not thread:
            return []
        if time.time() - float(thread.get("updated") or 0) > THREAD_IDLE_SEC:
            self._threads.pop(node_id, None)
            return []
        return list(thread.get("turns") or [])

    def _remember(self, node_id: str, prompt: str, answer: str) -> None:
        thread = self._threads.get(node_id) or {"turns": []}
        turns: list[dict[str, str]] = list(thread.get("turns") or [])
        turns.append({"role": "user", "text": prompt})
        turns.append({"role": "model", "text": answer})
        # keep last N user+model pairs
        if len(turns) > MAX_HISTORY_TURNS * 2:
            turns = turns[-(MAX_HISTORY_TURNS * 2) :]
        self._threads[node_id] = {"updated": time.time(), "turns": turns}

    def _send_rest(self, node_id: str, chunks: list[str]) -> None:
        for chunk in chunks:
            time.sleep(self.cfg.chunk_delay_sec)
            self._send(node_id, chunk)

    def _send(self, node_id: str, text: str) -> None:
        if not self.interface or not self.connected:
            log.warning("Send skipped (radio down) to %s: %s", node_id, text[:80])
            return
        # LoRa text: single-line; newlines confuse some clients / look like crashes
        text = " ".join(text.replace("\r", "\n").splitlines()).strip()
        if not text:
            return
        # Never send oversized packets — that throws and can drop the TCP session
        parts = chunk_text(text, self.cfg.max_chunk_chars, max(1, self.cfg.max_chunks))
        with self._send_lock:
            for i, part in enumerate(parts):
                if i:
                    time.sleep(self.cfg.chunk_delay_sec)
                try:
                    # wantAck: library waits for mesh ACK before returning
                    self.interface.sendText(
                        part, destinationId=node_id, wantAck=True
                    )
                    log.info("Sent to %s: %s", node_id, part[:120])
                    self._radio_event("tx", part[:120], node_id=node_id)
                    self.store.log_channel_message(
                        channel="DM",
                        channel_index=0,
                        direction="tx",
                        node_id=node_id,
                        text=part,
                        ack_status="acked",
                    )
                except Exception as exc:  # noqa: BLE001
                    err = str(exc).lower()
                    self.store.log_channel_message(
                        channel="DM",
                        channel_index=0,
                        direction="tx",
                        node_id=node_id,
                        text=part,
                        ack_status="failed",
                    )
                    if "too big" in err or "payload" in err:
                        log.error("Send too big to %s (%s chars): %s", node_id, len(part), part[:80])
                        continue
                    self.connected = False
                    log.exception("Send failed to %s", node_id)
                    return


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if config_needs_setup():
        log.warning("Missing GEMINI_API_KEY — opening setup dialog")
        from bot.setup_wizard import run_web_setup

        run_web_setup(
            host=os.getenv("ADMIN_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.getenv("ADMIN_HTTP_PORT", "8787")),
            open_browser=True,
        )
        return

    cfg = load_config()
    log.info("METDET mesh bot %s — %s", VERSION, RELEASE_HEADLINE)
    bot = MetdetBot(cfg)
    bot.start()

    mesh_label = (
        f"USB {cfg.meshtastic_serial}"
        if cfg.meshtastic_mode == "usb"
        else cfg.meshtastic_host
    )
    app = create_admin_app(
        bot.store,
        get_status=bot.status,
        get_passphrase=bot.get_passphrase,
        set_passphrase=bot.set_passphrase,
        get_mesh_nodes=bot.mesh_nodes,
        push_to_node=bot.push_to_node,
        push_weather_unlocked=bot.push_weather_unlocked,
        mqtt_status=bot.mqtt_status,
        set_mqtt_enabled=bot.set_mqtt_enabled,
        get_channel_invite_url=bot.get_channel_invite_url,
        set_channel_invite_url=bot.set_channel_invite_url,
        get_traceroute_log=bot.traceroute_log,
        get_radio_log=bot.radio_log,
        list_radio_channels=bot.list_radio_channels,
        create_metdet_channel=bot.create_metdet_channel,
        send_on_channel=bot.send_on_channel,
        announce_weather=bot.announce_weather,
        set_ringtone=bot.set_ringtone,
        nudge_alerts_subscribers=bot.nudge_alerts_subscribers,
        alerts_off_subscribers=bot.alerts_off_subscribers,
        apply_playa_hotfix=bot.apply_playa_hotfix,
        announce_update=bot.announce_update,
        revoke_flagged_users=bot.revoke_flagged_users,
        mesh_host=mesh_label,
        mesh_port=cfg.meshtastic_port,
    )

    log.info("Admin UI on http://%s:%s", cfg.admin_http_host, cfg.admin_http_port)
    app.run(
        host=cfg.admin_http_host,
        port=cfg.admin_http_port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
