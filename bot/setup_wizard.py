"""Interactive setup: CLI prompts + localhost web dialog."""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from typing import Any

from flask import Flask, redirect, request

from bot.envfile import ensure_env_file, read_env, update_env


def list_serial_ports() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports
    except Exception:  # noqa: BLE001
        return []
    likely: list[dict[str, str]] = []
    other: list[dict[str, str]] = []
    for p in list_ports.comports():
        desc = (p.description or "").strip()
        low = f"{p.device} {desc}".lower()
        if any(
            x in low
            for x in (
                "bluetooth",
                "debug-console",
                "incoming",
                "audio",
                "shokz",
                "trekz",
                "iphone",
            )
        ):
            continue
        row = {"device": p.device, "description": desc or p.device}
        if any(
            x in low
            for x in (
                "usbserial",
                "usbmodem",
                "wchusb",
                "cp210",
                "ch340",
                "ttyusb",
                "ttyacm",
                "silabs",
                "serial",
                "com",
            )
        ):
            likely.append(row)
        else:
            other.append(row)
    return likely + other


def apply_setup(form: dict[str, Any]) -> list[str]:
    """Validate + write .env. Returns error messages (empty = ok)."""
    errors: list[str] = []
    key = (form.get("gemini_api_key") or "").strip()
    mode = (form.get("meshtastic_mode") or "tcp").strip().lower()
    if mode not in ("tcp", "usb"):
        mode = "tcp"
    host = (form.get("meshtastic_host") or "").strip()
    port = (form.get("meshtastic_port") or "4403").strip()
    serial = (form.get("meshtastic_serial") or "").strip()
    passphrase = (form.get("unlock_passphrase") or "metdet").strip().lower()
    admins = (form.get("admin_node_ids") or "").strip()
    rate = (form.get("rate_limit_seconds") or "20").strip()
    delay = (form.get("chunk_delay_sec") or "15").strip()
    chunks = (form.get("max_chunks") or "3").strip()
    max_chars = (form.get("max_chunk_chars") or "200").strip()

    if not key:
        errors.append("Gemini API key is required.")
    if mode == "tcp":
        if not host or host in ("127.0.0.1", "localhost"):
            # Allow localhost but warn — still valid for some setups
            if not host:
                errors.append("Radio IP / hostname is required for Wi‑Fi / TCP mode.")
        try:
            pnum = int(port)
            if not (1 <= pnum <= 65535):
                raise ValueError
        except ValueError:
            errors.append("TCP port must be a number (default 4403).")
        serial = ""
    else:
        if not serial:
            errors.append("Pick a USB serial port, or plug the radio in and refresh.")
        host = host or "127.0.0.1"

    for label, val in (
        ("RATE_LIMIT_SECONDS", rate),
        ("CHUNK_DELAY_SEC", delay),
        ("MAX_CHUNKS", chunks),
        ("MAX_CHUNK_CHARS", max_chars),
    ):
        try:
            float(val) if "DELAY" in label or "RATE" in label else int(val)
        except ValueError:
            errors.append(f"{label} must be a number.")

    if errors:
        return errors

    update_env(
        {
            "GEMINI_API_KEY": key,
            "MESHTASTIC_MODE": mode,
            "MESHTASTIC_HOST": host,
            "MESHTASTIC_PORT": str(int(port)) if mode == "tcp" else port or "4403",
            "MESHTASTIC_SERIAL": serial,
            "UNLOCK_PASSPHRASE": passphrase,
            "ADMIN_NODE_IDS": admins,
            "RATE_LIMIT_SECONDS": rate,
            "CHUNK_DELAY_SEC": delay,
            "MAX_CHUNKS": chunks,
            "MAX_CHUNK_CHARS": max_chars,
            "SETUP_COMPLETE": "1",
        }
    )
    return []


def current_form_defaults() -> dict[str, str]:
    env = read_env()
    return {
        "gemini_api_key": env.get("GEMINI_API_KEY", ""),
        "meshtastic_mode": env.get("MESHTASTIC_MODE", "tcp"),
        "meshtastic_host": env.get("MESHTASTIC_HOST", ""),
        "meshtastic_port": env.get("MESHTASTIC_PORT", "4403"),
        "meshtastic_serial": env.get("MESHTASTIC_SERIAL", ""),
        "unlock_passphrase": env.get("UNLOCK_PASSPHRASE", "metdet"),
        "admin_node_ids": env.get("ADMIN_NODE_IDS", ""),
        "rate_limit_seconds": env.get("RATE_LIMIT_SECONDS", "20"),
        "chunk_delay_sec": env.get("CHUNK_DELAY_SEC", "15"),
        "max_chunks": env.get("MAX_CHUNKS", "3"),
        "max_chunk_chars": env.get("MAX_CHUNK_CHARS", "200"),
    }


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _setup_html(
    *,
    values: dict[str, str],
    errors: list[str],
    saved: bool,
    restart_path: str = "/restart",
    form_action: str = "",
    refresh_path: str = "",
) -> str:
    ports = list_serial_ports()
    port_opts = "".join(
        f'<option value="{_esc(p["device"])}"'
        f'{" selected" if p["device"] == values.get("meshtastic_serial") else ""}>'
        f'{_esc(p["device"])} — {_esc(p["description"])}</option>'
        for p in ports
    )
    if not port_opts:
        port_opts = '<option value="">(no serial ports found — plug in USB or use Wi‑Fi)</option>'

    mode = values.get("meshtastic_mode") or "tcp"
    action = form_action or ""
    refresh = refresh_path or (form_action or "/")
    err_html = ""
    if errors:
        items = "".join(f"<li>{_esc(e)}</li>" for e in errors)
        err_html = f'<div class="err"><ul>{items}</ul></div>'
    ok_html = (
        '<div class="ok">Saved to <code>.env</code>. Restart the bot for radio settings to apply '
        '(or click Restart below if this was first-run setup).</div>'
        if saved
        else ""
    )
    key_ph = "(unchanged)" if values.get("gemini_api_key") else "paste key from AI Studio"
    key_val = _esc(values.get("gemini_api_key", ""))

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>METDET setup</title>
<style>
  :root {{ --bg:#0f1419; --card:#1a222c; --text:#e7eef7; --muted:#9aa8b5; --accent:#3d9cf0; --bad:#e85d5d; --ok:#3ecf8e; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.45 system-ui,sans-serif; background:var(--bg); color:var(--text); }}
  main {{ max-width:640px; margin:0 auto; padding:28px 18px 60px; }}
  h1 {{ font-size:1.5rem; margin:0 0 8px; }}
  .muted {{ color:var(--muted); font-size:0.92rem; }}
  .card {{ background:var(--card); border-radius:12px; padding:18px 18px 8px; margin:16px 0; }}
  label {{ display:block; margin:0 0 14px; font-size:0.92rem; }}
  input, select {{ width:100%; margin-top:6px; padding:10px 12px; border-radius:8px; border:1px solid #334; background:#0c1117; color:var(--text); }}
  .row {{ display:flex; gap:12px; flex-wrap:wrap; }}
  .row label {{ flex:1; min-width:140px; }}
  button, .btn {{ appearance:none; border:0; border-radius:8px; padding:11px 16px; background:var(--accent); color:#fff; font-weight:600; cursor:pointer; text-decoration:none; display:inline-block; }}
  button.secondary {{ background:#2a3542; }}
  .err {{ background:#3a1a1a; color:#ffc9c9; padding:12px 14px; border-radius:8px; margin:12px 0; }}
  .ok {{ background:#143328; color:#b6f5d4; padding:12px 14px; border-radius:8px; margin:12px 0; }}
  details {{ margin:12px 0; }}
  summary {{ cursor:pointer; color:var(--muted); }}
  .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:8px; }}
  .hint {{ font-size:0.85rem; color:var(--muted); margin:-6px 0 14px; }}
</style>
<script>
function syncMode() {{
  const m = document.querySelector('input[name=meshtastic_mode]:checked')?.value || 'tcp';
  document.getElementById('tcp-fields').style.display = m === 'tcp' ? 'block' : 'none';
  document.getElementById('usb-fields').style.display = m === 'usb' ? 'block' : 'none';
}}
document.addEventListener('DOMContentLoaded', syncMode);
</script>
</head><body><main>
  <h1>METDET setup</h1>
  <p class="muted">Configure Gemini and how this computer talks to your Meshtastic radio.
  Only one app can use the radio at a time — quit the phone Meshtastic connection to this node before testing.</p>
  {err_html}{ok_html}
  <form method="post" action="{_esc(action) or ''}">
    <div class="card">
      <label>Gemini API key
        <input type="password" name="gemini_api_key" value="{key_val}"
               placeholder="{key_ph}" autocomplete="off"/>
      </label>
      <p class="hint">Get a free key at <a href="https://aistudio.google.com/apikey" style="color:var(--accent)">aistudio.google.com/apikey</a></p>

      <p style="margin:0 0 8px;font-weight:600">Radio connection</p>
      <label style="display:inline-flex;align-items:center;gap:8px;width:auto;margin-right:18px">
        <input type="radio" name="meshtastic_mode" value="tcp" {"checked" if mode=="tcp" else ""} onchange="syncMode()"/>
        Wi‑Fi / Ethernet (TCP)
      </label>
      <label style="display:inline-flex;align-items:center;gap:8px;width:auto">
        <input type="radio" name="meshtastic_mode" value="usb" {"checked" if mode=="usb" else ""} onchange="syncMode()"/>
        USB serial
      </label>

      <div id="tcp-fields" style="margin-top:14px">
        <p class="hint">On the radio (Meshtastic app): enable Wi‑Fi or Ethernet, note its IP, and leave TCP API available (port 4403). This computer must be on the same network. This wizard does not change radio firmware — you still set Wi‑Fi once in the Meshtastic app.</p>
        <div class="row">
          <label>Radio IP / hostname
            <input name="meshtastic_host" value="{_esc(values.get("meshtastic_host",""))}" placeholder="192.168.1.50"/>
          </label>
          <label>TCP port
            <input name="meshtastic_port" value="{_esc(values.get("meshtastic_port","4403"))}"/>
          </label>
        </div>
      </div>

      <div id="usb-fields" style="margin-top:14px;display:none">
        <p class="hint">Plug the radio in over USB. Close other serial clients. Pick the port (often <code>cu.usbserial…</code> / <code>ttyUSB0</code> / <code>COM3</code>).</p>
        <label>Serial port
          <select name="meshtastic_serial">{port_opts}</select>
        </label>
        <p class="hint"><a href="{_esc(refresh)}" style="color:var(--accent)">Refresh port list</a></p>
      </div>
    </div>

    <div class="card">
      <label>Unlock passphrase (users DM this word)
        <input name="unlock_passphrase" value="{_esc(values.get("unlock_passphrase","metdet"))}"/>
      </label>
      <label>Admin node IDs <span class="muted">(optional, comma-separated, e.g. !aabbccdd)</span>
        <input name="admin_node_ids" value="{_esc(values.get("admin_node_ids",""))}" placeholder="!aabbccdd"/>
      </label>
    </div>

    <details class="card">
      <summary>Anti-spam / LoRa pacing (optional)</summary>
      <div class="row" style="margin-top:12px">
        <label>Ask rate limit (sec)
          <input name="rate_limit_seconds" value="{_esc(values.get("rate_limit_seconds","20"))}"/>
        </label>
        <label>Delay between chunks (sec)
          <input name="chunk_delay_sec" value="{_esc(values.get("chunk_delay_sec","15"))}"/>
        </label>
      </div>
      <div class="row">
        <label>Max chunks per answer
          <input name="max_chunks" value="{_esc(values.get("max_chunks","3"))}"/>
        </label>
        <label>Max chars per chunk
          <input name="max_chunk_chars" value="{_esc(values.get("max_chunk_chars","200"))}"/>
        </label>
      </div>
      <p class="hint">Higher delays = less congestion and better message order. Defaults are camp-friendly.</p>
    </details>

    <div class="actions">
      <button type="submit">Save settings</button>
      <a class="btn secondary" href="{_esc(restart_path)}">Restart bot with new settings</a>
    </div>
  </form>
</main></body></html>"""


def create_setup_app(*, allow_restart: bool = True) -> Flask:
    app = Flask(__name__)
    ensure_env_file()

    @app.get("/")
    @app.get("/setup")
    def setup():
        saved = request.args.get("saved") == "1"
        return _setup_html(values=current_form_defaults(), errors=[], saved=saved)

    @app.post("/")
    @app.post("/setup")
    def setup_save():
        form = request.form.to_dict()
        # Keep existing key if password field left blank but one was saved
        if not (form.get("gemini_api_key") or "").strip():
            form["gemini_api_key"] = current_form_defaults().get("gemini_api_key", "")
        errors = apply_setup(form)
        if errors:
            vals = current_form_defaults()
            vals.update({k: form.get(k, vals.get(k, "")) for k in vals})
            if form.get("gemini_api_key"):
                vals["gemini_api_key"] = form["gemini_api_key"]
            return _setup_html(values=vals, errors=errors, saved=False), 400
        return redirect(url_for("setup", saved=1))

    @app.get("/restart")
    def restart():
        if not allow_restart:
            return (
                "<p>Settings saved. Restart the bot process yourself "
                "(e.g. <code>pm2 restart metdet-mesh-bot</code>).</p>",
                200,
            )

        def _reexec() -> None:
            time.sleep(0.6)
            os.execv(sys.executable, [sys.executable, "-m", "bot.main"])

        threading.Thread(target=_reexec, daemon=True).start()
        return (
            "<p>Restarting… open <a href='http://127.0.0.1:8787/'>admin UI</a> in a few seconds.</p>",
            200,
        )

    return app


def run_web_setup(*, host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> None:
    app = create_setup_app(allow_restart=True)
    url = f"http://{host}:{port}/"
    print(f"\n==> Setup dialog: {url}\n    Save settings there, then click Restart.\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def run_cli_setup() -> None:
    ensure_env_file()
    d = current_form_defaults()
    print("\nMETDET setup (press Enter to keep the value in [brackets])\n")

    def ask(label: str, key: str, *, secret: bool = False) -> str:
        cur = d.get(key, "")
        shown = "****" if secret and cur else cur
        raw = input(f"{label} [{shown}]: ").strip()
        if not raw and secret and cur:
            return cur
        return raw if raw else cur

    key = ask("Gemini API key", "gemini_api_key", secret=True)
    mode = ask("Connection mode: tcp (Wi-Fi) or usb", "meshtastic_mode").lower()
    if mode not in ("tcp", "usb"):
        mode = "tcp"
    host, port, serial = d.get("meshtastic_host", ""), d.get("meshtastic_port", "4403"), ""
    if mode == "tcp":
        host = ask("Radio IP / hostname", "meshtastic_host")
        port = ask("TCP port", "meshtastic_port")
    else:
        ports = list_serial_ports()
        if ports:
            print("USB ports found:")
            for i, p in enumerate(ports, 1):
                print(f"  {i}. {p['device']}  ({p['description']})")
            choice = input("Pick number or paste device path: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(ports):
                serial = ports[int(choice) - 1]["device"]
            else:
                serial = choice or d.get("meshtastic_serial", "")
        else:
            serial = ask("Serial device path (none auto-detected)", "meshtastic_serial")

    passphrase = ask("Unlock passphrase", "unlock_passphrase")
    admins = ask("Admin node IDs (comma-separated, optional)", "admin_node_ids")
    print("\nAnti-spam defaults (optional):")
    rate = ask("Ask rate limit seconds", "rate_limit_seconds")
    delay = ask("Chunk delay seconds", "chunk_delay_sec")
    chunks = ask("Max chunks", "max_chunks")
    max_chars = ask("Max chars per chunk", "max_chunk_chars")

    errors = apply_setup(
        {
            "gemini_api_key": key,
            "meshtastic_mode": mode,
            "meshtastic_host": host,
            "meshtastic_port": port,
            "meshtastic_serial": serial,
            "unlock_passphrase": passphrase,
            "admin_node_ids": admins,
            "rate_limit_seconds": rate,
            "chunk_delay_sec": delay,
            "max_chunks": chunks,
            "max_chunk_chars": max_chars,
        }
    )
    if errors:
        print("\nCould not save:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"\nSaved {ensure_env_file()}")
    print("Start the bot with:  python -m bot.main\n")
