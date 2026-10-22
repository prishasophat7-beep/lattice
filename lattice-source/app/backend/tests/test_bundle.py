import json
import zipfile

import pytest

from oasis_engine import bundle


def make_bundle(src, out, version="2026.09.22-1", fmt="markdown"):
    return bundle.build_bundle(src, fmt, version, out)


def test_import_activates_and_loads(markdown_kb, tmp_path):
    zip_path = tmp_path / "kb.zip"
    make_bundle(markdown_kb, zip_path)
    data = tmp_path / "data"
    manifest = bundle.import_bundle(zip_path, data)
    assert bundle.active_version(data) == manifest.kb_version == "2026.09.22-1"
    loaded_manifest, docs = bundle.load_active(data)
    assert loaded_manifest.source_format == "markdown" and docs[0].name == "torque-tool.md"


def test_pageindex_bundle_roundtrip(pageindex_kb, tmp_path):
    zip_path = tmp_path / "kb.zip"
    make_bundle(pageindex_kb, zip_path, fmt="pageindex")
    bundle.import_bundle(zip_path, tmp_path / "data")
    _, docs = bundle.load_active(tmp_path / "data")
    assert docs[0].name == "press-manual.pdf"


def rewrite_zip(src, dst, edit):
    """Copy a zip, letting edit(name, data) change or drop (return None) members."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = edit(info.filename, zin.read(info.filename))
            if data is not None:
                zout.writestr(info.filename, data)
    return dst


def test_tampered_file_is_rejected_and_active_version_is_kept(markdown_kb, tmp_path):
    data = tmp_path / "data"
    make_bundle(markdown_kb, tmp_path / "v1.zip", version="v1")
    bundle.import_bundle(tmp_path / "v1.zip", data)

    make_bundle(markdown_kb, tmp_path / "v2.zip", version="v2")
    rewrite_zip(tmp_path / "v2.zip", tmp_path / "v2-bad.zip",
                lambda n, d: d.replace(b"12 Nm", b"99 Nm") if n.endswith(".md") else d)
    with pytest.raises(bundle.BundleError, match="Checksum mismatch"):
        bundle.import_bundle(tmp_path / "v2-bad.zip", data)
    assert bundle.active_version(data) == "v1"
    assert bundle.list_versions(data) == ["v1"]  # no partial leftovers


def test_zip_slip_path_is_rejected(markdown_kb, tmp_path):
    make_bundle(markdown_kb, tmp_path / "kb.zip")
    with zipfile.ZipFile(tmp_path / "kb.zip", "a") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(bundle.BundleError, match="Unsafe path"):
        bundle.import_bundle(tmp_path / "kb.zip", tmp_path / "data")
    assert not (tmp_path / "evil.txt").exists()


def test_unlisted_file_is_rejected(markdown_kb, tmp_path):
    make_bundle(markdown_kb, tmp_path / "kb.zip")
    with zipfile.ZipFile(tmp_path / "kb.zip", "a") as zf:
        zf.writestr("content/extra.md", "# Sneaky")
    with pytest.raises(bundle.BundleError, match="not listed"):
        bundle.import_bundle(tmp_path / "kb.zip", tmp_path / "data")


def test_invalid_manifest_is_rejected(markdown_kb, tmp_path):
    make_bundle(markdown_kb, tmp_path / "kb.zip")

    def bad_version(name, data):
        if name != "kb.json":
            return data
        m = json.loads(data)
        m["kb_version"] = "../../etc"
        return json.dumps(m).encode()

    rewrite_zip(tmp_path / "kb.zip", tmp_path / "bad.zip", bad_version)
    with pytest.raises(bundle.BundleError, match="Invalid kb.json"):
        bundle.import_bundle(tmp_path / "bad.zip", tmp_path / "data")


def test_reimport_switches_back_and_rollback_works(markdown_kb, tmp_path):
    data = tmp_path / "data"
    make_bundle(markdown_kb, tmp_path / "v1.zip", version="v1")
    make_bundle(markdown_kb, tmp_path / "v2.zip", version="v2")
    bundle.import_bundle(tmp_path / "v1.zip", data)
    bundle.import_bundle(tmp_path / "v2.zip", data)
    assert bundle.active_version(data) == "v2"
    bundle.import_bundle(tmp_path / "v1.zip", data)  # the same file again: switch to it
    assert bundle.active_version(data) == "v1"
    bundle.activate(data, "v2")
    assert bundle.active_version(data) == "v2"
    with pytest.raises(bundle.BundleError, match="not installed"):
        bundle.activate(data, "..")


def test_different_bundle_with_an_installed_version_is_rejected(markdown_kb, tmp_path):
    data = tmp_path / "data"
    make_bundle(markdown_kb, tmp_path / "a.zip", version="v1")
    bundle.import_bundle(tmp_path / "a.zip", data)
    (markdown_kb / "extra.md").write_text("# Extra\nMore text.\n", encoding="utf-8")
    make_bundle(markdown_kb, tmp_path / "b.zip", version="v1")
    with pytest.raises(bundle.BundleError, match="different knowledge base"):
        bundle.import_bundle(tmp_path / "b.zip", data)


def test_import_retries_a_briefly_locked_rename(markdown_kb, tmp_path, monkeypatch):
    # Windows: a virus scanner still reading the new files makes the folder rename fail for a moment
    zip_path = tmp_path / "kb.zip"
    make_bundle(markdown_kb, zip_path)
    real, fails = bundle.os.replace, [2]

    def flaky(src, dst):
        if fails[0]:
            fails[0] -= 1
            raise PermissionError(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(bundle.os, "replace", flaky)
    monkeypatch.setattr(bundle.time, "sleep", lambda s: None)
    data = tmp_path / "data"
    manifest = bundle.import_bundle(zip_path, data)
    assert fails[0] == 0 and bundle.active_version(data) == manifest.kb_version


def test_import_gives_up_on_a_lasting_lock(markdown_kb, tmp_path, monkeypatch):
    zip_path = tmp_path / "kb.zip"
    make_bundle(markdown_kb, zip_path)

    def locked(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(bundle.os, "replace", locked)
    monkeypatch.setattr(bundle.time, "sleep", lambda s: None)
    data = tmp_path / "data"
    with pytest.raises(PermissionError):
        bundle.import_bundle(zip_path, data)
    assert bundle.active_version(data) is None
    assert not (data / "versions").exists() or not any((data / "versions").iterdir())  # partial cleaned up
