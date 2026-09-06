from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, Inexact, InvalidOperation, localcontext

import pytest

import options_lab.greek_inputs as api
from options_lab.contracts import ContractId
from options_lab.greeks import (
    FIXTURE_GREEK_METHOD,
    GreekInputs,
    GreekObservation,
    assess_greek_readiness,
    greek_input_hash,
)
from options_lab.observations import ObservationMeta
from options_lab.quote_content import identify_quote_content
from options_lab.quotes import QuoteObservation
from options_lab.underlying import UnderlyingQuote


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)
AS_OF = DECISION - timedelta(seconds=2)
ROOT_FIELDS = (
    "delta", "iv", "as_of", "available_at", "delta_unit", "iv_unit",
    "method_id", "method_version", "inputs", "input_hash", "source",
    "provider_record_id", "availability_basis",
)
INPUT_FIELDS = (
    "option_quote_hash", "underlying_quote_hash", "rate", "dividend_yield",
    "rate_unit", "dividend_unit", "assumptions_id",
)


def contract() -> ContractId:
    return ContractId(
        "SPY", date(2026, 9, 18), "call", Decimal("650"), 100,
        "standard-spy-100",
    )


def meta(record_id: str) -> ObservationMeta:
    return ObservationMeta(
        "fixture-sip", record_id, f"raw://{record_id}", "realtime", "genuine",
        "quote", AS_OF, AS_OF + timedelta(seconds=1), DECISION, "measured",
        f"fixture://capture/{record_id}",
    )


def option_quote() -> QuoteObservation:
    return QuoteObservation(
        contract(), meta("option-1"), Decimal("5.00"), Decimal("5.10"),
        AS_OF, AS_OF, 7, 9,
    )


def underlying_quote() -> UnderlyingQuote:
    return UnderlyingQuote(
        "SPY", meta("underlying-1"), Decimal("650.00"), Decimal("650.02"),
        AS_OF, AS_OF, 700, 900,
    )


def valid_raw(**changes: object) -> dict[str, object]:
    option = option_quote()
    underlying = underlying_quote()
    nested: dict[str, object] = {
        "option_quote_hash": identify_quote_content(option).content_hash,
        "underlying_quote_hash": identify_quote_content(underlying).content_hash,
        "rate": "-0.01",
        "dividend_yield": "0.0125",
        "rate_unit": FIXTURE_GREEK_METHOD.rate_unit,
        "dividend_unit": FIXTURE_GREEK_METHOD.dividend_unit,
        "assumptions_id": FIXTURE_GREEK_METHOD.assumptions_id,
    }
    parsed_inputs = GreekInputs(
        nested["option_quote_hash"], nested["underlying_quote_hash"],
        Decimal(nested["rate"]), Decimal(nested["dividend_yield"]),
        nested["rate_unit"], nested["dividend_unit"], nested["assumptions_id"],
    )
    raw: dict[str, object] = {
        "delta": "0.50",
        "iv": "0.20",
        "as_of": "2026-09-05T10:29:58-04:00",
        "available_at": "2026-09-05T14:29:59Z",
        "delta_unit": FIXTURE_GREEK_METHOD.delta_unit,
        "iv_unit": FIXTURE_GREEK_METHOD.iv_unit,
        "method_id": FIXTURE_GREEK_METHOD.method_id,
        "method_version": FIXTURE_GREEK_METHOD.method_version,
        "inputs": nested,
        "input_hash": greek_input_hash(
            parsed_inputs, contract=contract(), method=FIXTURE_GREEK_METHOD,
            as_of=AS_OF,
        ),
        "source": "fixture-greeks",
        "provider_record_id": "greek-1",
        "availability_basis": "measured",
    }
    raw.update(changes)
    return raw


def normalize(raw: object, **changes: object) -> api.GreekValidation:
    envelope = {
        "contract": contract(),
        "event_id": "event-1",
        "raw_ref": "raw://greek-1",
        "received_at": DECISION,
    }
    envelope.update(changes)
    return api.normalize_greek_observation(raw, **envelope)


def assert_failure(raw: object, field: str, code: str) -> api.GreekInputRejection:
    result = normalize(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


def set_path(raw: dict[str, object], path: str, value: object) -> None:
    if path.startswith("inputs."):
        raw["inputs"][path.removeprefix("inputs.")] = value
    else:
        raw[path] = value


def test_valid_body_normalizes_and_integrates_into_actual_readiness() -> None:
    result = normalize(valid_raw())

    assert type(result) is api.GreekValidation
    assert result.rejection is None and type(result.value) is GreekObservation
    greek = result.value
    assert greek.contract == contract()
    assert (greek.delta, greek.iv) == (Decimal("0.50"), Decimal("0.20"))
    assert greek.as_of == AS_OF
    assert greek.available_at == AS_OF + timedelta(seconds=1)
    assert greek.received_at == DECISION
    assert greek.raw_ref == "raw://greek-1"
    assert greek.provider_record_id == "greek-1"
    assert greek.inputs is not None
    assert (greek.inputs.rate, greek.inputs.dividend_yield) == (
        Decimal("-0.01"), Decimal("0.0125"),
    )
    readiness = assess_greek_readiness(
        greek, option_quote(), underlying_quote(), method=FIXTURE_GREEK_METHOD,
        decision_at=DECISION,
    )
    assert readiness.status == "READY"
    assert readiness.reasons == ()


def test_all_explicit_nullable_fields_remain_missing_facts_without_synthesis() -> None:
    raw = valid_raw(
        delta=None, iv=None, as_of=None, available_at=None, inputs=None,
        input_hash=None,
    )
    result = normalize(raw)

    assert result.rejection is None and result.value is not None
    assert (
        result.value.delta, result.value.iv, result.value.as_of,
        result.value.available_at, result.value.inputs, result.value.input_hash,
    ) == (None, None, None, None, None, None)

    raw = valid_raw(input_hash=None)
    for field in (
        "option_quote_hash", "underlying_quote_hash", "rate", "dividend_yield",
    ):
        raw["inputs"][field] = None
    result = normalize(raw)
    assert result.value is not None and result.value.inputs is not None
    assert (
        result.value.inputs.option_quote_hash,
        result.value.inputs.underlying_quote_hash,
        result.value.inputs.rate,
        result.value.inputs.dividend_yield,
    ) == (None, None, None, None)


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


class ContractSubclass(ContractId):
    pass


class ObservationSubclass(GreekObservation):
    pass


class RejectionSubclass(api.GreekInputRejection):
    pass


@pytest.mark.parametrize("raw", [None, [], "greek", object(), DictSubclass()])
def test_root_requires_an_exact_dictionary(raw: object) -> None:
    assert_failure(raw, "$", "expected_exact_dict")


@pytest.mark.parametrize("field", ROOT_FIELDS)
def test_every_root_field_is_mandatory(field: str) -> None:
    raw = valid_raw()
    del raw[field]
    assert_failure(raw, field, "missing")


@pytest.mark.parametrize("field", INPUT_FIELDS)
def test_every_non_null_nested_field_is_mandatory(field: str) -> None:
    raw = valid_raw()
    del raw["inputs"][field]
    assert_failure(raw, f"inputs.{field}", "missing")


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        ("delta", Decimal("0.5"), "invalid_type"),
        ("iv", True, "invalid_type"),
        ("inputs.rate", 0.01, "invalid_type"),
        ("inputs.dividend_yield", StringSubclass("0.01"), "invalid_type"),
        ("as_of", datetime(2026, 9, 5, 14, 29, 58, tzinfo=UTC), "invalid_type"),
        ("available_at", date(2026, 9, 5), "invalid_type"),
        ("delta_unit", StringSubclass("unit"), "invalid_type"),
        ("iv_unit", True, "invalid_type"),
        ("method_id", 1, "invalid_type"),
        ("method_version", None, "invalid_type"),
        ("input_hash", True, "invalid_type"),
        ("source", object(), "invalid_type"),
        ("provider_record_id", 1.0, "invalid_type"),
        ("availability_basis", False, "invalid_type"),
        ("inputs.option_quote_hash", StringSubclass("1" * 64), "invalid_type"),
        ("inputs.underlying_quote_hash", [], "invalid_type"),
        ("inputs.rate_unit", 1, "invalid_type"),
        ("inputs.dividend_unit", True, "invalid_type"),
        ("inputs.assumptions_id", None, "invalid_type"),
    ],
)
def test_each_scalar_family_rejects_wrong_exact_types(path: str, value: object, code: str) -> None:
    raw = valid_raw()
    set_path(raw, path, value)
    assert_failure(raw, path, code)


@pytest.mark.parametrize("path", ["delta", "iv", "inputs.rate", "inputs.dividend_yield"])
@pytest.mark.parametrize(
    "value", ["", "1e2", " 1", "1_0", "１２", "NaN", "Infinity", "--1"],
)
def test_numeric_fields_require_finite_ascii_fixed_point_strings(path: str, value: str) -> None:
    raw = valid_raw()
    set_path(raw, path, value)
    assert_failure(raw, path, "invalid_decimal")


@pytest.mark.parametrize(
    "path", ["input_hash", "inputs.option_quote_hash", "inputs.underlying_quote_hash"],
)
@pytest.mark.parametrize("value", ["", "a" * 63, "A" * 64, "g" * 64, "é" * 64])
def test_hash_fields_require_canonical_lowercase_sha256(path: str, value: str) -> None:
    raw = valid_raw()
    set_path(raw, path, value)
    assert_failure(raw, path, "invalid_value")


@pytest.mark.parametrize(
    "path",
    [
        "delta_unit", "iv_unit", "method_id", "method_version", "source",
        "provider_record_id", "inputs.rate_unit", "inputs.dividend_unit",
        "inputs.assumptions_id",
    ],
)
def test_required_strings_reject_empty_but_retain_unknown_nonempty_claims(path: str) -> None:
    raw = valid_raw()
    set_path(raw, path, "")
    assert_failure(raw, path, "invalid_value")

    raw = valid_raw()
    set_path(raw, path, "unknown exact claim")
    assert normalize(raw).value is not None


@pytest.mark.parametrize("value", ["MEASURED", "unknown", ""])
def test_availability_basis_has_one_exact_fixed_vocabulary(value: str) -> None:
    assert_failure(valid_raw(availability_basis=value), "availability_basis", "invalid_value")


@pytest.mark.parametrize("value", [[], DictSubclass(), {"unexpected": None}])
def test_inputs_requires_an_exact_seven_field_dictionary(value: object) -> None:
    code = "unknown_fields" if type(value) is dict else "expected_exact_dict"
    assert_failure(valid_raw(inputs=value), "inputs", code)


def test_fixed_two_level_structure_and_missing_precedence_is_frozen() -> None:
    raw = valid_raw()
    del raw["delta"]
    raw["inputs"]["secret"] = "classified"
    assert_failure(raw, "inputs", "unknown_fields")

    raw = valid_raw(delta="bad")
    del raw["inputs"]["rate"]
    assert_failure(raw, "inputs.rate", "missing")

    raw = valid_raw()
    del raw["source"]
    del raw["inputs"]["rate"]
    assert_failure(raw, "source", "missing")

    raw = valid_raw(delta="bad")
    raw["inputs"]["rate"] = "bad"
    assert_failure(raw, "delta", "invalid_decimal")

    raw = valid_raw(inputs=[])
    del raw["delta"]
    assert_failure(raw, "inputs", "expected_exact_dict")


def test_root_unknown_precedes_nested_unknown_and_missing_without_secret_echo() -> None:
    raw = valid_raw(root_secret="classified-root")
    raw["inputs"]["nested_secret"] = "classified-nested"
    del raw["delta"]
    rejection = assert_failure(raw, "$", "unknown_fields")
    assert "secret" not in repr(rejection)
    assert "classified" not in repr(rejection)


@pytest.mark.parametrize(
    "field",
    [
        "contract", "event_id", "event", "raw_ref", "received_at", "meta",
        "method_spec_hash", "status", "readiness", "availability_evidence_ref",
    ],
)
def test_trusted_or_derived_metadata_is_forbidden_in_the_raw_body(field: str) -> None:
    assert_failure(valid_raw(**{field: "claim"}), "$", "unknown_fields")


def test_scalar_parsing_descends_into_inputs_at_its_declared_position() -> None:
    raw = valid_raw(method_version="")
    raw["inputs"]["option_quote_hash"] = "BAD"
    assert_failure(raw, "method_version", "invalid_value")

    raw = valid_raw(input_hash="BAD", source="")
    raw["inputs"]["rate"] = "bad"
    assert_failure(raw, "inputs.rate", "invalid_decimal")

    raw = valid_raw()
    raw["inputs"]["option_quote_hash"] = "BAD"
    raw["inputs"]["rate"] = "bad"
    assert_failure(raw, "inputs.option_quote_hash", "invalid_value")


def test_signed_unbounded_finite_values_and_decimal_context_are_preserved() -> None:
    raw = valid_raw(delta="-1.0001", iv="-0.00")
    raw["inputs"]["rate"] = "+1" + "0" * 5000 + "."
    raw["inputs"]["dividend_yield"] = "-.5"
    with localcontext() as context:
        context.prec = 3
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        result = normalize(raw)
        after = (context.prec, context.flags.copy(), context.traps.copy())

    assert result.value is not None and result.value.inputs is not None
    assert result.value.delta == Decimal("-1.0001")
    assert result.value.iv.as_tuple() == Decimal("-0.00").as_tuple()
    assert result.value.inputs.rate == Decimal("+1" + "0" * 5000 + ".")
    assert result.value.inputs.dividend_yield == Decimal("-.5")
    assert after == before
    readiness = assess_greek_readiness(
        result.value, option_quote(), underlying_quote(), method=FIXTURE_GREEK_METHOD,
        decision_at=DECISION,
    )
    assert "delta_out_of_range" in readiness.reasons
    assert "iv_nonpositive" in readiness.reasons


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-05T14:29:58",
        "not-a-time",
        "0001-01-01T00:00:00+14:00",
    ],
)
@pytest.mark.parametrize("field", ["as_of", "available_at"])
def test_timestamps_reject_naive_malformed_and_utc_overflow(field: str, value: str) -> None:
    assert_failure(valid_raw(**{field: value}), field, "invalid_timestamp")


def test_raw_and_nested_mappings_are_detached_before_conversion(monkeypatch) -> None:
    source = valid_raw()
    original = api._parse_nullable_decimal
    calls = 0

    def mutate_source(value: object, field: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            source["source"] = "changed-source"
            source["inputs"]["rate_unit"] = "changed-unit"
        return original(value, field)

    monkeypatch.setattr(api, "_parse_nullable_decimal", mutate_source)
    result = normalize(source)
    assert result.value is not None and result.value.inputs is not None
    assert result.value.source == "fixture-greeks"
    assert result.value.inputs.rate_unit == FIXTURE_GREEK_METHOD.rate_unit


class CollidingKey:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        return hash("delta")

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


def test_hostile_keys_and_scalars_never_invoke_callbacks() -> None:
    root_key = CollidingKey()
    assert_failure({root_key: None}, "$", "unknown_fields")
    assert root_key.callbacks == []

    nested_key = CollidingKey()
    assert_failure(valid_raw(inputs={nested_key: None}), "inputs", "unknown_fields")
    assert nested_key.callbacks == []
    for path in ("delta", "as_of", "input_hash", "source", "availability_basis", "inputs.rate"):
        raw = valid_raw()
        set_path(raw, path, HostileScalar())
        assert_failure(raw, path, "invalid_type")


class ExplodingDict(dict):
    def __iter__(self):
        raise AssertionError("untrusted raw must not be inspected")


class ExplodingTimezone(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("trusted timezone failed")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"contract": object()}, TypeError),
        ({"contract": ContractSubclass(*contract().__dict__.values())}, TypeError),
        ({"event_id": 7}, TypeError),
        ({"event_id": StringSubclass("event")}, TypeError),
        ({"event_id": ""}, ValueError),
        ({"raw_ref": 7}, TypeError),
        ({"raw_ref": ""}, ValueError),
        ({"received_at": datetime(2026, 9, 5, 14, 30)}, ValueError),
        ({"received_at": datetime(2026, 9, 5, tzinfo=ExplodingTimezone())}, ValueError),
    ],
)
def test_trusted_envelope_is_validated_before_raw_inspection(changes, error) -> None:
    with pytest.raises(error):
        normalize(ExplodingDict(), **changes)


def test_rejection_evidence_is_safe_derived_canonical_and_immutable() -> None:
    received = datetime(2026, 9, 5, 10, 30, tzinfo=timezone(timedelta(hours=-4)))
    result = api.normalize_greek_observation(
        {"secret": "classified"}, contract=contract(), event_id="event-1",
        raw_ref="raw://greek/1", received_at=received,
    )

    assert result.rejection is not None
    assert (result.rejection.event_id, result.rejection.raw_ref) == (
        "event-1", "raw://greek/1",
    )
    assert result.rejection.received_at == DECISION
    assert result.rejection.stage == "greek_normalization"
    assert result.rejection.reasons == ("unknown_fields",)
    assert "secret" not in repr(result.rejection)
    assert "classified" not in repr(result.rejection)
    with pytest.raises(FrozenInstanceError):
        result.rejection.code = "missing"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("secret", "invalid_type"),
        ("$", "missing"),
        ("inputs", "invalid_type"),
        ("delta", "invalid_timestamp"),
        ("as_of", "invalid_decimal"),
        ("input_hash", "invalid_decimal"),
        ("availability_basis", "expected_exact_dict"),
        ("inputs.rate", "invalid_value"),
        ("inputs.option_quote_hash", "invalid_decimal"),
    ],
)
def test_rejection_rejects_incompatible_field_code_pairs(field: str, code: str) -> None:
    with pytest.raises(ValueError):
        api.GreekInputRejection("event", DECISION, "raw", field, code)


def test_validation_requires_exactly_one_exact_concrete_outcome() -> None:
    value = normalize(valid_raw()).value
    rejection = assert_failure([], "$", "expected_exact_dict")
    assert value is not None
    value_subclass = ObservationSubclass(**value.__dict__)
    rejection_subclass = RejectionSubclass(**rejection.__dict__)
    for kwargs, error in [
        ({}, ValueError),
        ({"value": value, "rejection": rejection}, ValueError),
        ({"value": "bad"}, TypeError),
        ({"value": value_subclass}, TypeError),
        ({"rejection": "bad"}, TypeError),
        ({"rejection": rejection_subclass}, TypeError),
    ]:
        with pytest.raises(error):
            api.GreekValidation(**kwargs)


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_process_control_and_programming_exceptions_propagate(monkeypatch, control) -> None:
    def stop(value: object, field: str):
        raise control()

    monkeypatch.setattr(api, "_parse_nullable_decimal", stop)
    with pytest.raises(control):
        normalize(valid_raw())
