from __future__ import annotations

import re

# Don't leave a tiny leftover packet (e.g. one word on its own radio message).
_ORPHAN_MIN = 40


def chunk_text(text: str, max_chars: int, max_chunks: int) -> list[str]:
    """Split for LoRa DMs. Prefers sentence/word breaks; merges tiny tails."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ["(empty answer)"]

    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    remaining = text
    while remaining and len(parts) < max_chunks:
        if len(remaining) <= max_chars:
            parts.append(remaining)
            remaining = ""
            break

        # If the leftover would be tiny, take a slightly shorter first piece
        # so the rest still fits in one packet — avoids "(2/2) word".
        if len(remaining) <= max_chars + _ORPHAN_MIN and len(parts) < max_chunks - 1:
            # Prefer splitting so both pieces are reasonably sized
            target = len(remaining) // 2
            window = remaining[: max_chars + 1]
            cut = max(
                window.rfind(". ", 0, max(target + 20, max_chars // 2)),
                window.rfind("? ", 0, max(target + 20, max_chars // 2)),
                window.rfind("! ", 0, max(target + 20, max_chars // 2)),
                window.rfind(" ", 0, max(target + 20, max_chars // 2)),
            )
            if cut < max_chars // 4:
                cut = max(
                    window.rfind(". "),
                    window.rfind("? "),
                    window.rfind("! "),
                    window.rfind(" "),
                )
            if cut < max_chars // 4:
                cut = max_chars
        else:
            window = remaining[: max_chars + 1]
            cut = max(
                window.rfind(". "),
                window.rfind("? "),
                window.rfind("! "),
                window.rfind(" "),
            )
            if cut < max_chars // 3:
                cut = max_chars

        piece = remaining[:cut].strip()
        remaining = remaining[cut:].strip()

        # Merge orphan tail into this piece if it still fits
        if remaining and len(remaining) < _ORPHAN_MIN:
            if len(piece) + 1 + len(remaining) <= max_chars:
                piece = f"{piece} {remaining}".strip()
                remaining = ""
            elif parts and len(parts[-1]) + 1 + len(piece) <= max_chars:
                # Pull this piece back into previous so remaining becomes primary
                parts[-1] = f"{parts[-1]} {piece}".strip()
                continue

        if piece:
            parts.append(piece)

    if remaining and len(parts) == max_chunks:
        # Last-resort merge into final chunk if tiny
        if len(remaining) < _ORPHAN_MIN and len(parts[-1]) + 1 + len(remaining) <= max_chars:
            parts[-1] = f"{parts[-1]} {remaining}".strip()
        else:
            parts[-1] = parts[-1][: max(0, max_chars - 14)].rstrip() + " … (say more)"
    elif remaining:
        parts.append(remaining)

    # Final pass: collapse any leftover orphans into the previous chunk
    while len(parts) >= 2 and len(parts[-1]) < _ORPHAN_MIN:
        if len(parts[-2]) + 1 + len(parts[-1]) <= max_chars:
            parts[-2] = f"{parts[-2]} {parts[-1]}".strip()
            parts.pop()
        else:
            break

    total = len(parts)
    if total > 1:
        parts = [f"({i}/{total}) {p}" for i, p in enumerate(parts, start=1)]
    return parts
