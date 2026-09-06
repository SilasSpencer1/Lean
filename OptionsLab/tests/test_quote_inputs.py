from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext

import pytest

from options_lab import (
    ContractId,
    ObservationMeta,
    QuoteInputRejection,
    QuoteObservation,
    QuoteValidation,
    assess_quote_premium_budget,
    normalize_quote_observation,
)


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def contract(*, right: str = "call") -> ContractId:
    return ContractId(
        "SPY", date(2026, 9, 18), right, Decimal("650"), 100,
        "standard-spy-100",
    )


def meta() -> ObservationMeta:
    return ObservationMeta(
        "sip", "quote-42", "raw://quote/42", "realtime", "genuine", "quote",
        DECISION - timedelta(seconds=5), DECISION, DECISION + timedelta(days=1),
        "measured", "capture://quote/42",
    )


def valid_raw(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "bid": "5.00",
        "ask": "5.10",
        "bid_at": "2026-09-05T10:29:55-04:00",
        "ask_at": "2026-09-05T14:29:55Z",
        "bid_size": 1,
        "ask_size": 2,
    }
    raw.update(changes)
    return raw


def normalize(
    raw: object, *, trusted_contract: ContractId | None = None,
    trusted_meta: ObservationMeta | None = None,
) -> QuoteValidation:
    return normalize_quote_observation(
        raw,
        contract=contract() if trusted_contract is None else trusted_contract,
        meta=meta() if trusted_meta is None else trusted_meta,
        event_id="event-1",
    )


def assert_failure(raw: object, field: str, code: str) -> QuoteInputRejection:
    result = normalize(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


@pytest.mark.parametrize("right", ["call", "put"])
def test_valid_quote_normalizes_and_flows_through_premium_assessment(right) -> None:
    result = normalize(valid_raw(), trusted_contract=contract(right=right))

    assert type(result) is QuoteValidation
    assert result.rejection is None and type(result.value) is QuoteObservation
    quote = result.value
    assert quote.contract.right == right
    assert quote.meta is not None
    assert quote.bid == Decimal("5.00")
    assert quote.ask == Decimal("5.10")
    assert quote.bid_at == quote.ask_at == DECISION - timedelta(seconds=5)
    assert (quote.bid_size, quote.ask_size) == (1, 2)
    assessment = assess_quote_premium_budget(
        quote,
        decision_at=DECISION,
        virtual_equity=Decimal("104200"),
        available_cash=Decimal("521"),
    )
    assert assessment.quote_budget_suitable is True


def test_explicit_nulls_are_retained_as_missing_quote_facts() -> None:
    result = normalize({field: None for field in valid_raw()})

    assert result.rejection is None and result.value is not None
    quote = result.value
    assert (
        quote.bid, quote.ask, quote.bid_at, quote.ask_at,
        quote.bid_size, quote.ask_size,
    ) == (None, None, None, None, None, None)


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


class ContractSubclass(ContractId):
    pass


class MetaSubclass(ObservationMeta):
    pass


@pytest.mark.parametrize("raw", [None, [], "quote", object(), DictSubclass()])
def test_root_requires_an_exact_dictionary(raw) -> None:
    assert_failure(raw, "$", "expected_exact_dict")


def test_unknown_fields_precede_missing_fields_without_leaking_source() -> None:
    raw = valid_raw(secret_provider_key="classified")
    del raw["bid"]

    rejection = assert_failure(raw, "$", "unknown_fields")

    assert "secret_provider_key" not in repr(rejection)
    assert "classified" not in repr(rejection)


@pytest.mark.parametrize("field", tuple(valid_raw()))
def test_every_quote_field_is_mandatory(field) -> None:
    raw = valid_raw()
    del raw[field]
    assert_failure(raw, field, "missing")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("bid", "-0.01", "invalid_value"),
        ("ask", "-1", "invalid_value"),
        ("bid", "NaN", "invalid_decimal"),
        ("ask", "Infinity", "invalid_decimal"),
        ("bid", "1e2", "invalid_decimal"),
        ("ask", " 1", "invalid_decimal"),
        ("bid", "", "invalid_decimal"),
        ("ask", Decimal("1"), "invalid_type"),
        ("bid", True, "invalid_type"),
        ("ask", StringSubclass("1"), "invalid_type"),
    ],
)
def test_malformed_prices_return_safe_codes(field, value, code) -> None:
    assert_failure(valid_raw(**{field: value}), field, code)


@pytest.mark.parametrize("value", ["+1.", ".5", "-0.00", "123456789.123456789"])
def test_prices_preserve_exact_precision_and_caller_decimal_context(value) -> None:
    raw = valid_raw(bid=value)
    with localcontext() as context:
        context.prec = 3
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        result = normalize(raw)
        after = (context.prec, context.flags.copy(), context.traps.copy())

    assert result.value is not None
    assert result.value.bid.as_tuple() == Decimal(value).as_tuple()
    assert after == before


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-05T14:30:00",
        "not-a-time",
        "0001-01-01T00:00:00+14:00",
        datetime(2026, 9, 5, 14, 30, tzinfo=UTC),
        StringSubclass("2026-09-05T14:30:00Z"),
    ],
)
@pytest.mark.parametrize("field", ["bid_at", "ask_at"])
def test_malformed_side_times_return_safe_codes(field, value) -> None:
    code = "invalid_type" if type(value) is not str else "invalid_timestamp"
    assert_failure(valid_raw(**{field: value}), field, code)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("bid_size", True, "invalid_type"),
        ("ask_size", IntSubclass(1), "invalid_type"),
        ("bid_size", "1", "invalid_type"),
        ("ask_size", 1.0, "invalid_type"),
        ("bid_size", -1, "invalid_value"),
        ("ask_size", -2, "invalid_value"),
    ],
)
def test_malformed_sizes_return_safe_codes(field, value, code) -> None:
    assert_failure(valid_raw(**{field: value}), field, code)


@pytest.mark.parametrize("value", [0, None])
@pytest.mark.parametrize("field", ["bid_size", "ask_size"])
def test_zero_and_missing_sizes_are_retained(field, value) -> None:
    result = normalize(valid_raw(**{field: value}))

    assert result.value is not None
    assert getattr(result.value, field) == value


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"bid": "0", "ask": "0"}, "bid_nonpositive"),
        ({"bid": "5.10", "ask": "5.00"}, "quote_crossed"),
        ({"bid": "5.00", "ask": "5.00"}, "quote_locked"),
        ({"bid_size": 0}, "bid_size_nonpositive"),
        ({"bid_at": "2026-09-05T14:30:00.000001Z"}, "bid_time_after_decision"),
        ({"ask_at": "2026-09-05T14:29:54.999999Z"}, "ask_too_old"),
    ],
)
def test_adverse_well_typed_facts_are_retained_for_assessment(changes, reason) -> None:
    result = normalize(valid_raw(**changes))

    assert result.value is not None
    assessment = assess_quote_premium_budget(
        result.value,
        decision_at=DECISION,
        virtual_equity=Decimal("104200"),
        available_cash=Decimal("521"),
    )
    assert reason in assessment.quote_reasons
    assert assessment.quote_budget_suitable is False


def test_source_mutation_cannot_change_normalized_quote() -> None:
    raw = valid_raw()
    quote = normalize(raw).value
    raw.update(bid="99", bid_at=None, bid_size=99)

    assert quote is not None
    assert (quote.bid, quote.bid_at, quote.bid_size) == (
        Decimal("5.00"), DECISION - timedelta(seconds=5), 1,
    )


class CollidingKey:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        return hash("bid")

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
        raise AssertionError("must not stringify hostile scalars")

    def __repr__(self) -> str:
        raise AssertionError("must not represent hostile scalars")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("must not compare hostile scalars")


def test_hostile_keys_and_scalars_never_invoke_callbacks() -> None:
    key = CollidingKey()
    assert_failure({key: None}, "$", "unknown_fields")
    assert key.callbacks == []
    for field in ("bid", "bid_at", "bid_size"):
        assert_failure(valid_raw(**{field: HostileScalar()}), field, "invalid_type")


def test_first_parse_failure_follows_quote_field_declaration_order() -> None:
    good = valid_raw()
    fields = tuple(good)
    for index, field in enumerate(fields):
        raw = {
            name: value if position < index else HostileScalar()
            for position, (name, value) in enumerate(good.items())
        }
        assert_failure(raw, field, "invalid_type")


class ExplodingDict(dict):
    def __iter__(self):
        raise AssertionError("raw must not be inspected")


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"contract": object()}, TypeError),
        ({"contract": ContractSubclass(
            "SPY", date(2026, 9, 18), "call", Decimal("650"), 100,
            "standard-spy-100",
        )}, TypeError),
        ({"meta": object()}, TypeError),
        ({"meta": MetaSubclass(
            "sip", "quote-42", "raw://quote/42", "realtime", "genuine", "quote",
            DECISION, DECISION, DECISION, "measured", "capture://quote/42",
        )}, TypeError),
        ({"event_id": 7}, TypeError),
        ({"event_id": StringSubclass("event-1")}, TypeError),
        ({"event_id": ""}, ValueError),
    ],
)
def test_trusted_envelope_is_validated_before_raw_inspection(kwargs, error) -> None:
    envelope = {"contract": contract(), "meta": meta(), "event_id": "event-1"}
    envelope.update(kwargs)
    with pytest.raises(error):
        normalize_quote_observation(ExplodingDict(), **envelope)


def test_rejection_evidence_is_derived_from_trusted_metadata_and_immutable() -> None:
    rejection = assert_failure([], "$", "expected_exact_dict")

    assert (rejection.event_id, rejection.received_at, rejection.raw_ref) == (
        "event-1", meta().received_at, "raw://quote/42",
    )
    assert rejection.stage == "quote_normalization"
    assert rejection.reasons == ("expected_exact_dict",)
    with pytest.raises(FrozenInstanceError):
        rejection.code = "unknown_fields"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("secret", "invalid_type"),
        ("$", "missing"),
        ("bid", "invalid_timestamp"),
        ("bid_at", "invalid_decimal"),
        ("ask_size", "invalid_decimal"),
        ("bid_size", "expected_exact_dict"),
    ],
)
def test_rejection_rejects_incompatible_field_code(field, code) -> None:
    with pytest.raises(ValueError):
        QuoteInputRejection("e", DECISION, "r", field, code)


def test_validation_requires_exactly_one_exact_concrete_outcome() -> None:
    value = normalize(valid_raw()).value
    rejection = assert_failure([], "$", "expected_exact_dict")
    for kwargs, error in [
        ({}, ValueError),
        ({"value": value, "rejection": rejection}, ValueError),
        ({"value": "bad"}, TypeError),
        ({"rejection": "bad"}, TypeError),
    ]:
        with pytest.raises(error):
            QuoteValidation(**kwargs)
