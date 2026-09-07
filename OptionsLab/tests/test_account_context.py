"""Exercise account source identity and finite closure through registered bytes."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

import options_lab.admission as admission
from options_lab.account import AccountSnapshot, assess_account, assess_holding_mark
from options_lab.account_inputs import AccountInputRejection, account_hash
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.observations import InputRejection
from options_lab.quote_inputs import QuoteInputRejection

FIXTURE_ID = 'p12c-account-context-v1'
PATH = Path(__file__).parent / 'fixtures' / (FIXTURE_ID+'.json')
NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
VERIFIED = datetime(2026, 9, 7, tzinfo=timezone.utc)
MARKET = ['session', 'good-option_quote', 'good-underlying_quote', 'good-greek', 'good-proof',
          'good-instrument_tradability', 'good-provider_contract_mapping', 'good-contract_reference']
UNITS = ('money', 'quantity', 'basis_debit', 'virtual_cash', 'virtual_settled_cash', 'virtual_reserved_cash',
         'broker_settled_cash', 'broker_available_cash', 'broker_nonmargin_buying_power', 'session_realized_pnl',
         'estimated_remaining_close_cost', 'applicable_round_trip_fees')


def verify(payload=None):
    """Admit real catalog-bound bytes with a distinct outer verification event."""
    return admission.verify_fixture_bundle(FIXTURE_ID, PATH.read_bytes() if payload is None else payload,
                                          event_id='verify-account', raw_ref='verification', received_at=VERIFIED)


def build(ids, *, at=NOW):
    """Run the public normalized request and actual finite-manifest assembler."""
    result = verify()
    assert result.rejection is None, result.rejection
    request = normalize_context_request(dict(decision_id='decision', decision_at=at.isoformat(), member_record_ids=ids),
                                        event_id='request', raw_ref='request', received_at=VERIFIED)
    return build_decision_context(request, result.value, config=StrategyConfig(), previous_feature_state=None)


def component(result, name):
    """Locate an actual retained occurrence, not a supplied selected-value wrapper."""
    return next(c for c in result.components if c.member.record_id == name)


def selected_accounts(result):
    """Read accounts from the existing selected-component consumer seam."""
    return tuple(c for c in result.context.selected_components if type(c.value) is AccountSnapshot)


def test_actual_account_and_independent_mark_share_one_manifest_and_envelopes():
    result = build(MARKET+['occupied'])
    account = component(result, 'occupied')
    quote = component(result, 'good-option_quote')
    assert selected_accounts(result) == (account,)
    assert account.value.event_id == 'event-occupied' and account.value.provider_record_id == 'report-occupied'
    assert account.value.received_at == NOW and account.value.raw_ref == 'synthetic://p12c/occupied'
    assert account.value.holdings[0].mark_quote == quote.value
    assert account.member.decode_raw_body()['holdings'][0]['mark_quote']['envelope'] == quote.member.decode_envelope()
    assert account.member in result.context.manifest.members and quote.member in result.context.manifest.members
    assert result.context.input_manifest_id == (FIXTURE_ID, result.manifest.payload_sha256)
    assert result.context.input_digest and result.context.input_reasons == ()
    assert result.context.tick_rules == () and not result.context.operational_allowed and not result.context.economic_allowed
    assert (result.context.origin, result.context.fidelity_tier, result.context.permitted_use) == ('synthetic', 0, 'core_fixture')
    assessment = assess_account(account.value, session=result.context.session, config=StrategyConfig(), now=NOW)
    assert assessment.computed_liquidation_equity == Decimal('99988')
    with pytest.raises(FrozenInstanceError):
        account.value.virtual_cash = Decimal(0)


def test_request_does_not_invent_account_or_independent_mark_membership():
    assert selected_accounts(build(MARKET)) == ()
    result = build(['occupied', 'session'])
    assert len(selected_accounts(result)) == 1 and result.context.option_quotes == ()
    assert {c.member.record_id for c in result.components} == {'occupied', 'session'}
    flat = build(['flat', 'session'])
    assert assess_account(selected_accounts(flat)[0].value, session=flat.context.session,
                          config=StrategyConfig(), now=NOW).observed_flat


def test_selected_adverse_account_keeps_every_signed_unknown_and_terminal_fact():
    result = build(['adverse', 'session'])
    account = selected_accounts(result)[0].value
    assert tuple(h.quantity for h in account.holdings) == (Decimal('-1'), Decimal('0.5'), Decimal(0), None, Decimal(100), Decimal(1))
    assert tuple(o.status for o in account.open_orders) == ('cancel_pending', 'terminal')
    assert account.holdings[4].asset_kind == 'equity' and account.holdings[4].contract is None
    assert account.holdings[5].quantity_unit == 'unknown'
    assessment = assess_account(account, session=result.context.session, config=StrategyConfig(), now=NOW)
    assert not assessment.observed_flat and assessment.integrity_reasons and assessment.exposure_reasons
    assert len(assessment.holding_marks) == 6 and assessment.computed_liquidation_equity is None


@pytest.mark.parametrize('name', ['nested-event', 'nested-stream', 'nested-sequence', 'nested-link',
                                 'nested-receipt', 'nested-ref', 'nested-order', 'adverse-conflict'])
def test_same_account_event_cannot_change_nested_claims_or_hide_adverse_records(name):
    result = build([name+'-A', name+'-B'])
    assert selected_accounts(result) == ()
    a, b = (component(result, name+'-'+label) for label in ('A', 'B'))
    assert all('source_identity_conflict' in c.reasons and c.value is not None for c in (a, b))
    assert result.context.input_digest and a.member.raw_body_bytes != b.member.raw_body_bytes
    if name in ('nested-event', 'nested-stream', 'nested-sequence', 'nested-link', 'nested-order'):
        assert replace(a.value, raw_ref=b.value.raw_ref) == b.value
        assert account_hash(replace(a.value, raw_ref=b.value.raw_ref)) == account_hash(b.value)
    if name == 'adverse-conflict':
        assert len(a.value.holdings) == len(b.value.holdings) == 6
        assert a.value.open_orders == b.value.open_orders and a.value.open_orders[0].execution_uncertain


@pytest.mark.parametrize('name', ['transport', 'semantic'])
@pytest.mark.parametrize('labels', [('A', 'B'), ('B',)])
def test_exact_source_redelivery_uses_requested_occurrence_and_semantic_equality(name, labels):
    result = build([name+'-'+label for label in labels])
    chosen = component(result, name+'-'+min(labels))
    assert selected_accounts(result) == (chosen,)
    assert chosen.value.raw_ref == 'synthetic://p12c/'+chosen.member.record_id
    assert chosen.value.received_at == datetime.fromisoformat(chosen.member.decode_envelope()['simulated_received_at'])
    assert 'source_identity_conflict' not in result.context.input_reasons
    other = component(result, name+'-'+('B' if min(labels) == 'A' else 'A'))
    assert other.disposition == 'duplicate'
    if name == 'transport':
        assert account_hash(chosen.value) != account_hash(other.value)
    else:
        assert chosen.member.raw_hash != other.member.raw_hash
        assert replace(chosen.value, raw_ref=other.value.raw_ref) == other.value


@pytest.mark.parametrize('labels', [('B',), ('A', 'B')])
def test_explicit_account_update_reuses_report_id_and_retires_changed_session_and_revision(labels):
    result = build(['update-'+label for label in labels]+['session'])
    a, b = component(result, 'update-A'), component(result, 'update-B')
    assert selected_accounts(result) == (b,) and a.disposition == 'superseded'
    assert a.value.provider_record_id == b.value.provider_record_id
    assert a.value.event_id != b.value.event_id and b.value.ledger_revision == 'ledger-2'
    assert b.value.connection == 'disconnected' and b.value.session_date.isoformat() == '2026-09-03'
    assert not assess_account(b.value, session=result.context.session, config=StrategyConfig(), now=NOW).observed_flat


@pytest.mark.parametrize('name,reason', [('update', 'incomplete_context_input_set'), ('malformed', 'normalization_failed'),
    ('unknown-availability', 'availability_unknown'), ('target-unknown', 'target_unknown'),
    ('source-unknown', 'source_identity_unknown'), ('profile-mismatch', 'profile_mismatch'),
    ('tie', 'stream_order_ambiguous'), ('roots', 'current_roots_conflict'), ('fork', 'lineage_fork')])
@pytest.mark.parametrize('only_old', [True, False])
def test_known_adverse_or_omitted_successor_prevents_old_good_flat_fallback(name, reason, only_old):
    ids = [name+'-A'] if only_old else [name+'-A', name+'-B']+([name+'-C'] if name == 'fork' else [])
    result = build(ids)
    if name == 'update' and not only_old:
        assert selected_accounts(result) == (component(result, 'update-B'),)
        return
    assert selected_accounts(result) == () and reason in result.context.input_reasons
    assert component(result, name+'-A').value is not None
    b = component(result, name+'-B')
    assert b.requested is (not only_old) and b.member in result.manifest.members
    assert b.disposition == ('omitted' if only_old else 'unresolved')
    if name == 'unknown-availability':
        assert b.available_at is None and type(b.value) is AccountSnapshot
    assert not any(c.member.record_id == 'flat' for c in result.components)


@pytest.mark.parametrize('name,reason', [('orphan', 'lineage_invalid'), ('cross-stream', 'lineage_invalid'),
                                       ('cycle', 'lineage_cycle'), ('tie', 'lineage_unorderable')])
def test_invalid_actual_account_lineage_has_no_selected_substitute(name, reason):
    ids = ['orphan'] if name == 'orphan' else [name+'-A', name+'-B']
    result = build(ids)
    assert not selected_accounts(result) and reason in result.context.input_reasons
    assert all(c.value is not None for c in result.components)


def test_equal_availability_uses_explicit_same_stream_sequence():
    result = build(['ordered-B'])
    assert selected_accounts(result) == (component(result, 'ordered-B'),)
    assert component(result, 'ordered-A').disposition == 'superseded'


def test_future_malformed_successor_is_not_backdated_or_included_in_causal_digest():
    old = build(['future-A'])
    requested = build(['future-A', 'future-B'])
    assert selected_accounts(old) == (component(old, 'future-A'),)
    assert component(requested, 'future-B').disposition == 'future'
    assert old.context.input_digest == requested.context.input_digest
    assert not any(type(r) is AccountInputRejection for r in requested.context.rejections)
    later = build(['future-A'], at=NOW+timedelta(microseconds=1))
    assert not selected_accounts(later) and 'normalization_failed' in later.context.input_reasons


@pytest.mark.parametrize('name,owner,field', [('malformed-B', InputRejection, 'holdings[0].mark_quote.envelope.metadata'),
                                           ('quote-invalid', QuoteInputRejection, 'holdings[0].mark_quote.raw_body')])
def test_native_nested_failure_retains_both_actual_envelopes_and_diagnostics(name, owner, field):
    result = build([name])
    row = component(result, name)
    failure = next(result.rejections[i] for i in row.rejection_indexes if type(result.rejections[i]) is AccountInputRejection)
    assert (failure.event_id, failure.raw_ref, failure.received_at) == ('event-'+name, 'synthetic://p12c/'+name, NOW)
    assert (failure.field, failure.code, failure.stage) == (field, 'nested_normalization_failed', 'account_normalization')
    nested = failure.nested_rejection
    assert type(nested) is owner and nested.event_id == 'event-good-option_quote'
    assert nested.raw_ref == 'synthetic://p11/good-option_quote' and nested.received_at == NOW-timedelta(seconds=1)
    if owner is InputRejection:
        assert [(d.field, d.code) for d in nested.diagnostics] == [('source', 'invalid_value'), ('available_at', 'invalid_timestamp')]
    else:
        assert (nested.field, nested.code) == ('bid', 'invalid_value')
    assert row.value is None and 'nested_normalization_failed' in result.context.input_reasons


def test_account_cannot_silently_refresh_an_omitted_independent_mark_successor():
    result = build(['mark-omission', 'mark-update-A', 'session'])
    account = selected_accounts(result)[0].value
    assert account.holdings[0].mark_quote.bid == Decimal('4.90')
    assert not result.context.option_quotes and component(result, 'mark-update-B').disposition == 'omitted'
    assert 'incomplete_context_input_set' in result.context.input_reasons
    current = build(['mark-omission', 'mark-update-B', 'session'])
    assert current.context.option_quotes[0].bid == Decimal('4.80')
    assert selected_accounts(current)[0].value.holdings[0].mark_quote != current.context.option_quotes[0]


def test_bid_only_actual_source_stays_measurable_despite_entry_quote_reasons():
    result = build(['bid-only', 'mark-bid-only', 'session'])
    account = selected_accounts(result)[0].value
    assert account.holdings[0].mark_quote == result.context.option_quotes[0]
    assert component(result, 'mark-bid-only').reasons
    assert assess_holding_mark(account.holdings[0], config=StrategyConfig(), now=NOW).gross_liquidation_value == Decimal('490')


def test_assessment_clocks_and_huge_identity_failure_do_not_change_source_selection():
    for delta, stale in [(timedelta(seconds=5), False), (timedelta(seconds=5, microseconds=1), True)]:
        result = build(['occupied', 'session'], at=NOW+delta)
        assessment = assess_account(selected_accounts(result)[0].value, session=result.context.session,
                                    config=StrategyConfig(), now=NOW+delta)
        assert ('account_too_old' in assessment.time_reasons) is stale
        assert ('reconciliation_too_old' in assessment.time_reasons) is stale
        assert assessment.holding_marks[0].gross_liquidation_value == 0
    result = build(['huge'])
    assert result.context.input_digest and selected_accounts(result)[0].value.virtual_cash == Decimal('1e1000')
    with pytest.raises(ValueError, match='^account_identity_representation_unsupported$'):
        account_hash(selected_accounts(result)[0].value)


@pytest.mark.parametrize('domain', ['body', 'nested', 'envelope'])
def test_actual_catalog_commitment_rejects_tampering_even_with_fresh_body_hash(domain):
    raw = json.loads(PATH.read_bytes())
    row = next(m for m in raw['members'] if m['record_id'] == 'occupied')
    if domain == 'body':
        row['raw_body']['virtual_cash'] = '0'
    elif domain == 'nested':
        row['raw_body']['holdings'][0]['mark_quote']['envelope']['event_id'] = 'changed'
    else:
        row['envelope']['event_id'] = 'changed'
    row['raw_hash'] = hashlib.sha256(json.dumps(row['raw_body'], sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    rejection = verify(json.dumps(raw).encode()).rejection
    assert (rejection.field, rejection.code) == ('payload', 'hash_mismatch')


@pytest.mark.parametrize('key', UNITS)
@pytest.mark.parametrize('change', ['missing', 'wrong'])
def test_account_units_emit_closed_constructor_compatible_schema_diagnostics(key, change):
    raw = json.loads(PATH.read_bytes())
    index = next(i for i, p in enumerate(raw['modeled_source_profiles']) if p['kind'] == 'account_snapshot')
    units = raw['modeled_source_profiles'][index]['units']
    units.pop(key) if change == 'missing' else units.update({key: 'wrong-netness'})
    expected = (f'modeled_source_profiles[{index}].units'+('.'+key if change == 'missing' else ''),
                'missing' if change == 'missing' else 'invalid_value')
    assert schema_failure(raw) == expected


def schema_failure(raw):
    """Exercise framing only, never manufacture admitted source authority."""
    descriptor = next(d for d in admission._CATALOG.descriptors if d.fixture_id == FIXTURE_ID)
    with pytest.raises(admission._AdmissionFailure) as failure:
        admission._build_manifest(descriptor, raw, PATH.read_bytes(), descriptor.expected_payload_sha256, 'event', 'raw', VERIFIED)
    field, code = failure.value.args
    rejection = admission.FixtureInputRejection('event', VERIFIED, 'raw', field, code)
    return rejection.field, rejection.code


@pytest.mark.parametrize('field,value', [('feed_class', 'realtime'), ('fidelity', 'genuine'),
    ('record_identity_rule', 'new_provider_record_id_per_update'), ('units', {'unexpected': 'USD'}),
    ('contract', {}), ('metadata', {})])
def test_account_profile_and_body_owned_envelope_are_closed(field, value):
    raw = json.loads(PATH.read_bytes())
    if field in ('contract', 'metadata'):
        index = next(i for i, m in enumerate(raw['members']) if m['kind'] == 'account_snapshot')
        raw['members'][index]['envelope'][field] = value
        path = f'members[{index}].envelope.{field}'
    else:
        index = next(i for i, p in enumerate(raw['modeled_source_profiles']) if p['kind'] == 'account_snapshot')
        raw['modeled_source_profiles'][index][field] = value
        path = f'modeled_source_profiles[{index}].{field}'
    assert schema_failure(raw) == (path, 'unknown_fields' if field == 'units' else 'invalid_value')


def test_assumed_account_availability_cannot_match_actual_measured_profile():
    result = build(['basis-mismatch'])
    assert [c.member.record_id for c in result.components if type(c.value) is AccountSnapshot] == ['basis-mismatch']
    row = component(result, 'basis-mismatch')
    assert row.value.availability_basis == 'assumed' and row.value.source == 'fixture-account-ledger'
    assert row.disposition == 'unresolved' and 'profile_mismatch' in row.reasons
    assert not selected_accounts(result)
