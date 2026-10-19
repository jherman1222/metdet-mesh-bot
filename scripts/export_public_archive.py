#!/usr/bin/env python3
"""
Build a public Burning Man 2026 archive from the live SQLite DB.

Outputs:
  archive/bm2026/metdet-archive.db   sanitized SQLite
  archive/bm2026/MANIFEST.json
  docs/index.html                    interactive frozen-admin UI (GitHub Pages)
  docs/metdet-bm2026-frozen-admin.html  same file (easy download name)
  docs/metdet-archive.db             optional SQLite download
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "metdet.db"
OUT_DIR = ROOT / "archive" / "bm2026"
OUT_DB = OUT_DIR / "metdet-archive.db"
MANIFEST = OUT_DIR / "MANIFEST.json"
DOCS = ROOT / "docs"

INVITE_RE = re.compile(r"https?://meshtastic\.org/e/[^\s\"']+", re.I)
UNLOCK_RE = re.compile(r"(['\"])metdet\1", re.I)
DM_ME_RE = re.compile(r"\bDM me ['\"]?metdet['\"]?", re.I)
DM_BARE_RE = re.compile(r"\bDM metdet\b", re.I)


def _scrub_text(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip().lower() == "metdet":
        return "[unlock]"
    s = INVITE_RE.sub("[redacted-channel-invite]", value)
    s = DM_ME_RE.sub("DM me [unlock]", s)
    s = DM_BARE_RE.sub("DM [unlock]", s)
    s = UNLOCK_RE.sub(r"\1[unlock]\1", s)
    return s


def _ts_iso(ts: float | int | None) -> str:
    if not isinstance(ts, (int, float)):
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def export_db(src: Path, dest: Path) -> dict:
    if not src.exists():
        raise SystemExit(f"Missing source DB: {src}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    shutil.copy2(src, dest)

    con = sqlite3.connect(dest)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("DELETE FROM settings WHERE key = 'unlock_passphrase'")
    cur.execute(
        "UPDATE settings SET value = ? WHERE key = 'channel_invite_url'",
        ("[redacted-channel-invite]",),
    )

    for table, cols in (
        ("channel_log", ("text",)),
        ("queries", ("prompt", "reply", "error")),
        ("pushes", ("detail", "target")),
        ("node_labels", ("alias", "note", "user_label")),
        ("users", ("short_name",)),
    ):
        id_col = "id" if table in ("channel_log", "queries", "pushes") else "node_id"
        for col in cols:
            rows = cur.execute(f"SELECT {id_col}, {col} FROM {table}").fetchall()
            for row in rows:
                raw = row[1]
                scrubbed = _scrub_text(raw) if isinstance(raw, str) else raw
                if scrubbed != raw:
                    cur.execute(
                        f"UPDATE {table} SET {col} = ? WHERE {id_col} = ?",
                        (scrubbed, row[0]),
                    )

    cur.execute("UPDATE users SET is_admin = 0")
    con.commit()

    stats: dict[str, object] = {}
    for t in ("users", "queries", "channel_log", "pushes", "subscriptions", "node_labels"):
        stats[t] = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    ch = dict(
        cur.execute(
            "SELECT channel, COUNT(*) FROM channel_log GROUP BY channel ORDER BY 2 DESC"
        ).fetchall()
    )
    kinds = dict(
        cur.execute(
            "SELECT COALESCE(kind,'(none)'), COUNT(*) FROM queries GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    )
    q_range = cur.execute("SELECT MIN(ts), MAX(ts) FROM queries").fetchone()
    c_range = cur.execute("SELECT MIN(ts), MAX(ts) FROM channel_log").fetchone()
    con.close()

    manifest = {
        "title": "METDET — Burning Man 2026 archive",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_note": (
            "Frozen snapshot of real playa mesh engagement. "
            "Secrets (API keys, invite/PSK, unlock word) removed."
        ),
        "db": "metdet-archive.db",
        "counts": stats,
        "channel_log_by_channel": ch,
        "queries_by_kind": kinds,
        "queries_range_utc": [_ts_iso(q_range[0]), _ts_iso(q_range[1])],
        "channel_log_range_utc": [_ts_iso(c_range[0]), _ts_iso(c_range[1])],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _load_payload(db_path: Path, manifest: dict) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    def label_for(node_id: str) -> str:
        return names.get(node_id) or node_id or "—"

    names: dict[str, str] = {}
    for r in con.execute("SELECT node_id, short_name FROM users"):
        if r["short_name"]:
            names[r["node_id"]] = r["short_name"]
    for r in con.execute("SELECT node_id, alias, user_label FROM node_labels"):
        names[r["node_id"]] = r["user_label"] or r["alias"] or names.get(r["node_id"]) or r["node_id"]

    users = [
        {
            "node_id": r["node_id"] or "",
            "name": label_for(r["node_id"] or ""),
            "short_name": r["short_name"] or "",
            "unlocked_at": _ts_iso(r["unlocked_at"]),
            "last_seen": _ts_iso(r["last_seen"]),
        }
        for r in con.execute(
            "SELECT node_id, short_name, unlocked_at, last_seen FROM users ORDER BY last_seen DESC"
        )
    ]
    labels = [
        {
            "node_id": r["node_id"] or "",
            "alias": r["alias"] or "",
            "user_label": r["user_label"] or "",
            "note": r["note"] or "",
        }
        for r in con.execute(
            "SELECT node_id, alias, user_label, note FROM node_labels ORDER BY node_id"
        )
    ]
    queries = [
        {
            "ts": _ts_iso(r["ts"]),
            "name": r["short_name"] or label_for(r["node_id"] or ""),
            "node_id": r["node_id"] or "",
            "kind": r["kind"] or "",
            "prompt": r["prompt"] or "",
            "reply": r["reply"] or "",
            "ms": r["latency_ms"] or "",
        }
        for r in con.execute(
            "SELECT ts, short_name, node_id, kind, prompt, reply, latency_ms "
            "FROM queries ORDER BY ts DESC"
        )
    ]
    public = [
        {
            "ts": _ts_iso(r["ts"]),
            "dir": r["direction"] or "",
            "node_id": r["node_id"] or "",
            "name": label_for(r["node_id"] or ""),
            "text": r["text"] or "",
        }
        for r in con.execute(
            "SELECT ts, direction, node_id, text FROM channel_log "
            "WHERE channel = 'Public' ORDER BY ts DESC"
        )
    ]
    dms = [
        {
            "ts": _ts_iso(r["ts"]),
            "dir": r["direction"] or "",
            "node_id": r["node_id"] or "",
            "name": label_for(r["node_id"] or ""),
            "text": r["text"] or "",
        }
        for r in con.execute(
            "SELECT ts, direction, node_id, text FROM channel_log "
            "WHERE channel = 'DM' ORDER BY ts DESC"
        )
    ]
    pushes = [
        {
            "ts": _ts_iso(r["ts"]),
            "action": r["action"] or "",
            "target": r["target"] or "",
            "node_id": r["node_id"] or "",
            "detail": r["detail"] or "",
        }
        for r in con.execute(
            "SELECT ts, action, target, node_id, detail FROM pushes ORDER BY ts DESC"
        )
    ]
    con.close()

    return {
        "meta": {
            "title": "METDET · Burning Man 2026",
            "subtitle": "Frozen admin — real mesh engagement, secrets removed",
            "exported_at": manifest.get("exported_at"),
            "note": manifest.get("source_note"),
            "counts": manifest.get("counts") or {},
            "by_channel": manifest.get("channel_log_by_channel") or {},
            "by_kind": manifest.get("queries_by_kind") or {},
            "channel_range": manifest.get("channel_log_range_utc") or [],
            "queries_range": manifest.get("queries_range_utc") or [],
        },
        "users": users,
        "labels": labels,
        "queries": queries,
        "public": public,
        "dms": dms,
        "pushes": pushes,
    }


FROZEN_ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>METDET · BM 2026 frozen admin</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f4ef; --panel: #fffcf7; --ink: #1c1917; --muted: #78716c;
      --line: #e7e5e4; --accent: #0f766e; --accent-soft: #ccfbf1;
      --ok: #15803d; --warn: #a16207; --danger: #b91c1c;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #1c1917; --panel: #292524; --ink: #fafaf9; --muted: #a8a29e;
        --line: #44403c; --accent: #2dd4bf; --accent-soft: #134e4a;
      }
    }
    * { box-sizing: border-box; }
    body {
      font-family: "Segoe UI", "Helvetica Neue", ui-sans-serif, system-ui, sans-serif;
      margin: 0; padding: 20px 24px 56px; max-width: 1180px;
      background: var(--bg); color: var(--ink); line-height: 1.45;
    }
    h1 { font-size: 1.45rem; font-weight: 650; letter-spacing: -0.02em; margin: 0 0 4px; }
    h2 { font-size: 1.1rem; font-weight: 600; margin: 0 0 12px; }
    .sub { color: var(--muted); font-size: 0.9rem; margin: 0 0 14px; }
    .flash {
      background: var(--accent-soft); border: 1px solid var(--accent);
      border-radius: 10px; padding: 10px 14px; margin-bottom: 16px;
    }
    nav {
      display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 18px;
    }
    nav button {
      cursor: pointer; font: inherit; font-size: 0.88rem;
      padding: 6px 11px; border-radius: 999px;
      background: var(--panel); border: 1px solid var(--line); color: var(--ink);
    }
    nav button.active, nav button:hover {
      border-color: var(--accent); color: var(--accent);
    }
    .row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
    .card {
      background: var(--panel); border: 1px solid var(--line);
      border-radius: 12px; padding: 14px 16px; min-width: 128px;
    }
    .card .muted { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
    .card b { display: block; font-size: 1.35rem; margin-top: 4px; }
    .muted { color: var(--muted); font-size: 0.9rem; }
    .toolbar {
      display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
      margin: 0 0 14px;
    }
    .toolbar input {
      flex: 1; min-width: 180px; padding: 8px 10px; border-radius: 8px;
      border: 1px solid var(--line); background: var(--panel); color: var(--ink); font: inherit;
    }
    .panel {
      background: var(--panel); border: 1px solid var(--line);
      border-radius: 12px; padding: 16px 18px; margin-bottom: 18px;
    }
    table {
      width: 100%; border-collapse: collapse; font-size: 0.84rem;
      background: var(--panel); border-radius: 12px; overflow: hidden;
      border: 1px solid var(--line);
    }
    th, td {
      text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em;
      color: var(--muted); position: sticky; top: 0; background: var(--panel);
    }
    td.wrap { white-space: pre-wrap; word-break: break-word; max-width: 36rem; }
    tr:hover td { background: color-mix(in srgb, var(--accent) 6%, transparent); }
    .dir-rx { color: var(--accent); font-weight: 600; }
    .dir-tx { color: var(--warn); font-weight: 600; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem; }
    .who { font-weight: 600; }
    .scroll { max-height: 70vh; overflow: auto; border-radius: 12px; }
    .hidden { display: none; }
    a.dl {
      color: var(--accent); font-weight: 600; text-decoration: none;
      margin-right: 14px;
    }
    code { font-size: 0.86em; }
  </style>
</head>
<body>
  <h1>METDET <span class="muted">frozen admin</span></h1>
  <p class="sub" id="subtitle"></p>
  <div class="flash">
    This is a <b>read-only snapshot</b> of the Burning Man 2026 mesh bot —
    real DMs, public traffic, and Gemini answers. No radio. No API keys.
    Works offline: File → Save Page As, or download the HTML below.
  </div>
  <p>
    <a class="dl" id="dlHtml" href="metdet-bm2026-frozen-admin.html" download>Download this UI (.html)</a>
    <a class="dl" href="metdet-archive.db" download>Download SQLite (.db)</a>
  </p>
  <nav id="nav"></nav>
  <div class="toolbar">
    <input id="q" type="search" placeholder="Filter this tab…" autocomplete="off"/>
    <span class="muted" id="count"></span>
  </div>
  <div id="view"></div>

  <script id="ARCHIVE_DATA" type="application/json">__ARCHIVE_JSON__</script>
  <script>
  const DATA = JSON.parse(document.getElementById("ARCHIVE_DATA").textContent);
  const meta = DATA.meta || {};
  const tabs = [
    { id: "status", label: "Status" },
    { id: "queries", label: "Queries" },
    { id: "public", label: "Public log" },
    { id: "dms", label: "DMs" },
    { id: "users", label: "Users" },
    { id: "pushes", label: "Pushes" },
  ];
  let active = "status";
  let filter = "";

  document.getElementById("subtitle").textContent =
    (meta.subtitle || "Frozen admin") +
    (meta.exported_at ? " · exported " + meta.exported_at : "");

  const nav = document.getElementById("nav");
  tabs.forEach((t) => {
    const b = document.createElement("button");
    b.textContent = t.label;
    b.dataset.id = t.id;
    b.onclick = () => { active = t.id; filter = ""; document.getElementById("q").value = ""; render(); };
    nav.appendChild(b);
  });

  document.getElementById("q").addEventListener("input", (e) => {
    filter = (e.target.value || "").trim().toLowerCase();
    render();
  });

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function match(row) {
    if (!filter) return true;
    return JSON.stringify(row).toLowerCase().includes(filter);
  }

  function table(headers, rows, cellsFn) {
    const shown = rows.filter(match);
    document.getElementById("count").textContent =
      shown.length + (filter ? " matching" : "") + " / " + rows.length;
    if (!shown.length) return "<p class='muted'>Nothing matches.</p>";
    const head = headers.map((h) => "<th>" + esc(h) + "</th>").join("");
    const body = shown.map((r) => "<tr>" + cellsFn(r).join("") + "</tr>").join("");
    return "<div class='scroll'><table><thead><tr>" + head +
      "</tr></thead><tbody>" + body + "</tbody></table></div>";
  }

  function td(s, cls) {
    return "<td" + (cls ? " class='" + cls + "'" : "") + ">" + esc(s) + "</td>";
  }

  function dirCell(d) {
    const cls = d === "rx" ? "dir-rx" : d === "tx" ? "dir-tx" : "";
    return "<td class='" + cls + "'>" + esc(d || "—") + "</td>";
  }

  function renderStatus() {
    document.getElementById("count").textContent = "";
    const c = meta.counts || {};
    const ch = meta.by_channel || {};
    const kinds = meta.by_kind || {};
    const kindLine = Object.entries(kinds).map(([k, v]) => k + " " + v).join(" · ");
    return `
      <div class="row">
        <div class="card"><div class="muted">Radio</div><b class="ok">archived</b></div>
        <div class="card"><div class="muted">Users</div><b>${esc(c.users || 0)}</b></div>
        <div class="card"><div class="muted">Queries</div><b>${esc(c.queries || 0)}</b></div>
        <div class="card"><div class="muted">Public lines</div><b>${esc(ch.Public || 0)}</b></div>
        <div class="card"><div class="muted">DM lines</div><b>${esc(ch.DM || 0)}</b></div>
        <div class="card"><div class="muted">Pushes</div><b>${esc(c.pushes || 0)}</b></div>
      </div>
      <div class="panel">
        <h2>What was this?</h2>
        <p>METDET sat next to a Meshtastic gateway on playa. People DM’d the bot unlock word,
        set a name, then asked questions (weather, search, chords, camp logistics).
        Answers came back over LoRa via Gemini + live weather. Admins used a local UI
        like this one — you’re looking at that UI, frozen with the real logs.</p>
        <p class="muted">${esc(meta.note || "")}</p>
        <p class="muted">Queries: ${esc(kindLine)}</p>
        <p class="muted">Channel log: ${esc((meta.channel_range || []).join(" → "))}</p>
        <p class="muted">Queries window: ${esc((meta.queries_range || []).join(" → "))}</p>
      </div>`;
  }

  function render() {
    nav.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("active", b.dataset.id === active);
    });
    const qBox = document.getElementById("q");
    qBox.classList.toggle("hidden", active === "status");
    let html = "";
    if (active === "status") html = renderStatus();
    else if (active === "queries") {
      html = table(
        ["When", "Who", "Kind", "Asked", "Bot replied", "ms"],
        DATA.queries || [],
        (r) => [
          td(r.ts),
          "<td><span class='who'>" + esc(r.name) + "</span><div class='mono muted'>" + esc(r.node_id) + "</div></td>",
          td(r.kind),
          td(r.prompt, "wrap"),
          td(r.reply, "wrap"),
          td(r.ms),
        ]
      );
    } else if (active === "public") {
      html = table(
        ["When", "Dir", "Who", "Text"],
        DATA.public || [],
        (r) => [
          td(r.ts),
          dirCell(r.dir),
          "<td><span class='who'>" + esc(r.name) + "</span><div class='mono muted'>" + esc(r.node_id) + "</div></td>",
          td(r.text, "wrap"),
        ]
      );
    } else if (active === "dms") {
      html = table(
        ["When", "Dir", "Who", "Text"],
        DATA.dms || [],
        (r) => [
          td(r.ts),
          dirCell(r.dir),
          "<td><span class='who'>" + esc(r.name) + "</span><div class='mono muted'>" + esc(r.node_id) + "</div></td>",
          td(r.text, "wrap"),
        ]
      );
    } else if (active === "users") {
      html = table(
        ["Name", "Node", "Unlocked", "Last seen"],
        DATA.users || [],
        (r) => [td(r.name, "who"), td(r.node_id, "mono"), td(r.unlocked_at), td(r.last_seen)]
      );
    } else if (active === "pushes") {
      html = table(
        ["When", "Action", "Target", "Node", "Detail"],
        DATA.pushes || [],
        (r) => [td(r.ts), td(r.action), td(r.target), td(r.node_id, "mono"), td(r.detail, "wrap")]
      );
    }
    document.getElementById("view").innerHTML = html;
  }

  // If opened as file:// and filename differs, still offer save via blob
  if (location.protocol === "file:") {
    const a = document.getElementById("dlHtml");
    a.removeAttribute("href");
    a.onclick = (e) => {
      e.preventDefault();
      const blob = new Blob([document.documentElement.outerHTML], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const x = document.createElement("a");
      x.href = url;
      x.download = "metdet-bm2026-frozen-admin.html";
      x.click();
      URL.revokeObjectURL(url);
    };
  }

  render();
  </script>
</body>
</html>
"""


def write_frozen_admin(manifest: dict, db_path: Path) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    payload = _load_payload(db_path, manifest)
    # Compact JSON; escape </script> so embedded payload can't break the page.
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    raw = raw.replace("<", "\\u003c")
    page = FROZEN_ADMIN_HTML.replace("__ARCHIVE_JSON__", raw)

    index = DOCS / "index.html"
    download = DOCS / "metdet-bm2026-frozen-admin.html"
    index.write_text(page, encoding="utf-8")
    download.write_text(page, encoding="utf-8")
    shutil.copy2(db_path, DOCS / "metdet-archive.db")

    (DOCS / "README.md").write_text(
        "# METDET BM 2026 frozen admin\n\n"
        "Open [index.html](index.html) on GitHub Pages, or download "
        "[metdet-bm2026-frozen-admin.html](metdet-bm2026-frozen-admin.html) "
        "and open it in any browser (works offline).\n\n"
        "Regenerate: `python scripts/export_public_archive.py`\n",
        encoding="utf-8",
    )


def write_archive_readme(manifest: dict) -> None:
    (OUT_DIR / "README.md").write_text(
        f"""# Burning Man 2026 engagement archive

Sanitized SQLite + interactive frozen-admin HTML.

- **Browse online:** GitHub Pages `docs/index.html`
- **Download UI:** `docs/metdet-bm2026-frozen-admin.html` (open in any browser)
- **DB:** `metdet-archive.db`
- **Exported:** {manifest.get("exported_at")}

Secrets (API keys, invite/PSK, unlock word) are removed. Mesh names and logs remain.
""",
        encoding="utf-8",
    )


def main() -> None:
    t0 = time.time()
    manifest = export_db(SRC, OUT_DB)
    write_frozen_admin(manifest, OUT_DB)
    write_archive_readme(manifest)
    print(json.dumps(manifest, indent=2))
    print(f"Wrote frozen admin + {OUT_DB} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
