"""Normalize order-report claims and reprove code-owned source members."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib

from ._input_parsing import (
    _InvalidInput, _parse_contract_id, _parse_decimal, _parse_timestamp,
    _parse_token, _require_shape,
)
from ._validation import _trusted_datetime
from .admission import (
    VerifiedFixtureManifest, VerifiedFixtureMember, _canonical_bytes,
    verify_fixture_bundle,
)
from .bar_inputs import _UnsupportedIdentity, _identity_decimal
from .bundle_inputs import _make
from .config import _snapshot_hash
from .contracts import ContractId


_FIELDS = (
    "schema_version", "account_id", "source", "provider_event_id",
    "broker_order_id", "client_order_id", "replaces_broker_order_id",
    "replaces_client_order_id", "replaced_by_broker_order_id",
    "replaced_by_client_order_id", "decision_id", "contract", "role",
    "event_at", "available_at", "provider_sequence", "status", "pending",
    "terminal", "quantity_basis", "raw_filled_quantity",
    "cumulative_fill_notional_usd", "cumulative_fees_usd",
)
_IDS = (
    "account_id", "source", "provider_event_id", "broker_order_id",
    "client_order_id", "replaces_broker_order_id", "replaces_client_order_id",
    "replaced_by_broker_order_id", "replaced_by_client_order_id", "decision_id",
    "status",
)
_OPTIONAL_IDS = frozenset(_IDS) - {"account_id", "source"}
_DECIMALS = ("raw_filled_quantity", "cumulative_fill_notional_usd", "cumulative_fees_usd")
_MAX_RAW_BYTES = 8192
_MAX_ID = 256
_MAX_DECIMAL = 128
_MAX_SEQUENCE = 2**63 - 1


class _OrderFailure(Exception):
    """This class represents a private safe field and code parser failure."""


@dataclass(frozen=True, init=False)
class OrderUpdate:
    """This class represents a normalized order-report claim without source authority."""

    account_id: str
    source: str
    provider_event_id: str | None
    broker_order_id: str | None
    client_order_id: str | None
    replaces_broker_order_id: str | None
    replaces_client_order_id: str | None
    replaced_by_broker_order_id: str | None
    replaced_by_client_order_id: str | None
    decision_id: str | None
    contract: ContractId | None
    role: str
    event_at: datetime | None
    available_at: datetime | None
    provider_sequence: int | None
    status: str | None
    pending: bool | None
    terminal: bool | None
    quantity_basis: str
    raw_filled_quantity: Decimal | None
    safe_cumulative_quantity: int | None
    cumulative_fill_notional_usd: Decimal | None
    cumulative_fees_usd: Decimal | None
    normalized_event_id: str
    semantic_content_hash: str
    identity_status: str
    event_id: str
    raw_ref: str
    received_at: datetime
    receive_sequence: int | None

    def __init__(self) -> None:
        """Block caller-authored quantities and semantic identities.

        :returns: None.
        :raises TypeError: Always; use normalize_order_update.
        """
        raise TypeError("OrderUpdate values come from normalization")


@dataclass(frozen=True, init=False)
class OrderInputRejection:
    """This class represents one bounded order-report normalization failure."""

    event_id: str
    raw_ref: str
    received_at: datetime
    field: str
    code: str

    def __init__(self) -> None:
        """Block caller-authored diagnostic evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_order_update.
        """
        raise TypeError("order input rejections come from normalization")


@dataclass(frozen=True, init=False)
class OrderInputValidation:
    """This class represents exactly one order claim or bounded rejection."""

    value: OrderUpdate | None
    rejection: OrderInputRejection | None

    def __init__(self) -> None:
        """Block caller-authored exclusive normalization outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_order_update.
        """
        raise TypeError("order input validations come from normalization")


@dataclass(frozen=True, init=False)
class OrderInputAdmission:
    """This class represents an attempted member proof and its fresh outcome."""

    supplied_fixture: VerifiedFixtureManifest
    record_id: str
    manifest: VerifiedFixtureManifest | None
    member: VerifiedFixtureMember | None
    validation: OrderInputValidation | None
    source_failure: tuple[str, str] | None

    def __init__(self) -> None:
        """Block caller-authored source authority and failure evidence.

        :returns: None.
        :raises TypeError: Always; use admit_order_update.
        """
        raise TypeError("order admissions come from actual fixture reproof")


def _string(value: object, field: str, *, nullable: bool = False) -> str | None:
    """Validate one bounded printable identifier or status claim.

    :param value:    Untrusted scalar value.
    :param field:    Safe field name for failure evidence.
    :param nullable: Whether explicit null is allowed.
    :returns:       The exact string or allowed null.
    :raises _OrderFailure: If the value is absent, malformed or oversized.
    """
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise _OrderFailure(field, "invalid_type")
    if not value or len(value) > _MAX_ID or not value.isprintable():
        raise _OrderFailure(field, "invalid_value")
    return value


def _decimal(value: object, field: str) -> Decimal | None:
    """Parse one optional signed fixed-point observation without policy limits.

    :param value: Untrusted decimal string or null.
    :param field: Safe field name for failure evidence.
    :returns:    A finite Decimal or null.
    :raises _OrderFailure: If syntax or identity representation is unsupported.
    """
    if value is None:
        return None
    if type(value) is str and len(value) > _MAX_DECIMAL:
        raise _OrderFailure(field, "too_long")
    try:
        parsed = _parse_decimal(value, field)
        _identity_decimal(parsed)
        return parsed
    except _InvalidInput as failure:
        raise _OrderFailure(*failure.args) from None
    except _UnsupportedIdentity:
        raise _OrderFailure(field, "representation_unsupported") from None


def _timestamp(value: object, field: str) -> datetime | None:
    """Parse an optional aware source clock without receipt fallback.

    :param value: Untrusted ISO time or null.
    :param field: Safe field name for failure evidence.
    :returns:    UTC datetime or null.
    :raises _OrderFailure: If the source time is malformed or oversized.
    """
    if type(value) is str and len(value) > _MAX_ID:
        raise _OrderFailure(field, "too_long")
    try:
        return _parse_timestamp(value, field, nullable=True)
    except _InvalidInput as failure:
        raise _OrderFailure(*failure.args) from None


def _contract(value: object) -> ContractId | None:
    """Parse the existing exact contract identity, retaining unknown as null.

    :param value: Untrusted six-field contract or null.
    :returns:    Parsed exact ContractId or null.
    :raises _OrderFailure: If any contract identity field is malformed.
    """
    if value is None:
        return None
    try:
        _require_shape(value, "contract", (
            "underlying", "expiry", "right", "strike", "multiplier", "deliverable_id"))
        for name in ("underlying", "deliverable_id", "strike"):
            item = value[name]
            if type(item) is str and len(item) > (_MAX_DECIMAL if name == "strike" else _MAX_ID):
                raise _OrderFailure("contract." + name, "too_long")
        return _parse_contract_id(value)
    except _InvalidInput as failure:
        raise _OrderFailure(*failure.args) from None
    except (TypeError, ValueError):
        raise _OrderFailure("contract", "invalid_value") from None


def _parse_body(raw: object) -> dict[str, object]:
    """Parse one exact body and derive only safe semantics.

    :param raw: Untrusted exact dictionary.
    :returns:   Normalized fields for an OrderUpdate.
    :raises _OrderFailure: If closed shape or representation fails.
    """
    try:
        _require_shape(raw, "$", _FIELDS)
    except _InvalidInput as failure:
        raise _OrderFailure(*failure.args) from None
    try:
        if len(_canonical_bytes(raw)) > _MAX_RAW_BYTES:
            raise _OrderFailure("$", "too_large")
    except (TypeError, ValueError, OverflowError, MemoryError, RecursionError):
        raise _OrderFailure("$", "invalid_value") from None
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise _OrderFailure("schema_version", "invalid_value")
    fields = {name: _string(raw[name], name, nullable=name in _OPTIONAL_IDS) for name in _IDS}
    fields["contract"] = _contract(raw["contract"])
    for name, allowed in (("role", ("buy_entry", "sell_exit", "unknown")),
                          ("quantity_basis", ("cumulative", "incremental", "unknown"))):
        try:
            fields[name] = _parse_token(raw[name], name, allowed)
        except _InvalidInput as failure:
            raise _OrderFailure(*failure.args) from None
    for name in ("event_at", "available_at"):
        fields[name] = _timestamp(raw[name], name)
    sequence = raw["provider_sequence"]
    if sequence is not None and (type(sequence) is not int or sequence < 0 or sequence > _MAX_SEQUENCE):
        raise _OrderFailure("provider_sequence", "invalid_value")
    fields["provider_sequence"] = sequence
    for name in ("pending", "terminal"):
        value = raw[name]
        if value is not None and type(value) is not bool:
            raise _OrderFailure(name, "invalid_type")
        fields[name] = value
    for name in _DECIMALS:
        fields[name] = _decimal(raw[name], name)
    quantity = fields["raw_filled_quantity"]
    safe = None
    if (fields["quantity_basis"] == "cumulative" and quantity is not None
            and quantity >= 0 and quantity == quantity.to_integral_value()
            and quantity <= _MAX_SEQUENCE):
        safe = int(quantity)
    fields["safe_cumulative_quantity"] = safe
    contract = fields["contract"]
    snapshot = {name: fields[name] for name in _FIELDS if name not in
                ("schema_version", "contract", "event_at", "available_at", *_DECIMALS)}
    snapshot["contract"] = None if contract is None else dict(
        underlying=contract.underlying, expiry=contract.expiry.isoformat(),
        right=contract.right, strike=_identity_decimal(contract.strike),
        multiplier=contract.multiplier, deliverable_id=contract.deliverable_id)
    for name in ("event_at", "available_at"):
        snapshot[name] = None if fields[name] is None else fields[name].isoformat()
    for name in _DECIMALS:
        snapshot[name] = _identity_decimal(fields[name])
    fields["semantic_content_hash"] = _snapshot_hash(snapshot)
    identity = (dict(source=fields["source"], account_id=fields["account_id"],
                     provider_event_id=fields["provider_event_id"])
                if fields["provider_event_id"] is not None else snapshot)
    fields["normalized_event_id"] = _snapshot_hash(identity)
    fields["identity_status"] = ("linked" if fields["broker_order_id"] is not None
                                 or fields["client_order_id"] is not None else "unlinked")
    return fields


def normalize_order_update(raw: object, *, event_id: str, raw_ref: str,
                           received_at: datetime,
                           receive_sequence: int | None = None) -> OrderInputValidation:
    """Normalize one untrusted exact-dictionary claim without source authority.

    :param raw:              Untrusted complete order-report dictionary.
    :param event_id:         Trusted receipt identifier.
    :param raw_ref:          Trusted original input locator.
    :param received_at:      Trusted aware receipt time.
    :param receive_sequence: Optional trusted local receipt sequence.
    :returns:                Exclusive normalized claim or safe rejection.
    :raises TypeError:       If a trusted argument has the wrong exact type.
    :raises ValueError:      If trusted receipt evidence is invalid.
    """
    for name, value in (("event_id", event_id), ("raw_ref", raw_ref)):
        if type(value) is not str:
            raise TypeError(f"{name} must be an exact string")
        if not value or len(value) > _MAX_ID or not value.isprintable():
            raise ValueError(f"{name} must be a bounded nonempty printable string")
    if type(received_at) is not datetime:
        raise TypeError("received_at must be an exact datetime")
    received_at = _trusted_datetime("received_at", received_at)
    if receive_sequence is not None and (type(receive_sequence) is not int
                                         or receive_sequence < 0 or receive_sequence > _MAX_SEQUENCE):
        raise ValueError("receive_sequence must be a bounded nonnegative integer or None")
    try:
        fields = _parse_body(raw)
    except _OrderFailure as failure:
        return _make(OrderInputValidation, value=None, rejection=_make(
            OrderInputRejection, event_id=event_id, raw_ref=raw_ref,
            received_at=received_at, field=failure.args[0], code=failure.args[1]))
    return _make(OrderInputValidation, value=_make(OrderUpdate, **fields,
        event_id=event_id, raw_ref=raw_ref, received_at=received_at,
        receive_sequence=receive_sequence), rejection=None)


def _retained_member_matches(old: VerifiedFixtureMember,
                             fresh: VerifiedFixtureMember) -> bool:
    """Compare one retained member through exact primitive fields only.

    :param old:   Supplied member that may have damaged attributes.
    :param fresh: Member decoded from newly admitted bytes.
    :returns:     Whether bounded retained framing equals fresh framing.
    """
    if type(old) is not VerifiedFixtureMember:
        return False
    for name in ("record_id", "kind", "profile_id", "raw_hash",
                 "raw_body_bytes", "envelope_bytes"):
        try:
            left, right = object.__getattribute__(old, name), getattr(fresh, name)
        except AttributeError:
            return False
        if type(left) is not type(right) or left != right:
            return False
    return True


def _retained_method_matches(old: object, fresh: object) -> bool:
    """Compare method evidence without invoking nested caller comparison hooks.

    :param old:   Supplied method with potentially damaged fields.
    :param fresh: Reproved concrete method.
    :returns:     Whether exact primitive method fields match.
    """
    if type(old) is not type(fresh):
        return False
    try:
        return all(type(object.__getattribute__(old, name)) is type(value)
                   and object.__getattribute__(old, name) == value
                   for name, value in vars(fresh).items())
    except (AttributeError, TypeError):
        return False


def _reproved_fixture(old: VerifiedFixtureManifest) -> VerifiedFixtureManifest | None:
    """Re-admit supplied bytes and reject altered retained framing safely.

    :param old: Supplied concrete manifest whose nested content is untrusted.
    :returns:   Fresh manifest when all retained source framing matches.
    """
    try:
        attributes = {name: object.__getattribute__(old, name) for name in (
            "fixture_id", "payload_bytes", "payload_sha256", "catalog_sha256",
            "event_id", "raw_ref", "received_at", "members",
            "modeled_source_profiles_bytes", "generator_id", "generator_version",
            "assembled_at", "generator_source_ref", "schema_version",
            "normalization_version", "greek_method", "coherence_protocol_ids",
            "tick_definition_ids", "origin", "fidelity_tier", "permitted_use",
            "operational_allowed", "economic_allowed")}
        if (type(attributes["fixture_id"]) is not str or not attributes["fixture_id"]
                or len(attributes["fixture_id"]) > _MAX_ID
                or type(attributes["payload_bytes"]) is not bytes
                or len(attributes["payload_bytes"]) > 8_000_000
                or type(attributes["event_id"]) is not str
                or type(attributes["raw_ref"]) is not str
                or type(attributes["received_at"]) is not datetime
                or type(attributes["members"]) is not tuple
                or len(attributes["members"]) > 10_000):
            return None
        checked = verify_fixture_bundle(attributes["fixture_id"],
            attributes["payload_bytes"], event_id=attributes["event_id"],
            raw_ref=attributes["raw_ref"], received_at=attributes["received_at"])
        fresh = checked.value
        if fresh is None:
            return None
        for name, left in attributes.items():
            if name == "members":
                if len(left) != len(fresh.members) or any(
                    not _retained_member_matches(a, b) for a, b in zip(left, fresh.members)):
                    return None
            elif name == "greek_method":
                if not _retained_method_matches(left, fresh.greek_method):
                    return None
            elif name in ("coherence_protocol_ids", "tick_definition_ids"):
                right = getattr(fresh, name)
                if (type(left) is not tuple or len(left) != len(right)
                        or any(type(a) is not str or a != b for a, b in zip(left, right))):
                    return None
            else:
                right = getattr(fresh, name)
                if type(left) is not type(right) or left != right:
                    return None
        return fresh
    except (AttributeError, TypeError, ValueError, OverflowError, MemoryError, RecursionError):
        return None


def admit_order_update(fixture: VerifiedFixtureManifest,
                       record_id: str) -> OrderInputAdmission:
    """Reprove actual fixture/member bytes before normalizing an order report.

    :param fixture:   Supplied prior fixture admission to verify afresh.
    :param record_id: Trusted exact member identifier attempted by the caller.
    :returns:         Fresh manifest/member/validation or safe source failure.
    :raises TypeError: If a trusted top-level argument has the wrong type.
    :raises ValueError: If record_id is empty, nonprintable or oversized.
    """
    if type(fixture) is not VerifiedFixtureManifest:
        raise TypeError("fixture must be a VerifiedFixtureManifest")
    if type(record_id) is not str:
        raise TypeError("record_id must be an exact string")
    if not record_id or len(record_id) > _MAX_ID or not record_id.isprintable():
        raise ValueError("record_id must be a bounded nonempty printable string")

    def failed(field: str, code: str) -> OrderInputAdmission:
        """Retain the attempted input with one closed source diagnostic.

        :param field: Safe source-boundary field name.
        :param code:  Safe source-boundary reason.
        :returns:     Failed factory-only admission.
        """
        return _make(OrderInputAdmission, supplied_fixture=fixture, record_id=record_id,
                     manifest=None, member=None, validation=None,
                     source_failure=(field, code))

    fresh = _reproved_fixture(fixture)
    if fresh is None:
        return failed("fixture", "admission_failed")
    member = next((item for item in fresh.members if item.record_id == record_id), None)
    if member is None:
        return failed("record_id", "unknown_member")
    if member.kind != "order_update":
        return failed("record_id", "kind_mismatch")
    if hashlib.sha256(member.raw_body_bytes).hexdigest() != member.raw_hash:
        return failed("member", "raw_hash_mismatch")
    try:
        profile = next((item for item in fresh.decode_modeled_source_profiles()
                        if item["profile_id"] == member.profile_id), None)
        envelope = member.decode_envelope()
        raw = member.decode_raw_body()
        if (profile is None or profile["kind"] != "order_update"
                or profile["stream_id"] != envelope["stream_id"]
                or (type(raw.get("source")) is str and profile["source"] != raw["source"])):
            return failed("profile", "profile_mismatch")
        validation = normalize_order_update(raw, event_id=envelope["event_id"],
            raw_ref=envelope["raw_ref"],
            received_at=_parse_timestamp(envelope["simulated_received_at"],
                                         "simulated_received_at"),
            receive_sequence=envelope["receive_sequence"])
    except (AttributeError, KeyError, TypeError, ValueError, _InvalidInput):
        return failed("member", "retained_content_mismatch")
    return _make(OrderInputAdmission, supplied_fixture=fixture, record_id=record_id,
                 manifest=fresh, member=member, validation=validation,
                 source_failure=None)
