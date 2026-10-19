# METDET mesh bot

A small Python bot that sits on a computer next to a Meshtastic radio (TCP gateway), answers private DMs with Gemini, and serves a local admin UI.

Works on **macOS**, **Linux / Raspberry Pi**, and **Windows**, as long as the host can reach your radio over TCP and the internet (for Gemini / weather).

## Burning Man 2026 archive (browse the real engagement)

This repo ships a **sanitized** snapshot of playa traffic so people can see what happened without your secrets:

| What | Where |
|---|---|
| Static browser (GitHub Pages) | [`docs/`](docs/) |
| SQLite snapshot | [`archive/bm2026/metdet-archive.db`](archive/bm2026/metdet-archive.db) |
| Admin-style UI (read-only) | `python -m bot.archive` → [http://127.0.0.1:8788/](http://127.0.0.1:8788/) |

Regenerate from a live `data/metdet.db` (never commit the live DB):

```bash
python scripts/export_public_archive.py
```

**Redacted from the public archive:** Gemini API key, `.env`, radio IP, channel invite / PSK URL, unlock passphrase.  
**Kept:** mesh names, node IDs, public channel lines, bot DMs, Gemini Q&A.

## What you need (to run a live bot)

1. A Meshtastic node with **TCP** enabled (WiFi/Ethernet), reachable from this machine  
2. A free [Google AI Studio](https://aistudio.google.com/apikey) **Gemini API key**  
3. Python **3.10+**  
4. (Optional) [Node.js](https://nodejs.org/) so you can use **pm2** for auto-restart  

Only one client should talk TCP/Bluetooth to the gateway radio at a time — quit the phone Meshtastic app connection to that node before starting the bot.

## Security (read before publishing / hosting)

| Keep private | Why |
|---|---|
| `.env` | Holds `GEMINI_API_KEY`, passphrase, admin node IDs, radio IP |
| `data/` / live `*.db` | Local user DB (names, unlocks, alert prefs) — gitignored |
| `backups/` | Full private DB copies — gitignored |
| Admin UI (`127.0.0.1:8787`) | **No login** — do not expose to the public internet |

`.env.example` is the template. On clone, the installer copies it to `.env` and the **setup wizard** prompts for your Gemini key, radio IP/USB, unlock word, and admin node IDs.

```bash
# Confirm nothing sensitive is staged
git status
grep -R "AIza\|GEMINI_API_KEY=.\+" --include='*.py' --include='*.md' --include='*.env*' . || true
```

Change `UNLOCK_PASSPHRASE` from the default. Treat admin node IDs like passwords for your mesh.

## Install

### macOS / Linux / Raspberry Pi

```bash
git clone https://github.com/YOUR_USER/metdet-mesh-bot.git
cd metdet-mesh-bot
chmod +x scripts/install.sh
./scripts/install.sh
```

The installer offers a **setup dialog** (browser or terminal) for Gemini key, Wi‑Fi IP or USB port, passphrase, and anti-spam pacing.

### Windows (PowerShell)

```powershell
git clone https://github.com/YOUR_USER/metdet-mesh-bot.git
cd metdet-mesh-bot
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
```

### Setup wizard (required for a live bot)

```bash
python -m bot.setup          # web on Mac/Windows, or pass --web / --cli
python -m bot.setup --cli    # SSH / Raspberry Pi friendly
```

Or open **Setup** in the admin UI: [http://127.0.0.1:8787/setup](http://127.0.0.1:8787/setup)

If you start the bot with no `GEMINI_API_KEY`, it opens the web setup dialog automatically and will not connect to a radio until you save a key.

**What the wizard configures:** API key, TCP (radio IP) **or** USB serial port, unlock word, admin node IDs, rate limits / chunk pacing.

**What it does not do:** put the radio on Wi‑Fi for you. You still enable Wi‑Fi once in the Meshtastic app and copy the IP (or plug in USB and pick the serial port from the list).

### Browse the BM 2026 archive only (no API key needed)

```bash
source .venv/bin/activate   # after install
python -m bot.archive
# → http://127.0.0.1:8788/
```

## Run

Foreground (good for first test):

```bash
# Mac / Linux / Pi
source .venv/bin/activate
python -m bot.main
```

```powershell
# Windows
.\.venv\Scripts\python.exe -m bot.main
```

Admin UI: [http://127.0.0.1:8787/](http://127.0.0.1:8787/)

From a phone node, DM the gateway radio your unlock word, set a name when asked, then `menu`.

## Keep running after reboot / power loss

`autorestart` in pm2 restarts the process if it crashes. **Surviving reboot** needs an OS startup hook once:

### pm2 (recommended on Mac / Pi / most Linux)

```bash
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup
# Run the command pm2 prints (often with sudo), then:
pm2 save
```

Windows (Administrator PowerShell):

```powershell
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 save
pm2-startup install
```

Check: `pm2 status` · Logs: `pm2 logs metdet-mesh-bot`

### systemd (Raspberry Pi / Linux alternative)

Create `/etc/systemd/system/metdet-mesh-bot.service` (edit paths/user):

```ini
[Unit]
Description=METDET Meshtastic bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/metdet-mesh-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/pi/metdet-mesh-bot/.venv/bin/python -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now metdet-mesh-bot
sudo systemctl status metdet-mesh-bot
```

## Mesh commands (DM only)

| Command | Effect |
|---|---|
| *(unlock word)* | Unlock this node |
| `menu` / `help` | Short menu |
| `intro` / `about` | Longer welcome (optional; not auto-sent) |
| `iam Name` | Set how the bot addresses you |
| `wx` / weather menu | Weather helpers |
| `chords …` | Guitar chords |
| `more` | Next chunk of a long answer |
| anything else (unlocked) | Short Gemini answer |

Onboarding is intentionally short: unlock → name → short tip + menu. Users who want the full README-style welcome say `intro`.

## Tuning multi-part DMs

LoRa often delivers packets out of order if several go out quickly. Defaults:

- Prefer fewer, fuller packets (`MAX_CHUNK_CHARS=200`, `MAX_CHUNKS=3`)
- Wait between parts (`CHUNK_DELAY_SEC=15`) and request radio ACK (`wantAck`)

If you still see `(2/2)` before `(1/2)`, raise `CHUNK_DELAY_SEC` (e.g. `18`–`25`).

## Architecture

```
Phone / radio  --LoRa DM-->  Gateway Heltec (TCP)
                                ^
                                |
                         this bot (Python)
                                |
                         Gemini + Open-Meteo + SQLite
                                |
                         Admin UI on localhost
```

## License

MIT — see [LICENSE](LICENSE).
