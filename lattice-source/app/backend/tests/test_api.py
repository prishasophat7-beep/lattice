import json

from fastapi.testclient import TestClient

from conftest import ScriptedModel
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings


def make_client(tmp_path, script, token=None):
    settings = Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted"), api_token=token)
    return TestClient(create_app(settings, model=script.model))


def test_import_then_ask(pageindex_kb, tmp_path):
    bundle.build_bundle(pageindex_kb, "pageindex", "v1", tmp_path / "kb.zip")
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "180 bar.", "section_ids": ["A2"]}])
    client = make_client(tmp_path, script)

    assert client.get("/api/health").json()["status"] == "no_knowledge_base"
    assert client.post("/api/ask", json={"question": "Pressure?"}).status_code == 409

    r = client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")})
    assert r.status_code == 200, r.text
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["kb_version"] == "v1"
    assert client.get("/api/kb").json()["documents"][0]["sections"] == 4

    body = client.post("/api/ask", json={"question": "What pressure at start-up?"}).json()
    assert body["found"] and body["citations"][0]["pages"] == [2]


def test_stream_reports_progress_then_result(pageindex_kb, tmp_path):
    bundle.build_bundle(pageindex_kb, "pageindex", "v1", tmp_path / "kb.zip")
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "180 bar.", "section_ids": ["A2"]}])
    client = make_client(tmp_path, script)
    client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")})
    with client.stream("POST", "/api/ask/stream", json={"question": "Start-up pressure?"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    stages = [e["stage"] for e in events]
    assert "reading" in stages and stages[-1] == "done"
    assert next(e for e in events if e["stage"] == "reading")["sections"][0]["title"] == "Start-up"
    assert [e["text"] for e in events if e["stage"] == "draft"][-1] == "180 bar."  # streamed while written
    assert events[-1]["result"]["answer"] == "180 bar."


def test_cors_allows_tauri_webview_only(tmp_path):
    client = make_client(tmp_path, ScriptedModel([], []))
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    ok = client.options("/api/ask", headers={"Origin": "http://tauri.localhost", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://tauri.localhost"
    bad = client.options("/api/ask", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers


def test_bad_bundle_returns_readable_error(tmp_path):
    (tmp_path / "not-a-zip.zip").write_text("hello")
    client = make_client(tmp_path, ScriptedModel([], []))
    r = client.post("/api/kb/import", json={"path": str(tmp_path / "not-a-zip.zip")})
    assert r.status_code == 422 and "not a readable .zip" in r.json()["detail"]


def test_token_is_required_when_configured(tmp_path):
    client = make_client(tmp_path, ScriptedModel([], []), token="s3cret")
    assert client.get("/api/health").status_code == 401
    ok = client.get("/api/health", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_an_unexpected_failure_shows_a_plain_message_not_the_exception(pageindex_kb, tmp_path):
    from pydantic_ai.models.function import FunctionModel
    from oasis_engine.api import UNEXPECTED_ERROR

    def broken(messages, info):
        raise KeyError("internal detail the user should not see")
    bundle.build_bundle(pageindex_kb, "pageindex", "v1", tmp_path / "kb.zip")
    settings = Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted"))
    client = TestClient(create_app(settings, model=FunctionModel(broken)))
    client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")})
    with client.stream("POST", "/api/ask/stream", json={"question": "Start-up pressure?"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[-1]["stage"] == "error" and events[-1]["message"] == UNEXPECTED_ERROR
    assert "internal detail" not in json.dumps(events)
