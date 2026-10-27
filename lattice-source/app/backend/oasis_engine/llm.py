"""Connection to the language model.

Production: llama.cpp's llama-server on localhost (OpenAI-compatible API).
Development: any OpenAI-compatible endpoint, e.g. OpenRouter, with an optional
daily request budget because its free tier allows only 50 requests per day.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import openai
from openai import AsyncOpenAI
from pydantic_ai import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model, StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider

log = logging.getLogger("oasis_engine.llm")

OutputMode = Literal["text", "native", "prompted", "tool"]  # "text": plain replies any model can write (replies.py)


class BudgetExceeded(RuntimeError):
    """The daily request budget for the development endpoint is used up."""


class RequestBudget:
    """Counts chat-completion requests per day in a small JSON file."""

    def __init__(self, limit: int, usage_file: Path):
        self.limit, self.usage_file = limit, Path(usage_file)

    def used_today(self) -> int:
        try:
            return json.loads(self.usage_file.read_text()).get(datetime.date.today().isoformat(), 0)
        except (FileNotFoundError, ValueError):
            return 0

    def ensure(self, needed: int = 1) -> None:
        left = self.limit - self.used_today()
        if left < needed:
            raise BudgetExceeded(f"{left} of {self.limit} daily requests left, {needed} needed")

    async def on_request(self, request: httpx.Request) -> None:
        if request.url.path.endswith("/chat/completions"):
            used = self.used_today()
            self.usage_file.parent.mkdir(parents=True, exist_ok=True)
            self.usage_file.write_text(json.dumps({datetime.date.today().isoformat(): used + 1}))


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "local"
    api_key: str = ""
    output_mode: OutputMode = "text"
    temperature: float = 0.0
    max_tokens: int = 2048
    select_max_tokens: int = 300   # see pipeline.SELECT_MAX_TOKENS
    answer_max_tokens: int = 800   # see pipeline.ANSWER_MAX_TOKENS
    timeout_seconds: float = 600.0
    # ask the server not to think first (Qwen 3.x and other thinking models on vLLM, SGLang or
    # llama.cpp think by default; the thinking would use up the short answer limits)
    thinking_off: bool = False
    reasoning: bool = False   # a thinking model: sampled (Qwen: 0.6; greedy decoding loops) with room to think
    context_tokens: int = 0  # the context window, when known (see config.Connection.context_tokens)


THINKING_OFF = {"enable_thinking": False}


class ThinkingOffTransport(httpx.AsyncBaseTransport):
    """Adds chat_template_kwargs.enable_thinking=false to chat-completion requests. A server that
    rejects the unknown field (400/422 naming it) gets the request again without it, once, and is
    then remembered as one that does not take it."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.supported = True

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not (self.supported and request.method == "POST" and request.url.path.endswith("/chat/completions")):
            return await self.inner.handle_async_request(request)
        try:
            original = json.loads(await request.aread())
        except ValueError:
            return await self.inner.handle_async_request(request)
        body = {**original, "chat_template_kwargs": {**THINKING_OFF, **original.get("chat_template_kwargs", {})}}
        response = await self.inner.handle_async_request(self._with_body(request, body))
        if response.status_code in (400, 422):
            text = (await response.aread()).decode("utf-8", "replace")
            if "chat_template_kwargs" in text or "enable_thinking" in text:
                log.info("model server does not take chat_template_kwargs; sending requests without it")
                self.supported = False
                return await self.inner.handle_async_request(self._with_body(request, original))
        return response

    @staticmethod
    def _with_body(request: httpx.Request, body: dict) -> httpx.Request:
        headers = {k: v for k, v in request.headers.items() if k.lower() != "content-length"}
        return httpx.Request(request.method, request.url, headers=headers, content=json.dumps(body).encode())

    async def aclose(self) -> None:
        await self.inner.aclose()


def local_server_status(cfg: LLMConfig) -> tuple[bool | None, str]:
    """Is the local llama-server up and done loading its model?

    Returns (ready, detail); ready is None when the endpoint is not a local server we can probe
    (demo mode, OpenRouter). llama-server answers GET /health with 200 when ready, 503 while loading.
    """
    if cfg.model == "demo":
        return True, "demo mode"
    url = httpx.URL(cfg.base_url)
    if url.host not in ("127.0.0.1", "localhost"):
        return None, "remote endpoint"
    try:
        r = httpx.get(str(url.copy_with(path="/health")), timeout=1.5)
    except httpx.HTTPError:
        return False, "model server not running"
    if r.status_code == 200:
        return True, "ready"
    if r.status_code == 503:
        return False, "loading model"
    return False, f"model server answered {r.status_code}"


def make_model(cfg: LLMConfig, budget: RequestBudget | None = None) -> OpenAIChatModel:
    hooks = {"request": [budget.on_request]} if budget else {}
    http = httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_seconds, connect=10.0), event_hooks=hooks,
                             transport=ThinkingOffTransport() if cfg.thinking_off else None)
    # max_retries=0: the SDK's silent retries would hide failures, multiply waiting time on a
    # stuck request and spend budget; the pipeline decides what to retry.
    client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "not-needed",
                         http_client=http, max_retries=0)
    return OpenAIChatModel(cfg.model, provider=OpenAIProvider(openai_client=client))


QUOTA_MESSAGE = ("The model service's usage limit is used up (for example a daily limit or no credits left). "
                  "It is not busy: trying again now will not help. Ask your IT team, or try again after the limit resets.")
_QUOTA_WORDS = re.compile(r"per[- ]day|per[- ]month|daily|quota|credits?|insufficient|billing|exceeded your",
                          re.IGNORECASE)


def quota_exhausted(e: BaseException | None) -> bool:
    """A 429 that says a usage allowance is used up (OpenRouter: "Rate limit exceeded: free-models-
    per-day"), not that the endpoint is momentarily busy: retrying only wastes time (OpenRouter eval,
    19 Oct 2026: three retries, then "busy or still loading")."""
    status = getattr(e, "status_code", None)
    return status in (402, 429) and bool(_QUOTA_WORDS.search(str(getattr(e, "body", "") or e)))


def _worth_retrying(e: Exception) -> bool:
    """A busy or overloaded endpoint, a dropped connection: not a bad request or a wrong key."""
    if quota_exhausted(e):
        return False
    if isinstance(e, ModelHTTPError):
        return e.status_code in (408, 429) or e.status_code >= 500
    if isinstance(e, openai.APIStatusError):
        return e.status_code in (408, 429) or e.status_code >= 500
    # OpenRouter reports an overloaded provider inside a 200 stream: an APIError with a body code
    if isinstance(e, openai.APIError):
        code = (e.body or {}).get("code") if isinstance(e.body, dict) else None
        return code is None or (isinstance(code, int) and (code in (408, 429) or code >= 500))
    return isinstance(e, ModelAPIError)


RATE_LIMIT_WAIT = 2.5  # times the usual wait between tries, after a 429


class RetryingModel(WrapperModel):
    """Retries one model call that fails before its reply starts (an overloaded provider, a
    dropped connection), so that a chat run keeps the tool results it already has instead of
    starting over. A reply that fails halfway is not retried here: its text was already shown."""

    def __init__(self, wrapped: Model, attempts: int = 3, delay_s: float = 2.0,
                 budget: RequestBudget | None = None):
        super().__init__(wrapped)
        self.attempts, self.delay_s, self.budget = attempts, delay_s, budget

    async def _pause(self, attempt: int, e: Exception) -> None:
        log.warning("model call failed (attempt %d of %d), retrying: %s", attempt, self.attempts, describe_error(e))
        if self.budget:
            self.budget.ensure(1)
        # a rate limit counts requests per minute: waiting 2 s, then 4 s gave up in 6 s (half the answers of an
        # overnight test on a rate-limited endpoint failed, 28 Oct 2026); it is waited out longer (5 s, then 10 s)
        rate_limited = getattr(e, "status_code", None) == 429
        await asyncio.sleep(self.delay_s * attempt * (RATE_LIMIT_WAIT if rate_limited else 1))

    async def request(self, messages, model_settings, model_request_parameters) -> ModelResponse:
        for attempt in range(1, self.attempts + 1):
            try:
                return await self.wrapped.request(messages, model_settings, model_request_parameters)
            except Exception as e:
                if attempt == self.attempts or not _worth_retrying(e):
                    raise
                await self._pause(attempt, e)
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters,
                             run_context=None) -> AsyncIterator[StreamedResponse]:
        async with AsyncExitStack() as stack:
            for attempt in range(1, self.attempts + 1):
                try:
                    stream = await stack.enter_async_context(self.wrapped.request_stream(
                        messages, model_settings, model_request_parameters, run_context))
                    break
                except Exception as e:
                    if attempt == self.attempts or not _worth_retrying(e):
                        raise
                    await self._pause(attempt, e)
            yield stream


def describe_error(e: Exception) -> str:
    """The provider's own words, for the log (never shown to users, never containing the key)."""
    parts = [type(e).__name__, str(e)[:300]]
    for attr in ("status_code", "body"):
        value = getattr(e, attr, None)
        if value:
            parts.append(f"{attr}={str(value)[:500]}")
    if e.__cause__ is not None and e.__cause__ is not e:
        parts.append(f"cause={type(e.__cause__).__name__}: {str(e.__cause__)[:300]}")
    return " | ".join(parts)


# Regenerate: the same question asked again for a different answer. At temperature 0 a model gives
# the same reply every time (and a cached prompt makes it come back in seconds), so a regenerated
# answer is sampled (seen in the final test of 15 Oct 2026)
REGENERATING: ContextVar[bool] = ContextVar("regenerating", default=False)
REGENERATE_TEMPERATURE = 0.7


def run_settings() -> dict | None:
    """Model settings for this one call: sampled when regenerating, else the model's own."""
    return {"temperature": REGENERATE_TEMPERATURE} if REGENERATING.get() else None
