"""Select from actual admitted source populations and preserve rejection evidence."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext, Inexact, Rounded, ROUND_UP
from datetime import timedelta
from hashlib import sha256
from itertools import permutations
import json
from pathlib import Path

import pytest

from options_lab import candidates
from options_lab.admission import verify_fixture_bundle
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from test_candidates import build as build_b, NOW, VERIFIED

NAMES = ('c99', 'c100', 'late', 'wide', 'delta', 'p99', 'p100', 'pdelta')
KINDS = ('option_quote', 'greek_observation', 'quote_coherence', 'instrument_tradability',
         'provider_contract_mapping', 'contract_reference', 'tick_rule')


def build(names=NAMES, *, ids=None, config=None, extras=(), at=NOW):
    """Build one actual finite population through the public admission owners."""
    config = config or StrategyConfig()
    fixture_id = 'p13c-candidate-selection-v1'
    payload = (Path(__file__).parent / 'fixtures' / (fixture_id+'.json')).read_bytes()
    verified = verify_fixture_bundle(fixture_id, payload, event_id='verify', raw_ref='fixture', received_at=VERIFIED)
    assert verified.rejection is None, verified.rejection
    ids = ids if ids is not None else ['session', 'account_snapshot', 'underlying_quote',
                                      *(name+'-'+kind for name in names for kind in KINDS), *extras]
    request = normalize_context_request(dict(decision_id='decision', decision_at=at.isoformat(), member_record_ids=ids),
        event_id='request', raw_ref='request', received_at=VERIFIED)
    context = build_decision_context(request, verified.value, config=config, previous_feature_state=None).context
    account = next(c.value for c in context.components if c.member.kind == 'account_snapshot' and c.disposition == 'selected')
    return context, account, config


def select_b(scene='initial', **kwargs):
    """Reuse the unchanged B fixture's actual independent adverse source scenes."""
    result, config = build_b(scene, **kwargs)
    account = next(c.value for c in result.components if c.member.record_id == scene+'-account_snapshot')
    return candidates.select_candidates(result.context, account, config)


def test_selects_both_rights_and_retains_every_exact_rank():
    context, account, config = build()
    assert hasattr(candidates, 'select_candidates'), 'deterministic initial selection is missing'
    result = candidates.select_candidates(context, account, config)
    assert result.selection_reason is None, [(c.contract, c.reasons) for c in result.considered]
    assert [(c.contract.right, c.contract.strike) for c in result.selected] == [('call', Decimal(99)), ('put', Decimal(100))]
    assert {(r.contract.right, r.contract.strike): r.rank for r in result.considered} == {
        ('call', Decimal(99)): 1, ('call', Decimal(100)): 2, ('call', Decimal(98)): 3,
        ('call', Decimal(101)): 4, ('call', Decimal(102)): 5,
        ('put', Decimal(99)): 2, ('put', Decimal(100)): 1, ('put', Decimal(101)): 3}
    assert all(r.disposition == ('selected' if r.rank == 1 else 'not_selected_by_rank') for r in result.considered)
    assert all(r.assessment.eligible and all(t.timing_suitable for t in r.initial_timing) for r in result.considered)
    assert 'instrument_missing' in result.initial_timing.reasons
    assert result.selected_call is next(r.assessment for r in result.considered if r.contract.right == 'call' and r.rank == 1)
    assert context.feature_state is None


def test_exact_spread_and_delta_survive_hostile_decimal_context():
    args = build(config=replace(StrategyConfig(), min_abs_delta=Decimal('.40'), max_abs_delta=Decimal('.55')))
    ordinary = candidates.select_candidates(*args)
    with localcontext() as ambient:
        ambient.prec = 2
        ambient.rounding = ROUND_UP
        ambient.traps[Inexact] = ambient.traps[Rounded] = True
        before = (ambient.flags.copy(), ambient.traps.copy())
        result = candidates.select_candidates(*args)
        assert (ambient.flags, ambient.traps) == before
    assert result.content_hash == ordinary.content_hash
    put = next(r for r in result.considered if r.contract.right == 'put' and r.contract.strike == 100)
    n, d = put.rank_evidence[2:]
    bid = 490*10**36 + 1
    ask = 510*10**36
    assert n * (ask+bid) == d * 2 * (ask-bid)
    assert n * 50 < d * 2
    assert next(r for r in result.considered if r.contract.right == 'put' and r.contract.strike == 101).rank_evidence[0] == 1


@pytest.mark.parametrize('order', list(permutations(('c99', 'c100', 'late'))))
def test_permutations_and_repeated_request_ids_preserve_full_identity(order):
    baseline = candidates.select_candidates(*build(('c99', 'c100', 'late')))
    args = build(order)
    ids = [c.member.record_id for c in args[0].components if c.requested]
    repeated = candidates.select_candidates(*build(ids=ids[::-1]+ids))
    assert repeated.content_hash == baseline.content_hash
    assert repeated.context_input_digest == baseline.context_input_digest


def test_new_receipt_changes_audit_identity_without_extra_contract_or_rank():
    baseline = candidates.select_candidates(*build())
    repeated = candidates.select_candidates(*build(extras=('c99-redelivery',)))
    assert len(repeated.considered) == len(baseline.considered) == 8
    assert [r.rank for r in repeated.considered] == [r.rank for r in baseline.considered]
    assert repeated.content_hash != baseline.content_hash


def test_initial_offgrid_retains_successful_current_assessor_and_original_expiry():
    original = select_b()
    current = select_b('fresh', at=NOW+timedelta(seconds=2))
    assert current.selection_reason == 'decision_off_grid'
    assert current.considered[0].assessment.eligible
    assert not current.selected and current.considered[0].rank is None
    assert original.initial_timing.original_expires_at == NOW+timedelta(seconds=5)
    from options_lab.sessions import assess_submission_time
    t = original.considered[0].initial_timing[0]
    expired = assess_submission_time(t.session_assessment.session, t.session_assessment.tradability,
        config=original.config, decision_at=NOW, original_expires_at=NOW+timedelta(seconds=5), now=NOW+timedelta(seconds=5))
    assert 'original_intent_expired' in expired.reasons


@pytest.mark.parametrize('scene,headline', [('crossed', 'no_eligible_candidates'), ('missing-ask', 'no_eligible_candidates'),
    ('wrong-sign', 'no_eligible_candidates'), ('unknown-update', 'source_unavailable'), ('omitted-update', 'no_eligible_candidates')])
def test_known_adverse_contract_is_never_an_empty_chain(scene, headline):
    result = select_b(scene)
    assert result.considered and result.selection_reason == headline
    assert all(r.disposition == 'ineligible' and r.rank is None for r in result.considered)
    assert result.content_hash is not None


@pytest.mark.parametrize('extras', [('overlap-status',), ('unknown-status',), ('overlap-reference',)])
def test_all_applicable_owner_evidence_survives_ambiguity(extras):
    result = select_b(extras=extras)
    assert not result.selected and result.considered[0].assessment.reasons
    if 'status' in extras[0]:
        assert len(result.considered[0].initial_timing) == 2


def test_missing_status_and_scheduled_status_keep_actual_p04_evidence():
    absent = select_b(omit=('initial-instrument_tradability',))
    assert 'instrument_missing' in absent.considered[0].initial_timing[0].reasons
    assert select_b(extras=('scheduled-status', 'scheduled-tick', 'scheduled-reference')).selected_call is not None


def test_empty_chain_and_missing_context_have_honest_clocks(monkeypatch):
    context, account, config = build(())
    empty = candidates.select_candidates(context, account, config)
    assert empty.selection_reason == 'empty_chain' and empty.considered == ()
    def forbidden(*args, **kwargs):
        raise AssertionError('missing context must not run a time-dependent assessor')
    monkeypatch.setattr(candidates, '_context_account_evidence', forbidden)
    missing = candidates.select_candidates(None, account, config)
    assert missing.selection_reason == 'context_missing'
    assert missing.account is account and missing.account_assessment is None
    assert missing.as_of is missing.decision_id is missing.initial_timing is None
    assert missing.snapshot()['as_of'] is None and missing.content_hash is not None


def test_full_snapshot_is_canonical_fresh_and_permission_free():
    result = candidates.select_candidates(*build())
    snapshot = result.snapshot()
    assert sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()).hexdigest() == result.content_hash
    assert sha256(result.snapshot_bytes).hexdigest() == result.content_hash
    snapshot['considered'][0]['reasons'].append('tampered')
    assert 'tampered' not in result.snapshot()['considered'][0]['reasons']
    assert (result.origin, result.fidelity_tier, result.permitted_use, result.operational_allowed, result.economic_allowed) == ('synthetic', 0, 'core_fixture', False, False)
    with pytest.raises(FrozenInstanceError):
        result.selected_call = None
    for cls in (candidates.CandidateSet, candidates.CandidateConsideration):
        with pytest.raises(TypeError):
            cls()


def test_unrepresentable_account_preserves_all_reached_evidence_and_clears_hash():
    context, account, config = build()
    unsupported = replace(account, virtual_cash=Decimal('1e1001'))
    result = candidates.select_candidates(context, unsupported, config)
    assert result.account is unsupported and len(result.considered) == 8
    assert result.account_assessment is not None and result.account_hash is None
    assert result.snapshot() is result.content_hash is None and not result.selected
    assert all(r.disposition == 'ineligible' for r in result.considered)


def test_snapshot_retains_original_source_clocks_and_owner_inputs():
    result = candidates.select_candidates(*build())
    row = result.snapshot()['considered'][0]
    owner = result.considered[0].assessment
    assert row['sessions'][0]['session']['available_at'] == owner.context.session.available_at.isoformat()
    assert row['sessions'][0]['instrument']['effective_from'] == owner.session_assessments[0].tradability.effective_from.isoformat()
    assert row['references'][0]['available_at'] == owner.reference_assessments[0].reference.available_at.isoformat()
    assert row['ticks'][0]['effective_until'] == owner.tick_assessments[0].rule.effective_until.isoformat()
    assert row['quote']['observation']['metadata']['event_at'] == owner.option_quote.meta.event_at.isoformat()
    source = {r['record_id']: r for r in result.snapshot()['source_records']}
    for component in result.context.components:
        if component.disposition != 'future':
            record = source[component.member.record_id]
            assert record['raw_hash'] == sha256(component.member.raw_body_bytes).hexdigest()
            assert record['envelope_hash'] == sha256(component.member.envelope_bytes).hexdigest()


@pytest.mark.parametrize('value', [Decimal('1e1001'), Decimal('1e-1001')])
def test_rank_bounds_precede_fraction_allocation(monkeypatch, value):
    contract = build(('c99',))[0].option_quotes[0].contract
    def forbidden(*args):
        raise AssertionError('unbounded value reached Fraction')
    monkeypatch.setattr(candidates, 'Fraction', forbidden)
    with pytest.raises(candidates._UnsupportedIdentity):
        candidates._selection_rank(value, Decimal('4.90'), Decimal('5.10'), contract)
    with pytest.raises(candidates._UnsupportedIdentity):
        candidates._selection_rank(Decimal('.50'), Decimal('4.90'), Decimal('5.10'), replace(contract, strike=value))


@pytest.mark.parametrize('delta', [Decimal('0.5000'), Decimal('0.5'+'0'*1100), Decimal('-0E+999999'), Decimal('1e-1000')])
def test_canonical_rank_leaves_and_exact_boundary(delta):
    contract = build(('c99',))[0].option_quotes[0].contract
    key, evidence = candidates._selection_rank(delta, Decimal('4.9000'), Decimal('5.1000'), contract)
    from fractions import Fraction
    assert key[0] == abs(abs(Fraction(delta))-Fraction(1, 2))
    assert evidence[2:] == (1, 25)
    assert max(abs(i).bit_length() for i in evidence) <= 13300


def test_local_arithmetic_failure_keeps_real_b_result_and_other_winner(monkeypatch):
    args = build()
    rank = candidates._selection_rank
    def bounded_failure(delta, bid, ask, contract):
        if contract.right == 'call' and contract.strike == 99:
            raise candidates._UnsupportedIdentity
        return rank(delta, bid, ask, contract)
    monkeypatch.setattr(candidates, '_selection_rank', bounded_failure)
    result = candidates.select_candidates(*args)
    row = next(r for r in result.considered if r.contract.right == 'call' and r.contract.strike == 99)
    assert row.assessment.eligible and row.disposition == 'ineligible' and row.rank_evidence is row.rank is None
    assert row.reasons[0] == 'input_integrity:selection:arithmetic_precision_unsupported'
    assert result.selected_call.contract.strike == 100 and result.content_hash is not None


def test_full_identity_failure_clears_both_winners_after_all_real_assessments(monkeypatch):
    args = build()
    assessed = []
    assess = candidates.assess_candidate
    def tracked(*args, **kwargs):
        result = assess(*args, **kwargs)
        assessed.append(result)
        return result
    def unsupported(context):
        raise candidates._UnsupportedIdentity
    monkeypatch.setattr(candidates, 'assess_candidate', tracked)
    monkeypatch.setattr(candidates, '_source_snapshot', unsupported)
    result = candidates.select_candidates(*args)
    assert len(assessed) == len(result.considered) == 8
    assert all(r.assessment is a for r, a in zip(result.considered, assessed))
    assert not result.selected and result.snapshot_bytes is result.content_hash is None
    assert result.selection_reason == 'arithmetic_precision_unsupported'
    assert all(r.disposition == 'selection_unavailable' and r.rank is not None for r in result.considered)


@pytest.mark.parametrize('changes', [dict(connection='disconnected'), dict(as_of=NOW-timedelta(seconds=6)),
    dict(reconciled_at=NOW-timedelta(seconds=6)), dict(reconciliation_id=None)])
def test_shared_account_failure_governs_before_per_contract_policy(changes):
    context, account, config = build()
    result = candidates.select_candidates(context, replace(account, **changes), config)
    assert result.selection_reason == 'account_unavailable' and not result.selected
    assert result.account_assessment is not None and result.considered


def test_original_contract_revalidation_never_invokes_selection(monkeypatch):
    original = select_b()
    frozen = original.snapshot_bytes
    current, config = build_b('other-bad')
    account = next(c.value for c in current.components if c.member.record_id == 'other-bad-account_snapshot')
    def forbidden(*args, **kwargs):
        raise AssertionError('fresh original-contract check reranked')
    monkeypatch.setattr(candidates, 'select_candidates', forbidden)
    result = candidates.assess_candidate(original.selected_call.contract, current.context, account, config,
        now=NOW, original_ask_cap=original.selected_call.original_ask_cap)
    assert not result.eligible and 'option_quote:quote:quote_missing' in result.reasons
    assert original.snapshot_bytes == frozen


@pytest.mark.parametrize('error', [ValueError('unexpected failure'), TypeError('unexpected type')])
def test_unexpected_identity_error_escapes_even_with_missing_context(monkeypatch, error):
    _, account, config = build(())
    def broken(account):
        raise error
    monkeypatch.setattr(candidates, 'account_snapshot', broken)
    with pytest.raises(type(error), match=str(error)):
        candidates.select_candidates(None, account, config)


def test_rule_identity_binds_fresh_definition_separately_from_display_token():
    result = candidates.select_candidates(*build())
    definition = candidates._selection_definition_snapshot()
    encoded = json.dumps(definition, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    assert sha256(encoded).hexdigest() == candidates.SELECTION_RULE_ID == result.selection_rule_id
    assert result.selection_rule_id != result.config.selection_rule
    definition['target'][0] = 99
    assert candidates._selection_definition_snapshot()['target'] == [1, 2]


def test_requested_mark_aliases_remain_audit_and_occupied_account_stays_visible():
    extras = ('identical-b-representative',)
    result = select_b('identical', extras=extras, omit=('identical-option_quote',))
    assert result.selection_reason == 'account_exposure' and not result.selected
    assert len(result.considered) == 1
    assert result.account_assessment.holding_marks[0].gross_liquidation_value == Decimal('500')
    assert result.snapshot()['mark_records'] == [['identical-a-unrequested', 'identical-b-representative']]
    assert result.snapshot()['account_assessment']['holding_marks'][0]['valuation_basis'] == 'observed_bid'
    assert 'source_admission:account:mark_source_unavailable' not in result.reasons


@pytest.mark.parametrize('index', [0, 1, 2])
def test_exact_trusted_argument_types_reject_before_owner_methods(index):
    args = list(build())
    args[index] = object()
    with pytest.raises(TypeError):
        candidates.select_candidates(*args)


def test_features_are_not_consulted_and_reversed_full_request_is_identical(monkeypatch):
    import options_lab.feature_vector as feature_vector
    args = build()
    def forbidden(*args, **kwargs):
        raise AssertionError('selection reached feature assembly')
    monkeypatch.setattr(feature_vector, 'build_features', forbidden)
    initial = candidates.select_candidates(*args)
    reversed_ids = [c.member.record_id for c in args[0].components if c.requested][::-1]
    reversed_result = candidates.select_candidates(*build(ids=reversed_ids))
    assert initial.selected and initial.content_hash == reversed_result.content_hash


def test_unsupported_contract_is_assessed_once_and_retained_after_supported_inventory(monkeypatch):
    args = build()
    target = next(q.contract for q in args[0].option_quotes if q.contract.right == 'call' and q.contract.strike == 99)
    snapshot, assess, calls = candidates._contract_snapshot, candidates.assess_candidate, []
    def unsupported(contract):
        if contract == target:
            raise candidates._UnsupportedIdentity
        return snapshot(contract)
    def tracked(*args, **kwargs):
        assessment = assess(*args, **kwargs)
        calls.append(assessment)
        return assessment
    monkeypatch.setattr(candidates, '_contract_snapshot', unsupported)
    monkeypatch.setattr(candidates, 'assess_candidate', tracked)
    result = candidates.select_candidates(*args)
    assert len(calls) == len(result.considered) == 8 and sum(a.contract == target for a in calls) == 1
    last = result.considered[-1]
    assert last.contract == target and last.assessment is calls[-1] and last.assessment.eligible
    assert last.rank is last.rank_evidence is None and last.disposition == 'ineligible'
    assert last.reasons == ('input_integrity:selection:arithmetic_precision_unsupported',)
    assert not result.selected and result.snapshot_bytes is result.content_hash is None
    assert all(r.disposition == 'selection_unavailable' for r in result.considered[:-1])


def test_proven_future_failure_cannot_change_causal_selection_hash():
    from test_account_context import build as old_build, MARKET
    results = []
    for extras in ((), ('future-B',)):
        built = old_build([*MARKET, 'future-A', *extras])
        account = next(c.value for c in built.components if c.member.record_id == 'future-A')
        results.append(candidates.select_candidates(built.context, account, StrategyConfig()))
    assert results[0].context_input_digest == results[1].context_input_digest
    assert results[0].content_hash == results[1].content_hash
    assert any(c.disposition == 'future' for c in results[1].context.components)
    assert all(r['record_id'] != 'future-B' for r in results[1].snapshot()['source_records'])


def test_no_chain_retains_actual_offgrid_and_malformed_header_precedence():
    from test_candidates import KINDS as B_KINDS
    result = select_b('fresh', at=NOW+timedelta(seconds=2),
        omit=tuple('fresh-'+kind for kind in B_KINDS if kind != 'account_snapshot'))
    assert result.considered == () and result.selection_reason == 'decision_off_grid'
    assert result.initial_timing.reasons[0] == 'decision_off_grid'
    request = normalize_context_request(dict(decision_id='decision', decision_at=NOW.isoformat(),
        member_record_ids=['missing-member', False]), event_id='request', raw_ref='request', received_at=VERIFIED)
    context = build_decision_context(request, None, config=StrategyConfig(), previous_feature_state=None).context
    malformed = candidates.select_candidates(context, None, StrategyConfig())
    assert malformed.selection_reason == 'context_invalid' and malformed.considered == ()
    assert 'input_integrity:context:invalid_type' in malformed.reasons


def test_actual_1400_candidate_does_not_require_feature_warmup():
    early = ('option_quote', 'underlying_quote', 'account_snapshot', 'greek_observation', 'quote_coherence', 'tick_rule')
    ids = ['session', *('early-'+kind for kind in early),
           *('c99-'+kind for kind in ('instrument_tradability', 'provider_contract_mapping', 'contract_reference'))]
    result = candidates.select_candidates(*build(ids=ids, at=NOW-timedelta(minutes=5)))
    assert result.selected_call.contract.strike == 99 and result.selection_reason is None
    assert result.as_of.hour == 14 and result.as_of.minute == 0
    assert result.context.feature_state is None and result.context.bars == ()
