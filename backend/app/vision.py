"""Optional image-capable providers for transcription, never authoritative rendering."""

from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Protocol

import httpx


PROMPT = ("Transcribe the mathematical expression in this image exactly as printed into LaTeX. "
          "Preserve every character, subscript, superscript, accent, and symbol. "
          "Do not solve, simplify, correct, explain, or infer missing content. "
          "If any part is unreadable, write [unclear] at that position. Return only the transcription.")
VISION_LIMIT = BoundedSemaphore(2)


class VisionProvider(Protocol):
    name: str
    model: str
    def transcribe(self, image: bytes) -> str: ...


@dataclass(frozen=True)
class ChatVisionProvider:
    name: str
    model: str
    url: str
    key: str

    def transcribe(self, image: bytes) -> str:
        if len(image) > 8_000_000:
            raise ValueError("Formula crop exceeds the vision request size limit.")
        payload = {"model": self.model, "temperature": 0, "max_tokens": 512,
                   "messages": [{"role": "user", "content": [
                       {"type": "text", "text": PROMPT},
                       {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode(), "detail": "original"}},
                   ]}]}
        with VISION_LIMIT:
            for attempt in range(3):
                try:
                    response = httpx.post(self.url, headers={"Authorization": f"Bearer {self.key}"}, json=payload, timeout=45)
                    response.raise_for_status()
                    value = response.json()["choices"][0]["message"]["content"]
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("Vision provider returned empty output.")
                    return re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", value.strip()).strip()
                except httpx.HTTPStatusError as error:
                    if attempt == 2 or error.response.status_code not in {429, 500, 502, 503, 504}:
                        raise
                    time.sleep(1.5 * (attempt + 1))
                except (httpx.TimeoutException, httpx.ConnectError):
                    if attempt == 2:
                        raise
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("Vision transcription failed.")


def configured_provider() -> VisionProvider | None:
    """Only known image-capable models are allowed; keys never reach the browser."""
    deepseek = os.environ.get("PAPERLIGHT_DEEPSEEK_API_KEY", "")
    if deepseek:
        return ChatVisionProvider("deepseek", "deepseek-flash", "https://api.deepseek.com/chat/completions", deepseek)
    doubao = os.environ.get("PAPERLIGHT_DOUBAO_API_KEY", "")
    model = os.environ.get("PAPERLIGHT_DOUBAO_VISION_MODEL", "")
    if doubao and re.fullmatch(r"doubao-[A-Za-z0-9-]*vision[A-Za-z0-9-]*", model, re.I):
        return ChatVisionProvider("doubao", model, "https://ark.cn-beijing.volces.com/api/v3/chat/completions", doubao)
    return None


def transcribe_file(provider: VisionProvider, path: Path) -> dict[str, Any]:
    return {"source": "vision", "provider": provider.name, "model": provider.model, "originalAsset": path.name,
            "createdAt": time.time(),
            "rawOutput": provider.transcribe(path.read_bytes()), "revised": ""}
