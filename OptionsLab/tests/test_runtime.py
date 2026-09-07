"""Actual temporary-layout checks for runtime byte and contract evidence."""

from dataclasses import FrozenInstanceError, replace
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT = Path(__file__).resolve().parents[1]


def runtime_api():
    """Load the concrete runtime API, failing clearly before it exists."""
    assert importlib.util.find_spec("options_lab.runtime") is not None
    from options_lab import runtime
    return runtime


def canonical(value):
    """Independently hash the specified canonical JSON framing."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@pytest.fixture
def source(tmp_path):
    """Copy real package and anchored metadata without generated caches."""
    project = tmp_path / "checkout"
    shutil.copytree(PROJECT / "src/options_lab", project / "src/options_lab",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "MANIFEST.in"):
        shutil.copyfile(PROJECT / name, project / name)
    return project


def run_at(root, setup="", body="print(json.dumps(asdict(api.measure_runtime())))"):
    """Run the actual copied package in an isolated fresh interpreter."""
    code = ("import sys,json\nfrom pathlib import Path\nfrom dataclasses import asdict\n"
            "sys.path.insert(0,sys.argv[1])\nimport options_lab.runtime as api\n"
            "package=Path(api.__file__).parent\n" + setup + "\n" + body)
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(root)],
                               cwd=root.parent, text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)


def evidence(result):
    """Require a successful exclusive result."""
    assert result["rejection"] is None, result
    assert result["value"] is not None
    return result["value"]


def rejected(result, code):
    """Require a bounded exclusive rejection."""
    assert result["value"] is None
    assert result["rejection"]["code"] == code
    assert set(result["rejection"]) == {"field", "code"}


def installed(source):
    """Materialize an ordinary pure-wheel layout with actual RECORD metadata."""
    root = source / "site-packages"
    shutil.copytree(source / "src/options_lab", root / "options_lab")
    info = root / "options_lab-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text('Metadata-Version: 2.4\nName: options-lab\n'
                                  'Version: 0.1.0\nRequires-Python: <3.12,>=3.11\n'
                                  'Provides-Extra: test\n'
                                  'Requires-Dist: pytest==9.0.2; extra == "test"\n\n')
    (info / "WHEEL").write_text("Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    (info / "INSTALLER").write_text("test-layout\n")
    (info / "RECORD").touch()
    with (info / "RECORD").open("w", newline="") as stream:
        csv.writer(stream).writerows((p.relative_to(root).as_posix(), "", "")
                                    for p in sorted(root.rglob("*")) if p.is_file())
    return root, info


def test_actual_runtime_has_canonical_implementation_and_honest_provenance():
    api = runtime_api()
    result = api.measure_runtime()
    assert type(result) is api.RuntimeMeasurement
    assert result.rejection is None and type(result.value) is api.RuntimeEvidence
    value = result.value
    package = Path(api.__file__).parent
    files = sorted((p.relative_to(package).as_posix(),
                    hashlib.sha256(p.read_bytes()).hexdigest())
                   for p in package.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    implementation = [list(row) for row in files if row[0] != "_fixture_catalog.json"]
    assert list(map(list, value.implementation_files)) == implementation
    assert value.implementation_digest == canonical({"scheme": "CORE_IMPLEMENTATION_BYTES_V1",
                                                     "files": implementation})
    assert value.python_implementation == "CPython"
    assert value.exact_python_version == tuple(sys.version_info)
    assert value.requires_python == ">=3.11,<3.12" and value.runtime_dependencies == ()
    assert value.runtime_contract_digest == canonical({"scheme": "RUNTIME_CONTRACT_V1",
        "python_implementation": "CPython", "exact_python_version": list(sys.version_info),
        "requires_python": ">=3.11,<3.12", "runtime_dependencies": []})
    assert value.inventory_kind in ("SOURCE_RUNTIME_INVENTORY_V1", "INSTALLED_DISTRIBUTION_INVENTORY_V1")
    assert value.inventory_digest == canonical({"scheme": value.inventory_kind,
                                                "files": list(map(list, value.inventory_files))})
    inventory_root = package.parent.parent if value.inventory_kind == "SOURCE_RUNTIME_INVENTORY_V1" else package.parent
    for name, digest in value.inventory_files:
        assert hashlib.sha256((inventory_root / name).read_bytes()).hexdigest() == digest
    assert value.wheel_archive_sha256 is value.build_source_commit is value.build_source_commit_basis is None
    assert api.recheck_runtime(value).value == value
    for item in (value, result, api.measure_runtime().value):
        with pytest.raises((TypeError, FrozenInstanceError)):
            replace(item)
    for cls in (api.RuntimeEvidence, api.RuntimeMeasurement, api.RuntimeRejection):
        with pytest.raises(TypeError):
            cls()
    with pytest.raises(TypeError):
        api.recheck_runtime({"implementation_digest": value.implementation_digest})
    class Derived(api.RuntimeEvidence):
        """This class represents an unsupported subclass of evidence."""
    with pytest.raises(TypeError):
        api.recheck_runtime(object.__new__(Derived))


def test_source_installed_relocation_and_inventory_actual_bytes(source):
    expected = evidence(run_at(source / "src"))
    root, info = installed(source)
    actual = evidence(run_at(root))
    assert actual["implementation_digest"] == expected["implementation_digest"]
    assert actual["runtime_contract_digest"] == expected["runtime_contract_digest"]
    assert actual["inventory_kind"] == "INSTALLED_DISTRIBUTION_INVENTORY_V1"
    assert actual["inventory_digest"] != expected["inventory_digest"]
    for name, digest in actual["inventory_files"]:
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    (info / "unlisted-metadata").write_bytes(b"actual unlisted metadata")
    changed = evidence(run_at(root))
    assert changed["inventory_digest"] != actual["inventory_digest"]
    assert changed["implementation_digest"] == actual["implementation_digest"]


@pytest.mark.parametrize("name", ["__init__.py", "runtime.py", "admission.py", "candidates.py", "account.py"])
def test_every_executable_owner_byte_is_measured_and_rechecked(source, name):
    result = run_at(source / "src", body=f"""before=api.measure_runtime().value
with (package / {name!r}).open('ab') as f: f.write(b'\\n')
print(json.dumps({{'before':asdict(before),'current':asdict(api.measure_runtime()),
                  'recheck':asdict(api.recheck_runtime(before))}}))""")
    assert evidence(result["current"])["implementation_digest"] != result["before"]["implementation_digest"]
    rejected(result["recheck"], "runtime_changed")


def test_recursive_new_deleted_renamed_modules_and_policy_json(source):
    root = source / "src"
    initial = evidence(run_at(root))
    nested = root / "options_lab/nested"
    nested.mkdir()
    module = nested / "new.py"
    module.write_text("VALUE = 1\n")
    added = evidence(run_at(root))
    module.rename(nested / "renamed.py")
    renamed = evidence(run_at(root))
    (nested / "renamed.py").unlink()
    assert evidence(run_at(root))["implementation_digest"] == initial["implementation_digest"]
    (nested / "policy.json").write_text('{"policy":1}')
    policy = evidence(run_at(root))
    assert len({v["implementation_digest"] for v in (initial, added, renamed, policy)}) == 4


def test_catalog_downstream_identity_and_loaded_state_binding(source):
    root = source / "src"
    before = evidence(run_at(root))
    catalog = root / "options_lab/_fixture_catalog.json"
    catalog.write_bytes(catalog.read_bytes() + b"\n")
    after = evidence(run_at(root))
    assert after["implementation_digest"] == before["implementation_digest"]
    assert after["catalog_sha256"] != before["catalog_sha256"]
    assert after["inventory_digest"] != before["inventory_digest"]
    rejected(run_at(root, "p=package/'_fixture_catalog.json'\np.write_bytes(p.read_bytes()+b'\\n')"),
             "catalog_unavailable")
    (root.parent / "_fixture_catalog.json").write_text("spoof")
    assert evidence(run_at(root))["catalog_sha256"] == hashlib.sha256(catalog.read_bytes()).hexdigest()


@pytest.mark.parametrize("payload", [None, b"{bad", b"{}"])
def test_missing_or_corrupt_catalog_is_unavailable(source, payload):
    catalog = source / "src/options_lab/_fixture_catalog.json"
    catalog.unlink() if payload is None else catalog.write_bytes(payload)
    rejected(run_at(source / "src"), "catalog_unavailable")


@pytest.mark.parametrize("change", ["order", "stale", "dependency", "extra", "python", "dynamic", "malformed"])
def test_source_metadata_is_anchored_and_finite(source, change):
    root = source / "src"
    before = evidence(run_at(root))
    path = source / "pyproject.toml"
    text = path.read_text()
    if change == "stale":
        (root / "options_lab.egg-info").mkdir()
        (root / "options_lab.egg-info/PKG-INFO").write_text("Requires-Python: >=9\n")
    elif change == "order":
        text = text.replace(">=3.11,<3.12", "<3.12, >=3.11")
    elif change == "dependency":
        text = text.replace('[project]', '[project]\ndependencies = ["requests>=1"]')
    elif change == "extra":
        text = text.replace('pytest==9.0.2', 'pytest>=9; mystery == "x"')
    elif change == "python":
        text = text.replace(">=3.11,<3.12", ">=3.11")
    elif change == "dynamic":
        text = text.replace('[project]', '[project]\ndynamic = ["dependencies"]')
    else:
        text = "[broken"
    path.write_text(text)
    result = run_at(root)
    if change in ("order", "stale"):
        assert evidence(result)["runtime_contract_digest"] == before["runtime_contract_digest"]
    else:
        assert result["value"] is None


@pytest.mark.parametrize("change", ["missing", "duplicate", "name", "version", "python", "dependency",
    "marker", "editable", "wheel", "malformed", "duplicate_field"])
def test_installed_metadata_rejects_unsupported_ownership_and_contract(source, change):
    root, info = installed(source)
    metadata = info / "METADATA"
    text = metadata.read_text()
    if change == "missing":
        shutil.rmtree(info)
    elif change == "duplicate":
        shutil.copytree(info, root / "options_lab-0.2.0.dist-info")
    elif change == "editable":
        (info / "direct_url.json").write_text('{"dir_info":{"editable":true}}')
    elif change == "wheel":
        (info / "WHEEL").write_text("Root-Is-Purelib: false\nTag: cp311-cp311-any\n")
    else:
        changes = {"name": ("Name: options-lab", "Name: another"),
                   "version": ("Version: 0.1.0", "Version: 0.2.0"),
                   "python": ("<3.12,>=3.11", ">=3.10"),
                   "dependency": ('pytest==9.0.2; extra == "test"', "requests>=1"),
                   "marker": ('extra == "test"', 'unknown == "test"'),
                   "malformed": ("Metadata-Version: 2.4", "invalid line"),
                   "duplicate_field": ("Name: options-lab", "Name: options-lab\nName: options-lab")}
        metadata.write_text(text.replace(*changes[change]))
    assert run_at(root)["value"] is None


@pytest.mark.parametrize("path", ["../outside", "/absolute", "other/__pycache__/x.cpython-311.pyc",
    "options_lab/../outside", "options_lab//x.py", "options_lab/./x.py", "options_lab\\x.py",
    "options_lab/__init__.py"])
def test_record_paths_validate_ownership_before_cache_exemption(source, path):
    root, info = installed(source)
    with (info / "RECORD").open("a", newline="") as stream:
        csv.writer(stream).writerow((path, "", ""))
    rejected(run_at(root), "invalid_record")


def test_record_generated_cache_optional_but_owned_files_required(source):
    root, info = installed(source)
    cache = root / "options_lab/__pycache__/runtime.cpython-311.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"generated")
    with (info / "RECORD").open("a", newline="") as stream:
        csv.writer(stream).writerow((cache.relative_to(root).as_posix(), "", ""))
    before = evidence(run_at(root))
    cache.unlink()
    assert evidence(run_at(root)) == before
    (info / "INSTALLER").unlink()
    rejected(run_at(root), "invalid_record")


@pytest.mark.parametrize("kind", ["symlink_file", "symlink_dir", "symlink_root", "native", "sourceless",
    "cache_policy", "cache_file", "cache_symlink", "fifo", "loader", "locations", "loaded_sourceless"])
def test_unsupported_package_layouts_are_bounded(source, kind):
    root = source / "src"
    package = root / "options_lab"
    setup = ""
    if kind == "symlink_file":
        (package / "linked.json").symlink_to(package / "_fixture_catalog.json")
    elif kind == "symlink_dir":
        (package / "linked").symlink_to(source, target_is_directory=True)
    elif kind == "symlink_root":
        package.rename(root / "actual")
        package.symlink_to(root / "actual", target_is_directory=True)
    elif kind in ("native", "sourceless", "cache_policy"):
        path = {"native": "native.so", "sourceless": "orphan.pyc", "cache_policy": "__pycache__/policy.json"}[kind]
        (package / path).parent.mkdir(exist_ok=True)
        (package / path).write_bytes(b"unsupported")
    elif kind == "cache_file":
        (package / "__pycache__").write_bytes(b"not a cache directory")
    elif kind == "cache_symlink":
        (package / "__pycache__").symlink_to(source, target_is_directory=True)
    elif kind == "fifo":
        import os
        os.mkfifo(package / "pipe")
    elif kind == "loader":
        setup = "sys.modules['options_lab'].__spec__.loader = object()"
    elif kind == "locations":
        setup = "sys.modules['options_lab'].__spec__.submodule_search_locations.append('/another')"
    else:
        setup = "from importlib.machinery import SourcelessFileLoader\napi.__spec__.loader=SourcelessFileLoader(api.__name__,api.__file__)"
    rejected(run_at(root, setup), "unsupported_layout")


@pytest.mark.parametrize("version", [(3, 11, 12, "final", 0), (3, 11, 11, "candidate", 1), None, (3, 12, 0, "final", 0), (3, 11, 0, "alpha", 1)])
def test_exact_interpreter_identity_is_observed_not_inferred(source, version):
    before = evidence(run_at(source / "src"))
    result = run_at(source / "src", f"api.sys.version_info={version!r}")
    if version is None or version[1] != 11 or version[2] == 0:
        rejected(result, "unsupported_python")
    else:
        after = evidence(result)
        assert after["exact_python_version"] == list(version)
        assert after["runtime_contract_digest"] != before["runtime_contract_digest"]


@pytest.mark.parametrize("error, code", [("PermissionError", "runtime_unavailable"),
                                        ("MemoryError", "resource_limit"), ("RecursionError", "resource_limit")])
def test_owned_io_failures_are_safe_without_echo(source, error, code):
    setup = f"def denied(self): raise {error}('private sensitive path')\nPath.read_bytes=denied"
    result = run_at(source / "src", setup)
    rejected(result, code)
    assert "private sensitive" not in json.dumps(result)


def test_generated_cache_survives_deleted_source_without_becoming_policy(source):
    root = source / "src"
    before = evidence(run_at(root))
    cache = root / "options_lab/__pycache__"
    cache.mkdir()
    (cache / "deleted.cpython-311.pyc").write_bytes(b"generated stale cache")
    assert evidence(run_at(root)) == before


@pytest.mark.parametrize("variant", ["metadata", "missing_metadata", "symlink", "empty_symlink"])
def test_another_distribution_cannot_also_claim_package_ownership(source, variant):
    root, _ = installed(source)
    other = root / "other-1.0.dist-info"
    other.mkdir()
    (other / "METADATA").write_text("Metadata-Version: 2.4\nName: other\nVersion: 1.0\n")
    (other / "RECORD").write_text("options_lab/runtime.py,,\n")
    if variant == "missing_metadata":
        (other / "METADATA").unlink()
    if variant in ("symlink", "empty_symlink"):
        target = root / "other-data"
        other.rename(target)
        other.symlink_to(target, target_is_directory=True)
        if variant == "empty_symlink":
            (target / "METADATA").unlink()
            (target / "RECORD").unlink()
    rejected(run_at(root), "unsupported_layout" if "symlink" in variant else "ambiguous_distribution")


@pytest.mark.parametrize("variant", ["broken_metadata", "missing_metadata", "empty_record"])
def test_unrelated_broken_metadata_does_not_override_actual_owner(source, variant):
    root, _ = installed(source)
    other = root / "other-1.0.dist-info"
    other.mkdir()
    if variant == "broken_metadata":
        (other / "METADATA").write_text("Name: other\ninvalid unrelated body")
    elif variant == "empty_record":
        (other / "RECORD").touch()
    evidence(run_at(root))


def test_inventory_framing_is_independent_and_domain_separated():
    api = runtime_api()
    rows = (("a.py", "0" * 64), ("nested/é.json", "f" * 64))
    assert api._file_digest("CORE_IMPLEMENTATION_BYTES_V1", rows) == '72ba5456e9733a84035e5881cdd036fc0b4c58943ca6d885951af6b9b9a5faea'
    assert api._file_digest("SOURCE_RUNTIME_INVENTORY_V1", rows) != '72ba5456e9733a84035e5881cdd036fc0b4c58943ca6d885951af6b9b9a5faea'


def test_process_control_exceptions_are_not_converted(source):
    setup = "def stop(self): raise KeyboardInterrupt()\nPath.read_bytes=stop"
    body = "try: api.measure_runtime()\nexcept KeyboardInterrupt: print(json.dumps('propagated'))"
    assert run_at(source / "src", setup, body) == "propagated"


def test_actual_zip_import_is_explicitly_unsupported(source):
    import zipfile
    archive = source / "package.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for path in (source / "src").rglob("*"):
            if path.is_file():
                stream.write(path, path.relative_to(source / "src"))
    rejected(run_at(archive), "unsupported_layout")


def test_record_must_identify_the_loaded_package_as_owned(source):
    root, info = installed(source)
    record = info / "RECORD"
    record.write_text("".join(line for line in record.read_text().splitlines(True)
                              if not line.startswith("options_lab/")))
    rejected(run_at(root), "invalid_record")


@pytest.mark.parametrize("directory_info, supported", [({}, True), ({"editable": False}, True),
    ({"editable": True}, False), (None, False), ([], False), ({"editable": 0}, False),
    ({"editable": "false"}, False), ({"unknown": False}, False)])
def test_directory_provenance_requires_exact_noneditable_facts(source, directory_info, supported):
    root, info = installed(source)
    before = evidence(run_at(root))
    direct = info / "direct_url.json"
    direct.write_text(json.dumps({"url": "file:///unread-source", "dir_info": directory_info}))
    result = run_at(root)
    if not supported:
        rejected(result, "unsupported_layout")
        return
    after = evidence(result)
    assert after["implementation_digest"] == before["implementation_digest"]
    assert after["runtime_contract_digest"] == before["runtime_contract_digest"]
    assert after["inventory_digest"] != before["inventory_digest"]
    assert dict(after["inventory_files"])[info.name + "/direct_url.json"] == hashlib.sha256(direct.read_bytes()).hexdigest()
    assert after["wheel_archive_sha256"] is after["build_source_commit"] is None


def test_copied_source_inventory_has_exact_package_and_anchored_metadata(source):
    value = evidence(run_at(source / "src"))
    paths = sorted(p for p in source.rglob("*") if p.is_file())
    expected = [[p.relative_to(source).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()]
                for p in paths]
    assert value["inventory_kind"] == "SOURCE_RUNTIME_INVENTORY_V1"
    assert value["inventory_files"] == expected
    assert value["inventory_digest"] == canonical({"scheme": "SOURCE_RUNTIME_INVENTORY_V1", "files": expected})


@pytest.mark.parametrize("payload", [b'{"dir_info":{"editable":true,"editable":false}}',
                                   b'{"dir_info":{},"dir_info":{}}', b'{"dir_info":NaN}'])
def test_ambiguous_directory_provenance_cannot_hide_editable_facts(source, payload):
    root, info = installed(source)
    (info / "direct_url.json").write_bytes(payload)
    rejected(run_at(root), "invalid_metadata")
