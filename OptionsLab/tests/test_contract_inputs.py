from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext

import options_lab.contracts as contracts
import pytest
from options_lab.contract_inputs import (
    ContractReferenceRejection,
    ContractReferenceValidation,
    normalize_contract_reference,
)

UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)

def valid_raw(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "contract": {
            "underlying": "SPY", "expiry": "2026-09-18", "right": "call",
            "strike": "650.00", "multiplier": 100,
            "deliverable_id": "standard-spy-100",
        },
        "components": [{"kind": "shares", "asset": "SPY", "quantity": "100.0"}],
        "source": "reference-feed",
        "provider_record_id": "reference-1",
        "availability_basis": "measured",
        "availability_evidence_ref": "capture://reference/1",
        "available_at": "2026-09-05T10:30:00-04:00",
        "listed_at": "2026-08-01T00:00:00Z",
        "listing_status": "listed",
        "effective_from": "2026-08-01T00:00:00+00:00",
        "effective_until": None,
    }
    raw.update(changes)
    return raw


def normalize(raw: object) -> ContractReferenceValidation:
    return normalize_contract_reference(
        raw, raw_ref="raw://reference/1", event_id="event-1", received_at=DECISION
    )


def assert_failure(raw: object, field: str, code: str) -> ContractReferenceRejection:
    result = normalize(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


@pytest.mark.parametrize("right", ["call", "put"])
def test_valid_reference_normalizes_and_flows_through_assessment(right) -> None:
    raw = valid_raw()
    raw["contract"]["right"] = right
    result = normalize(raw)
    assert result.rejection is None and result.value is not None
    reference = result.value
    assert reference.contract == contracts.ContractId(
        "SPY", date(2026, 9, 18), right, Decimal("650.00"), 100, "standard-spy-100"
    )
    assert reference.components == (
        contracts.DeliverableComponent("shares", "SPY", Decimal("100.0")),
    )
    assert reference.raw_ref == "raw://reference/1"
    assert reference.available_at == DECISION
    mapping = contracts.ProviderContractMapping(
        "broker", "opaque", reference.contract, DECISION, "mapping", "measured", "proof"
    )
    assert contracts.assess_contract_reference(
        mapping, reference, decision_at=DECISION
    ).reference_suitable
    for changes in ({"components": None}, {"components": []}, {"listed_at": None}):
        assert normalize(valid_raw(**changes)).value is not None


class DictSubclass(dict): pass
class ListSubclass(list): pass
class StringSubclass(str): pass
class IntSubclass(int): pass


@pytest.mark.parametrize("raw", [None, [], "record", object(), DictSubclass()])
def test_root_requires_an_exact_dictionary(raw) -> None:
    assert_failure(raw, "$", "expected_exact_dict")


def test_unknown_fields_precede_missing_fields_without_leaking_source() -> None:
    raw = valid_raw(secret_provider_key="classified")
    del raw["contract"]
    failure = assert_failure(raw, "$", "unknown_fields")
    assert "secret_provider_key" not in repr(failure)
    assert "classified" not in repr(failure)
    assert_failure(valid_raw(raw_ref="external"), "$", "unknown_fields")


@pytest.mark.parametrize("field", tuple(valid_raw()))
def test_every_root_field_is_mandatory(field) -> None:
    raw = valid_raw()
    del raw[field]
    assert_failure(raw, field, "missing")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("source", None, "invalid_type"), ("source", "", "invalid_value"),
        ("source", StringSubclass("feed"), "invalid_type"),
        ("provider_record_id", 1, "invalid_type"), ("available_at", None, "invalid_type"),
        ("availability_basis", "estimated", "invalid_value"),
        ("availability_evidence_ref", "", "invalid_value"),
        ("listed_at", datetime(2026, 8, 1, tzinfo=UTC), "invalid_type"),
        ("listing_status", "active", "invalid_value"), ("effective_from", date(2026, 8, 1), "invalid_type"),
        ("effective_until", "not-a-time", "invalid_timestamp"),
    ],
)
def test_root_scalars_use_bounded_codes(field, value, code) -> None:
    assert_failure(valid_raw(**{field: value}), field, code)


class CollidingKey:
    def __init__(self) -> None: self.callbacks: list[str] = []
    def __hash__(self) -> int: return hash("contract")
    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        raise AssertionError("must not compare hostile keys")
    def __str__(self) -> str:
        self.callbacks.append("str")
        raise AssertionError("must not stringify hostile keys")
    def __repr__(self) -> str:
        self.callbacks.append("repr")
        raise AssertionError("must not repr hostile keys")


def test_non_string_colliding_key_never_invokes_callbacks() -> None:
    key = CollidingKey()
    assert_failure({key: None}, "$", "unknown_fields")
    assert key.callbacks == []


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        ({"underlying": 1}, "contract.underlying", "invalid_type"), ({"underlying": ""}, "contract.underlying", "invalid_value"),
        ({"expiry": date(2026, 9, 18)}, "contract.expiry", "invalid_type"), ({"expiry": "2026-02-30"}, "contract.expiry", "invalid_date"),
        ({"right": True}, "contract.right", "invalid_type"), ({"right": "CALL"}, "contract.right", "invalid_value"),
        ({"strike": Decimal("650")}, "contract.strike", "invalid_type"), ({"strike": "6.5e2"}, "contract.strike", "invalid_decimal"),
        ({"multiplier": True}, "contract.multiplier", "invalid_type"), ({"multiplier": IntSubclass(100)}, "contract.multiplier", "invalid_type"),
        ({"deliverable_id": ""}, "contract.deliverable_id", "invalid_value"),
    ],
)
def test_contract_scalars_use_precise_paths(change, field, code) -> None:
    raw = valid_raw()
    raw["contract"].update(change)
    assert_failure(raw, field, code)


@pytest.mark.parametrize(
    ("contract", "field", "code"),
    [
        ([], "contract", "expected_exact_dict"),
        (DictSubclass(), "contract", "expected_exact_dict"),
        ({"unexpected": 1}, "contract", "unknown_fields"),
        ({"expiry": "2026-09-18"}, "contract.underlying", "missing"),
    ],
)
def test_contract_shape_is_exact(contract, field, code) -> None:
    assert_failure(valid_raw(contract=contract), field, code)


@pytest.mark.parametrize(
    ("components", "field", "code"),
    [
        ((), "components", "invalid_type"), (ListSubclass(), "components", "invalid_type"),
        ([[]], "components[]", "expected_exact_dict"), ([DictSubclass()], "components[]", "expected_exact_dict"),
        ([{"unexpected": 1}], "components[]", "unknown_fields"),
        ([{"kind": "shares", "asset": "SPY"}], "components[].quantity", "missing"),
        ([{"kind": 1, "asset": "SPY", "quantity": "100"}], "components[].kind", "invalid_type"), ([{"kind": "stock", "asset": "SPY", "quantity": "100"}], "components[].kind", "invalid_value"),
        ([{"kind": "shares", "asset": "", "quantity": "100"}], "components[].asset", "invalid_value"), ([{"kind": "shares", "asset": "SPY", "quantity": 100}], "components[].quantity", "invalid_type"),
        ([{"kind": "shares", "asset": "SPY", "quantity": "NaN"}], "components[].quantity", "invalid_decimal"),
        ([{"kind": "shares", "asset": "", "quantity": "100"}, {"kind": "bad", "asset": "SPY", "quantity": "100"}], "components[].asset", "invalid_value"),
    ],
)
def test_component_shape_and_scalars_are_strict(components, field, code) -> None:
    assert_failure(valid_raw(components=components), field, code)


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "1e2", "1_000", " 1", "1 ", ".", "", 1, 1.0, True]
)
def test_money_rejects_non_fixed_point_or_non_string_inputs(value) -> None:
    raw = valid_raw()
    raw["contract"]["strike"] = value
    code = "invalid_decimal" if type(value) is str else "invalid_type"
    assert_failure(raw, "contract.strike", code)


@pytest.mark.parametrize("value", ["+1.", ".5", "-0.00", "123456789.123456789"])
def test_money_preserves_sign_precision_and_decimal_context(value) -> None:
    raw = valid_raw()
    raw["contract"]["strike"] = value
    with localcontext() as context:
        context.prec = 3
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        result = normalize(raw)
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert result.value is not None
    assert result.value.contract.strike.as_tuple() == Decimal(value).as_tuple()
    assert after == before


@pytest.mark.parametrize("value", ["20260918", "2026-W38-5", "2026-9-18", "2025-02-29"])
def test_expiry_requires_ascii_calendar_date(value) -> None:
    raw = valid_raw()
    raw["contract"]["expiry"] = value
    assert_failure(raw, "contract.expiry", "invalid_date")


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2026-09-05T14:30:00", "invalid_timestamp"),
        ("bad", "invalid_timestamp"),
        ("0001-01-01T00:00:00+14:00", "invalid_timestamp"),
        (datetime(2026, 9, 5, 14, 30, tzinfo=UTC), "invalid_type"),
        (StringSubclass("2026-09-05T14:30:00Z"), "invalid_type"),
    ],
)
def test_timestamp_boundaries_reject_safely(value, code) -> None:
    assert_failure(valid_raw(available_at=value), "available_at", code)


def test_valid_leap_date_and_offset_timestamp_normalize_to_utc() -> None:
    raw = valid_raw(available_at="2024-02-29T09:30:00-05:00")
    raw["contract"]["expiry"] = "2024-02-29"
    reference = normalize(raw).value
    assert reference is not None
    assert reference.contract.expiry == date(2024, 2, 29)
    assert reference.available_at == datetime(2024, 2, 29, 14, 30, tzinfo=UTC)


def test_adverse_facts_are_retained_for_typed_assessment() -> None:
    raw = valid_raw(
        components=[
            {"kind": "cash", "asset": "USD", "quantity": "0"},
            {"kind": "cash", "asset": "USD", "quantity": "-1"},
        ],
        available_at="2026-09-06T00:00:00Z", listed_at=None,
        listing_status="unknown", effective_from="2026-09-07T00:00:00Z",
        effective_until="2026-09-06T00:00:00Z",
    )
    raw["contract"].update(
        underlying="QQQ", strike="-0.1", multiplier=0, deliverable_id="adjusted"
    )
    reference = normalize(raw).value
    assert reference is not None and reference.components[1].quantity == Decimal("-1")
    mapping = contracts.ProviderContractMapping(
        "p", "opaque", reference.contract, DECISION, "m", "measured", "proof"
    )
    reasons = contracts.assess_contract_reference(mapping, reference, decision_at=DECISION).reasons
    expected = {
        "unsupported_underlying", "unsupported_multiplier", "nonpositive_strike",
        "effective_interval_invalid", "deliverable_unsupported",
    }
    assert expected <= set(reasons)


def test_source_mutation_cannot_change_normalized_records() -> None:
    raw = valid_raw()
    reference = normalize(raw).value
    raw["contract"]["underlying"] = "QQQ"
    raw["components"][0]["quantity"] = "1"
    assert reference is not None and reference.contract.underlying == "SPY"
    assert reference.components[0].quantity == Decimal("100.0")


def test_first_parse_failure_follows_declaration_order() -> None:
    raw = valid_raw(source="", components=())
    raw["contract"].update(expiry="bad", right="BAD")
    assert_failure(raw, "contract.expiry", "invalid_date")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"raw_ref": ""}, ValueError), ({"raw_ref": StringSubclass("r")}, TypeError),
        ({"event_id": 7}, TypeError),
        ({"received_at": datetime(2026, 9, 5)}, ValueError),
    ],
)
def test_invalid_trusted_envelope_raises(changes, error) -> None:
    kwargs = {"raw_ref": "r", "event_id": "e", "received_at": DECISION}
    with pytest.raises(error):
        normalize_contract_reference(valid_raw(), **(kwargs | changes))


def test_rejection_evidence_is_derived_and_immutable() -> None:
    failure = assert_failure([], "$", "expected_exact_dict")
    assert (failure.event_id, failure.received_at, failure.raw_ref) == (
        "event-1", DECISION, "raw://reference/1"
    )
    assert failure.stage == "contract_reference_normalization"
    assert failure.reasons == ("expected_exact_dict",)
    with pytest.raises(FrozenInstanceError):
        failure.code = "unknown_fields"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("secret", "invalid_type"), ("$", "missing"),
        ("contract.strike", "invalid_date"), ("components", "invalid_decimal"),
        ("available_at", "invalid_date"),
    ],
)
def test_rejection_rejects_incompatible_field_code(field, code) -> None:
    with pytest.raises(ValueError):
        ContractReferenceRejection("e", DECISION, "r", field, code)


def test_validation_requires_exactly_one_concrete_outcome() -> None:
    value = normalize(valid_raw()).value
    rejection = assert_failure([], "$", "expected_exact_dict")
    for kwargs, error in [
        ({}, ValueError), ({"value": value, "rejection": rejection}, ValueError),
        ({"value": "bad"}, TypeError), ({"rejection": "bad"}, TypeError),
    ]:
        with pytest.raises(error):
            ContractReferenceValidation(**kwargs)
