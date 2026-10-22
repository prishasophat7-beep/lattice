"""Download the public eval datasets into data/ (Hugging Face dataset viewer API as JSON, so no
Parquet reader is needed). Already downloaded files are kept.

    python eval/rag_eval/fetch_datasets.py [nfcorpus techqa multihop fiqa scifact emanual]"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
API = "https://datasets-server.huggingface.co/rows?dataset={ds}&config={cfg}&split={split}&offset={off}&length=100"


def get_json(url: str, tries: int = 10):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - rate limits and dropped connections: wait and retry
            if attempt == tries - 1:
                raise
            print(f"  retry after {type(e).__name__}", flush=True)
            time.sleep(min(120, 15 * (attempt + 1)))  # rate limited (429): back off


def rows(ds: str, cfg: str, split: str, out: Path) -> None:
    if out.exists():
        print(f"have {out.name}")
        return
    got, off, total = [], 0, None
    while total is None or off < total:
        d = get_json(API.format(ds=ds, cfg=cfg, split=split, off=off))
        batch = [r["row"] for r in d["rows"]]
        total = d.get("num_rows_total", 0)
        got += batch
        off += len(batch)
        if not batch:
            break
        if off % 2000 < 100:
            print(f"  {out.name}: {off}/{total}", flush=True)
        time.sleep(1.0)  # the viewer API rate-limits fast clients
    out.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out.name}: {len(got)} rows", flush=True)


def file(url: str, out: Path) -> None:
    if out.exists():
        print(f"have {out.name}")
        return
    with urllib.request.urlopen(url, timeout=300) as r:
        out.write_bytes(r.read())
    print(f"wrote {out.name}: {out.stat().st_size} bytes", flush=True)


def main() -> None:
    wanted = set(sys.argv[1:]) or {"nfcorpus", "techqa", "multihop", "fiqa"}
    DATA.mkdir(exist_ok=True)
    if "nfcorpus" in wanted:
        d = DATA / "nfcorpus"
        d.mkdir(exist_ok=True)
        rows("BeIR/nfcorpus", "corpus", "corpus", d / "corpus.json")
        rows("BeIR/nfcorpus", "queries", "queries", d / "queries.json")
        rows("BeIR/nfcorpus-qrels", "default", "test", d / "qrels_test.json")
    if "techqa" in wanted:
        rows("galileo-ai/ragbench", "techqa", "test", DATA / "techqa_test.json")
    if "multihop" in wanted:
        d = DATA / "multihop"
        d.mkdir(exist_ok=True)
        base = "https://huggingface.co/datasets/yixuantt/MultiHopRAG/resolve/main/"
        file(base + "corpus.json", d / "corpus.json")
        file(base + "MultiHopRAG.json", d / "MultiHopRAG.json")
    if "fiqa" in wanted:
        d = DATA / "fiqa"
        d.mkdir(exist_ok=True)
        rows("BeIR/fiqa", "corpus", "corpus", d / "corpus.json")
        rows("BeIR/fiqa", "queries", "queries", d / "queries.json")
        rows("BeIR/fiqa-qrels", "default", "test", d / "qrels_test.json")
    if "emanual" in wanted:
        rows("galileo-ai/ragbench", "emanual", "test", DATA / "emanual_test.json")
    print("DONE")


if __name__ == "__main__":
    main()
