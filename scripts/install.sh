#!/usr/bin/env bash
# Install METDET mesh bot on macOS, Linux, or Raspberry Pi.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> METDET mesh bot install"
echo "    Project: $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.10+ first."
  exit 1
fi

PY=python3
if command -v python3.12 >/dev/null 2>&1; then PY=python3.12; fi
if command -v python3.11 >/dev/null 2>&1; then PY=python3.11; fi
if command -v python3.10 >/dev/null 2>&1; then PY=python3.10; fi

echo "==> Using $($PY --version)"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created .env from .env.example"
else
  echo "==> .env already exists (left unchanged)"
fi

echo "==> Creating virtualenv (.venv)"
$PY -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "==> Install complete."
echo ""

# Interactive setup dialog
if [[ -t 0 ]]; then
  echo "Open the setup wizard now? (recommended)"
  echo "  [w] Web dialog in browser  [c] Terminal prompts  [s] Skip"
  read -r -p "Choice [w/c/s]: " choice || choice=s
  case "${choice:-w}" in
    c|C)
      python -m bot.setup --cli
      ;;
    s|S)
      echo "Skipped. Later:  python -m bot.setup"
      ;;
    *)
      python -m bot.setup --web
      ;;
  esac
else
  echo "Non-interactive shell — run setup later:"
  echo "  source .venv/bin/activate && python -m bot.setup --cli"
fi

echo ""
echo "After setup:"
echo "  source .venv/bin/activate && python -m bot.main"
echo "  # or: pm2 start ecosystem.config.cjs && pm2 save && pm2 startup"
echo "  Admin UI: http://127.0.0.1:8787/"
echo ""
