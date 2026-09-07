"""Assess real catalog-bound candidates without authored admission or approval."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext, Inexact, Rounded, ROUND_UP
import importlib.util
from pathlib import Path

import pytest

from options_lab.account import AccountSnapshot
from options_lab.admission import verify_fixture_bundle
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request

NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
VERIFIED = datetime(2026, 9, 8, tzinfo=timezone.utc)
FIXTURE_ID = 'p13b-candidate-evidence-v1'
KINDS = ('option_quote', 'underlying_quote', 'greek_observation', 'quote_coherence',
         'instrument_tradability', 'provider_contract_mapping', 'contract_reference', 'tick_rule', 'account_snapshot')


def build(scene='initial', *, extras=(), omit=(), at=NOW, config=None, reverse=False):
    """Admit real source bytes and resolve an explicit finite source request."""
    config = config or StrategyConfig()
    payload = (Path(__file__).parent / 'fixtures' / (FIXTURE_ID+'.json')).read_bytes()
    verified = verify_fixture_bundle(FIXTURE_ID, payload, event_id='verify', raw_ref='fixture', received_at=VERIFIED)
    assert verified.rejection is None, verified.rejection
    ids = ['session', *(scene+'-'+kind for kind in KINDS), *extras]
    ids = [name for name in ids if name not in omit]
    request = normalize_context_request(dict(decision_id='decision', decision_at=at.isoformat(),
        member_record_ids=ids[::-1] if reverse else ids), event_id='request', raw_ref='request', received_at=VERIFIED)
    result = build_decision_context(request, verified.value, config=config, previous_feature_state=None)
    return result, config


def assess(scene='initial', *, cap=Decimal('5.10'), account_changes=None, now=None, **kwargs):
    """Consume the actual selected account and contract through the sole public gate."""
    assert importlib.util.find_spec('options_lab.candidates') is not None, 'shared candidate assessor is missing'
    from options_lab.candidates import assess_candidate
    result, config = build(scene, **kwargs)
    account = next(c.value for c in result.components if c.member.record_id == scene+'-account_snapshot')
    if account_changes:
        account = replace(account, **account_changes)
    contract = next(c.contract for c in result.components if c.member.record_id.startswith(scene+'-') and c.contract is not None)
    return assess_candidate(contract, result.context, account, config,
                            now=now or result.context.decision_at, original_ask_cap=cap)


@pytest.mark.parametrize('scene,seconds,cost,bid,ask', [
    ('initial', 0, '521', '5.00', '5.10'), ('fresh', 2, '516', '4.95', '5.00'),
    ('wide', 2, '531', '4.80', '5.00')])
def test_original_cap_uses_actual_current_spread_without_new_slot(scene, seconds, cost, bid, ask):
    result = assess(scene, at=NOW+timedelta(seconds=seconds))
    assert result.eligible, result.reasons
    assert result.required_cash == Decimal(cost)
    assert (result.option_quote.bid, result.option_quote.ask) == (Decimal(bid), Decimal(ask))
    assert result.original_ask_cap == Decimal('5.10') and result.account.virtual_cash == Decimal('150000')
    assert result.context.feature_state is None
    if seconds:
        assert 'decision_off_grid' in result.context.timing.reasons


def test_context_projection_preserves_actual_objects_and_rejection_namespace():
    result, _ = build()
    assert hasattr(result.context, 'components'), 'full inspected evidence projection is missing'
    assert result.context.components is result.components
    assert all(any(c is full for full in result.components) for c in result.context.selected_components)
    assert all(c.representative_record_id is None for c in result.components)


@pytest.mark.parametrize('cap,reason', [(None, 'original_ask_cap_missing'), (Decimal(0), 'original_ask_cap_nonpositive'),
                                      (Decimal('5.00'), 'ask_exceeds_original_cap')])
def test_missing_or_incompatible_cap_retains_current_quote_checks(cap, reason):
    result = assess(cap=cap)
    assert not result.eligible and any(r.endswith(':'+reason) for r in result.reasons)
    assert result.option_quote.ask == Decimal('5.10') and result.option_observation is not None
    assert result.required_cash is None


def test_current_context_clock_must_equal_explicit_now_and_config_must_bind():
    from options_lab.candidates import assess_candidate
    result = assess(now=NOW+timedelta(seconds=2))
    assert 'context_time/ledger_freshness:context:context_time_mismatch' in result.reasons
    tightened = replace(result.config, min_abs_delta=Decimal('.51'))
    changed = assess_candidate(result.contract, result.context, result.account, tightened, now=NOW, original_ask_cap=Decimal('5.10'))
    assert 'config_policy_input_binding:context:config_mismatch' in changed.reasons
    assert 'config_policy_input_binding:context:policy_mismatch' in changed.reasons
    assert 'delta_band:candidate:delta_out_of_range' in changed.reasons


@pytest.mark.parametrize('scene,eligible,code', [
    ('dte6', False, 'dte_out_of_range'), ('dte7', True, None), ('dte21', True, None), ('dte22', False, 'dte_out_of_range'),
    ('delta39', False, 'delta_out_of_range'), ('delta40', True, None), ('delta60', True, None), ('delta61', False, 'delta_out_of_range'),
    ('put', True, None), ('wrong-sign', False, 'delta_sign_mismatch'), ('locked-underlying', True, None),
    ('missing-ask', False, 'ask_missing'), ('locked', False, 'quote_locked'), ('crossed', False, 'quote_crossed'),
    ('bad-proof', False, 'greek_coherence_missing'), ('bad-greek', False, 'input_hash_mismatch'), ('stale-greek', False, 'greek_too_old')])
def test_actual_independent_market_scenes_preserve_owner_limits(scene, eligible, code):
    result = assess(scene)
    assert result.eligible is eligible, result.reasons
    if code:
        assert any(r.endswith(':'+code) for r in result.reasons), result.reasons


@pytest.mark.parametrize('extras,code', [
    (('overlap-reference',), 'reference_ambiguous'), (('invalid-reference',), 'reference_ambiguous'),
    (('overlap-status',), 'instrument_ambiguous'), (('unknown-status',), 'instrument_ambiguous'),
    (('null-status',), 'instrument_unresolved')])
def test_complete_potential_applicability_precedes_favorable_policy_filter(extras, code):
    result = assess(extras=extras)
    assert not result.eligible and any(r.endswith(':'+code) for r in result.reasons), result.reasons
    assert any(c.member.record_id == extras[0] for c in result.relevant_components)


def test_valid_future_schedules_are_audited_without_blocking_current_claims():
    result = assess(extras=('scheduled-reference', 'scheduled-status', 'scheduled-tick'))
    assert result.eligible, result.reasons
    assert len(result.reference_assessments) == len(result.tick_assessments) == 2
    assert len(result.applicable_tick_components) == len(result.session_assessments) == 1


def test_overlapping_tick_claims_reject_before_alignment_filtering():
    result = assess('overlap', cap=Decimal('3.51'), extras=('overlap-tick',))
    assert not result.eligible and 'tick_cap:tick:tick_rule_ambiguous' in result.reasons
    assert len(result.applicable_tick_components) == 2
    assert sum(not t.reasons for t in result.tick_assessments) == 1
    assert 'tick_cap:tick:price_off_grid' in result.reasons


@pytest.mark.parametrize('cap', [None, Decimal('5.10')])
def test_negative_fee_does_not_skip_independent_configured_spread_checks(cap):
    result = assess('negative-fee', cap=cap, config=replace(StrategyConfig(), max_spread_fraction=Decimal('.001')))
    assert result.quote_budget_assessment is None
    assert result.option_quote_reasons == ('spread_too_wide',)
    assert 'option_quote:quote:spread_too_wide' in result.reasons
    assert any('applicable_round_trip_fees' in r for r in result.reasons)


@pytest.mark.parametrize('field,value,cost,reason', [
    ('estimated_round_trip_cost', Decimal(2), '522', None), ('round_trip_fee_floor', Decimal(3), '523', None),
    ('premium_fraction', Decimal('.003'), '521', 'premium cap exceeded'),
    ('min_dte', 15, '521', 'dte_out_of_range'), ('max_dte', 13, '521', 'dte_out_of_range'),
    ('min_abs_delta', Decimal('.51'), '521', 'delta_out_of_range'),
    ('max_abs_delta', Decimal('.49'), '521', 'delta_out_of_range')])
def test_tighter_actual_context_config_reaches_owning_assessment(field, value, cost, reason):
    result = assess(config=replace(StrategyConfig(), **{field: value}))
    assert result.required_cash == Decimal(cost)
    assert result.eligible is (reason is None), result.reasons
    if reason:
        assert any(r.endswith(':'+reason) for r in result.reasons)


@pytest.mark.parametrize('changes,reason', [
    ({'virtual_equity': Decimal('104200'), 'broker_available_cash': Decimal('521')}, None),
    ({'virtual_equity': Decimal('104199.99'), 'broker_available_cash': Decimal('520.99')}, 'premium cap exceeded'),
    ({'broker_available_cash': Decimal('520.99')}, 'insufficient available cash'),
    ({'virtual_equity': None}, 'virtual equity is undeclared'),
    ({'virtual_equity': Decimal('-1')}, 'virtual equity must be positive')])
def test_supplied_adverse_account_keeps_budget_evidence_but_never_borrows_source_identity(changes, reason):
    result = assess(account_changes=changes)
    assert not result.eligible and 'source_admission:account:account_source_mismatch' in result.reasons
    assert result.quote_budget_assessment.budget.reason == reason
    assert result.account_assessment.account is result.account


def test_missing_account_and_zero_chain_helper_preserve_common_failures():
    from options_lab.candidates import assess_candidate, _context_account_evidence
    result = assess()
    missing = assess_candidate(result.contract, result.context, None, result.config, now=NOW, original_ask_cap=Decimal('5.10'))
    assert not missing.eligible and missing.account is None and missing.account_hash is None
    assert missing.option_observation is not None
    context, config = build(omit=tuple('initial-'+k for k in KINDS if k != 'account_snapshot'))
    common = _context_account_evidence(context.context, result.account, config, now=NOW)
    assert len(common) == 6 and common[0].observed_flat and common[-1] == ()
    assert not context.context.option_quotes


def test_other_contract_policy_is_scoped_but_unknown_and_omitted_sources_block():
    result = assess(extras=('other-bad-option_quote',))
    assert result.eligible and 'quote_crossed' in result.context.input_reasons
    for scene in ('unknown-update', 'omitted-update'):
        failed = assess(scene)
        assert not failed.eligible and failed.option_quote is None
        assert 'source_admission:candidate:source_scope_unresolved' in failed.reasons
        assert any(c.member.record_id == scene+'-successor' and not c.requested for c in failed.relevant_components)


def alias_assessment(scene='m1', *, embedded=True, successor=False, reverse=False):
    """Consume actual independently requested mark duplicates and selected aliases."""
    from options_lab.candidates import assess_candidate
    extras = [scene+'-b-representative']+([scene+'-c-embedded'] if embedded else [])+([scene+'-successor'] if successor else [])
    result, config = build(scene, extras=extras, omit=(scene+'-option_quote',), reverse=reverse)
    account = next(c.value for c in result.components if c.member.kind == 'account_snapshot')
    return assess_candidate(account.holdings[0].contract, result.context, account, config, now=NOW, original_ask_cap=Decimal('5.10'))


def test_requested_mark_duplicate_binds_final_promoted_owner_and_retains_own_age():
    result = alias_assessment()
    by_id = {c.member.record_id: c for c in result.context.components}
    assert by_id['m1-a-unrequested'].representative_record_id == 'm1-b-representative'
    assert by_id['m1-c-embedded'].representative_record_id == 'm1-b-representative'
    assert by_id['m1-b-representative'].representative_record_id is None
    assert tuple(c.member.record_id for c in result.mark_components[0]) == ('m1-b-representative', 'm1-c-embedded')
    assert 'source_admission:account:mark_source_unavailable' not in result.reasons
    assert not result.eligible and 'account_exposure:account:not_observed_flat' in result.reasons
    assert result.account.holdings[0].mark_quote.meta.received_at == NOW-timedelta(seconds=1)+timedelta(microseconds=3)
    assert result.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('500')
    reordered = alias_assessment(reverse=True)
    assert result.reasons == reordered.reasons and result.context_input_digest == reordered.context_input_digest


@pytest.mark.parametrize('scene,embedded,successor', [('m1', False, False), ('m3-conflict', True, True),
                                                    ('m3-retired', True, True), ('m3-retired', True, False)])
def test_duplicate_cannot_borrow_request_or_revive_conflicted_retired_representative(scene, embedded, successor):
    result = alias_assessment(scene, embedded=embedded, successor=successor)
    assert not result.eligible and 'source_admission:account:mark_source_unavailable' in result.reasons
    assert result.account.holdings and result.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('500')
    assert any(c.member.record_id == scene+'-c-embedded' for c in result.mark_components[0])


def test_unsupported_full_account_identity_retains_actual_occupied_stress():
    from options_lab.candidates import assess_candidate
    result = alias_assessment()
    account = replace(result.account, virtual_cash=Decimal('1e1000'))
    failed = assess_candidate(result.contract, result.context, account, result.config, now=NOW, original_ask_cap=Decimal('5.10'))
    assert failed.account is account and failed.account_hash is None
    assert 'input_integrity:account:account_identity_representation_unsupported' in failed.reasons
    assert failed.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('500')
    assert failed.account_assessment.holding_marks[0].unrealized_pnl == Decimal('-1')


@pytest.mark.parametrize('delta,stale', [(timedelta(seconds=3), False), (timedelta(seconds=3,microseconds=1), True)])
def test_actual_quote_and_greek_clocks_include_exact_five_seconds(delta, stale):
    result = assess(at=NOW+delta)
    assert ('option_quote:quote:bid_too_old' in result.reasons) is stale
    assert ('greek_readiness:greek:greek_too_old' in result.reasons) is stale
    assert not result.account_assessment.time_reasons


@pytest.mark.parametrize('field', ['max_account_age', 'max_reconciliation_age'])
def test_tighter_independent_account_clock_does_not_refresh_from_receipt(field):
    result = assess(at=NOW+timedelta(seconds=2), config=replace(StrategyConfig(), **{field: timedelta(seconds=1)}))
    expected = 'account_too_old' if field == 'max_account_age' else 'reconciliation_too_old'
    assert result.account_assessment.time_reasons == (expected,)


def test_exact_decimal_ambient_context_and_factory_boundaries():
    from options_lab.candidates import CandidateAssessment, assess_candidate
    result = assess()
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 2, ROUND_UP
        ctx.traps[Inexact] = ctx.traps[Rounded] = True
        before = ctx.copy()
        low = assess()
        assert low.required_cash == Decimal('521') and low.eligible
        assert ctx.flags == before.flags and ctx.traps == before.traps and ctx.prec == 2
    with pytest.raises(TypeError):
        CandidateAssessment()
    with pytest.raises(FrozenInstanceError):
        result.original_ask_cap = Decimal(1)
    class HostileDecimal(Decimal):
        """This class represents a subclass whose arithmetic hooks must not run."""
        def is_finite(self):
            raise AssertionError('untrusted subclass hook executed')
    for cap, error in [(HostileDecimal('5.10'), TypeError), (5.10, TypeError), (Decimal('NaN'), ValueError)]:
        with pytest.raises(error):
            assess_candidate(result.contract, result.context, result.account, result.config, now=NOW, original_ask_cap=cap)


def test_negative_fee_still_retains_incompatible_original_cap():
    result = assess('negative-fee', cap=Decimal('5.00'))
    assert result.quote_budget_assessment is None
    assert 'tick_cap:quote:ask_exceeds_original_cap' in result.reasons


@pytest.mark.parametrize('field', ['event_id', 'stream_id', 'receive_sequence', 'supersedes_record_id'])
def test_matching_typed_mark_cannot_launder_four_unretained_envelope_fields(field):
    from options_lab.candidates import assess_candidate
    context, config = build(extras=('mark-'+field,), omit=('initial-account_snapshot',))
    account = next(c.value for c in context.components if c.member.kind == 'account_snapshot')
    quote = context.context.option_quotes[0]
    assert account.holdings[0].mark_quote == quote
    result = assess_candidate(quote.contract, context.context, account, config, now=NOW, original_ask_cap=Decimal('5.10'))
    assert 'source_admission:account:mark_source_unavailable' in result.reasons
    assert result.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('500')


@pytest.mark.parametrize('error', [ValueError('unexpected hash failure'), TypeError('unexpected hash type')])
def test_only_documented_account_identity_valueerror_is_translated(monkeypatch, error):
    import options_lab.candidates as candidates
    def broken_hash(account):
        raise error
    monkeypatch.setattr(candidates, 'account_hash', broken_hash)
    with pytest.raises(type(error), match=str(error)):
        assess()


def test_old_context_digest_and_full_rejection_indexes_remain_owner_facts():
    from test_account_context import build as old_build, MARKET
    from options_lab.candidates import assess_candidate
    old = old_build(MARKET+['occupied'])
    assert old.context.input_digest == '5b0f61c16ef29073254c5fbbfd85061254310e8e917ecb52935d030fc9aadb5e'
    result = old_build(MARKET+['future-A', 'future-B', 'malformed-B'])
    future = next(c for c in result.components if c.member.record_id == 'future-B')
    assert future.disposition == 'future' and future.rejection_indexes
    assert any(result.rejections[i] not in result.context.rejections for i in future.rejection_indexes)
    account = next(c.value for c in result.components if c.member.record_id == 'malformed-A')
    checked = assess_candidate(result.context.option_quotes[0].contract, result.context, account, StrategyConfig(), now=NOW, original_ask_cap=Decimal('5.10'))
    assert not checked.eligible and 'source_admission:context:normalization_failed' in checked.reasons


def test_real_bid_only_account_mark_keeps_value_without_entry_ask_or_greeks():
    from test_account_context import build as old_build
    from options_lab.candidates import assess_candidate
    context = old_build(['bid-only', 'mark-bid-only', 'session'])
    account = next(c.value for c in context.components if c.member.kind == 'account_snapshot')
    result = assess_candidate(account.holdings[0].contract, context.context, account, StrategyConfig(), now=NOW, original_ask_cap=Decimal('5.10'))
    assert result.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('490')
    assert 'source_admission:account:mark_source_unavailable' not in result.reasons
    assert result.option_quote.ask is None and not result.eligible


def test_common_evidence_retains_conflicting_calendar_when_no_session_wins():
    from test_context_reference import scenario
    from options_lab.candidates import _context_account_evidence
    result = scenario('session-conflict')
    assert result.context.session is None
    common = _context_account_evidence(result.context, None, StrategyConfig(), now=result.context.decision_at)
    assert len(common[2]) == 2
    assert 'source_admission:context:current_roots_conflict' in common[-1]


@pytest.mark.parametrize('name,reason', [('capital-equal', None), ('capital-below', 'premium cap exceeded'),
                                      ('cash-below', 'insufficient available cash')])
def test_actual_bound_capital_equality_and_cent_below_are_eligible_only_at_boundary(name, reason):
    from options_lab.candidates import assess_candidate
    result, config = build(extras=(name,), omit=('initial-account_snapshot',))
    account = next(c.value for c in result.components if c.member.kind == 'account_snapshot')
    assessed = assess_candidate(result.context.option_quotes[0].contract, result.context, account, config, now=NOW, original_ask_cap=Decimal('5.10'))
    assert assessed.eligible is (reason is None)
    assert assessed.required_cash == Decimal('521') and assessed.quote_budget_assessment.budget.reason == reason


@pytest.mark.parametrize('kind,code', [('option_quote', 'quote_missing'), ('underlying_quote', 'underlying_missing'),
    ('greek_observation', 'greek_missing'), ('quote_coherence', 'coherence_missing'),
    ('instrument_tradability', 'instrument_missing'), ('contract_reference', 'reference_missing'), ('tick_rule', 'tick_rule_missing')])
def test_missing_actual_owner_input_never_selects_a_substitute(kind, code):
    result = assess(omit=('initial-'+kind,))
    assert not result.eligible and any(r.endswith(':'+code) for r in result.reasons)


def test_unknown_chain_and_malformed_request_remain_common_evidence():
    from options_lab.candidates import _context_account_evidence
    result, config = build('unknown-update')
    account = next(c.value for c in result.components if c.member.kind == 'account_snapshot')
    common = _context_account_evidence(result.context, account, config, now=NOW)
    assert not result.context.option_quotes and any(c.contract is None and c.member.kind == 'option_quote' for c in common[2])
    assert 'source_admission:context:normalization_failed' in common[-1]
    request = normalize_context_request(dict(decision_id='decision', decision_at=NOW.isoformat(),
        member_record_ids=['missing-member', False]), event_id='request', raw_ref='request', received_at=VERIFIED)
    failed = build_decision_context(request, None, config=config, previous_feature_state=None)
    reasons = _context_account_evidence(failed.context, None, config, now=NOW)[-1]
    assert 'input_integrity:context:unknown_member' in reasons and 'input_integrity:context:invalid_type' in reasons
    assert 'config_policy_input_binding:context:manifest_missing' in reasons


@pytest.mark.parametrize('microseconds,stale', [(0, False), (1, True)])
def test_account_and_reconciliation_age_five_seconds_is_inclusive(microseconds, stale):
    result = assess(at=NOW+timedelta(seconds=5, microseconds=microseconds))
    assert ('account_too_old' in result.account_assessment.time_reasons) is stale
    assert ('reconciliation_too_old' in result.account_assessment.time_reasons) is stale


def test_unrequested_identical_occurrence_does_not_veto_requested_mark_binding():
    result = alias_assessment('identical', embedded=False)
    a, b = (next(c for c in result.context.components if c.member.record_id == 'identical-'+name)
            for name in ('a-unrequested', 'b-representative'))
    assert a.member.raw_body_bytes == b.member.raw_body_bytes and a.member.envelope_bytes == b.member.envelope_bytes
    assert not a.requested and a.disposition == 'duplicate' and a.representative_record_id == b.member.record_id
    assert b.requested and b.disposition == 'selected' and b.representative_record_id is None
    assert result.mark_components == ((a, b),) and result.account.holdings[0].mark_quote == b.value
    assert result.account.holdings[0].mark_quote.meta.received_at == NOW-timedelta(seconds=1)
    assert result.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('500')
    assert 'source_admission:account:mark_source_unavailable' not in result.reasons
    assert not result.eligible and 'account_exposure:account:not_observed_flat' in result.reasons
    reordered = alias_assessment('identical', embedded=False, reverse=True)
    assert result.reasons == reordered.reasons and result.context_input_digest == reordered.context_input_digest
    assert result.mark_components == reordered.mark_components


def test_identical_embedded_mark_without_any_requested_occurrence_still_fails():
    from options_lab.candidates import assess_candidate
    context, config = build('identical', omit=('identical-option_quote',))
    account = next(c.value for c in context.components if c.member.kind == 'account_snapshot')
    result = assess_candidate(account.holdings[0].contract, context.context, account, config, now=NOW, original_ask_cap=Decimal('5.10'))
    assert not context.context.option_quotes and result.mark_components == ((),)
    assert 'source_admission:account:mark_source_unavailable' in result.reasons
    assert result.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('500')
