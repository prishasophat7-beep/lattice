"""Settings an administrator changes in the app (Manage), kept in <data_dir>/config.json.

    profile      "line":   manuals only, answered by the built-in local model (the default)
                 "office": manuals and Chat mode, usually with a stronger remote model
    connection   which language model answers: the built-in llama-server ("local"), or any
                 OpenAI-compatible endpoint such as a vLLM server ("remote")
    chat_connection  optionally, a different model for Chat mode (e.g. a strong network model for
                 chat while manuals questions stay on the built-in model); None: the same one
    embeddings   optionally, an embedding model on an office server (OpenAI-compatible
                 /embeddings): Chat mode then also searches by meaning; None: keywords only

Environment variables (OASIS_LLM_*) still describe the local model; a remote connection
replaces them. The API key is stored in the user's own app-data folder.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .llm import LLMConfig

CONFIG_FILE = "config.json"
# a model that reasons before answering counts its thinking as output: it needs room
REASONING_TEMPERATURE = 0.6
REASONING_TOKENS = {"max_tokens": 8000, "select_max_tokens": 4000, "answer_max_tokens": 6000}


class Connection(BaseModel):
    kind: Literal["local", "remote"] = "local"
    base_url: str = Field(default="", max_length=500)  # e.g. http://vllm.office.lan:8000/v1
    model: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=500)
    # kept so that older config files load; replies are plain text with every model (see replies.py)
    output_mode: Literal["text", "native", "prompted", "tool"] = "text"
    reasoning: bool = False  # the model thinks before answering (larger token caps)
    # the model's context window in tokens (the server's --max-model-len / -c); 0: asked from the
    # server when it says, else assumed (8,192 on this computer, 32,768 on a server)
    context_tokens: int = Field(default=0, ge=0, le=2_000_000)


class EmbeddingConnection(BaseModel):
    base_url: str = Field(default="", max_length=500)  # e.g. http://pado.office.lan:8080/v1
    model: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=500)


class AppConfig(BaseModel):
    profile: Literal["line", "office"] = "line"
    default_mode: Literal["manuals", "chat"] = "manuals"
    work_topics_only: bool = True  # Chat mode declines topics unrelated to work
    connection: Connection = Connection()
    chat_connection: Connection | None = None
    embeddings: EmbeddingConnection | None = None

    @property
    def chat_available(self) -> bool:
        return self.profile == "office"


def load_config(data_dir: Path) -> AppConfig:
    try:
        return AppConfig.model_validate_json((Path(data_dir) / CONFIG_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValidationError, ValueError):
        return AppConfig()


def save_config(data_dir: Path, config: AppConfig) -> None:
    path = Path(data_dir) / CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config.model_dump(), indent=1), encoding="utf-8")
    os.replace(tmp, path)


def effective_llm(local: LLMConfig, connection: Connection) -> LLMConfig:
    """The model settings to use: the local defaults, or the remote connection."""
    if connection.kind != "remote":
        return local
    cfg = LLMConfig(base_url=connection.base_url.rstrip("/"), model=connection.model,
                    api_key=connection.api_key, output_mode="text",
                    context_tokens=connection.context_tokens)
    if connection.reasoning:
        for key, value in REASONING_TOKENS.items():
            setattr(cfg, key, value)
        # greedy decoding (temperature 0) sends a thinking model into endless loops ("got stuck
        # writing", Qwen 3.5 27B at the office); Qwen's own advice for thinking is 0.6
        cfg.reasoning, cfg.temperature = True, REASONING_TEMPERATURE
    else:
        cfg.max_tokens = max(cfg.max_tokens, 4000)  # chat answers can be longer than manual answers
        cfg.thinking_off = True  # a thinking model (Qwen 3.x) would spend the short limits thinking
    return cfg


def public_view(config: AppConfig) -> dict:
    """The config for the UI: the API key is never sent back, only whether one is set."""
    data = config.model_dump()
    for name in ("connection", "chat_connection", "embeddings"):
        if data.get(name):
            data[name]["api_key_set"] = bool(data[name].pop("api_key"))
    data["chat_available"] = config.chat_available
    return data
