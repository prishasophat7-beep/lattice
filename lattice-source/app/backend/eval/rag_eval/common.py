"""Shared helpers for the Lattice RAG eval: datasets, gold labels and metrics."""
from __future__ import annotations

import json
import math
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
OASIS = HERE.parents[3]  # the repository root, where the sample bundles are


@dataclass
class Question:
    id: str
    q: str
    kb: str = ""
    type: str = ""
    facts: list[list[str]] = field(default_factory=list)
    gold: list[str] = field(default_factory=list)  # "document::section title" (samples) or doc file names
    gold_any: bool = False
    answerable: bool = True
    conv: str | None = None
    reference: str = ""  # a reference answer (RAGBench), for the judge
    polarity: str = ""  # yes/no questions: the answer's first sentence must say "yes" or "no"
    figures: list[str] = field(default_factory=list)  # pictures the answer must show: "document::image file"
    # reasoning set: "answer" (default), "clarify" (a vague first question: the right reply asks back)
    expect: str = "answer"
    inferred: bool = False  # the answer is not stated word for word: worked out from the documents


def load_sample_questions(path: Path | None = None, name: str = "samples") -> tuple[dict[str, str], list[Question]]:
    data = json.loads((path or HERE / f"questions_{name}.json").read_text(encoding="utf-8"))
    qs = [Question(**{k: v for k, v in q.items() if k in Question.__dataclass_fields__}) for q in data["questions"]]
    return data["bundles"], qs


# ── facts and refusals ──

def _has(answer: str, spelling: str) -> bool:
    """Short words ("no", "6") must stand alone: "no" is not in "know", "6" is not in "16"."""
    # a larger model's no-break spaces and hyphens ("30 minutes", "HX‑500") are plain ones
    norm = str.maketrans({" ": " ", " ": " ", " ": " ", "‑": "-", "‐": "-", "–": "-"})
    a, s = answer.translate(norm).casefold(), spelling.translate(norm).casefold()
    if len(s) <= 3 and re.fullmatch(r"[\w.]+", s):
        return re.search(rf"(?<![\w.]){re.escape(s)}(?![\w])", a) is not None
    return s in a


def facts_found(answer: str, facts: list[list[str]]) -> bool:
    return all(any(_has(answer, s) for s in group) for group in facts)


_NEGATIVE = re.compile(r"\b(no|not|never|cannot|can't|don't|doesn't|isn't|aren't|shouldn't|must not|do not|does not)\b", re.IGNORECASE)


def polarity_ok(answer: str, polarity: str) -> bool:
    """The first sentence says no (for "no") or does not (for "yes"). "Based on the documents, **no**,
    parental leave is not stated as paid" fails a "yes" even if "fully paid" appears later."""
    if not polarity:
        return True
    first = re.split(r"(?<=[.!?])\s", answer.replace("*", "").strip(), maxsplit=1)[0]
    negative = bool(_NEGATIVE.search(first))
    return negative if polarity == "no" else not negative


_REFUSAL = re.compile(
    r"(don'?t|do not|doesn'?t|does not) (cover|mention|contain|include|say|state|specify|provide)|"
    r"not (explicitly |clearly |directly )?(covered|mentioned|stated|specified|included|provided|available|found|"
    r"listed|given|named|identified|in the (documents|sources))|"
    r"no (information|mention|details?|data)|(can'?t|cannot|could ?n[o']t|unable to) (find|answer|determine|identify|locate)|"
    r"isn'?t (in|covered|mentioned)|answer this reliably", re.IGNORECASE)


def is_refusal(answer: str, found: bool) -> bool:
    return (not found) or bool(_REFUSAL.search(answer[:400]))


# ── retrieval metrics (binary relevance) ──

def ranking_metrics(ranked: list, gold: set, ks=(1, 3, 5, 10)) -> dict:
    """hit@k, recall@k, MRR@10 and nDCG@10 for one query. ranked: items best first."""
    out = {}
    for k in ks:
        top = ranked[:k]
        out[f"hit@{k}"] = float(any(r in gold for r in top))
        out[f"recall@{k}"] = len(gold & set(top)) / len(gold) if gold else 0.0
    rr = next((1 / (i + 1) for i, r in enumerate(ranked[:10]) if r in gold), 0.0)
    out["mrr@10"] = rr
    dcg = sum(1 / math.log2(i + 2) for i, r in enumerate(ranked[:10]) if r in gold)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    out["ndcg@10"] = dcg / ideal if ideal else 0.0
    return out


def mean_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {}
    keys = rows[0].keys()
    return {k: round(sum(r[k] for r in rows) / len(rows), 3) for k in keys}


# ── corpora ──

def import_sample_bundle(zip_name: str, work: Path):
    """The sample bundle as the app installs it: (manifest, docs)."""
    from oasis_engine import bundle
    data_dir = work / zip_name.removesuffix(".zip")
    if not (data_dir / "versions").exists():
        bundle.import_bundle(OASIS / zip_name, data_dir)
    loaded = bundle.load_active(data_dir)
    assert loaded is not None, zip_name
    return loaded


def section_key(doc, nid: str) -> str:
    return f"{doc.name}::{doc.sections[nid].title}"


def gold_keys(docs, refs: list[str]) -> set[tuple[int, str]]:
    """'document::section title' -> {(doc index, node id)}."""
    out = set()
    for ref in refs:
        name, title = ref.split("::", 1)
        hits = [(di, nid) for di, d in enumerate(docs) if d.name == name
                for nid, s in d.sections.items() if s.title.casefold() == title.casefold()]
        if not hits:
            raise ValueError(f"gold section not found: {ref}")
        out.update(hits)
    return out


def write_markdown_corpus(folder: Path, items: list[tuple[str, str, str]]) -> None:
    """items: (file stem, title, text) -> one Markdown file per document."""
    folder.mkdir(parents=True, exist_ok=True)
    for stem, title, text in items:
        safe_title = re.sub(r"\s+", " ", title).strip() or stem
        (folder / f"{stem}.md").write_text(f"# {safe_title}\n\n{text.strip()}\n", encoding="utf-8")


def load_scifact(limit_queries: int | None = None):
    """BEIR SciFact test split: (corpus items, queries [(id, text)], qrels {qid: {doc ids}})."""
    root = DATA / "scifact"
    corpus = [json.loads(line) for line in (root / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if line]
    queries = {q["_id"]: q["text"] for q in map(json.loads, (root / "queries.jsonl").read_text(encoding="utf-8").splitlines())}
    qrels: dict[str, set[str]] = {}
    for line in (root / "qrels" / "test.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        qid, did, score = line.split("\t")
        if int(score) > 0:
            qrels.setdefault(qid, set()).add(did)
    qs = [(qid, queries[qid]) for qid in qrels]
    if limit_queries:
        qs = qs[:limit_queries]
    items = [(f"d{c['_id']}", c.get("title") or c["text"][:80], c["text"]) for c in corpus]
    return items, qs, qrels


def load_beir_json(name: str):
    """A BEIR set downloaded as JSON rows (fetch_datasets.py): data/<name>/corpus.json, queries.json,
    qrels_test.json. Returns (items, queries [(id, text)], qrels {qid: {doc ids}}) like load_scifact."""
    root = DATA / name
    corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    queries = {str(q["_id"]): q["text"] for q in json.loads((root / "queries.json").read_text(encoding="utf-8"))}
    qrels: dict[str, set[str]] = {}
    for r in json.loads((root / "qrels_test.json").read_text(encoding="utf-8")):
        if int(r.get("score", 1)) > 0:
            qrels.setdefault(str(r["query-id"]), set()).add(str(r["corpus-id"]))
    qs = [(qid, queries[qid]) for qid in qrels if qid in queries]
    items = [(f"d{c['_id']}", c.get("title") or c["text"][:80], c["text"]) for c in corpus]
    return items, qs, qrels


def load_ragbench(name: str):
    """A RAGBench split (EManual, TechQA): see load_emanual."""
    return _ragbench(DATA / f"{name}_test.json", name)


def load_multihop(limit: int | None = None):
    """MultiHop-RAG: news articles as documents; each question's gold documents are the articles
    its evidence comes from (usually 2-4). Returns (items, questions)."""
    root = DATA / "multihop"
    corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    stem_of = {}
    items = []
    for i, art in enumerate(corpus):
        stem = f"a{i:04d}"
        stem_of[(art.get("title") or "").strip()] = stem
        items.append((stem, art.get("title") or f"article {i}", art.get("body") or ""))
    questions = []
    for i, q in enumerate(json.loads((root / "MultiHopRAG.json").read_text(encoding="utf-8"))):
        gold = sorted({stem_of[e["title"].strip()] for e in q.get("evidence_list", []) if e.get("title", "").strip() in stem_of})
        if not gold:  # null queries (answer "Insufficient information") have no evidence
            continue
        questions.append(Question(id=f"m{i:04d}", q=q["query"], kb="multihop", type=q.get("question_type", "multihop"),
                                  gold=gold, gold_any=False, reference=str(q.get("answer", ""))))
    return items, questions[:limit] if limit else questions


def load_emanual():
    """RAGBench EManual test split: unique passages as documents; each question's gold passages are
    the ones holding a sentence the annotators marked relevant. Returns (items, questions)."""
    return _ragbench(DATA / "emanual_test.json", "emanual")


def _ragbench(path: Path, name: str):
    rows = json.loads(path.read_text(encoding="utf-8"))
    passages: dict[str, str] = {}
    questions: list[Question] = []
    for row in rows:
        stems = []
        for text in row["documents"]:
            key = re.sub(r"\s+", " ", text).strip()
            stem = passages.setdefault(key, f"p{len(passages):04d}")
            stems.append(stem)
        relevant = {k for k in (row.get("all_relevant_sentence_keys") or [])}
        gold = sorted({stems[int(k[0])] for k in relevant if k[:1].isdigit() and int(k[0]) < len(stems)})
        if not gold:
            continue
        questions.append(Question(id=f"{name[0]}{len(questions):03d}", q=row["question"].strip(), kb=name,
                                  type=name, gold=gold, gold_any=True, reference=str(row.get("response") or "")))
    items = []
    for text, stem in passages.items():
        first = re.split(r"(?<=[.!?:])\s", text, maxsplit=1)[0]
        items.append((stem, first[:90], text))
    return items, questions


def tmpdir(name: str) -> Path:
    p = Path(tempfile.gettempdir()) / "lattice-rag-eval" / name
    p.mkdir(parents=True, exist_ok=True)
    return p
