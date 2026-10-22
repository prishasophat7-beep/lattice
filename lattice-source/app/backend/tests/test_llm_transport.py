import asyncio
import json

import httpx

from oasis_engine.config import Connection, effective_llm
from oasis_engine.llm import LLMConfig, ThinkingOffTransport

URL = "http://llm.lan/v1/chat/completions"


def post(transport, body):
    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.post(URL, json=body)
    return asyncio.run(go())


def test_thinking_is_turned_off_for_a_remote_model_unless_reasoning_is_on():
    remote = Connection(kind="remote", base_url="http://llm.lan/v1", model="qwen3.6-27b")
    assert effective_llm(LLMConfig(), remote).thinking_off
    assert not effective_llm(LLMConfig(), remote.model_copy(update={"reasoning": True})).thinking_off
    assert not effective_llm(LLMConfig(), Connection()).thinking_off  # the built-in server: --reasoning off


def test_the_request_asks_the_server_not_to_think():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})
    r = post(ThinkingOffTransport(httpx.MockTransport(handler)), {"model": "m", "messages": []})
    assert r.status_code == 200 and seen[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_server_that_rejects_the_field_gets_the_request_without_it():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "chat_template_kwargs" in body:
            return httpx.Response(400, json={"error": "Unrecognized request argument: chat_template_kwargs"})
        return httpx.Response(200, json={"ok": True})
    transport = ThinkingOffTransport(httpx.MockTransport(handler))
    assert post(transport, {"model": "m"}).status_code == 200
    assert post(transport, {"model": "m"}).status_code == 200
    # one rejected request, then the field is no longer sent
    assert ["chat_template_kwargs" in b for b in seen] == [True, False, False]
