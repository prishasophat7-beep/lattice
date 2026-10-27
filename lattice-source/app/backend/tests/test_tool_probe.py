"""Test connection checks that the server turns tool calls into calls (Chat mode searches with them)."""
import httpx

from oasis_engine import api
from oasis_engine.llm import LLMConfig

CFG = LLMConfig(base_url="http://server:8000/v1", model="qwen", api_key="")


def _reply(monkeypatch, status, payload=None, text=""):
    def post(url, **kw):
        request = httpx.Request("POST", url)
        if payload is not None:
            return httpx.Response(status, json=payload, request=request)
        return httpx.Response(status, text=text, request=request)
    monkeypatch.setattr(api.httpx, "post", post)


def _message(**message):
    return {"choices": [{"message": message}]}


def test_real_tool_calls_pass(monkeypatch):
    _reply(monkeypatch, 200, _message(content=None, tool_calls=[{"function": {"name": "search_manuals"}}]))
    assert api.probe_tools(CFG) == ""


def test_tool_calls_written_as_text_are_reported_with_the_vllm_fix(monkeypatch):
    # vLLM serving Qwen without --enable-auto-tool-choice --tool-call-parser hermes
    _reply(monkeypatch, 200, _message(content='<tool_call>\n{"name": "search_manuals", "arguments": {}}\n</tool_call>'))
    warning = api.probe_tools(CFG)
    assert warning == api.TOOLS_TEXT and "--tool-call-parser hermes" in warning


def test_a_server_that_refuses_tools_is_reported(monkeypatch):
    _reply(monkeypatch, 400, text='{"error": "\\"auto\\" tool choice requires --enable-auto-tool-choice"}')
    assert api.probe_tools(CFG) == api.TOOLS_REFUSED


def test_an_unreachable_check_does_not_claim_chat_is_broken(monkeypatch):
    def post(url, **kw):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(api.httpx, "post", post)
    assert "may still work" in api.probe_tools(CFG)
