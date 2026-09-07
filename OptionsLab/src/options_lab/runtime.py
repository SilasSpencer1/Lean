"""Measure the ordinary trusted package actually running, without archive claims.

Implementation bytes exclude only the literal catalog and conventional generated
caches. Source and installed inventories retain downstream catalog/metadata bytes.
Measurements assume a stable trusted checkout or installation, not hostile process
mutation or continuous attestation. External runtime dependencies are unsupported.
"""

import csv
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
import hashlib
from importlib.machinery import SourceFileLoader
import io
from pathlib import Path
import re
import stat
import sys
import tomllib

from .admission import _CATALOG, _decode_json
from .config import _snapshot_hash


@dataclass(frozen=True, init=False)
class RuntimeEvidence:
    """This class represents factory-measured immutable runtime and file identity."""

    implementation_scheme: str
    implementation_files: tuple[tuple[str, str], ...]
    implementation_digest: str
    python_implementation: str
    exact_python_version: tuple[int, int, int, str, int]
    requires_python: str
    runtime_dependencies: tuple[tuple[str, str], ...]
    runtime_contract_digest: str
    catalog_sha256: str
    inventory_kind: str
    inventory_files: tuple[tuple[str, str], ...]
    inventory_digest: str
    distribution_name: str
    distribution_version: str
    wheel_archive_sha256: None
    build_source_commit: None
    build_source_commit_basis: None

    def __init__(self) -> None:
        """
        Block construction without actual package measurement.

        :returns:          None.
        :raises TypeError: Always; use measure_runtime.
        """
        raise TypeError("RuntimeEvidence values come from measure_runtime")


@dataclass(frozen=True, init=False)
class RuntimeRejection:
    """This class represents a fixed safe runtime failure field and code."""

    field: str
    code: str

    def __init__(self) -> None:
        """
        Block standalone rejection construction.

        :returns:          None.
        :raises TypeError: Always; use runtime measurement operations.
        """
        raise TypeError("RuntimeRejection values come from runtime measurement")


@dataclass(frozen=True, init=False)
class RuntimeMeasurement:
    """This class represents exactly one measured value or bounded rejection."""

    value: RuntimeEvidence | None
    rejection: RuntimeRejection | None

    def __init__(self) -> None:
        """
        Block caller-selected success or failure results.

        :returns:          None.
        :raises TypeError: Always; use runtime measurement operations.
        """
        raise TypeError("RuntimeMeasurement values come from runtime measurement")


class _RuntimeFailure(Exception):
    """This class represents private fixed-field measurement failure control flow."""


def _make(cls, **fields):
    """Construct private factory records from already measured concrete fields."""
    value = object.__new__(cls)
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    return value


def _fail(field: str, code: str) -> None:
    """Raise one owned bounded failure without environmental content."""
    raise _RuntimeFailure(field, code)


def _rejected(field: str, code: str) -> RuntimeMeasurement:
    """Return an exclusive failure result with fixed private vocabulary."""
    return _make(RuntimeMeasurement, value=None,
                 rejection=_make(RuntimeRejection, field=field, code=code))


def _regular(path: Path) -> bytes:
    """Read actual bytes only after rejecting symlinks and special files."""
    if not stat.S_ISREG(path.lstat().st_mode):
        _fail("layout", "unsupported_layout")
    return path.read_bytes()


def _cache(path: Path, relative: Path) -> bool:
    """Recognize conventional CPython caches, including harmless stale bytecode."""
    if "__pycache__" not in relative.parts:
        return False
    match = re.fullmatch(r"(.+)\.cpython-[0-9]+(?:\.opt-[012])?\.pyc", path.name)
    if len(relative.parts) < 2 or relative.parts[-2] != "__pycache__" or not match:
        _fail("layout", "unsupported_layout")
    return True


def _tree(root: Path) -> dict[str, bytes]:
    """Enumerate all actual ordinary files; inspect caches before excluding them."""
    files: dict[str, bytes] = {}

    def visit(directory: Path) -> None:
        """Traverse one ordinary directory without following symlinks."""
        if not stat.S_ISDIR(directory.lstat().st_mode):
            _fail("layout", "unsupported_layout")
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if "\\" in path.name or any(ord(char) < 32 for char in path.name):
                _fail("layout", "unsupported_layout")
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                if "__pycache__" in relative.parts[:-1]:
                    _fail("layout", "unsupported_layout")
                visit(path)
            elif not stat.S_ISREG(mode):
                _fail("layout", "unsupported_layout")
            elif not _cache(path, relative):
                if path.suffix.lower() in (".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib"):
                    _fail("layout", "unsupported_layout")
                files[relative.as_posix()] = path.read_bytes()

    visit(root)
    return files


def _package_root() -> Path:
    """Resolve one ordinary loaded package and all currently loaded core modules."""
    package = sys.modules.get("options_lab")
    spec = getattr(package, "__spec__", None)
    if (spec is None or type(spec.loader) is not SourceFileLoader
            or type(spec.origin) is not str or not spec.submodule_search_locations
            or len(spec.submodule_search_locations) != 1):
        _fail("layout", "unsupported_layout")
    root = Path(spec.origin).parent
    if (root.name != "options_lab" or not root.is_absolute()
            or Path(spec.origin) != root / "__init__.py"
            or list(spec.submodule_search_locations) != [str(root)]):
        _fail("layout", "unsupported_layout")
    for name, module in tuple(sys.modules.items()):
        if name == "options_lab" or name.startswith("options_lab."):
            loaded = getattr(module, "__spec__", None)
            if (loaded is None or type(loaded.loader) is not SourceFileLoader
                    or type(loaded.origin) is not str):
                _fail("layout", "unsupported_layout")
            path = Path(loaded.origin)
            if not path.is_relative_to(root) or path.suffix != ".py":
                _fail("layout", "unsupported_layout")
            if not stat.S_ISREG(path.lstat().st_mode):
                _fail("layout", "unsupported_layout")
            if loaded.submodule_search_locations is not None:
                if list(loaded.submodule_search_locations) != [str(path.parent)]:
                    _fail("layout", "unsupported_layout")
    return root


def _requires_python(raw: object) -> str:
    """Normalize only the supported finite Python requirement semantics."""
    if (type(raw) is not str or sorted(part.strip() for part in raw.split(","))
            != ["<3.12", ">=3.11"]):
        _fail("python", "unsupported_python")
    return ">=3.11,<3.12"


def _python_version() -> tuple[int, int, int, str, int]:
    """Observe the exact supported CPython version, including prerelease identity."""
    version = sys.version_info
    if (getattr(sys.implementation, "name", None) != "cpython"
            or not isinstance(version, tuple) or len(version) != 5
            or any(type(version[index]) is not int or version[index] < 0 for index in (0, 1, 2, 4))
            or type(version[3]) is not str
            or version[3] not in ("alpha", "beta", "candidate", "final")
            or version[:2] != (3, 11)
            or (version[2] == 0 and version[3] != "final")):
        _fail("python", "unsupported_python")
    return tuple(version)


def _identity(name: object, version: object) -> tuple[str, str]:
    """Validate the observed distribution identity without treating it as code."""
    if (type(name) is not str or re.sub(r"[-_.]+", "-", name).lower() != "options-lab"
            or type(version) is not str or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)):
        _fail("metadata", "invalid_metadata")
    return "options-lab", version


def _source(root: Path) -> tuple[str, str, str, dict[str, bytes]]:
    """Read source contract and inventory from the imported src layout only."""
    project = root.parent.parent
    if root.parent.is_symlink() or project.is_symlink():
        _fail("layout", "unsupported_layout")
    files = {name: _regular(project / name) for name in ("pyproject.toml", "MANIFEST.in")}
    raw = tomllib.loads(files["pyproject.toml"].decode("utf-8"))
    project_data = raw.get("project")
    tool = raw.get("tool", {})
    if type(project_data) is not dict or type(tool) is not dict:
        _fail("metadata", "invalid_metadata")
    find = tool
    for key in ("setuptools", "packages", "find"):
        find = find.get(key, {}) if type(find) is dict else None
    if type(find) is not dict or find.get("where") != ["src"]:
        _fail("metadata", "invalid_metadata")
    name, version = _identity(project_data.get("name"), project_data.get("version"))
    requires = _requires_python(project_data.get("requires-python"))
    optional = project_data.get("optional-dependencies", {})
    if (project_data.get("dynamic", []) != [] or project_data.get("dependencies", []) != []
            or type(optional) is not dict or set(optional) - {"test"}
            or ("test" in optional and optional["test"] != ["pytest==9.0.2"])):
        _fail("dependencies", "unsupported_dependency")
    return name, version, requires, files


def _message(payload: bytes):
    """Parse ordinary core metadata while rejecting structural parser defects."""
    message = BytesParser(policy=policy.default).parsebytes(payload)
    if message.defects:
        _fail("metadata", "invalid_metadata")
    return message


def _header(message, name: str) -> str:
    """Read exactly one required metadata header."""
    values = message.get_all(name, [])
    if len(values) != 1 or values[0].defects:
        _fail("metadata", "invalid_metadata")
    return str(values[0])


def _record(root: Path, info: Path, payload: bytes, files: dict[str, bytes]) -> None:
    """Validate RECORD ownership before exempting optional generated caches."""
    seen: set[str] = set()
    for row in csv.reader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True):
        if len(row) != 3:
            _fail("metadata", "invalid_record")
        path = row[0]
        parts = path.split("/")
        if (len(parts) < 2 or parts[0] not in ("options_lab", info.name)
                or any(part in ("", ".", "..") for part in parts)
                or "\\" in path or any(ord(char) < 32 for char in path) or path in seen):
            _fail("metadata", "invalid_record")
        seen.add(path)
        candidate = root.joinpath(*parts)
        for parent in (candidate, *candidate.parents):
            if parent == root:
                break
            if parent.is_symlink():
                _fail("layout", "unsupported_layout")
        if "__pycache__" in parts:
            if parts[0] != "options_lab":
                _fail("metadata", "invalid_record")
            _cache(candidate, Path(*parts[1:]))
        elif path not in files:
            _fail("metadata", "invalid_record")
    if not {"options_lab/__init__.py", info.name + "/RECORD"} <= seen:
        _fail("metadata", "invalid_record")


def _installed(root: Path, package_files: dict[str, bytes]) -> tuple[str, str, str, dict[str, bytes]]:
    """Measure one owning ordinary pure wheel, including unlisted metadata bytes."""
    owners = []
    for candidate in sorted(root.parent.glob("*.dist-info")):
        if candidate.is_symlink():
            _fail("layout", "unsupported_layout")
        named = re.fullmatch(r"options[-_]lab-.*\.dist-info", candidate.name, re.IGNORECASE)
        record = candidate / "RECORD"
        claims_package = False
        if record.is_file():
            rows = csv.reader(io.StringIO(_regular(record).decode("utf-8")), strict=True)
            claims_package = any(row and row[0].startswith("options_lab/") for row in rows)
        metadata = candidate / "METADATA"
        message = None
        if metadata.is_file():
            message = BytesParser(policy=policy.default).parsebytes(_regular(metadata))
        names = [] if message is None else message.get_all("Name", [])
        matching = any(re.sub(r"[-_.]+", "-", str(name)).lower() == "options-lab" for name in names)
        if named or matching or claims_package:
            owners.append((candidate, message))
    if len(owners) != 1:
        _fail("metadata", "ambiguous_distribution" if owners else "missing_distribution")
    info, metadata = owners[0]
    if metadata is None or metadata.defects:
        _fail("metadata", "invalid_metadata")
    name, version = _identity(_header(metadata, "Name"), _header(metadata, "Version"))
    if info.name != f"options_lab-{version}.dist-info":
        _fail("metadata", "invalid_metadata")
    if _header(metadata, "Metadata-Version") not in ("2.1", "2.2", "2.3", "2.4"):
        _fail("metadata", "invalid_metadata")
    requires = _requires_python(_header(metadata, "Requires-Python"))
    requirements = metadata.get_all("Requires-Dist", [])
    if any(not re.fullmatch(r'''pytest\s*==\s*9\.0\.2\s*;\s*extra\s*==\s*(["'])test\1''',
                           str(item)) for item in requirements):
        _fail("dependencies", "unsupported_dependency")
    if len(requirements) > 1 or list(map(str, metadata.get_all("Provides-Extra", []))) not in ([], ["test"]):
        _fail("dependencies", "unsupported_dependency")
    metadata_files = _tree(info)
    if not {"METADATA", "WHEEL", "RECORD"} <= metadata_files.keys():
        _fail("metadata", "invalid_metadata")
    wheel = _message(metadata_files["WHEEL"])
    if (_header(wheel, "Wheel-Version") != "1.0" or _header(wheel, "Root-Is-Purelib") != "true"
            or list(map(str, wheel.get_all("Tag", []))) != ["py3-none-any"]):
        _fail("layout", "unsupported_layout")
    if "direct_url.json" in metadata_files:
        direct, error = _decode_json(metadata_files["direct_url.json"])
        if error is not None:
            _fail("metadata", "resource_limit" if error == "resource_limit" else "invalid_metadata")
        if type(direct) is not dict:
            _fail("layout", "unsupported_layout")
        if "dir_info" in direct:
            directory = direct["dir_info"]
            if (type(directory) is not dict or set(directory) - {"editable"}
                    or ("editable" in directory and directory["editable"] is not False)):
                _fail("layout", "unsupported_layout")
    files = {"options_lab/" + path: data for path, data in package_files.items()}
    files.update({info.name + "/" + path: data for path, data in metadata_files.items()})
    _record(root.parent, info, metadata_files["RECORD"], files)
    return name, version, requires, files


def _rows(files: dict[str, bytes]) -> tuple[tuple[str, str], ...]:
    """Hash sorted actual-byte rows using relative POSIX file names."""
    return tuple((name, hashlib.sha256(data).hexdigest()) for name, data in sorted(files.items()))


def _file_digest(scheme: str, rows: tuple[tuple[str, str], ...]) -> str:
    """Frame a versioned file inventory unambiguously through the owned hasher."""
    return _snapshot_hash({"scheme": scheme, "files": [list(row) for row in rows]})


def measure_runtime() -> RuntimeMeasurement:
    """
    Measure the actual loaded package, interpreter, catalog and owned metadata.

    Source inventory is the runtime subset of package files plus anchored
    pyproject.toml/MANIFEST.in, not complete tested-source evidence. Installed
    inventory hashes actual files, not RECORD assertions or an original archive.
    Unknown original archive and build-source facts remain None.

    :returns: RuntimeMeasurement with exactly one evidence value or safe rejection.
    """
    try:
        root = _package_root()
        if _CATALOG.error_code is not None or _CATALOG.catalog_sha256 is None:
            _fail("catalog", "catalog_unavailable")
        package_files = _tree(root)
        catalog = package_files.get("_fixture_catalog.json")
        if catalog is None or hashlib.sha256(catalog).hexdigest() != _CATALOG.catalog_sha256:
            _fail("catalog", "catalog_unavailable")
        if root.parent.name == "src":
            name, version, requires, inventory = _source(root)
            inventory.update({"src/options_lab/" + path: data for path, data in package_files.items()})
            kind = "SOURCE_RUNTIME_INVENTORY_V1"
        else:
            name, version, requires, inventory = _installed(root, package_files)
            kind = "INSTALLED_DISTRIBUTION_INVENTORY_V1"
        python_version = _python_version()
        implementation = _rows({path: data for path, data in package_files.items()
                                if path != "_fixture_catalog.json"})
        inventory_rows = _rows(inventory)
        contract = {"scheme": "RUNTIME_CONTRACT_V1", "python_implementation": "CPython",
                    "exact_python_version": list(python_version), "requires_python": requires,
                    "runtime_dependencies": []}
        value = _make(RuntimeEvidence,
            implementation_scheme="CORE_IMPLEMENTATION_BYTES_V1", implementation_files=implementation,
            implementation_digest=_file_digest("CORE_IMPLEMENTATION_BYTES_V1", implementation),
            python_implementation="CPython", exact_python_version=python_version,
            requires_python=requires, runtime_dependencies=(), runtime_contract_digest=_snapshot_hash(contract),
            catalog_sha256=_CATALOG.catalog_sha256, inventory_kind=kind, inventory_files=inventory_rows,
            inventory_digest=_file_digest(kind, inventory_rows), distribution_name=name,
            distribution_version=version, wheel_archive_sha256=None,
            build_source_commit=None, build_source_commit_basis=None)
        return _make(RuntimeMeasurement, value=value, rejection=None)
    except _RuntimeFailure as failure:
        return _rejected(*failure.args)
    except (MemoryError, RecursionError):
        return _rejected("runtime", "resource_limit")
    except OSError:
        return _rejected("runtime", "runtime_unavailable")
    except (UnicodeError, ValueError, csv.Error):
        return _rejected("metadata", "invalid_metadata")


def recheck_runtime(evidence: RuntimeEvidence) -> RuntimeMeasurement:
    """
    Remeasure the trusted runtime and compare every retained immutable fact.

    :param    evidence: RuntimeEvidence produced by this runtime owner.
    :returns:           Current measurement or runtime_changed on disagreement.
    :raises TypeError:  If evidence is not the exact RuntimeEvidence type.
    """
    if type(evidence) is not RuntimeEvidence:
        raise TypeError("evidence must be an exact RuntimeEvidence")
    current = measure_runtime()
    if current.value is not None and current.value != evidence:
        return _rejected("evidence", "runtime_changed")
    return current
