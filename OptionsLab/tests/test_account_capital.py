"""Hand-calculated monetary observations and bounded recovery failures."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import (Context, Decimal, Inexact, InvalidOperation, Overflow,
                     ROUND_DOWN, Rounded, localcontext)

import pytest

import options_lab.account as api
from options_lab.config import ExecutionPolicy, StrategyConfig
from test_account import D, NOW, account, assess, contract, holding, mark, order


def funded(**changes):
    """Return explicit cash already debited for one premium and posted entry fee."""
    values = dict(virtual_cash=D('99499'), virtual_equity=D('100000'),
                  session_realized_pnl=D('-1'), virtual_settled_cash=D('99499'),
                  virtual_reserved_cash=D('0'), broker_settled_cash=D('99499'),
                  broker_available_cash=D('99499'), broker_nonmargin_buying_power=D('99499'),
                  holdings=(holding(),))
    return account(**(values | changes))


def value(fact, **changes):
    return api.assess_holding_mark(fact, **(dict(config=StrategyConfig(), now=NOW) | changes))


@pytest.mark.parametrize('bid,gross,net,unrealized,daily,equity,basis', [
    ('4.50', '450', '449', '-51', '-52', '99948', 'observed_bid'),
    ('0', '0', '-1', '-501', '-502', '99498', 'zero_bid_nonexecutable'),
    (None, '0', '-1', '-501', '-502', '99498', 'full_premium_stress'),
    ('6', '600', '599', '99', '-1', '100098', 'observed_bid'),
])
def test_paid_premium_is_not_debited_again_and_stress_is_not_realized(bid, gross, net, unrealized, daily, equity, basis):
    fact = holding(mark_quote=None if bid is None else mark(bid=D(bid)))
    snapshot = funded(holdings=(fact,))
    result = assess(snapshot)
    observed = result.holding_marks[0]
    assert observed.holding is fact and result.account is snapshot
    assert observed.valuation_basis == basis
    assert observed.gross_liquidation_value == D(gross)
    assert observed.net_liquidation_value == D(net)
    assert observed.unrealized_pnl == result.unrealized_pnl == D(unrealized)
    assert result.conservative_daily_pnl == D(daily)
    assert result.computed_liquidation_equity == D(equity)
    assert result.conservative_virtual_equity == min(D('100000'), D(equity))
    assert snapshot.session_realized_pnl == D('-1') and snapshot.virtual_cash == D('99499')
    assert not result.observed_flat


@pytest.mark.parametrize('reported,expected', [('1000000', '99948'), ('99900', '99900'), ('-1', '-1'), (None, None)])
def test_reported_balance_only_tightens_independent_equity(reported, expected):
    snapshot = funded(virtual_equity=None if reported is None else D(reported), reported_marked_pnl=D('999999'))
    result = assess(snapshot, config=StrategyConfig(initial_virtual_equity=D('150000')))
    assert result.computed_liquidation_equity == D('99948')
    assert result.conservative_virtual_equity == (None if expected is None else D(expected))
    assert result.conservative_daily_pnl == D('-52')


@pytest.mark.parametrize('side_changes', [dict(ask=None, ask_at=None, ask_size=None),
    dict(ask=D('1'), ask_at=NOW-timedelta(days=1), ask_size=0),
    dict(ask=D('0')), dict(ask=D('1E+1000000'))])
def test_entry_ask_spread_or_absent_greeks_cannot_erase_an_independent_bid(side_changes):
    fact = holding(mark_quote=mark(**side_changes))
    result = value(fact)
    assert result.valuation_basis == 'observed_bid'
    assert result.gross_liquidation_value == D('450')
    assert result.mark_reasons == ()
    assert result.observation.meta is fact.mark_quote.meta


@pytest.mark.parametrize('clock', ['event_at', 'bid_at'])
@pytest.mark.parametrize('age,expected', [(timedelta(seconds=5), '450'), (timedelta(seconds=5, microseconds=1), '0')])
def test_bid_and_source_age_have_independent_exact_boundaries(clock, age, expected):
    quote = mark()
    quote = replace(quote, meta=replace(quote.meta, event_at=NOW-age)) if clock == 'event_at' else replace(quote, bid_at=NOW-age)
    result = assess(funded(holdings=(holding(mark_quote=quote),)))
    observed = result.holding_marks[0]
    assert observed.gross_liquidation_value == D(expected)
    assert result.account_age == result.reconciliation_age == timedelta(0)
    reasons = observed.observation.live_quote_reasons if clock == 'event_at' else observed.mark_reasons
    if expected == '0':
        assert ('quote_too_old' if clock == 'event_at' else 'bid_too_old') in reasons
        assert result.conservative_daily_pnl == D('-502')


def test_quote_limit_tightening_and_new_assessment_recompute_actual_metadata():
    fact = holding(mark_quote=mark(bid_at=NOW-timedelta(seconds=2)))
    assert value(fact).gross_liquidation_value == D('450')
    tightened = StrategyConfig(execution=ExecutionPolicy(max_quote_age=timedelta(seconds=1)))
    assert value(fact, config=tightened).gross_liquidation_value == 0
    later = value(fact, now=NOW+timedelta(seconds=6))
    assert later.valuation_basis == 'full_premium_stress'
    assert later.observation.live_quote_reasons == ('quote_too_old',)
    assert later.holding is fact


@pytest.mark.parametrize('changes,reason', [
    ({'bid_at': None}, 'bid_time_missing'),
    ({'bid_at': NOW+timedelta(seconds=1)}, 'bid_after_available'),
    ({'bid': None}, 'bid_missing'), ({'bid_size': None}, 'bid_size_unknown'),
    ({'bid_size': 0}, 'bid_size_insufficient'),
    ({'contract': contract(strike=D('655'))}, 'mark_contract_mismatch'),
])
def test_unusable_bid_keeps_interpretable_long_premium_stress(changes, reason):
    result = value(holding(mark_quote=mark(**changes)))
    assert reason in result.mark_reasons
    assert result.valuation_basis == 'full_premium_stress'
    assert result.gross_liquidation_value == 0 and result.unrealized_pnl == D('-501')


@pytest.mark.parametrize('changes,reason', [
    ({'feed_class': 'delayed'}, 'feed_not_realtime'),
    ({'feed_class': 'indicative'}, 'feed_not_realtime'),
    ({'fidelity': 'synthetic'}, 'fidelity_not_genuine'),
    ({'availability_basis': 'assumed'}, 'availability_not_measured'),
    ({'is_fill_forward': True}, 'fill_forward'),
    ({'quality_flags': ('bad',)}, 'quality_flags_present'),
    ({'event_at': None}, 'event_time_missing'),
    ({'event_at': NOW+timedelta(seconds=1)}, 'event_after_available'),
    ({'available_at': NOW+timedelta(seconds=1)}, 'available_after_decision'),
    ({'kind': 'interval'}, 'not_quote'),
])
def test_adverse_metadata_is_reassessed_from_actual_quote(changes, reason):
    quote = mark()
    quote = replace(quote, meta=replace(quote.meta, **changes))
    result = value(holding(mark_quote=quote))
    assert reason in result.observation.live_quote_reasons
    assert result.gross_liquidation_value == 0
    assert result.valuation_basis == 'full_premium_stress'


def test_fresh_zero_bid_is_separate_from_stale_zero_and_has_no_fill_claim():
    fresh = value(holding(mark_quote=mark(bid=D('0'), bid_size=None)))
    stale = value(holding(mark_quote=mark(bid=D('0'), bid_at=NOW-timedelta(seconds=6))))
    assert fresh.valuation_basis == 'zero_bid_nonexecutable'
    assert stale.valuation_basis == 'full_premium_stress'
    assert fresh.gross_liquidation_value == stale.gross_liquidation_value == 0
    assert 'bid_size_unknown' in fresh.mark_reasons


@pytest.mark.parametrize('size,gross,daily,equity', [(2, '900', '-102', '99898'), (1, '0', '-1002', '98998')])
def test_two_whole_contracts_are_measurable_without_becoming_entry_eligible(size, gross, daily, equity):
    fact = holding(quantity=D('2.00'), basis_debit=D('1000'), mark_quote=mark(bid_size=size))
    result = assess(funded(holdings=(fact,), virtual_cash=D('98999')))
    assert result.holding_marks[0].gross_liquidation_value == D(gross)
    assert result.conservative_daily_pnl == D(daily)
    assert result.computed_liquidation_equity == D(equity)
    assert 'holding_quantity_not_one' in result.exposure_reasons and not result.observed_flat


@pytest.mark.parametrize('changes,reason', [
    ({'quantity': None}, 'holding_quantity_unknown'), ({'quantity': D('0')}, 'holding_quantity_nonpositive'),
    ({'quantity': D('-1')}, 'holding_quantity_nonpositive'), ({'quantity': D('0.5')}, 'holding_fractional_quantity'),
    ({'contract': None}, 'holding_contract_unknown'), ({'asset_kind': 'equity'}, 'holding_asset_unsupported'),
    ({'asset_kind': 'unknown'}, 'holding_asset_unsupported'), ({'quantity_unit': 'shares'}, 'holding_unit_unsupported'),
    ({'contract': contract(multiplier=10)}, 'unsupported_multiplier'),
    ({'contract': contract(multiplier=0)}, 'unsupported_multiplier'),
    ({'contract': contract(multiplier=10**1001)}, 'unsupported_multiplier'),
])
def test_unsupported_exposure_is_not_assigned_a_long_premium_loss_bound(changes, reason):
    fact = holding(**changes)
    result = assess(funded(holdings=(fact,)))
    observed = result.holding_marks[0]
    assert reason in observed.holding_reasons
    assert observed.valuation_basis == 'unavailable'
    assert observed.gross_liquidation_value is observed.net_liquidation_value is observed.unrealized_pnl is None
    assert result.conservative_daily_pnl is result.conservative_virtual_equity is result.effective_available_cash is None
    assert result.account.holdings == (fact,)


@pytest.mark.parametrize('field,amount', [('estimated_remaining_close_cost', None),
    ('estimated_remaining_close_cost', D('-1')), ('basis_debit', None), ('basis_debit', D('-1'))])
def test_unknown_or_negative_basis_and_close_cost_never_become_zero(field, amount):
    fact = holding(**{field: amount})
    result = assess(funded(holdings=(fact,)))
    observed = result.holding_marks[0]
    assert observed.gross_liquidation_value == D('450')
    assert observed.unrealized_pnl is result.conservative_daily_pnl is result.conservative_virtual_equity is None
    assert getattr(fact, field) is amount
    assert observed.net_liquidation_value == (D('449') if field == 'basis_debit' else None)
    assert f"{field}_{'missing' if amount is None else 'negative'}" in observed.cost_reasons
    if field == 'basis_debit' and amount is None:
        assert result.computed_liquidation_equity == D('99948')


def test_missing_basis_or_report_does_not_erase_other_independent_inputs():
    result = assess(funded(holdings=(holding(basis_debit=None),), virtual_equity=None))
    assert result.holding_marks[0].net_liquidation_value == D('449')
    assert result.computed_liquidation_equity == D('99948')
    assert result.virtual_unencumbered_cash == D('99499')
    assert result.conservative_virtual_equity is None


def test_net_cash_constraints_do_not_double_subtract_broker_holds_or_debit_equity():
    snapshot = funded(holdings=(), virtual_cash=D('1000'), virtual_settled_cash=D('1000'),
                      virtual_reserved_cash=D('100'), virtual_equity=D('1000'),
                      broker_settled_cash=D('1000'), broker_available_cash=D('850'),
                      broker_nonmargin_buying_power=D('850'))
    result = assess(snapshot)
    assert result.virtual_unencumbered_cash == D('900') and result.effective_available_cash == D('850')
    assert result.computed_liquidation_equity == D('1000') and result.conservative_daily_pnl == D('-1')
    huge_broker = assess(replace(snapshot, broker_settled_cash=D('1000000'),
                               broker_available_cash=D('1000000'), broker_nonmargin_buying_power=D('1000000')))
    assert huge_broker.effective_available_cash == D('900')


@pytest.mark.parametrize('field', ['virtual_cash', 'virtual_settled_cash', 'virtual_reserved_cash',
    'broker_settled_cash', 'broker_available_cash', 'broker_nonmargin_buying_power'])
def test_each_unknown_cash_operand_blocks_effective_cash_without_fallback(field):
    result = assess(funded(**{field: None}), config=StrategyConfig(initial_virtual_equity=D('150000')))
    assert result.effective_available_cash is None
    assert f'{field}_missing' in result.capital_reasons
    if field.startswith('broker_'):
        assert result.virtual_unencumbered_cash == D('99499')


def test_missing_realized_cash_and_reported_equity_have_separate_dependencies():
    missing_realized = assess(funded(session_realized_pnl=None))
    assert missing_realized.conservative_daily_pnl is None
    assert missing_realized.computed_liquidation_equity == D('99948')
    missing_cash = assess(funded(virtual_cash=None))
    assert missing_cash.computed_liquidation_equity is None
    assert missing_cash.conservative_daily_pnl == D('-52')
    empty = assess(funded(holdings=(), virtual_cash=D('1000'), virtual_equity=D('1000'), session_realized_pnl=D('-3')))
    assert empty.unrealized_pnl == 0 and empty.conservative_daily_pnl == D('-3')
    assert empty.computed_liquidation_equity == empty.conservative_virtual_equity == D('1000')


def test_negative_cash_and_equity_stay_signed_and_positive_unrealized_cannot_offset_realized():
    result = assess(funded(holdings=(), virtual_cash=D('50'), virtual_settled_cash=D('40'),
                           virtual_reserved_cash=D('100'), virtual_equity=D('-1')))
    assert result.virtual_unencumbered_cash == result.effective_available_cash == D('-60')
    assert result.conservative_virtual_equity == D('-1')
    result = assess(funded(holdings=(holding(mark_quote=mark(bid=D('6'))),), session_realized_pnl=D('-1000')))
    assert result.conservative_daily_pnl == D('-1000')


@pytest.mark.parametrize('field', ['virtual_reserved_cash', 'applicable_round_trip_fees'])
def test_invalid_reservation_or_fee_claim_is_not_silently_clipped(field):
    result = assess(funded(**{field: D('-1')}))
    assert f'{field}_negative' in result.integrity_reasons
    assert result.effective_available_cash is None
    assert result.holding_marks[0].gross_liquidation_value == D('450')


@pytest.mark.parametrize('changes', [dict(holdings_completeness='unknown'), dict(holdings_completeness='incomplete'),
    dict(orders_completeness='unknown'), dict(orders_completeness='incomplete'),
    dict(reconciled_ledger_revision='other'), dict(ledger_revision=None), dict(currency='EUR')])
def test_incomplete_or_incoherent_account_never_aggregates_away_uncertainty(changes):
    result = assess(funded(**changes))
    assert result.holding_marks[0].gross_liquidation_value == D('450')
    assert result.unrealized_pnl is result.computed_liquidation_equity is result.conservative_virtual_equity is None
    assert result.effective_available_cash is None
    assert not result.observed_flat


@pytest.mark.parametrize('changes', [dict(as_of=NOW-timedelta(seconds=6)), dict(reconciled_at=NOW-timedelta(seconds=6)),
    dict(connection='disconnected'), dict(availability_basis='assumed')])
def test_noncurrent_claims_keep_independent_stress_without_current_approval(changes):
    result = assess(funded(holdings=(holding(mark_quote=None),), **changes))
    assert result.time_reasons or result.reconciliation_reasons
    assert result.conservative_daily_pnl == D('-502')
    assert result.computed_liquidation_equity == D('99498')
    assert not result.observed_flat


@pytest.mark.parametrize('status', ['open', 'cancel_pending', 'replace_pending', 'terminal', 'unknown'])
def test_order_possible_fill_never_double_books_premium_or_claims_flat(status):
    fact = order(status=status, cumulative_filled_quantity=D('0.5'), reserved_cash=D('521'))
    result = assess(funded(open_orders=(fact,), virtual_reserved_cash=D('521')))
    assert result.holding_marks[0].gross_liquidation_value == D('450')
    assert result.virtual_unencumbered_cash == D('98978')
    assert result.conservative_daily_pnl is result.computed_liquidation_equity is result.effective_available_cash is None
    assert 'order_execution_accounting_unresolved' in result.capital_reasons
    assert result.account.open_orders == (fact,) and not result.observed_flat


def test_distinct_lots_sum_before_clipping_while_duplicate_identity_suppresses_aggregates():
    first = holding(mark_quote=mark(bid=D('6')))
    second = holding(position_id='position-2', provider_record_id='holding-record-2')
    for facts in ((first, second), (second, first)):
        result = assess(funded(holdings=facts))
        assert result.unrealized_pnl == D('48') and result.conservative_daily_pnl == D('-1')
        assert result.computed_liquidation_equity == D('100547')
    for facts in ((first, first), (first, replace(first, quantity=D('2'))),
                  (first, replace(first, position_id='position-2'))):
        result = assess(funded(holdings=facts))
        assert len(result.holding_marks) == 2 and result.account.holdings is facts
        assert result.conservative_daily_pnl is result.computed_liquidation_equity is None
        assert 'duplicate_source_fact_unresolved' in result.capital_reasons


def test_prior_session_or_unknown_entry_time_does_not_erase_known_bid_money():
    for when in (None, NOW-timedelta(days=1)):
        result = assess(funded(holdings=(holding(entry_filled_at=when),)))
        assert result.conservative_daily_pnl == D('-52')
        assert result.computed_liquidation_equity == D('99948')
        assert result.exposure_reasons and not result.observed_flat


def test_explicit_context_ignores_hostile_ambient_rounding_limits_traps_and_flags():
    fact = holding(quantity=D('1.'+'0'*1200), basis_debit=D('500.'+'0'*1200))
    snapshot = funded(holdings=(fact,))
    with localcontext(Context(prec=1, rounding=ROUND_DOWN, Emin=-1, Emax=1,
                              traps=[Inexact, Rounded, Overflow, InvalidOperation])) as ambient:
        ambient.flags[Rounded] = True
        before = ambient.copy()
        result = assess(snapshot)
        assert result.conservative_daily_pnl == D('-52') and result.computed_liquidation_equity == D('99948')
        assert ambient.prec == before.prec and ambient.rounding == before.rounding
        assert ambient.Emin == before.Emin and ambient.Emax == before.Emax
        assert ambient.traps == before.traps and ambient.flags == before.flags


@pytest.mark.parametrize('changes', [dict(quantity=D('1E+1000000')),
    dict(mark_quote=mark(bid=D('1E+1000000'))), dict(basis_debit=D('1E+1000000')),
    dict(estimated_remaining_close_cost=D('1E-1000000'))])
def test_extreme_finite_operand_yields_bounded_failure_and_retained_holding(changes):
    fact = holding(**changes)
    result = assess(funded(holdings=(fact,)))
    observed = result.holding_marks[0]
    assert 'arithmetic_precision_unsupported' in observed.holding_reasons + observed.mark_reasons + observed.cost_reasons
    assert result.conservative_daily_pnl is None and result.account.holdings == (fact,)
    assert result.virtual_unencumbered_cash == D('99499')


def test_carry_overflow_and_tiny_exact_amount_are_bounded_without_float_or_cent_rounding():
    tiny = value(holding(mark_quote=mark(bid=D('1E-999')), basis_debit=D('0'), estimated_remaining_close_cost=D('0')))
    assert tiny.gross_liquidation_value == D('1E-997')
    cash = D('9'*1000)
    result = assess(funded(virtual_cash=cash))
    assert result.computed_liquidation_equity is None
    assert 'arithmetic_precision_unsupported' in result.capital_reasons
    assert result.holding_marks[0].net_liquidation_value == D('449')


def test_all_zero_and_extreme_zero_exponents_do_not_need_a_precision_sentinel():
    zero = D('0E-1000000')
    result = assess(funded(holdings=(), virtual_cash=zero, virtual_equity=zero,
                           virtual_settled_cash=zero, virtual_reserved_cash=zero,
                           session_realized_pnl=zero, broker_settled_cash=zero,
                           broker_available_cash=zero, broker_nonmargin_buying_power=zero))
    assert result.conservative_daily_pnl == result.conservative_virtual_equity == result.effective_available_cash == 0
    assert result.capital_reasons == ()


def test_mark_and_account_amounts_are_frozen_derived_outputs_not_supplied_certificates():
    observed = value(holding())
    result = assess(funded())
    for record, field in ((observed, 'gross_liquidation_value'), (result, 'effective_available_cash')):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, D('1000000'))
    with pytest.raises(TypeError):
        api.HoldingMarkAssessment(holding(), StrategyConfig(), NOW, gross_liquidation_value=D('1000000'))
    for changes in ({'holding': object()}, {'config': object()}, {'now': NOW.replace(tzinfo=None)}):
        with pytest.raises((TypeError, ValueError)):
            api.HoldingMarkAssessment(**(dict(holding=holding(), config=StrategyConfig(), now=NOW) | changes))
    missing = assess(None)
    assert missing.holding_marks == () and missing.conservative_daily_pnl is None
    assert missing.effective_available_cash is None and not missing.observed_flat


def test_oversized_bid_size_is_rejected_before_decimal_integer_comparison():
    observed = value(holding(mark_quote=mark(bid_size=10**1001)))
    assert observed.valuation_basis == 'full_premium_stress'
    assert observed.gross_liquidation_value == 0
    assert 'arithmetic_precision_unsupported' in observed.mark_reasons


def test_unsupported_intermediate_product_does_not_hide_behind_small_final_bid():
    fact = holding(quantity=D('1E+999'), mark_quote=mark(bid=D('1E-999'), bid_size=10**999))
    observed = value(fact)
    assert observed.valuation_basis == 'unavailable'
    assert observed.gross_liquidation_value is None
    assert observed.mark_reasons == ('arithmetic_precision_unsupported',)
