# Burning Man 2026 engagement archive

Sanitized SQLite snapshot from the playa METDET mesh bot.

- **DB:** `metdet-archive.db`
- **Exported:** 2026-10-19T20:50:42Z
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
