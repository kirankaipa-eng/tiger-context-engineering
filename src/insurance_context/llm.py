"""Minimal OpenAI-compatible chat completion (e.g. the Tiger AI Gateway).

Reads API_KEY, API_BASE_URL and CHAT_MODEL from the environment (a .env in the
working directory is honoured). Kept dependency-free on purpose: the context
layer must run in client environments that only allow approved packages.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path


def _dotenv_value(raw: str) -> str:
    """A .env value: quoted ("..." or '...') up to its closing quote, else up to an inline ` #` comment."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), _dotenv_value(v))


def configured() -> bool:
    _load_dotenv()
    return bool(os.environ.get("API_KEY") and os.environ.get("API_BASE_URL"))


def complete(prompt: str, system: str = "You are a precise insurance data architect.",
             model: str | None = None, timeout: int = 90) -> str:
    _load_dotenv()
    base = os.environ["API_BASE_URL"].rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    body = json.dumps({
        "model": model or os.environ.get("CHAT_MODEL", "gpt-4o"),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {os.environ['API_KEY']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]
