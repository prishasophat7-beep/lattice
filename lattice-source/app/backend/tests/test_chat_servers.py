"""Chat mode against how model servers actually behave: thinking left in the reply (Qwen 3.x on vLLM
without a reasoning parser) and tool calls passed on as text (no tool-call parser)."""
import json

from conftest import ChatScript
from test_chat import office  # noqa: F401  (the fixture)


def _stream(client, question):
    r = client.post("/api/ask/stream", json={"question": question, "mode": "chat", "conversation_id": "c1"})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    shown = "".join(e.get("text", "") for e in events if e.get("stage") == "delta")
    return events[-1]["result"], shown


def test_thinking_in_the_reply_is_neither_shown_nor_kept(office):  # noqa: F811
    client = office(ChatScript([{"text": "<think>The user asks about E17. Section S1 says torque not reached."
                                         "</think>\nE17 means torque not reached [S1]."}]))
    result, shown = _stream(client, "What is E17?")
    assert result["answer"] == "E17 means torque not reached [1]."
    assert "think" not in shown and "The user asks" not in shown and shown.strip().startswith("E17 means")


def test_cut_off_thinking_is_not_the_answer(office):  # noqa: F811
    client = office(ChatScript([{"text": "<think>The user asks about E17 and I should check"}]))
    result, shown = _stream(client, "What is E17?")
    assert "The user asks" not in result["answer"] and "The user asks" not in shown


def test_a_tool_call_written_as_text_is_answered_from_what_was_found(office):  # noqa: F811
    script = ChatScript([{"text": '<tool_call>\n{"name": "search_manuals", "arguments": {"query": "E17"}}\n</tool_call>'}],
                        final="E17 means torque not reached [S1].")
    client = office(script)
    result, _ = _stream(client, "What is E17?")
    assert "tool_call" not in result["answer"] and result["answer"].startswith("E17 means torque not reached")


def test_reading_the_same_section_again_gets_a_pointer_not_the_text(office):  # noqa: F811
    script = ChatScript([{"tool": "read_section", "args": {"ref": "S1"}},
                         {"tool": "read_section", "args": {"ref": "S1"}},
                         {"text": "E17 means torque not reached [S1]."}])
    client = office(script)
    result, _ = _stream(client, "What is E17?")
    assert result["answer"].startswith("E17 means torque not reached")
    second = json.dumps([m.parts for m in script.seen[2]], default=str)
    assert "already read this" in second
