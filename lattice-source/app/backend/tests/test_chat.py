import json

import pytest
from fastapi.testclient import TestClient

from conftest import ChatScript, write_markdown_kb
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.config import AppConfig, save_config
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings


def parts_text(messages) -> str:
    return json.dumps([m.parts for m in messages], default=str)


@pytest.fixture
def office(tmp_path):
    """A client for the office profile (chat on) over the Markdown test manual."""
    def make(script, profile="office"):
        data = tmp_path / "data"
        bundle.build_bundle(write_markdown_kb(tmp_path / "md"), "markdown", "v1", tmp_path / "kb.zip",
                            documents=[{"id": "torque-tool.md", "title": "DT-450 manual", "tags": ["S4"]}])
        save_config(data, AppConfig(profile=profile))
        settings = Settings(data_dir=data, llm=LLMConfig(output_mode="prompted"))
        client = TestClient(create_app(settings, model=script.model))
        assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
        return client
    return make


def test_chat_searches_reads_and_cites(office):
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}},
                         {"tool": "read_section", "args": {"ref": "S1"}},
                         {"text": "E17 means torque not reached [S1]. Check air pressure is above 6 bar [S1]."}])
    client = office(script)
    r = client.post("/api/ask", json={"question": "What is E17?", "mode": "chat", "conversation_id": "c1"}).json()
    assert r["mode"] == "chat" and r["found"]
    assert r["answer"] == "E17 means torque not reached [1]. Check air pressure is above 6 bar [1]."
    assert [c["title"] for c in r["citations"]] == ["Error E17"] and r["citations"][0]["doc_name"] == "DT-450 manual"
    assert r["unverified_numbers"] == []
    assert "E17 means torque not reached" in parts_text(script.seen[2])  # the model read the section


def test_general_answer_has_no_sources(office):
    client = office(ChatScript([{"text": "Torque is a turning force, measured in newton metres."}]))
    r = client.post("/api/ask", json={"question": "What is torque?", "mode": "chat"}).json()
    assert r["citations"] == [] and r["mode"] == "chat" and r["unverified_numbers"] == []


def test_unknown_references_are_dropped(office):
    client = office(ChatScript([{"text": "It is 12 Nm [S9]."}]))
    r = client.post("/api/ask", json={"question": "M6 torque?", "mode": "chat"}).json()
    assert r["answer"] == "It is 12 Nm." and r["citations"] == []


def test_chat_uses_the_conversation(office):
    script = ChatScript([{"text": "Calibrate every 500 cycles."}, {"text": "The torque is 12 Nm."}])
    client = office(script)
    client.post("/api/ask", json={"question": "How often to calibrate?", "mode": "chat", "conversation_id": "c9"})
    client.post("/api/ask", json={"question": "and the torque?", "mode": "chat", "conversation_id": "c9"})
    earlier = parts_text(script.seen[1])
    assert "How often to calibrate?" in earlier and "Calibrate every 500 cycles." in earlier


def test_station_filter_limits_what_chat_can_search(office):
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}}, {"text": "Not found."}])
    client = office(script)
    client.post("/api/ask", json={"question": "E17?", "mode": "chat", "tags": ["Paint"]})
    assert "No matching sections" in parts_text(script.seen[1])


def test_chat_stream_sends_tool_and_text_events(office):
    client = office(ChatScript([{"tool": "search_manuals", "args": {"query": "E22"}},
                                {"text": "E22 is a sensor fault [S1]."}]))
    with client.stream("POST", "/api/ask/stream", json={"question": "E22?", "mode": "chat"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    stages = [e["stage"] for e in events]
    assert {"stage": "tool", "tool": "search_manuals", "detail": "E22"} in events
    assert "delta" in stages and stages[-1] == "done"
    assert "".join(e["text"] for e in events if e["stage"] == "delta") == "E22 is a sensor fault [S1]."
    assert events[-1]["result"]["answer"] == "E22 is a sensor fault [1]."


def test_chat_is_refused_in_the_line_profile(office):
    client = office(ChatScript([]), profile="line")
    assert client.post("/api/ask", json={"question": "Hi", "mode": "chat"}).status_code == 403
    health = client.get("/api/health").json()
    assert health["chat_available"] is False and health["default_mode"] == "manuals"


def test_config_round_trip_keeps_and_masks_the_key(office):
    client = office(ChatScript([]))
    remote = {"kind": "remote", "base_url": "http://vllm.test:8000/v1", "model": "qwen", "api_key": "sk-secret"}
    r = client.put("/api/config", json={"profile": "office", "default_mode": "chat", "connection": remote})
    assert r.status_code == 200 and r.json()["connection"]["api_key_set"] and "sk-secret" not in r.text
    r = client.put("/api/config", json={"profile": "office", "connection": dict(remote, api_key=None, model="q2")})
    assert r.json()["connection"]["api_key_set"] and r.json()["connection"]["model"] == "q2"
    assert client.get("/api/config").json()["chat_available"]
    assert "sk-secret" not in client.get("/api/config").text
    bad = client.put("/api/config", json={"profile": "office", "connection": {"kind": "remote", "base_url": "vllm"}})
    assert bad.status_code == 422


def test_connection_test_reports_unreachable_endpoints(office):
    client = office(ChatScript([]))
    r = client.post("/api/config/test", json={"kind": "remote", "base_url": "http://127.0.0.1:9/v1", "model": "x"})
    assert r.json()["ok"] is False and ("Cannot connect" in r.json()["detail"] or "Cannot reach" in r.json()["detail"])
    r = client.post("/api/config/test", json={"kind": "remote", "base_url": "abc", "model": "x"})
    assert r.json()["ok"] is False and "http://" in r.json()["detail"]


def test_regenerate_replaces_the_last_turn(office):
    script = ChatScript([{"text": "First try."}, {"text": "Second try."}, {"text": "Follow-up answer."}])
    client = office(script)
    first = client.post("/api/ask", json={"question": "Q1?", "mode": "chat", "conversation_id": "c5"}).json()
    client.post("/api/ask", json={"question": "Q1?", "mode": "chat", "conversation_id": "c5",
                                  "replaces": first["history_id"]})
    turns = client.get("/api/history", params={"conversation_id": "c5"}).json()
    assert [t["answer"] for t in turns] == ["Second try."]
    assert "First try." not in parts_text(script.seen[1])  # the replaced answer is not context
    client.post("/api/ask", json={"question": "Q2?", "mode": "chat", "conversation_id": "c5"})
    assert "Second try." in parts_text(script.seen[2])


def test_the_app_window_may_save_settings(office):
    """The Tauri window runs on another origin: the browser checks PUT with a CORS preflight first."""
    client = office(ChatScript([]))
    pre = client.options("/api/config", headers={"Origin": "http://tauri.localhost",
                                                  "Access-Control-Request-Method": "PUT",
                                                  "Access-Control-Request-Headers": "authorization,content-type"})
    assert pre.status_code == 200 and "PUT" in pre.headers["access-control-allow-methods"]


def test_the_assistant_knows_the_installed_manuals(office):
    script = ChatScript([{"text": "The DT-450 manual is installed."}])
    office(script).post("/api/ask", json={"question": "Which manuals do you have?", "mode": "chat"})
    instructions = script.seen[0][-1].instructions
    assert "Documents installed on this machine" in instructions and "DT-450 manual" in instructions


def test_keyword_matches_come_with_the_question_and_can_be_cited(office):
    script = ChatScript([{"text": "E17 means torque not reached [S1]."}])  # no tool calls, like a small model
    r = office(script).post("/api/ask", json={"question": "What does error E17 mean?", "mode": "chat"}).json()
    prompt = parts_text(script.seen[0])
    assert "Possibly relevant document sections" in prompt and "[S1]" in prompt
    assert [c["title"] for c in r["citations"]] == ["Error E17"] and r["answer"].endswith("[1].")


def test_references_in_other_brackets_are_resolved_too(office):
    client = office(ChatScript([{"text": "E17 means torque not reached (S1). See also (S1 and S9)."}]))
    r = client.post("/api/ask", json={"question": "What does error E17 mean?", "mode": "chat"}).json()
    assert r["answer"] == "E17 means torque not reached [1]. See also [1]." and len(r["citations"]) == 1
    assert "S1" not in r["answer"] and "S9" not in r["answer"]


def test_chat_mode_can_use_its_own_model(office):
    client = office(ChatScript([]))
    chat_remote = {"kind": "remote", "base_url": "https://openrouter.ai/api/v1", "model": "some/model:free",
                   "api_key": "sk-chat", "reasoning": True}
    r = client.put("/api/config", json={"profile": "office", "connection": {"kind": "local"},
                                        "chat_connection": chat_remote})
    body = r.json()
    assert r.status_code == 200 and body["connection"]["kind"] == "local"
    assert body["chat_connection"]["model"] == "some/model:free" and body["chat_connection"]["api_key_set"]
    assert "sk-chat" not in r.text
    engine = client.app.state.engine
    assert engine.llm.model != "some/model:free" and engine.chat_llm.model == "some/model:free"
    assert engine.chat_llm.max_tokens >= 6000  # reasoning model: room to think
    # saving again without a key keeps the stored chat key
    r = client.put("/api/config", json={"profile": "office", "chat_connection": dict(chat_remote, api_key=None)})
    assert r.json()["chat_connection"]["api_key_set"]
    # back to one model for both modes
    r = client.put("/api/config", json={"profile": "office"}).json()
    assert r["chat_connection"] is None and client.app.state.engine.chat_llm is client.app.state.engine.llm


def test_a_reply_that_keeps_repeating_is_cut_off(office):
    unit = "**Regarding the tool head:** see section [S1], page 2. The diagram shows the tool head assembly.\n"
    client = office(ChatScript([{"text": "Here is what I found.\n" + unit * 15}]))
    r = client.post("/api/ask", json={"question": "What about the tool head?", "mode": "chat"}).json()
    assert r["answer"].count("Regarding the tool head") <= 2 and r["answer"].endswith("*(The answer was cut off.)*")


def test_bare_reference_lists_are_removed(office):
    client = office(ChatScript([{"text": "Calibrate every 500 cycles [S1].\n\n**References:**\n* [S1]"}]))
    r = client.post("/api/ask", json={"question": "Calibration interval?", "mode": "chat"}).json()
    assert r["answer"] == "Calibrate every 500 cycles [1]."


def test_section_references_written_out_become_sources(office):
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "M6 torque"}},
                         {"text": "It is 12 Nm.\n\n* **Source:** Section S1 states the torque."}])
    r = office(script).post("/api/ask", json={"question": "M6 torque?", "mode": "chat"}).json()
    assert "S1" not in r["answer"] and "[1]" in r["answer"] and len(r["citations"]) == 1


def test_instructions_copied_into_the_answer_are_left_out():
    from oasis_engine.chat import _without_tool_talk
    leaked = ("The weather is not in the manuals.\n\nTo show pictures, call find_pictures: the app shows "
              "the pages it returns next to your reply. You cannot see those pictures.")
    assert _without_tool_talk(leaked) == "The weather is not in the manuals."
    assert _without_tool_talk("Set 12 Nm [S1].\n\n- step one") == "Set 12 Nm [S1].\n\n- step one"


def test_a_general_knowledge_line_inside_a_paragraph_is_set_apart():
    # seen with the server model: "*General knowledge:*" on a line of its own within a paragraph
    from oasis_engine.chat import mark_general
    text, found = mark_general("The manual does not define backlash. [1]  \n*General knowledge:* Backlash is lost motion.")
    assert found and "\n\n> **General knowledge:** Backlash is lost motion." in text


def test_a_part_that_says_the_documents_lack_what_it_then_gives_loses_that_sentence():
    # final computer-use check: "The documents do not state the RAM ... However, ... 4 GB [1]"
    from oasis_engine.chat import without_contradicted_not_covered as clean
    src = "System requirements: RAM 4 GB minimum. There are 10 paid holidays in 2026."
    out = clean("**Part 1:** The documents do not state the RAM needed. However, the system requirements list "
                "4 GB [1].", src)
    assert out == "**Part 1:** The system requirements list 4 GB [1]."
    kept = "The documents do not mention a VPN policy. Contact IT."
    assert clean(kept, src) == kept  # no figure from the documents follows: a real "not covered"
