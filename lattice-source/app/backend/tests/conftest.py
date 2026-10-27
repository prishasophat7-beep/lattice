from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

TORQUE_MANUAL = """\
Maintenance manual for line 3.

# Station 4 Torque Tool Manual
Covers the DT-450 torque driver.

## Setup
### Calibration
Calibrate every 500 cycles. Set torque to 12 Nm for M6 bolts.
### Bit replacement
Use bit type T30. Replace after 10,000 cycles.

## Troubleshooting
### Error E17
E17 means torque not reached. Check air pressure is above 6 bar.

```text
# this is not a heading, it is inside a code block
```

### Error E22
E22 indicates sensor fault. Power cycle, then call maintenance.
"""


def write_markdown_kb(root: Path, summaries: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "torque-tool.md").write_text(TORQUE_MANUAL, encoding="utf-8")
    if summaries:
        (root / "torque-tool.summaries.json").write_text(json.dumps(summaries), encoding="utf-8")
    return root


def write_pageindex_kb(root: Path) -> Path:
    doc = root / "docs" / "pi-test"
    doc.mkdir(parents=True)
    (doc / "doc.json").write_text(json.dumps({
        "id": "pi-test", "name": "press-manual.pdf", "description": "Hydraulic press manual.",
        "status": "completed", "pageNum": 3}), encoding="utf-8")
    (doc / "tree.json").write_text(json.dumps([
        {"title": "Safety", "node_id": "0000", "start_index": 1, "end_index": 1,
         "summary": "Lockout procedure and PPE for the hydraulic press."},
        {"title": "Operation", "node_id": "0001", "start_index": 2, "end_index": 3, "nodes": [
            {"title": "Start-up", "node_id": "0002", "start_index": 2, "end_index": 2,
             "summary": "Start-up checks: oil level, pressure 180 bar."},
            {"title": "Shutdown", "node_id": "0003", "start_index": 3, "end_index": 3},
        ]},
    ]), encoding="utf-8")
    (doc / "pages.json").write_text(json.dumps([
        {"page_index": 1, "markdown": "Safety\nAlways apply lockout before maintenance."},
        {"page_index": 2, "markdown": "Operation\nStart-up\nCheck oil level. Set pressure to 180 bar."},
        {"page_index": 3, "markdown": "Shutdown\nRelease pressure, then switch off the pump."},
    ]), encoding="utf-8")
    return root


class ScriptedModel:
    """A FunctionModel that plays back canned JSON replies for the select and answer steps."""

    def __init__(self, select: list, answer: list, route: list | None = None, summary: list | None = None):
        self.select, self.answer = list(select), list(answer)
        self.queues = {"select": self.select, "answer": self.answer,
                       "route": list(route or []), "summary": list(summary or [])}
        self.prompts: list[tuple[str, str]] = []
        self.model = FunctionModel(self._reply, stream_function=self._stream)

    async def _stream(self, messages, info: AgentInfo):
        """The same replies, streamed in chunks (the API's streaming endpoint uses this)."""
        text = self._reply(messages, info).parts[0].content
        for i in range(0, len(text), 9):
            yield text[i:i + 9]

    def _reply(self, messages, info: AgentInfo) -> ModelResponse:
        instructions = info.instructions or ""
        step = ("route" if "You sort requests" in instructions else
                "summary" if "You summarize a document" in instructions else
                "select" if "You find where" in instructions else "answer")
        last_prompt = ""
        for m in messages:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                        last_prompt = p.content
        self.prompts.append((step, last_prompt))
        queue = self.queues[step]
        if not queue:
            raise AssertionError(f"Unexpected extra {step} call")
        reply = queue.pop(0)
        if isinstance(reply, str):  # a plain-text reply, as the "text" reply format reads it
            return ModelResponse(parts=[TextPart(reply)])
        reply = dict(reply)
        finish = reply.pop("_finish", None)
        return ModelResponse(parts=[TextPart(json.dumps(reply))], finish_reason=finish)


@pytest.fixture
def markdown_kb(tmp_path):
    return write_markdown_kb(tmp_path / "md")


@pytest.fixture
def pageindex_kb(tmp_path):
    return write_pageindex_kb(tmp_path / "pi")


class ChatScript:
    """A streaming FunctionModel for chat mode: each step is {"tool": name, "args": {...}} (a tool
    call) or {"text": "..."} (the answer, streamed in small chunks)."""

    def __init__(self, steps: list[dict], route: dict | None = None, final: str | None = None):
        self.steps = list(steps)
        self.final = final  # the answer from what was gathered, at the step limit (not streamed)
        self.seen: list[list] = []  # messages the model received at each step
        self.route = route or {"kind": "question", "topic": ""}  # reply to the manuals router
        self.model = FunctionModel(self._route, stream_function=self._stream)

    def _route(self, messages, info: AgentInfo) -> ModelResponse:
        """The request-sorting step (not streamed); the chat agent's own steps are streamed."""
        if self.final is not None and "What was found in the documents" in str(messages[-1]):
            return ModelResponse(parts=[TextPart(self.final)])
        assert "You sort requests" in (info.instructions or ""), "unexpected non-streamed call"
        return ModelResponse(parts=[TextPart(json.dumps(self.route))])

    async def _stream(self, messages, info: AgentInfo):
        from pydantic_ai.models.function import DeltaToolCall
        self.seen.append(list(messages))
        if not self.steps:
            raise AssertionError("Unexpected extra chat model call")
        step = self.steps.pop(0)
        if "tool" in step:
            yield {0: DeltaToolCall(name=step["tool"], json_args=json.dumps(step.get("args", {})),
                                    tool_call_id=f"call{len(self.seen)}")}
        else:
            text = step["text"]
            for i in range(0, len(text), 7):
                yield text[i:i + 7]


def make_pdf(path: Path, pages: list[str], figure_pages: set[int] = frozenset()) -> Path:
    """A minimal PDF: one line of text per page, and a picture filling half of each figure page."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    font = 3
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    image = 4
    pixels = bytes([200, 30, 30] * 4)
    objs.append(b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB "
                b"/BitsPerComponent 8 /Length %d >>\nstream\n" % len(pixels) + pixels + b"\nendstream")
    for n, text in enumerate(pages, start=1):
        content = b"BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text.encode()
        if n in figure_pages:
            content += b" q 400 0 0 300 100 300 cm /Im1 Do Q"
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        contents = len(objs)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 %d 0 R >> /XObject << /Im1 %d 0 R >> >> >>"
                    % (contents, font, image))
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path
