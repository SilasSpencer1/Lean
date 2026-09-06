from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext

import pytest
import options_lab.contract_inputs as inputs
import options_lab.contracts as contracts


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def valid_raw(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "provider": "broker",
        "symbol": "SPY  260918C00650000 opaque",
        "contract": {
            "underlying": "SPY",
            "expiry": "2026-09-18",
            "right": "call",
            "strike": "650.00",
            "multiplier": 100,
            "deliverable_id": "standard-spy-100",
        },
        "available_at": "2026-09-05T10:30:00-04:00",
        "availability_basis": "measured",
        "availability_evidence_ref": "capture://mapping/1",
    }
    raw.update(changes)
    return raw


def normalize(raw: object):
    return inputs.normalize_provider_contract_mapping(
        raw,
        raw_ref="raw://mapping/1",
        event_id="event-1",
        received_at=DECISION,
    )


def assert_failure(raw: object, field: str, code: str):
    result = normalize(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


@pytest.mark.parametrize("right", ["call", "put"])
def test_valid_mapping_preserves_supplied_symbol_and_contract(right) -> None:
    raw = valid_raw(symbol="not-an-occ-symbol")
    raw["contract"]["right"] = right

    result = normalize(raw)

    assert result.rejection is None and result.value is not None
    assert result.value == contracts.ProviderContractMapping(
        "broker",
        "not-an-occ-symbol",
        contracts.ContractId(
            "SPY", date(2026, 9, 18), right, Decimal("650.00"), 100,
            "standard-spy-100",
        ),
        DECISION,
        "raw://mapping/1",
        "measured",
        "capture://mapping/1",
    )


def reference_raw(*, right: str = "call") -> dict[str, object]:
    return {
        "contract": {
            "underlying": "SPY", "expiry": "2026-09-18", "right": right,
            "strike": "650", "multiplier": 100,
            "deliverable_id": "standard-spy-100",
        },
        "components": [{"kind": "shares", "asset": "SPY", "quantity": "100"}],
        "source": "reference-feed",
        "provider_record_id": "reference-1",
        "availability_basis": "measured",
        "availability_evidence_ref": "capture://reference/1",
        "available_at": "2026-09-05T14:30:00Z",
        "listed_at": "2026-08-01T00:00:00Z",
        "listing_status": "listed",
        "effective_from": "2026-08-01T00:00:00Z",
        "effective_until": None,
    }


@pytest.mark.parametrize(
    ("mapping_changes", "reference_right", "reason"),
    [
        ({}, "call", None),
        ({}, "put", "identity_right_mismatch"),
        ({"available_at": "2026-09-05T14:30:00.000001Z"}, "call", "mapping_available_after_decision"),
        ({"availability_basis": "assumed"}, "call", "mapping_availability_not_measured"),
    ],
)
def test_independent_raw_inputs_flow_through_reference_assessment(
    mapping_changes, reference_right, reason
) -> None:
    mapping = normalize(valid_raw(**mapping_changes)).value
    reference = inputs.normalize_contract_reference(
        reference_raw(right=reference_right),
        raw_ref="raw://reference/1", event_id="event-2", received_at=DECISION,
    ).value
    assert mapping is not None and reference is not None

    assessment = contracts.assess_contract_reference(
        mapping, reference, decision_at=DECISION
    )

    assert assessment.reference_suitable is (reason is None)
    assert (reason is not None) is (reason in assessment.reasons)


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


@pytest.mark.parametrize("raw", [None, [], "mapping", object(), DictSubclass()])
def test_root_requires_exact_dictionary(raw) -> None:
    assert_failure(raw, "$", "expected_exact_dict")


def test_unknown_root_and_contract_fields_precede_missing_fields() -> None:
    raw = valid_raw(secret="classified")
    del raw["provider"]
    failure = assert_failure(raw, "$", "unknown_fields")
    assert "secret" not in repr(failure) and "classified" not in repr(failure)

    contract = {"secret": "classified"}
    assert_failure(valid_raw(contract=contract), "contract", "unknown_fields")


@pytest.mark.parametrize("field", tuple(valid_raw()))
def test_every_root_field_is_mandatory(field) -> None:
    raw = valid_raw()
    del raw[field]
    assert_failure(raw, field, "missing")


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("provider", "invalid_type"),
        ("symbol", "invalid_type"),
        ("contract", "expected_exact_dict"),
        ("available_at", "invalid_type"),
        ("availability_basis", "invalid_type"),
        ("availability_evidence_ref", "invalid_type"),
    ],
)
def test_null_is_not_missing_or_allowed(field, code) -> None:
    assert_failure(valid_raw(**{field: None}), field, code)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("provider", "", "invalid_value"),
        ("provider", StringSubclass("broker"), "invalid_type"),
        ("symbol", 1, "invalid_type"),
        ("availability_basis", "estimated", "invalid_value"),
        ("availability_evidence_ref", "", "invalid_value"),
        ("available_at", StringSubclass("2026-09-05T14:30:00Z"), "invalid_type"),
    ],
)
def test_root_scalars_reject_empty_wrong_and_subclass_values(field, value, code) -> None:
    assert_failure(valid_raw(**{field: value}), field, code)


class CollidingKey:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        return hash("provider")

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        raise AssertionError("must not compare hostile keys")

    def __str__(self) -> str:
        self.callbacks.append("str")
        raise AssertionError("must not stringify hostile keys")

    def __repr__(self) -> str:
        self.callbacks.append("repr")
        raise AssertionError("must not repr hostile keys")


@pytest.mark.parametrize("nested", [False, True])
def test_hostile_keys_never_invoke_callbacks(nested) -> None:
    key = CollidingKey()
    raw = valid_raw(contract={key: None}) if nested else {key: None}
    assert_failure(raw, "contract" if nested else "$", "unknown_fields")
    assert key.callbacks == []


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        ({"underlying": 1}, "contract.underlying", "invalid_type"),
        ({"expiry": "2026-02-30"}, "contract.expiry", "invalid_date"),
        ({"right": "CALL"}, "contract.right", "invalid_value"),
        ({"strike": "6.5e2"}, "contract.strike", "invalid_decimal"),
        ({"multiplier": IntSubclass(100)}, "contract.multiplier", "invalid_type"),
        ({"deliverable_id": ""}, "contract.deliverable_id", "invalid_value"),
    ],
)
def test_malformed_identity_uses_shared_contract_parser(change, field, code) -> None:
    raw = valid_raw()
    raw["contract"].update(change)
    assert_failure(raw, field, code)


def test_signed_money_preserves_precision_and_decimal_context() -> None:
    raw = valid_raw()
    raw["contract"].update(
        underlying="QQQ", expiry="2020-01-01", strike="-123456789.123456789",
        multiplier=-1,
    )
    with localcontext() as context:
        context.prec = 3
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        mapping = normalize(raw).value
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert mapping is not None
    assert mapping.contract.strike.as_tuple() == Decimal("-123456789.123456789").as_tuple()
    assert (
        mapping.contract.underlying,
        mapping.contract.expiry,
        mapping.contract.multiplier,
    ) == ("QQQ", date(2020, 1, 1), -1)
    assert after == before


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2026-09-05T14:30:00", "invalid_timestamp"),
        ("0001-01-01T00:00:00+14:00", "invalid_timestamp"),
        (datetime(2026, 9, 5, 14, 30, tzinfo=UTC), "invalid_type"),
    ],
)
def test_available_at_rejects_naive_overflow_and_raw_datetime(value, code) -> None:
    assert_failure(valid_raw(available_at=value), "available_at", code)


def test_offset_timestamp_normalizes_to_utc_and_source_mutation_isolated() -> None:
    raw = valid_raw(available_at="2026-09-05T09:30:00-05:00")
    mapping = normalize(raw).value
    raw["provider"] = "changed"
    raw["contract"]["right"] = "put"
    assert mapping is not None
    assert mapping.available_at == DECISION
    assert (mapping.provider, mapping.contract.right) == ("broker", "call")


def test_first_failure_follows_declared_field_order() -> None:
    raw = valid_raw(provider="", symbol=None, contract=[])
    assert_failure(raw, "provider", "invalid_value")
    del raw["provider"]
    del raw["symbol"]
    assert_failure(raw, "provider", "missing")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"raw_ref": ""}, ValueError),
        ({"raw_ref": StringSubclass("raw")}, TypeError),
        ({"event_id": 1}, TypeError),
        ({"received_at": datetime(2026, 9, 5)}, ValueError),
    ],
)
def test_invalid_envelope_precedes_hostile_raw_inspection(changes, error) -> None:
    key = CollidingKey()
    kwargs = {"raw_ref": "raw", "event_id": "event-1", "received_at": DECISION}
    with pytest.raises(error):
        inputs.normalize_provider_contract_mapping(
            {key: None}, **(kwargs | changes)
        )
    assert key.callbacks == []


def test_rejection_evidence_is_safe_derived_and_immutable() -> None:
    failure = assert_failure([], "$", "expected_exact_dict")
    assert (failure.event_id, failure.received_at, failure.raw_ref) == (
        "event-1", DECISION, "raw://mapping/1",
    )
    assert failure.stage == "provider_contract_mapping_normalization"
    assert failure.reasons == ("expected_exact_dict",)
    with pytest.raises(FrozenInstanceError):
        failure.code = "unknown_fields"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("components", "invalid_type"),
        ("source", "invalid_value"),
        ("listed_at", "invalid_timestamp"),
        ("$", "missing"),
        ("provider", "invalid_decimal"),
        ("contract.strike", "invalid_date"),
    ],
)
def test_rejection_rejects_reference_only_or_incompatible_field_codes(field, code) -> None:
    with pytest.raises(ValueError):
        inputs.ProviderContractMappingRejection(
            "event-1", DECISION, "raw://mapping/1", field, code
        )


def test_validation_requires_one_exact_concrete_mapping_outcome() -> None:
    value = normalize(valid_raw()).value
    rejection = assert_failure([], "$", "expected_exact_dict")

    class MappingSubclass(contracts.ProviderContractMapping):
        pass

    class RejectionSubclass(inputs.ProviderContractMappingRejection):
        pass

    reference = inputs.normalize_contract_reference(
        reference_raw(), raw_ref="ref", event_id="event-2", received_at=DECISION
    ).value
    reference_rejection = inputs.normalize_contract_reference(
        [], raw_ref="ref", event_id="event-2", received_at=DECISION
    ).rejection
    assert value is not None and rejection is not None and reference is not None
    for kwargs, error in [
        ({}, ValueError),
        ({"value": value, "rejection": rejection}, ValueError),
        ({"value": reference}, TypeError),
        ({"rejection": reference_rejection}, TypeError),
        ({"value": MappingSubclass(**value.__dict__)}, TypeError),
        ({"rejection": RejectionSubclass(**rejection.__dict__)}, TypeError),
    ]:
        with pytest.raises(error):
            inputs.ProviderContractMappingValidation(**kwargs)
