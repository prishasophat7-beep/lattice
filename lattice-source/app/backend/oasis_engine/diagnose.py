"""A report on how the installed knowledge base is read and searched, for sending to the developers
from a machine whose documents cannot leave it:

    lattice-backend.exe --diagnose "What is Frontier application" "How to train a model"

It loads the knowledge base the way the app does and asks no model. It prints titles, counts and
settings only: no section text, no picture descriptions, no keys.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DEFAULT_QUESTIONS = ("What is Frontier application", "How to train a model", "How do I create a solution?")
TOP = 5


def _data_dir() -> Path:
    if os.environ.get("OASIS_DATA_DIR"):
        return Path(os.environ["OASIS_DATA_DIR"])
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for name in ("com.oasis.lineassistant", "Oasis"):  # the desktop app's folder, then the engine's own default
        if (local / name / "active.json").is_file():
            return local / name
    return local / "com.oasis.lineassistant"


def report(questions: list[str], data_dir: Path) -> str:
    from . import bundle
    from .api import ENGINE_BUILT
    from .config import load_config
    from .interview import Interview
    from .kbfolder import load_extras
    from .overview import overview
    from .search import SectionIndex
    from .vague import vague_clarification

    out = [f"Lattice diagnose | engine built {ENGINE_BUILT} | data folder {data_dir}"]
    config = load_config(data_dir)
    for name in ("connection", "chat_connection"):
        conn = getattr(config, name, None)
        if conn is not None:
            out.append(f"{name}: kind={conn.kind} model={conn.model or '-'} "
                       f"reasoning={getattr(conn, 'reasoning', '-')} key_set={bool(conn.api_key)}")
    out.append(f"search by meaning: {'set up (' + config.embeddings.model + ')' if config.embeddings else 'not set up'}")
    loaded = bundle.load_active(data_dir)
    if loaded is None:
        return "\n".join(out + ["No knowledge base is active."])
    manifest, docs = loaded
    content = bundle.version_dir(data_dir, manifest.kb_version) / bundle.CONTENT
    out.append(f"knowledge base: {manifest.kb_version} | format {manifest.source_format} | {len(docs)} documents")

    # how each document was read (see health.py), then the bundle's own files
    from .health import check
    health = check(docs, content)
    for d in health.documents:
        out.append(f"  - {d.title}: {d.sections} sections ({d.empty_sections} without text), {d.pictures} pictures "
                   f"({d.described} with a description), {d.loose_descriptions} descriptions left in the text")
    out += [f"  {c.level.upper()}: {c.message}" for c in health.checks] or ["  checks: nothing found"]
    for kb in sorted(content.glob("*/kb.json"))[:10] or sorted(content.glob("kb.json")):
        try:
            data = json.loads(kb.read_text(encoding="utf-8-sig"))
            sample = next(iter(data.get("sections") or []), {})
            out.append(f"  kb.json {kb.parent.name}: keys {sorted(data)} | section keys {sorted(sample)}")
        except (OSError, ValueError) as e:
            out.append(f"  kb.json {kb.parent.name}: unreadable ({type(e).__name__})")
        tree = kb.parent / "interaction" / "decision-tree.json"
        if tree.is_file():
            try:
                t = json.loads(tree.read_text(encoding="utf-8-sig"))
                start = (t.get("nodes") or {}).get(t.get("start"), {})
                out.append(f"    decision tree: {len(t.get('nodes') or {})} nodes, {len(t.get('results') or {})} results, "
                           f"first question: {start.get('ask', '-')!r}")
            except (OSError, ValueError) as e:
                out.append(f"    decision tree: unreadable ({type(e).__name__})")

    index = SectionIndex(docs)
    interview = None
    if manifest.source_format == "semkb":
        try:
            interview = Interview(docs, load_extras(content))
        except Exception as e:  # noqa: BLE001 - a report, not the app: say what failed and go on
            out.append(f"decision trees not loaded: {type(e).__name__}")
    for q in questions:
        out += ["", f"QUESTION: {q}"]
        found = overview(q, index)
        if found is not None:
            out.append(f"  answered from an overview, without searching: {docs[found[1]].locator(found[2])}")
        asked = interview.clarification(q, index) if interview is not None else None
        if asked is None:
            asked = vague_clarification(q, index)
        if asked is not None:
            out.append(f"  asked back: {asked.prompt!r} -> " + " | ".join(o.label for o in asked.options))
        out.append("  keyword search, best first:")
        for h in index.search(q)[:TOP]:
            out.append(f"    {h.score:6.2f}  {docs[h.doc_index].display_title[:40]} > {docs[h.doc_index].locator(h.node_id)}")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    questions = [a for a in argv if not a.startswith("--")] or list(DEFAULT_QUESTIONS)
    text = report(questions, _data_dir())
    print(text)
    target = Path.home() / "Desktop" / "lattice-diagnose.txt"
    try:
        target.write_text(text, encoding="utf-8")
        print(f"\nSaved to {target}")
    except OSError:
        pass
