"""Demo mode (OASIS_LLM_MODEL=demo): a stand-in "model" with no language model behind it.

Picks the table-of-contents line with the most words in common with the question and
answers by quoting the start of that section. Used to develop and demo the UI without
a model server or API budget. Every answer says it is a demo.
"""
from __future__ import annotations

import json
import re

from pydantic_ai import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .search import tokenize

_LINE = re.compile(r"^\s*\[([A-Z]-?[\w]+)\]\s*(.*)$")


def _last_prompt(messages) -> str:
    text = ""
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    text = p.content
    return text


def _reply(messages, info: AgentInfo) -> ModelResponse:
    prompt = _last_prompt(messages)
    if "You summarize a document" in (info.instructions or ""):
        # the document's own description and first headings (a summary request failed as "an
        # unusable reply" in demo mode: flow audit, 20 Oct 2026)
        about = re.search(r"DESCRIPTION: (.*)", prompt)
        outline = prompt.split("TABLE OF CONTENTS:", 1)[-1].split("\n\nREQUEST:", 1)[0]
        heads = [line.split(" (", 1)[0].strip() for line in outline.splitlines() if line.strip() and not line.startswith("  ")]
        return ModelResponse(parts=[TextPart(json.dumps({
            "overview": f"[Demo mode, no language model] {about.group(1) if about else 'A document.'}",
            "points": heads[:5]}))])
    question = prompt.rsplit("QUESTION:", 1)[-1]
    q_tokens = set(tokenize(question))
    if "You find where" in (info.instructions or ""):
        best, best_score = None, 0
        for line in prompt.splitlines():
            m = _LINE.match(line)
            if m:
                score = len(q_tokens & set(tokenize(m.group(2))))
                if score > best_score:
                    best, best_score = m.group(1), score
        choice: dict = {"section_ids": [best] if best else []}
        if "standalone_question" in (info.instructions or "") + prompt:  # a follow-up: asked as it stands
            choice["standalone_question"] = " ".join(question.split())
        return ModelResponse(parts=[TextPart(json.dumps(choice))])
    excerpt = re.search(r"=== \[([^\]]+)\][^\n]*===\n(.*?)(?=\n=== \[|\n+QUESTION:|\Z)", prompt, re.S)
    if not excerpt:
        body = {"found": False, "answer": "[Demo] Nothing to quote.", "section_ids": []}
    else:
        text = " ".join(re.sub(r"\[page \d+\]", "", excerpt.group(2)).split())
        body = {"found": True, "section_ids": [excerpt.group(1)],
                "answer": f"[Demo mode, no language model] The section starts: \"{text[:280]}...\""}
    return ModelResponse(parts=[TextPart(json.dumps(body))])


_SNIPPET = re.compile(r"^\[(S\d+)\] ([^\n]*?) \([^\n]*?\): (.*)$", re.M)


async def _stream(messages, info: AgentInfo):
    """Chat mode streams its answers: quote the best search match the question came with."""
    if "You find where" in (info.instructions or "") or "EXCERPTS:" in _last_prompt(messages):
        yield _reply(messages, info).parts[0].content  # the manuals steps, streamed in one piece
        return
    m = _SNIPPET.search(_last_prompt(messages))
    yield (f"[Demo mode, no language model] The closest section is {m.group(2)}: \"{m.group(3)[:280]}\" [{m.group(1)}]"
           if m else "[Demo mode, no language model] No section matches this question.")


def demo_model() -> FunctionModel:
    return FunctionModel(_reply, stream_function=_stream, model_name="demo")
