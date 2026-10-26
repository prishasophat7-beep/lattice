"""Runtime settings, read from OASIS_* environment variables.

The Tauri shell sets these when it launches the backend. Defaults target the
production setup: llama-server on localhost, data under %LOCALAPPDATA%\\Oasis.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .llm import LLMConfig


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "Oasis"


@dataclass
class Settings:
    data_dir: Path
    llm: LLMConfig
    api_token: str | None = None      # required as a Bearer token when set
    daily_budget: int | None = None   # development endpoints only (OpenRouter free tier)
    # Origins allowed to call the API from a browser engine: Tauri's webview on Windows,
    # and the Vite dev server used while developing the UI.
    cors_origins: tuple[str, ...] = ("http://tauri.localhost", "tauri://localhost", "http://localhost:1420")
    # the embedding server on this computer (a second llama-server the desktop app starts when an
    # embedding model is installed); the engine asks it which model it serves
    embed_base_url: str | None = None
    embed_api_key: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        budget = env("OASIS_DAILY_BUDGET")
        origins = env("OASIS_CORS_ORIGINS")
        extra = {"cors_origins": tuple(o.strip() for o in origins.split(",") if o.strip())} if origins else {}
        return cls(
            **extra,
            data_dir=Path(env("OASIS_DATA_DIR") or _default_data_dir()),
            llm=LLMConfig(
                base_url=env("OASIS_LLM_BASE_URL", "http://127.0.0.1:8080/v1"),
                model=env("OASIS_LLM_MODEL", "local"),
                api_key=env("OASIS_LLM_API_KEY", ""),
                output_mode=env("OASIS_OUTPUT_MODE", "native"),  # type: ignore[arg-type]
                max_tokens=int(env("OASIS_MAX_TOKENS", "2048")),
                # raise both for reasoning models, whose thinking counts as output
                select_max_tokens=int(env("OASIS_SELECT_MAX_TOKENS", "300")),
                answer_max_tokens=int(env("OASIS_ANSWER_MAX_TOKENS", "800")),
                # the built-in llama-server's -c (the desktop app passes it); 0: asked from the server
                context_tokens=int(env("OASIS_LLM_CONTEXT", "0")),
            ),
            api_token=env("OASIS_API_TOKEN") or None,
            daily_budget=int(budget) if budget else None,
            embed_base_url=env("OASIS_EMBED_BASE_URL") or None,
            embed_api_key=env("OASIS_EMBED_API_KEY", ""),
        )
