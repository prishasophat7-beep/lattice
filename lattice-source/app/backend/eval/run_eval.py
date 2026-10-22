"""Score the engine against questions with known answers.

    python eval/run_eval.py --model nvidia/nemotron-3.5-lightning:free --output-mode prompted

Talks to OpenRouter (key read from oasis/.env, never printed) or, with --base-url,
to a local llama-server. Shares the daily request counter with the prototype so
the free-tier budget is counted once.

Scores:
  correct   every required fact group appears in the answer (unanswerable: found=false)
  located   the sections the model chose cover a page that holds the answer
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OASIS = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

from oasis_engine import bundle  # noqa: E402
from oasis_engine.llm import BudgetExceeded, LLMConfig, RequestBudget, make_model  # noqa: E402
from oasis_engine.pipeline import Pipeline, ProviderBusy  # noqa: E402


def read_env_key(name: str) -> str:
    env = OASIS / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def score(item: dict, r) -> dict:
    if item["facts"] is None:
        return {"correct": not r.found, "located": None}
    text = norm(r.answer)
    correct = r.found and all(any(norm(alt) in text for alt in group) for group in item["facts"])
    chosen_pages = {p for c in r.considered for p in (c.pages or [])}
    return {"correct": correct, "located": bool(chosen_pages & set(item["pages"]))}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    ap.add_argument("--api-key", default="", help="key for a local llama-server started with --api-key")
    ap.add_argument("--output-mode", default="prompted", choices=["native", "prompted", "tool"])
    ap.add_argument("--kb", default=str(HERE.parent.parent / "sample-data" / "devdata"),
                    help="data dir with an installed, active knowledge base")
    ap.add_argument("--questions", default=str(HERE / "questions_earnings.json"))
    ap.add_argument("--budget", type=int, default=45)
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions")
    args = ap.parse_args()

    loaded = bundle.load_active(Path(args.kb))
    if loaded is None:
        sys.exit(f"No active knowledge base in {args.kb}; import a bundle first")
    manifest, docs = loaded
    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[:args.limit]

    is_openrouter = "openrouter.ai" in args.base_url
    budget = RequestBudget(args.budget, OASIS / "prototype" / ".usage.json") if is_openrouter else None
    cfg = LLMConfig(base_url=args.base_url, model=args.model, output_mode=args.output_mode,
                    api_key=read_env_key("OPENROUTER_API_KEY") if is_openrouter else args.api_key)
    pipe = Pipeline(docs, make_model(cfg, budget), output_mode=args.output_mode, budget=budget)

    print(f"KB {manifest.kb_version}: {[d.name for d in docs]} | model {args.model} ({args.output_mode})")
    if budget:
        print(f"Requests left today: {budget.limit - budget.used_today()}")
    rows, busy = [], 0
    for item in questions:
        try:
            r = await pipe.ask(item["q"])
            busy = 0
        except BudgetExceeded as e:
            print(f"  STOPPED: {e}")
            break
        except ProviderBusy:
            busy += 1
            rows.append({"question": item["q"], "error": "rate-limited"})
            print(f"  BUSY   {item['q'][:70]}")
            if busy >= 2:
                print("  SKIPPING model: rate-limited twice in a row")
                break
            continue
        except Exception as e:  # record and continue
            rows.append({"question": item["q"], "error": f"{type(e).__name__}: {e}"[:300]})
            print(f"  ERROR  {item['q'][:70]}  {rows[-1]['error'][:150]}")
            continue
        s = score(item, r)
        rows.append({**r.model_dump(), **s})
        loc = "" if s["located"] is None else ("  sections:hit" if s["located"] else "  sections:MISS")
        print(f"  {'OK ' if s['correct'] else 'BAD'} {r.seconds.get('total', 0):6.1f}s "
              f"{item['q'][:62]:62}{loc}\n        -> {r.answer[:160]}")

    done = [x for x in rows if "error" not in x]
    answerable = [x for x in done if x["located"] is not None]
    summary = {
        "model": args.model, "output_mode": args.output_mode, "kb_version": manifest.kb_version,
        "asked": len(rows), "answered": len(done), "correct": sum(x["correct"] for x in done),
        "located": sum(bool(x["located"]) for x in answerable), "answerable": len(answerable),
        "avg_seconds": round(sum(x["seconds"].get("total", 0) for x in done) / max(1, len(done)), 1),
        "requests": sum(x["model_requests"] for x in done), "rows": rows,
    }
    out = HERE / "results" / f"{datetime.now():%Y%m%d-%H%M}-{re.sub(r'[^\w.-]+', '_', args.model)}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n=> correct {summary['correct']}/{summary['asked']}, right sections "
          f"{summary['located']}/{summary['answerable']}, avg {summary['avg_seconds']}s, "
          f"{summary['requests']} model requests -> {out.name}")


if __name__ == "__main__":
    asyncio.run(main())
