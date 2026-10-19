# Install METDET mesh bot on Windows (PowerShell).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> METDET mesh bot install"
Write-Host "    Project: $Root"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  Write-Error "python not found. Install Python 3.10+ from https://www.python.org/downloads/ and check 'Add to PATH'."
}

Write-Host "==> Using $(python --version)"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "==> Created .env from .env.example"
} else {
  Write-Host "==> .env already exists (left unchanged)"
}

Write-Host "==> Creating virtualenv (.venv)"
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

Write-Host ""
Write-Host "==> Install complete."
Write-Host "Open the setup wizard now? (recommended)"
Write-Host "  [W] Web dialog in browser  [C] Terminal prompts  [S] Skip"
$choice = Read-Host "Choice [W/C/S]"
switch ($choice.ToUpper()) {
  "C" { & .\.venv\Scripts\python.exe -m bot.setup --cli }
  "S" { Write-Host "Skipped. Later:  .\.venv\Scripts\python.exe -m bot.setup" }
  Default { & .\.venv\Scripts\python.exe -m bot.setup --web }
}

Write-Host ""
Write-Host "After setup:"
Write-Host "  .\.venv\Scripts\python.exe -m bot.main"
Write-Host "  # or: pm2 start ecosystem.config.cjs"
Write-Host "  Admin UI: http://127.0.0.1:8787/"
Write-Host ""
