"""What actually goes over the wire to an OpenAI-compatible server (llama.cpp, vLLM).

Some chat templates are strict: Qwen's in llama.cpp raises an error unless there is exactly one
system message and it comes first. FunctionModel tests cannot see this, so these tests capture
the real request bodies from the OpenAI client.
"""
import asyncio
import json

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from oasis_engine.chat import ChatAgent
from oasis_engine.loaders import load_markdown
from oasis_engine.pipeline import Pipeline
from oasis_engine.search import SectionIndex


def capturing_model(replies: list[str]):
    """An OpenAI chat model whose server is a stub: records each request, answers with `replies`."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        text = replies[min(len(bodies), len(replies)) - 1]
        if body.get("stream"):
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "local",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "local",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    client = AsyncOpenAI(base_url="http://stub/v1", api_key="k",
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return OpenAIChatModel("local", provider=OpenAIProvider(openai_client=client)), bodies


def roles(body: dict) -> list[str]:
    return [m["role"] for m in body["messages"]]


def test_chat_sends_one_system_message_first(markdown_kb):
    docs = load_markdown(markdown_kb)
    model, bodies = capturing_model(["The DT-450 manual."])
    asyncio.run(ChatAgent(model).ask("Which manuals do you have?", docs, SectionIndex(docs),
                                     context=[("Earlier question", "Earlier answer")]))
    assert roles(bodies[0]) == ["system", "user", "assistant", "user"]
    assert "Documents installed on this machine" in bodies[0]["messages"][0]["content"]


def test_manuals_pipeline_sends_one_system_message_first(markdown_kb):
    docs = load_markdown(markdown_kb)
    model, bodies = capturing_model(['{"section_ids": ["A3"]}',
                                     '{"found": true, "answer": "Every 500 cycles.", "section_ids": ["A3"]}'])
    asyncio.run(Pipeline(docs, model, output_mode="native").ask("How often to calibrate?"))
    assert [roles(b) for b in bodies] == [["system", "user"], ["system", "user"]]
