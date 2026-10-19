# METDET mesh bot

A Meshtastic camp helper that ran on playa at **Burning Man 2026**.

People DM the gateway radio. The bot unlocks them, remembers a name, and answers over LoRa — weather, short web-backed Q&A (Gemini), sunrise / wind alerts, guitar chords, menus. A Mac Mini (or Pi) next to the radio does the internet part; phones only talk mesh.

```
Your phone  --LoRa DM-->  Heltec gateway  --TCP-->  this bot  --internet-->  Gemini / weather
                                              |
                                         frozen admin archive (HTML)
```

## Try the Burning Man 2026 archive (no install)

This is a **frozen copy of the real admin UI** filled with playa engagement — queries people asked, public mesh traffic, bot DMs, who unlocked.

**Open in your browser (works online):**  
➜ **[Interactive frozen admin](https://jherman1222.github.io/metdet-mesh-bot/)**

**Or download and open offline:**  
➜ [metdet-bm2026-frozen-admin.html](docs/metdet-bm2026-frozen-admin.html) — save the file, double-click it. No server, no Python, no API key.

Secrets (Gemini key, radio IP, channel invite / PSK, unlock word) were stripped. Mesh names and conversation text from the event are kept so you can see how it was actually used.

---

## Run your own live bot

You do **not** need to hand-edit config files for a first run.

1. Have a Meshtastic node with **TCP** on (Wi‑Fi) **or** plug it in over **USB**
2. Get a free [Gemini API key](https://aistudio.google.com/apikey)
3. Install + answer the **setup wizard** prompts:

```bash
git clone https://github.com/jherman1222/metdet-mesh-bot.git
cd metdet-mesh-bot
chmod +x scripts/install.sh
./scripts/install.sh
```

The installer creates a virtualenv and opens the setup wizard (browser on Mac/Windows, or terminal). It asks for:

- Gemini API key  
- Radio TCP IP **or** USB serial port  
- Unlock word people DM to start  
- Optional admin node IDs  

Then:

```bash
source .venv/bin/activate
python -m bot.main
```

Your live admin UI is on **that computer only** (`http://127.0.0.1:8787/`). That address is localhost — it is not a public demo link. For the public demo, use the [frozen admin](https://jherman1222.github.io/metdet-mesh-bot/) above.

**Windows:** `.\scripts\install.ps1` then `.\.venv\Scripts\python.exe -m bot.main`

**Re-run setup anytime:** `python -m bot.setup`  
If `GEMINI_API_KEY` is missing, starting the bot opens the wizard automatically.

Only one client should use the gateway radio at a time — disconnect the phone app from that node before starting the bot.

### Optional: keep it running (pm2)

```bash
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 save && pm2 startup   # then run the command it prints
```

---

## What people can DM

| They send | What happens |
|---|---|
| unlock word | Unlocks that radio |
| `menu` / `help` | Short menu |
| `iam Name` | How the bot addresses them |
| `wx` / `wx wind` / `wx watch`… | Weather helpers |
| `chords …` | Guitar chords |
| anything else (unlocked) | Short Gemini answer |
| `.r …` | Continue the same chat |
| `more` | Next chunk if a long reply was split |

---

## Security

| Private (never commit) | Why |
|---|---|
| `.env` | API key, passphrase, admin IDs, radio IP |
| `data/` live database | Your camp’s runtime DB |
| `backups/` | Full private copies |

`.env.example` is the blank template. Live DB stays gitignored; the **sanitized** BM2026 snapshot lives in `archive/bm2026/` and `docs/`.

Do not expose the live admin UI to the public internet — it has no login.

Rebuild the public HTML/DB from your live DB (optional):

```bash
python scripts/export_public_archive.py
```

---

## License

MIT — see [LICENSE](LICENSE).
