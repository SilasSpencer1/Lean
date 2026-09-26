"""Exercise actual registered broker-query source admission."""

from datetime import datetime, timezone
from pathlib import Path

import pytest


AT = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
FIXTURE_ID = "p21-query-v1"


def admitted(record_id: str):
    """Reprove the registered fixture and admit one query member."""
    from options_lab.admission import verify_fixture_bundle
    from options_lab.query_inputs import admit_broker_query

    path = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    checked = verify_fixture_bundle(FIXTURE_ID, path.read_bytes(),
        event_id="query-read", raw_ref="synthetic://p21/read", received_at=AT)
    assert checked.value is not None, checked.rejection
    return admit_broker_query(checked.value, record_id)


def test_complete_atomic_capture_retains_actual_account_source():
    """A registered complete empty capture carries account and query proof."""
    result = admitted("fresh-flat")
    assert result.source_failure is None
    assert result.manifest.fixture_id == FIXTURE_ID
    assert result.member.record_id == "fresh-flat"
    assert result.query.requested_at == result.query.captured_at == AT
    assert result.query.responded_at > result.query.captured_at
    assert result.account.as_of == AT
    assert result.account.holdings == result.account.open_orders == ()
    assert result.query.holdings_coverage == result.query.orders_coverage == "complete"
    assert result.included_orders == ()


def test_partial_noncompleted_and_unresolved_are_admitted_source_claims():
    """Admission preserves adverse claims for P21B coverage decisions."""
    partial = admitted("partial-holdings")
    assert partial.source_failure is None
    assert partial.query.holdings_coverage == "partial"
    assert partial.account.holdings_completeness == "incomplete"
    noncompleted = admitted("noncompleted")
    assert noncompleted.source_failure is None
    assert noncompleted.query.completed is False
    unresolved = admitted("unresolved-send")
    assert unresolved.source_failure is None
    assert unresolved.query.issued_actions[0][0:4] == (
        "send-1", "submit", AT, "unresolved")


def test_included_order_and_account_alias_have_actual_source_proof():
    """Typed mapping needs both an account row and a re-admitted order member."""
    result = admitted("included-entry")
    assert result.source_failure is None
    assert result.account.holdings == ()  # P21B assesses aggregate economics.
    assert result.account.open_orders[0].order_ref == "order-1"
    assert result.query.order_ref_aliases == (("order-1", "broker", "broker-1", "client-1"),)
    assert len(result.included_orders) == 1
    assert result.included_orders[0].member.record_id == "entry-fill"
    assert result.included_orders[0].validation.value.broker_order_id == "broker-1"


@pytest.mark.parametrize("record", [
    "late-included", "wrong-kind-ref", "missing-ref", "bad-alias",
    "missing-account", "wrong-account", "malformed-clock", "duplicate-action",
    "action-after-capture", "duplicate-include", "duplicate-alias",
    "bad-account-contract", "missing-field", "wrong-source",
    "malformed-account", "malformed-order",
])
def test_bad_source_reference_or_clock_has_no_partial_positive_proof(record):
    """A damaged nested source cannot retain a favorable partial admission."""
    result = admitted(record)
    assert record in {row.record_id for row in result.supplied_fixture.members}
    assert result.source_failure is not None
    assert result.manifest is result.member is result.query is result.account is None
    assert result.included_orders == ()
    assert result.supplied_fixture.fixture_id == FIXTURE_ID
    assert result.record_id == record


def test_response_after_fill_does_not_change_atomic_capture():
    """An excluded post-capture report does not become an included member."""
    result = admitted("late-excluded")
    assert result.source_failure is None
    assert result.query.requested_at == result.query.captured_at == AT
    assert result.query.responded_at > datetime(2026, 9, 4, 14, 5, 1,
                                                tzinfo=timezone.utc)
    assert result.included_orders == ()


def test_caller_cannot_author_positive_query_or_admission():
    """Only actual member admission may construct these derived records."""
    from options_lab.query_inputs import BrokerQuery, BrokerQueryAdmission
    with pytest.raises(TypeError):
        BrokerQuery()
    with pytest.raises(TypeError):
        BrokerQueryAdmission()


def test_trusted_top_level_errors_and_tampered_fixture_are_distinct():
    """Trusted argument mistakes raise; nested source damage is a result."""
    from options_lab.query_inputs import admit_broker_query
    with pytest.raises(TypeError):
        admit_broker_query(object(), "fresh-flat")
    with pytest.raises(ValueError):
        admit_broker_query(admitted("fresh-flat").supplied_fixture, "")
    fixture = admitted("fresh-flat").supplied_fixture
    object.__setattr__(fixture, "payload_bytes", fixture.payload_bytes + b"x")
    result = admit_broker_query(fixture, "fresh-flat")
    assert result.source_failure is not None
    assert result.manifest is result.member is result.query is result.account is None
    assert result.included_orders == ()
