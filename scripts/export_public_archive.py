#!/usr/bin/env python3
"""
Build a public Burning Man 2026 archive from the live SQLite DB.

- Copies engagement tables (users, queries, channel_log, pushes, …)
- Redacts channel invite URLs / unlock passphrase wording
- Writes archive/bm2026/metdet-archive.db + MANIFEST.json
- Generates docs/ archive HTML for GitHub Pages browsing

Private full backups belong in backups/ (gitignored), not this tree.
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
# Historical camp unlock word — keep chat flow readable, hide the literal.
# Leave "METDET" brand / "METDET — menu" alone; scrub unlock usages only.
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

    # Drop unlock passphrase if stored; redact invite URL.
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

    # Never publish is_admin as a privilege signal for live clones — keep history but zero it.
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
        "title": "METDET mesh bot — Burning Man 2026 archive",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_note": (
            "Sanitized copy of playa engagement. Channel invite URLs and unlock "
            "passphrase removed. Names/node IDs are as used on the mesh."
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


def _table_html(title: str, headers: list[str], rows: list[list[str]], *, anchor: str) -> str:
    thead = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        tds = "".join(f"<td>{html.escape(c)}</td>" for c in row)
        body_rows.append(f"<tr>{tds}</tr>")
    tbody = "\n".join(body_rows) or f"<tr><td colspan='{len(headers)}'>(empty)</td></tr>"
    return f"""
<section id="{html.escape(anchor)}">
  <h2>{html.escape(title)} <span class="muted">({len(rows)})</span></h2>
  <div class="scroll">
  <table>
    <thead><tr>{thead}</tr></thead>
    <tbody>
{tbody}
    </tbody>
  </table>
  </div>
</section>
"""


def write_docs(manifest: dict, db_path: Path) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    users = [
        [
            r["node_id"] or "",
            r["short_name"] or "",
            _ts_iso(r["unlocked_at"]),
            _ts_iso(r["last_seen"]),
        ]
        for r in con.execute(
            "SELECT node_id, short_name, unlocked_at, last_seen FROM users ORDER BY last_seen DESC"
        )
    ]
    labels = [
        [r["node_id"] or "", r["alias"] or "", r["user_label"] or "", r["note"] or ""]
        for r in con.execute(
            "SELECT node_id, alias, user_label, note FROM node_labels ORDER BY node_id"
        )
    ]
    queries = [
        [
            _ts_iso(r["ts"]),
            r["short_name"] or "",
            r["node_id"] or "",
            r["kind"] or "",
            (r["prompt"] or "")[:500],
            (r["reply"] or "")[:500],
            str(r["latency_ms"] or ""),
        ]
        for r in con.execute(
            "SELECT ts, short_name, node_id, kind, prompt, reply, latency_ms "
            "FROM queries ORDER BY ts DESC"
        )
    ]
    public = [
        [
            _ts_iso(r["ts"]),
            r["direction"] or "",
            r["node_id"] or "",
            (r["text"] or "")[:800],
        ]
        for r in con.execute(
            "SELECT ts, direction, node_id, text FROM channel_log "
            "WHERE channel = 'Public' ORDER BY ts DESC"
        )
    ]
    dms = [
        [
            _ts_iso(r["ts"]),
            r["direction"] or "",
            r["node_id"] or "",
            (r["text"] or "")[:800],
        ]
        for r in con.execute(
            "SELECT ts, direction, node_id, text FROM channel_log "
            "WHERE channel = 'DM' ORDER BY ts DESC"
        )
    ]
    pushes = [
        [
            _ts_iso(r["ts"]),
            r["action"] or "",
            r["target"] or "",
            r["node_id"] or "",
            (r["detail"] or "")[:400],
        ]
        for r in con.execute(
            "SELECT ts, action, target, node_id, detail FROM pushes ORDER BY ts DESC"
        )
    ]
    con.close()

    counts = manifest.get("counts") or {}
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>METDET BM 2026 archive</title>
  <style>
    :root {{ color-scheme: light dark; --ink:#1c1917; --muted:#78716c; --line:#e7e5e4; --bg:#f6f4ef; --panel:#fffcf7; --accent:#0f766e; }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --ink:#fafaf9; --muted:#a8a29e; --line:#44403c; --bg:#1c1917; --panel:#292524; --accent:#2dd4bf; }}
    }}
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--ink); line-height: 1.45; }}
    header {{ padding: 28px 20px 12px; max-width: 1100px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; letter-spacing: -0.02em; }}
    .muted {{ color: var(--muted); font-weight: 400; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 10px 14px; margin: 16px 0 8px; }}
    nav a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 0 20px 48px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 16px 0 28px; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }}
    .card b {{ display: block; font-size: 1.35rem; }}
    section {{ margin: 28px 0; }}
    .scroll {{ overflow: auto; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: var(--panel); }}
    td {{ white-space: pre-wrap; word-break: break-word; max-width: 42rem; }}
    code {{ font-size: 0.9em; }}
  </style>
</head>
<body>
<header>
  <h1>METDET · Burning Man 2026 archive</h1>
  <p class="muted">{html.escape(str(manifest.get("source_note") or ""))}</p>
  <p class="muted">Exported {html.escape(str(manifest.get("exported_at") or ""))}.
    Browse here, or run locally: <code>python -m bot.archive</code></p>
  <nav>
    <a href="#stats">Stats</a>
    <a href="#queries">Queries</a>
    <a href="#public">Public mesh</a>
    <a href="#dms">DMs</a>
    <a href="#users">Users</a>
    <a href="#labels">Labels</a>
    <a href="#pushes">Pushes</a>
    <a href="../archive/bm2026/metdet-archive.db">Download SQLite</a>
  </nav>
</header>
<main>
<section id="stats">
  <h2>Engagement</h2>
  <div class="cards">
    <div class="card"><span class="muted">Users</span><b>{counts.get("users", 0)}</b></div>
    <div class="card"><span class="muted">Queries</span><b>{counts.get("queries", 0)}</b></div>
    <div class="card"><span class="muted">Channel lines</span><b>{counts.get("channel_log", 0)}</b></div>
    <div class="card"><span class="muted">Public</span><b>{(manifest.get("channel_log_by_channel") or {}).get("Public", 0)}</b></div>
    <div class="card"><span class="muted">DMs</span><b>{(manifest.get("channel_log_by_channel") or {}).get("DM", 0)}</b></div>
    <div class="card"><span class="muted">Pushes</span><b>{counts.get("pushes", 0)}</b></div>
  </div>
  <p class="muted">Queries: {html.escape(" · ".join(f"{k} {v}" for k,v in (manifest.get("queries_by_kind") or {}).items()))}</p>
  <p class="muted">Channel log window: {html.escape(" → ".join(manifest.get("channel_log_range_utc") or []))}</p>
</section>
{_table_html("Gemini / bot queries", ["When", "Name", "Node", "Kind", "Prompt", "Reply", "ms"], queries, anchor="queries")}
{_table_html("Public primary channel", ["When", "Dir", "Node", "Text"], public, anchor="public")}
{_table_html("Direct messages (bot ↔ nodes)", ["When", "Dir", "Node", "Text"], dms, anchor="dms")}
{_table_html("Unlocked users", ["Node", "Name", "Unlocked", "Last seen"], users, anchor="users")}
{_table_html("Node labels", ["Node", "Alias", "Label", "Note"], labels, anchor="labels")}
{_table_html("Admin pushes", ["When", "Action", "Target", "Node", "Detail"], pushes, anchor="pushes")}
</main>
</body>
</html>
"""
    # Pages serves only docs/ — keep a downloadable copy beside index.html.
    shutil.copy2(db_path, DOCS / "metdet-archive.db")
    page = page.replace(
        'href="../archive/bm2026/metdet-archive.db"',
        'href="metdet-archive.db"',
    )
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "README.md").write_text(
        "# METDET BM 2026 public archive\n\n"
        "Static browse pages for GitHub Pages. SQLite: "
        "[`metdet-archive.db`](metdet-archive.db) "
        "(same file as [`../archive/bm2026/`](../archive/bm2026/)).\n\n"
        "Regenerate: `python scripts/export_public_archive.py`\n",
        encoding="utf-8",
    )


def write_archive_readme(manifest: dict) -> None:
    (OUT_DIR / "README.md").write_text(
        f"""# Burning Man 2026 engagement archive

Sanitized SQLite snapshot from the playa METDET mesh bot.

- **DB:** `metdet-archive.db`
- **Exported:** {manifest.get("exported_at")}
- **Browse (GitHub Pages):** [`../../docs/`](../../docs/)
- **Local admin-style UI:** `python -m bot.archive`

## What was removed

- `.env` secrets (API keys, radio IP, admin IDs) — never in this folder
- Channel invite URL / PSK blob
- Stored unlock passphrase; invite wording scrubbed to `[unlock]`

## What remains

Mesh-chosen names, node IDs, public channel traffic, bot DMs, and Gemini Q&A
from the event window (plus some post-event sun/wx noise if the bot stayed up).

Treat this as a camp story archive, not a live service.
""",
        encoding="utf-8",
    )


def main() -> None:
    t0 = time.time()
    manifest = export_db(SRC, OUT_DB)
    write_docs(manifest, OUT_DB)
    write_archive_readme(manifest)
    print(json.dumps(manifest, indent=2))
    print(f"Wrote {OUT_DB} and {DOCS / 'index.html'} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
