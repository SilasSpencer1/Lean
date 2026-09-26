"""Exercise source-reproved order lifecycle reduction and uncertainty."""

from datetime import datetime, timezone
from pathlib import Path

from options_lab.admission import verify_fixture_bundle
from options_lab.order_inputs import admit_order_update
from decimal import Decimal
import pytest


AT = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)


def admitted(record_id: str):
    """Return one actual registered order member admission."""
    path = Path(__file__).parent / "fixtures/p19-order-update-v1.json"
    fixture = verify_fixture_bundle("p19-order-update-v1", path.read_bytes(),
                                    event_id="fixture-read", raw_ref="synthetic://p19/read",
                                    received_at=AT).value
    assert fixture is not None
    return admit_order_update(fixture, record_id)


def test_first_seen_fill_before_ack_retains_exposure_without_fabricating_zero():
    """An actual first q1 is exposure and completion evidence, not proved prior q0."""
    from options_lab.lifecycle import reduce_order, start_lifecycle

    start = start_lifecycle("acct-1", "alpaca")
    assert start.possible_exposure and start.reconciliation_required
    result = reduce_order(start, admitted("fill-1"))
    assert result.state.phase == "OPEN"
    assert result.state.local_long_quantity == 1
    assert result.state.possible_exposure and result.state.reconciliation_required
    assert len(result.newly_completed_entries) == 1
    assert result.newly_completed_entries[0].prior_zero_proof is None
    duplicate = reduce_order(result.state, admitted("fill-1"))
    assert duplicate.newly_completed_entries == ()
    assert duplicate.state.local_long_quantity == 1
    assert duplicate.state.lifecycle_revision == result.state.lifecycle_revision


def test_malformed_actual_member_and_failed_proof_preserve_uncertainty():
    """No malformed or failed source may be interpreted as zero/flat proof."""
    from options_lab.lifecycle import reduce_order, start_lifecycle

    state = start_lifecycle("acct-1", "alpaca")
    for record in ("malformed-1", "missing-order"):
        result = reduce_order(state, admitted(record))
        assert result.state.possible_exposure and result.state.reconciliation_required
        assert result.newly_completed_entries == ()
        assert result.state.local_long_quantity == 0
        assert result.reasons
        assert not result.applied_event


def replay(*records: str):
    """Reduce distinct registered source reports in supplied delivery order."""
    from options_lab.lifecycle import reduce_order, start_lifecycle

    state = start_lifecycle("acct-1", "alpaca")
    results = []
    for record in records:
        result = reduce_order(state, admitted(record))
        results.append(result)
        state = result.state
    return results


def test_known_zero_to_one_uses_source_order_and_no_duplicate_completion():
    """An older actual q0 upgrades count evidence; duplicate q1 emits nothing."""
    first, filled, duplicate = replay("entry-zero", "fill-1", "fill-1")
    assert first.state.phase == "ENTRY_PENDING"
    assert filled.state.local_long_quantity == 1
    assert len(filled.newly_completed_entries) == 1
    assert filled.newly_completed_entries[0].prior_zero_proof[1] == "entry-zero"
    assert duplicate.newly_completed_entries == ()


def test_same_provider_event_id_conflict_retains_larger_quantity_and_halts():
    """Changed content under one provider ID cannot overwrite or hide a fill."""
    first, conflict = replay("fill-1", "same-id-conflict")
    assert first.state.local_long_quantity == 1
    assert conflict.state.local_long_quantity == 2
    assert "event_identity_conflict" in conflict.state.halt_reasons
    assert conflict.state.possible_exposure
    assert conflict.state.completed_entries == first.state.completed_entries
    assert conflict.newly_completed_entries == ()


def test_cancel_request_and_terminal_cancel_do_not_erase_late_entry_fill():
    """Actual late q1 remains exposure after cancellation reports."""
    requested, canceled, filled = replay("cancel-request", "entry-canceled", "late-fill")
    assert requested.state.local_long_quantity == 0
    assert canceled.state.local_long_quantity == 0
    assert filled.state.local_long_quantity == 1
    assert filled.state.phase == "OPEN"
    assert len(filled.newly_completed_entries) == 1


def test_old_order_fill_does_not_complete_new_pending_order():
    """An old order's late fill retains its own identity while another waits."""
    pending, old_fill = replay("new-entry-pending", "fill-1")
    assert pending.state.phase == "ENTRY_PENDING"
    assert old_fill.state.local_long_quantity == 1
    assert old_fill.newly_completed_entries[0].order_aliases == (
        ("broker", "broker-1"), ("client", "client-1"))
    assert any(o.pending and ("broker", "broker-3") in o.aliases
               for o in old_fill.state.orders)


def test_exit_cancel_preserves_long_and_late_exit_fill_only_reduces_once():
    """A canceled exit cannot imply a sale; actual fill can locally reduce one."""
    entry, submitted, canceled, fill, duplicate = replay(
        "fill-1", "exit-pending", "exit-canceled", "exit-fill", "exit-fill")
    assert submitted.state.phase == "EXIT_PENDING"
    assert canceled.state.local_long_quantity == 1
    assert fill.state.local_long_quantity == 0
    assert fill.state.possible_exposure
    assert duplicate.state.local_long_quantity == 0
    assert duplicate.state.lifecycle_revision == fill.state.lifecycle_revision


def test_replacement_two_positive_cumulatives_keeps_unknown_total_and_raw_facts():
    """Parent q1 and successor q1 cannot be summed or maxed as exact total."""
    parent, child, duplicate = replay("replacement-parent", "replacement-child",
                                      "replacement-child")
    assert parent.state.local_long_quantity == 1
    assert child.state.local_long_quantity is None
    assert child.state.possible_exposure
    assert "replacement_quantity_ambiguous" in child.state.halt_reasons
    assert len(child.state.completed_entries) == 1
    assert duplicate.newly_completed_entries == ()
    assert len(child.state.seen_events) == 2


def test_reordered_replacement_keeps_same_semantic_state():
    """Arrival order cannot choose a different chain quantity or semantic identity."""
    forward = replay("replacement-parent", "replacement-child")[-1].state
    reverse = replay("replacement-child", "replacement-parent")[-1].state
    assert forward.local_long_quantity is reverse.local_long_quantity is None
    assert forward.completed_entries == reverse.completed_entries
    assert forward.lifecycle_revision == reverse.lifecycle_revision


def test_regression_and_old_sequence_higher_quantity_retain_adverse_maximum():
    """Arrival and provider sequence never discard a larger observed fill."""
    first, regression = replay("fill-1", "quantity-regression")
    assert regression.state.local_long_quantity == 1
    assert "quantity_regression" in regression.state.halt_reasons
    _, old_higher = replay("sequence-low", "old-sequence-higher")
    assert old_higher.state.local_long_quantity == 2
    assert "old_sequence_higher_quantity" in old_higher.state.halt_reasons


def test_fractional_unknown_and_q2_never_become_a_one_contract_completion():
    """Unsupported quantity facts remain uncertainty and raw observations."""
    for record in ("fractional-fill", "unknown-fill", "two-fill"):
        result = replay(record)[0]
        assert result.newly_completed_entries == ()
        assert result.state.possible_exposure
        assert result.state.local_long_quantity != 1
        assert result.state.seen_events[0].value.safe_cumulative_quantity != 1


def test_late_money_updates_independent_watermarks_without_quantity_replay():
    """A new fee can arrive after terminal q1 with no second fill/count."""
    first, money = replay("fill-1", "late-money")
    assert first.state.orders[0].cumulative_fees_usd is None
    assert money.state.orders[0].cumulative_quantity == 1
    assert money.state.orders[0].cumulative_fill_notional_usd == Decimal("510")
    assert money.state.orders[0].cumulative_fees_usd == Decimal("1.25")
    assert money.newly_completed_entries == ()


def test_unlinked_actual_fill_is_quarantined_and_identical_actual_proof_upgrades():
    """No order ID means no linked fill; a direct claim applies only after proof."""
    from options_lab.lifecycle import reduce_order, start_lifecycle

    unlinked = replay("unlinked-fill")[0]
    assert unlinked.state.local_long_quantity is None
    assert unlinked.newly_completed_entries == ()
    admitted_fill = admitted("fill-1")
    direct = reduce_order(start_lifecycle("acct-1", "alpaca"),
                          admitted_fill.validation.value)
    assert direct.newly_completed_entries == ()
    assert direct.state.local_long_quantity == 0
    upgraded = reduce_order(direct.state, admitted_fill)
    assert upgraded.proof_upgraded
    assert upgraded.state.local_long_quantity == 1
    assert len(upgraded.newly_completed_entries) == 1


def test_late_directional_link_unions_completion_aliases_without_new_emission():
    """A late actual link joins prior identity evidence without replaying a fill."""
    parent, child, linked = replay("late-link-parent", "late-link-child",
                                   "late-link-report")
    assert len(child.state.completed_entries) == 2
    assert len(linked.state.completed_entries) == 1
    assert linked.newly_completed_entries == ()
    assert linked.state.local_long_quantity is None
    assert len([e for e in linked.state.seen_events if e.proved]) == 3
    representative = linked.state.completed_entries[0]
    assert representative.first_q1_proof[1] in ("late-link-parent", "late-link-child")
    assert representative.first_q1_event_at == next(e.value.event_at for e in linked.state.seen_events
        if e.source_record[1] == representative.first_q1_proof[1])
    assert representative.first_q1_available_at == next(e.value.available_at
        for e in linked.state.seen_events
        if e.source_record[1] == representative.first_q1_proof[1])


def test_negative_exit_balance_is_unknown_and_never_apparent_flat():
    """An actual sell without known local buy is possible short exposure."""
    result = replay("exit-fill")[0]
    assert result.state.local_long_quantity is None
    assert result.state.phase != "FLAT"
    assert "negative_local_balance" in result.state.halt_reasons


def test_state_and_retained_source_tamper_are_denied_with_reached_input():
    """Replay must detect forged prior state and damaged retained source bytes."""
    from options_lab.lifecycle import LifecycleAdvanceError, reduce_order, start_lifecycle

    first = reduce_order(start_lifecycle("acct-1", "alpaca"), admitted("fill-1"))
    bad_state = first.state
    object.__setattr__(bad_state, "local_long_quantity", 0)
    attempt = admitted("fill-1")
    with pytest.raises(LifecycleAdvanceError, match="state_replay_mismatch") as error:
        reduce_order(bad_state, attempt)
    assert error.value.reached_update is attempt
    fresh = reduce_order(start_lifecycle("acct-1", "alpaca"), admitted("fill-1"))
    fixture = fresh.state.transcript[0].update.supplied_fixture
    object.__setattr__(fixture, "payload_bytes", fixture.payload_bytes + b" ")
    with pytest.raises(LifecycleAdvanceError, match="state_replay_mismatch"):
        reduce_order(fresh.state, admitted("fill-1"))


def test_transcript_bound_denies_before_replaying_nested_source():
    """A swollen transcript cannot drive unbounded fixture re-admission."""
    from options_lab.lifecycle import LifecycleAdvanceError, reduce_order, start_lifecycle

    state = reduce_order(start_lifecycle("acct-1", "alpaca"), admitted("fill-1")).state
    object.__setattr__(state, "transcript", state.transcript * 257)
    with pytest.raises(LifecycleAdvanceError, match="history_limit"):
        reduce_order(state, admitted("fill-1"))


def test_source_older_zero_arriving_after_q1_enriches_proof_without_recount():
    """Late source-ordered q0 establishes 0->1 evidence without another completion."""
    filled, old_zero = replay("fill-1", "entry-zero")
    assert filled.state.completed_entries[0].prior_zero_proof is None
    assert old_zero.state.completed_entries[0].prior_zero_proof[1] == "entry-zero"
    assert old_zero.newly_completed_entries == ()
    assert old_zero.state.local_long_quantity == 1


def test_no_provider_id_semantic_redelivery_ignores_distinct_receipts():
    """A second actual member with the same report content is not a new fill."""
    first, duplicate = replay("no-provider-fill-a", "no-provider-fill-b")
    assert first.state.local_long_quantity == 1
    assert duplicate.state.lifecycle_revision == first.state.lifecycle_revision
    assert duplicate.newly_completed_entries == ()


def test_money_regression_retains_largest_known_fee_and_requires_reconcile():
    """A later lower cumulative fee cannot erase an earlier actual cost."""
    _, larger, lower = replay("fill-1", "late-money", "fee-regression")
    assert larger.state.orders[0].cumulative_fees_usd == Decimal("1.25")
    assert lower.state.orders[0].cumulative_fees_usd == Decimal("1.25")
    assert "money_conflict" in lower.state.halt_reasons
    assert lower.newly_completed_entries == ()


def test_shared_client_with_two_broker_ids_is_uncertain_without_same_order_proof():
    """A reused client ID cannot silently collapse distinct broker orders."""
    first, second = replay("fill-1", "client-reused")
    assert first.state.local_long_quantity == 1
    assert second.state.local_long_quantity is None
    assert "order_alias_conflict" in second.state.halt_reasons
    assert second.state.completed_entries == ()
    assert second.newly_completed_entries == ()


def test_unknown_role_positive_fill_and_bad_source_after_holding_remain_exposure():
    """Neither unknown side nor later source failure can erase an actual long."""
    unknown = replay("unknown-role-fill")[0]
    assert unknown.state.local_long_quantity is None
    assert unknown.state.phase != "FLAT"
    held, failed = replay("fill-1", "profile-mismatch-1")
    assert failed.state.local_long_quantity == held.state.local_long_quantity == 1
    assert failed.state.possible_exposure and failed.state.reconciliation_required


def test_higher_sequence_pending_after_terminal_requires_reconciliation():
    """A provider regression cannot reopen an apparently terminal order."""
    first, later = replay("fill-1", "terminal-regression")
    assert first.state.local_long_quantity == later.state.local_long_quantity == 1
    assert "terminal_regression" in later.state.halt_reasons
    assert later.state.possible_exposure


def test_damaged_direct_claim_is_bounded_quarantine():
    """A mutated direct normalized claim cannot drive a hook or economic fill."""
    from options_lab.lifecycle import reduce_order, start_lifecycle

    claim = admitted("fill-1").validation.value
    object.__setattr__(claim, "broker_order_id", object())
    result = reduce_order(start_lifecycle("acct-1", "alpaca"), claim)
    assert result.state.local_long_quantity == 0
    assert result.newly_completed_entries == ()
    assert "unproved_claim_damaged" in result.reasons


@pytest.mark.parametrize("zero", ("identity-zero-a", "identity-zero-null",
                                   "identity-zero-exit"))
def test_conflicting_same_order_identity_never_mints_new_completion(zero):
    """A q0 with wrong contract/role cannot label a later q1 as completed."""
    forward = replay(zero, "fill-1")
    reverse = replay("fill-1", zero)
    for final in (forward[-1], reverse[-1]):
        assert final.state.local_long_quantity is None
        assert "order_identity_conflict" in final.reasons
        assert final.state.completed_entries == ()
        assert final.newly_completed_entries == ()
        assert len(final.state.seen_events) == 2
        assert final.state.possible_exposure
    assert forward[-1].state.lifecycle_revision == reverse[-1].state.lifecycle_revision
    assert len(reverse[0].newly_completed_entries) == 1  # earlier proof is retained raw


@pytest.mark.parametrize("zero", ("clock-zero-seq-early-time-late",
                                   "clock-zero-seq-late-time-early"))
def test_contradictory_source_clocks_never_prove_prior_zero(zero):
    """Neither clock may override a contrary comparable source clock."""
    for records in ((zero, "clock-fill"), ("clock-fill", zero)):
        final = replay(*records)[-1]
        assert len(final.state.completed_entries) == 1
        assert final.state.completed_entries[0].prior_zero_proof is None
        assert "source_order_conflict" in final.reasons
        assert final.state.possible_exposure


def test_one_actual_source_clock_can_prove_older_matching_zero():
    """Missing provider sequence still permits a strictly older source time."""
    for zero in ("clock-zero-time-only", "clock-zero-seq-only"):
        final = replay(zero, "clock-fill")[-1]
        assert final.state.completed_entries[0].prior_zero_proof[1] == zero
        assert "source_order_conflict" not in final.reasons


def test_zero_between_two_q1_reports_cannot_prove_first_fill():
    """A lexical q1 representative cannot hide an earlier actual q1 report."""
    for records in (("z-early-q1", "mid-zero", "a-later-q1"),
                    ("a-later-q1", "mid-zero", "z-early-q1")):
        final = replay(*records)[-1]
        assert final.state.completed_entries[0].first_q1_proof[1] == "a-later-q1"
        assert final.state.completed_entries[0].prior_zero_proof is None
        assert final.state.possible_exposure
