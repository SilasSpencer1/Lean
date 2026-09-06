from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext

import pytest

import options_lab.underlying_inputs as api
from options_lab.observations import ObservationMeta
from options_lab.underlying import UnderlyingQuote, assess_underlying_quote


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def meta() -> ObservationMeta:
    return ObservationMeta(
        "synthetic-sip-fixture", "underlying-42", "raw://underlying/42",
        "realtime", "genuine", "quote", DECISION - timedelta(seconds=5),
        DECISION, DECISION + timedelta(days=1), "measured",
        "synthetic://capture/underlying/42",
    )


def valid_raw(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "symbol": "SPY", "bid": "100.00", "ask": "100.02",
        "bid_at": "2026-09-05T10:29:55-04:00",
        "ask_at": "2026-09-05T14:29:55Z", "bid_size": 1, "ask_size": 2,
    }
    raw.update(changes)
    return raw


def normalize(raw: object, **changes: object) -> api.UnderlyingQuoteValidation:
    inputs = {"meta": meta(), "event_id": "event-1"}
    inputs.update(changes)
    return api.normalize_underlying_quote(raw, **inputs)


def assert_failure(raw: object, field: str, code: str) -> api.UnderlyingQuoteInputRejection:
    result = normalize(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


def test_valid_body_normalizes_and_flows_to_underlying_assessment() -> None:
    result = normalize(valid_raw())
    assert type(result) is api.UnderlyingQuoteValidation
    assert result.rejection is None and type(result.value) is UnderlyingQuote
    observed = result.value
    assert observed.symbol == "SPY" and observed.meta == meta()
    assert (observed.bid, observed.ask) == (Decimal("100.00"), Decimal("100.02"))
    assert observed.bid_at == observed.ask_at == DECISION - timedelta(seconds=5)
    assert (observed.bid_size, observed.ask_size) == (1, 2)
    assert assess_underlying_quote(observed, decision_at=DECISION).underlying_quote_suitable


def test_explicit_nulls_are_retained_as_missing_facts() -> None:
    raw = valid_raw(**{name: None for name in valid_raw() if name != "symbol"})
    observed = normalize(raw).value
    assert observed is not None
    assert (observed.bid, observed.ask, observed.bid_at, observed.ask_at,
            observed.bid_size, observed.ask_size) == (None,) * 6


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


class MetaSubclass(ObservationMeta):
    pass


@pytest.mark.parametrize("raw", [None, [], "quote", object(), DictSubclass()])
def test_root_requires_an_exact_dictionary(raw) -> None:
    assert_failure(raw, "$", "expected_exact_dict")


def test_unknown_fields_precede_missing_without_leaking_values() -> None:
    raw = valid_raw(secret_provider_key="classified")
    del raw["symbol"]
    rejection = assert_failure(raw, "$", "unknown_fields")
    assert "secret_provider_key" not in repr(rejection)
    assert "classified" not in repr(rejection)


@pytest.mark.parametrize("field", tuple(valid_raw()))
def test_every_field_is_mandatory(field) -> None:
    raw = valid_raw()
    del raw[field]
    assert_failure(raw, field, "missing")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("symbol", "", "invalid_value"), ("symbol", 1, "invalid_type"),
        ("symbol", StringSubclass("SPY"), "invalid_type"),
        ("bid", "-0.01", "invalid_value"), ("ask", "-1", "invalid_value"),
        ("bid", "NaN", "invalid_decimal"), ("ask", "Infinity", "invalid_decimal"),
        ("bid", "1e2", "invalid_decimal"), ("ask", " 1", "invalid_decimal"),
        ("bid", Decimal("1"), "invalid_type"), ("ask", True, "invalid_type"),
        ("bid_at", "2026-09-05T14:30:00", "invalid_timestamp"),
        ("ask_at", datetime(2026, 9, 5, tzinfo=UTC), "invalid_type"),
        ("bid_size", True, "invalid_type"), ("ask_size", IntSubclass(1), "invalid_type"),
        ("bid_size", "1", "invalid_type"), ("ask_size", 1.0, "invalid_type"),
        ("bid_size", -1, "invalid_value"), ("ask_size", -2, "invalid_value"),
    ],
)
def test_malformed_external_values_return_fixed_safe_codes(field, value, code) -> None:
    assert_failure(valid_raw(**{field: value}), field, code)


@pytest.mark.parametrize("value", ["+1.", ".5", "-0.00", "123456789.123456789"])
def test_prices_preserve_exact_precision_and_decimal_context(value) -> None:
    with localcontext() as context:
        context.prec = 3
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        result = normalize(valid_raw(bid=value))
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert result.value is not None
    assert result.value.bid.as_tuple() == Decimal(value).as_tuple()
    assert after == before


def test_source_mutation_cannot_change_normalized_quote() -> None:
    raw = valid_raw()
    observed = normalize(raw).value
    raw.update(symbol="AAPL", bid="99", bid_at=None, bid_size=99)
    assert observed is not None
    assert (observed.symbol, observed.bid, observed.bid_at, observed.bid_size) == (
        "SPY", Decimal("100.00"), DECISION - timedelta(seconds=5), 1,
    )


class CollidingKey:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        return hash("symbol")

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        raise AssertionError("must not compare hostile keys")

    def __repr__(self) -> str:
        self.callbacks.append("repr")
        raise AssertionError("must not represent hostile keys")


class HostileScalar:
    def __repr__(self) -> str:
        raise AssertionError("must not represent hostile scalars")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("must not compare hostile scalars")


def test_hostile_keys_and_scalars_never_invoke_callbacks() -> None:
    key = CollidingKey()
    assert_failure({key: None}, "$", "unknown_fields")
    assert key.callbacks == []
    for field in ("symbol", "bid", "bid_at", "bid_size"):
        assert_failure(valid_raw(**{field: HostileScalar()}), field, "invalid_type")


def test_first_parse_failure_follows_declared_field_order() -> None:
    good = valid_raw()
    for index, field in enumerate(good):
        raw = {
            name: value if position < index else HostileScalar()
            for position, (name, value) in enumerate(good.items())
        }
        assert_failure(raw, field, "invalid_type")


class ExplodingDict(dict):
    def __iter__(self):
        raise AssertionError("raw must not be inspected")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"meta": object()}, TypeError),
        ({"meta": MetaSubclass(
            "sip", "q", "raw://q", "realtime", "genuine", "quote",
            DECISION, DECISION, DECISION, "measured", "capture://q",
        )}, TypeError),
        ({"event_id": 7}, TypeError), ({"event_id": StringSubclass("e")}, TypeError),
        ({"event_id": ""}, ValueError),
    ],
)
def test_trusted_envelope_is_validated_before_raw_inspection(changes, error) -> None:
    inputs = {"meta": meta(), "event_id": "event-1"}
    inputs.update(changes)
    with pytest.raises(error):
        api.normalize_underlying_quote(ExplodingDict(), **inputs)


def test_rejection_evidence_is_fixed_safe_and_immutable() -> None:
    rejection = assert_failure([], "$", "expected_exact_dict")
    assert (rejection.event_id, rejection.received_at, rejection.raw_ref) == (
        "event-1", meta().received_at, "raw://underlying/42",
    )
    assert rejection.stage == "underlying_quote_normalization"
    assert rejection.reasons == ("expected_exact_dict",)
    with pytest.raises(FrozenInstanceError):
        rejection.code = "unknown_fields"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("secret", "invalid_type"), ("$", "missing"),
        ("symbol", "invalid_decimal"), ("bid", "invalid_timestamp"),
        ("bid_at", "invalid_decimal"), ("ask_size", "invalid_decimal"),
    ],
)
def test_rejection_rejects_incompatible_field_code(field, code) -> None:
    with pytest.raises(ValueError):
        api.UnderlyingQuoteInputRejection("e", DECISION, "r", field, code)


def test_validation_requires_exactly_one_exact_concrete_outcome() -> None:
    value = normalize(valid_raw()).value
    rejection = assert_failure([], "$", "expected_exact_dict")
    for inputs, error in [
        ({}, ValueError), ({"value": value, "rejection": rejection}, ValueError),
        ({"value": "bad"}, TypeError), ({"rejection": "bad"}, TypeError),
    ]:
        with pytest.raises(error):
            api.UnderlyingQuoteValidation(**inputs)
