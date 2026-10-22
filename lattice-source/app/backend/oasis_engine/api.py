"""Local HTTP API the Tauri UI talks to. Bound to 127.0.0.1 only.

    GET  /api/health                         status, active knowledge-base version, model
    GET  /api/kb                             active knowledge base: documents and sections
    GET  /api/kb/versions                    installed versions
    POST /api/kb/import                      {"path": "C:/.../kb.zip"}   import and activate
    POST /api/kb/activate                    {"kb_version": "..."}       switch or roll back

    POST /api/ask                            {"question", "tags", "conversation_id", "mode", "replaces"}
    POST /api/ask/stream                     same, as server-sent events: progress, then the result
    GET  /api/config                         admin settings: profile, default mode, model connection
    PUT  /api/config                         change them (the API key is kept unless a new one is sent)
    POST /api/config/test                    try a remote model endpoint; lists its models

    GET  /api/docs                           manuals with catalog title, tags, original-PDF availability
    GET  /api/tags                           station / line tags from the bundle catalog
    GET  /api/docs/{doc_id}/toc              a manual's table of contents
    GET  /api/docs/{doc_id}/sections/{id}    one section's text, for the source panel
    GET  /api/docs/{doc_id}/original         the original PDF, when the bundle includes it
    GET  /api/search?q=...&tags=...          keyword search over sections (no model)
    GET  /api/suggestions?tags=...           example questions for the empty screen
    GET  /api/history                        recently answered questions
    DELETE /api/history?conversation_id=     delete one conversation (and its attachments)
    POST /api/feedback                       {"history_id", "rating": "helpful"|"wrong", "comment"}
    GET  /api/feedback/summary               counts, for the Manage panel
    POST /api/feedback/export                {"path": "C:/.../feedback.json"}

When OASIS_API_TOKEN is set, every request needs "Authorization: Bearer <token>",
so other programs (or web pages) on the machine cannot call the backend.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
import json
import logging
import secrets
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.models import Model

from . import __version__, bundle
from .attachments import AttachmentError, AttachmentStore
from .chat import _ABOUT_SELF_OR_CHAT, HISTORY_TURNS, LIVE_REPLY, ChatAgent, live_only
from .config import (AppConfig, Connection, EmbeddingConnection, effective_llm, load_config, public_view,
                     save_config)
from .embeddings import EmbeddingClient, EmbeddingError, SemanticIndex
from .llm import (REGENERATING, BudgetExceeded, LLMConfig, RequestBudget, RetryingModel, local_server_status,
                  make_model)
from .loaders import IMAGE_NAME, KnowledgeBaseError, markdown_doc_id, table_description, useful_summary
from .models import Doc
from .figures import (MAX_FIGURES, MAX_LISTED, PictureRef, PictureRequest, figure_pages, picture_request,
                      fix_typos, is_visual, render_page, topic_tokens)
from .pipeline import (CLOSING_REPLIES, MAX_CLARIFY_OPTIONS, MAX_CONTEXT_TURNS, AskResult, Clarification, ClarifyOption,
                       Figure, ModelRanAway, ModelRefused, Pipeline, ProviderBusy, _ELLIPTICAL, fixed_reply, smalltalk_reply,
                       summary_request)
from .calc import CalcError, calculate
from .search import tokenize
from .vague import vague_clarification
from .focus import (focus_of, heading, mixed_parts, navigation, near, part_label, short_text, step_to, steps,
                    task_sections, walk_request)
from .settings import Settings
from .store import JsonlLog

log = logging.getLogger("oasis_engine.api")

MAX_SEARCH_HITS = 20
SECTION_VIEW_CHARS = 60_000
CLOSEST = 3  # sections offered instead, when an answer is not in the documents
SMALL_MODEL_B = 3.0  # a built-in model smaller than this (billions of parameters) answers from documents only
LOCAL_EMBEDDER_RECHECK_S = 15  # how often to ask the local embedding server which model it serves


class FocusRef(BaseModel):
    doc_id: str = Field(min_length=1, max_length=200)
    node_id: str = Field(min_length=1, max_length=64)


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default=[], max_length=20)
    # questions with the same id form a conversation: follow-ups are read in its context
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    mode: Literal["manuals", "chat"] = "manuals"
    # regenerate / edit: the history entry this answer replaces (it drops out of the conversation)
    replaces: str | None = Field(default=None, max_length=64)
    # manuals the user picked after a clarifying question: search only these, don't ask again
    doc_ids: list[str] = Field(default=[], max_length=20)
    # the section of the one picked manual that a clarifying question's option stands for
    section: str | None = Field(default=None, max_length=64)
    # Chat mode: ids of files attached with this question (uploaded first, see /api/attachments)
    attachments: list[str] = Field(default=[], max_length=10)
    # where the conversation is (the "Working in" line, see focus.py); None: as its last answer left
    # it. focus_off: the user closed that line, so the question is read without it
    focus: FocusRef | None = None
    focus_off: bool = False


class AttachmentUpload(BaseModel):
    conversation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(max_length=21_000_000)  # 15 MB of file, base64-encoded


class ConnectionUpdate(BaseModel):
    kind: Literal["local", "remote"] = "local"
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=500)  # None: keep the stored key
    output_mode: Literal["native", "prompted", "tool"] = "native"
    reasoning: bool = False


class EmbeddingUpdate(BaseModel):
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=500)  # None: keep the stored key


class ConfigUpdate(BaseModel):
    profile: Literal["line", "office"] = "line"
    default_mode: Literal["manuals", "chat"] = "manuals"
    work_topics_only: bool = True
    connection: ConnectionUpdate = ConnectionUpdate()
    chat_connection: ConnectionUpdate | None = None  # None: Chat mode uses the same model
    embeddings: EmbeddingUpdate | None = None  # None: no search by meaning


class ImportRequest(BaseModel):
    path: str


class ActivateRequest(BaseModel):
    kb_version: str


class FeedbackRequest(BaseModel):
    history_id: str = Field(min_length=1, max_length=64)
    rating: Literal["helpful", "wrong"]
    comment: str = Field(default="", max_length=2000)


class ExportRequest(BaseModel):
    path: str


class Engine:
    """Holds the active knowledge base, the pipeline built on it, and the local logs."""

    def __init__(self, settings: Settings, model: Model | None = None):
        self.settings = settings
        self.budget = (RequestBudget(settings.daily_budget, settings.data_dir / "usage.json")
                       if settings.daily_budget else None)
        self.injected_model = model
        if model is None and settings.llm.model == "demo":
            from .demo import demo_model
            self.injected_model = demo_model()
            settings.llm.output_mode = "prompted"
        self.config: AppConfig = load_config(settings.data_dir)
        self.llm: LLMConfig = settings.llm
        self.chat_llm: LLMConfig = settings.llm
        self.model: Model = self.injected_model or make_model(settings.llm, self.budget)
        self.chat: ChatAgent | None = None
        self._remote_status: tuple[float, bool | None, str] = (0.0, None, "")
        self.history = JsonlLog(settings.data_dir / "history.jsonl")
        self.feedback = JsonlLog(settings.data_dir / "feedback.jsonl")
        self.manifest: bundle.Manifest | None = None
        self.pipeline: Pipeline | None = None
        self.catalog: dict[str, bundle.DocEntry] = {}
        self.catalog_unmatched: list[str] = []  # catalog ids that name no document (a bundle mistake)
        self.load_error: str | None = None
        self.attachments = AttachmentStore(settings.data_dir / "attachments")
        try:  # files left behind by conversations never asked in, or deleted before this was done
            self.attachments.prune({conversation_of(h) for h in self.history.read(limit=None)})
        except OSError as e:
            log.warning("could not clean up attachments: %s", e)
        self.semantic: SemanticIndex | None = None  # with an embedding model (on this PC or a server)
        self._semantic_key: tuple[str, str, int] | None = None  # (endpoint, model, pipeline) of the index
        self._local_embedder: tuple[float, str] = (-LOCAL_EMBEDDER_RECHECK_S, "")  # (when asked, model)
        self.loop: asyncio.AbstractEventLoop | None = None  # the server's, once it runs
        self._served: dict[str, tuple[float, str]] = {}  # base URL -> (when asked, served model file)
        self.answering = 0  # answers being written now: a new knowledge base waits for them
        self.apply_config()

    def apply_config(self) -> None:
        """(Re)build the model, the manuals pipeline and the chat agent from the current settings."""
        self.llm = effective_llm(self.settings.llm, self.config.connection)
        self.model = self.injected_model or make_model(self.llm, self.budget)
        chat_conn = self.config.chat_connection
        if chat_conn is None:  # Chat mode uses the same model as Manuals mode
            self.chat_llm, chat_model = self.llm, self.model
        else:
            self.chat_llm = effective_llm(self.settings.llm, chat_conn)
            chat_model = self.injected_model or make_model(self.chat_llm, self.budget)
        if self.injected_model is None:  # a busy endpoint mid-conversation: retry that one call
            chat_model = RetryingModel(chat_model, budget=self.budget)
        self.chat = ChatAgent(chat_model, max_tokens=self.chat_llm.max_tokens,
                              work_topics_only=self.config.work_topics_only, budget=self.budget)
        self._remote_status = (0.0, None, "")
        self.reload()

    async def warm_up_when_ready(self, wait_s: float = 300) -> None:
        """Wait for the local model server, then send the pipeline's warm-up. Runs as a task on the
        server's own event loop: the model's HTTP connections belong to that loop."""
        if self.injected_model is not None or self.config.connection.kind != "local":
            return
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            ready, _ = await asyncio.to_thread(local_server_status, self.llm)
            if ready:
                if self.pipeline:
                    await self.pipeline.warm_up()
                return
            await asyncio.sleep(1.0)

    def model_status(self) -> tuple[bool | None, str]:
        if self.injected_model is not None:
            return True, "injected model"
        if self.config.connection.kind != "remote":
            return local_server_status(self.llm)
        checked, ready, detail = self._remote_status
        if time.monotonic() - checked > 30:  # a remote endpoint is probed at most every 30 s
            ready, detail = probe_endpoint(self.llm)[:2]
            self._remote_status = (time.monotonic(), ready, detail)
        return ready, detail

    def reload(self) -> None:
        self.semantic, self._semantic_key = None, None  # new documents: a new index
        try:
            loaded = bundle.load_active(self.settings.data_dir)
        except (bundle.BundleError, KnowledgeBaseError) as e:
            self.manifest, self.pipeline, self.catalog, self.load_error = None, None, {}, str(e)
            return
        self.load_error = None
        if loaded is None:
            self.manifest, self.pipeline, self.catalog = None, None, {}
            return
        self.manifest, docs = loaded
        markdown = self.manifest.source_format == "markdown"
        self.catalog = {(markdown_doc_id(e.id) if markdown else e.id): e for e in self.manifest.documents}
        for d in docs:  # the catalog's title and description win over what the files say
            entry = self.catalog.get(d.doc_id)
            if entry:
                d.title = entry.title or ""
                # ... except a table read as one line ("Part number Description Price (INR) ..."):
                # the export's summary of a spreadsheet; the cards showed it raw (final check)
                # a summary that talks about missing content ("Section 4 is titled Options but contains no
                # descriptive content") is wrong all through; the catalog keeps it as given, so it is
                # dropped here, also for a knowledge base imported before this rule
                d.description = useful_summary(entry.description or "", whole=True) or d.description
                d.description = table_description(d) or d.description
            # what list_documents can filter on, for every bundle format
            # the original file's type: from a title like "budget.xlsx", else from the file itself
            titled = re.fullmatch(r".+\.([A-Za-z0-9]{2,5})", d.display_title)
            suffix = (titled.group(1) if titled else Path(d.name).suffix.lstrip(".")).lower()
            if suffix and "type" not in d.meta:
                d.meta["type"] = suffix
            if d.kind == "pages" and d.pages and "pages" not in d.meta:
                d.meta["pages"] = str(len(d.pages))
            if markdown and "/" in d.name.replace("\\", "/") and "folder" not in d.meta:
                d.meta["folder"] = d.name.replace("\\", "/").rsplit("/", 1)[0]
            tags = self.tags_of(d)
            if tags:
                d.meta["tags"] = ", ".join(tags)
        self.catalog_unmatched = [e.id for key, e in self.catalog.items() if key not in {d.doc_id for d in docs}]
        self.pipeline = Pipeline(docs, self.model, output_mode=self.llm.output_mode,
                                 budget=self.budget, max_tokens=self.llm.max_tokens,
                                 select_max_tokens=self.llm.select_max_tokens,
                                 answer_max_tokens=self.llm.answer_max_tokens,
                                 # a server model asked not to think may think anyway (Qwen 3.5 27B
                                 # at the office: "got stuck writing" on every question)
                                 may_think=self.llm.thinking_off)
        self.start_semantic()

    async def model_label(self, cfg: LLMConfig) -> str:
        """The model's name for the chat agent to tell users: a server model by its name, the
        built-in one by its file (the engine only knows it as "local"; llama-server knows the file)."""
        if cfg.model and cfg.model not in ("local", "demo"):
            local = httpx.URL(cfg.base_url).host in ("127.0.0.1", "localhost")
            return f"{cfg.model} ({'on this computer' if local else 'on a model server'})"
        if self.injected_model is not None:
            return ""
        name = await self._builtin_model_file(cfg)
        return f"{name} (the built-in model, running on this computer)" if name else "the built-in model on this computer"

    def _builtin_model_file_now(self, cfg: LLMConfig) -> str:
        """The file the built-in model server serves ("Qwen3.5-0.8B-Q4_K_M"), or "" (blocking)."""
        cached = self._served.get(cfg.base_url)
        # asked again after a minute: a new model file may have been imported (or the server started)
        if cached is None or time.monotonic() - cached[0] > 60:
            cached = self._served[cfg.base_url] = (time.monotonic(), _served_model(cfg))
        return cached[1]

    async def _builtin_model_file(self, cfg: LLMConfig) -> str:
        return await asyncio.to_thread(self._builtin_model_file_now, cfg)

    def chat_is_small_now(self, cfg: LLMConfig) -> bool:
        """Chat mode runs on a small built-in model (under SMALL_MODEL_B billion parameters, by its
        file name): it then answers from the documents only, since its general knowledge is too
        often wrong. A model on a server, or one whose size the name does not tell, is not limited."""
        size = self.builtin_model_b_now(cfg)
        return size is not None and size < SMALL_MODEL_B

    def builtin_model_b_now(self, cfg: LLMConfig) -> float | None:
        """Billions of parameters of the built-in model ("Qwen3.5-4B-Q4_K_M.gguf": 4.0), or None for
        a model on a server or a file name that does not tell."""
        if self.injected_model is not None or (cfg.model and cfg.model not in ("local", "demo")):
            return None
        size = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", self._builtin_model_file_now(cfg))
        return float(size.group(1)) if size else None

    async def chat_is_small(self, cfg: LLMConfig) -> bool:
        return await asyncio.to_thread(self.chat_is_small_now, cfg)

    def embedding_connection(self) -> EmbeddingConnection | None:
        """Where sections are embedded: the office server set in Manage (Office profile), else the
        embedding model on this computer, when one is installed and its server is up."""
        emb = self.config.embeddings
        if emb and emb.base_url and emb.model and self.config.chat_available:
            return emb
        url = self.settings.embed_base_url
        if not url:
            return None
        checked, name = self._local_embedder
        # asked again after a while: the server may still be loading, or a model was just imported
        if time.monotonic() - checked >= LOCAL_EMBEDDER_RECHECK_S:
            name = _served_model(LLMConfig(base_url=url, api_key=self.settings.embed_api_key))
            self._local_embedder = (time.monotonic(), name)
        return EmbeddingConnection(base_url=url, model=name, api_key=self.settings.embed_api_key) if name else None

    @property
    def semantic_is_local(self) -> bool:
        return self._semantic_key is not None and self._semantic_key[0] == self.settings.embed_base_url

    def _update_semantic(self) -> None:
        """A new index when the embedding model appeared, went away or changed, or the documents
        changed. The key holds the pipeline itself: a health poll running while an import swaps
        the documents in cannot leave an index of the old documents behind."""
        pipeline = self.pipeline
        conn = self.embedding_connection() if pipeline else None
        key = (conn.base_url, conn.model, id(pipeline)) if conn and pipeline else None
        if key != self._semantic_key:
            self._semantic_key = key
            self.semantic = (SemanticIndex(pipeline.docs, EmbeddingClient(conn.base_url, conn.model, conn.api_key),
                                           self.settings.data_dir / "embeddings") if key else None)

    def start_semantic(self) -> None:
        """Build the search-by-meaning index in the background, on the server's event loop. Called
        after every reload (an import or a settings change runs in a worker thread), at start-up,
        before answering and when the UI polls the health (so indexing starts once the local
        embedding server has loaded)."""
        self._update_semantic()
        if self.semantic is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # a worker thread: hand the start to the server's loop, if it runs yet
            if self.loop is not None and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.semantic.start)
            return
        self.semantic.start()

    # ── helpers ──

    @property
    def docs(self) -> list[Doc]:
        return self.pipeline.docs if self.pipeline else []

    def require_kb(self) -> Pipeline:
        if self.pipeline is None:
            raise HTTPException(409, self.load_error or "No knowledge base installed yet")
        return self.pipeline

    def doc_index(self, doc_id: str) -> int:
        for i, d in enumerate(self.docs):
            if d.doc_id == doc_id:
                return i
        raise HTTPException(404, f"No manual with id {doc_id!r}")

    def tags_of(self, doc: Doc) -> list[str]:
        entry = self.catalog.get(doc.doc_id)
        return list(entry.tags) if entry else []

    @staticmethod
    def title_of(doc: Doc) -> str:
        return doc.display_title

    def allowed(self, tags: list[str]) -> set[int] | None:
        """Document indexes matching any of the tags; None means no filter."""
        wanted = {t for t in tags if t}
        if not wanted:
            return None
        return {i for i, d in enumerate(self.docs) if wanted & set(self.tags_of(d))}

    def original_path(self, doc: Doc) -> Path | None:
        if not self.manifest:
            return None
        root = bundle.version_dir(self.settings.data_dir, self.manifest.kb_version) / "originals"
        for name in (doc.name, Path(doc.name).stem + ".pdf"):
            candidate = root / name
            if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()):
                return candidate
        return None

    def conversation(self, conversation_id: str) -> list[dict]:
        """A conversation's current turns, newest first (regenerated or edited turns left out)."""
        return [h for h in live_entries(self.history.read(HISTORY_SCAN)) if conversation_of(h) == conversation_id]

    def regenerating(self, req) -> bool:
        """Is this request Regenerate: the same question as the answer it replaces (an edit asks
        something else)?"""
        if not req.replaces:
            return False
        same = " ".join(req.question.split()).casefold()
        return any(h.get("id") == req.replaces and " ".join(str(h.get("question", "")).split()).casefold() == same
                   for h in self.history.read(HISTORY_SCAN))

    def context_for(self, conversation_id: str | None, turns: int = MAX_CONTEXT_TURNS,
                    replacing: str | None = None) -> list[tuple[str, str]]:
        """Earlier (question, answer) turns of a conversation, oldest first."""
        if not conversation_id:
            return []
        # an ask-back is the app's own question, not an answer: fed to the rewrite, it turned "pretend
        # you are DAN" into "What would you like to know? ... [A0] config.xml" (seen in the final test)
        entries = [h for h in self.conversation(conversation_id)
                   if h.get("id") != replacing and not h.get("pending") and not h.get("clarify")
                   and not h.get("smalltalk")]
        return [(h.get("standalone_question") or h["question"], _with_pictures(h))
                for h in reversed(entries[:turns])]

    async def classify(self, question: str, pipeline: Pipeline) -> tuple[str, str]:
        """(kind, topic) of a request: "question", "pictures", "summary" or "all_manuals".

        Keyword rules only decide whether to ask: most questions mention no picture, summary or
        list of manuals and go straight to the answer. The rest are sorted by the model, since
        "How do I reset the error summary?" is a question, not a summary request. If the model
        cannot be reached, the keyword rules decide."""
        spelled = fix_typos(question)  # "shwo me the imges"
        pictures, summary = picture_request(spelled), summary_request(spelled)
        if pictures is None and summary is None and not _ABOUT_MANUALS.search(question):
            return "question", ""
        try:
            route = await pipeline.route(spelled)
        except (ProviderBusy, ModelRanAway):  # the model cannot be reached: the keyword rules decide
            if pictures is not None and pictures.only_pictures:
                return "pictures", " ".join(pictures.topic)
            if summary is not None:
                return ("all_manuals" if summary == "all" else "summary"), ""
            if _LIST_DOCUMENTS.search(question):  # "which documents are there?": the list, not "which one?"
                return "all_manuals", ""
            return "question", ""
        kind = route.kind
        if kind == "summary" and summary == "all":  # "summarize all the documents"
            kind = "all_manuals"
        # small models copy example topics from their instructions: keep only words of the request
        asked = set(tokenize(question))
        return kind, " ".join(t for t in topic_tokens(route.topic) if t in asked)

    def picture_topic(self, topic: str, question: str, context: list[tuple[str, str]]) -> PictureRequest:
        """What pictures to show: the topic the model named, else the question's own words, else
        (for "show me a picture of it") the topic of the question before."""
        words = topic_tokens(topic)
        if not words:
            heuristic = picture_request(fix_typos(question))
            words = heuristic.topic if heuristic else topic_tokens(question)
        # ... not of a request for a summary: "show me the pictures" after "summarize the user manual"
        # said 'No section about "summarize document" has a picture' (flow audit, 20 Oct 2026)
        if not words and context and summary_request(context[-1][0]) is None:
            words = topic_tokens(context[-1][0])
        return PictureRequest(only_pictures=True, topic=words)

    def summary_target(self, req: "AskRequest", allowed: set[int] | None, topic: str = "") -> int | Clarification:
        """Which manual "summarize this doc" means: the only one in scope, the one the request
        names, the one the conversation was about; otherwise ask."""
        scope = [i for i in range(len(self.docs)) if allowed is None or i in allowed]
        if len(scope) == 1:
            return scope[0]
        named = self._manual_named_by(topic_tokens(topic), scope)
        if named is not None:
            return named
        by_id = {d.doc_id: i for i, d in enumerate(self.docs)}
        recent: list[int] = []  # the manuals the last answer of this conversation used
        for entry in self.conversation(req.conversation_id) if req.conversation_id else []:
            if entry.get("id") != req.replaces:
                cited = (by_id.get(c.get("doc_id")) for c in entry.get("citations") or [])
                recent = list(dict.fromkeys(i for i in cited if i in scope))
                break
        if len(recent) == 1:
            return recent[0]
        named = self._manual_named_by([t for t in tokenize(req.question) if t not in _SUMMARY_FILLER], scope)
        if named is not None:
            return named
        if recent:  # the last answer used several manuals: ask which of those
            scope = recent
        return Clarification(prompt="Which document should I summarize?", options=[
            ClarifyOption(label=self.docs[i].display_title, detail=self.docs[i].description or "",
                          doc_ids=[self.docs[i].doc_id]) for i in scope[:MAX_CLARIFY_OPTIONS]])

    def _manual_named_by(self, words: list[str], scope: list[int]) -> int | None:
        """The manual these words point at clearly (by title, description or content), if any."""
        if not words or not self.pipeline:
            return None
        titles = {i: set(tokenize(f"{self.docs[i].display_title} {self.docs[i].description}")) for i in scope}
        by_title = [i for i in scope if set(words) <= titles[i]]
        if len(by_title) == 1:
            return by_title[0]
        best: dict[int, float] = {}
        for h in self.pipeline.index.search(" ".join(words)):
            if h.doc_index in scope:
                best.setdefault(h.doc_index, h.score)
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        if ranked and (len(ranked) == 1 or ranked[0][1] >= 1.5 * ranked[1][1]):
            return ranked[0][0]
        return None

    def _doc_named_in(self, topic: list[str], allowed: set[int] | None) -> tuple[int, list[str]] | None:
        """(document, the rest of the topic) when the topic names one document by its title
        ("meeting notes" for meeting_notes.docx), else None."""
        scope = [i for i in range(len(self.docs)) if allowed is None or i in allowed]
        if len(scope) < 2 or not topic:
            return None
        titles = {i: set(tokenize(re.sub(r"\.[A-Za-z0-9]{2,5}$", "", self.docs[i].display_title).replace("_", " ")))
                  for i in scope}
        overlap = {i: len(set(topic) & t) for i, t in titles.items()}
        best = max(overlap.values())
        named = [i for i, n in overlap.items() if n == best]
        if best == 0 or len(named) > 1:
            return None
        return named[0], [w for w in topic if w not in titles[named[0]]]

    def pictures_of(self, di: int) -> list[PictureRef]:
        """Every picture of a document, in document order: the images a web-platform export
        extracted, or the pages of the original PDF that hold one."""
        doc = self.docs[di]
        if doc.assets_dir is not None:
            return [(di, s.node_id, s.start_page, name) for s in doc.sections.values() for name in s.images]
        pdf = self.original_path(doc) if doc.kind == "pages" else None
        try:
            pages = sorted(figure_pages(pdf)) if pdf else []
        except Exception:  # an unreadable PDF must not break the answer
            pages = []
        return [(di, self.section_at(doc, p), p, None) for p in pages]

    def can_show_pictures(self, allowed: set[int] | None) -> bool:
        return any(d.assets_dir is not None or (d.kind == "pages" and self.original_path(d))
                   for i, d in enumerate(self.docs) if allowed is None or i in allowed)

    def picture_refs(self, topic: list[str], allowed: set[int] | None) -> list[PictureRef]:
        """Pictures in the sections about the topic, best match first; all of them without a topic."""
        scope = [i for i in range(len(self.docs)) if allowed is None or i in allowed]
        every = {i: self.pictures_of(i) for i in scope}
        if not topic:
            return [ref for i in scope for ref in every[i]]
        if self.pipeline:  # "org chart": the documents say "organization"
            topic = self.pipeline.index.expand(topic)
        hits = [h for h in (self.pipeline.index.search(" ".join(topic))[:20] if self.pipeline else [])
                if every.get(h.doc_index)]
        # a section whose summary or text names the topic next to a picture word ("with an
        # SNR-vs-illuminance figure") holds that picture: before one that only mentions the topic
        hits.sort(key=lambda h: not self._shows(h.doc_index, h.node_id, topic))
        matched = {(h.doc_index, h.node_id) for h in hits}
        found: list[PictureRef] = []
        for h in hits:
            doc = self.docs[h.doc_index]
            within = {sub.node_id for sub in doc.subtree(h.node_id)}
            for ref in every[h.doc_index]:
                # a picture counts when the section it is in matches the topic, or when this is the
                # best match and it is a whole chapter ("Tool head" with an untitled figure page)
                if ref[1] in within and ref not in found and (
                        (h.doc_index, ref[1]) in matched or (h is hits[0] and doc.sections[h.node_id].children)):
                    found.append(ref)
        return found

    def _shows(self, di: int, node_id: str, topic: list[str]) -> bool:
        """Does the section describe a picture of the topic, as captions put it: "SNR chart", "an
        SNR-vs-illuminance figure" (the topic up to three words before a picture word) or "chart of
        SNR" (up to two words after)?"""
        s = self.docs[di].sections[node_id]
        # the bare "[Figure]" left where a picture was is no caption: it would match any word before it
        words = tokenize(f"{s.summary} {s.own_text.replace('[Figure]', ' ')}")
        near = [j for j, w in enumerate(words) if w in topic]
        return any(is_visual(w) and any(0 < i - j <= 3 or 0 < j - i <= 2 for j in near) for i, w in enumerate(words))

    @staticmethod
    def section_at(doc: Doc, page: int) -> str:
        """The node id of the deepest section covering this page."""
        covering = [s for s in doc.sections.values()
                    if (s.start_page or 0) <= page <= (s.end_page or s.start_page or 0)]
        return max(covering, key=lambda s: s.depth).node_id if covering else next(iter(doc.sections))

    def cite_pictures(self, result: AskResult, refs: list[PictureRef], limit: int) -> None:
        """Add these pictures to the result as figures, citing the section each one is in."""
        cited = {(c.doc_id, c.node_id): i for i, c in enumerate(result.citations)}
        shown = {(result.citations[f.citation].doc_id, f.page, f.image) for f in result.figures}
        for di, node, page, image in refs:
            if len(result.figures) >= limit:
                return
            doc = self.docs[di]
            key = (doc.doc_id, page, image)
            if key in shown:
                continue
            if (doc.doc_id, node) not in cited:
                cited[(doc.doc_id, node)] = len(result.citations)
                result.citations.append(self.pipeline.citation(di, node))
            notes = doc.sections[node].image_notes.get(image, {}) if image and node in doc.sections else {}
            result.figures.append(Figure(citation=cited[(doc.doc_id, node)], page=page, image=image,
                                         caption=notes.get("caption"), description=notes.get("description")))
            shown.add(key)

    def picture_answer(self, question: str, request: PictureRequest, allowed: set[int] | None,
                       mode: str) -> AskResult:
        """The answer to "show me the diagrams (of X)": the pictures, found without the model."""
        t0 = time.perf_counter()
        if not self.can_show_pictures(allowed):
            return AskResult(question=question, found=False, mode=mode, seconds={"total": 0.0},
                             answer="The pictures in these documents can't be shown: the knowledge base has "
                                    "neither the pictures nor the original PDF files. Ask the knowledge-base team "
                                    "to include them.")
        topic, where = request.topic, "the documents"
        named = self._doc_named_in(topic, allowed)
        if named is not None:  # "any pictures in the meeting notes?": only that document
            di, topic = named
            where, allowed = self.docs[di].display_title, {di}
            if not self.pictures_of(di):
                return AskResult(question=question, found=False, mode=mode, seconds={"total": 0.0},
                                 answer=f"{where} has no pictures.")
        about = f' about "{_as_asked(question, topic)}"' if topic else ""
        refs = self.picture_refs(topic, allowed)
        answer = f"Pictures in {where}{about}. Select one to see it larger."
        if topic and not refs:
            refs = self.picture_refs([], allowed)
            answer = f"No section{about} has a picture. These are the pictures in {where}."
        if not refs:
            return AskResult(question=question, found=False, mode=mode, seconds={"total": 0.0},
                             answer="The documents hold no pictures.")
        result = AskResult(question=question, found=True, answer=answer, mode=mode, pictures_only=True)
        self.cite_pictures(result, refs, MAX_LISTED)
        result.seconds = {"total": round(time.perf_counter() - t0, 2)}
        return result

    def add_figures(self, result: AskResult, seen: set[tuple] = frozenset()) -> None:
        """Show the pictures of the cited sections (or cited PDF pages) with the answer, except
        those already shown earlier in the conversation (seen: (doc id, page, image))."""
        if not result.found:
            return
        by_id = {d.doc_id: i for i, d in enumerate(self.docs)}
        cited_nodes = {(c.doc_id, c.node_id) for c in result.citations}
        for ci, c in enumerate(result.citations):
            di = by_id.get(c.doc_id)
            if di is None:
                continue
            doc = self.docs[di]
            if doc.assets_dir is not None:
                within = {sub.node_id for sub in doc.subtree(c.node_id)} if c.node_id in doc.sections else set()
                refs = self._relevant([r for r in self.pictures_of(di) if r[1] in within], doc, c.node_id,
                                      result.question, cited_nodes)
            else:
                pdf = self.original_path(doc) if c.pages else None
                try:
                    pages = figure_pages(pdf) if pdf else frozenset()
                except Exception:  # an unreadable PDF must not break the answer
                    pages = frozenset()
                refs = [(di, c.node_id, p, None) for p in (c.pages or [])[:5] if p in pages]
            refs = [r for r in refs if (doc.doc_id, r[2], r[3]) not in seen]
            before = len(result.figures)
            self.cite_pictures(result, refs, MAX_FIGURES)
            for f in result.figures[before:]:
                f.citation = ci  # figures of a cited section stay with that citation
            if len(result.figures) >= MAX_FIGURES:
                return

    @staticmethod
    def _relevant(refs: list[PictureRef], doc: Doc, node_id: str, question: str,
                  cited: set[tuple[str, str]]) -> list[PictureRef]:
        """The pictures of a cited chapter worth showing: those in the cited section itself or in a
        subsection the answer also cites, then those of the subsections that share the most words
        with the question. A chapter's other pictures are left out: citing "Features" for the
        create-project steps must not show the dashboard and the burndown chart."""
        words = set(tokenize(question))
        sure: list[PictureRef] = []
        overlap: dict[PictureRef, int] = {}
        for ref in refs:
            if ref[1] == node_id or (doc.doc_id, ref[1]) in cited:
                sure.append(ref)
                continue
            sec = doc.sections[ref[1]]
            notes = sec.image_notes.get(ref[3] or "", {})
            text = f"{sec.title} {sec.own_text} {notes.get('caption', '')} {notes.get('description', '')}"
            overlap[ref] = len(words & set(tokenize(text)))
        best = max(overlap.values(), default=0)
        return sure + [ref for ref, n in overlap.items() if best and n == best]

    def last_cited(self, conversation_id: str | None, replacing: str | None = None) -> list[tuple[str, str]]:
        """(doc id, node id) of the sections the conversation's last answer cited."""
        entries = [h for h in self.conversation(conversation_id) if h.get("id") != replacing and not h.get("pending")] if conversation_id else []
        return [(c["doc_id"], c["node_id"]) for c in (entries[0].get("citations") or []) if c.get("node_id")] \
            if entries else []

    def earlier_sections(self, conversation_id: str | None, replacing: str | None = None,
                         turns: int = 2) -> list[tuple[int, str]]:
        """(doc index, node id) of the sections the conversation's last answers cited, newest first:
        a follow-up starts from them, with the same references as before."""
        if not conversation_id:
            return []
        by_doc = {d.doc_id: i for i, d in enumerate(self.docs)}
        entries = [h for h in self.conversation(conversation_id) if h.get("id") != replacing and not h.get("pending")]
        out: list[tuple[int, str]] = []
        for h in entries[:turns]:
            for c in h.get("citations") or []:
                di = by_doc.get(c.get("doc_id"))
                if di is not None and c.get("node_id") in self.docs[di].sections and (di, c["node_id"]) not in out:
                    out.append((di, c["node_id"]))
        return out[:6]

    def section_by_id(self, doc_id: str | None, node_id: str | None) -> tuple[int, str] | None:
        """(doc index, node id) of a section of the installed documents, if it still exists."""
        di = next((i for i, d in enumerate(self.docs) if d.doc_id == doc_id), None)
        return (di, node_id) if di is not None and node_id in self.docs[di].sections else None

    def place_for(self, req) -> tuple[int, str] | None:
        """Where a conversation is (see focus.py): the place the app shows, else where its last
        answer left it; None once the user closed the "Working in" line."""
        if req.focus_off:
            return None
        if req.focus is not None:
            return self.section_by_id(req.focus.doc_id, req.focus.node_id)
        for h in self.conversation(req.conversation_id) if req.conversation_id else []:
            if h.get("id") != req.replaces and not h.get("pending") and h.get("focus"):
                return self.section_by_id(h["focus"].get("doc_id"), h["focus"].get("node_id"))
        return None

    def cited_sections(self, result: AskResult) -> list[tuple[int, str]]:
        """(doc index, node id) of the sections an answer cites, as the documents are now."""
        return [at for c in result.citations if (at := self.section_by_id(c.doc_id, c.node_id))]

    def note_mixed_parts(self, result: AskResult) -> None:
        """An answer whose steps come from different parts of a manual says so, and that asking
        about one part gives its exact steps. Not for questions that name those parts or compare."""
        if not result.found or result.smalltalk or result.summary or _COMPARES.search(result.question):
            return
        parts = mixed_parts(self.docs, self.cited_sections(result))
        asked = f" {' '.join(tokenize(result.question))} "
        if parts and not any(f" {' '.join(tokenize(p))} " in asked for p in parts):
            result.answer = (result.answer.rstrip() + f"\n\n*These steps come from different parts of the manual "
                             f"({', '.join(parts)}). Ask about one of them for its exact steps.*")

    def place_after(self, result: AskResult, place: tuple[int, str] | None) -> None:
        """Where the conversation is after an answer: the section it came from, else where it was."""
        if result.found and result.citations and not result.pictures_only:
            first = result.citations[0]
            # steps from several parts of the manual: the conversation is in none of them
            if not mixed_parts(self.docs, self.cited_sections(result)):
                place = self.section_by_id(first.doc_id, first.node_id) or place
        elif result.clarify and result.clarify.options:
            # "Type 1 or Type 2?": the conversation is at the section they are ways of
            picked = [self.section_by_id((o.doc_ids or [None])[0], o.section) for o in result.clarify.options]
            parents = {(p[0], self.docs[p[0]].sections[p[1]].parent) for p in picked if p is not None}
            if len(parents) == 1 and None not in picked and next(iter(parents))[1] is not None:
                place = next(iter(parents))
        result.focus = focus_of(self.docs[place[0]], place[1]) if place is not None else None

    def chat_tier(self) -> str:
        """"local": the built-in model on this computer (fewer, larger steps); else "remote"."""
        return "local" if self.injected_model is None and self.chat_llm.model in ("local", "demo") else "remote"

    def figures_shown(self, conversation_id: str | None, replacing: str | None = None) -> set[tuple]:
        """(doc id, page, image) of the pictures shown in a conversation's earlier answers."""
        shown = set()
        for h in self.conversation(conversation_id) if conversation_id else []:
            if h.get("id") == replacing:
                continue
            cites = h.get("citations") or []
            for f in h.get("figures") or []:
                if 0 <= f.get("citation", -1) < len(cites):
                    shown.add((cites[f["citation"]].get("doc_id"), f.get("page"), f.get("image")))
        return shown

    def hold_place(self, req) -> str:
        """Save a question before its answer, so that History keeps it when the answer never comes
        (stopped, or the app closed: seen with a large model on an 8 GB laptop). The answer replaces
        it; returns its id."""
        return self.history.append({
            "conversation_id": req.conversation_id, "mode": req.mode, "replaces": req.replaces,
            "question": req.question, "tags": req.tags, "found": False, "answer": UNANSWERED,
            "pending": True, "kb_version": self.manifest.kb_version if self.manifest else None,
        })["id"]

    def record(self, result: AskResult, tags: list[str], conversation_id: str | None = None,
               replaces: str | None = None) -> AskResult:
        if result.smalltalk:
            # an instant reply ("Hi", "who are you", a declined jailbreak) is saved too: a conversation
            # of only those was missing from Recent and History (final computer-use check). It is
            # left out of the context follow-ups are read in (see context_for)
            entry = self.history.append({
                "conversation_id": conversation_id, "mode": result.mode, "replaces": replaces,
                "question": result.question, "tags": tags, "found": False, "answer": result.answer,
                "smalltalk": True, "seconds": result.seconds.get("total"),
                "kb_version": self.manifest.kb_version if self.manifest else None,
            })
            result.history_id = entry["id"]
            return result
        # a follow-up citing the same table again does not repeat its chart under every answer
        self.add_figures(result, self.figures_shown(conversation_id, replaces))
        entry = self.history.append({
            "conversation_id": conversation_id, "standalone_question": result.standalone_question,
            "mode": result.mode, "replaces": replaces,
            "question": result.question, "tags": tags, "found": result.found, "answer": result.answer,
            "citations": [c.model_dump() for c in result.citations],
            "evidence": [e.model_dump() for e in result.evidence],
            # a "not in the documents" answer lists what was checked, also when reopened
            "considered": [c.model_dump() for c in result.considered] if not result.found else [],
            "clarify": result.clarify.model_dump() if result.clarify else None,
            "figures": [f.model_dump() for f in result.figures],
            "summary": result.summary,
            # Chat mode: so a reopened conversation shows the same banner and file note
            "general": result.general, "files": result.files,
            "worked_out": result.worked_out,
            "focus": result.focus.model_dump() if result.focus else None,
            "unverified_numbers": result.unverified_numbers, "seconds": result.seconds.get("total"),
            "kb_version": self.manifest.kb_version if self.manifest else None,
            "model": (self.chat_llm if result.mode == "chat" else self.llm).model,
        })
        result.history_id = entry["id"]
        return result


# "which documents are there?", "list the manuals", "what files do you have?": the list of documents
_LIST_DOCUMENTS = re.compile(
    r"^\W*(?:which|what)\s+(?:documents|manuals|docs|files)\s+(?:are\s+there|do\s+you\s+have|are\s+available|"
    r"can\s+you\s+(?:see|read|use)|are\s+in\b)|^\W*list\s+(?:all\s+)?(?:the\s+|your\s+)?(?:documents|manuals|docs|files)\W*$",
    re.IGNORECASE)
# an answer that is one step of a walk-through ("**Step 2 of 5: Training**")
_WALK_STEP = re.compile(r"^\*\*Step \d+ of \d+:")
# "which manuals do you have?", "list the documents": may ask about all manuals
_ABOUT_MANUALS = re.compile(r"\b(manuals|documents|docs)\b|\bwhich (manual|document|doc)\b", re.IGNORECASE)
# "plot a chart of ...", "draw a graph", "make a bar chart": a new chart, which nothing here can draw
# a question about several things at once: its answer may draw on several parts of a manual
_COMPARES = re.compile(r"\b(compare|comparison|differen\w*|versus|vs\.?|both|between|each|every|all)\b", re.IGNORECASE)
_DRAW = re.compile(r"\b(plot|draw|graph|visuali[sz]e)\b|\b(make|create|generate|build|give me)\b.{0,25}"
                   r"\b(chart|graph|histogram|plot)\b", re.IGNORECASE)
# "what is 17 * 23?", "1,560 / 13", "calculate (412 + 389) * 1.1"
_ARITHMETIC = re.compile(r"^\s*(?:what(?:'s| is)|whats|calculate|compute|how much is)?\s*"
                         r"([\d\s.,+\-*/x×÷^()%]*\d[\d\s.,+\-*/x×÷^()%]*?)\s*[=?.!]*\s*$", re.IGNORECASE)


def arithmetic_answer(question: str) -> str | None:
    """A question that is only a calculation, calculated exactly (no model: a small one got it wrong)."""
    m = _ARITHMETIC.match(question)
    if not m or not re.search(r"\d\s*[+\-*/x×÷^%]\s*\(?\s*\d", m.group(1)):
        return None
    shown = " ".join(m.group(1).split())
    expression = re.sub(r"(?<=\d)\s*[x×]\s*(?=[\d(])", "*", shown).replace("÷", "/").replace("^", "**")
    try:
        return f"{shown} = **{calculate(expression)}**"
    except CalcError:
        return None


# "write an essay / a report / an email about ...": Chat mode writes it (with its tools), even when it
# is about all the documents or one of them
_COMPOSE = re.compile(r"\b(write|essay|draft|compose|compare|contrast|explain|analy[sz]e|report on|article|"
                      r"presentation|slides?|blog|letter|memo|e-?mail)\b", re.IGNORECASE)
# "summarize our conversation": about the chat itself, not a document
_ABOUT_CHAT = re.compile(r"\b(conversation|chat|what we (discussed|talked about|said)|so far)\b", re.IGNORECASE)
# "summarize the file I attached", "what's in my upload": about an attachment, not a document
_ABOUT_ATTACHMENT = re.compile(r"\b(attach\w*|upload\w*|this (file|pdf|document|doc|sheet|spreadsheet)|"
                               r"my (file|pdf|document|doc|sheet|spreadsheet))\b", re.IGNORECASE)
ATTACHMENT_WORD_MIN = 4  # a question word this long, in an attached file, ties the question to it


def _attachments_named(question: str, attached: list, text_of) -> list[str]:
    """The attachments a later question is about without saying "the file": "Which company replaced
    the belt?" after a note about the belt (the 0.8B did not read it and named the leave policy's
    company; final check). A word of the question, not a common one, in the file's text."""
    words = {t for t in tokenize(question) if len(t) >= ATTACHMENT_WORD_MIN and t not in _COMMON_WORDS}
    out = []
    for att in attached:
        text = text_of(att.id) or ""
        if words & set(tokenize(text[:200_000])):
            out.append(att.id)
    return out


_COMMON_WORDS = frozenset(
    "what which where when does have from with that this they them their there about would could should "
    "into also more most many much some your mine ours were been being will shall must than then".split())


# words of a summary request that say nothing about which manual is meant
_SUMMARY_FILLER = frozenset("summary summarize summarise overview outline gist give me please this that doc document "
                            "manual pdf file short brief quick can you key point topic about tell".split())

def _built() -> str:
    """When this engine was built: the packaged executable's time (the installed app), else the
    newest source file's. Shown in Manage, so testers can tell builds apart."""
    target = Path(sys.executable) if getattr(sys, "frozen", False) else \
        max(Path(__file__).parent.glob("*.py"), key=lambda p: p.stat().st_mtime)
    return datetime.fromtimestamp(target.stat().st_mtime).strftime("%d %b %Y %H:%M")


ENGINE_BUILT = _built()
UNEXPECTED_ERROR = ("Something went wrong while answering. Try again; if it keeps happening, "
                    "send backend.log from the app's data folder to IT.")
UNANSWERED = "No answer: the question was stopped, or the app closed before it was answered."
RAN_AWAY = ("The assistant got stuck writing this answer and was stopped. "
            "Try again, or ask in different words.")
HISTORY_SCAN = 2000  # history entries searched for one conversation's turns


def live_entries(entries: list[dict]) -> list[dict]:
    """History without entries that a later regenerate, edit or answer replaced (a question's
    placeholder, see Engine.hold_place)."""
    replaced = {e.get("replaces") for e in entries if e.get("replaces")}
    return [e for e in entries if e.get("id") not in replaced]


def probe_endpoint(cfg: LLMConfig, timeout: float = 5.0) -> tuple[bool, str, list[str]]:
    """Can the OpenAI-compatible endpoint be reached with this key? Returns (ok, detail, model ids)."""
    if not cfg.base_url:
        return False, "No endpoint URL set", []
    if not re.match(r"https?://[^/\s]+", cfg.base_url.strip(), re.IGNORECASE):
        return False, "The address must start with http:// or https://, e.g. http://server:8000/v1", []
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    try:
        r = httpx.get(cfg.base_url.rstrip("/") + "/models", headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        return False, f"{cfg.base_url} did not answer within {timeout:.0f} seconds", []
    except httpx.ConnectError:
        return False, f"Cannot connect to {cfg.base_url}: check the address and that the server is running", []
    except httpx.HTTPError as e:
        return False, f"Cannot reach {cfg.base_url} ({type(e).__name__})", []
    if r.status_code in (401, 403):
        return False, "The endpoint refused the API key", []
    if r.status_code != 200:
        return False, f"The endpoint answered HTTP {r.status_code}", []
    try:
        ids = [m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m]
    except (ValueError, AttributeError):
        ids = []
    if cfg.model and ids and cfg.model not in ids:
        return False, f"Model {cfg.model!r} is not served by this endpoint", ids
    return True, "ready", ids


async def _done(result: AskResult) -> AskResult:
    return result


async def _as_smalltalk(pending) -> AskResult:
    """A greeting in Chat mode: the model's reply, shown without the "general answer" warning."""
    result = await pending
    if not result.citations:
        result.smalltalk = True
    return result


def conversation_of(entry: dict) -> str:
    """Entries saved before conversations existed are each their own conversation."""
    return entry.get("conversation_id") or entry.get("id", "")


def _served_model(cfg: LLMConfig) -> str:
    """The model file a llama-server serves ("Qwen3.5-4B-Q4_K_M"), or "" when it cannot be asked."""
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    try:
        r = httpx.get(cfg.base_url.rstrip("/") + "/models", headers=headers, timeout=2.0)
        name = r.json()["data"][0]["id"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return ""
    return re.sub(r"\.gguf$", "", re.split(r"[\\/]", str(name))[-1], flags=re.IGNORECASE)


def _as_asked(question: str, topic: list[str]) -> str:
    """The topic in the question's own words ("lens", not the search stem "len")."""
    words = [w.rstrip("./-") for w in re.findall(r"[\w./-]+", question) if any(t in topic for t in tokenize(w))]
    return " ".join(dict.fromkeys(words)) or " ".join(topic)


def _doc_noun(doc: Doc) -> str:
    """How a suggested question names a document: 'the manual', or 'the meeting notes' for
    meeting_notes.docx (web-platform exports are titled by their file names)."""
    title = doc.title or doc.name  # the file name, for a web-platform export
    root = doc.sections[doc._title_root].title if doc._title_root else ""
    if re.search(r"\bmanual\b", f"{title} {root}", re.I):
        return "the manual"
    stem = re.fullmatch(r"(.+)\.[A-Za-z0-9]{2,5}", title)
    if stem:
        # a file name: the title the document gives itself reads better ("IT Setup Guide for
        # New Employees", not "it setup guide")
        own = _own_title(doc)
        if own:
            return f"the {own}"
        # "notes_v3_2" -> "notes v3.2", but "plant_performance_h1_2026" stays "h1 2026" (not "h1.2026")
        # without a document code or copy mark ("[B-ACD-E1]SEM_AI_Frontier7.0_UserManual (1)")
        readable = doc.display_title
        name = readable if readable != title else stem.group(1)
        name = re.sub(r"(?<![A-Za-z0-9])(v\d+)_(\d{1,2})(?![0-9])", r"\1.\2", name, flags=re.IGNORECASE)
        # "HX-500_manual" -> "HX-500 manual": a hyphen inside a code stays
        name = re.sub(r"_+|(?<![A-Za-z0-9])-+|-+(?![A-Za-z0-9])", " ", name)
        name = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", name)  # "meeting-notes" -> "meeting notes"
        name = " ".join(name.split())
        # acronyms as the document writes them: "the IT security standard", not "the it security
        # standard" (it_security_standard.docx, titled "Helix Precision - IT Security Standard")
        words = name.split()
        seen = re.search(r"\b" + r"[\s_-]+".join(map(re.escape, words)) + r"\b",
                         " | ".join(s.title for s in list(doc.sections.values())[:4] if s.title != title),
                         re.IGNORECASE)
        if seen and not seen.group(0).isupper():  # "LEAVE POLICY" is shouting, not acronyms
            name = " ".join(w if w.isupper() and len(w) > 1 else w.lower()
                            for w in re.split(r"[\s_]+|(?<=[a-z])-(?=[a-z])", seen.group(0), flags=re.IGNORECASE))
        return "the " + name
    return f"the {title}" if len(title) <= 40 else "the documents"


# documents named in the plural: "the meeting notes say", but "the guide for new employees says"
def _with_pictures(entry: dict) -> str:
    """An earlier answer as the next question sees it, with the pictures it showed named: "what is this
    project dialog?" after "are there any images?" refers to one of them (the answer text alone only
    says "Pictures in the documents. Select one to see it larger.")."""
    answer = entry.get("answer") or ""
    cites = entry.get("citations") or []
    shown = []
    for f in entry.get("figures") or []:
        c = cites[f["citation"]] if 0 <= f.get("citation", -1) < len(cites) else None
        if c is None:
            continue
        what = f.get("caption") or (f"page {f['page']}" if f.get("page") else "a picture")
        shown.append(f"{what} ({c.get('doc_name')} > {c.get('title')})"
                     + (f": {f['description']}" if f.get("description") else ""))
    return f"{answer}\nPictures shown: " + "; ".join(shown) if shown else answer


# a code with a hyphen and a digit, searched as a whole: "WEB-142", "E-217", "HR-POL-007"
_CODE = re.compile(r"\b[A-Za-z]+(?:-[A-Za-z0-9]+)*-\d[A-Za-z0-9]*\b")

_PLURAL_DOC = re.compile(r"\b(notes|minutes|instructions|guidelines|procedures|specifications|slides|"
                         r"records|results|terms|requirements)$", re.IGNORECASE)
_WH_TITLE = re.compile(r"(what|how|why|when|where|who|which)\b", re.IGNORECASE)


# "Creating a Project" -> "How do I create a project?": the verbs of task headings in manuals
_TASK_VERBS = {g: v for v, g in (v.split(":") for v in (
    "add:adding apply:applying approve:approving assign:assigning calibrate:calibrating cancel:cancelling "
    "change:changing check:checking clean:cleaning configure:configuring connect:connecting create:creating "
    "delete:deleting disable:disabling download:downloading edit:editing enable:enabling export:exporting "
    "import:importing install:installing invite:inviting manage:managing remove:removing replace:replacing "
    "report:reporting request:requesting reset:resetting run:running schedule:scheduling "
    "share:sharing start:starting stop:stopping submit:submitting troubleshoot:troubleshooting "
    "uninstall:uninstalling update:updating upgrade:upgrading upload:uploading use:using").split())}
_TASK_VERBS.update({"canceling": "cancel", "setting up": "set up"})


def _is_task(title: str) -> bool:
    """A heading that names a task ("Creating a Project", "Setting up VPN")."""
    first, _, rest = title.partition(" ")
    return bool(rest) and (first.casefold() in _TASK_VERBS
                           or (first.casefold() == "setting" and rest.casefold().startswith("up ")))


def _example_question(title: str, noun: str) -> str:
    """"How do I create a project?" for a task heading, else "What does the manual say about X?"."""
    first, _, rest = title.partition(" ")
    key = first.casefold()
    if key == "setting" and rest.casefold().startswith("up "):
        key, rest = "setting up", rest[3:]
    verb = _TASK_VERBS.get(key)
    if verb and rest:
        # "a Project" -> "a project", "Team Members" -> "team members"; "VPN", "TaskFlow" stay
        words = (w.lower() if w[:1].isupper() and w[1:].islower() else w for w in rest.split())
        return f"How do I {verb} {' '.join(words)}?"
    return f"What {'do' if _PLURAL_DOC.search(noun) else 'does'} {noun} say about {_mid_sentence(title)}?"


# titles that make no example question: "What does the manual say about about?"
_GENERIC_TITLES = frozenset("about overview introduction summary contents general".split()) | {"table of contents"}


def _names_of(doc: Doc, noun: str) -> set[str]:
    """What a document is called: its title, file name without extension, and noun ('leave policy')."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", doc.display_title)
    return {n.casefold() for n in (doc.display_title, stem, stem.replace("_", " "), noun.removeprefix("the ")) if n}


def _unsuggestable(title: str, names: set[str]) -> bool:
    """Sections no one would ask about: a picture's file name (an image description), markup such as
    '<option name=... />' (a config file read as sections), the document's own name, or a heading like
    'About'."""
    # "1. About this manual": its number and "about this ..." left out ("say about about this manual?")
    low = re.sub(r"^[\d.\s]+", "", title.casefold()).strip()
    words = set(re.findall(r"\w+", low))
    return bool(IMAGE_NAME.fullmatch(title) or re.search(r"[<>={}]", title)
                or low in names or low in _GENERIC_TITLES
                # the document's title block ("Helix Precision - IT Security Standard (ITS-03, version 2.1)",
                # "HX-900 Horizontal Machining Center - Service Manual" for hx-900_service_manual.pdf)
                or any(len(n.split()) >= 2 and set(re.findall(r"\w+", n)) <= words for n in names)
                # ... or a sheet named like its file: "say about spindle log?" for the HX-900 spindle log
                or any(len(words) >= 2 and words <= set(re.findall(r"\w+", n)) for n in names)
                or re.match(r"about (this|the) (manual|document|guide|handbook|policy|report)\b", low))


def _own_title(doc: Doc) -> str | None:
    """The bold line a converted document starts with ("**Acme Corp Employee Handbook 2026**"),
    when it is short enough for a question."""
    preface = next((s for s in doc.sections.values() if s.title == "Preface"), None)
    first = next((line.strip() for line in (preface.own_text if preface else "").splitlines() if line.strip()), "")
    m = re.fullmatch(r"\*\*([^*]{3,45})\*\*", first)
    return m.group(1).strip() if m else None


def _mid_sentence(title: str) -> str:
    """'Curing' -> 'curing', 'SAFETY RULES' -> 'safety rules'; codes stay as they are ('E17 - ...', 'PPE')."""
    if title.isupper() and " " in title:
        return title.lower()
    later = [w for w in title.split()[1:] if w[:1].isalpha()]
    title_case = any(w[0].isupper() and w[1:].islower() for w in later)  # "Capital Expenditures"
    if len(title) > 1 and title[0].isupper() and title[1].islower() and not title_case:
        return title[0].lower() + title[1:]
    return title


def create_app(settings: Settings | None = None, model: Model | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = Engine(settings, model)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        engine.loop = asyncio.get_running_loop()
        warm_up = asyncio.create_task(engine.warm_up_when_ready())
        engine.start_semantic()
        yield
        warm_up.cancel()

    app = FastAPI(title="Lattice engine", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    def auth(request: Request) -> None:
        if settings.api_token is None:
            return
        header = request.headers.get("authorization", "")
        if not secrets.compare_digest(header, f"Bearer {settings.api_token}"):
            raise HTTPException(401, "Missing or wrong API token")

    guarded = [Depends(auth)]

    # ── status and knowledge base ──

    @app.get("/api/health", dependencies=guarded)
    def health():
        llm_ready, llm_detail = engine.model_status()
        engine.start_semantic()  # the local embedding server may have finished loading since
        return {
            "profile": engine.config.profile,
            "chat_available": engine.config.chat_available,
            "default_mode": engine.config.default_mode if engine.config.chat_available else "manuals",
            "provider": engine.config.connection.kind,
            "chat_model": engine.chat_llm.model if engine.config.chat_available else None,
            # Chat on a small built-in model answers from the documents only (no general knowledge)
            "chat_documents_only": engine.config.chat_available and engine.chat_is_small_now(engine.chat_llm),
            # billions of parameters of the built-in model, by its file name: how long answers take
            "builtin_model_b": engine.builtin_model_b_now(engine.llm),
            "status": "ok" if engine.pipeline else "no_knowledge_base",
            "engine_version": __version__,
            "engine_built": ENGINE_BUILT,
            "kb_version": engine.manifest.kb_version if engine.manifest else None,
            "kb_error": engine.load_error,
            "model": engine.llm.model,
            "llm_ready": llm_ready,
            "llm_detail": llm_detail,
            # search by meaning: None (not set up), "indexing", "ready", "error: ..." (not built) or
            # "unavailable: ..." (built, but the embedding server stopped answering)
            "semantic": engine.semantic.status if engine.semantic else None,
            # while indexing: [sections done, all sections]
            "semantic_progress": list(engine.semantic.progress) if engine.semantic and engine.semantic.status == "indexing" else None,
            # the embedding model, and whether it runs on this computer (else on an office server)
            "semantic_model": engine.semantic.client.model if engine.semantic else None,
            "semantic_local": engine.semantic is not None and engine.semantic_is_local,
            "requests_left_today": (settings.daily_budget - engine.budget.used_today()) if engine.budget else None,
        }

    @app.get("/api/kb", dependencies=guarded)
    def kb():
        if not engine.pipeline or not engine.manifest:
            raise HTTPException(404, engine.load_error or "No knowledge base installed yet")
        return {
            "kb_version": engine.manifest.kb_version,
            "source_format": engine.manifest.source_format,
            "created_at": engine.manifest.created_at,
            "catalog_unmatched": engine.catalog_unmatched,
            "skipped": engine.manifest.skipped,
            "documents": [{"doc_id": d.doc_id, "name": d.name, "title": d.display_title,
                           "description": d.description,
                           "kind": d.kind, "sections": len(d.sections),
                           "fallback_summaries": sum(s.summary_is_fallback for s in d.sections.values())}
                          for d in engine.pipeline.docs],
        }

    @app.get("/api/kb/versions", dependencies=guarded)
    def versions():
        return {"active": bundle.active_version(settings.data_dir),
                "installed": bundle.list_versions(settings.data_dir)}

    def refuse_while_answering() -> None:
        """A new knowledge base mid-answer: the answer would be saved against the new documents, its
        sources pointing at sections that may be gone."""
        if engine.answering:
            raise HTTPException(409, "An answer is being written. Import or switch the knowledge base "
                                     "when it has finished (or stop it).")

    @app.post("/api/kb/import", dependencies=guarded)
    def import_kb(req: ImportRequest):
        refuse_while_answering()
        try:
            manifest = bundle.import_bundle(Path(req.path), settings.data_dir)
        except bundle.BundleError as e:
            raise HTTPException(422, str(e)) from e
        except OSError as e:  # disk full, file locked or removed while reading
            raise HTTPException(422, f"Could not import {Path(req.path).name}: {e.strerror or e}") from e
        except Exception as e:  # e.g. a damaged file inside the zip: a message, not "Internal Server Error"
            raise HTTPException(422, f"Could not import {Path(req.path).name}: the file is damaged "
                                     f"({type(e).__name__})") from e
        engine.reload()
        return {"imported": manifest.kb_version, "documents": len(manifest.documents) or None,
                "skipped": manifest.skipped}

    @app.post("/api/kb/activate", dependencies=guarded)
    def activate(req: ActivateRequest):
        refuse_while_answering()
        try:
            bundle.activate(settings.data_dir, req.kb_version)
        except (bundle.BundleError, KnowledgeBaseError) as e:
            raise HTTPException(422, str(e)) from e
        engine.reload()
        return {"active": req.kb_version}

    @app.delete("/api/kb/versions/{kb_version}", dependencies=guarded)
    def remove_version(kb_version: str):
        try:
            bundle.remove_version(settings.data_dir, kb_version)
        except bundle.BundleError as e:
            raise HTTPException(422, str(e)) from e
        except OSError as e:  # a file of it still open
            raise HTTPException(422, f"Could not remove version {kb_version}: {e.strerror or e}") from e
        return {"removed": kb_version, "installed": bundle.list_versions(settings.data_dir)}

    # ── asking ──

    def _whole_time(result: AskResult, t0: float) -> AskResult:
        """The time from the question to the answer: some answers time only their last step (pictures
        found after the model sorted the request showed 0.0 s)."""
        result.seconds["total"] = max(result.seconds.get("total", 0.0), round(time.perf_counter() - t0, 2))
        return result

    def answer(req: AskRequest, on_event=None):
        """The coroutine answering a request in its mode (manuals pipeline or chat agent). Checks
        that can refuse the request run here, before a streamed response starts."""
        pipeline = engine.require_kb()
        if req.mode == "chat" and (not engine.config.chat_available or engine.chat is None):
            raise HTTPException(403, "Chat mode is turned off on this machine")
        # the same answer whatever the model: refusals, plain arithmetic, "plot a chart of ..."
        refused = fixed_reply(req.question)
        if refused is not None:
            return _done(AskResult(question=req.question, found=False, answer=refused, smalltalk=True, mode=req.mode))
        sum_ = arithmetic_answer(req.question)
        if sum_ is not None:
            return _done(AskResult(question=req.question, found=True, answer=sum_, mode=req.mode))
        if _DRAW.search(req.question) and not req.attachments:
            return _done(chart_answer(req))
        place = engine.place_for(req)
        if req.mode == "chat":
            return placed(chat_answer(req, pipeline, on_event, place), place, req)
        return placed(manuals_answer(req, pipeline, on_event, place=place), place, req)

    async def placed(pending, place: tuple[int, str] | None, req: AskRequest | None = None) -> AskResult:
        """The answer, with where the conversation is after it (see focus.py)."""
        result = await pending
        engine.place_after(result, place)
        engine.note_mixed_parts(result)
        if req is not None and not result.found and not result.considered and not result.smalltalk \
                and not result.clarify:
            # not in the documents: the closest sections, to look at instead of a dead end
            scope = request_scope(req)
            result.considered = [engine.pipeline.citation(h.doc_index, h.node_id)
                                 for h in engine.pipeline.index.search(req.question)
                                 if scope is None or h.doc_index in scope][:CLOSEST]
        return result

    async def step_answer(req: AskRequest, pipeline: Pipeline, on_event,
                          place: tuple[int, str] | None) -> AskResult | None:
        """"next" / "go back": the section after or before the one the conversation is at, in the
        manual's order (see focus.py). None when the question is something else."""
        asked = navigation(req.question)
        if asked is None:
            return await walk_answer(req, pipeline, on_event, place)
        way, title = asked
        if place is None:
            return AskResult(question=req.question, found=False, smalltalk=True, mode=req.mode,
                             answer='Ask about a topic first; "next" and "go back" then step through the '
                                    'manual from there.')
        di, nid = place
        doc = engine.docs[di]
        target = step_to(doc, nid, way, title)
        if target is None:
            return AskResult(question=req.question, found=False, smalltalk=True, mode=req.mode,
                             answer=f"That was the {'last' if way == 'next' else 'first'} section of "
                                    f"{engine.title_of(doc)}.")
        where = " > ".join(doc.short_path(target)[:-1])
        result = await pipeline.read_place(
            req.question, di, target, on_progress=on_event,
            read_as=f'Explain the section "{doc.sections[target].title}"' + (f" ({where})" if where else "") + ".")
        result.mode = req.mode
        if result.found and walking(req):
            result.answer = walk_step(doc, nid, target, result.answer)
        return result

    def walking(req: AskRequest) -> bool:
        """Is the conversation walking through a task step by step (its last answer was a step)?"""
        context = engine.context_for(req.conversation_id, replacing=req.replaces)
        return bool(context) and _WALK_STEP.match(context[-1][1]) is not None

    def walk_step(doc: Doc, came_from: str, target: str, answer: str) -> str:
        """A step's answer in a walk-through, framed as the first one was: "Step 2 of 5: ...", what
        comes next, and that the task is done after its last step (Next went on into the next section
        of the manual as if nothing had ended)."""
        parent = doc.sections[target].parent
        task = steps(doc, parent) if parent else []
        # a step of the same task (past its last step, the next chapter is no "Step 3 of 3")
        if target in task and came_from in task:
            k, title = task.index(target) + 1, heading(doc.sections[parent].title)
            after = (f"*Next step: {heading(doc.sections[task[k]].title)}.*" if k < len(task)
                     else f'*That was the last step of "{title}".*')
            return f"**Step {k} of {len(task)}: {heading(doc.sections[target].title)}**\n\n{answer}\n\n{after}"
        left = doc.sections[came_from].parent
        if left and came_from in steps(doc, left) and target not in doc.sections[left].children:
            return (f'*You have finished "{heading(doc.sections[left].title)}". This is the next section '
                    f"of the manual.*\n\n{answer}")
        return answer

    def chart_answer(req: AskRequest) -> AskResult:
        """"Plot a chart of weekly tickets": nothing here can draw one (a small model made up the
        numbers instead). The documents' own charts about it are shown."""
        context = engine.context_for(req.conversation_id, replacing=req.replaces)
        result = engine.picture_answer(req.question, engine.picture_topic("", req.question, context),
                                       request_scope(req), req.mode)
        result.answer = "I can't draw new charts. " + (
            result.answer if result.found else "The documents have no chart about this either.")
        return result

    def which_way_for(req: AskRequest, pipeline: Pipeline, place: tuple[int, str] | None) -> Clarification | None:
        """Chat mode: the question back for a step the manual describes in several ways, for the
        section picked from an earlier question back, or the one of the conversation's part the
        question names (see focus.py). None when there is no such choice."""
        target = picked_section(req)
        if target is None and place is not None and not _ELLIPTICAL.match(req.question) \
                and engine.context_for(req.conversation_id, replacing=req.replaces):
            found = near(req.question, pipeline.index, *place)
            target = (place[0], found) if found else None
        return pipeline.which_way(req.question, *target) if target is not None else None

    def picked_section(req: AskRequest) -> tuple[int, str] | None:
        """(doc index, node id) of the section an option picked after a question back stands for."""
        return engine.section_by_id(req.doc_ids[0], req.section) if req.section and req.doc_ids else None

    async def walk_answer(req: AskRequest, pipeline: Pipeline, on_event,
                          place: tuple[int, str] | None) -> AskResult | None:
        """"Walk me through creating a solution": its first step only, then Next for each of the
        others (see focus.py). None when the question is something else, or its section has no steps."""
        task_asked = walk_request(req.question)
        if task_asked is None:
            return None
        scope = request_scope(req)
        target = engine.section_by_id((req.doc_ids or [None])[0], req.section) if req.section else None
        if target is None and place is not None:
            found = near(task_asked, pipeline.index, *place)
            target = (place[0], found) if found else None
        if target is None:
            found = task_sections(task_asked, pipeline.index, scope)
            if len(found) > 1:  # "Creating a MultiX solution": in Multi X Classification or in Segmentation?
                labels = [f"{part_label(engine.docs[di], n)}" for di, n in found[:MAX_CLARIFY_OPTIONS]]
                if len(set(labels)) == len(labels):
                    title = engine.docs[found[0][0]].sections[found[0][1]].title.strip()
                    return AskResult(question=req.question, found=False, mode=req.mode,
                                     answer=f'"{title}" is in several parts of the manual. Which one do you mean?',
                                     clarify=Clarification(
                                         prompt=f'"{title}" is in several parts of the manual. Which one do you mean?',
                                         options=[ClarifyOption(label=label, detail=short_text(engine.docs[di], n),
                                                                doc_ids=[engine.docs[di].doc_id], section=n,
                                                                question=f"{req.question.rstrip(' ?')} in {label}")
                                                  for (di, n), label in zip(found, labels)]))
            target = found[0] if found else None
        if target is None or not (task := steps(engine.docs[target[0]], target[1])):
            return None
        di, nid = target
        doc = engine.docs[di]
        title, first = heading(doc.sections[nid].title), heading(doc.sections[task[0]].title)
        result = await pipeline.read_place(req.question, di, task[0], on_progress=on_event,
                                           read_as=f'Explain the first step of "{title}": "{first}".')
        if result.found:
            after = heading(doc.sections[task[1]].title)
            result.answer = (f"**Step 1 of {len(task)}: {first}**\n\n{result.answer}\n\n"
                             f"*Next step: {after}. Use **Next** above the question box to go on.*")
        result.mode = req.mode
        return result

    async def manuals_answer(req: AskRequest, pipeline: Pipeline, on_event,
                             classified: tuple[str, str] | None = None,
                             place: tuple[int, str] | None = None) -> AskResult:
        REGENERATING.set(engine.regenerating(req))
        stepped = await step_answer(req, pipeline, on_event, place)
        if stepped is not None:  # "next", "go back"
            return stepped
        scope = request_scope(req)
        context = engine.context_for(req.conversation_id, replacing=req.replaces)
        # a section picked or clicked ("Validation in Image Classification"): a question about it, not
        # a request for pictures (the word "Image" made it one) or a summary
        kind, topic = classified or (("question", "") if req.section else await engine.classify(req.question, pipeline))
        if kind == "pictures":
            request = engine.picture_topic(topic, req.question, context)
            return engine.picture_answer(req.question, request, scope, "manuals")
        if kind == "all_manuals" and not req.doc_ids:
            return pipeline.overview(req.question, scope)
        if kind in ("summary", "all_manuals"):
            target = engine.summary_target(req, scope, topic)
            if isinstance(target, Clarification):
                return AskResult(question=req.question, found=False, answer=target.prompt, clarify=target)
            return await pipeline.summarize(req.question, target, on_progress=on_event)
        if live_only(req.question, pipeline.index):
            # "what's the weather today": no document has it, and the model would only say so slowly
            return AskResult(question=req.question, found=False, answer=LIVE_REPLY, smalltalk=True)
        reply = smalltalk_reply(req.question)
        if reply is not None:  # "who are you": an introduction, not "which document?"
            return AskResult(question=req.question, found=False, answer=reply, smalltalk=True)
        vague = None if context or req.doc_ids else vague_clarification(req.question, pipeline.index, scope)
        if vague is not None:  # "what is the limit?": which one (see vague.py)
            return AskResult(question=req.question, found=False, answer=vague.prompt, clarify=vague)
        engine.start_semantic()
        # an option picked after "which part do you mean?": its section is read straight away
        focus = next(((i, req.section) for i, d in enumerate(engine.docs)
                      if req.section and d.doc_id in req.doc_ids[:1] and req.section in d.sections), None)
        result = await pipeline.ask(req.question, on_progress=on_event, doc_filter=scope, context=context,
                                    clarify=not req.doc_ids,
                                    previous=engine.last_cited(req.conversation_id, req.replaces),
                                    semantic=engine.semantic, focus=focus, place=place)
        mention = picture_request(req.question)  # "What does the wiring diagram show?"
        if mention is not None and result.found:
            engine.cite_pictures(result, engine.picture_refs(mention.topic, scope), MAX_FIGURES)
        return result

    def chat_answer(req: AskRequest, pipeline: Pipeline, on_event, place: tuple[int, str] | None = None):
        reply = smalltalk_reply(req.question)
        if reply in CLOSING_REPLIES:  # "thanks", "nice work", "bye": no model call (greetings go to it)
            return _done(AskResult(question=req.question, found=False, answer=reply, smalltalk=True, mode="chat"))
        return chat_routed(req, pipeline, on_event, greeting=reply is not None, place=place)

    async def chat_routed(req: AskRequest, pipeline: Pipeline, on_event, greeting: bool,
                          place: tuple[int, str] | None = None) -> AskResult:
        """Chat mode: "show me pictures" and "which manuals are there" get the same direct answer as
        in Manuals mode (any model, instantly); everything else goes to the agent and its tools."""
        REGENERATING.set(engine.regenerating(req))
        if greeting:
            # "hi" gets a fixed reply on every model: a small one answered it with whatever the
            # documents say about something, a large one with the conversation's last question
            # again (it saw an answer replaced by "not covered" and tried once more)
            return AskResult(question=req.question, found=False, answer=smalltalk_reply(req.question) or "",
                             smalltalk=True, mode="chat")
        stepped = await step_answer(req, pipeline, on_event, place)
        if stepped is not None:  # "next", "go back": the next section, read as in Manuals mode
            return stepped
        scope = request_scope(req)
        attached = engine.attachments.list(req.conversation_id)
        # "summarize this file": about an attachment, so the agent answers (not the shortcuts below)
        about_attachment = bool(req.attachments) or (bool(attached) and bool(_ABOUT_ATTACHMENT.search(req.question)))
        # "write an essay on all the documents": a text to write, not the list of documents
        compose = bool(_COMPOSE.search(req.question))
        if not greeting and not about_attachment:
            kind, topic = ("question", "") if req.section else await engine.classify(req.question, pipeline)
            if compose and kind in ("summary", "all_manuals"):
                kind = "question"
            if kind == "pictures":
                context = engine.context_for(req.conversation_id, replacing=req.replaces)
                return engine.picture_answer(req.question, engine.picture_topic(topic, req.question, context),
                                             scope, "chat")
            if kind == "all_manuals" and not req.doc_ids:
                return pipeline.overview(req.question, scope, "chat")
            # "summarize the meeting notes": the same summary as in Manuals mode (a small model
            # summarizing through its tools mixed other documents in); "summarize our chat" is the agent's
            if kind in ("summary", "all_manuals") and not _ABOUT_CHAT.search(req.question):
                target = engine.summary_target(req, scope, topic)
                if isinstance(target, Clarification):
                    return AskResult(question=req.question, found=False, answer=target.prompt, clarify=target,
                                     mode="chat")
                result = await pipeline.summarize(req.question, target, on_progress=on_event)
                result.mode = "chat"
                return result
            # "what is the limit?" as a first question: which one (see vague.py); "summarize our
            # chat" and "make it shorter" are about the conversation, not vague
            if not req.doc_ids and not _ABOUT_CHAT.search(req.question) \
                    and not engine.context_for(req.conversation_id, replacing=req.replaces):
                # "How long?" with no topic: a larger model's assistant asks what is meant; a small one
                # (documents only) declined it as a general question (final check), so it is asked here
                vague = vague_clarification(req.question, pipeline.index, scope,
                                            topicless=not _ABOUT_SELF_OR_CHAT.search(req.question)
                                            and await engine.chat_is_small(engine.chat_llm))
                if vague is not None:
                    return AskResult(question=req.question, found=False, answer=vague.prompt, clarify=vague,
                                     mode="chat")
            # "How do I add data?" where the manual gives a Type 1 and a Type 2: asked back here, as in
            # Manuals mode, rather than left to the assistant (which asks only on a first question)
            if not _ABOUT_CHAT.search(req.question):
                which = which_way_for(req, pipeline, place)
                if which is not None:
                    return AskResult(question=req.question, found=False, answer=which.prompt, clarify=which,
                                     mode="chat")
            # an option picked after a question back ("Multi X Classification"): that section, read
            # as in Manuals mode. Told which one was picked, the assistant (Nemotron 3 Super on
            # OpenRouter) read the namesake in the PyTorch chapter instead (real-model test, 20 Oct)
            # ... and a follow-up naming a section of the part the conversation is in ("How do I test
            # it?" in Multi X): its Testing, read the same way. Told so, the assistant cited Binary
            # Segmentation's Testing (same words) and the conversation left Multi X (same test)
            in_place = place is not None and not _ELLIPTICAL.match(req.question) \
                and bool(engine.context_for(req.conversation_id, replacing=req.replaces)) \
                and near(req.question, pipeline.index, *place) is not None
            if (picked_section(req) is not None or in_place) and not attached and not compose:
                result = await manuals_answer(req, pipeline, on_event, classified=("question", ""), place=place)
                result.mode = "chat"
                return result
            # a small model (documents only) asked about the documents: the Manuals answer, which checks
            # what the model wrote. Its own Chat answers did worse on the same questions (complex web
            # eval on the 0.8B: 31/48 in Chat, 38/48 in Manuals) and invented answers for things the
            # documents do not cover (the HX-700's spindle speed). Files, writing and the conversation
            # itself stay with the assistant
            if not attached and not compose and not _ABOUT_SELF_OR_CHAT.search(req.question) \
                    and not _ABOUT_CHAT.search(req.question) and await engine.chat_is_small(engine.chat_llm):
                result = await manuals_answer(req, pipeline, on_event, classified=(kind, topic), place=place)
                # ... except a bare number ("1" for "how many sick days in a row without a certificate?",
                # "12" for the price per user; reasoning eval): the assistant reasons those out better
                if not (result.found and re.fullmatch(r"\d{1,3}", result.answer.strip().rstrip("."))):
                    result.mode = "chat"
                    return result
        engine.start_semantic()  # after an import or a settings change: build in the background
        conversation = req.conversation_id
        pending = engine.chat.ask(
            req.question, pipeline.docs, pipeline.index, allowed=scope,
            context=engine.context_for(req.conversation_id, HISTORY_TURNS, req.replaces), on_event=on_event,
            find_pictures=lambda topic: engine.picture_refs(topic_tokens(topic), scope),
            semantic=engine.semantic, attachments=attached,
            about=list(req.attachments) or ([a.id for a in attached] if about_attachment else
                                            _attachments_named(req.question, attached,
                                                               lambda aid: engine.attachments.text(conversation, aid))),
            model_label=await engine.model_label(engine.chat_llm),
            documents_only=await engine.chat_is_small(engine.chat_llm),
            tier=engine.chat_tier(), earlier=engine.earlier_sections(req.conversation_id, req.replaces), place=place,
            picked=picked_section(req),
            attachment_text=(lambda aid: engine.attachments.text(conversation, aid)) if conversation else None)
        return await chat_with_pictures(_as_smalltalk(pending) if greeting else pending)

    async def chat_with_pictures(pending) -> AskResult:
        """The pages the agent asked to show become figures, citing the section each page is in."""
        result = await pending
        if result.picture_pages:
            engine.cite_pictures(result, result.picture_pages, MAX_LISTED)
        return result

    def request_scope(req: AskRequest) -> set[int] | None:
        """The manuals a request may use: its station filter, narrowed to the picked manuals."""
        scope = engine.allowed(req.tags)
        if req.doc_ids:
            picked = {i for i, d in enumerate(engine.docs) if d.doc_id in req.doc_ids}
            scope = picked if scope is None else scope & picked
        return scope

    @app.post("/api/ask", response_model=AskResult, dependencies=guarded)
    async def ask(req: AskRequest):
        t0 = time.perf_counter()
        engine.answering += 1
        try:
            result = _whole_time(await answer(req), t0)
        except ProviderBusy as e:
            raise HTTPException(503, str(e)) from e
        except ModelRanAway as e:
            raise HTTPException(502, RAN_AWAY) from e
        except ModelRefused as e:
            raise HTTPException(502, str(e)) from e
        except BudgetExceeded as e:
            raise HTTPException(429, str(e)) from e
        finally:
            engine.answering -= 1
        return engine.record(result, req.tags, req.conversation_id, req.replaces)

    @app.post("/api/ask/stream", dependencies=guarded)
    async def ask_stream(req: AskRequest):
        """Server-sent events, then {"stage": "done", "result": {...}} or {"stage": "error", ...}.
        Manuals mode: {"stage": "selecting"}, {"stage": "reading", "sections": [...]}.
        Chat mode: {"stage": "tool", "tool", "detail"}, {"stage": "writing"} (a new reply starts),
        {"stage": "delta", "text": "..."} (answer text as it is written)."""
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        t0 = time.perf_counter()
        placeholder: list[str] = []  # the question saved before its answer, once the model works on it
        started: list[bool] = []

        async def on_event(event: dict) -> None:
            # instant replies (greetings, declines) send no events and save no placeholder; nor does
            # Regenerate or Edit: the answer it replaces stays in History until the new one comes
            # (a placeholder replacing it hid a good answer for good when the new one failed)
            if not started:
                started.append(True)
                if not req.replaces:
                    placeholder.append(engine.hold_place(req))
                    # the app's Ask again / Try again replaces the placeholder, not adds a turn after it
                    await queue.put({"stage": "held", "history_id": placeholder[0]})
            await queue.put(event)
        pending = answer(req, on_event=on_event)  # raises 409 / 403 before streaming starts

        async def failed(status: int, message: str) -> None:
            # an error is shown with Try again: the placeholder would stay in History as a turn
            # "stopped" before the answer Try again gives (kept only when the answer never comes)
            if placeholder:
                engine.history.remove(lambda e: e.get("id") == placeholder[0])
            await queue.put({"stage": "error", "status": status, "message": message})

        async def work() -> None:
            engine.answering += 1
            try:
                result = _whole_time(await pending, t0)
                done = engine.record(result, req.tags, req.conversation_id,
                                     placeholder[0] if placeholder else req.replaces)
                await queue.put({"stage": "done", "result": done.model_dump()})
            except ProviderBusy as e:
                await failed(503, str(e))
            except ModelRanAway:
                await failed(502, RAN_AWAY)
            except ModelRefused as e:
                await failed(502, str(e))
            except BudgetExceeded as e:
                await failed(429, str(e))
            except Exception:  # surface, don't hang the client; the details go to backend.log
                log.exception("answering failed: %r", req.question[:200])
                await failed(500, UNEXPECTED_ERROR)
            finally:
                engine.answering -= 1
                await queue.put(None)

        async def events():
            task = asyncio.create_task(work())
            try:
                while (event := await queue.get()) is not None:
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                task.cancel()  # client went away: stop working on the answer

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # ── manuals ──

    @app.get("/api/docs", dependencies=guarded)
    def docs():
        engine.require_kb()
        return [{"doc_id": d.doc_id, "name": d.name, "title": engine.title_of(d), "description": d.description,
                 "kind": d.kind, "sections": len(d.sections), "tags": engine.tags_of(d),
                 "has_original": engine.original_path(d) is not None,
                 "pages": max(d.pages) if d.pages else None,
                 # the file name a readable title was made from: its document code ("[B-ACD-E1]") may matter
                 "file": (d.title or d.name) if engine.title_of(d) != (d.title or d.name) else None}
                for d in engine.docs]

    @app.get("/api/tags", dependencies=guarded)
    def tags():
        return sorted({t for d in engine.docs for t in engine.tags_of(d)}, key=str.lower)

    @app.get("/api/docs/{doc_id}/pages/{page}/image", dependencies=guarded)
    def page_image(doc_id: str, page: int, crop: bool = False):
        """A page of the original PDF as a PNG (for figures shown with an answer); crop=true: only
        the figure on the page."""
        engine.require_kb()
        doc = engine.docs[engine.doc_index(doc_id)]
        pdf = engine.original_path(doc)
        if pdf is None:
            raise HTTPException(404, f"No original PDF for {doc.name}")
        try:
            png = render_page(pdf, page, settings.data_dir / "cache" / "pages", crop=crop)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return FileResponse(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/docs/{doc_id}/images/{name}", dependencies=guarded)
    def doc_image(doc_id: str, name: str):
        """A picture a web-platform export extracted from this document."""
        engine.require_kb()
        doc = engine.docs[engine.doc_index(doc_id)]
        folder = doc.assets_dir / "images" if doc.assets_dir else None
        if folder is None or not IMAGE_NAME.fullmatch(name):
            raise HTTPException(404, "No such picture")
        path = folder / name
        if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
            raise HTTPException(404, "No such picture")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/docs/{doc_id}/toc", dependencies=guarded)
    def toc(doc_id: str):
        engine.require_kb()
        doc = engine.docs[engine.doc_index(doc_id)]
        return {"doc_id": doc.doc_id, "name": doc.name, "title": engine.title_of(doc),
                "description": doc.description, "kind": doc.kind,
                "sections": [{"node_id": s.node_id, "title": s.title, "depth": s.depth, "parent": s.parent,
                              "locator": doc.locator(s.node_id), "summary": s.summary,
                              "summary_is_fallback": s.summary_is_fallback, "has_children": bool(s.children)}
                             for s in doc.sections.values()]}

    @app.get("/api/docs/{doc_id}/sections/{node_id}", dependencies=guarded)
    def section(doc_id: str, node_id: str):
        engine.require_kb()
        doc = engine.docs[engine.doc_index(doc_id)]
        s = doc.sections.get(node_id)
        if s is None:
            raise HTTPException(404, f"No section {node_id!r} in {doc.name}")
        return {"doc_id": doc.doc_id, "doc_name": doc.name, "doc_title": engine.title_of(doc),
                "node_id": node_id, "title": s.title, "path": doc.short_path(node_id),
                "locator": doc.locator(node_id),
                "pages": list(range(s.start_page, s.end_page + 1)) if doc.kind == "pages" else None,
                "kind": doc.kind, "text": doc.section_text(node_id, SECTION_VIEW_CHARS),
                "has_original": engine.original_path(doc) is not None,
                "images": [{"image": name, "section": sub.title, "page": sub.start_page, **sub.image_notes.get(name, {})}
                           for sub in doc.subtree(node_id) for name in sub.images]}

    @app.get("/api/docs/{doc_id}/original", dependencies=guarded)
    def original(doc_id: str):
        engine.require_kb()
        path = engine.original_path(engine.docs[engine.doc_index(doc_id)])
        if path is None:
            raise HTTPException(404, "The knowledge base does not include the original of this manual")
        return FileResponse(path, media_type="application/pdf", filename=path.name,
                            content_disposition_type="inline")

    @app.get("/api/search", dependencies=guarded)
    def search(q: str = Query(min_length=1, max_length=200), tags: list[str] = Query(default=[])):
        pipeline = engine.require_kb()
        allowed = engine.allowed(tags)
        hits = [h for h in pipeline.index.search(q) if allowed is None or h.doc_index in allowed]
        # a code ("WEB-142", "E-217"): the search also matches its parts, "WEB" or "142" alone; when some
        # sections have the whole code, only those are listed
        codes = [c.casefold() for c in _CODE.findall(q)]
        if codes:
            whole = [h for h in hits[:MAX_SEARCH_HITS * 5] if all(
                c in engine.docs[h.doc_index].section_text(h.node_id).casefold() for c in codes)]
            hits = whole or hits
        # A parent section contains its subsections' text; when a subsection matches, list only it.
        matched = {(h.doc_index, h.node_id) for h in hits}
        hits = [h for h in hits if not any((h.doc_index, sub.node_id) in matched
                                           for sub in engine.docs[h.doc_index].subtree(h.node_id)[1:])]
        out = []
        for h in hits[:MAX_SEARCH_HITS]:
            doc = engine.docs[h.doc_index]
            out.append({"doc_id": doc.doc_id, "doc_title": engine.title_of(doc), "kind": doc.kind,
                        "node_id": h.node_id,
                        "title": doc.sections[h.node_id].title, "path": doc.short_path(h.node_id),
                        "locator": doc.locator(h.node_id), "score": round(h.score, 2),
                        "snippet": pipeline.index.snippet(h.doc_index, h.node_id, q)})
        return out

    @app.get("/api/suggestions", dependencies=guarded)
    def suggestions(tags: list[str] = Query(default=[])):
        """Example questions: leaf sections that have a real summary, spread across the manuals."""
        engine.require_kb()
        allowed = engine.allowed(tags)
        summarized, plain = {}, {}
        for i, d in enumerate(engine.docs):
            if allowed is not None and i not in allowed:
                continue
            noun = _doc_noun(d)
            names = _names_of(d, noun)
            for s in d.sections.values():
                title = re.sub(r"^Sheet:\s*", "", s.title.split(";")[0].strip().rstrip(":"))
                title = re.sub(r"^\d+(\.\d+)*\.?\s+", "", title)  # "3.1 Remote Work"
                # "What This Warranty Covers" is a question already: "say about What ..." reads badly
                # a task heading ("Creating a MultiX solution") is a good example even with subsections
                if (not s.children or _is_task(title)) and s.title != "Preface" and 3 <= len(title) <= 60 \
                        and not _WH_TITLE.match(title) and not _unsuggestable(title, names):
                    # manuals often share titles ("Setup"): the first one names it
                    (plain if s.summary_is_fallback else summarized).setdefault(title, (i, noun))
        # sections with a real summary first; a document without any still takes part
        with_summary = {i for i, _ in summarized.values()}
        leaves = list(summarized.items()) + [(t, v) for t, v in plain.items() if v[0] not in with_summary]
        if not leaves:
            return []
        # spread over the documents: each one's sections in turn, evenly through each document
        by_doc: dict[int, list[tuple[str, str]]] = {}
        for t, (i, noun) in leaves:
            by_doc.setdefault(i, []).append((t, noun))
        # each document's share of the four, evenly through it ("len // 2" gave one manual only two);
        # "How do I ..." questions are the better examples: a document with enough task headings
        # is sampled from those
        share = -(-4 // len(by_doc))
        pools = []
        for items in by_doc.values():
            tasks = [x for x in items if _is_task(x[0])]
            pools.append(tasks if len(tasks) >= share else items)
        picked: list[tuple[str, str]] = []
        for rank in range(share):
            for pool in pools:
                step = max(1, len(pool) // share)
                if rank * step < len(pool) and len(picked) < 4:
                    picked.append(pool[rank * step])
        questions = [_example_question(t, noun) for t, noun in picked]
        # documents with pictures: one example shows that Lattice can show them
        pictured = [(len(engine.pictures_of(i)), i) for i in range(len(engine.docs))
                    if (allowed is None or i in allowed) and engine.docs[i].assets_dir is not None]
        count, di = max(pictured, key=lambda p: (p[0], -p[1]), default=(0, -1))
        if count:  # in place of a "What does ... say about" question: "How do I ..." ones are the better examples
            plain = [i for i, q in enumerate(questions) if not q.startswith("How do I")]
            if len(questions) >= 4 and plain:
                del questions[plain[-1]]
            noun = _doc_noun(engine.docs[di])
            if noun == "the manual":  # "manual" alone names no document: "the user manual" does
                noun = "the " + re.sub(r"\.[A-Za-z0-9]{2,5}$", "", engine.docs[di].display_title).replace("_", " ")
            questions = questions[:3] + [f"Show me the pictures in {noun}"]
        return questions

    # ── settings (Manage) ──

    @app.get("/api/config", dependencies=guarded)
    def get_config():
        return public_view(engine.config)

    @app.put("/api/config", dependencies=guarded)
    def put_config(update: ConfigUpdate):
        def stored(conn: ConnectionUpdate, previous: Connection | None) -> Connection:
            if conn.kind == "remote" and not (conn.base_url.startswith(("http://", "https://")) and conn.model):
                raise HTTPException(422, "A remote model needs an http(s) endpoint URL and a model name")
            key = (previous.api_key if previous else "") if conn.api_key is None else conn.api_key.strip()
            return Connection(kind=conn.kind, base_url=conn.base_url.strip(), model=conn.model.strip(),
                              api_key=key, output_mode=conn.output_mode, reasoning=conn.reasoning)

        def stored_embeddings(emb: EmbeddingUpdate | None) -> EmbeddingConnection | None:
            if emb is None or not (emb.base_url.strip() or emb.model.strip()):
                return None
            if not (emb.base_url.strip().startswith(("http://", "https://")) and emb.model.strip()):
                raise HTTPException(422, "An embedding server needs an http(s) endpoint URL and a model name")
            previous = engine.config.embeddings
            key = (previous.api_key if previous else "") if emb.api_key is None else emb.api_key.strip()
            return EmbeddingConnection(base_url=emb.base_url.strip(), model=emb.model.strip(), api_key=key)

        chat = update.chat_connection
        engine.config = AppConfig(
            profile=update.profile, default_mode=update.default_mode, work_topics_only=update.work_topics_only,
            connection=stored(update.connection, engine.config.connection),
            chat_connection=stored(chat, engine.config.chat_connection) if chat else None,
            embeddings=stored_embeddings(update.embeddings))
        save_config(settings.data_dir, engine.config)
        engine.apply_config()
        return public_view(engine.config)

    @app.post("/api/config/test", dependencies=guarded)
    def test_connection(conn: ConnectionUpdate, target: Literal["main", "chat"] = "main"):
        previous = engine.config.chat_connection if target == "chat" else engine.config.connection
        key = (previous.api_key if previous else "") if conn.api_key is None else conn.api_key.strip()
        ok, detail, models = probe_endpoint(LLMConfig(base_url=conn.base_url.strip(), model=conn.model.strip(),
                                                      api_key=key))
        return {"ok": ok, "detail": detail, "models": models[:200]}

    @app.post("/api/config/test-embeddings", dependencies=guarded)
    async def test_embeddings(emb: EmbeddingUpdate):
        """Embed one short text with these settings: reachable, and how many dimensions."""
        previous = engine.config.embeddings
        key = (previous.api_key if previous else "") if emb.api_key is None else emb.api_key.strip()
        # said as the language model's test says it ("embedding request failed: ConnectError: All
        # connection attempts failed" before)
        url = emb.base_url.strip()
        if not re.match(r"https?://[^/\s]+", url, re.IGNORECASE):
            return {"ok": False, "detail": "The address must start with http:// or https://, e.g. http://server:8000/v1"}
        if not emb.model.strip():
            return {"ok": False, "detail": "Enter the embedding model's name, as the server lists it"}
        try:
            vectors = await EmbeddingClient(url, emb.model.strip(), key).embed(["connection test"], timeout=20)
        except EmbeddingError as e:
            text = str(e)
            if "ConnectError" in text or "ConnectTimeout" in text:
                text = f"Cannot reach {url}: check the address, and that the embedding server is running"
            elif "401" in text or "403" in text:
                text = "The server refused the API key"
            elif "404" in text:
                text = f'The server has no embedding model "{emb.model.strip()}", or the address is not its /v1 address'
            return {"ok": False, "detail": text}
        return {"ok": True, "detail": f"works: {len(vectors[0])} dimensions"}

    # ── attachments (Chat mode) ──

    @app.post("/api/attachments", dependencies=guarded)
    def add_attachment(upload: AttachmentUpload):
        """A file for the agent to read in this conversation: converted to text on this machine."""
        if not engine.config.chat_available:
            raise HTTPException(403, "Chat mode is turned off on this machine")
        try:
            data = base64.b64decode(upload.data_base64, validate=True)
        except (ValueError, binascii.Error) as e:
            raise HTTPException(422, "The file data is not valid base64") from e
        try:
            att = engine.attachments.add(upload.conversation_id, upload.name, data)
        except AttachmentError as e:
            raise HTTPException(422, str(e)) from e
        return {"id": att.id, "name": att.name, "chars": att.chars}

    @app.get("/api/attachments", dependencies=guarded)
    def list_attachments(conversation_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")):
        return [a.__dict__ for a in engine.attachments.list(conversation_id)]

    # ── history and feedback ──

    @app.get("/api/history", dependencies=guarded)
    def history(limit: int = Query(default=50, ge=1, le=500), conversation_id: str | None = None):
        """Newest first. With conversation_id: that conversation's questions, oldest first. Each
        entry carries the latest vote on it ("feedback"), so a reopened answer shows it."""
        if conversation_id:
            entries = list(reversed(engine.conversation(conversation_id)[:limit]))
        else:
            entries = live_entries(engine.history.read(limit))
        votes = {v.get("history_id"): v for v in latest_votes()}
        # sources under the document's name as shown now (a name made readable later, a renamed file)
        names = {d.doc_id: engine.title_of(d) for d in engine.docs}
        entries = [{**e, "citations": [{**c, "doc_name": names.get(c.get("doc_id"), c.get("doc_name"))}
                                       for c in e["citations"]]} if e.get("citations") else e for e in entries]
        # where the conversation was, as the documents are now (gone after an update: not shown)
        entries = [{**e, "focus": focus_of(engine.docs[at[0]], at[1]).model_dump() if (at := engine.section_by_id(
            e["focus"].get("doc_id"), e["focus"].get("node_id"))) else None} if e.get("focus") else e for e in entries]
        return [{**e, "feedback": {"rating": votes[e["id"]]["rating"], "comment": votes[e["id"]].get("comment", "")}}
                if e.get("id") in votes else e for e in entries]

    @app.delete("/api/history", dependencies=guarded)
    def delete_history(conversation_id: str = Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")):
        """Delete one conversation: every entry with that conversation id (an entry saved before
        conversations existed is its own, by its id) and its attachments. Votes stay in feedback."""
        removed = engine.history.remove(lambda e: conversation_of(e) == conversation_id)
        engine.attachments.remove(conversation_id)
        if not removed:
            raise HTTPException(404, "No such conversation in the history")
        return {"deleted": removed}

    @app.post("/api/feedback", dependencies=guarded)
    def feedback(req: FeedbackRequest):
        # the whole history: an answer in an older conversation can be voted on too (only the last
        # 500 entries were searched, and voting on it said it was no longer in the history)
        asked = next((h for h in engine.history.read(limit=None) if h.get("id") == req.history_id), None)
        if asked is None:
            raise HTTPException(404, "That answer is no longer in the history")
        entry = engine.feedback.append({
            "history_id": req.history_id, "rating": req.rating, "comment": req.comment.strip(),
            "question": asked.get("question"), "answer": asked.get("answer"),
            "citations": asked.get("citations"), "kb_version": asked.get("kb_version"), "model": asked.get("model"),
        })
        return {"id": entry["id"]}

    def latest_votes() -> list[dict]:
        """Newest first, one per answer: voting again on an answer replaces the earlier vote."""
        seen, out = set(), []
        for v in engine.feedback.read(limit=None):
            if v.get("history_id") not in seen:
                seen.add(v.get("history_id"))
                out.append(v)
        return out

    @app.get("/api/feedback/summary", dependencies=guarded)
    def feedback_summary():
        votes = latest_votes()
        return {"total": len(votes), "helpful": sum(v.get("rating") == "helpful" for v in votes),
                "wrong": sum(v.get("rating") == "wrong" for v in votes)}

    @app.post("/api/feedback/export", dependencies=guarded)
    def feedback_export(req: ExportRequest):
        target = Path(req.path)
        if target.suffix.lower() != ".json":
            raise HTTPException(422, "Choose a .json file name for the export")
        payload = {"exported_at": datetime.now(timezone.utc).isoformat(), "engine_version": __version__,
                   "kb_version": engine.manifest.kb_version if engine.manifest else None,
                   "feedback": list(reversed(latest_votes()))}
        try:
            target.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            raise HTTPException(422, f"Could not write {target}: {e.strerror}") from e
        return {"path": str(target), "count": len(payload["feedback"])}

    return app
