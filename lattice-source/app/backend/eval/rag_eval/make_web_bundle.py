"""Turn a folder of Markdown documents into the web platform's knowledge-base export (13 Oct 2026):

    page_index.json                      the KB, each document and its sections with summaries
    files/<stem>_<ext>.bundle.zip        <stem>_<ext>.json (markdown + nodes) and <stem>_<ext>.md

    python eval/rag_eval/make_web_bundle.py CONTENT_DIR OUT.zip "KB name" [name.md=report.pdf ...]

The summaries are each section's first sentence (the platform writes them with a model)."""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path


def first_sentence(text: str, most: int = 220) -> str:
    text = " ".join(re.sub(r"[|#*`-]+", " ", text).split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:most]


def nodes_of(markdown: str, filename: str) -> list[dict]:
    parts = re.split(r"(?m)^(#{1,6})\s+(.+)$", markdown)
    nodes, n = [], 0
    for i in range(1, len(parts), 3):
        n += 1
        title, body = parts[i + 1].strip(), parts[i + 2]
        nodes.append({"node_id": f"{n:04d}", "title": filename if n == 1 else title, "type": "section",
                      "level": 1, "section_number": None, "start_page": None, "end_page": None,
                      "summary": first_sentence(body) or title})
    return nodes


def main() -> None:
    content, out, kb_name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    names = dict(a.split("=", 1) for a in sys.argv[4:])
    docs, files = [], {}
    for md in sorted(content.glob("*.md")):
        filename = names.get(md.name, md.stem + ".pdf")
        stem, ext = filename.rsplit(".", 1)
        key = f"{stem}_{ext}"
        text = md.read_text(encoding="utf-8")
        # the platform's first heading is the file name; the document's own title follows it
        body = f"# {filename}\n\n" + re.sub(r"\A#\s", "## ", text, count=1)
        nodes = nodes_of(body, filename)
        doc_summary = first_sentence(re.sub(r"(?m)^#.*$", "", text), 400)
        nodes[0]["summary"] = doc_summary
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{key}.json", json.dumps({"status": "ok", "filename": filename, "markdown": body,
                                                  "pages": None, "nodes": nodes}, ensure_ascii=False, indent=2))
            z.writestr(f"{key}.md", body)
        files[f"files/{key}.bundle.zip"] = inner.getvalue()
        docs.append({"filename": filename, "relative_path": filename, "file_type": f".{ext}",
                     "file_size": len(text.encode()), "parser_engine": "mcp_ocr", "page_count": None,
                     "node_count": len(nodes), "has_images": False, "image_count": 0, "image_files": [],
                     "bundle_zip": f"files/{key}.bundle.zip", "doc_summary": doc_summary, "sections": nodes})
    index = {"kb_id": 101, "kb_name": kb_name, "kb_description": f"{kb_name} (test export)",
             "generated_at": "2026-10-15T18:00:00Z", "total_files": len(docs), "indexed_files": len(docs),
             "documents": docs}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(path, data)
        z.writestr("page_index.json", json.dumps(index, ensure_ascii=False, indent=2))
    print(f"{out}: {len(docs)} documents")


if __name__ == "__main__":
    main()
