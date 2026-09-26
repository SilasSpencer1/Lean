"""Exercise order claims and reprovable fixture-member admission."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest


AT = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)


def body(**changes):
    """Return one complete raw report with only specified source changes."""
    raw = dict(schema_version=1, account_id="acct-1", source="alpaca",
               provider_event_id=None, broker_order_id="broker-1",
               client_order_id="client-1", replaces_broker_order_id=None,
               replaces_client_order_id=None, replaced_by_broker_order_id=None,
               replaced_by_client_order_id=None, decision_id="decision-1",
               contract=dict(underlying="SPY", expiry="2026-09-04", right="call",
                             strike="500", multiplier=100, deliverable_id="SPY-100"),
               role="buy_entry", event_at="2026-09-04T14:05:00Z",
               available_at="2026-09-04T14:05:00Z", provider_sequence=None,
               status="partial", pending=True, terminal=False,
               quantity_basis="cumulative", raw_filled_quantity="1",
               cumulative_fill_notional_usd="510", cumulative_fees_usd=None)
    raw.update(changes)
    return raw


def test_missing_provider_id_redelivery_keeps_semantic_identity():
    """A new receipt must not turn the same provider report into a new event."""
    try:
        from options_lab.order_inputs import normalize_order_update
    except ModuleNotFoundError:
        pytest.fail("order_inputs normalizer is missing")
    first = normalize_order_update(body(), event_id="receipt-1", raw_ref="one",
                                   received_at=AT)
    second = normalize_order_update(body(), event_id="receipt-2", raw_ref="two",
                                    received_at=AT.replace(second=1), receive_sequence=2)
    assert first.rejection is None and second.rejection is None
    assert first.value.normalized_event_id == second.value.normalized_event_id
    assert first.value.semantic_content_hash == second.value.semantic_content_hash
    assert first.value.safe_cumulative_quantity == 1
    assert first.value.cumulative_fill_notional_usd == Decimal("510")


def test_provider_identity_is_namespaced_and_content_conflicts_remain_visible():
    """A repeated provider ID cannot hide changed cumulative content or cross sources."""
    from options_lab.order_inputs import normalize_order_update
    def parsed(raw):
        return normalize_order_update(raw, event_id="receipt", raw_ref="test",
                                      received_at=AT).value
    first = parsed(body(provider_event_id="provider-42"))
    changed = parsed(body(provider_event_id="provider-42", raw_filled_quantity="2"))
    other = parsed(body(provider_event_id="provider-42", source="lean"))
    assert first.normalized_event_id == changed.normalized_event_id
    assert first.semantic_content_hash != changed.semantic_content_hash
    assert first.normalized_event_id != other.normalized_event_id
    assert changed.safe_cumulative_quantity == 2


def test_reported_quantities_and_money_preserve_adverse_facts():
    """Policy cannot erase signed, fractional, incremental or unknown source facts."""
    from options_lab.order_inputs import normalize_order_update
    for quantity, basis in (("-1", "cumulative"), ("0.5", "cumulative"),
                            ("1", "incremental"), ("1", "unknown"),
                            ("9223372036854775808", "cumulative")):
        result = normalize_order_update(body(raw_filled_quantity=quantity,
            quantity_basis=basis, cumulative_fill_notional_usd="-100.25",
            cumulative_fees_usd="-0.30", pending=True, terminal=True),
            event_id="receipt", raw_ref="test", received_at=AT)
        assert result.rejection is None
        assert result.value.raw_filled_quantity == Decimal(quantity)
        assert result.value.safe_cumulative_quantity is None
        assert result.value.cumulative_fill_notional_usd == Decimal("-100.25")
        assert result.value.cumulative_fees_usd == Decimal("-0.30")
        assert result.value.pending is True and result.value.terminal is True


def test_unlinked_or_missing_facts_stay_uncertain():
    """An orphan report still carries its observed fill and unknown money."""
    from options_lab.order_inputs import normalize_order_update
    result = normalize_order_update(body(broker_order_id=None, client_order_id=None,
        contract=None, decision_id=None, role="unknown", event_at=None,
        available_at=None, cumulative_fees_usd=None), event_id="receipt",
        raw_ref="test", received_at=AT)
    assert result.rejection is None
    assert result.value.identity_status == "unlinked"
    assert result.value.safe_cumulative_quantity == 1
    assert result.value.cumulative_fees_usd is None
    assert result.value.event_at is None and result.value.available_at is None


@pytest.mark.parametrize("change,field", [
    ({"raw_filled_quantity": "NaN"}, "raw_filled_quantity"),
    ({"raw_filled_quantity": "1e10"}, "raw_filled_quantity"),
    ({"provider_sequence": True}, "provider_sequence"),
    ({"terminal": "yes"}, "terminal"),
    ({"raw_filled_quantity": "1" * 129}, "raw_filled_quantity"),
    ({"source": ""}, "source"),
    ({"contract": {"underlying": "SPY"}}, "contract.expiry"),
])
def test_malformed_claim_has_safe_field_diagnostic(change, field):
    """Malformed claims cannot become apparent zero-filled order updates."""
    from options_lab.order_inputs import normalize_order_update
    result = normalize_order_update(body(**change), event_id="receipt", raw_ref="test",
                                    received_at=AT)
    assert result.value is None
    assert result.rejection.field == field
    assert result.rejection.code in ("invalid_decimal", "invalid_value", "invalid_type",
                                     "too_long", "missing")


def test_caller_cannot_construct_derived_claim_or_validation():
    """Derived quantities and event identities must come from normalization."""
    from options_lab.order_inputs import OrderUpdate, OrderInputValidation, OrderInputRejection
    for cls in (OrderUpdate, OrderInputValidation, OrderInputRejection):
        with pytest.raises(TypeError):
            cls()


def test_invalid_trusted_receipt_arguments_raise_boundary_errors():
    """Malformed caller receipt evidence must not masquerade as a body rejection."""
    from options_lab.order_inputs import normalize_order_update
    with pytest.raises(TypeError):
        normalize_order_update(body(), event_id=123, raw_ref="test", received_at=AT)
    with pytest.raises(ValueError):
        normalize_order_update(body(), event_id="", raw_ref="test", received_at=AT)
    with pytest.raises(ValueError):
        normalize_order_update(body(), event_id="receipt", raw_ref="test",
                               received_at=AT, receive_sequence=-1)


def admitted_order_fixture():
    """Read and verify the one registered order source in this process."""
    from options_lab.admission import verify_fixture_bundle
    fixture_id = "p19-order-update-v1"
    path = Path(__file__).parent / "fixtures" / (fixture_id + ".json")
    assert path.is_file(), "registered P19 fixture is missing"
    checked = verify_fixture_bundle(fixture_id, path.read_bytes(),
        event_id="fixture-read", raw_ref="synthetic://p19/read", received_at=AT)
    assert checked.value is not None, checked.rejection
    return checked.value


def test_actual_member_proof_binds_profile_and_report():
    """An admitted update retains the fresh member and source proof inputs."""
    from options_lab.order_inputs import admit_order_update
    original = admitted_order_fixture()
    result = admit_order_update(original, "fill-1")
    assert result.source_failure is None
    assert result.supplied_fixture is original
    assert result.record_id == "fill-1"
    assert result.manifest is not original
    assert result.member.record_id == "fill-1"
    assert result.validation.rejection is None
    assert result.validation.value.safe_cumulative_quantity == 1
    assert result.validation.value.source == "alpaca"


def test_actual_malformed_member_keeps_source_and_rejection():
    """A valid source envelope with bad order body remains unresolved evidence."""
    from options_lab.order_inputs import admit_order_update
    result = admit_order_update(admitted_order_fixture(), "malformed-1")
    assert result.source_failure is None
    assert result.manifest is not None and result.member is not None
    assert result.validation.value is None
    assert result.validation.rejection.field == "raw_filled_quantity"


def test_actual_member_with_mismatched_provider_profile_fails_proof():
    """A catalog-admitted member cannot claim another modeled source."""
    from options_lab.order_inputs import admit_order_update
    result = admit_order_update(admitted_order_fixture(), "profile-mismatch-1")
    assert result.source_failure == ("profile", "profile_mismatch")
    assert result.manifest is None and result.member is None and result.validation is None


def test_failed_source_proof_retains_attempt_for_recheck():
    """A forged retained fixture cannot become an admitted order claim."""
    from options_lab.admission import VerifiedFixtureManifest
    from options_lab.order_inputs import admit_order_update, OrderInputAdmission
    original = admitted_order_fixture()
    forged = object.__new__(VerifiedFixtureManifest)
    for name, value in vars(original).items():
        object.__setattr__(forged, name, value)
    object.__setattr__(forged, "payload_bytes", original.payload_bytes + b" ")
    result = admit_order_update(forged, "fill-1")
    assert result.supplied_fixture is forged and result.record_id == "fill-1"
    assert result.source_failure is not None
    assert result.manifest is None and result.member is None and result.validation is None
    missing = admit_order_update(original, "missing-order")
    assert missing.source_failure is not None and missing.record_id == "missing-order"
    with pytest.raises(TypeError):
        OrderInputAdmission()


def test_forged_retained_authority_flag_cannot_pass_fresh_proof():
    """A changed authority field must fail even when actual bytes still match."""
    from options_lab.admission import VerifiedFixtureManifest
    from options_lab.order_inputs import admit_order_update
    original = admitted_order_fixture()
    forged = object.__new__(VerifiedFixtureManifest)
    for name, value in vars(original).items():
        object.__setattr__(forged, name, value)
    object.__setattr__(forged, "operational_allowed", True)
    result = admit_order_update(forged, "fill-1")
    assert result.source_failure is not None
    assert result.manifest is None
