"""Summarise retrieval and end-to-end results into results/summary.md (and print it).

    python eval/rag_eval/report.py"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import RESULTS, facts_found, is_refusal, load_sample_questions, polarity_ok  # noqa: E402


def rescore(rows: list[dict]) -> list[dict]:
    """Rule scores from the saved answers with the current rules (they improve between runs)."""
    _, questions = load_sample_questions()
    polarity = {q.id: q.polarity for q in questions}
    for r in rows:
        if r.get("error"):
            continue
        if r["answerable"] and r.get("facts"):
            r["correct"] = facts_found(r["answer"], r["facts"]) and polarity_ok(r["answer"], polarity.get(r["id"], ""))
        elif r["answerable"] and r.get("figures"):  # "show me the chart": right if the chart is shown
            r["correct"] = r.get("figure_hit")
        elif not r["answerable"]:
            refused = is_refusal(r["answer"], r["found"])
            r["correct"], r["hallucinated"] = refused, not refused
    return rows


def pct(n: float) -> str:
    return f"{100 * n:.0f}%"


def rate(rows, key) -> str:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return f"{pct(sum(map(bool, vals)) / len(vals))} ({sum(map(bool, vals))}/{len(vals)})" if vals else "-"


def retrieval_section(lines: list[str]) -> None:
    files = sorted(RESULTS.glob("retrieval-*.json"))
    if not files:
        return
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    lines.append(f"## Retrieval (search only, no language model)\n\nEmbedding model: {data.get('embedding_model')}. "
                 f"File: {files[-1].name}\n")
    for name, r in data["sets"].items():
        extra = f", {r['documents']} documents" if "documents" in r else ""
        lines.append(f"### {name}: {r['queries']} queries{extra}; index built in {r['index_build_s']} s"
                     + (f"; {r['meaning_query_ms']} ms per meaning query" if r.get("meaning_query_ms") else "") + "\n")
        lines.append("| Method | Hit@1 | Hit@3 | Hit@5 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for method, m in r["methods"].items():
            if "[" in method:
                continue
            lines.append(f"| {method} | {m['hit@1']:.2f} | {m['hit@3']:.2f} | {m['hit@5']:.2f} | {m['hit@10']:.2f} | "
                         f"{m['recall@10']:.2f} | {m['mrr@10']:.2f} | {m['ndcg@10']:.2f} |")
        typed = {k: v for k, v in r["methods"].items() if "[" in k}
        if typed:
            lines.append("\nHit@3 by question type:\n")
            types = sorted({k.split("[")[1].rstrip("]") for k in typed})
            methods = sorted({k.split(" [")[0] for k in typed}, key=lambda m: ["keyword", "meaning", "union (app)"].index(m) if m in ["keyword", "meaning", "union (app)"] else 9)
            lines.append("| Type | " + " | ".join(methods) + " |")
            lines.append("|---|" + "---|" * len(methods))
            for t in types:
                lines.append(f"| {t} | " + " | ".join(f"{typed.get(f'{m} [{t}]', {}).get('hit@3', 0):.2f}" for m in methods) + " |")
        lines.append("")


def e2e_section(lines: list[str]) -> None:
    files = sorted(RESULTS.glob("e2e-*.json"))
    if not files:
        return
    lines.append("## End to end (through the installed engine)\n")
    lines.append("| Run | Model | Questions | Correct (rules) | Read right section | Cited right section | "
                 "Faithful (judge) | Correct (judge) | Not-in-docs refused | Hallucinated | Median s | p90 s |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    details = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or "rows" not in d:  # not an end-to-end result (e.g. a file added by hand)
            continue
        rows, meta = rescore(d["rows"]), d["meta"]
        label = meta["label"]
        judge_file = RESULTS / f"judge-{label}.json"
        judged = json.loads(judge_file.read_text(encoding="utf-8")) if judge_file.exists() else {}
        ans = [r for r in rows if r["answerable"]]
        neg = [r for r in rows if not r["answerable"]]
        jv = [judged[r["id"]] for r in ans if r["id"] in judged]
        faithful = f"{pct(sum(v['faithful'] == 'yes' for v in jv) / len(jv))} of {len(jv)}" if jv else "-"
        jcorrect = f"{pct(sum(v['correct'] == 'yes' for v in jv) / len(jv))} of {len(jv)}" if jv else "-"
        secs = [r["secs"] for r in rows if not r.get("error")]
        p90 = sorted(secs)[int(0.9 * (len(secs) - 1))] if secs else 0
        model = (meta.get("chat_model") if meta["mode"] == "chat" else meta.get("model")) or meta.get("model")
        lines.append(f"| {label} | {model} | {len(rows)} | {rate(ans, 'correct')} | {rate(ans, 'read_hit')} | "
                     f"{rate(ans, 'cite_hit')} | {faithful} | {jcorrect} | {rate(neg, 'correct')} | "
                     f"{sum(r['hallucinated'] for r in neg)} | {statistics.median(secs) if secs else 0:.0f} | {p90:.0f} |")
        types = sorted({r["type"] for r in ans})
        if any(r.get("correct") is not None for r in ans):
            details.append(f"\n**{label}**, correct by question type: " + ", ".join(
                f"{t} {rate([r for r in ans if r['type'] == t], 'correct')}" for t in types))
        pictured = [r for r in ans if r.get("figures")]
        if pictured:
            details.append(f"\n**{label}**, the right picture shown with the answer: {rate(pictured, 'figure_hit')}")
        errors = [r for r in rows if r.get("error")]
        if errors:
            details.append(f"{label}: {len(errors)} errors, e.g. {errors[0]['error'][:120]}")
    lines += details
    lines.append("")


def same_questions(lines: list[str], a: str, b: str) -> None:
    """Two runs compared on the questions both answered (a partial run against a full one)."""
    fa, fb = RESULTS / f"e2e-{a}.json", RESULTS / f"e2e-{b}.json"
    if not (fa.exists() and fb.exists()):
        return
    ra = {r["id"]: r for r in rescore(json.loads(fa.read_text(encoding="utf-8"))["rows"]) if not r.get("error")}
    rb = {r["id"]: r for r in rescore(json.loads(fb.read_text(encoding="utf-8"))["rows"]) if not r.get("error")}
    ids = sorted(set(ra) & set(rb))
    if not ids:
        return
    lines.append(f"## {a} vs {b} on the same {len(ids)} questions\n")
    lines.append("| Run | Correct (rules) | Correct (judge) | Faithful (judge) | Not-in-docs refused | Median s |")
    lines.append("|---|---|---|---|---|---|")
    for label, rows in ((a, ra), (b, rb)):
        sub = [rows[i] for i in ids]
        judge_file = RESULTS / f"judge-{label}.json"
        judged = json.loads(judge_file.read_text(encoding="utf-8")) if judge_file.exists() else {}
        jv = [judged[r["id"]] for r in sub if r["answerable"] and r["id"] in judged]
        jc = f"{pct(sum(v['correct'] == 'yes' for v in jv) / len(jv))} of {len(jv)}" if jv else "-"
        jf = f"{pct(sum(v['faithful'] == 'yes' for v in jv) / len(jv))} of {len(jv)}" if jv else "-"
        lines.append(f"| {label} | {rate([r for r in sub if r['answerable']], 'correct')} | {jc} | {jf} | "
                     f"{rate([r for r in sub if not r['answerable']], 'correct')} | {statistics.median(r['secs'] for r in sub):.0f} |")
    lines.append("")


def main() -> None:
    lines = ["# Lattice RAG eval results\n"]
    retrieval_section(lines)
    e2e_section(lines)
    same_questions(lines, "4b-manuals", "08b-manuals")
    text = "\n".join(lines)
    (RESULTS / "summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
