"""LLM judge for end-to-end results: faithfulness (is every claim supported by the sources the
answer cites?) and correctness (does it answer the question, given the expected facts or the
reference answer?). Uses an OpenRouter model; the key is read from oasis/.env and never printed.

    python eval/rag_eval/judge.py e2e-08b-manuals [e2e-...] --max-calls 30

Judges several answers per request to fit the free tier's daily limit. Resumable: judgments are
saved in results/judge-<label>.json and not asked again."""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from common import OASIS, RESULTS, import_sample_bundle, is_refusal, load_sample_questions, tmpdir  # noqa: E402

MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
BATCH = 10
SOURCE_CHARS = 1500
PROMPT = """You grade answers from a document assistant. For each item you get the question, the
answer, the source sections the answer was based on, and either the expected facts or a reference
answer.

faithful: "yes" if every factual claim in the answer is supported by the given sources, "partly" if
some claims are unsupported, "no" if the main claim is unsupported or contradicts the sources.
Ignore politeness and phrasing; judge facts only.
correct: "yes" if the answer correctly answers the question (it contains the expected facts or
agrees with the reference), "partly" if incomplete, "no" if wrong or missing.

Reply with only a JSON list, one object per item, in order:
[{"id": "...", "faithful": "yes|partly|no", "correct": "yes|partly|no", "note": "<10 words"}]

ITEMS:
"""


def api_key(env_name: str) -> str:
    """From the environment variable, else from oasis/.env (never printed)."""
    import os
    if os.environ.get(env_name):
        return os.environ[env_name]
    env_file = OASIS / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(env_name):
                return line.split("=", 1)[1].strip().strip('"')
    return ""  # a server without a key


def section_texts(set_name: str = "samples") -> dict[str, str]:
    """'document::section title' -> text, for the sample bundles or one public dataset's documents."""
    out = {}
    if set_name == "samples":
        bundles, _ = load_sample_questions()
        for zip_name in bundles.values():
            _, docs = import_sample_bundle(zip_name, tmpdir("bundles"))
            for d in docs:
                for nid, s in d.sections.items():
                    out[f"{d.name}::{s.title}".casefold()] = d.section_text(nid, max_chars=SOURCE_CHARS)
        return out
    for f in tmpdir(f"{set_name}-bundle-content").glob("*.md"):
        text = f.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip()
        out[f"{f.name}::{title}".casefold()] = text[:SOURCE_CHARS]
        out[f"{f.stem}"] = text[:SOURCE_CHARS]
    return out


def sources_for(row: dict, texts: dict[str, str]) -> str:
    keys = row["cited"] or row["read"] or row["gold"]
    parts = []
    for k in keys[:3]:
        t = texts.get(k.casefold()) or texts.get(k.split("::")[0].removesuffix(".md")) or ""
        if t:
            parts.append(f"[{k}]\n{t}")
    return "\n\n".join(parts) or "(none)"


def ask_judge(key: str, items: list[dict], base_url: str = "https://openrouter.ai/api/v1", model: str = MODEL,
              no_thinking: bool = False) -> list[dict]:
    body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": PROMPT + json.dumps(items, ensure_ascii=False, indent=1)}]}
    if no_thinking:  # Qwen 3.x on vLLM / llama.cpp: answer straight away
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(), method="POST")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=600) as r:
        text = json.loads(r.read())["choices"][0]["message"]["content"] or ""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    return json.loads(m.group(0)) if m else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="+", help="result files without .json, e.g. e2e-08b-manuals")
    ap.add_argument("--max-calls", type=int, default=30)
    ap.add_argument("--base-url", default="https://openrouter.ai/api/v1", help="any OpenAI-compatible endpoint")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--api-key-env", default="OPENROUTER_API_KEY", help="environment variable holding the key")
    ap.add_argument("--no-thinking", action="store_true", help="ask a Qwen 3.x server not to think first")
    args = ap.parse_args()
    key, calls = api_key(args.api_key_env), 0
    texts_by_set: dict[str, dict[str, str]] = {}
    for label in args.labels:
        data = json.loads((RESULTS / f"{label}.json").read_text(encoding="utf-8"))
        rows, set_name = data["rows"], data.get("meta", {}).get("set", "samples")
        texts = texts_by_set.setdefault(set_name, section_texts(set_name))
        out = RESULTS / f"judge-{label.removeprefix('e2e-')}.json"
        judged = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        todo = [r for r in rows if r["answerable"] and r["answer"] and not r.get("error")
                and not is_refusal(r["answer"], r["found"]) and r["id"] not in judged]
        print(f"== {label}: {len(todo)} answers to judge", flush=True)
        for i in range(0, len(todo), BATCH):
            if calls >= args.max_calls:
                print("call limit reached; rerun later to continue", flush=True)
                return
            batch = todo[i:i + BATCH]
            items = [{"id": r["id"], "question": r["q"], "answer": r["answer"][:2500],
                      "expected": (" AND ".join(" or ".join(g) for g in r["facts"]) if r["facts"] else r["reference"][:800]),
                      "sources": sources_for(r, texts)} for r in batch]
            for attempt in range(3):
                try:
                    calls += 1
                    verdicts = ask_judge(key, items, args.base_url, args.model, args.no_thinking)
                    break
                except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError, KeyError, IndexError) as e:
                    print(f"judge call failed ({type(e).__name__}); retrying", flush=True)
                    verdicts = []
                    time.sleep(20)
            for v in verdicts:
                if isinstance(v, dict) and v.get("id") in {r["id"] for r in batch}:
                    judged[v["id"]] = {k: v.get(k) for k in ("faithful", "correct", "note")}
            out.write_text(json.dumps(judged, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  judged {len(judged)} (calls {calls})", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
