from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import options_lab.session_inputs as api
from options_lab.config import StrategyConfig
from options_lab.session_inputs import (
    SessionInputRejection,
    SessionValidation,
    normalize_exchange_session,
    normalize_instrument_tradability,
)
from options_lab.sessions import ExchangeSession, InstrumentTradability
from options_lab.sessions import assess_session


UTC = timezone.utc
RECEIVED = datetime(2026, 9, 4, 13, 5, tzinfo=UTC)


def session_raw(**changes: object) -> dict[str, object]:
    """Return synthetic-session-input-factory-v1 raw calendar evidence."""
    raw: dict[str, object] = {
        "calendar": "XNYS",
        "session_date": "2026-09-04",
        "kind": "regular",
        "opens_at": "2026-09-04T09:30:00-04:00",
        "closes_at": "2026-09-04T20:00:00Z",
        "source": "synthetic-calendar",
        "provider_record_id": "synthetic-session-2026-09-04",
        "source_version": "synthetic-calendar-v1",
        "available_at": "2026-09-04T13:00:00Z",
        "availability_basis": "measured",
        "fidelity": "synthetic",
    }
    raw.update(changes)
    return raw


def contract_raw(**changes: object) -> dict[str, object]:
    """Return synthetic-contract-input-factory-v1 raw option identity."""
    raw: dict[str, object] = {
        "underlying": "SPY",
        "expiry": "2026-09-18",
        "right": "call",
        "strike": "650.00",
        "multiplier": 100,
        "deliverable_id": "standard-spy-100",
    }
    raw.update(changes)
    return raw


def instrument_raw(**changes: object) -> dict[str, object]:
    """Return synthetic-instrument-input-factory-v1 raw tradability evidence."""
    raw: dict[str, object] = {
        "contract": contract_raw(),
        "instrument_ref": "synthetic-option-2026-09-18-C-650",
        "session_date": "2026-09-04",
        "opens_at": "2026-09-04T13:30:00Z",
        "closes_at": "2026-09-04T16:15:00-04:00",
        "status": "tradable",
        "effective_from": "2026-09-04T13:30:00Z",
        "effective_until": "2026-09-04T20:15:00Z",
        "source": "synthetic-instrument",
        "provider_record_id": "synthetic-instrument-2026-09-04",
        "source_version": "synthetic-instrument-v1",
        "available_at": "2026-09-04T13:00:00Z",
        "availability_basis": "measured",
        "fidelity": "synthetic",
    }
    raw.update(changes)
    return raw


def normalize_session(raw: object) -> SessionValidation:
    return normalize_exchange_session(
        raw, raw_ref="raw://calendar/1", event_id="event-1", received_at=RECEIVED
    )


def normalize_instrument(raw: object) -> SessionValidation:
    return normalize_instrument_tradability(
        raw, raw_ref="raw://instrument/1", event_id="event-1", received_at=RECEIVED
    )


def assert_session_failure(raw: object, field: str, code: str) -> SessionInputRejection:
    result = normalize_session(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


def assert_instrument_failure(raw: object, field: str, code: str) -> SessionInputRejection:
    result = normalize_instrument(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


def test_valid_exchange_session_normalizes_exact_record_and_utc_times() -> None:
    result = normalize_session(session_raw())

    assert type(result) is SessionValidation
    assert result.rejection is None and type(result.value) is ExchangeSession
    assert result.value.session_date == date(2026, 9, 4)
    assert result.value.opens_at == datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
    assert result.value.closes_at == datetime(2026, 9, 4, 20, tzinfo=UTC)
    assert result.value.received_at == RECEIVED
    assert result.value.raw_ref == "raw://calendar/1"


def test_valid_instrument_normalizes_exact_contract_and_independent_times() -> None:
    result = normalize_instrument(instrument_raw())

    assert result.rejection is None and type(result.value) is InstrumentTradability
    assert result.value.contract is not None
    assert result.value.contract.expiry == date(2026, 9, 18)
    assert result.value.contract.strike == Decimal("650.00")
    assert result.value.closes_at == datetime(2026, 9, 4, 20, 15, tzinfo=UTC)
    assert result.value.received_at == RECEIVED
    assert result.value.raw_ref == "raw://instrument/1"


def test_nullable_session_and_instrument_facts_remain_explicit() -> None:
    calendar = normalize_session(
        session_raw(opens_at=None, closes_at=None, available_at=None)
    ).value
    instrument = normalize_instrument(
        instrument_raw(
            contract=None,
            opens_at=None,
            closes_at=None,
            effective_from=None,
            effective_until=None,
            available_at=None,
        )
    ).value

    assert calendar is not None
    assert (calendar.opens_at, calendar.closes_at, calendar.available_at) == (None,) * 3
    assert instrument is not None
    assert (
        instrument.contract,
        instrument.opens_at,
        instrument.closes_at,
        instrument.effective_from,
        instrument.effective_until,
        instrument.available_at,
    ) == (None,) * 6


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


class SessionSubclass(ExchangeSession):
    pass


class RejectionSubclass(SessionInputRejection):
    pass


@pytest.mark.parametrize("raw", [None, [], "record", object(), DictSubclass()])
@pytest.mark.parametrize("normalizer", [assert_session_failure, assert_instrument_failure])
def test_root_requires_an_exact_dictionary(normalizer, raw) -> None:
    normalizer(raw, "$", "expected_exact_dict")


@pytest.mark.parametrize(
    ("factory", "normalizer"),
    [(session_raw, assert_session_failure), (instrument_raw, assert_instrument_failure)],
)
def test_unknown_root_fields_precede_missing_without_leaking_payload(
    factory, normalizer
) -> None:
    raw = factory(secret_provider_key="classified")
    del raw[next(iter(raw))]

    rejection = normalizer(raw, "$", "unknown_fields")

    assert "secret_provider_key" not in repr(rejection)
    assert "classified" not in repr(rejection)
    normalizer(factory(raw_ref="body-metadata-is-forbidden"), "$", "unknown_fields")


@pytest.mark.parametrize("field", tuple(session_raw()))
def test_every_exchange_session_field_is_mandatory(field) -> None:
    raw = session_raw()
    del raw[field]
    assert_session_failure(raw, field, "missing")


@pytest.mark.parametrize("field", tuple(instrument_raw()))
def test_every_instrument_field_is_mandatory(field) -> None:
    raw = instrument_raw()
    del raw[field]
    assert_instrument_failure(raw, field, "missing")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("calendar", None, "invalid_type"),
        ("calendar", "", "invalid_value"),
        ("calendar", StringSubclass("XNYS"), "invalid_type"),
        ("session_date", date(2026, 9, 4), "invalid_type"),
        ("session_date", "2026-9-04", "invalid_date"),
        ("session_date", "2026-02-30", "invalid_date"),
        ("kind", True, "invalid_type"),
        ("kind", "holiday", "invalid_value"),
        ("source", 1, "invalid_type"),
        ("provider_record_id", "", "invalid_value"),
        ("source_version", None, "invalid_type"),
        ("availability_basis", "estimated", "invalid_value"),
        ("fidelity", "paper", "invalid_value"),
    ],
)
def test_exchange_scalars_return_safe_specific_codes(field, value, code) -> None:
    assert_session_failure(session_raw(**{field: value}), field, code)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("instrument_ref", None, "invalid_type"),
        ("instrument_ref", "", "invalid_value"),
        ("session_date", True, "invalid_type"),
        ("session_date", "2026-W36-5", "invalid_date"),
        ("status", StringSubclass("tradable"), "invalid_type"),
        ("status", "open", "invalid_value"),
        ("source", "", "invalid_value"),
        ("provider_record_id", 1, "invalid_type"),
        ("source_version", None, "invalid_type"),
        ("availability_basis", True, "invalid_type"),
        ("fidelity", "estimated", "invalid_value"),
    ],
)
def test_instrument_scalars_return_safe_specific_codes(field, value, code) -> None:
    assert_instrument_failure(instrument_raw(**{field: value}), field, code)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2026-09-04T13:30:00", "invalid_timestamp"),
        ("bad", "invalid_timestamp"),
        ("0001-01-01T00:00:00+14:00", "invalid_timestamp"),
        (datetime(2026, 9, 4, 13, 30, tzinfo=UTC), "invalid_type"),
        (StringSubclass("2026-09-04T13:30:00Z"), "invalid_type"),
        (True, "invalid_type"),
    ],
)
@pytest.mark.parametrize("field", ["opens_at", "closes_at", "available_at"])
def test_session_timestamps_reject_naive_overflow_and_non_strings(field, value, code) -> None:
    assert_session_failure(session_raw(**{field: value}), field, code)


@pytest.mark.parametrize(
    "field", ["opens_at", "closes_at", "effective_from", "effective_until", "available_at"]
)
@pytest.mark.parametrize(
    ("value", "code"),
    [("2026-09-04T13:30:00", "invalid_timestamp"), (object(), "invalid_type")],
)
def test_instrument_timestamps_reject_malformed_values(field, value, code) -> None:
    assert_instrument_failure(instrument_raw(**{field: value}), field, code)


@pytest.mark.parametrize(
    ("contract", "field", "code"),
    [
        ([], "contract", "expected_exact_dict"),
        (DictSubclass(), "contract", "expected_exact_dict"),
        ({"secret": "classified"}, "contract", "unknown_fields"),
        ({"expiry": "2026-09-18"}, "contract.underlying", "missing"),
    ],
)
def test_instrument_contract_shape_is_exact(contract, field, code) -> None:
    assert_instrument_failure(instrument_raw(contract=contract), field, code)


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        ({"underlying": 1}, "contract.underlying", "invalid_type"),
        ({"underlying": ""}, "contract.underlying", "invalid_value"),
        ({"expiry": date(2026, 9, 18)}, "contract.expiry", "invalid_type"),
        ({"expiry": "2025-02-29"}, "contract.expiry", "invalid_date"),
        ({"right": True}, "contract.right", "invalid_type"),
        ({"right": "CALL"}, "contract.right", "invalid_value"),
        ({"strike": Decimal("650")}, "contract.strike", "invalid_type"),
        ({"strike": "6.5e2"}, "contract.strike", "invalid_decimal"),
        ({"multiplier": True}, "contract.multiplier", "invalid_type"),
        ({"multiplier": IntSubclass(100)}, "contract.multiplier", "invalid_type"),
        ({"deliverable_id": ""}, "contract.deliverable_id", "invalid_value"),
    ],
)
def test_instrument_contract_uses_existing_six_field_parser(change, field, code) -> None:
    assert_instrument_failure(
        instrument_raw(contract=contract_raw(**change)), field, code
    )


def test_nested_unknown_fields_precede_root_and_nested_missing_fields() -> None:
    raw = instrument_raw(contract=contract_raw(secret="classified"))
    del raw["instrument_ref"]
    del raw["contract"]["underlying"]
    assert_instrument_failure(raw, "contract", "unknown_fields")

    raw = instrument_raw()
    del raw["instrument_ref"]
    del raw["contract"]["underlying"]
    assert_instrument_failure(raw, "contract.underlying", "missing")


class CollidingKey:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        return hash("calendar")

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        raise AssertionError("must not compare hostile keys")

    def __str__(self) -> str:
        self.callbacks.append("str")
        raise AssertionError("must not stringify hostile keys")

    def __repr__(self) -> str:
        self.callbacks.append("repr")
        raise AssertionError("must not represent hostile keys")


class HostileScalar:
    def __str__(self) -> str:
        raise AssertionError("must not stringify hostile values")

    def __repr__(self) -> str:
        raise AssertionError("must not represent hostile values")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("must not compare hostile values")


@pytest.mark.parametrize("normalizer", [assert_session_failure, assert_instrument_failure])
def test_hostile_keys_and_values_never_invoke_callbacks(normalizer) -> None:
    key = CollidingKey()
    normalizer({key: None}, "$", "unknown_fields")
    assert key.callbacks == []

    if normalizer is assert_instrument_failure:
        nested_key = CollidingKey()
        normalizer(instrument_raw(contract={nested_key: None}), "contract", "unknown_fields")
        assert nested_key.callbacks == []


def test_hostile_scalar_callbacks_are_never_invoked() -> None:
    assert_session_failure(session_raw(calendar=HostileScalar()), "calendar", "invalid_type")
    assert_session_failure(session_raw(opens_at=HostileScalar()), "opens_at", "invalid_type")
    assert_instrument_failure(instrument_raw(status=HostileScalar()), "status", "invalid_type")
    assert_instrument_failure(
        instrument_raw(contract=contract_raw(underlying=HostileScalar())),
        "contract.underlying", "invalid_type",
    )


def test_first_parse_failure_follows_each_declaration_order() -> None:
    assert_session_failure(
        session_raw(calendar="", session_date="bad", kind="bad"),
        "calendar", "invalid_value",
    )
    raw = instrument_raw(instrument_ref="", session_date="bad")
    raw["contract"]["expiry"] = "bad"
    assert_instrument_failure(raw, "contract.expiry", "invalid_date")


class ExplodingDict(dict):
    def __iter__(self):
        raise AssertionError("untrusted raw must not be inspected")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"raw_ref": ""}, ValueError),
        ({"raw_ref": StringSubclass("raw")}, TypeError),
        ({"event_id": 1}, TypeError),
        ({"event_id": ""}, ValueError),
        ({"received_at": datetime(2026, 9, 4, 13, 5)}, ValueError),
    ],
)
@pytest.mark.parametrize(
    "normalizer", [normalize_exchange_session, normalize_instrument_tradability]
)
def test_trusted_envelope_is_validated_before_raw_inspection(
    normalizer, changes, error
) -> None:
    envelope = {"raw_ref": "raw", "event_id": "event", "received_at": RECEIVED}
    with pytest.raises(error):
        normalizer(ExplodingDict(), **(envelope | changes))


def test_root_and_nested_contract_are_snapshotted_before_conversion(monkeypatch) -> None:
    session_source = session_raw()
    instrument_source = instrument_raw()
    real_parse_date = api._parse_date

    def mutating_session_date(value: object, field: str):
        session_source["source"] = "changed-calendar"
        return real_parse_date(value, field)

    monkeypatch.setattr(api, "_parse_date", mutating_session_date)
    calendar = normalize_session(session_source).value

    real_parse_contract = api._parse_contract_id

    def mutating_contract(raw: object):
        instrument_source["instrument_ref"] = "changed-instrument"
        instrument_source["contract"]["deliverable_id"] = "adjusted"
        return real_parse_contract(raw)

    monkeypatch.setattr(api, "_parse_contract_id", mutating_contract)
    instrument = normalize_instrument(instrument_source).value

    assert calendar is not None and calendar.source == "synthetic-calendar"
    assert instrument is not None and instrument.instrument_ref.startswith("synthetic-option")
    assert instrument.contract is not None
    assert instrument.contract.deliverable_id == "standard-spy-100"


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_propagate(monkeypatch, control) -> None:
    def stop(value: object, field: str):
        raise control()

    monkeypatch.setattr(api, "_parse_date", stop)
    with pytest.raises(control):
        normalize_session(session_raw())


def test_safe_rejections_are_frozen_and_retain_only_trusted_envelope() -> None:
    received = datetime(2026, 9, 4, 9, 5, tzinfo=timezone(timedelta(hours=-4)))
    result = normalize_instrument_tradability(
        {"secret": "classified"}, raw_ref="raw://instrument/1",
        event_id="event-1", received_at=received,
    )

    assert result.rejection is not None
    assert result.rejection.received_at == RECEIVED
    assert result.rejection.stage == "instrument_tradability_normalization"
    assert result.rejection.reasons == ("unknown_fields",)
    assert "secret" not in repr(result.rejection)
    assert "classified" not in repr(result.rejection)
    with pytest.raises(FrozenInstanceError):
        result.rejection.code = "missing"


@pytest.mark.parametrize(
    ("stage", "field", "code"),
    [
        ("bad", "$", "unknown_fields"),
        ("exchange_session_normalization", "contract", "unknown_fields"),
        ("instrument_tradability_normalization", "kind", "invalid_value"),
        ("exchange_session_normalization", "session_date", "invalid_timestamp"),
        ("instrument_tradability_normalization", "contract.strike", "invalid_date"),
        ("instrument_tradability_normalization", "contract.", "invalid_type"),
        ("instrument_tradability_normalization", "contract.expiry.extra", "missing"),
        ("instrument_tradability_normalization", "contract.raw_ref", "invalid_value"),
        ("instrument_tradability_normalization", "contract.arbitrary", "invalid_type"),
    ],
)
def test_rejection_rejects_incompatible_stage_field_and_code(stage, field, code) -> None:
    with pytest.raises(ValueError):
        SessionInputRejection("event", RECEIVED, "raw", stage, field, code)


def test_validation_requires_one_exact_concrete_outcome() -> None:
    value = normalize_session(session_raw()).value
    rejection = assert_session_failure([], "$", "expected_exact_dict")
    assert value is not None
    subclass = SessionSubclass(**value.__dict__)
    rejection_subclass = RejectionSubclass(**rejection.__dict__)

    for kwargs, error in [
        ({}, ValueError),
        ({"value": value, "rejection": rejection}, ValueError),
        ({"value": "bad"}, TypeError),
        ({"value": subclass}, TypeError),
        ({"rejection": "bad"}, TypeError),
        ({"rejection": rejection_subclass}, TypeError),
    ]:
        with pytest.raises(error):
            SessionValidation(**kwargs)


def test_source_mutation_cannot_change_normalized_records() -> None:
    session_source = session_raw()
    instrument_source = instrument_raw()
    calendar = normalize_session(session_source).value
    instrument = normalize_instrument(instrument_source).value
    session_source.update(calendar="OTHER", kind="closed")
    instrument_source.update(status="halted", instrument_ref="changed")
    instrument_source["contract"]["underlying"] = "QQQ"

    assert calendar is not None and (calendar.calendar, calendar.kind) == ("XNYS", "regular")
    assert instrument is not None and instrument.status == "tradable"
    assert instrument.contract is not None and instrument.contract.underlying == "SPY"


def test_well_typed_adverse_facts_normalize_then_assessment_rejects_entry() -> None:
    calendar = normalize_session(
        session_raw(
            calendar="OTHER", kind="closed",
            opens_at="2026-09-04T20:00:00Z",
            closes_at="2026-09-04T13:30:00Z",
            available_at="2026-09-05T00:00:00Z",
            availability_basis="assumed", fidelity="unknown",
        )
    ).value
    instrument = normalize_instrument(
        instrument_raw(
            contract=None, session_date="2026-09-05", status="halted",
            effective_from="2026-09-04T20:15:00Z",
            effective_until="2026-09-04T13:30:00Z",
            available_at=None, availability_basis="assumed", fidelity="unknown",
        )
    ).value

    assert calendar is not None and instrument is not None
    result = assess_session(
        calendar, instrument, config=StrategyConfig(),
        now=datetime(2026, 9, 4, 14, tzinfo=UTC),
    )
    assert not result.entry_timing_suitable
    assert "calendar_unsupported" in result.session_reasons
    assert "instrument_unresolved" in result.instrument_hours_reasons
    assert "instrument_halted" in result.operability_reasons

    adjusted = normalize_instrument(
        instrument_raw(contract=contract_raw(
            underlying="QQQ", strike="-1", multiplier=0, deliverable_id="adjusted"
        ))
    ).value
    assert adjusted is not None and adjusted.contract is not None
    assert adjusted.contract.deliverable_id == "adjusted"


def test_early_close_and_independent_instrument_hours_retain_recovery_clocks() -> None:
    day = "2026-11-27"
    instrument = normalize_instrument(
        instrument_raw(
            session_date=day,
            opens_at="2026-11-27T14:30:00Z",
            closes_at="2026-11-27T18:00:00Z",
            effective_from="2026-11-27T14:30:00Z",
            effective_until="2026-11-27T18:00:00Z",
            available_at="2026-11-27T14:00:00Z",
            status="halted",
        )
    ).value
    calendar = normalize_session(
        session_raw(
            session_date=day, kind="early_close",
            opens_at="2026-11-27T14:30:00Z",
            closes_at="2026-11-27T18:00:00Z",
            available_at="2026-11-27T14:00:00Z",
        )
    ).value
    now = datetime(2026, 11, 27, 17, 30, tzinfo=UTC)

    common = assess_session(calendar, instrument, config=StrategyConfig(), now=now)
    independent = assess_session(None, instrument, config=StrategyConfig(), now=now)

    assert common.recovery_basis == "common_hours"
    assert common.effective_liquidation_start == datetime(2026, 11, 27, 17, 35, tzinfo=UTC)
    assert independent.recovery_basis == "instrument_hours_only"
    assert independent.effective_liquidation_deadline == datetime(2026, 11, 27, 17, 40, tzinfo=UTC)
    assert instrument is not None and instrument.status == "halted"
