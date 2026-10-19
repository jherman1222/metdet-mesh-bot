"""
Read-only archive browser for the Burning Man 2026 public SQLite snapshot.

  python -m bot.archive
  ARCHIVE_HTTP_PORT=8788 python -m bot.archive

No radio, no Gemini, no outbound POSTs. Uses the same admin UI in archive mode.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from bot.admin import create_admin_app
from bot.config import ROOT
from bot.db import Store
from bot.version import RELEASE_HEADLINE, VERSION

log = logging.getLogger("metdet.archive")

DEFAULT_ARCHIVE_DB = ROOT / "archive" / "bm2026" / "metdet-archive.db"


def _noop(*_a, **_k):  # noqa: ANN001
    return "Archive is read-only"


def _noop_str(*_a, **_k) -> str:  # noqa: ANN001
    return "Archive is read-only"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db = Path(os.getenv("ARCHIVE_DB", str(DEFAULT_ARCHIVE_DB))).expanduser()
    if not db.exists():
        raise SystemExit(
            f"Archive DB missing: {db}\n"
            "Run: python scripts/export_public_archive.py\n"
            "Or set ARCHIVE_DB=/path/to/metdet-archive.db"
        )

    store = Store(db)
    started = time.time()

    def status() -> dict:
        stats = store.stats()
        return {
            "connected": False,
            "uptime_sec": int(time.time() - started),
            "last_inbound": "archive",
            "my_node_id": "archive",
            "radio_name": "BM2026 archive",
            "radio_long": "Burning Man 2026 engagement snapshot",
            "version": VERSION,
            "release_headline": f"ARCHIVE · {RELEASE_HEADLINE}",
            "modem_preset": "—",
            "users": stats.get("users"),
            "queries": stats.get("queries"),
        }

    app = create_admin_app(
        store,
        get_status=status,
        get_passphrase=lambda: "[archived]",
        set_passphrase=_noop,
        get_mesh_nodes=lambda: [],
        push_to_node=_noop_str,
        push_weather_unlocked=_noop_str,
        mqtt_status=lambda: {"enabled": False, "note": "archive"},
        set_mqtt_enabled=_noop_str,
        get_channel_invite_url=lambda: "[redacted-channel-invite]",
        set_channel_invite_url=_noop,
        get_traceroute_log=lambda: [],
        get_radio_log=lambda: [],
        list_radio_channels=lambda: [],
        create_metdet_channel=_noop_str,
        send_on_channel=_noop_str,
        announce_weather=_noop_str,
        set_ringtone=_noop_str,
        nudge_alerts_subscribers=_noop_str,
        alerts_off_subscribers=_noop_str,
        apply_playa_hotfix=_noop_str,
        announce_update=_noop_str,
        revoke_flagged_users=_noop_str,
        mesh_host="archive",
        mesh_port=0,
        archive_mode=True,
    )

    host = os.getenv("ARCHIVE_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("ARCHIVE_HTTP_PORT", "8788"))
    log.info("BM2026 archive UI on http://%s:%s  (db=%s)", host, port, db)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
