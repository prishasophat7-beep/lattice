"""The warm-up request at start-up must not break the questions that follow.

It once ran on its own event loop: the model's pooled HTTP connection then belonged to a closed
loop, and the first real question failed with "Event loop is closed". Only a real socket server
with keep-alive shows this, so this test runs a tiny OpenAI-compatible server on localhost.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from conftest import write_markdown_kb
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings


class StubLlama(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like llama-server: the client pools the connection
    requests: list[dict] = []

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # /health and /v1/models
        self._send({"status": "ok", "data": [{"id": "local"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        StubLlama.requests.append(body)
        system = body["messages"][0]["content"]
        text = ('{"section_ids": ["A3"]}' if "You find where" in system else
                '{"found": true, "answer": "Every 500 cycles.", "section_ids": ["A3"]}')
        self._send({"id": "x", "object": "chat.completion", "created": 0, "model": "local",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def log_message(self, *args):
        pass


def test_first_question_after_warm_up_works(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubLlama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    StubLlama.requests = []
    try:
        data = tmp_path / "data"
        bundle.build_bundle(write_markdown_kb(tmp_path / "md"), "markdown", "v1", tmp_path / "kb.zip")
        bundle.import_bundle(tmp_path / "kb.zip", data)
        llm = LLMConfig(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="local")
        with TestClient(create_app(Settings(data_dir=data, llm=llm))) as client:
            deadline = time.monotonic() + 10
            while not StubLlama.requests and time.monotonic() < deadline:
                time.sleep(0.05)
            first = StubLlama.requests[0] if StubLlama.requests else {}
            assert (first.get("max_tokens") or first.get("max_completion_tokens")) == 1  # the warm-up
            r = client.post("/api/ask", json={"question": "How often should the DT-450 be calibrated?"})
            assert r.status_code == 200, r.text
            assert r.json()["answer"] == "Every 500 cycles."
    finally:
        server.shutdown()
