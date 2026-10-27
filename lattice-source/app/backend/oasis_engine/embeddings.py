"""Search by meaning, next to keyword search, in both modes.

Keyword search misses questions worded unlike the text ("how sharp at the edges" for "MTF50
corner") and questions in another language. With an embedding model, every section is embedded
once; a question is embedded and compared with them. The model runs either on this computer (a
second llama-server that the desktop app starts when an embedding model is installed: works
offline, for line PCs) or on an office server (any OpenAI-compatible /embeddings endpoint: vLLM,
TEI, Ollama ...), set in Manage.

Most embedding models are trained with a short task label before questions and before documents
("search_query: ..."); without it they retrieve noticeably worse. prefixes_for() knows the labels
of the common families by the model's name.

Vectors are cached on disk by the hash of the embedded text, so a new knowledge-base version only
embeds the sections that changed, and restarting the app embeds nothing. The index builds in the
background; until it is ready (or if the server cannot be reached) keyword search works as before.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path

import httpx
import numpy as np

from .models import Doc
from .search import Hit

log = logging.getLogger("oasis_engine.embeddings")

BATCH = 32
MAX_CACHED = 50_000  # vectors kept in the cache, of all knowledge-base versions (about 150 MB at 768 dims)
SECTION_CHARS = 1_500     # of a section's own text, after its title path and summary
QUERY_TIMEOUT_S = 20.0
BUILD_TIMEOUT_S = 120.0
MIN_SCORE = 0.2           # cosine similarity below this is noise
RETRY_S = 60.0            # a failed index build is tried again after this long, not sooner


class EmbeddingError(RuntimeError):
    pass


# (words in the model's name, question label, document label), first match wins
_PREFIXES = (
    (("embeddinggemma", "embedding-gemma", "gemma-embed"), "task: search result | query: ", "title: none | text: "),
    (("nomic-embed",), "search_query: ", "search_document: "),
    (("e5-", "e5_"), "query: ", "passage: "),
    (("qwen3-embedding", "qwen3_embedding"),
     "Instruct: Given a question, retrieve passages that answer it\nQuery: ", ""),
    (("bge-small-en", "bge-base-en", "bge-large-en"), "Represent this sentence for searching relevant passages: ", ""),
)


def prefixes_for(model: str) -> tuple[str, str]:
    """(question label, document label) the model was trained with; none for unknown models."""
    name = model.lower()
    for words, query, document in _PREFIXES:
        if any(w in name for w in words):
            return query, document
    return "", ""


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, api_key: str = ""):
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.query_prefix, self.document_prefix = prefixes_for(model)

    async def embed(self, texts: list[str], timeout: float = BUILD_TIMEOUT_S) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=timeout) as http:
            for i in range(0, len(texts), BATCH):
                batch = texts[i:i + BATCH]
                try:
                    r = await http.post(self.url, headers=self.headers, json={"model": self.model, "input": batch})
                    r.raise_for_status()
                    data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
                except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
                    raise EmbeddingError(f"embedding request failed: {type(e).__name__}: {str(e)[:200]}") from e
                if len(data) != len(batch):
                    raise EmbeddingError(f"asked for {len(batch)} embeddings, got {len(data)}")
                out += [d["embedding"] for d in data]
        return out


def section_text(doc: Doc, node_id: str) -> str:
    """What is embedded for a section: where it is, its summary and the start of its own text."""
    s = doc.sections[node_id]
    own = re.sub(r"\s+", " ", s.own_text.replace("[Figure]", " ")).strip()[:SECTION_CHARS]
    return f"{doc.display_title} > {' > '.join(doc.path(node_id))}\n{s.summary}\n{own}".strip()


class SemanticIndex:
    def __init__(self, docs: list[Doc], client: EmbeddingClient, cache_dir: Path):
        self.docs, self.client = docs, client
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", client.model).strip("-") or "model"
        self.cache_file = Path(cache_dir) / f"{safe}.npz"
        self.keys = [(di, nid) for di, d in enumerate(docs) for nid in d.sections]
        self.matrix: np.ndarray | None = None
        self.status = "not started"   # "indexing", "ready" or "error: ..."
        self.progress: tuple[int, int] = (0, len(self.keys))  # sections embedded (or cached), of all
        self._task: asyncio.Task | None = None
        self._failed_at = -RETRY_S

    @property
    def ready(self) -> bool:
        return self.matrix is not None

    def start(self) -> None:
        """Build in the background on the running event loop: once, and again after an error, but
        not more often than RETRY_S (the UI's health polls call this every few seconds)."""
        failed_recently = time.monotonic() - self._failed_at < RETRY_S
        if self._task is None or (self._task.done() and not self.ready and not failed_recently):
            self._task = asyncio.get_running_loop().create_task(self.build())

    def _load_cache(self) -> dict[str, np.ndarray]:
        try:
            with np.load(self.cache_file) as f:
                return dict(zip(f["hashes"].tolist(), f["vectors"]))
        except (FileNotFoundError, OSError, KeyError, ValueError):
            return {}

    async def build(self) -> None:
        t0 = time.perf_counter()
        self.status = "indexing"
        # the label is part of what is embedded (and hashed): a model change re-embeds everything
        texts = [self.client.document_prefix + section_text(self.docs[di], nid) for di, nid in self.keys]
        hashes = [hashlib.sha1(t.encode("utf-8")).hexdigest() for t in texts]
        cache = self._load_cache()
        missing = [i for i, h in enumerate(hashes) if h not in cache]
        self.progress = (len(hashes) - len(missing), len(hashes))
        try:
            # a batch at a time, so Manage can show how far it got (a large library takes many minutes)
            for start in range(0, len(missing), BATCH):
                part = missing[start:start + BATCH]
                vectors = await self.client.embed([texts[i] for i in part])
                for i, v in zip(part, vectors):
                    cache[hashes[i]] = np.asarray(v, dtype=np.float32)
                self.progress = (self.progress[0] + len(part), len(hashes))
            dims = {len(cache[h]) for h in hashes}
            if len(dims) > 1:  # the model changed its size: embed everything again
                raise EmbeddingError("cached vectors have different sizes; delete the cache and rebuild")
            matrix = np.stack([cache[h] for h in hashes]) if hashes else np.zeros((0, 1), np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            self.matrix = matrix / np.where(norms == 0, 1, norms)
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            # this version's sections, and other versions' up to a limit: switching back to a knowledge
            # base indexed before re-embedded all of it (minutes on a laptop; final computer-use check)
            current = set(hashes)
            keep = sorted(current) + [h for h in cache if h not in current][:max(0, MAX_CACHED - len(current))]
            np.savez(self.cache_file, hashes=np.array(keep), vectors=np.stack([cache[h] for h in keep])
                     if keep else np.zeros((0, 1), np.float32))
            self.status = "ready"
            log.info("semantic index ready: %d sections (%d embedded) in %.1fs",
                     len(hashes), len(missing), time.perf_counter() - t0)
        except (EmbeddingError, OSError, ValueError) as e:
            self._failed_at = time.monotonic()
            self.status = f"error: {e}"
            log.warning("semantic index not built: %s", e)

    async def search(self, query: str, allowed: set[int] | None = None, limit: int = 8) -> list[Hit]:
        """Sections closest in meaning to the query, best first; [] when not ready or on error."""
        if self.matrix is None or not len(self.keys):
            return []
        try:
            q = np.asarray((await self.client.embed([self.client.query_prefix + query], timeout=QUERY_TIMEOUT_S))[0],
                           dtype=np.float32)
        except EmbeddingError as e:
            # shown in Manage until a search works again; callers treat it as no search by meaning
            log.warning("semantic search failed: %s", e)
            self.status = f"unavailable: {e}"
            return []
        self.status = "ready"
        if q.shape[0] != self.matrix.shape[1]:
            return []
        scores = self.matrix @ (q / (np.linalg.norm(q) or 1.0))
        hits = []
        for i in np.argsort(-scores):
            if scores[i] < MIN_SCORE or len(hits) == limit:
                break
            di, nid = self.keys[i]
            if allowed is None or di in allowed:
                hits.append(Hit(di, nid, float(scores[i])))
        return hits


def union(keyword: list[Hit], meaning: list[Hit], limit: int = 8, keyword_first: int = 1) -> list[Hit]:
    """Keyword and meaning results together: the best keyword hit first (exact codes and part
    numbers: "E-217"), then the best matches by meaning, then the other keyword hits.

    Chosen over reciprocal-rank fusion by a test on the sample bundles: when a question shares no
    words with the document, keyword search returns noise that fusion weighs as much as the right
    match by meaning (22 reworded questions, correct section in the top 3: fusion 15, union 20,
    keyword alone 10)."""
    out: list[Hit] = []
    seen: set[tuple[int, str]] = set()
    for h in [*keyword[:keyword_first], *meaning, *keyword[keyword_first:]]:
        key = (h.doc_index, h.node_id)
        if key not in seen:
            seen.add(key)
            out.append(h)
        if len(out) == limit:
            break
    return out


def fuse(question: str, index, keyword: list[Hit], meaning: list[Hit], limit: int = 8) -> list[Hit]:
    """Keyword and meaning results together. Meaning first, unless the question names a code that
    few sections have ("E-217"): the keyword hit comes first then. Measured on 24 reworded questions
    about a plant's manuals: the right section first for keywords alone 16, keyword hit first 16,
    meaning first 23."""
    return union(keyword, meaning, limit=limit, keyword_first=1 if index.names_code(question) else 0)
