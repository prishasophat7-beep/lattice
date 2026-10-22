"""Retrieval-only eval of Lattice's search: keyword (BM25), meaning (embedding model) and the union
the app uses. No language model is involved.

    python eval/rag_eval/retrieval_eval.py --embed-url http://127.0.0.1:8096/v1 --embed-key KEY \
        --embed-model embeddinggemma-300M-Q8_0 [--sets samples,emanual,scifact]

Without --embed-url only keyword search is scored. Metrics per method: hit@1/3/5/10, recall@5/10,
MRR@10, nDCG@10. Samples are scored per section; EManual and SciFact per document (passage).
Writes results/retrieval-<time>.json and prints a table."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from common import (RESULTS, load_beir_json, load_emanual, load_multihop, load_ragbench,  # noqa: E402
                    load_sample_questions, load_scifact, gold_keys,
                    import_sample_bundle, mean_metrics, ranking_metrics, tmpdir, write_markdown_corpus)
from oasis_engine.embeddings import EmbeddingClient, SemanticIndex, union  # noqa: E402
from oasis_engine.loaders import load_markdown  # noqa: E402
from oasis_engine.search import SectionIndex  # noqa: E402

DEPTH = 100  # results kept per method


class Searcher:
    def __init__(self, docs, embed: EmbeddingClient | None, cache: Path):
        self.docs = docs
        self.keyword = SectionIndex(docs)
        self.meaning = SemanticIndex(docs, embed, cache) if embed else None

    async def ready(self) -> float:
        if self.meaning is None:
            return 0.0
        t = time.perf_counter()
        await self.meaning.build()
        if not self.meaning.ready:
            raise RuntimeError(f"meaning index not built: {self.meaning.status}")
        return time.perf_counter() - t

    async def rank(self, query: str) -> dict[str, list[tuple[int, str]]]:
        kw = self.keyword.search(query)[:DEPTH]
        out = {"keyword": [(h.doc_index, h.node_id) for h in kw]}
        if self.meaning is not None:
            t = time.perf_counter()
            mn = await self.meaning.search(query, None, DEPTH)
            out["_meaning_s"] = time.perf_counter() - t  # type: ignore[assignment]
            out["meaning"] = [(h.doc_index, h.node_id) for h in mn]
            out["union (app)"] = [(h.doc_index, h.node_id) for h in union(kw, mn, limit=DEPTH)]
        return out


def by_doc(ranked: list[tuple[int, str]]) -> list[int]:
    seen, out = set(), []
    for di, _ in ranked:
        if di not in seen:
            seen.add(di)
            out.append(di)
    return out


async def eval_samples(embed, per_query: list) -> dict:
    bundles, questions = load_sample_questions()
    results: dict[str, list] = {}
    build_s = 0.0
    for kb, zip_name in bundles.items():
        _, docs = import_sample_bundle(zip_name, tmpdir("bundles"))
        s = Searcher(docs, embed, tmpdir("emb") / kb)
        build_s += await s.ready()
        for q in questions:
            if q.kb != kb or not q.answerable or q.type == "follow-up":
                continue  # follow-ups name no topic on their own; not-in-docs have no gold
            gold = gold_keys(docs, q.gold)
            ranked = await s.rank(q.q)
            for method, items in ranked.items():
                if method.startswith("_"):
                    continue
                m = ranking_metrics(items, gold)
                results.setdefault(method, []).append(m)
                per_query.append({"set": "samples", "id": q.id, "type": q.type, "method": method,
                                  **m, "top3": [f"{docs[d].name}::{docs[d].sections[n].title}" for d, n in items[:3]]})
            for method, items in ranked.items():
                if method.startswith("_"):
                    continue
                results.setdefault(f"{method} [{q.type}]", []).append(ranking_metrics(items, gold))
    return {"queries": len(results.get("keyword", [])), "index_build_s": round(build_s, 1),
            "methods": {k: mean_metrics(v) for k, v in results.items()}}


async def eval_docs(name: str, items, queries, embed, per_query: list) -> dict:
    """A flat corpus (one document per item); queries: [(id, text, gold doc stems)]."""
    folder = tmpdir(f"corpus-{name}")
    if not any(folder.glob("*.md")):
        write_markdown_corpus(folder, items)
    docs = load_markdown(folder)
    stem_of = [Path(d.name).stem for d in docs]
    s = Searcher(docs, embed, tmpdir("emb") / name)
    t = time.perf_counter()
    build_s = await s.ready()
    results: dict[str, list] = {}
    meaning_s = []
    for qid, text, gold_stems in queries:
        gold = {i for i, st in enumerate(stem_of) if st in gold_stems}
        if not gold:
            continue
        ranked = await s.rank(text)
        if "_meaning_s" in ranked:
            meaning_s.append(ranked.pop("_meaning_s"))  # type: ignore[arg-type]
        for method, hits in ranked.items():
            m = ranking_metrics(by_doc(hits), gold)
            results.setdefault(method, []).append(m)
            per_query.append({"set": name, "id": qid, "method": method, **m})
    return {"documents": len(docs), "queries": len(results.get("keyword", [])),
            "index_build_s": round(build_s, 1),
            "meaning_query_ms": round(1000 * sum(meaning_s) / len(meaning_s)) if meaning_s else None,
            "methods": {k: mean_metrics(v) for k, v in results.items()},
            "total_s": round(time.perf_counter() - t, 1)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed-url")
    ap.add_argument("--embed-key", default="")
    ap.add_argument("--embed-model", default="embeddinggemma-300M-Q8_0")
    ap.add_argument("--label", default="", help="results file name: retrieval-<label>.json")
    ap.add_argument("--sets", default="samples,emanual,scifact",
                    help="any of samples,emanual,techqa,multihop,scifact,nfcorpus,fiqa")
    args = ap.parse_args()
    embed = EmbeddingClient(args.embed_url, args.embed_model, args.embed_key) if args.embed_url else None
    per_query: list = []
    report = {"embedding_model": args.embed_model if embed else None, "sets": {}}
    sets = args.sets.split(",")
    if "samples" in sets:
        report["sets"]["samples"] = await eval_samples(embed, per_query)
        print("samples done", flush=True)
    if "emanual" in sets:
        items, qs = load_emanual()
        report["sets"]["emanual"] = await eval_docs("emanual", items, [(q.id, q.q, set(q.gold)) for q in qs], embed, per_query)
        print("emanual done", flush=True)
    for name in ("techqa",):
        if name in sets:
            items, qs = load_ragbench(name)
            report["sets"][name] = await eval_docs(name, items, [(q.id, q.q, set(q.gold)) for q in qs], embed, per_query)
            print(f"{name} done", flush=True)
    if "multihop" in sets:
        items, qs = load_multihop()
        report["sets"]["multihop"] = await eval_docs("multihop", items, [(q.id, q.q, set(q.gold)) for q in qs], embed, per_query)
        print("multihop done", flush=True)
    for name in ("nfcorpus", "fiqa"):
        if name in sets:
            items, qs, qrels = load_beir_json(name)
            report["sets"][name] = await eval_docs(name, items, [(qid, text, {f"d{d}" for d in qrels[qid]}) for qid, text in qs],
                                                   embed, per_query)
            print(f"{name} done", flush=True)
    if "scifact" in sets:
        items, qs, qrels = load_scifact()
        report["sets"]["scifact"] = await eval_docs("scifact", items, [(qid, text, {f"d{d}" for d in qrels[qid]}) for qid, text in qs],
                                                    embed, per_query)
        print("scifact done", flush=True)
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"retrieval-{args.label or time.strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps({**report, "per_query": per_query}, indent=1, ensure_ascii=False), encoding="utf-8")
    for name, r in report["sets"].items():
        print(f"\n== {name}: {r.get('queries')} queries" + (f", {r['documents']} documents" if "documents" in r else "")
              + f", index built in {r['index_build_s']}s")
        print(f"{'method':38} hit@1  hit@3  hit@5  hit@10  rec@10  MRR@10  nDCG@10")
        for method, m in r["methods"].items():
            print(f"{method:38} {m['hit@1']:.3f}  {m['hit@3']:.3f}  {m['hit@5']:.3f}  {m['hit@10']:.3f}   "
                  f"{m['recall@10']:.3f}   {m['mrr@10']:.3f}   {m['ndcg@10']:.3f}")
    print(f"\nwritten {out}")


if __name__ == "__main__":
    asyncio.run(main())
