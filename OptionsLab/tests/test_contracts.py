from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest
import options_lab.contracts as api


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def contract(**changes):
    values = {
        "underlying": "SPY",
        "expiry": date(2026, 9, 18),
        "right": "call",
        "strike": Decimal("650"),
        "multiplier": 100,
        "deliverable_id": "standard-spy-100",
    }
    values.update(changes)
    return api.ContractId(**values)


def mapping(**changes):
    values = {
        "provider": "broker",
        "symbol": "SPY CALL opaque label",
        "contract": contract(),
        "available_at": DECISION,
        "raw_ref": "raw://mapping/1",
        "availability_basis": "measured",
        "availability_evidence_ref": "capture://mapping/1",
    }
    values.update(changes)
    return api.ProviderContractMapping(**values)


def reference(**changes):
    values = {
        "contract": contract(),
        "components": (
            api.DeliverableComponent("shares", "SPY", Decimal("100")),
        ),
        "source": "reference-feed",
        "provider_record_id": "reference-1",
        "raw_ref": "raw://reference/1",
        "availability_basis": "measured",
        "availability_evidence_ref": "capture://reference/1",
        "available_at": DECISION,
        "listed_at": DECISION - timedelta(days=30),
        "listing_status": "listed",
        "effective_from": DECISION - timedelta(days=30),
        "effective_until": None,
    }
    values.update(changes)
    return api.ContractReference(**values)


def assess(*, mapped=None, ref=None, decision_at=DECISION):
    return api.assess_contract_reference(
        mapping() if mapped is None else mapped,
        reference() if ref is None else ref,
        decision_at=decision_at,
    )


@pytest.mark.parametrize("right", ["call", "put"])
def test_standard_contract_reference_is_suitable(right) -> None:
    identity = contract(right=right, strike=Decimal("650.0"))
    result = assess(
        mapped=mapping(contract=identity),
        ref=reference(contract=contract(right=right, strike=Decimal("650.00"))),
    )

    assert result.reference_suitable is True
    assert result.reasons == ()
    assert result.decision_at == DECISION


@pytest.mark.parametrize(
    ("field", "different", "reason"),
    [
        ("underlying", "QQQ", "identity_underlying_mismatch"),
        ("expiry", date(2026, 9, 19), "identity_expiry_mismatch"),
        ("right", "put", "identity_right_mismatch"),
        ("strike", Decimal("651"), "identity_strike_mismatch"),
        ("multiplier", 10, "identity_multiplier_mismatch"),
        ("deliverable_id", "adjusted-1", "identity_deliverable_id_mismatch"),
    ],
)
def test_each_identity_mismatch_retains_both_claims(field, different, reason) -> None:
    mapped = mapping()
    ref = reference(contract=contract(**{field: different}))

    result = assess(mapped=mapped, ref=ref)

    assert reason in result.reasons
    assert result.mapping is mapped
    assert result.reference is ref
    assert getattr(result.mapping.contract, field) != getattr(result.reference.contract, field)


@pytest.mark.parametrize(
    ("components", "reason"),
    [
        (None, "deliverable_unknown"),
        ((), "deliverable_unsupported"),
        (("shares", "SPY", Decimal("99")), "deliverable_unsupported"),
        (("shares", "QQQ", Decimal("100")), "deliverable_unsupported"),
        (("other", "SPY", Decimal("100")), "deliverable_unsupported"),
    ],
)
def test_unknown_or_nonstandard_deliverable_is_retained_and_refused(components, reason) -> None:
    if components is not None and components:
        components = (api.DeliverableComponent(*components),)
    result = assess(ref=reference(components=components))

    assert result.reference.components == components
    assert reason in result.reasons


@pytest.mark.parametrize(
    "components",
    [
        (("shares", "SPY", "100"), ("cash", "USD", "5")),
        (("shares", "SPY", "100"), ("other", "XYZ", "1")),
        (("shares", "SPY", "50"), ("shares", "SPY", "50")),
    ],
)
def test_extra_or_split_deliverable_components_are_not_aggregated(components) -> None:
    complete = tuple(
        api.DeliverableComponent(kind, asset, Decimal(quantity))
        for kind, asset, quantity in components
    )

    result = assess(ref=reference(components=complete))

    assert result.reference.components == complete
    assert "deliverable_unsupported" in result.reasons


@pytest.mark.parametrize(
    ("contract_changes", "reason"),
    [
        ({"underlying": "QQQ"}, "unsupported_underlying"),
        ({"multiplier": 10}, "unsupported_multiplier"),
        ({"strike": Decimal("0")}, "nonpositive_strike"),
        ({"strike": Decimal("-1")}, "nonpositive_strike"),
    ],
)
def test_well_typed_unsupported_identity_facts_are_retained(contract_changes, reason) -> None:
    identity = contract(**contract_changes)
    result = assess(
        mapped=mapping(contract=identity),
        ref=reference(contract=identity),
    )

    assert result.mapping.contract is identity
    assert reason in result.reasons


def test_expiry_and_matching_adjusted_identifier_are_retained_without_dte_policy() -> None:
    identity = contract(
        expiry=date(2026, 9, 4),
        deliverable_id="adjusted-reference-label",
    )

    result = assess(
        mapped=mapping(contract=identity),
        ref=reference(contract=identity),
    )

    assert result.reference_suitable
    assert result.reference.contract is identity


@pytest.mark.parametrize(
    ("mapping_delta", "reference_delta", "expected"),
    [
        (timedelta(0), timedelta(0), ()),
        (timedelta(microseconds=1), timedelta(0), ("mapping_available_after_decision",)),
        (timedelta(0), timedelta(microseconds=1), ("reference_available_after_decision",)),
    ],
)
def test_mapping_and_reference_availability_have_independent_inclusive_boundaries(
    mapping_delta, reference_delta, expected
) -> None:
    result = assess(
        mapped=mapping(available_at=DECISION + mapping_delta),
        ref=reference(available_at=DECISION + reference_delta),
    )

    assert result.reasons == expected


@pytest.mark.parametrize(
    ("mapping_basis", "reference_basis", "expected"),
    [
        ("assumed", "measured", ("mapping_availability_not_measured",)),
        ("measured", "assumed", ("reference_availability_not_measured",)),
        (
            "assumed",
            "assumed",
            ("mapping_availability_not_measured", "reference_availability_not_measured"),
        ),
    ],
)
def test_assumed_availability_is_retained_but_unsuitable(
    mapping_basis, reference_basis, expected
) -> None:
    result = assess(
        mapped=mapping(availability_basis=mapping_basis),
        ref=reference(availability_basis=reference_basis),
    )

    assert result.mapping.availability_basis == mapping_basis
    assert result.reference.availability_basis == reference_basis
    assert result.reasons == expected


@pytest.mark.parametrize(
    ("reference_changes", "expected"),
    [
        ({"effective_from": DECISION}, ()),
        ({"effective_from": DECISION + timedelta(microseconds=1)}, ("reference_not_yet_effective",)),
        ({"effective_until": DECISION + timedelta(microseconds=1)}, ()),
        ({"effective_until": DECISION}, ("reference_no_longer_effective",)),
        (
            {"effective_from": DECISION + timedelta(days=1), "available_at": DECISION - timedelta(days=1)},
            ("reference_not_yet_effective",),
        ),
        (
            {"effective_from": DECISION, "effective_until": DECISION - timedelta(days=1)},
            ("reference_no_longer_effective", "effective_interval_invalid"),
        ),
    ],
)
def test_effective_interval_is_half_open_and_independent_of_announcement_time(
    reference_changes, expected
) -> None:
    result = assess(ref=reference(**reference_changes))

    assert result.reasons == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"listed_at": None}, ("listing_time_unknown",)),
        ({"listed_at": DECISION + timedelta(microseconds=1)}, ("listed_after_decision",)),
        ({"listing_status": "inactive"}, ("listing_status_inactive",)),
        ({"listing_status": "unknown"}, ("listing_status_unknown",)),
    ],
)
def test_listing_evidence_must_be_known_current_and_listed(changes, expected) -> None:
    assert assess(ref=reference(**changes)).reasons == expected


def test_offset_datetimes_normalize_to_utc_and_equal_instants_pass() -> None:
    eastern = timezone(timedelta(hours=-4))
    local = datetime(2026, 9, 5, 10, 30, tzinfo=eastern)
    result = assess(
        mapped=mapping(available_at=local),
        ref=reference(available_at=local, listed_at=local, effective_from=local),
        decision_at=local,
    )

    assert result.reference_suitable
    assert result.mapping.available_at == DECISION
    assert result.reference.available_at == DECISION
    assert result.reference.listed_at == DECISION
    assert result.decision_at == DECISION


@pytest.mark.parametrize(
    "factory",
    [
        lambda: api.ContractId("SPY", datetime(2026, 9, 18), "call", Decimal("1"), 100, "d"),
        lambda: api.ContractId("SPY", date(2026, 9, 18), "CALL", Decimal("1"), 100, "d"),
        lambda: api.ContractId("SPY", date(2026, 9, 18), "call", 1, 100, "d"),
        lambda: api.ContractId("SPY", date(2026, 9, 18), "call", Decimal("NaN"), 100, "d"),
        lambda: api.ContractId("SPY", date(2026, 9, 18), "call", Decimal("1"), True, "d"),
        lambda: api.DeliverableComponent("shares", "SPY", Decimal("Infinity")),
        lambda: api.ProviderContractMapping("p", "s", "not-contract", DECISION, "r", "measured", "e"),
        lambda: reference(components=[api.DeliverableComponent("shares", "SPY", Decimal("100"))]),
        lambda: reference(listed_at=datetime(2026, 9, 5)),
        lambda: mapping(available_at="2026-09-05T14:30:00Z"),
    ],
)
def test_records_reject_wrong_exact_types_nonfinite_values_and_mutable_nesting(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_nonpositive_multiplier_and_component_quantity_remain_representable() -> None:
    identity = contract(multiplier=0)
    component = api.DeliverableComponent("shares", "SPY", Decimal("0"))
    result = assess(
        mapped=mapping(contract=identity),
        ref=reference(contract=identity, components=(component,)),
    )

    assert result.reference.contract.multiplier == 0
    assert result.reference.components == (component,)
    assert result.reasons == ("unsupported_multiplier", "deliverable_unsupported")


class CallbackTimezone(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("secret")

    def dst(self, dt):
        return timedelta(0)


def test_timezone_callback_failure_becomes_descriptive_validation_error() -> None:
    bad_time = datetime(2026, 9, 5, tzinfo=CallbackTimezone())
    with pytest.raises(ValueError, match="available_at timezone evaluation failed") as error:
        mapping(available_at=bad_time)
    assert "secret" not in str(error.value)


def test_assessment_reasons_are_canonical_derived_and_immutable() -> None:
    mapped = mapping(
        contract=contract(underlying="QQQ", multiplier=10, strike=Decimal("0")),
        available_at=DECISION + timedelta(microseconds=1),
        availability_basis="assumed",
    )
    ref = reference(
        contract=contract(underlying="QQQ", multiplier=10, strike=Decimal("0")),
        components=None,
        available_at=DECISION + timedelta(microseconds=1),
        availability_basis="assumed",
        listed_at=None,
        listing_status="unknown",
        effective_from=DECISION + timedelta(days=1),
    )

    first = assess(mapped=mapped, ref=ref)
    second = assess(mapped=mapped, ref=ref)

    assert first == second
    assert first.reasons == (
        "mapping_available_after_decision",
        "reference_available_after_decision",
        "mapping_availability_not_measured",
        "reference_availability_not_measured",
        "listing_time_unknown",
        "listing_status_unknown",
        "reference_not_yet_effective",
        "unsupported_underlying",
        "unsupported_multiplier",
        "nonpositive_strike",
        "deliverable_unknown",
    )
    assert first.reference_suitable is False
    with pytest.raises(TypeError):
        api.ContractReferenceAssessment(mapped, ref, DECISION, ())
    with pytest.raises(FrozenInstanceError):
        first.reasons = ()


@pytest.mark.parametrize(
    ("mapped", "ref", "decision_at"),
    [
        ("mapping", None, DECISION),
        (None, "reference", DECISION),
        (None, None, datetime(2026, 9, 5)),
        (None, None, "2026-09-05T14:30:00Z"),
    ],
)
def test_assessment_rejects_invalid_trusted_inputs(mapped, ref, decision_at) -> None:
    with pytest.raises((TypeError, ValueError)):
        assess(
            mapped=mapping() if mapped is None else mapped,
            ref=reference() if ref is None else ref,
            decision_at=decision_at,
        )
