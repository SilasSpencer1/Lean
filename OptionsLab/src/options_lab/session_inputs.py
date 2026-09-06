"""Normalize untrusted JSON-style session and instrument evidence."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from ._input_parsing import (
    RejectionCode,
    _CONTRACT_FIELDS,
    _InvalidInput,
    _fail,
    _parse_contract_id,
    _parse_date,
    _parse_string,
    _parse_timestamp,
    _parse_token,
)
from ._validation import _require_nonempty_string, _require_token, _trusted_datetime
from .sessions import ExchangeSession, InstrumentTradability


SessionNormalizationStage: TypeAlias = Literal[
    "exchange_session_normalization", "instrument_tradability_normalization"
]
_STAGES = (
    "exchange_session_normalization", "instrument_tradability_normalization",
)
_SESSION_FIELDS = (
    "calendar", "session_date", "kind", "opens_at", "closes_at", "source",
    "provider_record_id", "source_version", "available_at",
    "availability_basis", "fidelity",
)
_INSTRUMENT_FIELDS = (
    "contract", "instrument_ref", "session_date", "opens_at", "closes_at",
    "status", "effective_from", "effective_until", "source",
    "provider_record_id", "source_version", "available_at",
    "availability_basis", "fidelity",
)
_SESSION_KINDS = ("regular", "early_close", "closed", "unknown")
_INSTRUMENT_STATUSES = ("tradable", "closed", "halted", "unknown")
_AVAILABILITY_BASES = ("measured", "assumed")
_FIDELITIES = ("genuine", "synthetic", "unknown")
_CONTRACT_PATHS = tuple(f"contract.{field}" for field in _CONTRACT_FIELDS)


@dataclass(frozen=True)
class SessionInputRejection:
    """This class represents one safe session-input parsing failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    stage: SessionNormalizationStage
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted identity and the stage-specific diagnostic vocabulary.

        :returns:             None.
        :raises   TypeError:  If an identity, timestamp, stage, field, or code has the wrong type.
        :raises   ValueError: If identity is empty or stage, field, and code are incompatible.
        """
        _require_nonempty_string("event_id", self.event_id)
        _require_nonempty_string("raw_ref", self.raw_ref)
        object.__setattr__(
            self, "received_at", _trusted_datetime("received_at", self.received_at)
        )
        _require_token("stage", self.stage, _STAGES)
        _require_nonempty_string("field", self.field)
        _require_nonempty_string("code", self.code)
        if self.code not in _allowed_codes(self.stage, self.field):
            raise ValueError("rejection stage, field, and code are incompatible")

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class SessionValidation:
    """This class represents exactly one session value or safe rejection."""

    value: ExchangeSession | InstrumentTradability | None = None
    rejection: SessionInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete session-normalization outcome.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) not in (
            ExchangeSession, InstrumentTradability,
        ):
            raise TypeError(
                "validation result value must be an ExchangeSession or InstrumentTradability"
            )
        if self.rejection is not None and type(self.rejection) is not SessionInputRejection:
            raise TypeError("validation result rejection must be a SessionInputRejection")


def normalize_exchange_session(
    raw: object, *, raw_ref: str, event_id: str, received_at: datetime
) -> SessionValidation:
    """
    Normalize one exact JSON-style exchange-session dictionary.

    The mandatory body keys are ``calendar``, ``session_date``, ``kind``,
    ``opens_at``, ``closes_at``, ``source``, ``provider_record_id``,
    ``source_version``, ``available_at``, ``availability_basis``, and
    ``fidelity``. Session date is an ASCII ``YYYY-MM-DD`` string. The three
    time fields accept null or aware ISO strings. Kind is ``regular``,
    ``early_close``, ``closed``, or ``unknown``; availability basis is
    ``measured`` or ``assumed``; fidelity is ``genuine``, ``synthetic``, or
    ``unknown``. Source identity strings are exact and nonempty.

    ``raw_ref``, ``event_id``, and ``received_at`` form the trusted envelope;
    they are not body keys. Trusted strings are exact and nonempty, and the
    trusted timestamp must be aware and representable in UTC.

    :param    raw:         Untrusted exchange-session body.
    :param    raw_ref:     Trusted reference to the unmodified source record.
    :param    event_id:    Trusted identifier for the ingestion event.
    :param    received_at: Trusted current-ingestion timestamp.
    :returns:              Exactly one normalized exchange session or rejection.
    :raises   TypeError:   If a trusted envelope argument has the wrong exact type.
    :raises   ValueError:  If a trusted envelope string or timestamp is invalid.
    """
    _require_nonempty_string("raw_ref", raw_ref)
    _require_nonempty_string("event_id", event_id)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        root = _snapshot_root(raw, _SESSION_FIELDS)
        session = ExchangeSession(
            calendar=_parse_string(root["calendar"], "calendar"),
            session_date=_parse_date(root["session_date"], "session_date"),
            kind=_parse_token(root["kind"], "kind", _SESSION_KINDS),
            opens_at=_parse_timestamp(root["opens_at"], "opens_at", nullable=True),
            closes_at=_parse_timestamp(root["closes_at"], "closes_at", nullable=True),
            source=_parse_string(root["source"], "source"),
            provider_record_id=_parse_string(
                root["provider_record_id"], "provider_record_id"
            ),
            source_version=_parse_string(root["source_version"], "source_version"),
            available_at=_parse_timestamp(
                root["available_at"], "available_at", nullable=True
            ),
            availability_basis=_parse_token(
                root["availability_basis"], "availability_basis", _AVAILABILITY_BASES
            ),
            received_at=received_at,
            raw_ref=raw_ref,
            fidelity=_parse_token(root["fidelity"], "fidelity", _FIDELITIES),
        )
    except _InvalidInput as failure:
        field, code = failure.args
        return _rejected(
            event_id, received_at, raw_ref, "exchange_session_normalization",
            field, code,
        )
    return SessionValidation(value=session)


def normalize_instrument_tradability(
    raw: object, *, raw_ref: str, event_id: str, received_at: datetime
) -> SessionValidation:
    """
    Normalize one exact JSON-style instrument-tradability dictionary.

    The mandatory body keys are ``contract``, ``instrument_ref``,
    ``session_date``, ``opens_at``, ``closes_at``, ``status``,
    ``effective_from``, ``effective_until``, ``source``,
    ``provider_record_id``, ``source_version``, ``available_at``,
    ``availability_basis``, and ``fidelity``. Contract may be null; otherwise
    it has mandatory ``underlying``, ``expiry``, ``right``, ``strike``,
    ``multiplier``, and ``deliverable_id`` fields. Dates are ASCII
    ``YYYY-MM-DD`` strings, strike is an ASCII fixed-point string, and all
    time fields accept null or aware ISO strings. Status is ``tradable``,
    ``closed``, ``halted``, or ``unknown``; availability basis is ``measured``
    or ``assumed``; fidelity is ``genuine``, ``synthetic``, or ``unknown``.
    Contract right is exactly ``call`` or ``put``; multiplier is an exact
    integer, excluding booleans and subclasses. Source and instrument identity
    strings are exact and nonempty.

    ``raw_ref``, ``event_id``, and ``received_at`` form the trusted envelope;
    they are not body keys. Trusted strings are exact and nonempty, and the
    trusted timestamp must be aware and representable in UTC.

    :param    raw:         Untrusted instrument-tradability body.
    :param    raw_ref:     Trusted reference to the unmodified source record.
    :param    event_id:    Trusted identifier for the ingestion event.
    :param    received_at: Trusted current-ingestion timestamp.
    :returns:              Exactly one normalized tradability record or rejection.
    :raises   TypeError:   If a trusted envelope argument has the wrong exact type.
    :raises   ValueError:  If a trusted envelope string or timestamp is invalid.
    """
    _require_nonempty_string("raw_ref", raw_ref)
    _require_nonempty_string("event_id", event_id)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        root = _snapshot_instrument(raw)
        raw_contract = root["contract"]
        contract = None if raw_contract is None else _parse_contract_id(raw_contract)
        instrument = InstrumentTradability(
            contract=contract,
            instrument_ref=_parse_string(root["instrument_ref"], "instrument_ref"),
            session_date=_parse_date(root["session_date"], "session_date"),
            opens_at=_parse_timestamp(root["opens_at"], "opens_at", nullable=True),
            closes_at=_parse_timestamp(root["closes_at"], "closes_at", nullable=True),
            status=_parse_token(root["status"], "status", _INSTRUMENT_STATUSES),
            effective_from=_parse_timestamp(
                root["effective_from"], "effective_from", nullable=True
            ),
            effective_until=_parse_timestamp(
                root["effective_until"], "effective_until", nullable=True
            ),
            source=_parse_string(root["source"], "source"),
            provider_record_id=_parse_string(
                root["provider_record_id"], "provider_record_id"
            ),
            source_version=_parse_string(root["source_version"], "source_version"),
            available_at=_parse_timestamp(
                root["available_at"], "available_at", nullable=True
            ),
            availability_basis=_parse_token(
                root["availability_basis"], "availability_basis", _AVAILABILITY_BASES
            ),
            received_at=received_at,
            raw_ref=raw_ref,
            fidelity=_parse_token(root["fidelity"], "fidelity", _FIDELITIES),
        )
    except _InvalidInput as failure:
        field, code = failure.args
        return _rejected(
            event_id, received_at, raw_ref, "instrument_tradability_normalization",
            field, code,
        )
    return SessionValidation(value=instrument)


def _snapshot_root(raw: object, fields: tuple[str, ...]) -> dict[str, object]:
    """Validate root structure and detach it before scalar conversion."""
    if type(raw) is not dict:
        _fail("$", "expected_exact_dict")
    keys = tuple(raw)
    if any(type(key) is not str for key in keys):
        _fail("$", "unknown_fields")
    if any(key not in fields for key in keys):
        _fail("$", "unknown_fields")
    root = raw.copy()
    for field in fields:
        if field not in root:
            _fail(field, "missing")
    return root


def _snapshot_instrument(raw: object) -> dict[str, object]:
    """Validate and detach instrument root and non-null nested contract."""
    if type(raw) is not dict:
        _fail("$", "expected_exact_dict")
    keys = tuple(raw)
    if any(type(key) is not str for key in keys):
        _fail("$", "unknown_fields")
    if any(key not in _INSTRUMENT_FIELDS for key in keys):
        _fail("$", "unknown_fields")
    root = raw.copy()
    nested = root.get("contract")
    if nested is not None:
        if type(nested) is not dict:
            _fail("contract", "expected_exact_dict")
        nested_keys = tuple(nested)
        if any(type(key) is not str for key in nested_keys):
            _fail("contract", "unknown_fields")
        if any(key not in _CONTRACT_FIELDS for key in nested_keys):
            _fail("contract", "unknown_fields")
        root["contract"] = nested.copy()
    for field in _INSTRUMENT_FIELDS:
        if field not in root:
            _fail(field, "missing")
        if field == "contract" and root[field] is not None:
            for nested_field in _CONTRACT_FIELDS:
                if nested_field not in root[field]:
                    _fail(f"contract.{nested_field}", "missing")
    return root


def _rejected(
    event_id: str,
    received_at: datetime,
    raw_ref: str,
    stage: SessionNormalizationStage,
    field: str,
    code: RejectionCode,
) -> SessionValidation:
    """Build one safe immutable session-input rejection result."""
    return SessionValidation(
        rejection=SessionInputRejection(
            event_id, received_at, raw_ref, stage, field, code
        )
    )


def _allowed_codes(
    stage: SessionNormalizationStage, field: str
) -> tuple[RejectionCode, ...]:
    """Return the bounded codes compatible with one stage and safe path."""
    if field == "$":
        return ("expected_exact_dict", "unknown_fields")
    fields = _SESSION_FIELDS if stage == "exchange_session_normalization" else _INSTRUMENT_FIELDS
    if field not in fields and not (
        stage == "instrument_tradability_normalization"
        and field in _CONTRACT_PATHS
    ):
        return ()
    if field == "contract":
        return ("missing", "expected_exact_dict", "unknown_fields")
    if field in ("session_date", "contract.expiry"):
        return ("missing", "invalid_type", "invalid_date")
    if field in (
        "opens_at", "closes_at", "available_at", "effective_from",
        "effective_until",
    ):
        return ("missing", "invalid_type", "invalid_timestamp")
    if field == "contract.strike":
        return ("missing", "invalid_type", "invalid_decimal")
    if field == "contract.multiplier":
        return ("missing", "invalid_type")
    return ("missing", "invalid_type", "invalid_value")
