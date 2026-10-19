from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from flask import Flask, redirect, render_template_string, request, url_for

from bot.db import Store
from bot.version import user_update_message

# Friendly labels for common Meshtastic hwModel enum names
_HW_FRIENDLY = {
    "TRACKER_T1000_E": "SenseCap T1000-E",
    "TRACKER_T1000_E_PRO": "SenseCap T1000-E Pro",
    "SENSECAP_INDICATOR": "SenseCap Indicator",
    "HELTEC_V3": "Heltec V3",
    "HELTEC_WSL_V3": "Heltec WSL V3",
    "HELTEC_WIRELESS_TRACKER": "Heltec Wireless Tracker",
    "HELTEC_WIRELESS_PAPER": "Heltec Wireless Paper",
    "HELTEC_V2_1": "Heltec V2.1",
    "HELTEC_V2_0": "Heltec V2.0",
    "TBEAM": "T-Beam",
    "TBEAM_V0P7": "T-Beam v0.7",
    "LILYGO_TBEAM_S3_CORE": "LilyGO T-Beam S3",
    "T_DECK": "LilyGO T-Deck",
    "T_ECHO": "LilyGO T-Echo",
    "T_WATCH_S3": "LilyGO T-Watch S3",
    "RAK4631": "RAK4631",
    "RAK11200": "RAK11200",
    "RAK2560": "RAK2560",
    "STATION_G1": "Station G1",
    "STATION_G2": "Station G2",
    "NANO_G1": "Nano G1",
    "NANO_G1_EXPLORER": "Nano G1 Explorer",
    "NANO_G2_ULTRA": "Nano G2 Ultra",
    "SEEED_XIAO_S3": "Seeed XIAO S3",
    "SEEED_WIO_TRACKER_L1": "Seeed Wio Tracker L1",
    "SEEED_WIO_TRACKER_L1_EINK": "Seeed Wio Tracker L1 eInk",
    "RP2040_LORA": "RP2040 LoRa",
    "BETAFPV_2400_TX": "BETAFPV 2.4G TX",
    "UNSET": "Unknown",
}


def friendly_hw_model(hw: Any) -> str:
    """Turn Meshtastic hwModel enum / string into a short human label."""
    if hw is None or hw == "":
        return "—"
    raw = str(hw).strip()
    if not raw or raw.upper() == "UNSET":
        return "—"
    if raw in _HW_FRIENDLY:
        return _HW_FRIENDLY[raw]
    upper = raw.upper()
    if upper in _HW_FRIENDLY:
        return _HW_FRIENDLY[upper]
    # TRACKER_T1000_E → Tracker T1000 E
    return raw.replace("_", " ").title()


def radio_name(mesh: dict[str, Any] | None, node_id: str, stored_short: str | None = None) -> str:
    """Show radio long + short names; use the node ID only when names are unknown."""
    mesh = mesh or {}
    long_name = (mesh.get("long_name") or "").strip()
    short_name = (mesh.get("short_name") or stored_short or "").strip()
    if long_name and short_name and short_name.casefold() != long_name.casefold():
        return f"{long_name} ({short_name})"
    return long_name or short_name or node_id or "—"


PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>METDET admin</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f4ef;
      --panel: #fffcf7;
      --ink: #1c1917;
      --muted: #78716c;
      --line: #e7e5e4;
      --accent: #0f766e;
      --accent-soft: #ccfbf1;
      --danger: #b91c1c;
      --ok: #15803d;
      --warn: #a16207;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #1c1917;
        --panel: #292524;
        --ink: #fafaf9;
        --muted: #a8a29e;
        --line: #44403c;
        --accent: #2dd4bf;
        --accent-soft: #134e4a;
      }
    }
    * { box-sizing: border-box; }
    body {
      font-family: "Segoe UI", "Helvetica Neue", ui-sans-serif, system-ui, sans-serif;
      margin: 0; padding: 20px 24px 48px;
      max-width: 1180px;
      background: var(--bg); color: var(--ink);
      line-height: 1.45;
    }
    h1 { font-size: 1.45rem; font-weight: 650; letter-spacing: -0.02em; margin: 0 0 4px; }
    h2 { font-size: 1.1rem; font-weight: 600; margin: 0 0 12px; }
    .sub { color: var(--muted); font-size: 0.9rem; margin: 0 0 14px; }
    nav {
      display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 18px;
    }
    nav a {
      text-decoration: none; color: var(--ink);
      padding: 6px 11px; border-radius: 999px;
      background: var(--panel); border: 1px solid var(--line);
      font-size: 0.88rem;
    }
    nav a:hover { border-color: var(--accent); color: var(--accent); }
    .row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
    .card {
      background: var(--panel); border: 1px solid var(--line);
      border-radius: 12px; padding: 14px 16px; min-width: 128px;
    }
    .card .muted { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
    .muted { color: var(--muted); font-size: 0.9rem; }
    table { width: 100%; border-collapse: collapse; font-size: 0.84rem; background: var(--panel); border-radius: 12px; overflow: hidden; border: 1px solid var(--line); }
    th, td {
      text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line);
      vertical-align: middle; white-space: nowrap;
    }
    td.wrap, th.wrap { white-space: normal; }
    td.clip {
      max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    th { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); background: transparent; }
    th a { color: inherit; text-decoration: none; }
    thead .filters-row th { background: var(--bg); font-weight: 400; text-transform: none; letter-spacing: 0; padding: 4px 6px; }
    thead .filters-row input {
      width: 100%; min-width: 0; padding: 3px 6px; font-size: 0.75rem;
    }
    th.js-sort { cursor: pointer; user-select: none; }
    th.js-sort:hover { color: var(--accent); }
    th.js-sort .sort-mark { font-size: 0.7em; color: var(--accent); margin-left: 3px; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: color-mix(in srgb, var(--accent) 6%, transparent); }
    form.inline { display: inline-flex; gap: 3px; align-items: center; flex-wrap: nowrap; }
    input[type=text], select {
      padding: 4px 7px; min-width: 72px; border-radius: 6px;
      border: 1px solid var(--line); background: var(--bg); color: var(--ink); font: inherit;
      font-size: 0.82rem;
    }
    button, .btn {
      padding: 4px 8px; cursor: pointer; font: inherit; font-size: 0.78rem;
      border-radius: 6px; border: 1px solid var(--line);
      background: var(--panel); color: var(--ink); white-space: nowrap;
    }
    button:hover { border-color: var(--accent); }
    .ok { color: var(--ok); }
    .bad { color: var(--danger); }
    .flash {
      background: var(--accent-soft); border: 1px solid var(--accent);
      border-radius: 10px; padding: 10px 14px; margin-bottom: 16px; color: var(--ink);
    }
    .filters { display:flex; gap:10px; flex-wrap:wrap; margin: 10px 0 16px; align-items:end; }
    #map { height: 420px; width: 100%; border: 1px solid var(--line); border-radius: 12px; }
    .snr { min-width: 100px; }
    .snr-bar {
      height: 7px; width: 72px; background: var(--line); border-radius: 4px; overflow: hidden;
      display: inline-block; vertical-align: middle; margin-right: 5px;
    }
    .snr-fill { height: 100%; border-radius: 4px; }
    .snr-great { background: #15803d; }
    .snr-good { background: #16a34a; }
    .snr-ok { background: #ca8a04; }
    .snr-weak { background: #ea580c; }
    .snr-poor { background: #dc2626; }
    .snr-label { font-size: 0.78rem; white-space: nowrap; }
    .fav { color: #ca8a04; }
    .label-form input[type=text] { min-width: 70px; max-width: 110px; }
    .actions { display:inline-flex; gap:3px; flex-wrap: nowrap; }
    .tag { font-size: 0.75rem; color: var(--muted); }
    .who { font-weight: 600; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem; }
    .dir-rx { color: var(--accent); font-weight: 600; }
    .dir-tx { color: var(--warn); font-weight: 600; }
    .ack-ok { color: #16a34a; font-weight: 600; }
    .ack-fail { color: #dc2626; font-weight: 600; }
    .ack-na { color: var(--muted); }
    .dir-sys { color: var(--muted); font-weight: 600; }
    code { font-size: 0.86em; }
    .panel {
      background: var(--panel); border: 1px solid var(--line);
      border-radius: 12px; padding: 16px 18px; margin-bottom: 18px;
    }
  </style>
  {% if include_leaflet %}
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  {% endif %}
</head>
<body>
  <h1>METDET{% if archive_mode %} <span class="muted">archive</span>{% endif %}</h1>
  <p class="sub">{% if archive_mode %}Burning Man 2026 engagement snapshot (read-only) · {% endif %}v{{ version }} · {{ release_headline }} · {{ host }}{% if mesh_port %}:{{ mesh_port }}{% endif %}</p>
  {% if archive_mode %}
  <div class="flash">ARCHIVE MODE — browsing saved playa data. No radio, no Gemini, no outbound sends.</div>
  {% endif %}
  <nav>
    <a href="/">Status</a>
    {% if not archive_mode %}<a href="/nodes">Nodes</a>
    <a href="/radio">Radio log</a>
    <a href="/metaldetect">MetalDetect</a>{% endif %}
    <a href="/public">Public log</a>
    <a href="/dms">DMs</a>
    {% if not archive_mode %}<a href="/canned">Canned</a>
    <a href="/ringtones">Ringtones</a>
    <a href="/traceroute">Trace</a>
    <a href="/map">Map</a>{% endif %}
    <a href="/users">Users</a>
    <a href="/queries">Queries</a>
    <a href="/pushes">Pushes</a>
    {% if not archive_mode %}<a href="/chords">Chords</a>
    <a href="/settings">Settings</a>
    <a href="/setup">Setup</a>{% endif %}
  </nav>
  {% if flash %}<div class="flash">{{ flash }}</div>{% endif %}
  {{ body|safe }}
  <script>
  (function () {
    function cellText(td) {
      return (td && (td.innerText || td.textContent) || "").replace(/\\s+/g, " ").trim();
    }
    function parseVal(s) {
      if (!s || s === "—") return { n: null, s: "" };
      const ago = s.match(/^(\\d+)\\s*(s|m|h|d)\\b/i);
      if (ago) {
        const n = parseInt(ago[1], 10);
        const u = ago[2].toLowerCase();
        const mul = { s: 1, m: 60, h: 3600, d: 86400 }[u] || 1;
        return { n: n * mul, s: s.toLowerCase() };
      }
      const ts = Date.parse(s.replace(/(\\d{2}:\\d{2}:\\d{2})$/, " $1"));
      if (!isNaN(ts)) return { n: ts, s: s.toLowerCase() };
      const num = s.replace(/[^0-9.+-]/g, "");
      if (num && /[0-9]/.test(num) && s.length < 24) {
        const n = parseFloat(num);
        if (!isNaN(n)) return { n: n, s: s.toLowerCase() };
      }
      return { n: null, s: s.toLowerCase() };
    }
    function enhance(table) {
      if (table.dataset.plain === "1") return;
      const thead = table.tHead;
      const tbody = table.tBodies[0];
      if (!thead || !tbody || !thead.rows[0]) return;
      const header = thead.rows[0];
      if (header.dataset.enhanced === "1") return;
      header.dataset.enhanced = "1";
      [...header.cells].forEach((th, i) => {
        th.classList.add("js-sort");
        th.addEventListener("click", (e) => {
          if (e.target.closest("a, input, button, select, label")) return;
          sortTable(table, i);
        });
      });
      const fr = document.createElement("tr");
      fr.className = "filters-row";
      [...header.cells].forEach(() => {
        const th = document.createElement("th");
        const inp = document.createElement("input");
        inp.type = "search";
        inp.placeholder = "filter";
        inp.addEventListener("click", (e) => e.stopPropagation());
        inp.addEventListener("input", () => {
          filterTable(table);
          saveFilters(table);
        });
        th.appendChild(inp);
        fr.appendChild(th);
      });
      thead.appendChild(fr);
      loadFilters(table);
      filterTable(table);
    }
    function storeKey(table) {
      const n = [...document.querySelectorAll("table")].indexOf(table);
      return "metdet-tbl:" + location.pathname + ":" + n;
    }
    function saveFilters(table) {
      const vals = [...table.querySelectorAll("thead .filters-row input")].map((i) => i.value || "");
      try { sessionStorage.setItem(storeKey(table), JSON.stringify(vals)); } catch (e) {}
    }
    function loadFilters(table) {
      try {
        const raw = sessionStorage.getItem(storeKey(table));
        if (!raw) return;
        const vals = JSON.parse(raw);
        [...table.querySelectorAll("thead .filters-row input")].forEach((inp, i) => {
          if (vals[i]) inp.value = vals[i];
        });
      } catch (e) {}
    }
    function filterTable(table) {
      const ins = [...table.querySelectorAll("thead .filters-row input")];
      const qs = ins.map((i) => (i.value || "").trim().toLowerCase());
      [...table.tBodies[0].rows].forEach((tr) => {
        const ok = qs.every((q, i) => !q || cellText(tr.cells[i]).toLowerCase().includes(q));
        tr.style.display = ok ? "" : "none";
      });
    }
    function sortTable(table, idx) {
      const tbody = table.tBodies[0];
      const rows = [...tbody.rows];
      const prev = table.dataset.sortIdx;
      const dir = prev === String(idx) && table.dataset.sortDir === "asc" ? "desc" : "asc";
      table.dataset.sortIdx = String(idx);
      table.dataset.sortDir = dir;
      [...table.tHead.rows[0].cells].forEach((th, i) => {
        th.querySelectorAll(".sort-mark").forEach((m) => m.remove());
        if (i === idx) {
          const m = document.createElement("span");
          m.className = "sort-mark";
          m.textContent = dir === "asc" ? "▲" : "▼";
          th.appendChild(m);
        }
      });
      rows.sort((a, b) => {
        const va = parseVal(cellText(a.cells[idx]));
        const vb = parseVal(cellText(b.cells[idx]));
        let c = 0;
        if (va.n != null && vb.n != null) c = va.n - vb.n;
        else c = va.s.localeCompare(vb.s, undefined, { numeric: true, sensitivity: "base" });
        return dir === "asc" ? c : -c;
      });
      rows.forEach((r) => tbody.appendChild(r));
    }
    document.addEventListener("DOMContentLoaded", () => {
      document.querySelectorAll("table").forEach(enhance);
    });
  })();
  </script>
</body>
</html>
"""


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _ago(ts: float | None) -> str:
    if not ts:
        return "—"
    sec = max(0, int(datetime.now(tz=timezone.utc).timestamp() - ts))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def _fmt_ack(direction: str | None, ack_status: str | None) -> str:
    d = (direction or "").lower()
    if d == "rx":
        return "<span class='ack-na'>—</span>"
    ack = (ack_status or "").lower()
    if ack == "acked":
        return "<span class='ack-ok' title='mesh ACK'>✓</span>"
    if ack in ("failed", "fail"):
        return "<span class='ack-fail' title='send failed'>✗</span>"
    if d == "tx":
        return "<span class='ack-na' title='before ACK logging'>?</span>"
    return "<span class='ack-na'>—</span>"


def _fmt_uptime(sec: Any) -> str:
    if not isinstance(sec, (int, float)) or sec < 0:
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h{sec % 3600 // 60:02d}m"
    return f"{sec // 86400}d{sec % 86400 // 3600}h"


def _fmt_power(battery: Any, voltage: Any = None) -> str:
    """Meshtastic: batteryLevel 101 often means plugged in / charging."""
    bits: list[str] = []
    if isinstance(battery, (int, float)):
        if int(battery) >= 101:
            bits.append("⚡chg")
        else:
            bits.append(f"{int(battery)}%")
    if isinstance(voltage, (int, float)) and voltage > 0:
        bits.append(f"{voltage:.2f}V")
    return " ".join(bits) if bits else "—"


def _snr_cell(snr: Any) -> str:
    """
    Meshtastic SNR is in dB (signal vs noise). Higher = better.
    Rough guide: >5 great, 0..5 good, -5..0 ok, -10..-5 weak, <-10 poor.
    LoRa can decode around -20 in good conditions.
    """
    if not isinstance(snr, (int, float)):
        return "—"
    # Map -20..+10 dB -> 0..100% bar
    pct = int(max(0, min(100, (snr + 20) * (100 / 30))))
    if snr >= 5:
        cls, word = "snr-great", "great"
    elif snr >= 0:
        cls, word = "snr-good", "good"
    elif snr >= -5:
        cls, word = "snr-ok", "ok"
    elif snr >= -10:
        cls, word = "snr-weak", "weak"
    else:
        cls, word = "snr-poor", "poor"
    return (
        f'<div class="snr">'
        f'<span class="snr-bar"><span class="snr-fill {cls}" style="width:{pct}%"></span></span>'
        f'<span class="snr-label">{snr:.1f} dB · {word}</span>'
        f"</div>"
    )


def create_admin_app(
    store: Store,
    *,
    get_status: Callable[[], dict[str, Any]],
    get_passphrase: Callable[[], str],
    set_passphrase: Callable[[str], None],
    get_mesh_nodes: Callable[[], list[dict[str, Any]]],
    push_to_node: Callable[..., str],
    push_weather_unlocked: Callable[..., str],
    mqtt_status: Callable[[], dict[str, Any]],
    set_mqtt_enabled: Callable[[bool], str],
    get_channel_invite_url: Callable[[], str],
    set_channel_invite_url: Callable[[str], None],
    get_traceroute_log: Callable[[], list[dict[str, Any]]],
    get_radio_log: Callable[[], list[dict[str, Any]]],
    list_radio_channels: Callable[[], list[dict[str, Any]]],
    create_metdet_channel: Callable[[], str],
    send_on_channel: Callable[..., str],
    announce_weather: Callable[..., str],
    set_ringtone: Callable[..., str],
    nudge_alerts_subscribers: Callable[[], str],
    alerts_off_subscribers: Callable[..., str],
    apply_playa_hotfix: Callable[..., str],
    announce_update: Callable[[], str],
    revoke_flagged_users: Callable[..., str],
    mesh_host: str,
    mesh_port: int,
    archive_mode: bool = False,
) -> Flask:
    from bot.version import RELEASE_HEADLINE, VERSION

    app = Flask(__name__)

    @app.before_request
    def _archive_readonly():  # type: ignore[misc]
        if archive_mode and request.method not in ("GET", "HEAD", "OPTIONS"):
            return ("Archive is read-only", 403)
        return None

    def render(body: str, *, include_leaflet: bool = False) -> str:
        return render_template_string(
            PAGE,
            body=body,
            host=mesh_host,
            mesh_port=mesh_port,
            passphrase="[archived]" if archive_mode else get_passphrase(),
            flash=request.args.get("flash"),
            include_leaflet=include_leaflet,
            version=VERSION,
            release_headline=RELEASE_HEADLINE,
            archive_mode=archive_mode,
        )

    @app.get("/")
    def status_page() -> str:
        st = get_status()
        stats = store.stats()
        connected = st.get("connected")
        cls = "ok" if connected else "bad"
        radio_name = (st.get("radio_name") or "—").replace("<", "&lt;")
        radio_id = (st.get("my_node_id") or "—").replace("<", "&lt;")
        radio_long = (st.get("radio_long") or "").replace("<", "&lt;")
        version = (st.get("version") or "—").replace("<", "&lt;")
        headline = (st.get("release_headline") or "").replace("<", "&lt;")
        body = f"""
        <div class="row">
          <div class="card"><div class="muted">Version</div><div>{version}</div>
            <div class="muted" style="text-transform:none;letter-spacing:0;margin-top:4px;font-size:0.82rem">{headline or "—"}</div></div>
          <div class="card"><div class="muted">Radio</div><div class="{cls}">{"connected" if connected else "down"}</div></div>
          <div class="card"><div class="muted">Bot radio</div><div>{radio_name}</div>
            <div class="muted" style="text-transform:none;letter-spacing:0;margin-top:4px"><code>{radio_id}</code>
            {f' · {radio_long}' if radio_long and radio_long != radio_name else ''}</div></div>
          <div class="card"><div class="muted">Uptime</div><div>{int(st.get("uptime_sec", 0))}s</div></div>
          <div class="card"><div class="muted">Unlocked users</div><div>{stats["users"]}</div></div>
          <div class="card"><div class="muted">Queries</div><div>{stats["queries"]}</div></div>
          <div class="card"><div class="muted">Chords</div><div>{stats.get("chords", 0)}</div></div>
          <div class="card"><div class="muted">Sun opts-in</div><div>{stats.get("sun", 0)}</div></div>
          <div class="card"><div class="muted">WX alerts</div><div>{stats.get("alert", 0)}</div></div>
          <div class="card"><div class="muted">Mesh nodes</div><div>{len(get_mesh_nodes())}</div></div>
          <div class="card"><div class="muted">Modem preset</div><div>{st.get("modem_preset") or "—"}</div></div>
          <div class="card"><div class="muted">Last inbound</div><div>{st.get("last_inbound") or "—"}</div></div>
        </div>
        <form class="inline" method="post" action="{url_for('push_weather_all')}"
              onsubmit="return confirm('Push this weather DM to ALL unlocked users?');"
              style="gap:8px;margin:12px 0;align-items:center">
          <label>Push WX
            <select name="wx_mode">
              <option value="watch" selected>watch (beware flags)</option>
              <option value="now">now (full numbers)</option>
              <option value="wind">wind (hourly)</option>
              <option value="rain">rain (hourly)</option>
              <option value="week">week (7-day)</option>
            </select>
          </label>
          <label>Place
            <select name="wx_place">
              <option value="reno">reno</option>
              <option value="brc" selected>brc</option>
              <option value="both">both</option>
            </select>
          </label>
          <button type="submit">→ all unlocked</button>
        </form>
          <p class="muted">Say <code>menu</code> on the radio. Best push is usually <b>watch</b> (only abnormal heat/wind/rain). Users can also ask <code>wx watch</code> themselves.</p>
        <p class="muted">Admin is bound to <code>127.0.0.1</code> by default. To reach it over Tailscale, see Settings → Tailnet.</p>
        """
        return render(body)

    @app.post("/push/weather-all")
    def push_weather_all():
        mode = (request.form.get("wx_mode") or "watch").strip()
        place = (request.form.get("wx_place") or "brc").strip()
        msg = push_weather_unlocked(mode, place)
        return redirect(url_for("status_page", flash=msg))

    @app.get("/chords")
    def chords_page() -> str:
        rows = store.list_chords(150)
        tr = []
        for r in rows:
            prompt = (r.get("prompt") or "").replace("<", "&lt;")
            reply = (r.get("reply") or r.get("error") or "").replace("<", "&lt;")
            tr.append(
                "<tr>"
                f"<td>{_fmt_ts(r.get('ts'))}</td>"
                f"<td>{r.get('short_name') or ''}<br/><code>{r.get('node_id')}</code></td>"
                f"<td class='wrap'>{prompt}</td>"
                f"<td class='wrap'>{reply}</td>"
                "</tr>"
            )
        body = """
        <h2>Chord sheets</h2>
        <p class="muted">Logged when someone DMs <code>chords …</code></p>
        <table>
          <thead><tr><th>Time</th><th>Who</th><th>Request</th><th>Sheet</th></tr></thead>
          <tbody>
        """ + "".join(tr) + "</tbody></table>"
        return render(body)

    @app.get("/queries")
    def queries_page() -> str:
        rows = store.list_queries(300)
        labels = store.labels_map()
        mesh_by_id = {n.get("node_id"): n for n in get_mesh_nodes()}
        tr = []
        for r in rows:
            nid = r.get("node_id") or ""
            mesh = mesh_by_id.get(nid) or {}
            who = Store.display_name(
                labels.get(nid),
                mesh_short=mesh.get("short_name") or r.get("short_name"),
                mesh_long=mesh.get("long_name"),
                node_id=nid,
            ).replace("<", "&lt;")
            prompt = (r.get("prompt") or "").replace("<", "&lt;")
            reply = (r.get("reply") or r.get("error") or "").replace("<", "&lt;")
            tr.append(
                "<tr>"
                f"<td>{_ago(r.get('ts'))}</td>"
                f"<td class='mono'>{_fmt_ts(r.get('ts'))}</td>"
                f"<td class='who' title='{nid}'>{who}</td>"
                f"<td class='wrap'>{prompt}</td>"
                f"<td class='wrap'>{reply}</td>"
                f"<td>{r.get('mode') or ''}</td>"
                f"<td>{r.get('kind') or ''}</td>"
                f"<td>{r.get('latency_ms') if r.get('latency_ms') is not None else '—'}</td>"
                "</tr>"
            )
        body = """
        <h2>Queries</h2>
        <p class="muted">Gemini / weather DMs from radios. Admin blasts are on <a href="/pushes">Pushes</a>. Click a column to sort; type in the filter row.</p>
        <table>
          <thead><tr><th>When</th><th>Timestamp</th><th>Who</th><th>Prompt</th><th>Reply</th><th>Mode</th><th>Kind</th><th>ms</th></tr></thead>
          <tbody>
        """ + ("".join(tr) or "<tr><td colspan='8' class='muted'>No queries yet.</td></tr>") + "</tbody></table>"
        return render(body)

    @app.get("/pushes")
    def pushes_page() -> str:
        rows = store.list_pushes(300)
        labels = store.labels_map()
        mesh_by_id = {n.get("node_id"): n for n in get_mesh_nodes()}
        tr = []
        for r in rows:
            nid = r.get("node_id") or ""
            mesh = mesh_by_id.get(nid) or {}
            who = ""
            if nid:
                who = Store.display_name(
                    labels.get(nid),
                    mesh_short=mesh.get("short_name"),
                    mesh_long=mesh.get("long_name"),
                    node_id=nid,
                ).replace("<", "&lt;")
            target = (r.get("target") or who or "—").replace("<", "&lt;")
            detail = (r.get("detail") or "").replace("<", "&lt;")
            tr.append(
                "<tr>"
                f"<td>{_ago(r.get('ts'))}</td>"
                f"<td class='mono'>{_fmt_ts(r.get('ts'))}</td>"
                f"<td>{(r.get('action') or '—').replace('<','&lt;')}</td>"
                f"<td class='who' title='{nid}'>{target}</td>"
                f"<td>{(r.get('channel') or '—').replace('<','&lt;')}</td>"
                f"<td class='wrap'>{detail}</td>"
                "</tr>"
            )
        body = """
        <h2>Push log</h2>
        <p class="muted">Admin outbound: WX pushes, canned/channel announces, invites, alert recheck/off. Radio questions stay on <a href="/queries">Queries</a>.</p>
        <table>
          <thead><tr><th>When</th><th>Timestamp</th><th>Action</th><th>Target</th><th>Channel</th><th class="wrap">Detail</th></tr></thead>
          <tbody>
        """ + ("".join(tr) or "<tr><td colspan='6' class='muted'>No pushes yet.</td></tr>") + "</tbody></table>"
        return render(body)

    @app.get("/nodes")
    def nodes_page() -> str:
        q = (request.args.get("q") or "").strip().lower()
        sort = (request.args.get("sort") or "fav").strip()
        order = (request.args.get("order") or "desc").strip()
        fav_only = (request.args.get("fav") or "") == "1"
        labels = store.labels_map()
        nodes = get_mesh_nodes()
        for n in nodes:
            lab = labels.get(n.get("node_id") or "") or {}
            n["alias"] = lab.get("alias")
            n["user_label"] = lab.get("user_label")
            n["note"] = lab.get("note")
            n["favorite"] = bool(lab.get("favorite"))
            n["display"] = Store.display_name(
                lab,
                mesh_short=n.get("short_name"),
                mesh_long=n.get("long_name"),
                node_id=n.get("node_id"),
            )
        if fav_only:
            nodes = [n for n in nodes if n.get("favorite")]
        if q:
            nodes = [
                n
                for n in nodes
                if q in (n.get("short_name") or "").lower()
                or q in (n.get("long_name") or "").lower()
                or q in (n.get("node_id") or "").lower()
                or q in (n.get("alias") or "").lower()
                or q in (n.get("user_label") or "").lower()
                or q in (n.get("note") or "").lower()
                or q in (n.get("device") or "").lower()
                or q in (str(n.get("hw_model") or "")).lower()
                or q in (str(n.get("role") or "")).lower()
            ]

        reverse = order != "asc"

        def key_fn(n: dict[str, Any]):
            if sort == "snr":
                return n.get("snr") if n.get("snr") is not None else -999
            if sort == "name":
                return (n.get("display") or "").lower()
            if sort == "device":
                return (n.get("device") or n.get("hw_model") or "").lower()
            if sort == "hops":
                return n.get("hops_away") if n.get("hops_away") is not None else 999
            if sort == "fav":
                # Favorites first, then last heard
                return (1 if n.get("favorite") else 0, n.get("last_heard") or 0)
            if sort == "batt":
                b = n.get("battery")
                return b if isinstance(b, (int, float)) else -1
            return n.get("last_heard") or 0

        nodes.sort(key=key_fn, reverse=reverse)

        def sort_link(col: str) -> str:
            next_order = "asc" if sort == col and order == "desc" else "desc"
            return url_for(
                "nodes_page",
                sort=col,
                order=next_order,
                q=q or None,
                fav="1" if fav_only else None,
            )

        tr = []
        for n in nodes:
            nid = n.get("node_id") or ""
            snr_html = _snr_cell(n.get("snr"))
            hops = n.get("hops_away")
            hops_s = str(hops) if hops is not None else "—"
            alias_esc = (n.get("alias") or "").replace('"', "&quot;")
            note_esc = (n.get("note") or "").replace('"', "&quot;")
            fav_checked = "checked" if n.get("favorite") else ""
            star = '<span class="fav">★</span>' if n.get("favorite") else ""
            disp = (n.get("display") or "—").replace("<", "&lt;")
            iam = (n.get("user_label") or "—").replace("<", "&lt;")
            long_n = (n.get("long_name") or "—").replace("<", "&lt;")
            short_n = (n.get("short_name") or "—").replace("<", "&lt;")
            device = (n.get("device") or "—").replace("<", "&lt;")
            hw_raw = (n.get("hw_model") or "")
            device_title = str(hw_raw).replace('"', "&quot;") if hw_raw else ""
            detail_href = url_for("node_detail", node_id=nid)
            label_form = f"""
            <form class="inline label-form" method="post" action="{url_for('node_label')}">
              <input type="hidden" name="node_id" value="{nid}"/>
              <input type="text" name="alias" value="{alias_esc}" placeholder="alias" maxlength="40" title="Your name for them"/>
              <input type="text" name="note" value="{note_esc}" placeholder="note" maxlength="80"/>
              <label title="Favorite"><input type="checkbox" name="favorite" value="1" {fav_checked}/>★</label>
              <button type="submit">Save</button>
            </form>
            """
            act = f"""
            <div class="actions">
              <a class="btn" href="{detail_href}">Info</a>
              <form class="inline" method="post" action="{url_for('node_action')}"><input type="hidden" name="node_id" value="{nid}"/><input type="hidden" name="action" value="invite"/><button type="submit">Invite</button></form>
              <form class="inline" method="post" action="{url_for('node_action')}"><input type="hidden" name="node_id" value="{nid}"/><input type="hidden" name="action" value="channel"/><button type="submit">Chan</button></form>
              <form class="inline" method="post" action="{url_for('node_action')}"><input type="hidden" name="node_id" value="{nid}"/><input type="hidden" name="action" value="traceroute"/><button type="submit">Trace</button></form>
              <form class="inline" method="post" action="{url_for('node_action')}"><input type="hidden" name="node_id" value="{nid}"/><input type="hidden" name="action" value="menu"/><button type="submit">Menu</button></form>
              <form class="inline" method="post" action="{url_for('node_action')}">
                <input type="hidden" name="node_id" value="{nid}"/>
                <input type="hidden" name="action" value="weather"/>
                <select name="wx_mode" title="WX type">
                  <option value="watch" selected>watch</option>
                  <option value="now">now</option>
                  <option value="wind">wind</option>
                  <option value="rain">rain</option>
                  <option value="week">week</option>
                </select>
                <select name="wx_place" title="Place">
                  <option value="reno">reno</option>
                  <option value="brc" selected>brc</option>
                  <option value="both">both</option>
                </select>
                <button type="submit">WX</button>
              </form>
            </div>
            """
            power = _fmt_power(n.get("battery"), n.get("voltage"))
            up = _fmt_uptime(n.get("uptime_sec"))
            tr.append(
                "<tr>"
                f"<td class='who' title='{nid}'>{star}"
                f"<a href=\"{detail_href}\">{disp}</a></td>"
                f"<td class='clip' title='{long_n}'>{long_n}</td>"
                f"<td>{short_n}</td>"
                f"<td class='who'>{iam}</td>"
                f"<td title=\"{device_title}\">{device}</td>"
                f"<td>{label_form}</td>"
                f"<td>{snr_html}</td>"
                f"<td>{hops_s}</td>"
                f"<td title='{_fmt_ts(n.get('last_heard'))}'>{_ago(n.get('last_heard'))}</td>"
                f"<td>{power}</td>"
                f"<td>{up}</td>"
                f"<td>{act}</td>"
                "</tr>"
            )

        fav_link = url_for(
            "nodes_page",
            sort=sort,
            order=order,
            q=q or None,
            fav=None if fav_only else "1",
        )
        body = f"""
        <h2>Mesh nodes ({len(nodes)})</h2>
        <form class="filters" method="get" action="{url_for('nodes_page')}">
          <label>Filter<br/><input type="text" name="q" value="{q}" placeholder="name, device, note, !id"/></label>
          <input type="hidden" name="sort" value="{sort}"/>
          <input type="hidden" name="order" value="{order}"/>
          {"<input type='hidden' name='fav' value='1'/>" if fav_only else ""}
          <button type="submit">Apply</button>
          <a class="muted" href="{url_for('nodes_page')}">Clear</a>
          <a href="{fav_link}">{"Show all" if fav_only else "★ Favorites"}</a>
        </form>
        <p class="muted">Long = Meshtastic long name. Short = 4-char radio name. IAM = name they set with the bot (<code>iam …</code>). Click a name (or Info) for details.</p>
        <table>
          <thead><tr>
            <th><a href="{sort_link('name')}">Who</a></th>
            <th>Long</th>
            <th>Short</th>
            <th>IAM</th>
            <th><a href="{sort_link('device')}">Device</a></th>
            <th><a href="{sort_link('fav')}">Alias / note</a></th>
            <th><a href="{sort_link('snr')}">SNR</a></th>
            <th><a href="{sort_link('hops')}">Hops</a></th>
            <th><a href="{sort_link('last_heard')}">Heard</a></th>
            <th><a href="{sort_link('batt')}">Power</a></th>
            <th>Uptime</th>
            <th>Actions</th>
          </tr></thead>
          <tbody>
        """ + "".join(tr) + "</tbody></table>"
        return render(body)

    @app.get("/node/<path:node_id>")
    def node_detail(node_id: str) -> str:
        node_id = (node_id or "").strip()
        labels = store.labels_map()
        n = next((x for x in get_mesh_nodes() if x.get("node_id") == node_id), None)
        if not n:
            return redirect(url_for("nodes_page", flash=f"Node {node_id} not in mesh DB yet"))
        lab = labels.get(node_id) or {}
        display = Store.display_name(
            lab,
            mesh_short=n.get("short_name"),
            mesh_long=n.get("long_name"),
            node_id=node_id,
        )
        iam = (lab.get("user_label") or "").strip()

        def row(label: str, value: Any) -> str:
            if value is None or value == "":
                val = "—"
            else:
                val = str(value).replace("<", "&lt;")
            return f"<tr><th style='width:160px'>{label}</th><td class='wrap'>{val}</td></tr>"

        lat, lon = n.get("latitude"), n.get("longitude")
        pos = "—"
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            alt = n.get("altitude")
            pos = f"{lat:.5f}, {lon:.5f}"
            if isinstance(alt, (int, float)):
                pos += f" · {int(alt)} m"
            pos = (
                f'{pos} · <a href="https://maps.google.com/?q={lat},{lon}" '
                f'target="_blank" rel="noopener">map</a>'
            )

        pk = n.get("public_key") or ""
        pk_show = (pk[:20] + "…") if len(str(pk)) > 24 else (pk or "—")

        ch_util = n.get("channel_util")
        air = n.get("air_util_tx")
        util = "—"
        bits = []
        if isinstance(ch_util, (int, float)):
            bits.append(f"ch {ch_util:.1f}%")
        if isinstance(air, (int, float)):
            bits.append(f"airTx {air:.1f}%")
        if bits:
            util = " · ".join(bits)

        body = f"""
        <p class="muted"><a href="{url_for('nodes_page')}">← Nodes</a></p>
        <h2>{(display or '—').replace('<','&lt;')}</h2>
        <p class="sub"><code>{node_id.replace('<','&lt;')}</code></p>
        <div class="panel" style="max-width:640px;padding:0;overflow:hidden">
          <table>
            <tbody>
              {row("Device", n.get("device") or friendly_hw_model(n.get("hw_model")))}
              {row("hwModel", n.get("hw_model") or "—")}
              {row("Role", n.get("role") or "—")}
              {row("Long name", n.get("long_name") or "—")}
              {row("Short / radio", n.get("short_name") or "—")}
              {row("IAM (bot)", iam or "—")}
              {row("Alias", lab.get("alias") or "—")}
              {row("Note", lab.get("note") or "—")}
              {row("MAC", n.get("macaddr") or "—")}
              {row("Public key", pk_show)}
              {row("SNR", f"{n['snr']:.1f} dB" if isinstance(n.get('snr'), (int, float)) else "—")}
              {row("Hops away", n.get("hops_away") if n.get("hops_away") is not None else "—")}
              {row("Channel idx", n.get("channel") if n.get("channel") is not None else "—")}
              {row("Last heard", f"{_fmt_ts(n.get('last_heard'))} ({_ago(n.get('last_heard'))})")}
              {row("Power", _fmt_power(n.get("battery"), n.get("voltage")))}
              {row("Uptime", _fmt_uptime(n.get("uptime_sec")))}
              {row("Util", util)}
              <tr><th>Position</th><td class="wrap">{pos}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="actions" style="margin-top:14px;flex-wrap:wrap;gap:6px">
          <form class="inline" method="post" action="{url_for('node_action')}">
            <input type="hidden" name="node_id" value="{node_id}"/>
            <input type="hidden" name="action" value="invite"/>
            <button type="submit">Invite</button>
          </form>
          <form class="inline" method="post" action="{url_for('node_action')}">
            <input type="hidden" name="node_id" value="{node_id}"/>
            <input type="hidden" name="action" value="channel"/>
            <button type="submit">Chan</button>
          </form>
          <form class="inline" method="post" action="{url_for('node_action')}">
            <input type="hidden" name="node_id" value="{node_id}"/>
            <input type="hidden" name="action" value="traceroute"/>
            <button type="submit">Trace</button>
          </form>
          <form class="inline" method="post" action="{url_for('node_action')}">
            <input type="hidden" name="node_id" value="{node_id}"/>
            <input type="hidden" name="action" value="menu"/>
            <button type="submit">Menu</button>
          </form>
          <form class="inline" method="post" action="{url_for('node_action')}">
            <input type="hidden" name="node_id" value="{node_id}"/>
            <input type="hidden" name="action" value="weather"/>
            <select name="wx_mode"><option value="watch" selected>watch</option>
              <option value="now">now</option><option value="wind">wind</option><option value="rain">rain</option>
              <option value="week">week</option></select>
            <select name="wx_place"><option value="reno">reno</option>
              <option value="brc" selected>brc</option><option value="both">both</option></select>
            <button type="submit">WX</button>
          </form>
          <a class="btn" href="{url_for('ringtones_page')}">Ringtones</a>
        </div>
        """
        return render(body)

    @app.post("/nodes/action")
    def node_action():
        node_id = (request.form.get("node_id") or "").strip()
        action = (request.form.get("action") or "").strip()
        note = (request.form.get("note") or "").strip()
        msg = push_to_node(
            node_id,
            action,
            note,
            wx_mode=request.form.get("wx_mode"),
            wx_place=request.form.get("wx_place"),
        )
        if action == "traceroute":
            return redirect(url_for("traceroute_page", flash=msg))
        nxt = (request.form.get("next") or "").strip()
        dest = "users_page" if nxt == "users" else "nodes_page"
        return redirect(url_for(dest, flash=msg))

    @app.post("/nodes/label")
    def node_label():
        node_id = (request.form.get("node_id") or "").strip()
        if not node_id:
            return redirect(url_for("nodes_page", flash="Missing node id"))
        alias = request.form.get("alias")
        note = request.form.get("note")
        favorite = request.form.get("favorite") == "1"
        # Empty string clears; form always sends fields
        store.set_admin_label(
            node_id,
            alias=alias if alias is not None else "",
            note=note if note is not None else "",
            favorite=favorite,
        )
        shown = (alias or "").strip() or node_id
        return redirect(url_for("nodes_page", flash=f"Saved label for {shown}"))

    @app.get("/traceroute")
    def traceroute_page() -> str:
        rows = get_traceroute_log()
        tr = []
        for r in rows:
            ok = r.get("ok")
            status = "ok" if ok else "bad"
            toward = (r.get("toward") or "—").replace("<", "&lt;")
            back = (r.get("back") or "").replace("<", "&lt;")
            err = (r.get("error") or "").replace("<", "&lt;")
            back_html = f"<div class='muted'>back: {back}</div>" if back else ""
            err_html = f"<div class='bad'>{err}</div>" if err and not ok else ""
            tr.append(
                "<tr>"
                f"<td>{_fmt_ts(r.get('ts'))}<br/><span class='muted'>{_ago(r.get('ts'))} ago</span></td>"
                f"<td><code>{r.get('target')}</code></td>"
                f"<td class='{status}'>{'OK' if ok else 'FAIL'}</td>"
                f"<td style='font-size:0.88rem'>{toward}{back_html}{err_html}</td>"
                "</tr>"
            )
        body = f"""
        <h2>Traceroute</h2>
        <p class="muted">From Nodes, hit <b>Trace</b> on a target. Uses LoRa airtime; wait 15–60s then refresh this page. Only works to nodes that share your channel key.</p>
        <p><a href="{url_for('traceroute_page')}">Refresh</a> · <a href="{url_for('nodes_page')}">Back to nodes</a></p>
        <table>
          <thead><tr><th>When</th><th>Target</th><th>Result</th><th>Path (SNR per hop)</th></tr></thead>
          <tbody>
        """ + ("".join(tr) or "<tr><td colspan='4' class='muted'>No traces yet</td></tr>") + "</tbody></table>"
        return render(body)

    @app.get("/radio")
    def radio_log_page() -> str:
        labels = store.labels_map()
        mesh_by_id = {n.get("node_id"): n for n in get_mesh_nodes()}
        users_by_id = {u["node_id"]: u for u in store.list_users()}
        rows = get_radio_log()
        tr = []
        for r in rows:
            d = r.get("dir") or ""
            cls = f"dir-{d}" if d in ("rx", "tx", "sys") else ""
            summ = (r.get("summary") or "").replace("<", "&lt;")
            nid = r.get("node_id") or ""
            if nid:
                mesh = mesh_by_id.get(nid) or {}
                user = users_by_id.get(nid) or {}
                lab = labels.get(nid)
                short = mesh.get("short_name") or user.get("short_name")
                long = mesh.get("long_name")
                who = radio_name(mesh, nid, stored_short=short).replace("<", "&lt;")
                tip_bits = [nid]
                if lab and lab.get("user_label"):
                    tip_bits.append(f"iam:{lab['user_label']}")
                if lab and lab.get("alias"):
                    tip_bits.append(f"alias:{lab['alias']}")
                if short:
                    tip_bits.append(f"short:{short}")
                if long:
                    tip_bits.append(f"long:{long}")
                tip = " · ".join(tip_bits).replace("'", "")
            else:
                who = "—"
                tip = ""
            tr.append(
                "<tr>"
                f"<td title='{_fmt_ts(r.get('ts'))}'>{_ago(r.get('ts'))}</td>"
                f"<td class='{cls}'>{d.upper()}</td>"
                f"<td class='who' title='{tip}'>{who}</td>"
                f"<td class='clip' title='{summ}'>{summ}</td>"
                "</tr>"
            )
        body = f"""
        <h2>Radio log</h2>
        <p class="muted">Last ~200 RX/TX. Who shows the radio long name and short name. Hover for the node ID and saved admin labels. Auto-refresh 5s.</p>
        <table>
          <thead><tr><th>When</th><th>Dir</th><th>Who</th><th class="wrap">Summary</th></tr></thead>
          <tbody>
        """ + ("".join(tr) or "<tr><td colspan='4' class='muted'>Quiet so far</td></tr>") + """
          </tbody>
        </table>
        <script>setTimeout(() => location.reload(), 5000);</script>
        """
        return render(body)

    @app.get("/metaldetect")
    def metaldetect_log_page() -> str:
        labels = store.labels_map()
        mesh_by_id = {n.get("node_id"): n for n in get_mesh_nodes()}
        users_by_id = {u["node_id"]: u for u in store.list_users()}
        rows = store.list_channel_log(channel="MetalDetect", limit=300)
        tr = []
        for r in rows:
            nid = r.get("node_id") or ""
            mesh = mesh_by_id.get(nid) or {}
            who = radio_name(mesh, nid, stored_short=(users_by_id.get(nid) or {}).get("short_name")).replace("<", "&lt;")
            txt = (r.get("text") or "").replace("<", "&lt;")
            d = (r.get("direction") or "rx").upper()
            cls = f"dir-{r.get('direction') or 'rx'}"
            tr.append(
                "<tr>"
                f"<td>{_ago(r.get('ts'))}</td>"
                f"<td class='mono'>{_fmt_ts(r.get('ts'))}</td>"
                f"<td class='{cls}'>{d}</td>"
                f"<td class='who' title='{nid}'>{who}</td>"
                f"<td class='wrap'>{txt}</td>"
                "</tr>"
            )
        body = f"""
        <h2>MetalDetect channel</h2>
        <p class="muted" style="max-width:720px;line-height:1.45">
          Rolling log of private camp channel texts (saved). Bot chat stays DM-only;
          use the forms below for one-shot announces from this computer.
        </p>
        <div class="panel">
          <form method="post" action="{url_for('channel_announce')}" style="margin-bottom:12px">
            <input type="hidden" name="channel" value="MetalDetect"/>
            <p><label>Announce to MetalDetect<br/>
            <input type="text" name="text" maxlength="200" style="width:min(100%,520px)" placeholder="Meetup at shade in 10 min"/></label>
            <button type="submit">Send</button></p>
          </form>
          <form method="post" action="{url_for('channel_weather')}" class="inline" style="gap:8px;align-items:end">
            <input type="hidden" name="channel" value="MetalDetect"/>
            <label>WX type
              <select name="wx_mode">
                <option value="watch" selected>watch</option>
                <option value="now">now</option>
                <option value="wind">wind</option>
                <option value="rain">rain</option>
                <option value="week">week</option>
              </select>
            </label>
            <label>Place
              <select name="wx_place">
                <option value="reno">reno</option>
                <option value="brc" selected>brc</option>
                <option value="both">both</option>
              </select>
            </label>
            <button type="submit">Push WX → MetalDetect</button>
          </form>
        </div>
        <table>
          <thead><tr><th>When</th><th>Timestamp</th><th>Dir</th><th>Who</th><th class="wrap">Message</th></tr></thead>
          <tbody>
        """ + ("".join(tr) or "<tr><td colspan='5' class='muted'>No MetalDetect messages yet.</td></tr>") + """
          </tbody>
        </table>
        <script>setTimeout(() => location.reload(), 8000);</script>
        """
        return render(body)

    @app.post("/channel/announce")
    def channel_announce():
        ch = (request.form.get("channel") or "MetalDetect").strip()
        text = (request.form.get("text") or "").strip()
        msg = send_on_channel(text, channel=ch)
        dest = "metaldetect_log_page" if ch.lower() != "public" else "public_log_page"
        return redirect(url_for(dest, flash=msg))

    @app.post("/channel/weather")
    def channel_weather():
        ch = (request.form.get("channel") or "MetalDetect").strip()
        mode = (request.form.get("wx_mode") or "watch").strip()
        place = (request.form.get("wx_place") or "brc").strip()
        msg = announce_weather(mode=mode, place=place, channel=ch)
        dest = "metaldetect_log_page" if ch.lower() != "public" else "public_log_page"
        return redirect(url_for(dest, flash=msg))

    @app.get("/public")
    def public_log_page() -> str:
        labels = store.labels_map()
        mesh_by_id = {n.get("node_id"): n for n in get_mesh_nodes()}
        users_by_id = {u["node_id"]: u for u in store.list_users()}
        rows = store.list_channel_log(channel="Public", limit=300)
        tr = []
        for r in rows:
            nid = r.get("node_id") or ""
            mesh = mesh_by_id.get(nid) or {}
            who = radio_name(mesh, nid, stored_short=(users_by_id.get(nid) or {}).get("short_name")).replace("<", "&lt;")
            txt = (r.get("text") or "").replace("<", "&lt;")
            d = (r.get("direction") or "rx").upper()
            cls = f"dir-{r.get('direction') or 'rx'}"
            tr.append(
                "<tr>"
                f"<td>{_ago(r.get('ts'))}</td>"
                f"<td class='mono'>{_fmt_ts(r.get('ts'))}</td>"
                f"<td class='{cls}'>{d}</td>"
                f"<td class='who' title='{nid}'>{who}</td>"
                f"<td class='wrap'>{txt}</td>"
                "</tr>"
            )
        body = f"""
        <h2>Public primary channel</h2>
        <p class="muted" style="max-width:720px;line-height:1.45">
          Broadcasts on the public primary only (Everyone / LongFast / whatever the camp uses).
          Direct messages and Gemini queries are <a href="{url_for('dms_page')}">DMs</a> and
          <a href="{url_for('queries_page')}">Queries</a> — not here.
        </p>
        <div class="panel">
          <form method="post" action="{url_for('channel_announce')}">
            <input type="hidden" name="channel" value="public"/>
            <p><label>Announce on public primary (use sparingly)<br/>
            <input type="text" name="text" maxlength="180" style="width:min(100%,520px)" placeholder="Short public note"/>
            </label>
            <button type="submit">Send</button></p>
          </form>
        </div>
        <table>
          <thead><tr><th>When</th><th>Timestamp</th><th>Dir</th><th>Who</th><th class="wrap">Message</th></tr></thead>
          <tbody>
        """ + ("".join(tr) or "<tr><td colspan='5' class='muted'>Quiet on primary so far.</td></tr>") + """
          </tbody>
        </table>
        <script>setTimeout(() => location.reload(), 8000);</script>
        """
        return render(body)

    @app.get("/dms")
    def dms_page() -> str:
        labels = store.labels_map()
        mesh_by_id = {n.get("node_id"): n for n in get_mesh_nodes()}
        users_by_id = {u["node_id"]: u for u in store.list_users()}
        rows = store.list_channel_log(channel="DM", limit=300)
        tr = []
        for r in rows:
            nid = r.get("node_id") or ""
            mesh = mesh_by_id.get(nid) or {}
            who = radio_name(mesh, nid, stored_short=(users_by_id.get(nid) or {}).get("short_name")).replace("<", "&lt;")
            txt = (r.get("text") or "").replace("<", "&lt;")
            d = (r.get("direction") or "rx").upper()
            cls = f"dir-{r.get('direction') or 'rx'}"
            ack = _fmt_ack(r.get("direction"), r.get("ack_status"))
            tr.append(
                "<tr>"
                f"<td>{_ago(r.get('ts'))}</td>"
                f"<td class='mono'>{_fmt_ts(r.get('ts'))}</td>"
                f"<td class='{cls}'>{d}</td>"
                f"<td>{ack}</td>"
                f"<td class='who' title='{nid}'>{who}</td>"
                f"<td class='wrap'>{txt}</td>"
                "</tr>"
            )
        body = f"""
        <h2>Direct messages</h2>
        <p class="muted" style="max-width:720px;line-height:1.45">
          Bot DMs only (unlock, menu, Gemini). <b>Ack</b> = mesh routing ACK after bot TX
          (<code>wantAck</code> — not “read on phone”). Search/answers also land in
          <a href="{url_for('queries_page')}">Queries</a>. Public channel stays on
          <a href="{url_for('public_log_page')}">Public log</a>.
        </p>
        <table>
          <thead><tr><th>When</th><th>Timestamp</th><th>Dir</th><th>Ack</th><th>Who</th><th class="wrap">Message</th></tr></thead>
          <tbody>
        """ + ("".join(tr) or "<tr><td colspan='6' class='muted'>No DMs logged yet.</td></tr>") + """
          </tbody>
        </table>
        <script>setTimeout(() => location.reload(), 8000);</script>
        """
        return render(body)

    @app.get("/canned")
    def canned_page() -> str:
        from bot.canned import CANNED

        cards = []
        for item in CANNED:
            body_esc = (item.get("body") or "").replace("<", "&lt;")
            title = (item.get("title") or item["id"]).replace("<", "&lt;")
            ch = item.get("channel") or "MetalDetect"
            cards.append(
                f"""
                <div class="panel">
                  <h3 style="margin:0 0 8px;font-size:1rem">{title}</h3>
                  <p class="muted wrap" style="white-space:normal">{body_esc}</p>
                  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
                    <form method="post" action="{url_for('canned_send')}">
                      <input type="hidden" name="canned_id" value="{item['id']}"/>
                      <input type="hidden" name="channel" value="MetalDetect"/>
                      <button type="submit">Send to MetalDetect</button>
                    </form>
                    <form method="post" action="{url_for('canned_send')}"
                          onsubmit="return confirm('Send this to the PUBLIC primary mesh (not just camp)?');">
                      <input type="hidden" name="canned_id" value="{item['id']}"/>
                      <input type="hidden" name="channel" value="public"/>
                      <button type="submit">Send to public channel</button>
                    </form>
                  </div>
                </div>
                """
            )
        body = f"""
        <h2>Canned announces</h2>
        <p class="muted" style="max-width:720px">One-tap camp blasts. Long text is split for LoRa. Prefer MetalDetect over public.</p>
        {''.join(cards)}
        <div class="panel">
          <h3 style="margin:0 0 8px;font-size:1rem">Custom</h3>
          <form method="post" action="{url_for('channel_announce')}">
            <p><label>Channel
              <select name="channel">
                <option value="MetalDetect" selected>MetalDetect</option>
                <option value="public">Public primary</option>
              </select>
            </label></p>
            <p><label>Message<br/>
              <textarea name="text" rows="4" maxlength="600" style="width:min(100%,560px);font:inherit"></textarea>
            </label></p>
            <button type="submit">Send</button>
          </form>
        </div>
        """
        return render(body)

    @app.post("/canned/send")
    def canned_send():
        from bot.canned import CANNED

        cid = (request.form.get("canned_id") or "").strip()
        ch = (request.form.get("channel") or "MetalDetect").strip()
        item = next((x for x in CANNED if x["id"] == cid), None)
        if not item:
            return redirect(url_for("canned_page", flash="Unknown canned message"))
        msg = send_on_channel(item["body"], channel=ch)
        return redirect(url_for("canned_page", flash=msg))

    @app.get("/ringtones")
    def ringtones_page() -> str:
        from bot.ringtones import list_presets

        presets = list_presets()
        labels = store.labels_map()
        node_opts = ['<option value="">— pick a radio —</option>']
        for n in sorted(
            get_mesh_nodes(),
            key=lambda x: (
                Store.display_name(
                    labels.get(x.get("node_id") or ""),
                    mesh_short=x.get("short_name"),
                    mesh_long=x.get("long_name"),
                    node_id=x.get("node_id"),
                )
                or ""
            ).lower(),
        ):
            nid = n.get("node_id") or ""
            if not nid:
                continue
            name = Store.display_name(
                labels.get(nid),
                mesh_short=n.get("short_name"),
                mesh_long=n.get("long_name"),
                node_id=nid,
            )
            esc_n = (
                str(name)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace('"', "&quot;")
            )
            node_opts.append(f'<option value="{nid}">{esc_n} ({nid})</option>')
        opts_html = "".join(node_opts)

        def _esc_attr(s: str) -> str:
            return (
                s.replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
            )

        def _esc_html(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;")

        load_action = url_for("ringtone_load")
        row_bits: list[str] = []
        for p in presets:
            attr = _esc_attr(p["rtttl"])
            q = _esc_attr(f"{p['name'].lower()} {p['id']}")
            row_bits.append(
                f"<tr class='rt-row' data-q=\"{q}\">"
                f"<td><b>{_esc_html(p['name'])}</b><br/>"
                f"<span class='muted'>{_esc_html(p['id'])} · {p['chars']}/230</span></td>"
                f"<td class='wrap'><code class='rt-code'>{_esc_html(p['rtttl'])}</code></td>"
                "<td class='actions'>"
                f"<button type='button' class='rt-play' data-rtttl=\"{attr}\">▶ preview</button> "
                f"<button type='button' class='rt-use' data-rtttl=\"{attr}\">use ↓</button> "
                f"<form class='inline' method='post' action=\"{load_action}\">"
                f"<input type='hidden' name='preset' value=\"{_esc_attr(p['id'])}\"/>"
                "<input type='hidden' name='node_id' class='rt-node-mirror' value=''/>"
                "<button type='submit' name='target' value='node' "
                "title='DM how-to + RTTTL string to selected radio'>DM → radio</button> "
                "<button type='submit' name='target' value='gateway' "
                "title='Writes to Heltec gateway only'>→ Heltec</button>"
                "</form></td></tr>"
            )
        rows = "".join(row_bits)
        body = f"""
        <h2>RTTTL ringtones</h2>
        <div class="panel" style="max-width:780px">
          <h3 style="margin:0 0 8px;font-size:0.95rem">How to put one on your SenseCap T1000-E</h3>
          <ol style="margin:0;padding-left:1.2rem;line-height:1.5;max-width:720px">
            <li><b>▶ preview</b> a tone in this browser.</li>
            <li>Pick your radio in <b>Target radio</b>, then click <b>DM → radio</b>.
              You get one DM with the RTTTL string — long-press copy it.</li>
            <li>Paste into <b>Radio → External Notification → Ringtone</b>.</li>
            <li><b>Nag Timeout must be 0</b> (once only).</li>
            <li>Test with a DM that has 🔔.</li>
          </ol>
          <p class="muted" style="margin:10px 0 0;line-height:1.45">
            No remote admin needed — just DM + copy/paste.
            <b>→ Heltec</b> still writes the gateway radio directly (optional).
            Max length <b>230</b> chars. SFX only.
          </p>
        </div>

        <div class="panel">
          <form method="post" action="{url_for('ringtone_load')}" id="rt-form">
            <p class="inline" style="gap:10px;align-items:end;margin:0 0 12px">
              <label>Target radio<br/>
              <select name="node_id" id="rt-node" style="min-width:220px">{opts_html}</select></label>
              <span class="muted" style="padding-bottom:6px">Used by every “DM → radio” button</span>
            </p>
            <p><label>Custom / selected RTTTL<br/>
            <textarea id="rt-text" name="rtttl" rows="3" maxlength="230"
              style="width:min(100%,720px);font-family:ui-monospace,monospace"
              placeholder="MetDet:d=16,o=5,b=140:8c6,8e6,8g6,4c7"></textarea></label></p>
            <p class="inline" style="gap:8px;flex-wrap:wrap">
              <button type="button" id="rt-preview-custom">▶ preview</button>
              <button type="button" id="rt-copy">Copy</button>
              <button type="submit" name="target" value="node">DM → selected radio</button>
              <button type="submit" name="target" value="gateway">Set on Heltec gateway</button>
              <span class="muted" id="rt-len">0/230</span>
            </p>
          </form>
        </div>

        <p class="filters" style="align-items:center">
          <label>Filter
            <input type="search" id="rt-filter" placeholder="batman, nokia, star…" style="min-width:200px"/>
          </label>
          <span class="muted">{len(presets)} presets · click ▶ to hear</span>
        </p>
        <table>
          <thead><tr><th>Preset</th><th>RTTTL</th><th>Preview / load</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <script>
        (function() {{
          const NOTE_FREQ = {{
            c:16.35,'c#':17.32,d:18.35,'d#':19.45,e:20.60,f:21.83,'f#':23.12,
            g:24.50,'g#':25.96,a:27.50,'a#':29.14,b:30.87
          }};
          let ctx = null, stopTimer = null, active = [];
          function ensureCtx() {{
            if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
            if (ctx.state === 'suspended') ctx.resume();
            return ctx;
          }}
          function stopAll() {{
            active.forEach(o => {{ try {{ o.stop(); }} catch(e) {{}} }});
            active = [];
            if (stopTimer) clearTimeout(stopTimer);
            stopTimer = null;
          }}
          function parseRtttl(str) {{
            const parts = String(str||'').trim().split(':');
            if (parts.length < 3) throw new Error('need Name:defaults:notes');
            const defs = {{ d:4, o:6, b:63 }};
            (parts[1]||'').split(',').forEach(kv => {{
              const [k,v] = kv.split('=');
              if (k && v) defs[k.trim()] = parseInt(v,10);
            }});
            const notes = [];
            (parts[2]||'').split(',').forEach(tok => {{
              tok = tok.trim();
              if (!tok) return;
              const m = tok.match(/^(\\d+)?([a-gp]#?)(\\.?)(\\d+)?$/i);
              if (!m) return;
              const durNum = m[1] ? parseInt(m[1],10) : defs.d;
              let beats = 1 / durNum;
              if (m[3]) beats *= 1.5;
              const ms = (beats * 4 * 60 * 1000) / defs.b;
              const note = m[2].toLowerCase();
              const oct = m[4] ? parseInt(m[4],10) : defs.o;
              if (note === 'p') {{ notes.push({{ freq:0, ms }}); return; }}
              const base = NOTE_FREQ[note];
              if (!base) return;
              notes.push({{ freq: base * Math.pow(2, oct), ms }});
            }});
            return notes;
          }}
          function playRtttl(str) {{
            stopAll();
            let notes;
            try {{ notes = parseRtttl(str); }}
            catch(e) {{ alert('Bad RTTTL: ' + e.message); return; }}
            if (!notes.length) {{ alert('No playable notes'); return; }}
            const ac = ensureCtx();
            let t = ac.currentTime + 0.05;
            notes.forEach(n => {{
              const dur = Math.max(0.02, n.ms / 1000);
              if (n.freq > 0) {{
                const o = ac.createOscillator();
                const g = ac.createGain();
                o.type = 'square';
                o.frequency.value = n.freq;
                g.gain.setValueAtTime(0.0001, t);
                g.gain.exponentialRampToValueAtTime(0.12, t + 0.01);
                g.gain.exponentialRampToValueAtTime(0.0001, t + dur * 0.9);
                o.connect(g); g.connect(ac.destination);
                o.start(t); o.stop(t + dur);
                active.push(o);
              }}
              t += dur;
            }});
            stopTimer = setTimeout(stopAll, (t - ac.currentTime) * 1000 + 50);
          }}

          const ta = document.getElementById('rt-text');
          const len = document.getElementById('rt-len');
          const nodeSel = document.getElementById('rt-node');
          function syncLen() {{
            const n = (ta.value||'').length;
            len.textContent = n + '/230';
            len.style.color = n > 230 ? 'var(--danger)' : '';
          }}
          function syncNodeMirrors() {{
            document.querySelectorAll('.rt-node-mirror').forEach(el => {{
              el.value = nodeSel.value || '';
            }});
          }}
          ta.addEventListener('input', syncLen);
          nodeSel.addEventListener('change', syncNodeMirrors);
          syncLen(); syncNodeMirrors();

          document.getElementById('rt-preview-custom').addEventListener('click', () => {{
            if (!ta.value.trim()) {{ alert('Paste or pick a ringtone first'); return; }}
            playRtttl(ta.value);
          }});
          document.getElementById('rt-copy').addEventListener('click', async () => {{
            if (!ta.value.trim()) return;
            try {{
              await navigator.clipboard.writeText(ta.value.trim());
              alert('Copied — paste into Meshtastic app → External Notification → Ringtone');
            }} catch(e) {{
              ta.select(); document.execCommand('copy');
              alert('Selected — press ⌘C / Ctrl+C to copy');
            }}
          }});
          document.querySelectorAll('.rt-play').forEach(btn => {{
            btn.addEventListener('click', () => playRtttl(btn.getAttribute('data-rtttl')));
          }});
          document.querySelectorAll('.rt-use').forEach(btn => {{
            btn.addEventListener('click', () => {{
              ta.value = btn.getAttribute('data-rtttl') || '';
              syncLen();
              ta.scrollIntoView({{ behavior:'smooth', block:'center' }});
            }});
          }});
          document.getElementById('rt-filter').addEventListener('input', (e) => {{
            const q = (e.target.value || '').toLowerCase().trim();
            document.querySelectorAll('.rt-row').forEach(tr => {{
              tr.style.display = !q || (tr.getAttribute('data-q')||'').includes(q) ? '' : 'none';
            }});
          }});
          document.querySelectorAll('form.inline').forEach(f => {{
            f.addEventListener('submit', () => syncNodeMirrors());
          }});
        }})();
        </script>
        """
        return render(body)

    @app.post("/ringtones/load")
    def ringtone_load():
        from bot.ringtones import get_preset

        preset = (request.form.get("preset") or "").strip()
        rtttl = (request.form.get("rtttl") or "").strip()
        if preset and not rtttl:
            rtttl = get_preset(preset) or ""
        target = (request.form.get("target") or "gateway").strip()
        node_raw = (request.form.get("node_id") or "").strip()
        node_id = None
        if target == "node":
            if not node_raw:
                return redirect(
                    url_for(
                        "ringtones_page",
                        flash="Pick your radio in the Target radio dropdown first, then click DM → radio",
                    )
                )
            nid, _ = store.resolve_label_token(node_raw)
            if not nid:
                low = node_raw.lower()
                for n in get_mesh_nodes():
                    if (n.get("node_id") or "").lower() == low:
                        nid = n["node_id"]
                        break
                    if (n.get("short_name") or "").lower() == low:
                        nid = n["node_id"]
                        break
            node_id = nid or (node_raw if node_raw.startswith("!") else None)
            if not node_id:
                return redirect(url_for("ringtones_page", flash=f"Can't find node '{node_raw}'"))
        msg = set_ringtone(rtttl, node_id=node_id)
        return redirect(url_for("ringtones_page", flash=msg))

    @app.get("/map")
    def map_page() -> str:
        labels = store.labels_map()
        nodes = [
            n
            for n in get_mesh_nodes()
            if n.get("latitude") is not None and n.get("longitude") is not None
        ]
        payload = [
            {
                "id": n.get("node_id"),
                "name": Store.display_name(
                    labels.get(n.get("node_id") or ""),
                    mesh_short=n.get("short_name"),
                    mesh_long=n.get("long_name"),
                    node_id=n.get("node_id"),
                ),
                "lat": n.get("latitude"),
                "lon": n.get("longitude"),
                "snr": n.get("snr"),
                "last": _ago(n.get("last_heard")),
            }
            for n in nodes
        ]
        data = json.dumps(payload)
        body = f"""
        <h2>Mesh map ({len(nodes)} with GPS)</h2>
        <p class="muted">Only nodes that have shared a position. Empty if nobody is beaconing GPS.</p>
        <div id="map"></div>
        <script>
          const nodes = {data};
          const map = L.map('map').setView([40.786, -119.206], 11);
          L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 18,
            attribution: '&copy; OpenStreetMap'
          }}).addTo(map);
          const bounds = [];
          for (const n of nodes) {{
            const m = L.marker([n.lat, n.lon]).addTo(map);
            m.bindPopup(`<b>${{n.name}}</b><br/><code>${{n.id}}</code><br/>SNR ${{n.snr ?? '—'}} · ${{n.last}} ago`);
            bounds.push([n.lat, n.lon]);
          }}
          if (bounds.length) map.fitBounds(bounds, {{ padding: [30, 30] }});
        </script>
        """
        return render(body, include_leaflet=True)

    @app.get("/users")
    def users_page() -> str:
        rows = store.list_users()
        labels = store.labels_map()
        mesh_nodes = get_mesh_nodes()
        mesh_by_id = {n.get("node_id"): n for n in mesh_nodes}
        mesh_ids = set(mesh_by_id.keys())
        audit = store.audit_users(mesh_ids)
        sun_all = {s["node_id"]: s for s in store.list_subscriptions("sun", enabled_only=False)}
        alert_all = {
            s["node_id"]: s for s in store.list_subscriptions("alert", enabled_only=False)
        }
        tr = []
        for r in rows:
            nid = r.get("node_id") or ""
            lab = labels.get(nid) or {}
            mesh = mesh_by_id.get(nid) or {}
            mesh_short = mesh.get("short_name") or r.get("short_name")
            mesh_long = mesh.get("long_name") or ""
            disp = Store.display_name(
                lab, mesh_short=mesh_short, mesh_long=mesh_long, node_id=nid
            )
            star = '<span class="fav">★</span>' if lab.get("favorite") else ""
            power = _fmt_power(mesh.get("battery"), mesh.get("voltage"))
            up = _fmt_uptime(mesh.get("uptime_sec"))
            alert = alert_all.get(nid) or {}
            sun = sun_all.get(nid) or {}
            alert_on = bool(alert.get("enabled"))
            sun_on = bool(sun.get("enabled"))
            alert_place = alert.get("place") or "brc"
            sun_place = sun.get("place") or "brc"

            def sub_controls(kind: str, on: bool, place: str) -> str:
                on_val = "1" if on else "0"
                return f"""
                <form class="inline" method="post" action="{url_for('set_sub')}">
                  <input type="hidden" name="node_id" value="{nid}"/>
                  <input type="hidden" name="kind" value="{kind}"/>
                  <input type="hidden" name="next" value="users"/>
                  <input type="hidden" name="current_enabled" value="{on_val}"/>
                  <select name="place">
                    <option value="reno" {"selected" if place=="reno" else ""}>Reno</option>
                    <option value="brc" {"selected" if place=="brc" else ""}>BRC</option>
                    <option value="both" {"selected" if place=="both" else ""}>both</option>
                  </select>
                  <button type="submit" name="save_place" value="1">Save</button>
                  <button name="enabled" value="1" type="submit">{"✓" if on else "ON"}</button>
                  <button name="enabled" value="0" type="submit">OFF</button>
                </form>
                """

            tr.append(
                "<tr>"
                f"<td class='who' title='{nid}'>{star}{disp}"
                f"{(' · '+mesh_short) if mesh_short and mesh_short != disp else ''}</td>"
                f"<td>{power}</td>"
                f"<td>{up}</td>"
                f"<td>{sub_controls('alert', alert_on, alert_place)}</td>"
                f"<td>{sub_controls('sun', sun_on, sun_place)}</td>"
                f"<td class='actions'>"
                f"<form class='inline' method='post' action='{url_for('node_action')}'>"
                f"<input type='hidden' name='node_id' value='{nid}'/>"
                f"<input type='hidden' name='next' value='users'/>"
                f"<button name='action' value='alerts_recheck' type='submit'>Recheck</button>"
                f"<button name='action' value='alerts_setup' type='submit'>Redo</button>"
                f"<button name='action' value='alerts_off' type='submit'>Off + DM</button>"
                f"</form></td>"
                f"<td>{_ago(r.get('last_seen'))}</td>"
                f"<td>{'admin' if r.get('is_admin') else 'user'}</td>"
                f"<td><form class='inline' method='post' action='{url_for('revoke')}'>"
                f"<input type='hidden' name='node_id' value='{nid}'/>"
                f"<button type='submit'>Revoke</button></form></td>"
                "</tr>"
            )
        rec_all = url_for("alerts_recheck_all")
        off_all = url_for("alerts_off_all")
        brc_all = url_for("alerts_place_brc_all")
        hotfix_apply = url_for("playa_hotfix_apply")
        hotfix_dm = url_for("playa_hotfix_notify")
        announce_dm = url_for("announce_update_route")
        revoke_flagged = url_for("revoke_flagged_users_route")

        flagged_rows = []
        for dup in audit.get("duplicates") or []:
            keep = dup.get("keep") or ""
            keep_lab = labels.get(keep) or {}
            keep_name = Store.display_name(
                keep_lab, mesh_short=(mesh_by_id.get(keep) or {}).get("short_name"), node_id=keep
            )
            for nid in dup.get("revoke") or []:
                lab = labels.get(nid) or {}
                disp = Store.display_name(
                    lab,
                    mesh_short=(mesh_by_id.get(nid) or {}).get("short_name"),
                    node_id=nid,
                )
                flagged_rows.append(
                    "<tr>"
                    f"<td><input type='checkbox' name='node_id' value='{nid}' checked/></td>"
                    f"<td class='who' title='{nid}'>{disp.replace('<','&lt;')}</td>"
                    f"<td>duplicate iam</td>"
                    f"<td class='muted'>keep {keep_name.replace('<','&lt;')} <code>{keep}</code></td>"
                    "</tr>"
                )
        for st in audit.get("stale") or []:
            nid = st.get("node_id") or ""
            lab = labels.get(nid) or {}
            u = next((x for x in rows if x.get("node_id") == nid), {})
            disp = Store.display_name(
                lab,
                mesh_short=u.get("short_name"),
                node_id=nid,
            )
            flagged_rows.append(
                "<tr>"
                f"<td><input type='checkbox' name='node_id' value='{nid}' checked/></td>"
                f"<td class='who' title='{nid}'>{disp.replace('<','&lt;')}</td>"
                f"<td>stale</td>"
                f"<td class='muted'>{st.get('reason','')}</td>"
                "</tr>"
            )
        flagged_block = ""
        if flagged_rows:
            flagged_block = f"""
        <h3 style="margin-top:24px">Flagged unlocks</h3>
        <p class="muted" style="max-width:760px">Duplicate <code>iam</code> names (keeps on-mesh / newest) and unlocks
        not heard on mesh in 7+ days. Cleanup removes unlock + alert rows + labels for checked IDs.</p>
        <form method="post" action="{revoke_flagged}"
              onsubmit="return confirm('Remove checked unlock(s) and their subs/labels?');">
          <table>
            <thead><tr><th></th><th>Who</th><th>Why</th><th>Detail</th></tr></thead>
            <tbody>{"".join(flagged_rows)}</tbody>
          </table>
          <p style="margin-top:10px"><button type="submit">Revoke checked</button></p>
        </form>
            """

        interacted_n = len(store.list_interacted_user_ids())
        announce_js = (
            user_update_message()
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", " ")
        )
        body = f"""
        <h2>Users</h2>
        <p class="muted" style="max-width:760px">Unlocked radios. Change place with the dropdown, then <b>Save</b> (keeps ON/OFF as-is).
        <b>Recheck</b> DMs them: KEEP / OFF / SETUP.
        <b>Off + DM</b> unsubscribes and tells them. They can also DM <code>unsubscribe</code>, <code>alerts off</code>, or <code>config</code>.</p>
        <div class="panel" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
          <form method="post" action="{hotfix_apply}"
                onsubmit="return confirm('Set sun+wind alerts ON @ BRC for {interacted_n} interacted user(s)? No DM.');">
            <button type="submit">Playa hotfix (silent)</button>
          </form>
          <form method="post" action="{hotfix_dm}"
                onsubmit="return confirm('Apply BRC alerts ON + DM {interacted_n} interacted user(s)?');">
            <button type="submit">Playa hotfix + DM</button>
          </form>
          <form method="post" action="{announce_dm}"
                onsubmit="return confirm('DM {interacted_n} interacted user(s) (no alert changes): {announce_js}');">
            <button type="submit">Announce update (DM only)</button>
          </form>
          <form method="post" action="{brc_all}"
                onsubmit="return confirm('Set sun + wind/rain place to BRC for everyone with a subscription row?');">
            <button type="submit">Set all subs → BRC</button>
          </form>
          <form method="post" action="{rec_all}"
                onsubmit="return confirm('DM KEEP/OFF/SETUP to everyone currently opted into sun or wind texts?');">
            <button type="submit">Recheck all alert subs</button>
          </form>
          <form method="post" action="{off_all}"
                onsubmit="return confirm('Turn OFF sun/wind texts for everyone opted in, and DM them?');">
            <button type="submit">Unsubscribe all (with DM)</button>
          </form>
        </div>
        {flagged_block}
        <table>
          <thead><tr>
            <th>Who</th>
            <th>Power</th>
            <th>Uptime</th>
            <th>Wind/rain</th>
            <th>Sunrise</th>
            <th>Alerts</th>
            <th>Seen</th>
            <th>Role</th>
            <th></th>
          </tr></thead>
          <tbody>
        """ + "".join(tr) + "</tbody></table>"
        return render(body)

    @app.post("/users/alerts-recheck-all")
    def alerts_recheck_all():
        msg = nudge_alerts_subscribers()
        return redirect(url_for("users_page", flash=msg))

    @app.post("/users/alerts-off-all")
    def alerts_off_all():
        msg = alerts_off_subscribers(notify=True)
        return redirect(url_for("users_page", flash=msg))

    @app.post("/users/alerts-place-brc")
    def alerts_place_brc_all():
        n = store.bulk_set_subscription_place("brc")
        return redirect(
            url_for("users_page", flash=f"Set {n} sun/wind subscription(s) to BRC")
        )

    @app.post("/users/playa-hotfix")
    def playa_hotfix_apply():
        msg = apply_playa_hotfix(notify=False)
        return redirect(url_for("users_page", flash=msg))

    @app.post("/users/playa-hotfix-dm")
    def playa_hotfix_notify():
        msg = apply_playa_hotfix(notify=True)
        return redirect(url_for("users_page", flash=msg))

    @app.post("/users/announce-update")
    def announce_update_route():
        msg = announce_update()
        return redirect(url_for("users_page", flash=msg))

    @app.post("/users/revoke-flagged")
    def revoke_flagged_users_route():
        ids = request.form.getlist("node_id")
        msg = revoke_flagged_users(ids)
        return redirect(url_for("users_page", flash=msg))

    @app.post("/users/revoke")
    def revoke():
        node_id = (request.form.get("node_id") or "").strip()
        if node_id:
            store.revoke_user(node_id)
        return redirect(url_for("users_page", flash=f"Revoked {node_id}"))

    @app.get("/setup")
    def setup_page():
        from bot.setup_wizard import _setup_html, current_form_defaults

        saved = request.args.get("saved") == "1"
        return _setup_html(
            values=current_form_defaults(),
            errors=[],
            saved=saved,
            restart_path="/setup/restart",
            form_action="/setup",
            refresh_path="/setup",
        )

    @app.post("/setup")
    def setup_save():
        from bot.setup_wizard import _setup_html, apply_setup, current_form_defaults

        form = request.form.to_dict()
        if not (form.get("gemini_api_key") or "").strip():
            form["gemini_api_key"] = current_form_defaults().get("gemini_api_key", "")
        errors = apply_setup(form)
        if errors:
            vals = current_form_defaults()
            vals.update({k: form.get(k, vals.get(k, "")) for k in vals})
            if form.get("gemini_api_key"):
                vals["gemini_api_key"] = form["gemini_api_key"]
            return (
                _setup_html(
                    values=vals,
                    errors=errors,
                    saved=False,
                    restart_path="/setup/restart",
                    form_action="/setup",
                    refresh_path="/setup",
                ),
                400,
            )
        return redirect("/setup?saved=1")

    @app.get("/setup/restart")
    def setup_restart_hint():
        return (
            "<p style='font-family:system-ui;padding:2rem'>Settings saved to <code>.env</code>. "
            "Restart the bot to reconnect the radio:<br/><br/>"
            "<code>pm2 restart metdet-mesh-bot</code><br/>or stop/start "
            "<code>python -m bot.main</code>.<br/><br/>"
            "<a href='/'>← Admin</a></p>"
        )

    @app.get("/settings")
    def settings_page() -> str:
        mqtt = mqtt_status()
        labels = store.labels_map()
        users = store.list_users()
        mesh_by_id = {n.get("node_id"): n for n in get_mesh_nodes()}
        sun_all = {s["node_id"]: s for s in store.list_subscriptions("sun", enabled_only=False)}
        alert_all = {
            s["node_id"]: s for s in store.list_subscriptions("alert", enabled_only=False)
        }

        def who_label(nid: str) -> str:
            lab = labels.get(nid) or {}
            mesh = mesh_by_id.get(nid) or {}
            u = next((x for x in users if x.get("node_id") == nid), {})
            return Store.display_name(
                lab,
                mesh_short=mesh.get("short_name") or u.get("short_name"),
                mesh_long=mesh.get("long_name"),
                node_id=nid,
            )

        alert_on = [s for s in alert_all.values() if s.get("enabled")]
        sun_on = [s for s in sun_all.values() if s.get("enabled")]
        alert_rows = "".join(
            f"<tr><td><b>{who_label(s['node_id'])}</b></td><td>{s.get('place')}</td></tr>"
            for s in alert_on
        ) or "<tr><td colspan='2' class='muted'>Nobody ON — manage on <a href='/users'>Users</a></td></tr>"
        sun_rows = "".join(
            f"<tr><td><b>{who_label(s['node_id'])}</b></td><td>{s.get('place')}</td></tr>"
            for s in sun_on
        ) or "<tr><td colspan='2' class='muted'>Nobody ON — manage on <a href='/users'>Users</a></td></tr>"
        has_metdet = any(
            (c.get("name") or "").lower() in ("metaldetect", "metdet")
            for c in list_radio_channels()
        )
        metdet_note = (
            "<p class='ok'>MetalDetect is on the Heltec — invite link includes the private key. Users can also say <code>5</code> / <code>join</code> on the radio.</p>"
            if has_metdet
            else "<p class='bad'>MetalDetect not on the Heltec yet. Invite link below may be just the <b>public primary</b> — don’t use that as your private club. Create MetalDetect on the Heltec first (or paste the share URL from a phone that already has it), then test Nodes → Chan or radio menu <code>5</code>.</p>"
        )

        ch_url = get_channel_invite_url() or ""
        ch_esc = ch_url.replace("&", "&amp;").replace('"', "&quot;")
        mqtt_state = "ON" if mqtt.get("enabled") else "OFF"
        chans = list_radio_channels()
        chan_rows = "".join(
            "<tr>"
            f"<td>{c.get('index')}</td>"
            f"<td>{(c.get('name') or '').replace('<','&lt;')}</td>"
            f"<td>{c.get('role')}</td>"
            f"<td>{c.get('psk')}</td>"
            f"<td>{'↑' if c.get('uplink') else '—'} {'↓' if c.get('downlink') else ''}</td>"
            "</tr>"
            for c in chans
        ) or "<tr><td colspan='5' class='muted'>No channels read (radio down?)</td></tr>"
        body = f"""
        <h2>Settings</h2>
        <div class="panel">
          <p class="muted" style="margin-top:0"><b>Security:</b> this dashboard has <b>no login</b> and binds to localhost by default.
          Anyone on your LAN who can reach it can control the bot. Keep it on <code>127.0.0.1</code>,
          or put it behind Tailscale/VPN. Do not port-forward it to the internet.</p>
          <form method="post" action="{url_for('save_settings')}">
            <p><label>Unlock passphrase / invite word<br/>
            <input type="text" name="passphrase" value="{get_passphrase()}"/></label></p>
            <button type="submit">Save passphrase</button>
          </form>
        </div>

        <h2 style="margin-top:28px">Use the admin page from your phone (Tailscale)</h2>
        <div class="panel">
          <p style="margin-top:0;max-width:740px;line-height:1.5">
            This dashboard lives only on this computer, at <code>http://127.0.0.1:8787/</code>.
            That’s a private address — other phones on the internet (or even your home Wi‑Fi, by default)
            cannot open it. <b>Tailscale</b> is a small app that makes a private tunnel between
            <i>your</i> devices after you sign in. Think of it as a secret ethernet cable in the cloud,
            not a public website.
          </p>
          <p class="muted" style="max-width:740px;line-height:1.5"><b>Do this once:</b></p>
          <ol style="max-width:740px;line-height:1.55">
            <li>On <b>this Mac Mini</b>: install Tailscale from
              <a href="https://tailscale.com/download" target="_blank" rel="noopener">tailscale.com/download</a>,
              open it, and sign in (Apple/Google/email is fine).</li>
            <li>On the <b>phone or laptop</b> you’ll use: install the same Tailscale app and sign into
              <b>the same account</b>. Wait until it says connected.</li>
            <li>On the Mini, open Terminal and paste:
              <br/><code>tailscale serve --bg 8787</code></li>
            <li>Still in Terminal, run <code>tailscale status</code>. You’ll see this computer’s name,
              like <code>mac-mini</code>.</li>
            <li>On your phone (cellular is fine), open Safari to
              <code>https://THAT-NAME.ts.net/</code>
              — for example <code>https://mac-mini.ts.net/</code>.</li>
          </ol>
          <p class="muted" style="max-width:740px">Don’t use “Funnel”, and don’t port-forward 8787 on Starlink.
          Funnel would put this unauthenticated admin UI on the public internet.</p>
        </div>

        <h2 style="margin-top:28px">MetalDetect channel</h2>
        <div class="panel">
          <p class="muted" style="margin-top:0;max-width:740px;line-height:1.45">
            Best practice: add a <b>secondary</b> private channel named <b>MetalDetect</b>
            (keeps your primary for the wider mesh).
            Use a <b>random 256-bit</b> key. MQTT uplink/downlink stay OFF.
          </p>
          {metdet_note}
          <p class="muted">Invite links are <b>add-only</b> (<code>?add=true</code>) and include <b>MetalDetect only</b> — not your primary, and not LoRa modem/hops. Users can still change hop limit anytime under LoRa settings. If the phone shows Replace vs Add, pick <b>Add</b>.</p>
          <form method="post" action="{url_for('create_channel')}" onsubmit="return confirm('Create MetalDetect secondary channel with a new random 256-bit key on the Heltec? (Skip if you already pasted a share URL from a phone that has MetalDetect.)');">
            <button type="submit">Create MetalDetect on Heltec</button>
          </form>
          <h3 style="margin:18px 0 8px;font-size:0.95rem">Channels on radio</h3>
          <p class="muted">Channel <b>name</b> ≠ LoRa <b>modem preset</b> (MediumFast / LongFast / Burning Mesh, etc.). Preset is radio-wide; empty primary name used to show as “LongFast” in older UI — that was misleading.</p>
          <table>
            <thead><tr><th>#</th><th>Name</th><th>Role</th><th>Key</th><th>MQTT</th></tr></thead>
            <tbody>{chan_rows}</tbody>
          </table>
          <h3 style="margin:18px 0 8px;font-size:0.95rem">Invite link (clickable / QR-equivalent)</h3>
          <p style="word-break:break-all"><a href="{ch_esc}" target="_blank" rel="noopener">{ch_esc or "(none yet — create channel or refresh)"}</a></p>
          <form method="post" action="{url_for('save_channel_url')}">
            <p><label>Paste URL from phone if needed<br/>
            <input type="text" name="url" value="{ch_esc}" style="width:min(100%,640px)"/></label></p>
            <button type="submit">Save channel URL</button>
          </form>
          <form class="inline" method="post" action="{url_for('refresh_channel_url')}" style="margin-top:8px">
            <button type="submit">Refresh from Heltec</button>
          </form>
          <p class="muted">Distribute: Nodes → <b>Channel link</b> (DM), or show QR from the Meshtastic app.</p>
        </div>

        <h2 style="margin-top:28px">Who has alerts ON</h2>
        <p class="muted">Human names (alias / radio name). Full ON/OFF controls: <a href="{url_for('users_page')}">Users</a>.</p>
        <table>
          <thead><tr><th>Who</th><th>Place</th></tr></thead>
          <tbody>{alert_rows}</tbody>
        </table>

        <h2 style="margin-top:28px">Who has sunrise ON</h2>
        <table>
          <thead><tr><th>Who</th><th>Place</th></tr></thead>
          <tbody>{sun_rows}</tbody>
        </table>

        <h2 style="margin-top:28px">MQTT bridge</h2>
        <p>Status: <b>{mqtt_state}</b> · broker <code>{mqtt.get('address') or '—'}</code> · root <code>{mqtt.get('root') or '(default)'}</code></p>
        <form class="inline" method="post" action="{url_for('mqtt_toggle')}" onsubmit="return confirm('Toggle MQTT? Gateway may reboot.');">
          <input type="hidden" name="enabled" value="1"/>
          <button type="submit">MQTT ON</button>
        </form>
        <form class="inline" method="post" action="{url_for('mqtt_toggle')}" onsubmit="return confirm('Disable MQTT?');">
          <input type="hidden" name="enabled" value="0"/>
          <button type="submit">MQTT OFF</button>
        </form>
        <p class="muted" style="max-width:720px">Optional. Leave OFF unless you know you need it.</p>
        """
        return render(body)

    @app.post("/settings/create-channel")
    def create_channel():
        msg = create_metdet_channel()
        return redirect(url_for("settings_page", flash=msg))

    @app.post("/settings/sub")
    def set_sub():
        node_id = (request.form.get("node_id") or "").strip()
        kind = (request.form.get("kind") or "").strip()
        place = (request.form.get("place") or "reno").strip()
        if request.form.get("save_place") == "1":
            enabled = (request.form.get("current_enabled") or "0") == "1"
        else:
            enabled = (request.form.get("enabled") or "0") == "1"
        nxt = (request.form.get("next") or "settings").strip()
        dest = "users_page" if nxt == "users" else "settings_page"
        if node_id and kind in ("sun", "alert"):
            if place not in ("reno", "brc", "both"):
                place = "reno"
            store.set_subscription(node_id, kind, enabled=enabled, place=place)
            state = "ON" if enabled else "OFF"
            verb = "saved" if request.form.get("save_place") == "1" else state
            return redirect(
                url_for(dest, flash=f"{kind} {verb} · {place}")
            )
        return redirect(url_for(dest, flash="Bad subscription request"))

    @app.post("/settings/channel-url")
    def save_channel_url():
        url = (request.form.get("url") or "").strip()
        set_channel_invite_url(url)
        return redirect(url_for("settings_page", flash="Channel URL saved" if url else "Channel URL cleared"))

    @app.post("/settings/channel-url/refresh")
    def refresh_channel_url():
        url = get_channel_invite_url()
        if url:
            set_channel_invite_url(url)
            return redirect(url_for("settings_page", flash="Pulled channel URL from Heltec and saved"))
        return redirect(url_for("settings_page", flash="Could not read channel URL from radio"))

    @app.post("/settings/mqtt")
    def mqtt_toggle():
        enabled = (request.form.get("enabled") or "0") == "1"
        msg = set_mqtt_enabled(enabled)
        return redirect(url_for("settings_page", flash=msg))

    @app.post("/settings")
    def save_settings():
        pw = (request.form.get("passphrase") or "").strip().lower()
        if pw:
            set_passphrase(pw)
        return redirect(url_for("settings_page", flash="Saved"))

    return app
