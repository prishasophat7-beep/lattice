"""End-to-end eval through a running Lattice engine (the same HTTP API the desktop app uses).

    python eval/rag_eval/e2e_eval.py --port 8870 --token TOKEN --mode manuals --set samples --label 08b-manuals

For each bundle: import it, wait for search by meaning when it is set up, then ask every question
(conversations in order, with one conversation id). Rule-based scores per answer:
  correct      answerable: every expected fact is in the answer; not-in-docs: it says so
  read_hit     a gold section was among the sections read (Manuals) or cited (Chat)
  cite_hit     a gold section is among the cited sources
  hallucinated not-in-docs question answered as if found
Writes results/e2e-<label>.json (resumable: finished questions are skipped on a rerun)."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from common import (OASIS, RESULTS, facts_found, is_refusal, polarity_ok, load_emanual, load_multihop,  # noqa: E402
                    load_ragbench, load_sample_questions,
                    tmpdir, write_markdown_corpus)


class Engine:
    def __init__(self, port: int, token: str):
        self.base, self.token = f"http://127.0.0.1:{port}", token

    def call(self, method: str, path: str, body=None, timeout=600):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")

    def ask(self, question: str, mode: str, conv: str) -> tuple[dict | None, list, float, str | None]:
        body = {"question": question, "mode": mode, "conversation_id": conv}
        req = urllib.request.Request(self.base + "/api/ask/stream", data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        t, result, tools, error = time.time(), None, [], None
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    ev = json.loads(line[6:])
                    if ev.get("stage") == "done":
                        result = ev["result"]
                    elif ev.get("stage") == "error":
                        error = f"{ev.get('status')}: {ev.get('message')}"
                    elif ev.get("stage") == "tool":
                        tools.append(f"{ev.get('tool')}({ev.get('detail', '')})")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            error = repr(e)
        return result, tools, round(time.time() - t, 1), error

    def use_bundle(self, zip_path: Path, want_semantic: bool) -> str:
        r = self.call("POST", "/api/kb/import", {"path": str(zip_path)})
        for _ in range(300):  # search by meaning indexes in the background
            h = self.call("GET", "/api/health")
            if not want_semantic or h.get("semantic") == "ready" or str(h.get("semantic", "")).startswith("error"):
                return f"{r.get('imported')} semantic={h.get('semantic')}"
            time.sleep(2)
        return f"{r.get('imported')} semantic=timeout"


CORPUS_SETS = {"emanual": load_emanual, "techqa": lambda: load_ragbench("techqa"), "multihop": load_multihop}


def corpus_bundle(name: str) -> tuple[Path, list]:
    """A public dataset's documents as a Lattice Markdown bundle, and its questions."""
    from oasis_engine import bundle
    items, questions = CORPUS_SETS[name]()
    content = tmpdir(f"{name}-bundle-content")
    if not any(content.glob("*.md")):
        write_markdown_corpus(content, items)
    out = tmpdir("bundles") / f"{name}-bundle.zip"
    if not out.exists():
        bundle.build_bundle(content, "markdown", f"public-{name}-test", out)
    return out, questions


def emanual_bundle() -> tuple[Path, list]:
    return corpus_bundle("emanual")


def spread(items: list, n: int) -> list:
    """n items evenly spaced through the list (a sample across question types and sources)."""
    if not n or n >= len(items):
        return items
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)] if n > 1 else items[:1]


def cited_keys(result: dict) -> list[str]:
    return [f"{c['doc_name']}::{c['title']}" for c in result.get("citations") or []]


def read_keys(result: dict) -> list[str]:
    return [f"{c['doc_name']}::{c['title']}" for c in result.get("considered") or []]


def shown_keys(result: dict) -> list[str]:
    """The pictures shown with the answer, as "document::image file" (PDF pages: "document::page N")."""
    cites = result.get("citations") or []
    return [f"{cites[f['citation']]['doc_name']}::{f.get('image') or 'page ' + str(f.get('page'))}"
            for f in result.get("figures") or [] if 0 <= f.get("citation", -1) < len(cites)]


def gold_match(keys: list[str], gold: list[str], set_name: str) -> bool:
    if set_name not in ("samples", "reasoning", "complex", "complexweb", "mixweb", "large", "remotem", "remotec", "remotec2"):  # gold are document files: p0001 -> p0001.md
        names = {k.split("::", 1)[0].removesuffix(".md") for k in keys}
        return bool(names & set(gold))
    low = {k.casefold() for k in keys}
    return any(g.casefold() in low for g in gold)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8870)
    ap.add_argument("--token", default="")
    ap.add_argument("--mode", choices=["manuals", "chat"], default="manuals")
    ap.add_argument("--set", choices=["samples", "reasoning", "complex", "complexweb", "mixweb", "large", "remotem", "remotec", "remotec2", *CORPUS_SETS], default="samples")
    ap.add_argument("--limit", type=int, default=0, help="public sets: ask an even sample of this many questions")
    ap.add_argument("--label", required=True)
    ap.add_argument("--semantic", action="store_true", help="wait for search by meaning after each import")
    ap.add_argument("--only", default="", help="comma-separated question ids")
    args = ap.parse_args()
    eng = Engine(args.port, args.token)
    health = eng.call("GET", "/api/health")
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"e2e-{args.label}.json"
    done = {r["id"]: r for r in json.loads(out.read_text(encoding="utf-8"))["rows"]} if out.exists() else {}

    if args.set in ("samples", "reasoning", "complex", "complexweb", "mixweb", "large", "remotem", "remotec", "remotec2"):
        bundles, questions = load_sample_questions(name=args.set)
        plan = [(OASIS / zip_name, [q for q in questions if q.kb == kb]) for kb, zip_name in bundles.items()]
    else:
        zip_path, questions = corpus_bundle(args.set)
        plan = [(zip_path, spread(questions, args.limit))]
    only = set(filter(None, args.only.split(",")))
    meta = {"label": args.label, "mode": args.mode, "set": args.set, "model": health.get("model"),
            "chat_model": health.get("chat_model"), "semantic_model": health.get("semantic_model"),
            "engine_version": health.get("engine_version"), "started": time.strftime("%Y-%m-%d %H:%M")}
    rows = list(done.values())
    stamp = time.strftime("%H%M%S")
    for zip_path, qs in plan:
        todo = [q for q in qs if q.id not in done and (not only or q.id in only)]
        if not todo:
            continue
        print(f"== {zip_path.name}: {eng.use_bundle(zip_path, args.semantic)} ({len(todo)} questions)", flush=True)
        for q in todo:
            conv = f"{args.label}-{q.conv or q.id}-{stamp}".replace(".", "-")[:64]
            result, tools, secs, error = eng.ask(q.q, args.mode, conv)
            answer = (result or {}).get("answer", "") if result else ""
            found = bool((result or {}).get("found"))
            row = {"id": q.id, "kb": q.kb, "type": q.type, "q": q.q, "answerable": q.answerable,
                   "secs": secs, "error": error, "answer": answer, "found": found,
                   "general": bool((result or {}).get("general")), "smalltalk": bool((result or {}).get("smalltalk")),
                   "read": read_keys(result or {}), "cited": cited_keys(result or {}), "tools": tools,
                   "unverified": (result or {}).get("unverified_numbers") or [], "gold": q.gold,
                   "facts": q.facts, "reference": q.reference,
                   "shown": shown_keys(result or {}), "figures": q.figures}
            row["figure_hit"] = bool(set(row["shown"]) & set(q.figures)) if q.figures else None
            row["clarified"] = bool((result or {}).get("clarify"))
            row["worked_out"] = bool((result or {}).get("worked_out"))
            row["expect"], row["inferred"] = q.expect, q.inferred
            if error or result is None:
                row.update(correct=False, read_hit=False, cite_hit=False, hallucinated=False)
            elif q.expect == "clarify":  # a vague first question: asking back is the right reply
                row.update(correct=row["clarified"], read_hit=None, cite_hit=None, hallucinated=False)
            elif q.answerable:
                row["correct"] = (facts_found(answer, q.facts) and polarity_ok(answer, q.polarity)) if q.facts \
                    else row["figure_hit"]  # "show me the chart": right if the chart is shown; EManual: judged later
                row["read_hit"] = gold_match(row["read"] + row["cited"], q.gold, args.set)
                row["cite_hit"] = gold_match(row["cited"], q.gold, args.set)
                row["hallucinated"] = False
            else:
                refused = is_refusal(answer, found)
                row.update(correct=refused, read_hit=None, cite_hit=None, hallucinated=not refused)
            rows.append(row)
            done[q.id] = row
            out.write_text(json.dumps({"meta": meta, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
            mark = "OK " if row["correct"] else ("?  " if row["correct"] is None else "-- ")
            print(f"{mark}{q.id:5} {secs:6.1f}s  {q.q[:60]!r} -> {answer[:90]!r}", flush=True)
    meta["finished"] = time.strftime("%Y-%m-%d %H:%M")
    out.write_text(json.dumps({"meta": meta, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
