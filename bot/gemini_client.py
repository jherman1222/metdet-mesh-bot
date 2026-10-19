from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

PT = ZoneInfo("America/Los_Angeles")

DEFAULT_SYSTEM = """You are METDET, a helpful LoRa mesh assistant for people around Black Rock City.

You do NOT have a private Burning Man camp directory. You only know what live web search returns.

Rules:
- Do not guess camp addresses, event times, or Gate waits.
- If search does not show a clear current-year (2026) source, say you could not verify it yet and suggest Playa Info / WWW / Placement / GARS 95.1 when relevant.
- Do not present older-year placement (2024/2025) as current. If that is all search finds, say it is from an older year and may be wrong for 2026.
- For weather and traffic, prefer live sources; ask one short clarifying question if time/place is needed.
- Keep answers short for radio: 1-3 plain sentences, no markdown, no bullets, no emojis.
- Be useful for how-to questions (shade, power, dust, water) from general knowledge when search is not required.
"""

CHORDS_SYSTEM = """You are a concise guitar helper for a LoRa radio.
Return ONLY compact chord sheets suitable for a tiny screen.
Format like: Song - Key X | Intro: ... | Verse: ... | Chorus: ... | Bridge: ...
Use common guitar chord names (Am, G, Cadd9, etc). No tabs unless asked. No markdown. No lyrics walls. Under ~350 characters when possible.
If the song is ambiguous, ask one clarifying question (which song/artist).
"""


def is_chords_request(text: str) -> bool:
    t = text.lower().strip()
    if t.startswith("chords ") or t.startswith("chord "):
        return True
    return bool(re.search(r"\b(guitar\s+)?chords?\b", t))


class GeminiService:
    def __init__(self, api_key: str, model: str, system_prompt: str | None = None) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.system_prompt = (system_prompt or DEFAULT_SYSTEM).strip()

    def answer(
        self,
        prompt: str,
        *,
        long_mode: bool = False,
        extra_context: str | None = None,
        history: list[dict[str, str]] | None = None,
        chords: bool = False,
        use_search: bool | None = None,
    ) -> str:
        now = datetime.now(PT).strftime("%Y-%m-%d %H:%M %Z")
        if chords:
            instruction = f"{CHORDS_SYSTEM}\nCurrent local datetime: {now}."
            search_on = False
        else:
            instruction = (
                f"{self.system_prompt}\n\nCurrent local datetime: {now}. "
                "Use Google Search for factual BRC/camp/event/traffic claims."
            )
            search_on = True
        if use_search is not None:
            search_on = use_search
        if extra_context:
            instruction += f"\nRadio context: {extra_context}"
        if history:
            instruction += " Continue the prior conversation helpfully and briefly."
        if long_mode and not chords:
            instruction += " User asked for more detail; still stay factual and plain text."
        elif not chords:
            instruction += " Prefer under ~350 characters. Never stop mid-sentence."

        contents: list = []
        if history:
            for turn in history[-6:]:
                role = turn.get("role") or "user"
                # google-genai uses user/model
                contents.append(
                    types.Content(
                        role="user" if role == "user" else "model",
                        parts=[types.Part(text=turn.get("text") or "")],
                    )
                )
        contents.append(
            types.Content(role="user", parts=[types.Part(text=prompt)])
        )

        config_kwargs: dict = dict(
            system_instruction=instruction,
            temperature=0.2 if not chords else 0.3,
            max_output_tokens=900 if long_mode else 512,
        )
        if search_on:
            config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception:
            config_kwargs.pop("tools", None)
            if not chords:
                config_kwargs["system_instruction"] = (
                    str(config_kwargs.get("system_instruction") or "")
                    + " Search tool unavailable; do not invent camp locations."
                )
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            return "Could not get an answer. Try a shorter question."
        return re.sub(r"\s+", " ", text).strip()
