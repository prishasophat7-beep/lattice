"""Knowledge-base bundles: the one artifact the web team hands over.

Bundle (a .zip):
    kb.json          manifest, see Manifest below
    content/...      the PageIndex store (docs/<id>/...) or the Markdown files
    originals/...    optional source PDFs, for opening cited pages

On the desktop, bundles are unpacked side by side and one is active:
    <data_dir>/versions/<kb_version>/   kb.json, content/, originals/
    <data_dir>/active.json              {"kb_version": "..."}

A sem-ai-kb knowledge base (kb.json with "modules" and "sections", modules/, AGENTS.md, ...; see
kbfolder.py), zipped with kb.json at its top or inside one folder, is imported too: import_semkb()
keeps its files under content/ with a manifest written here (source_format "semkb").

The web platform's own export (page_index.json + one bundle zip per document with its Markdown and
images: "bundle_zip": "files/<name>.bundle.zip", or in earlier exports "files": {"markdown": ...,
"bundle": ...}) is imported too: import_export() unpacks it into the same layout with a kb.json
written here (source_format "export"), so versions, rollback and checks work the same.

Import never touches the active version until the new one is fully extracted,
hash-checked and loadable; switching is a single atomic file replace, and older
versions stay on disk for rollback.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .loaders import EXPORT_INDEX, IMAGE_NAME, SKIPPED, KnowledgeBaseError, export_doc_id, export_title, load, mark_loose_descriptions
from .kbfolder import (KB_INDEX, KEPT as KB_KEPT, KEPT_SET as KB_SET_KEPT, MASTER_INDEX, doc_id_for as semkb_doc_id,
                       is_folder_index, is_semkb_index)
from .models import Doc

MANIFEST = "kb.json"
CONTENT = "content"
MAX_FILES = 20_000
MAX_TOTAL_BYTES = 4 * 1024**3  # 4 GB uncompressed


class BundleError(ValueError):
    """The bundle is malformed, tampered with or unsafe to extract."""


class DocEntry(BaseModel):
    id: str
    title: str | None = None
    description: str | None = None
    tags: list[str] = []
    language: str | None = None


class Manifest(BaseModel):
    format_version: Literal[1]
    kb_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    source_format: Literal["pageindex", "markdown", "export", "semkb"]
    created_at: str | None = None
    pageindex_version: str | None = None
    files: dict[str, str]  # bundle-relative path -> lowercase sha256 hex
    documents: list[DocEntry] = []
    # web-platform exports: documents left out because the export had no text for them
    skipped: list[str] = []


# ── helpers ──

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> str:
    """Reject absolute paths, drive letters, backslashes and '..' (zip-slip)."""
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        raise BundleError(f"Unsafe path in bundle: {name!r}")
    parts = PurePosixPath(name).parts
    if any(p in ("..", ".") for p in parts):
        raise BundleError(f"Unsafe path in bundle: {name!r}")
    return name


def _long_path(path: Path) -> Path:
    r"""On Windows, prefix an absolute path with \\?\ so pathlib can read and write paths longer
    than MAX_PATH (260). A knowledge base of long folder and file names (a manual's section
    titles, slugified) can reach that under %LOCALAPPDATA%\<app id>\versions\<kb version>\content\... .
    The prefix is idempotent and preserved by pathlib's / joins, so a prefixed root makes every
    path below it work; on other platforms the path is returned unchanged."""
    if os.name != "nt":
        return path
    s = str(path)
    if s.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\" + os.path.abspath(s))


def _replace(src: Path, dst: Path, attempts: int = 8, delay: float = 0.1) -> None:
    """os.replace, retried briefly: on Windows an antivirus scanner (or the search indexer) that is
    still reading freshly written files makes the rename fail with "Access is denied" for a moment."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(min(delay * 2**attempt, 2.0))


def _write_json_atomic(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    _replace(tmp, path)


def _versions(data_dir: Path) -> Path:
    return Path(data_dir) / "versions"


def version_dir(data_dir: Path, kb_version: str) -> Path:
    return _versions(data_dir) / kb_version


# ── reading an installed version ──

def read_manifest(version_dir: Path) -> Manifest:
    try:
        return Manifest.model_validate_json((Path(version_dir) / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise BundleError(f"No {MANIFEST} in {version_dir}") from e
    except ValidationError as e:
        raise BundleError(f"Invalid {MANIFEST}: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}") from e


def load_version(version_dir: Path) -> tuple[Manifest, list[Doc]]:
    manifest = read_manifest(version_dir)
    docs = load(_long_path(Path(version_dir) / CONTENT), manifest.source_format)
    mark_loose_descriptions(docs)
    return manifest, docs


def active_version(data_dir: Path) -> str | None:
    try:
        return json.loads((Path(data_dir) / "active.json").read_text(encoding="utf-8")).get("kb_version")
    except (FileNotFoundError, ValueError):
        return None


def list_versions(data_dir: Path) -> list[str]:
    root = _versions(data_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.endswith((".partial", ".removing")))


def activate(data_dir: Path, kb_version: str) -> None:
    """Switch the active version (also used for rollback)."""
    target = _versions(data_dir) / kb_version
    if kb_version not in list_versions(data_dir) or not target.is_dir():
        raise BundleError(f"Knowledge-base version {kb_version!r} is not installed")
    load_version(target)  # refuse to activate something that does not load
    _write_json_atomic(Path(data_dir) / "active.json", {
        "kb_version": kb_version, "activated_at": datetime.now(timezone.utc).isoformat()})


def remove_version(data_dir: Path, kb_version: str) -> None:
    """Delete an installed version that is not the active one (every import kept a full copy, its
    pictures and original files included, with no way to remove it)."""
    if kb_version not in list_versions(data_dir):
        raise BundleError(f"Knowledge-base version {kb_version!r} is not installed")
    if kb_version == active_version(data_dir):
        raise BundleError("The version in use cannot be removed; switch to another one first")
    target = _long_path(_versions(data_dir) / kb_version)
    trash = target.with_name(target.name + ".removing")  # never left half-deleted under its own name
    shutil.rmtree(trash, ignore_errors=True)
    _replace(target, trash)
    shutil.rmtree(trash, ignore_errors=True)


def load_active(data_dir: Path) -> tuple[Manifest, list[Doc]] | None:
    version = active_version(data_dir)
    if version is None:
        return None
    return load_version(_versions(data_dir) / version)


# ── import ──

MAX_IMAGE_BYTES = 25 * 1024**2
MAX_NESTED_BYTES = 512 * 1024**2


def _export_version(index: dict, raw: bytes) -> str:
    """A version name for an export, from its name and generation time:
    "sample-knowledge-base-2026.09.25-0821". Exports carry no version of their own."""
    name = re.sub(r"[^a-z0-9]+", "-", str(index.get("kb_name") or "kb").lower()).strip("-")[:36] or "kb"
    try:
        when = datetime.fromisoformat(str(index.get("generated_at")).replace("Z", "+00:00"))
        stamp = when.astimezone(timezone.utc).strftime("%Y.%m.%d-%H%M")
    except ValueError:
        stamp = hashlib.sha256(raw).hexdigest()[:10]
    return f"{name}-{stamp}"


_FILE_LOCATIONS = ("files", "bundle_zip")  # where the export put each file; the layout changed between exports


def _index_content(index: dict) -> dict:
    """An export index without its file locations: the same knowledge base exported in the earlier
    and the current layout compares equal."""
    docs = [{k: v for k, v in d.items() if k not in _FILE_LOCATIONS} if isinstance(d, dict) else d
            for d in index.get("documents") or []]
    return {**index, "documents": docs}


def import_export(zf: zipfile.ZipFile, names: set[str], data_dir: Path, make_active: bool) -> Manifest:
    """Unpack a web-platform export: the index, each document's Markdown and its images (from the
    document's nested bundle zip). Other files (structured.json, with its download links and
    access tokens) are not kept on the machine."""
    raw = zf.read(EXPORT_INDEX)
    try:
        index = json.loads(raw)
        documents = list(index["documents"])
    except (ValueError, KeyError, TypeError) as e:
        raise BundleError(f"{EXPORT_INDEX} is not a valid knowledge-base index") from e
    if not documents:
        raise BundleError(f"{EXPORT_INDEX} lists no documents")
    kb_version = _export_version(index, raw)
    final = _long_path(_versions(data_dir) / kb_version)
    index_hash = hashlib.sha256(raw).hexdigest()
    if final.exists():
        try:
            same = read_manifest(final).files.get(f"{CONTENT}/{EXPORT_INDEX}") == index_hash or (
                _index_content(json.loads((final / CONTENT / EXPORT_INDEX).read_bytes())) == _index_content(index))
        except (BundleError, OSError, ValueError):
            same = False
        if not same:
            raise BundleError(f"A different knowledge base named {kb_version!r} is already installed")
        if make_active:
            activate(data_dir, kb_version)
        return read_manifest(final)

    ids = [export_doc_id(e) for e in documents]
    if len(set(ids)) != len(ids):
        raise BundleError(f"{EXPORT_INDEX} lists the same document twice")
    partial = final.with_name(final.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    files: dict[str, str] = {}

    def write(rel: str, data: bytes) -> None:
        dest = partial.joinpath(*PurePosixPath(rel).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        files[rel] = hashlib.sha256(data).hexdigest()

    try:
        write(f"{CONTENT}/{EXPORT_INDEX}", raw)
        catalog, skipped, skipped_ids = [], [], []
        titles = [str(e.get("filename") or "") for e in documents]
        for entry, doc_id in zip(documents, ids):
            label = entry.get("filename") or doc_id
            paths = dict(entry.get("files") or {})
            if entry.get("bundle_zip"):  # current exports: just files/<name>.bundle.zip, the Markdown inside it
                paths.setdefault("bundle", entry["bundle_zip"])
            markdown, nested = None, None
            if paths.get("bundle"):
                member = _safe_member(paths["bundle"])
                if member in names:
                    if zf.getinfo(member).file_size > MAX_NESTED_BYTES:
                        raise BundleError(f"{label}: its bundle is too large")
                    try:
                        nested = zipfile.ZipFile(io.BytesIO(zf.read(member)))
                    except zipfile.BadZipFile:
                        nested = None  # the Markdown alone still answers; only the pictures are lost
            if paths.get("markdown") and _safe_member(paths["markdown"]) in names:
                markdown = zf.read(_safe_member(paths["markdown"]))
            elif nested is not None:  # the Markdown copy inside the document's own bundle
                md_names = [n for n in nested.namelist() if n.lower().endswith(".md") and "/" not in n]
                markdown = nested.read(md_names[0]) if md_names else None
            if markdown is None or not markdown.strip():
                # e.g. a file the platform could not convert: leave it out, say so, keep the rest
                skipped.append(str(label))
                skipped_ids.append(doc_id)
                continue
            write(f"{CONTENT}/docs/{doc_id}/doc.md", markdown)
            for info in nested.infolist() if nested is not None else []:
                image = info.filename[len("images/"):] if info.filename.startswith("images/") else ""
                if IMAGE_NAME.fullmatch(image) and info.file_size <= MAX_IMAGE_BYTES:
                    write(f"{CONTENT}/docs/{doc_id}/images/{image}", nested.read(info))
            catalog.append({"id": doc_id, "title": export_title(entry, titles),
                            "description": entry.get("doc_summary")})
        if not catalog:
            raise BundleError("The export has no document with text: " + ", ".join(skipped))
        if skipped_ids:
            write(f"{CONTENT}/{SKIPPED}", json.dumps(skipped_ids).encode())
        manifest = Manifest(format_version=1, kb_version=kb_version, source_format="export",
                            created_at=index.get("generated_at"), files=files,
                            documents=[DocEntry(**c) for c in catalog], skipped=skipped)
        (partial / MANIFEST).write_text(manifest.model_dump_json(indent=1), encoding="utf-8")
        try:
            load_version(partial)
        except KnowledgeBaseError as e:
            raise BundleError(f"The export does not load: {e}") from e
        except zipfile.BadZipFile as e:
            raise BundleError(f"A document's bundle in the export is not a readable zip: {e}") from e
        _replace(partial, final)
    except zipfile.BadZipFile as e:
        shutil.rmtree(partial, ignore_errors=True)
        raise BundleError(f"A document's bundle in the export is not a readable zip: {e}") from e
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if make_active:
        activate(data_dir, kb_version)
    return manifest


def _semkb_root(zf: zipfile.ZipFile, names: set[str]) -> str | None:
    """The folder prefix ("" or "inspectai-studio/") of a zipped sem-ai-kb knowledge base, if this is one."""
    for candidate in sorted((n for n in names if n == KB_INDEX or (n.endswith("/" + KB_INDEX) and n.count("/") == 1)),
                            key=len):
        try:
            if is_semkb_index(json.loads(zf.read(candidate))):
                return candidate[: -len(KB_INDEX)]
        except (ValueError, KeyError):
            continue
    return None


def _kbset_root(zf: zipfile.ZipFile, names: set[str]) -> str | None:
    """The folder prefix ("" or "multi doc test/") of a zipped knowledge base of document folders:
    page_index.json or README.md at the top, and one or more <document>/kb.json with modules and
    sections (convert_kb.py's, or one-manual sem-ai-kb kb.json files). page_index.json may name the
    folders or be the parser's own index."""
    tops = sorted({n[: -len(MASTER_INDEX)] for n in names if n == MASTER_INDEX or (n.endswith("/" + MASTER_INDEX) and n.count("/") == 1)}
                  | {n[: -len("README.md")] for n in names if n == "README.md" or (n.endswith("/README.md") and n.count("/") == 1)},
                  key=len)
    for prefix in tops:
        for n in sorted(names):
            rest = n[len(prefix):] if n.startswith(prefix) else None
            if rest is None or rest.count("/") != 1 or not rest.endswith("/" + KB_INDEX):
                continue
            if rest.split("/", 1)[0] in ("images", "modules", "interaction", "references", "pages"):
                continue
            try:
                if is_folder_index(json.loads(zf.read(n))):
                    return prefix
            except (ValueError, KeyError):
                continue
    return None


def import_semkb(zf: zipfile.ZipFile, names: set[str], prefix: str, data_dir: Path, make_active: bool,
                 *, several: bool = False) -> Manifest:
    """Unpack a sem-ai-kb knowledge base under content/, keeping only the files it is made of.
    several: a knowledge base of document folders under page_index.json (convert_kb.py)."""
    if several:
        try:
            master = json.loads(zf.read(prefix + MASTER_INDEX)) if prefix + MASTER_INDEX in names else {}
        except ValueError:
            master = {}
        master = master if isinstance(master, dict) else {}
        name = master.get("kb_name") or prefix.strip("/") or "kb"
        # the parser names a knowledge base of several files after its first one ("DataVault_Pro_UserManual"
        # for three manuals): the folder's name says more
        stems = {re.sub(r"\.[A-Za-z0-9]{2,5}$", "", str(d.get("filename") or "")) for d in master.get("documents") or []
                 if isinstance(d, dict)}
        if prefix.strip("/") and len(stems) > 1 and name in stems:
            name = prefix.strip("/")
        index = {"name": name, "source": {"generated_at": master.get("generated_at")}, "modules": []}
        keep = KB_SET_KEPT
    else:
        index = json.loads(zf.read(prefix + KB_INDEX))
        keep = KB_KEPT
    name = re.sub(r"[^a-z0-9]+", "-", str(index.get("name") or "kb").lower()).strip("-")[:40] or "kb"
    # the same knowledge base imported again is the same version; any change to it makes a new one
    content_hash = hashlib.sha256()
    kept = sorted(n for n in names if n.startswith(prefix) and keep.fullmatch(n[len(prefix):]))
    for n in kept:
        content_hash.update(n[len(prefix):].encode() + b"\0" + hashlib.sha256(zf.read(n)).digest())
    kb_version = f"{name}-{content_hash.hexdigest()[:10]}"
    final = _long_path(_versions(data_dir) / kb_version)
    if final.exists():
        if make_active:
            activate(data_dir, kb_version)
        return read_manifest(final)
    partial = final.with_name(final.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    files: dict[str, str] = {}
    try:
        for n in kept:
            info = zf.getinfo(n)
            if info.file_size > MAX_NESTED_BYTES:
                raise BundleError(f"{n} is too large")
            rel = f"{CONTENT}/{n[len(prefix):]}"
            data = zf.read(n)
            dest = partial.joinpath(*PurePosixPath(rel).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            files[rel] = hashlib.sha256(data).hexdigest()
        if several:  # the documents as loaded (names, ids), from the unpacked folders
            from .kbfolder import _as_semkb, _entry_for, document_folders, read_master
            root = partial / CONTENT
            found = read_master(root)
            if found is None:
                raise BundleError("No document folder with a kb.json was found in the knowledge base")
            documents = []
            for folder, data in document_folders(root, found):
                index = _as_semkb(folder, data, _entry_for(folder.name, found["documents"]))
                documents.append(DocEntry(id=semkb_doc_id(index["name"]), title=index["name"],
                                          description=str(index["summary"]) or None))
        else:
            sources = list(dict.fromkeys(str(m.get("source") or index.get("name") or "manual")
                                         for m in index["modules"] if isinstance(m, dict)))
            documents = [DocEntry(id=semkb_doc_id(src), title=src,
                                  description=next((str(m["summary"]) for m in index["modules"]
                                                    if isinstance(m, dict) and m.get("source") == src and m.get("summary")), None))
                         for src in sources]
        manifest = Manifest(format_version=1, kb_version=kb_version, source_format="semkb",
                            created_at=(index.get("source") or {}).get("generated_at") if isinstance(index.get("source"), dict) else None,
                            files=files, documents=documents)
        (partial / MANIFEST).write_text(manifest.model_dump_json(indent=1), encoding="utf-8")
        try:
            load_version(partial)
        except KnowledgeBaseError as e:
            raise BundleError(f"The knowledge base does not load: {e}") from e
        _replace(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if make_active:
        activate(data_dir, kb_version)
    return manifest


def import_bundle(zip_path: Path, data_dir: Path, *, make_active: bool = True) -> Manifest:
    zip_path, data_dir = Path(zip_path), Path(data_dir)
    if not zip_path.is_file():
        raise BundleError(f"File not found: {zip_path}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BundleError(f"{zip_path.name} is not a readable .zip file") from e

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise BundleError(f"Bundle has {len(infos)} files; the limit is {MAX_FILES}")
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise BundleError("Bundle is larger than 4 GB uncompressed")
        names = {_safe_member(i.filename) for i in infos}
        if len(names) != len(infos):
            raise BundleError("Bundle contains duplicate file names")
        kbset = _kbset_root(zf, names) if MANIFEST not in names else None
        if kbset is not None:  # document folders under page_index.json (convert_kb.py)
            return import_semkb(zf, names, kbset, data_dir, make_active, several=True)
        if MANIFEST not in names and EXPORT_INDEX in names:  # the web platform's own export format
            return import_export(zf, names, data_dir, make_active)
        semkb = _semkb_root(zf, names)
        if semkb is not None:  # a sem-ai-kb folder, zipped
            return import_semkb(zf, names, semkb, data_dir, make_active)
        if MANIFEST not in names:
            raise BundleError(f"Bundle has no {MANIFEST} (and no {EXPORT_INDEX})")
        try:
            manifest = Manifest.model_validate_json(zf.read(MANIFEST))
        except ValidationError as e:
            raise BundleError(f"Invalid {MANIFEST}: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}") from e

        listed = {_safe_member(p) for p in manifest.files}
        unlisted, missing = names - listed - {MANIFEST}, listed - names
        if unlisted:
            raise BundleError(f"Files not listed in {MANIFEST}: {sorted(unlisted)[:5]}")
        if missing:
            raise BundleError(f"Files listed in {MANIFEST} but missing: {sorted(missing)[:5]}")
        if not any(p.startswith(CONTENT + "/") for p in listed):
            raise BundleError(f"Bundle has no {CONTENT}/ folder")

        final = _long_path(_versions(data_dir) / manifest.kb_version)
        if final.exists():
            try:
                same = read_manifest(final).files == manifest.files
            except BundleError:
                same = False
            if not same:
                raise BundleError(f"A different knowledge base with version {manifest.kb_version!r} is "
                                  "already installed. Ask the web team to give each bundle a new version.")
            # the same bundle imported again (e.g. from the same USB stick): just switch to it
            if make_active:
                activate(data_dir, manifest.kb_version)
            return manifest
        partial = final.with_name(final.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        try:
            for info in infos:
                dest = partial.joinpath(*PurePosixPath(info.filename).parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                h, written = hashlib.sha256(), 0
                with zf.open(info) as src, open(dest, "wb") as out:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        written += len(chunk)
                        if written > info.file_size:
                            raise BundleError(f"{info.filename} is larger than declared")
                        h.update(chunk)
                        out.write(chunk)
                if info.filename != MANIFEST and h.hexdigest() != manifest.files[info.filename].lower():
                    raise BundleError(f"Checksum mismatch for {info.filename}: the bundle is "
                                      "corrupted or was modified after it was built")
            try:
                load_version(partial)
            except KnowledgeBaseError as e:
                raise BundleError(f"Bundle content does not load: {e}") from e
            _replace(partial, final)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise

    if make_active:
        activate(data_dir, manifest.kb_version)
    return manifest


# ── build (reference implementation for the web team, also used in tests) ──

def build_bundle(content_dir: Path, source_format: str, kb_version: str, out_zip: Path, *,
                 originals_dir: Path | None = None, documents: list[dict] | None = None,
                 pageindex_version: str | None = None) -> Manifest:
    files: dict[str, Path] = {}
    for prefix, root in ((CONTENT, content_dir), ("originals", originals_dir)):
        if root is None:
            continue
        for p in sorted(Path(root).rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                files[f"{prefix}/{p.relative_to(root).as_posix()}"] = p
    manifest = Manifest(
        format_version=1, kb_version=kb_version, source_format=source_format,  # type: ignore[arg-type]
        created_at=datetime.now(timezone.utc).isoformat(), pageindex_version=pageindex_version,
        files={name: _sha256_file(p) for name, p in files.items()},
        documents=[DocEntry(**d) for d in documents or []])
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, manifest.model_dump_json(indent=1))
        for name, p in files.items():
            zf.write(p, name)
    return manifest
