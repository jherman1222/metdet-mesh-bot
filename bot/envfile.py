"""Read/update .env without requiring python-dotenv for writes."""

from __future__ import annotations

from pathlib import Path

from bot.config import ROOT

ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"


def ensure_env_file() -> Path:
    if not ENV_PATH.exists():
        if EXAMPLE_PATH.exists():
            ENV_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV_PATH.write_text("", encoding="utf-8")
    return ENV_PATH


def read_env(path: Path | None = None) -> dict[str, str]:
    p = path or ensure_env_file()
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def update_env(updates: dict[str, str], path: Path | None = None) -> Path:
    """Set/replace keys in .env; preserve comments and unrelated lines."""
    p = path or ensure_env_file()
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    lines = text.splitlines()
    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                new_lines.append(f"{key}={remaining.pop(key)}")
                continue
        new_lines.append(line)
    if remaining:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# Updated by setup wizard")
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}")
    p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return p
