"""Account facts are retained claims, never source admission or authorization."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, localcontext
from zoneinfo import ZoneInfo

import pytest

import options_lab.account as account_owner
from options_lab.config import StrategyConfig
from options_lab.contracts import ContractId
from options_lab.observations import ObservationMeta
from options_lab.quotes import QuoteObservation
from options_lab.sessions import ExchangeSession

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
DAY = date(2026, 9, 4)
D = Decimal


def contract(**changes):
    values = dict(underlying="SPY", expiry=date(2026, 9, 18), right="call",
                  strike=D('650'), multiplier=100, deliverable_id="opaque-deliverable")
    return ContractId(**(values | changes))


def mark(**changes):
    meta = ObservationMeta("fixture", "quote-1", "fixture://quote", "realtime",
                           "genuine", "quote", NOW, NOW, NOW, "measured", "fixture://clock")
    values = dict(contract=contract(), meta=meta, bid=D('4.5'), ask=None,
                  bid_at=NOW, ask_at=None, bid_size=1, ask_size=None)
    return QuoteObservation(**(values | changes))


def holding(**changes):
    values = dict(position_id="position-1", instrument_ref="instrument-1", contract=contract(),
                  asset_kind="option", quantity_unit="contracts", quantity=D('1'),
                  basis_debit=D('500'), entry_filled_at=NOW-timedelta(minutes=2),
                  entry_client_order_id="entry-1", mark_quote=mark(),
                  estimated_remaining_close_cost=D('1'), source="fixture",
                  provider_record_id="holding-record-1", raw_ref="fixture://holding")
    return account_owner.HoldingFact(**(values | changes))


def order(**changes):
    values = dict(order_ref="order-1", client_order_id="entry-1", instrument_ref="instrument-1",
                  contract=contract(), side="buy", role="entry", status="open",
                  remaining_quantity=D('1'), cumulative_filled_quantity=D('0'),
                  reserved_cash=D('521'), execution_uncertain=False, source="fixture",
                  provider_record_id="order-record-1", raw_ref="fixture://order")
    return account_owner.OpenOrderFact(**(values | changes))


def account(**changes):
    values = dict(account_id="account-1", event_id="source-event-1", currency="USD",
                  source="fixture", provider_record_id="account-record-1", raw_ref="fixture://account",
                  received_at=NOW, available_at=NOW, availability_basis="measured",
                  as_of=NOW, reconciled_at=NOW, session_date=DAY, ledger_revision="ledger-1",
                  reconciled_ledger_revision="ledger-1", reconciliation_id="reconciliation-1",
                  risk_state_revision=None, halt_checkpoint_ref=None, connection="connected",
                  holdings_completeness="complete", orders_completeness="complete",
                  virtual_cash=None, virtual_equity=None, session_start_equity=None,
                  high_water_mark=None, session_realized_pnl=None, reported_marked_pnl=None,
                  virtual_settled_cash=None, virtual_reserved_cash=None, broker_settled_cash=None,
                  broker_available_cash=None, broker_nonmargin_buying_power=None,
                  applicable_round_trip_fees=None, holdings=(), open_orders=())
    return account_owner.AccountSnapshot(**(values | changes))


def calendar(**changes):
    values = dict(calendar="XNYS", session_date=DAY, kind="regular", opens_at=None,
                  closes_at=None, source="fixture", provider_record_id="calendar-1",
                  source_version="v1", available_at=NOW, availability_basis="measured",
                  received_at=NOW, raw_ref="fixture://calendar", fidelity="synthetic")
    return ExchangeSession(**(values | changes))


def assess(snapshot, **changes):
    values = dict(session=calendar(), config=StrategyConfig(), now=NOW)
    return account_owner.assess_account(snapshot, **(values | changes))


def test_complete_empty_claims_derive_flat_without_inventing_capital_or_halt_authority():
    snapshot = account()
    result = assess(snapshot, config=StrategyConfig(initial_virtual_equity=D('150000')))
    assert result.observed_flat
    assert result.account is snapshot
    assert snapshot.event_id == 'source-event-1' != snapshot.provider_record_id
    assert snapshot.virtual_equity is None and snapshot.virtual_cash is None
    assert snapshot.risk_state_revision is None and snapshot.halt_checkpoint_ref is None
    assert result.strategy_date == DAY
    assert result.integrity_reasons == result.time_reasons == result.session_reasons == ()
    assert result.reconciliation_reasons == result.exposure_reasons == ()
    assert result.account_age == result.reconciliation_age == timedelta(0)
    assert result.ledger_revision_matches
    assert not result.has_nonzero_holding and not result.has_unknown_holding_quantity
    assert not result.has_open_order_records and not result.execution_uncertain


def test_missing_account_is_unknown_even_with_no_observed_rows():
    result = assess(None)
    assert not result.observed_flat
    assert result.account is None
    assert result.integrity_reasons == ('account_missing',)
    assert result.exposure_reasons == ('account_state_unknown',)
    assert not result.ledger_revision_matches
    assert result.account_age is None and result.reconciliation_age is None


@pytest.mark.parametrize('field,value,reason', [
    ('holdings_completeness', 'unknown', 'holdings_unknown'),
    ('holdings_completeness', 'incomplete', 'holdings_incomplete'),
    ('orders_completeness', 'unknown', 'orders_unknown'),
    ('orders_completeness', 'incomplete', 'orders_incomplete'),
    ('connection', 'unknown', 'connection_unknown'),
    ('connection', 'disconnected', 'connection_disconnected'),
    ('ledger_revision', None, 'ledger_revision_missing'),
    ('reconciled_ledger_revision', None, 'reconciled_ledger_revision_missing'),
    ('reconciliation_id', None, 'reconciliation_id_missing'),
    ('reconciled_ledger_revision', 'ledger-2', 'ledger_revision_mismatch'),
])
def test_unreconciled_empty_claims_cannot_establish_flatness(field, value, reason):
    result = assess(account(**{field: value}))
    assert result.reconciliation_reasons == (reason,)
    assert not result.observed_flat


def test_two_missing_revisions_do_not_match():
    result = assess(account(ledger_revision=None, reconciled_ledger_revision=None))
    assert not result.ledger_revision_matches
    assert result.reconciliation_reasons == ('ledger_revision_missing', 'reconciled_ledger_revision_missing')


@pytest.mark.parametrize('field,age,reason', [
    ('as_of', timedelta(seconds=5), ()),
    ('as_of', timedelta(seconds=5, microseconds=1), ('account_too_old',)),
    ('reconciled_at', timedelta(seconds=5), ()),
    ('reconciled_at', timedelta(seconds=5, microseconds=1), ('reconciliation_too_old',)),
])
def test_source_and_reconciliation_age_boundaries_are_independent(field, age, reason):
    result = assess(account(**{field: NOW-age}))
    assert result.time_reasons == reason
    assert result.observed_flat == (not reason)
    assert getattr(result, 'account_age' if field == 'as_of' else 'reconciliation_age') == age


@pytest.mark.parametrize('field,limit,reason', [
    ('as_of', 'max_account_age', 'account_too_old'),
    ('reconciled_at', 'max_reconciliation_age', 'reconciliation_too_old'),
])
def test_each_tighter_age_limit_changes_only_its_clock(field, limit, reason):
    snapshot = account(**{field: NOW-timedelta(seconds=2)})
    assert assess(snapshot).observed_flat
    result = assess(snapshot, config=StrategyConfig(**{limit: timedelta(seconds=1)}))
    assert result.time_reasons == (reason,)
    other = 'max_reconciliation_age' if limit == 'max_account_age' else 'max_account_age'
    assert assess(snapshot, config=StrategyConfig(**{other: timedelta(seconds=1)})).observed_flat


@pytest.mark.parametrize('changes,reasons', [
    ({'as_of': None}, ('account_as_of_missing',)),
    ({'available_at': None}, ('account_availability_unknown',)),
    ({'availability_basis': 'assumed'}, ('account_availability_not_measured',)),
    ({'available_at': NOW+timedelta(seconds=1)}, ('account_available_after_now',)),
    ({'as_of': NOW+timedelta(seconds=1)}, ('account_as_of_after_available', 'account_as_of_after_now')),
    ({'reconciled_at': None}, ('reconciliation_time_missing',)),
    ({'reconciled_at': NOW+timedelta(seconds=1)}, ('reconciliation_after_available', 'reconciliation_after_now')),
])
def test_missing_or_future_clocks_stay_retained_with_literal_reasons(changes, reasons):
    snapshot = account(**changes)
    result = assess(snapshot)
    assert result.time_reasons == reasons
    assert not result.observed_flat and result.account is snapshot


def test_reconciliation_can_follow_observation_but_receipt_cannot_refresh_it():
    snapshot = account(as_of=NOW-timedelta(seconds=4), reconciled_at=NOW-timedelta(seconds=2))
    assert assess(snapshot).observed_flat
    stale = replace(snapshot, as_of=NOW, received_at=NOW+timedelta(days=1),
                    reconciled_at=NOW-timedelta(seconds=6))
    assert assess(stale).time_reasons == ('reconciliation_too_old',)
    assert assess(snapshot, now=NOW+timedelta(seconds=2)).time_reasons == ('account_too_old',)
    assert snapshot.as_of == NOW-timedelta(seconds=4)


@pytest.mark.parametrize('kind', ['regular', 'early_close', 'closed'])
def test_known_accounting_day_needs_neither_entry_hours_nor_grid(kind):
    at = NOW.replace(hour=23, second=2)
    assert assess(account(as_of=at, reconciled_at=at, available_at=at), now=at,
                  session=calendar(kind=kind)).observed_flat


@pytest.mark.parametrize('changes,reasons', [
    ({'calendar': 'OTHER'}, ('calendar_unsupported',)),
    ({'session_date': date(2026, 9, 3)}, ('session_wrong_date',)),
    ({'kind': 'unknown'}, ('session_unknown',)),
    ({'available_at': None}, ('session_availability_unknown',)),
    ({'available_at': NOW+timedelta(seconds=1)}, ('session_available_after_now',)),
    ({'availability_basis': 'assumed'}, ('session_availability_not_measured',)),
])
def test_accounting_calendar_requires_current_known_claims(changes, reasons):
    result = assess(account(), session=calendar(**changes))
    assert result.session_reasons == reasons
    assert not result.observed_flat


def test_account_date_uses_new_york_and_conversion_failure_is_bounded():
    at = datetime(2026, 9, 5, 1, tzinfo=UTC)
    snapshot = account(as_of=at, reconciled_at=at, available_at=at)
    assert assess(snapshot, now=at).observed_flat
    assert assess(replace(snapshot, session_date=date(2026, 9, 5)), now=at).session_reasons == ('account_wrong_date',)
    assert assess(snapshot, session=None).session_reasons == ('session_missing',)
    extreme = assess(snapshot, now=datetime.min.replace(tzinfo=UTC))
    assert extreme.strategy_date is None and not extreme.observed_flat
    assert 'time_arithmetic_unsupported' in extreme.time_reasons


@pytest.mark.parametrize('quantity,reason,nonzero,unknown', [
    (None, 'holding_quantity_unknown', False, True),
    (D('0'), 'holding_zero_quantity', False, False),
    (D('-1'), 'holding_short', True, False),
    (D('0.5'), 'holding_fractional_quantity', True, False),
    (D('2'), 'holding_quantity_not_one', True, False),
])
def test_nonconforming_observed_quantities_survive_for_recovery(quantity, reason, nonzero, unknown):
    fact = holding(quantity=quantity)
    result = assess(account(holdings=(fact,)))
    assert result.account.holdings == (fact,) and not result.observed_flat
    assert reason in result.exposure_reasons
    assert result.has_nonzero_holding == nonzero
    assert result.has_unknown_holding_quantity == unknown


def test_exercise_unknown_identity_old_time_and_mark_are_never_replaced():
    old = NOW-timedelta(days=1)
    facts = (holding(contract=None, asset_kind='equity', quantity_unit='shares', quantity=D('100'),
                     entry_filled_at=None, mark_quote=None),
             holding(position_id='p2', provider_record_id='h2', entry_filled_at=old,
                     mark_quote=mark(bid_at=old), contract=contract(deliverable_id='adjusted')))
    result = assess(account(holdings=facts))
    assert result.account.holdings is facts
    for reason in ('multiple_holdings', 'holding_asset_unsupported', 'holding_unit_unsupported',
                   'holding_contract_unknown', 'holding_entry_time_unknown', 'holding_prior_session'):
        assert reason in result.exposure_reasons
    assert result.integrity_reasons == ('holding_mark_contract_mismatch',)
    assert not result.observed_flat


@pytest.mark.parametrize('status,uncertain', [('open', False), ('cancel_pending', True),
                                             ('replace_pending', True), ('terminal', False), ('unknown', True)])
def test_order_rows_block_flat_even_terminal_or_cancel_requested(status, uncertain):
    fact = order(status=status)
    result = assess(account(open_orders=(fact,)))
    assert result.has_open_order_records and not result.observed_flat
    assert result.execution_uncertain == uncertain
    assert result.account.open_orders == (fact,)
    assert 'open_order_records' in result.exposure_reasons


def test_partial_fill_and_contradictory_order_roles_are_retained_not_reduced():
    entry = order(cumulative_filled_quantity=D('0.5'), remaining_quantity=D('-1'), execution_uncertain=True)
    exit_order = order(order_ref='o2', provider_record_id='o2', role='exit', side='buy')
    result = assess(account(holdings=(holding(),), open_orders=(entry, exit_order)))
    assert result.account.open_orders == (entry, exit_order)
    assert result.execution_uncertain and not result.observed_flat
    assert 'order_negative_quantity' in result.integrity_reasons
    assert 'order_side_role_mismatch' in result.integrity_reasons
    assert 'order_fractional_quantity' in result.exposure_reasons


@pytest.mark.parametrize('kind', ['holding', 'order'])
def test_duplicate_conflicts_do_not_use_last_write_or_discard_evidence(kind):
    first = holding() if kind == 'holding' else order()
    changed = replace(first, quantity=D('2')) if kind == 'holding' else replace(first, reserved_cash=D('10'))
    field = 'holdings' if kind == 'holding' else 'open_orders'
    identical = assess(account(**{field: (first, first)}))
    assert not identical.integrity_reasons
    assert getattr(identical.account, field) == (first, first)
    forward = assess(account(**{field: (first, changed)}))
    reverse = assess(account(**{field: (changed, first)}))
    assert forward.integrity_reasons == reverse.integrity_reasons
    assert f'{kind}_identity_conflict' in forward.integrity_reasons
    assert f'{kind}_source_identity_conflict' in forward.integrity_reasons
    assert not forward.observed_flat


def test_source_identity_reuse_and_mark_mutation_are_conflicts():
    first = holding()
    other = replace(first, position_id='p2', mark_quote=replace(first.mark_quote, bid=D('3')))
    result = assess(account(holdings=(first, other)))
    assert result.integrity_reasons == ('holding_source_identity_conflict',)
    unknown = assess(account(currency='unknown'))
    assert unknown.integrity_reasons == ('currency_unsupported',) and not unknown.observed_flat


@pytest.mark.parametrize('factory,field', [(holding, 'basis_debit'), (holding, 'estimated_remaining_close_cost'),
    (order, 'reserved_cash'), (account, 'virtual_reserved_cash'), (account, 'applicable_round_trip_fees')])
def test_negative_cost_claims_are_retained_with_integrity_evidence(factory, field):
    fact = factory(**{field: D('-1')})
    snapshot = account(holdings=(fact,)) if factory is holding else account(open_orders=(fact,)) if factory is order else fact
    result = assess(snapshot)
    assert getattr(fact, field) == D('-1')
    assert f'{field}_negative' in result.integrity_reasons


@pytest.mark.parametrize('field', ['virtual_cash', 'virtual_equity', 'session_start_equity', 'high_water_mark',
    'session_realized_pnl', 'reported_marked_pnl', 'virtual_settled_cash', 'broker_settled_cash',
    'broker_available_cash', 'broker_nonmargin_buying_power'])
def test_negative_ledger_amounts_are_facts_not_automatic_integrity_failures(field):
    snapshot = account(**{field: D('-100')})
    assert getattr(assess(snapshot).account, field) == D('-100')
    assert not assess(snapshot).integrity_reasons


@pytest.mark.parametrize('factory,field', [(holding, 'quantity'), (holding, 'basis_debit'),
    (holding, 'estimated_remaining_close_cost'), (order, 'remaining_quantity'),
    (order, 'cumulative_filled_quantity'), (order, 'reserved_cash'), (account, 'virtual_cash'),
    (account, 'virtual_equity'), (account, 'session_start_equity'), (account, 'high_water_mark'),
    (account, 'session_realized_pnl'), (account, 'reported_marked_pnl'), (account, 'virtual_settled_cash'),
    (account, 'virtual_reserved_cash'), (account, 'broker_settled_cash'), (account, 'broker_available_cash'),
    (account, 'broker_nonmargin_buying_power'), (account, 'applicable_round_trip_fees')])
@pytest.mark.parametrize('bad', [True, 1, 0.5, '1', D('NaN'), D('sNaN'), D('Infinity')])
def test_money_and_quantities_require_exact_finite_decimals(factory, field, bad):
    with pytest.raises((TypeError, ValueError)):
        factory(**{field: bad})


@pytest.mark.parametrize('factory,changes', [(holding, {'contract': object()}), (holding, {'mark_quote': object()}),
    (holding, {'asset_kind': 'future'}), (holding, {'quantity_unit': 'lots'}), (holding, {'position_id': ''}),
    (order, {'execution_uncertain': 1}), (order, {'side': 'short'}), (order, {'role': 'close'}),
    (order, {'status': 'filled'}), (account, {'session_date': NOW}), (account, {'holdings': []}),
    (account, {'holdings': (object(),)}), (account, {'open_orders': []}), (account, {'open_orders': (object(),)}),
    (account, {'event_id': None}), (account, {'ledger_revision': ''}), (account, {'connection': 'ready'}),
    (account, {'availability_basis': 'known'}), (account, {'as_of': NOW.replace(tzinfo=None)})])
def test_nested_and_scalar_boundaries_reject_malformed_representation(factory, changes):
    with pytest.raises((TypeError, ValueError)):
        factory(**changes)


def test_utc_frozen_records_and_derived_fields_cannot_be_supplied():
    ny = NOW.astimezone(ZoneInfo('America/New_York'))
    snapshot = account(received_at=ny, as_of=ny, available_at=ny, reconciled_at=ny)
    fact = holding(entry_filled_at=ny)
    result = assess(snapshot, now=ny)
    assert result.now == snapshot.as_of == NOW and snapshot.as_of.tzinfo is UTC
    assert fact.entry_filled_at.tzinfo is UTC
    for value, name in ((snapshot, 'currency'), (fact, 'quantity'), (order(), 'status'), (result, 'observed_flat')):
        with pytest.raises(FrozenInstanceError):
            setattr(value, name, None)
    with pytest.raises(TypeError):
        account_owner.AccountAssessment(snapshot, calendar(), StrategyConfig(), NOW, observed_flat=True)
    for changes in ({'session': object()}, {'config': object()}, {'now': NOW.replace(tzinfo=None)}):
        with pytest.raises((TypeError, ValueError)):
            assess(snapshot, **changes)


def test_finite_extreme_quantity_does_not_allocate_or_round_to_one():
    with localcontext() as ctx:
        ctx.prec = 1
        ctx.traps[Inexact] = True
        for q in (D('1E+1000000'), D('1E-1000000'), D('0E-1000000')):
            result = assess(account(holdings=(holding(quantity=q),)))
            assert result.account.holdings[0].quantity is q
            assert not result.observed_flat
