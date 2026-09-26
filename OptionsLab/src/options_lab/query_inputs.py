"""Reprove one synthetic atomic account query and its source members."""

from dataclasses import dataclass
from datetime import datetime

from ._input_parsing import _InvalidInput, _parse_timestamp, _parse_token, _require_shape
from .account import AccountSnapshot
from .account_inputs import normalize_account
from .admission import VerifiedFixtureManifest, VerifiedFixtureMember, _canonical_bytes
from .bundle_inputs import _make
from .order_inputs import OrderInputAdmission, _reproved_fixture, admit_order_update


_FIELDS = ("schema_version", "account_id", "source", "request_id", "response_id",
           "requested_at", "captured_at", "responded_at", "available_at",
           "completed", "account_record_id", "holdings_coverage", "orders_coverage",
           "included_order_record_ids", "issued_actions", "order_ref_aliases")
_ACTION_FIELDS = ("action_id", "kind", "issued_at", "resolution",
                  "broker_order_id", "client_order_id")
_ALIAS_FIELDS = ("order_ref", "alias_kind", "alias_id", "client_order_id")
_QUERY_PROFILE = "alpaca-query-atomic-v1"
_MAX_ID = 256
_MAX_ROWS = 256
_MAX_BODY = 8192


class _QueryFailure(Exception):
    """This class represents one safe source field and rejection code."""


@dataclass(frozen=True, init=False)
class BrokerQuery:
    """This class represents a normalized synthetic source query claim."""

    account_id: str
    source: str
    request_id: str
    response_id: str
    requested_at: datetime
    captured_at: datetime
    responded_at: datetime
    available_at: datetime
    completed: bool
    account_record_id: str
    holdings_coverage: str
    orders_coverage: str
    included_order_record_ids: tuple[str, ...]
    issued_actions: tuple[tuple[str, str, datetime, str, str | None, str | None], ...]
    order_ref_aliases: tuple[tuple[str, str, str, str | None], ...]

    def __init__(self) -> None:
        """Block caller-authored source coverage.

        :returns: None.
        :raises TypeError: Always; admit_broker_query constructs claims.
        """
        raise TypeError("broker queries come from source admission")


@dataclass(frozen=True, init=False)
class BrokerQueryAdmission:
    """This class represents one attempted source query and its fresh proof."""

    supplied_fixture: VerifiedFixtureManifest
    record_id: str
    manifest: VerifiedFixtureManifest | None
    member: VerifiedFixtureMember | None
    query: BrokerQuery | None
    account: AccountSnapshot | None
    included_orders: tuple[OrderInputAdmission, ...]
    source_failure: tuple[str, str] | None

    def __init__(self) -> None:
        """Block caller-authored admission outcomes.

        :returns: None.
        :raises TypeError: Always; admit_broker_query constructs admissions.
        """
        raise TypeError("broker query admissions come from source admission")


def _id(value: object, field: str, *, nullable: bool = False) -> str | None:
    """Parse one bounded printable source identity.

    :param value: Untrusted source value.
    :param field: Safe field name for a rejection.
    :param nullable: Whether explicit null is supported.
    :returns: Exact string or allowed None.
    :raises _QueryFailure: If the source identity is malformed.
    """
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise _QueryFailure(field, "invalid_type")
    if not value or len(value) > _MAX_ID or not value.isprintable():
        raise _QueryFailure(field, "invalid_value")
    return value


def _rows(value: object, field: str) -> list[object]:
    """Bound a source list before nested parsing.

    :param value: Untrusted source list.
    :param field: Safe field name for a rejection.
    :returns: Exact bounded list.
    :raises _QueryFailure: If the list is absent, oversized or malformed.
    """
    if type(value) is not list:
        raise _QueryFailure(field, "invalid_type")
    if len(value) > _MAX_ROWS:
        raise _QueryFailure(field, "resource_limit")
    return value


def _parse(raw: object) -> BrokerQuery:
    """Normalize one exact query body without granting flat authority.

    :param raw: Verified member's decoded source body.
    :returns: Factory-only query claim.
    :raises _InvalidInput: If a common source field is malformed.
    :raises _QueryFailure: If a query-specific field is malformed.
    """
    _require_shape(raw, "$", _FIELDS)
    if len(_canonical_bytes(raw)) > _MAX_BODY:
        raise _QueryFailure("$", "resource_limit")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise _QueryFailure("schema_version", "invalid_value")
    fields = {name: _id(raw[name], name) for name in
              ("account_id", "source", "request_id", "response_id", "account_record_id")}
    for name in ("requested_at", "captured_at", "responded_at", "available_at"):
        fields[name] = _parse_timestamp(raw[name], name)
    if not (fields["requested_at"] == fields["captured_at"]
            <= fields["responded_at"] <= fields["available_at"]):
        raise _QueryFailure("captured_at", "clock_mismatch")
    if type(raw["completed"]) is not bool:
        raise _QueryFailure("completed", "invalid_type")
    fields["completed"] = raw["completed"]
    for name in ("holdings_coverage", "orders_coverage"):
        fields[name] = _parse_token(raw[name], name, ("complete", "partial", "unknown"))
    included = tuple(_id(item, "included_order_record_ids") for item in
                     _rows(raw["included_order_record_ids"], "included_order_record_ids"))
    if len(set(included)) != len(included):
        raise _QueryFailure("included_order_record_ids", "duplicate_id")
    fields["included_order_record_ids"] = included
    actions = []
    for row in _rows(raw["issued_actions"], "issued_actions"):
        _require_shape(row, "issued_actions", _ACTION_FIELDS)
        action_id = _id(row["action_id"], "issued_actions.action_id")
        kind = _parse_token(row["kind"], "issued_actions.kind", ("submit", "cancel", "replace"))
        issued_at = _parse_timestamp(row["issued_at"], "issued_actions.issued_at")
        if issued_at > fields["captured_at"]:
            raise _QueryFailure("issued_actions.issued_at", "after_capture")
        resolution = _parse_token(row["resolution"], "issued_actions.resolution",
                                  ("reflected", "definitive_no_effect", "unresolved"))
        actions.append((action_id, kind, issued_at, resolution,
            _id(row["broker_order_id"], "issued_actions.broker_order_id", nullable=True),
            _id(row["client_order_id"], "issued_actions.client_order_id", nullable=True)))
    if len({row[0] for row in actions}) != len(actions):
        raise _QueryFailure("issued_actions", "duplicate_id")
    fields["issued_actions"] = tuple(actions)
    aliases = []
    for row in _rows(raw["order_ref_aliases"], "order_ref_aliases"):
        _require_shape(row, "order_ref_aliases", _ALIAS_FIELDS)
        aliases.append((_id(row["order_ref"], "order_ref_aliases.order_ref"),
            _parse_token(row["alias_kind"], "order_ref_aliases.alias_kind", ("broker", "client")),
            _id(row["alias_id"], "order_ref_aliases.alias_id"),
            _id(row["client_order_id"], "order_ref_aliases.client_order_id", nullable=True)))
    if (len({(row[1], row[2]) for row in aliases}) != len(aliases)
            or len({(row[0], row[1]) for row in aliases}) != len(aliases)):
        raise _QueryFailure("order_ref_aliases", "duplicate_id")
    fields["order_ref_aliases"] = tuple(aliases)
    return _make(BrokerQuery, **fields)


def _profile(manifest: VerifiedFixtureManifest, member: VerifiedFixtureMember,
             kind: str, source: str) -> bool:
    """Match an actual member to its source/profile/stream framing.

    :param manifest: Fresh catalog-admitted fixture.
    :param member: Fresh member to inspect.
    :param kind: Required member kind.
    :param source: Required source identity.
    :returns: Whether source and profile framing agree.
    :raises RuntimeError: If newly admitted profile bytes cannot be decoded.
    """
    profile = next((item for item in manifest.decode_modeled_source_profiles()
                    if item["profile_id"] == member.profile_id), None)
    envelope = member.decode_envelope()
    return (profile is not None and profile["kind"] == kind
            and profile["source"] == source
            and profile["stream_id"] == envelope["stream_id"])


def _mapping(query: BrokerQuery, account: AccountSnapshot,
             included: tuple[OrderInputAdmission, ...]) -> None:
    """Verify each typed account-order alias against an actual order report.

    :param query: Fresh query claim with typed mapping rows.
    :param account: Fresh normalized account member.
    :param included: Fresh same-fixture order admissions.
    :returns: None.
    :raises _QueryFailure: If any mapping lacks exact account/order identity.
    """
    for order_ref, kind, alias_id, client_id in query.order_ref_aliases:
        account_rows = [row for row in account.open_orders if row.order_ref == order_ref]
        if (len(account_rows) != 1 or account_rows[0].source != query.source
                or account_rows[0].client_order_id != client_id
                or account_rows[0].contract is None):
            raise _QueryFailure("order_ref_aliases", "account_order_mismatch")
        row = account_rows[0]
        expected_role = {"entry": "buy_entry", "exit": "sell_exit"}.get(row.role)
        if not any((admission.validation.value.contract == row.contract
                    and admission.validation.value.role == expected_role
                    and getattr(admission.validation.value,
                                "broker_order_id" if kind == "broker" else "client_order_id") == alias_id
                    and admission.validation.value.client_order_id == client_id)
                   for admission in included):
            raise _QueryFailure("order_ref_aliases", "order_alias_mismatch")


def admit_broker_query(fixture: VerifiedFixtureManifest,
                       record_id: str) -> BrokerQueryAdmission:
    """Reprove one fixed-protocol synthetic account query and its references.

    :param fixture: Supplied prior fixture admission to re-verify.
    :param record_id: Trusted exact query member identifier.
    :returns: Fresh complete admission or closed source failure.
    :raises TypeError: If a trusted top-level argument has the wrong type.
    :raises ValueError: If record_id is empty, nonprintable or oversized.
    """
    if type(fixture) is not VerifiedFixtureManifest:
        raise TypeError("fixture must be a VerifiedFixtureManifest")
    if type(record_id) is not str:
        raise TypeError("record_id must be an exact string")
    if not record_id or len(record_id) > _MAX_ID or not record_id.isprintable():
        raise ValueError("record_id must be a bounded nonempty printable string")

    def failed(field: str, code: str) -> BrokerQueryAdmission:
        """Retain only original carrier and one safe source failure.

        :param field: Safe source-boundary field.
        :param code: Closed source-boundary code.
        :returns: Factory-only failed admission.
        """
        return _make(BrokerQueryAdmission, supplied_fixture=fixture, record_id=record_id,
                     manifest=None, member=None, query=None, account=None,
                     included_orders=(), source_failure=(field, code))

    fresh = _reproved_fixture(fixture)
    if fresh is None:
        return failed("fixture", "admission_failed")
    try:
        member = next((row for row in fresh.members if row.record_id == record_id), None)
        if member is None:
            raise _QueryFailure("record_id", "unknown_member")
        if member.kind != "broker_query" or member.profile_id != _QUERY_PROFILE:
            raise _QueryFailure("record_id", "kind_or_protocol_mismatch")
        if not _profile(fresh, member, "broker_query", "alpaca"):
            raise _QueryFailure("profile", "source_mismatch")
        query = _parse(member.decode_raw_body())
        if query.source != "alpaca":
            raise _QueryFailure("source", "source_mismatch")
        account_member = next((row for row in fresh.members
                               if row.record_id == query.account_record_id), None)
        if account_member is None or account_member.kind != "account_snapshot":
            raise _QueryFailure("account_record_id", "member_missing_or_wrong_kind")
        if not _profile(fresh, account_member, "account_snapshot", query.source):
            raise _QueryFailure("account_record_id", "profile_mismatch")
        envelope = account_member.decode_envelope()
        validated = normalize_account(account_member.decode_raw_body(),
            event_id=envelope["event_id"], raw_ref=envelope["raw_ref"],
            received_at=_parse_timestamp(envelope["simulated_received_at"],
                                         "account_received_at"))
        account = validated.value
        if account is None:
            raise _QueryFailure("account_record_id", "normalization_failed")
        if ((account.account_id, account.source) != (query.account_id, query.source)
                or account.as_of != query.captured_at
                or account.availability_basis != "measured"
                or account.available_at is None
                or account.available_at > query.available_at):
            raise _QueryFailure("account_record_id", "account_capture_mismatch")
        for name in ("holdings", "orders"):
            expected = {"complete": "complete", "partial": "incomplete",
                        "unknown": "unknown"}[getattr(query, name + "_coverage")]
            if getattr(account, name + "_completeness") != expected:
                raise _QueryFailure(name + "_coverage", "account_coverage_mismatch")
        included = []
        # ponytail: <=256 refs reverify the <=8 MB fixture; batch only if measured cost warrants.
        for ref in query.included_order_record_ids:
            order = admit_order_update(fresh, ref)
            if (order.source_failure is not None or order.validation is None
                    or order.validation.value is None):
                raise _QueryFailure("included_order_record_ids", "order_source_failed")
            value = order.validation.value
            if ((value.account_id, value.source) != (query.account_id, query.source)
                    or value.event_at is None or value.available_at is None
                    or value.event_at > query.captured_at
                    or value.available_at > query.captured_at):
                raise _QueryFailure("included_order_record_ids", "order_capture_mismatch")
            included.append(order)
        _mapping(query, account, tuple(included))
        return _make(BrokerQueryAdmission, supplied_fixture=fixture,
            record_id=record_id, manifest=fresh, member=member, query=query,
            account=account, included_orders=tuple(included), source_failure=None)
    except (_QueryFailure, _InvalidInput) as failure:
        return failed(*failure.args)
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError,
            OverflowError, RecursionError, MemoryError):
        return failed("member", "retained_content_mismatch")
