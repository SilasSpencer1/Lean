"""Verify code-owned fixture bytes and retain permanently synthetic evidence."""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from importlib import resources
import json
import math
import re
from typing import Literal, Never

from ._input_parsing import (
    _InvalidInput,
    _parse_string,
    _parse_timestamp,
    _parse_token,
    _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .greeks import FIXTURE_GREEK_METHOD, GreekMethodSpec


FixtureKind = Literal[
    "volume_partition", "feature_normalization",
    "option_quote", "underlying_quote", "greek_observation", "quote_coherence", "tick_rule",
    "exchange_session", "instrument_tradability", "provider_contract_mapping", "contract_reference", "underlying_bar",
]
FixtureRejectionCode = Literal[
    "catalog_unavailable", "catalog_invalid", "catalog_resource_limit",
    "unknown_fixture", "hash_mismatch", "invalid_utf8", "duplicate_key",
    "nonfinite_number", "invalid_json", "resource_limit",
    "expected_exact_dict", "unknown_fields", "missing", "invalid_type",
    "invalid_value", "invalid_timestamp", "duplicate_id", "unknown_profile",
    "kind_mismatch", "stream_mismatch",
]

_ROOT_FIELDS = (
    "schema_version", "normalization_version", "fixture_id", "generator_id",
    "generator_version", "assembled_at", "generator_source_ref", "origin",
    "permitted_use", "modeled_source_profiles", "definitions", "members",
)
_PROFILE_FIELDS = (
    "profile_id", "kind", "source", "stream_id", "feed_class", "fidelity",
    "availability_basis", "units", "record_identity_rule",
)
_DEFINITION_FIELDS = (
    "greek_method", "coherence_protocol_ids", "tick_definition_ids",
)
_METHOD_FIELDS = (
    "method_id", "method_version", "assumptions_id", "method_spec_hash",
)
_MEMBER_FIELDS = (
    "record_id", "kind", "profile_id", "raw_hash", "raw_body", "envelope",
)
_ENVELOPE_FIELDS = (
    "event_id", "raw_ref", "simulated_received_at", "stream_id",
    "receive_sequence", "supersedes_record_id", "contract", "metadata",
)
_CATALOG_FIELDS = ("catalog_schema_version", "fixtures")
_DESCRIPTOR_FIELDS = (
    "fixture_id", "generator_id", "generator_version", "payload_schema_version",
    "normalization_version", "expected_payload_sha256",
)
_KINDS = (
    "volume_partition", "feature_normalization",
    "option_quote", "underlying_quote", "greek_observation", "quote_coherence", "tick_rule",
    "exchange_session", "instrument_tradability", "provider_contract_mapping", "contract_reference", "underlying_bar",
)
_TICK_DEFINITIONS = (("fixture-usd-premium-tick-v1", "1"),)
_COHERENCE_PROTOCOLS = (
    ("joint_snapshot", "fixture-joint-book-snapshot-v1"),
    ("side_validity_overlap", "fixture-side-validity-overlap-v1"),
)
_HASH_DIGITS = frozenset("0123456789abcdef")
_REJECTION_CODES = (
    "catalog_unavailable", "catalog_invalid", "catalog_resource_limit",
    "unknown_fixture", "hash_mismatch", "invalid_utf8", "duplicate_key",
    "nonfinite_number", "invalid_json", "resource_limit",
    "expected_exact_dict", "unknown_fields", "missing", "invalid_type",
    "invalid_value", "invalid_timestamp", "duplicate_id", "unknown_profile",
    "kind_mismatch", "stream_mismatch",
)
_UNITS = {
    "volume_partition": {},
    "feature_normalization": {"volume": "shares"},
    "option_quote": {
        "price": "option_premium_USD_per_share", "size": "contracts",
    },
    "underlying_quote": {"price": "USD_per_share", "size": "shares"},
    "underlying_bar": {
        "price": "USD_per_share", "volume": "shares",
        "vwap_numerator": "USD", "vwap_denominator": "shares",
    },
    "greek_observation": {
        "delta": "signed_option_price_per_underlying_price",
        "iv": "annualized_volatility_fraction",
        "rate": "continuous_annual_fraction",
        "dividend": "continuous_annual_fraction",
    },
    "quote_coherence": {},
    "tick_rule": {"price": "USD_per_share"},
    "exchange_session": {},
    "instrument_tradability": {},
    "provider_contract_mapping": {},
    "contract_reference": {},
}


class _AdmissionFailure(Exception):
    """This class represents private safe fixture-validation control flow."""


class _DuplicateJsonKey(Exception):
    """This class represents a duplicate JSON object key."""


class _NonfiniteJsonNumber(Exception):
    """This class represents a non-finite decoded JSON number."""


class _JsonResourceLimit(Exception):
    """This class represents an interpreter JSON numeric resource limit."""


@dataclass(frozen=True)
class _FixtureDescriptor:
    """This class represents one immutable private catalog descriptor."""

    fixture_id: str
    generator_id: str
    generator_version: str
    payload_schema_version: int
    normalization_version: int
    expected_payload_sha256: str


@dataclass(frozen=True)
class _CatalogState:
    """This class represents the one loaded catalog or a bounded failure."""

    descriptors: tuple[_FixtureDescriptor, ...] = ()
    catalog_sha256: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, init=False)
class VerifiedFixtureMember:
    """This class represents one immutable byte-backed member of verified bytes."""

    record_id: str
    kind: FixtureKind
    profile_id: str
    raw_hash: str
    raw_body_bytes: bytes
    envelope_bytes: bytes

    def __init__(self) -> None:
        """
        Block standalone construction without verified catalog membership.

        :returns:          None.
        :raises TypeError: Always; members are produced only by fixture admission.
        """
        raise TypeError("VerifiedFixtureMember values come from fixture admission")

    def decode_raw_body(self) -> dict[str, object]:
        """
        Decode and return a fresh copy of the retained raw body.

        :returns:              A fresh exact dictionary for an existing owner.
        :raises RuntimeError:  If verified internal bytes cannot be decoded.
        """
        return _decode_verified_dict(self.raw_body_bytes)

    def decode_envelope(self) -> dict[str, object]:
        """
        Decode and return a fresh copy of the retained simulated envelope.

        :returns:              A fresh exact dictionary for an existing owner.
        :raises RuntimeError:  If verified internal bytes cannot be decoded.
        """
        return _decode_verified_dict(self.envelope_bytes)


@dataclass(frozen=True, init=False)
class VerifiedFixtureManifest:
    """This class represents admitted code-owned fixture bytes and provenance."""

    fixture_id: str
    generator_id: str
    generator_version: str
    schema_version: int
    normalization_version: int
    assembled_at: datetime
    generator_source_ref: str
    payload_sha256: str
    catalog_sha256: str
    payload_bytes: bytes
    event_id: str
    received_at: datetime
    raw_ref: str
    members: tuple[VerifiedFixtureMember, ...]
    modeled_source_profiles_bytes: bytes
    greek_method: GreekMethodSpec
    coherence_protocol_ids: tuple[str, ...]
    tick_definition_ids: tuple[str, ...]
    origin: Literal["synthetic"] = field(default="synthetic", init=False)
    fidelity_tier: Literal[0] = field(default=0, init=False)
    permitted_use: Literal["core_fixture"] = field(default="core_fixture", init=False)
    operational_allowed: Literal[False] = field(default=False, init=False)
    economic_allowed: Literal[False] = field(default=False, init=False)

    def __init__(self) -> None:
        """
        Block construction that has not rechecked actual bytes and catalog state.

        :returns:          None.
        :raises TypeError: Always; manifests are produced only by fixture admission.
        """
        raise TypeError("VerifiedFixtureManifest values come from fixture admission")

    def decode_modeled_source_profiles(self) -> list[dict[str, object]]:
        """
        Decode and return fresh copies of all retained modeled profiles.

        :returns:              A fresh ordered list of exact profile dictionaries.
        :raises RuntimeError:  If verified internal bytes cannot be decoded.
        """
        value, code = _decode_json(self.modeled_source_profiles_bytes)
        if code is not None or type(value) is not list:
            raise RuntimeError("verified modeled source profiles cannot be decoded")
        return value


@dataclass(frozen=True)
class FixtureInputRejection:
    """This class represents one safe code-owned fixture admission failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: FixtureRejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted rejection identity and bounded safe evidence.

        :returns:             None.
        :raises TypeError:    If identity, time, field, or code has the wrong type.
        :raises ValueError:   If identity is empty or the code is unsupported.
        """
        for name in ("event_id", "raw_ref", "field"):
            _require_nonempty_string(name, getattr(self, name))
        object.__setattr__(
            self, "received_at", _trusted_datetime("received_at", self.received_at)
        )
        if type(self.code) is not str:
            raise TypeError("code must be a string")
        if self.code not in _REJECTION_CODES or self.code not in _allowed_codes(
            self.field
        ):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["fixture_admission"]:
        """
        Return the fixed fixture-admission stage.

        :returns: The fixture-admission stage.
        """
        return "fixture_admission"

    @property
    def reasons(self) -> tuple[FixtureRejectionCode]:
        """
        Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class FixtureVerification:
    """This class represents exactly one verified fixture manifest or rejection."""

    value: VerifiedFixtureManifest | None = None
    rejection: FixtureInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete fixture-admission outcome.

        :returns:             None.
        :raises TypeError:    If an outcome has the wrong concrete record type.
        :raises ValueError:   If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("verification result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not VerifiedFixtureManifest:
            raise TypeError("verification value must be a VerifiedFixtureManifest")
        if self.rejection is not None and type(self.rejection) is not FixtureInputRejection:
            raise TypeError("verification rejection must be a FixtureInputRejection")


def verify_fixture_bundle(
    fixture_id: str,
    payload: bytes,
    *,
    event_id: str,
    raw_ref: str,
    received_at: datetime,
) -> FixtureVerification:
    """
    Verify actual payload bytes against the one fixed packaged fixture catalog.

    :param    fixture_id:   Exact literal catalog fixture identifier.
    :param    payload:      Actual complete fixture payload bytes.
    :param    event_id:     Trusted verification-event identifier.
    :param    raw_ref:      Trusted locator for the supplied fixture bytes.
    :param    received_at:  Trusted aware timestamp of this verification event.
    :returns:               Exactly one immutable manifest or safe rejection.
    :raises TypeError:      If a trusted argument has the wrong exact type.
    :raises ValueError:     If trusted identity or timestamp evidence is invalid.
    """
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)
    if type(fixture_id) is not str:
        return _rejection(event_id, raw_ref, received_at, "fixture_id", "invalid_type")
    if not fixture_id:
        return _rejection(event_id, raw_ref, received_at, "fixture_id", "invalid_value")
    if type(payload) is not bytes:
        return _rejection(event_id, raw_ref, received_at, "payload", "invalid_type")
    if _CATALOG.error_code is not None:
        return _rejection(
            event_id, raw_ref, received_at, "catalog", _CATALOG.error_code,
        )
    descriptor = next(
        (item for item in _CATALOG.descriptors if item.fixture_id == fixture_id), None
    )
    if descriptor is None:
        return _rejection(
            event_id, raw_ref, received_at, "fixture_id", "unknown_fixture",
        )
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    if payload_sha256 != descriptor.expected_payload_sha256:
        return _rejection(
            event_id, raw_ref, received_at, "payload", "hash_mismatch"
        )
    decoded, decode_code = _decode_json(payload)
    if decode_code is not None:
        return _rejection(
            event_id, raw_ref, received_at, "payload", decode_code
        )
    try:
        manifest = _build_manifest(
            descriptor, decoded, payload, payload_sha256, event_id, raw_ref,
            received_at,
        )
    except (_AdmissionFailure, _InvalidInput) as failure:
        field_name, code = failure.args
        return _rejection(
            event_id, raw_ref, received_at, field_name, code
        )
    return FixtureVerification(value=manifest)


def _build_manifest(
    descriptor: _FixtureDescriptor,
    decoded: object,
    payload: bytes,
    payload_sha256: str,
    event_id: str,
    raw_ref: str,
    received_at: datetime,
) -> VerifiedFixtureManifest:
    """Bridge shared parser failures into this owner's bounded control flow."""
    try:
        return _build_manifest_checked(
            descriptor, decoded, payload, payload_sha256, event_id, raw_ref,
            received_at,
        )
    except _InvalidInput as failure:
        raise _AdmissionFailure(*failure.args) from None


def _build_manifest_checked(
    descriptor: _FixtureDescriptor,
    decoded: object,
    payload: bytes,
    payload_sha256: str,
    event_id: str,
    raw_ref: str,
    received_at: datetime,
) -> VerifiedFixtureManifest:
    """Validate closed payload framing and construct its private frozen evidence."""
    _require_shape(decoded, "$", _ROOT_FIELDS)
    root = decoded
    _exact_integer(root["schema_version"], "schema_version", 1)
    _exact_integer(root["normalization_version"], "normalization_version", 1)
    for name, expected in (
        ("fixture_id", descriptor.fixture_id),
        ("generator_id", descriptor.generator_id),
        ("generator_version", descriptor.generator_version),
        ("origin", "synthetic"),
        ("permitted_use", "core_fixture"),
    ):
        if _parse_string(root[name], name) != expected:
            _fail(name, "invalid_value")
    if descriptor.payload_schema_version != 1 or descriptor.normalization_version != 1:
        _fail("catalog", "catalog_invalid")
    assembled_at = _parse_timestamp(root["assembled_at"], "assembled_at")
    generator_source_ref = _parse_string(
        root["generator_source_ref"], "generator_source_ref"
    )
    profiles, profiles_by_id = _profiles(root["modeled_source_profiles"])
    coherence_protocol_ids, tick_definition_ids = _definitions(root["definitions"])
    members = _members(root["members"], profiles_by_id)
    manifest = object.__new__(VerifiedFixtureManifest)
    values = {
        "fixture_id": descriptor.fixture_id,
        "generator_id": descriptor.generator_id,
        "generator_version": descriptor.generator_version,
        "schema_version": 1,
        "normalization_version": 1,
        "assembled_at": assembled_at,
        "generator_source_ref": generator_source_ref,
        "payload_sha256": payload_sha256,
        "catalog_sha256": _CATALOG.catalog_sha256,
        "payload_bytes": payload,
        "event_id": event_id,
        "received_at": received_at,
        "raw_ref": raw_ref,
        "members": members,
        "modeled_source_profiles_bytes": _canonical_or_fail(
            profiles, "modeled_source_profiles"
        ),
        "greek_method": FIXTURE_GREEK_METHOD,
        "coherence_protocol_ids": coherence_protocol_ids,
        "tick_definition_ids": tick_definition_ids,
        "origin": "synthetic",
        "fidelity_tier": 0,
        "permitted_use": "core_fixture",
        "operational_allowed": False,
        "economic_allowed": False,
    }
    for name, value in values.items():
        object.__setattr__(manifest, name, value)
    return manifest


def _profiles(
    raw: object,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    """Validate exact profile framing and return a private immutable lookup basis."""
    values = _list(raw, "modeled_source_profiles")
    profiles: list[dict[str, object]] = []
    by_id: dict[str, dict[str, object]] = {}
    for index, value in enumerate(values):
        path = f"modeled_source_profiles[{index}]"
        _require_shape(value, path, _PROFILE_FIELDS)
        profile = value
        profile_id = _parse_string(profile["profile_id"], f"{path}.profile_id")
        if profile_id in by_id:
            _fail(f"{path}.profile_id", "duplicate_id")
        kind = _parse_token(profile["kind"], f"{path}.kind", _KINDS)
        _parse_string(profile["source"], f"{path}.source")
        _parse_string(profile["stream_id"], f"{path}.stream_id")
        quote = kind in ("option_quote", "underlying_quote", "underlying_bar")
        for name, expected in (
            ("feed_class", "realtime"), ("fidelity", "genuine")
        ):
            value = profile[name]
            if quote or (name == "fidelity" and kind in ("exchange_session", "instrument_tradability")):
                if _parse_string(value, f"{path}.{name}") != expected:
                    _fail(f"{path}.{name}", "invalid_value")
            elif value is not None:
                _fail(f"{path}.{name}", "invalid_value")
        identity_rule = (
            "new_evidence_id_per_update"
            if kind == "quote_coherence"
            else "provider_record_id_and_revision_id" if kind == "underlying_bar"
            else "new_event_id_per_update" if kind in ("provider_contract_mapping", "volume_partition", "feature_normalization")
            else "new_provider_record_id_per_update"
        )
        for name, expected in (
            ("availability_basis", "measured"),
            ("record_identity_rule", identity_rule),
        ):
            if _parse_string(profile[name], f"{path}.{name}") != expected:
                _fail(f"{path}.{name}", "invalid_value")
        expected_units = _UNITS[kind]
        units_path = f"{path}.units"
        _require_shape(profile["units"], units_path, tuple(expected_units))
        if profile["units"] != expected_units:
            _fail(units_path, "invalid_value")
        copied = profile.copy()
        profiles.append(copied)
        by_id[profile_id] = copied
    return profiles, by_id


def _definitions(raw: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate definitions and return retained protocol and tick IDs."""
    _require_shape(raw, "definitions", _DEFINITION_FIELDS)
    definitions = raw
    _require_shape(
        definitions["greek_method"], "definitions.greek_method", _METHOD_FIELDS
    )
    method = definitions["greek_method"]
    expected = {
        "method_id": FIXTURE_GREEK_METHOD.method_id,
        "method_version": FIXTURE_GREEK_METHOD.method_version,
        "assumptions_id": FIXTURE_GREEK_METHOD.assumptions_id,
        "method_spec_hash": FIXTURE_GREEK_METHOD.method_spec_hash,
    }
    for name in ("method_id", "method_version", "assumptions_id"):
        _parse_string(method[name], f"definitions.greek_method.{name}")
    _hash(
        method["method_spec_hash"], "definitions.greek_method.method_spec_hash"
    )
    if method != expected:
        _fail("definitions.greek_method", "invalid_value")
    protocols = _list(
        definitions["coherence_protocol_ids"],
        "definitions.coherence_protocol_ids",
    )
    allowed_protocols = tuple(protocol for _, protocol in _COHERENCE_PROTOCOLS)
    if (
        any(type(protocol) is not str for protocol in protocols)
        or len(set(protocols)) != len(protocols)
        or any(protocol not in allowed_protocols for protocol in protocols)
    ):
        _fail("definitions.coherence_protocol_ids", "invalid_value")
    tick_ids = _list(definitions["tick_definition_ids"], "definitions.tick_definition_ids")
    if (
        any(type(identifier) is not str for identifier in tick_ids)
        or len(set(tick_ids)) != len(tick_ids)
        or any(identifier not in {item[0] for item in _TICK_DEFINITIONS} for identifier in tick_ids)
    ):
        _fail("definitions.tick_definition_ids", "invalid_value")
    return tuple(protocols), tuple(tick_ids)


def _members(
    raw: object, profiles: dict[str, dict[str, object]]
) -> tuple[VerifiedFixtureMember, ...]:
    """Validate member framing, references, and canonical raw-body hashes."""
    values = _list(raw, "members")
    members: list[VerifiedFixtureMember] = []
    record_ids: set[str] = set()
    for index, value in enumerate(values):
        path = f"members[{index}]"
        _require_shape(value, path, _MEMBER_FIELDS)
        member = value
        record_id = _parse_string(member["record_id"], f"{path}.record_id")
        if record_id in record_ids:
            _fail(f"{path}.record_id", "duplicate_id")
        record_ids.add(record_id)
        kind = _parse_token(member["kind"], f"{path}.kind", _KINDS)
        profile_id = _parse_string(member["profile_id"], f"{path}.profile_id")
        profile = profiles.get(profile_id)
        if profile is None:
            _fail(f"{path}.profile_id", "unknown_profile")
        if profile["kind"] != kind:
            _fail(f"{path}.profile_id", "kind_mismatch")
        raw_body_path = f"{path}.raw_body"
        if type(member["raw_body"]) is not dict:
            _fail(raw_body_path, "expected_exact_dict")
        raw_body = member["raw_body"]
        raw_body_bytes = _canonical_or_fail(raw_body, f"{path}.raw_body")
        raw_hash = _hash(member["raw_hash"], f"{path}.raw_hash")
        if hashlib.sha256(raw_body_bytes).hexdigest() != raw_hash:
            _fail(f"{path}.raw_hash", "hash_mismatch")
        envelope = _envelope(member["envelope"], path, kind, profile["stream_id"])
        retained = object.__new__(VerifiedFixtureMember)
        for name, retained_value in (
            ("record_id", record_id), ("kind", kind), ("profile_id", profile_id),
            ("raw_hash", raw_hash), ("raw_body_bytes", raw_body_bytes),
            ("envelope_bytes", _canonical_or_fail(envelope, f"{path}.envelope")),
        ):
            object.__setattr__(retained, name, retained_value)
        members.append(retained)
    return tuple(members)


def _envelope(
    raw: object, member_path: str, kind: str, expected_stream_id: object
) -> dict[str, object]:
    """Validate one exact eight-field simulated ingestion envelope."""
    path = f"{member_path}.envelope"
    _require_shape(raw, path, _ENVELOPE_FIELDS)
    envelope = raw
    for name in ("event_id", "raw_ref", "stream_id"):
        _parse_string(envelope[name], f"{path}.{name}")
    _parse_timestamp(
        envelope["simulated_received_at"], f"{path}.simulated_received_at"
    )
    if envelope["stream_id"] != expected_stream_id:
        _fail(f"{path}.stream_id", "stream_mismatch")
    sequence = envelope["receive_sequence"]
    if sequence is not None and (type(sequence) is not int or sequence < 0):
        _fail(
            f"{path}.receive_sequence",
            "invalid_type" if type(sequence) is not int else "invalid_value",
        )
    supersedes = envelope["supersedes_record_id"]
    if supersedes is not None:
        _parse_string(supersedes, f"{path}.supersedes_record_id")
    if kind in (
        "volume_partition", "feature_normalization",
        "quote_coherence", "tick_rule", "exchange_session", "instrument_tradability",
        "provider_contract_mapping", "contract_reference",
    ):
        for name in ("contract", "metadata"):
            if envelope[name] is not None:
                _fail(f"{path}.{name}", "invalid_value")
    elif kind in ("underlying_quote", "underlying_bar"):
        if envelope["contract"] is not None:
            _fail(f"{path}.contract", "invalid_value")
        if type(envelope["metadata"]) is not dict:
            _fail(f"{path}.metadata", "expected_exact_dict")
    elif kind == "greek_observation":
        if type(envelope["contract"]) is not dict:
            _fail(f"{path}.contract", "expected_exact_dict")
        if envelope["metadata"] is not None:
            _fail(f"{path}.metadata", "invalid_value")
    else:
        for name in ("contract", "metadata"):
            if type(envelope[name]) is not dict:
                _fail(f"{path}.{name}", "expected_exact_dict")
    return envelope.copy()


def _load_catalog() -> _CatalogState:
    """Read the one fixed package resource into immutable private state once."""
    try:
        payload = resources.files(__package__).joinpath("_fixture_catalog.json").read_bytes()
    except MemoryError:
        return _CatalogState(error_code="catalog_resource_limit")
    except OSError:
        return _CatalogState(error_code="catalog_unavailable")
    return _parse_catalog(payload)


def _parse_catalog(payload: bytes) -> _CatalogState:
    """Parse closed literal catalog bytes without exposing a runtime override."""
    decoded, code = _decode_json(payload)
    if code is not None:
        catalog_code = "catalog_resource_limit" if code == "resource_limit" else "catalog_invalid"
        return _CatalogState(error_code=catalog_code)
    try:
        _require_shape(decoded, "$catalog", _CATALOG_FIELDS)
        root = decoded
        _exact_integer(root["catalog_schema_version"], "catalog_schema_version", 1)
        descriptors: list[_FixtureDescriptor] = []
        fixture_ids: set[str] = set()
        for index, item in enumerate(_list(root["fixtures"], "fixtures")):
            path = f"fixtures[{index}]"
            _require_shape(item, path, _DESCRIPTOR_FIELDS)
            raw = item
            fixture_id = _parse_string(raw["fixture_id"], f"{path}.fixture_id")
            if fixture_id in fixture_ids:
                _fail(f"{path}.fixture_id", "duplicate_id")
            fixture_ids.add(fixture_id)
            payload_version = _integer(raw["payload_schema_version"], f"{path}.payload_schema_version")
            normalization_version = _integer(raw["normalization_version"], f"{path}.normalization_version")
            if payload_version != 1 or normalization_version != 1:
                _fail(path, "invalid_value")
            descriptors.append(_FixtureDescriptor(
                fixture_id,
                _parse_string(raw["generator_id"], f"{path}.generator_id"),
                _parse_string(raw["generator_version"], f"{path}.generator_version"),
                payload_version,
                normalization_version,
                _hash(raw["expected_payload_sha256"], f"{path}.expected_payload_sha256"),
            ))
    except (_AdmissionFailure, _InvalidInput):
        return _CatalogState(error_code="catalog_invalid")
    return _CatalogState(
        tuple(descriptors), hashlib.sha256(payload).hexdigest(), None
    )


def _decode_json(payload: bytes) -> tuple[object | None, str | None]:
    """Decode strict UTF-8 JSON with duplicate and non-finite numbers rejected."""
    if type(payload) is not bytes:
        raise TypeError("payload must be bytes")
    try:
        text = payload.decode("utf-8")
        return json.loads(
            text, object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant, parse_float=_json_float,
            parse_int=_json_integer,
        ), None
    except UnicodeDecodeError:
        return None, "invalid_utf8"
    except _DuplicateJsonKey:
        return None, "duplicate_key"
    except _NonfiniteJsonNumber:
        return None, "nonfinite_number"
    except (MemoryError, RecursionError, _JsonResourceLimit):
        return None, "resource_limit"
    except (json.JSONDecodeError, ValueError):
        return None, "invalid_json"


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one strict object while rejecting every duplicate key."""
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey
        value[key] = item
    return value


def _reject_json_constant(_: str) -> Never:
    """Reject non-JSON numeric constants accepted by Python's decoder."""
    raise _NonfiniteJsonNumber


def _json_float(value: str) -> float:
    """Decode one JSON float only when its binary result remains finite."""
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _NonfiniteJsonNumber
    return parsed


def _json_integer(value: str) -> int:
    """Decode one JSON integer with interpreter limits mapped to safe evidence."""
    try:
        return int(value)
    except ValueError:
        raise _JsonResourceLimit from None


def _list(value: object, path: str) -> list[object]:
    """Require one exact JSON array."""
    if type(value) is not list:
        _fail(path, "invalid_type")
    return value


def _integer(value: object, path: str) -> int:
    """Require one exact integer excluding booleans."""
    if type(value) is not int:
        _fail(path, "invalid_type")
    return value


def _exact_integer(value: object, path: str, expected: int) -> None:
    """Require one exact integer literal."""
    if _integer(value, path) != expected:
        _fail(path, "invalid_value")


def _hash(value: object, path: str) -> str:
    """Require one canonical lowercase SHA-256 string."""
    parsed = _parse_string(value, path)
    if len(parsed) != 64 or any(character not in _HASH_DIGITS for character in parsed):
        _fail(path, "invalid_value")
    return parsed


def _canonical_bytes(value: object) -> bytes:
    """Encode one decoded JSON value using compact sorted ASCII JSON."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_or_fail(value: object, path: str) -> bytes:
    """Map bounded canonicalization failures to safe owner evidence."""
    try:
        return _canonical_bytes(value)
    except (MemoryError, RecursionError):
        _fail(path, "resource_limit")
    except (TypeError, ValueError):
        _fail(path, "invalid_value")


def _decode_verified_dict(payload: bytes) -> dict[str, object]:
    """Decode a fresh dictionary from previously verified canonical bytes."""
    value, code = _decode_json(payload)
    if code is not None or type(value) is not dict:
        raise RuntimeError("verified fixture member cannot be decoded")
    return value


def _allowed_codes(field_name: str) -> tuple[str, ...]:
    """Return the fixed rejection codes allowed for one bounded schema path."""
    external = {
        "catalog": (
            "catalog_unavailable", "catalog_invalid", "catalog_resource_limit",
        ),
        "fixture_id": (
            "missing", "invalid_type", "invalid_value", "unknown_fixture",
        ),
        "payload": (
            "invalid_type", "hash_mismatch", "invalid_utf8", "duplicate_key",
            "nonfinite_number", "invalid_json", "resource_limit",
        ),
        "$": ("expected_exact_dict", "unknown_fields"),
    }
    if field_name in external:
        return external[field_name]
    schema_path = re.fullmatch(
        r"(?:schema_version|normalization_version|generator_id|generator_version|"
        r"assembled_at|generator_source_ref|origin|permitted_use|"
        r"modeled_source_profiles(?:\[[0-9]+\](?:\.(?:profile_id|kind|source|"
        r"stream_id|feed_class|fidelity|availability_basis|record_identity_rule|"
        r"units(?:\.(?:price|size|delta|iv|rate|dividend|volume|vwap_numerator|vwap_denominator))?))?)?|"
        r"definitions(?:\.(?:coherence_protocol_ids|tick_definition_ids|"
        r"greek_method(?:\.(?:method_id|method_version|assumptions_id|"
        r"method_spec_hash))?))?|members(?:\[[0-9]+\](?:\.(?:record_id|kind|"
        r"profile_id|raw_hash|raw_body|envelope(?:\.(?:event_id|raw_ref|"
        r"simulated_received_at|stream_id|receive_sequence|supersedes_record_id|"
        r"contract|metadata))?))?)?)",
        field_name,
    )
    if schema_path is None:
        return ()
    return (
        "expected_exact_dict", "unknown_fields", "missing", "invalid_type",
        "invalid_value", "invalid_timestamp", "resource_limit", "duplicate_id",
        "unknown_profile", "kind_mismatch", "stream_mismatch", "hash_mismatch",
    )


def _rejection(
    event_id: str,
    raw_ref: str,
    received_at: datetime,
    field_name: str,
    code: str,
) -> FixtureVerification:
    """Create one exclusive safe rejection result."""
    return FixtureVerification(rejection=FixtureInputRejection(
        event_id, received_at, raw_ref, field_name, code
    ))


def _fail(path: str, code: str) -> Never:
    """Stop private validation with one safe path and code."""
    raise _AdmissionFailure(path, code)


_CATALOG = _load_catalog()
