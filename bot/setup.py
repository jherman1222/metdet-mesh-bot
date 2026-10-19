"""CLI entry: python -m bot.setup [--web|--cli]."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="METDET first-run / reconfigure wizard")
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--web",
        action="store_true",
        help="Open localhost setup dialog in a browser (default on Mac/Windows)",
    )
    g.add_argument(
        "--cli",
        action="store_true",
        help="Terminal prompts (good for SSH / Raspberry Pi)",
    )
    p.add_argument("--port", type=int, default=8787, help="Web setup port")
    p.add_argument("--no-browser", action="store_true", help="Do not auto-open browser")
    args = p.parse_args(argv)

    from bot.setup_wizard import run_cli_setup, run_web_setup

    use_cli = args.cli
    use_web = args.web
    if not use_cli and not use_web:
        # Default: web when a display is likely; else CLI
        use_web = sys.platform in ("darwin", "win32") and sys.stdin.isatty()
        use_cli = not use_web

    if use_cli:
        run_cli_setup()
    else:
        run_web_setup(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
