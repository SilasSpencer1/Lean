"""Risk ingress uses admitted account and market facts without granting entry permission."""

from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal
import json
from pathlib import Path

import pytest

from options_lab.admission import verify_fixture_bundle
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.risk_inputs import normalize_risk_control, observe_risk_inputs


NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
VERIFIED = datetime(2026, 9, 7, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / 'fixtures/p12c-account-context-v1.json'


def context(*record_ids):
    manifest = verify_fixture_bundle('p12c-account-context-v1', FIXTURE.read_bytes(),
                                     event_id='risk-verify', raw_ref='risk-test', received_at=VERIFIED)
    assert manifest.value is not None, manifest.rejection
    request = normalize_context_request(dict(decision_id='risk-decision', decision_at=NOW.isoformat(),
                                             member_record_ids=list(record_ids)), event_id='risk-request',
                                        raw_ref='risk-test', received_at=VERIFIED)
    result = build_decision_context(request, manifest.value, config=StrategyConfig(), previous_feature_state=None)
    assert result.context is not None
    return result.context


def control(body, *, event_id='operator-1'):
    return normalize_risk_control(json.dumps(body).encode(), event_id=event_id,
                                  raw_ref='local://operator', received_at=NOW)


BASE = dict(schema_version=1, account_id='account-flat', source='fixture-account-ledger',
            actor_id='operator-1', occurred_at=NOW.isoformat(), expected_risk_revision='risk-local-1')


def test_observation_retains_real_flat_account_and_separate_source_evidence():
    result = observe_risk_inputs(context('flat', 'session'), config=StrategyConfig(), now=NOW)
    assert result.context_recheck.valid
    assert result.account.event_id == 'event-flat'
    assert result.account_assessment.observed_flat
    assert result.account_assessment.conservative_virtual_equity == Decimal('100000')
    assert len(result.account_components) == 1
    assert result.mark_components == ()
    assert result.mark_source_reasons == ()
    assert result.bundle_assessment is None


def test_observation_keeps_independent_adverse_money_when_mark_source_is_missing():
    result = observe_risk_inputs(context('occupied', 'session'), config=StrategyConfig(), now=NOW)
    assert result.account.holdings[0].quantity == 1
    assert result.account_assessment.unrealized_pnl == Decimal('-11')
    assert result.account_assessment.conservative_daily_pnl == Decimal('-12')
    assert 'source_admission:account:mark_source_unavailable' in result.mark_source_reasons
    assert not result.account_assessment.observed_flat


def test_actual_mark_source_can_bind_without_requiring_entry_readiness():
    result = observe_risk_inputs(context('occupied', 'session', 'good-option_quote'),
                                 config=StrategyConfig(), now=NOW)
    assert result.account_assessment.conservative_daily_pnl == Decimal('-12')
    assert result.mark_source_reasons == ()
    assert len(result.mark_components) == 1 and result.mark_components[0]
    assert result.account_assessment.has_nonzero_holding


def test_recovery_account_claims_remain_visible_without_fabricating_capital():
    result = observe_risk_inputs(context('adverse', 'session'), config=StrategyConfig(), now=NOW)
    assert result.account is not None
    assert len(result.account.holdings) == 6
    assert result.account_assessment.conservative_virtual_equity is None
    assert result.account_assessment.exposure_reasons
    assert result.account_assessment.reconciliation_reasons


def test_missing_account_is_explicit_unknown():
    result = observe_risk_inputs(context('session'), config=StrategyConfig(), now=NOW)
    assert result.account is None
    assert result.account_assessment is not None
    assert 'source_admission:account:account_source_missing' in result.account_evidence_reasons
    assert not result.account_assessment.observed_flat


def test_competing_account_source_members_do_not_select_favorable_claim():
    result = observe_risk_inputs(context('tie-A', 'tie-B', 'session'),
                                 config=StrategyConfig(), now=NOW)
    assert result.account is None
    assert len(result.account_components) == 2
    assert result.account_assessment is not None and not result.account_assessment.observed_flat
    assert result.account_evidence_reasons


def test_control_normalizes_local_request_without_success_authority():
    result = control(BASE | dict(kind='halt', reason='manual_halt'))
    assert result.rejection is None
    assert result.value.kind == 'halt'
    assert result.value.reason == 'manual_halt'
    assert result.value.expected_risk_revision == 'risk-local-1'
    assert result.value.raw_bytes
    assert not hasattr(result.value, 'approved')


def test_reset_and_cashflow_are_distinct_requests_with_canonical_content_identity():
    reset = BASE | dict(kind='reset', reason='drawdown')
    first = control(reset).value
    later = normalize_risk_control(json.dumps(reset | dict(occurred_at='2026-09-04T10:05:00-04:00'),
                                              separators=(',', ':')).encode(), event_id='operator-1',
                                   raw_ref='local://redelivery', received_at=NOW + timedelta(seconds=1)).value
    assert first is not None and later is not None
    assert first.content_hash == later.content_hash
    assert first.raw_bytes != later.raw_bytes and first.received_at != later.received_at
    flow = BASE | dict(kind='cashflow', previous_account_event_id='account-before',
                       current_account_event_id='account-after', ledger_before='ledger-1',
                       ledger_after='ledger-2', amount='12.50')
    a = control(flow).value
    b = control(flow | dict(amount='12.5')).value
    assert a is not None and b is not None
    assert a.amount == Decimal('12.50') and a.reason is None
    assert a.content_hash == b.content_hash
    assert control(flow | dict(amount='13')).value.content_hash != a.content_hash


@pytest.mark.parametrize('body,field', [
    (BASE | dict(kind='reset', reason='broker_disconnected'), 'reason'),
    (BASE | dict(kind='halt', reason='drawdown'), 'reason'),
    (BASE | dict(kind='halt', reason='manual_halt', amount='1'), '$'),
    (BASE | dict(kind='halt'), 'reason'),
    (BASE | dict(kind='halt', reason='manual_halt', schema_version=True), 'schema_version'),
    (BASE | dict(kind='cashflow', previous_account_event_id='a', current_account_event_id='b',
                 ledger_before='l1', ledger_after='l2', amount=True), 'amount'),
    (BASE | dict(kind='cashflow', previous_account_event_id='a', current_account_event_id='b',
                 ledger_before='l1', ledger_after='l2', amount='1' * 1001), 'amount'),
    (BASE | dict(kind='halt', reason='manual_halt', occurred_at='2026-09-04T14:05:00'), 'occurred_at'),
])
def test_control_rejects_unproven_or_malformed_claims(body, field):
    result = control(body)
    assert result.value is None and result.rejection.field == field


def test_unknown_control_key_is_never_echoed_in_rejection():
    attacker_key = 'attacker-controlled-' + 'x' * 4000
    result = control(BASE | dict(kind='halt', reason='manual_halt', **{attacker_key: '1'}))
    assert result.value is None
    assert result.rejection.field == '$'
    assert result.rejection.code == 'unknown_field'
    assert attacker_key not in (result.rejection.field, result.rejection.code)


@pytest.mark.parametrize('raw', [
    b'{"schema_version":1,"schema_version":1}',
    b'\xef\xbb\xbf{}',
    b'\xff',
    b'{"amount":NaN}',
    b'{"schema_version":' + b'9' * 5000 + b'}',
    b' ' * 8193,
], ids=['duplicate', 'bom', 'invalid-utf8', 'nonfinite', 'huge-int', 'oversize'])
def test_control_rejects_ambiguous_or_oversized_json(raw):
    result = normalize_risk_control(raw, event_id='bad', raw_ref='local://bad', received_at=NOW)
    assert result.value is None
    assert result.rejection is not None


def test_wrong_top_level_types_raise_type_error():
    with pytest.raises(TypeError):
        observe_risk_inputs(object(), config=StrategyConfig(), now=NOW)
    with pytest.raises(TypeError):
        normalize_risk_control({}, event_id='bad', raw_ref='local://bad', received_at=NOW)


def test_trusted_control_receipt_identifiers_are_bounded():
    raw = json.dumps(BASE | dict(kind='halt', reason='manual_halt')).encode()
    with pytest.raises(ValueError, match='event_id'):
        normalize_risk_control(raw, event_id='e' * 257, raw_ref='local://operator', received_at=NOW)


def test_invalid_retained_context_is_bounded_before_account_or_bundle_hooks():
    original = context('flat', 'session')
    object.__delattr__(original, 'decision_at')
    result = observe_risk_inputs(original, config=StrategyConfig(), now=NOW)
    assert result.context is None and result.account is None
    assert result.context_reasons == ('retained_context_mismatch',)
    assert result.account_assessment is None


def test_mismatched_current_time_and_config_do_not_reuse_retained_account():
    original = context('flat', 'session')
    late = observe_risk_inputs(original, config=StrategyConfig(), now=NOW + timedelta(seconds=1))
    assert late.context is None and late.account is None
    assert 'context_time_mismatch' in late.context_reasons
    changed = observe_risk_inputs(original, config=StrategyConfig(max_entries_per_session=2), now=NOW)
    assert changed.context is None and changed.account is None
    assert changed.context_reasons


def test_hostile_timezone_is_rejected_before_its_methods_run():
    class HostileZone(tzinfo):
        def utcoffset(self, _dt):
            raise AssertionError('timezone hook invoked')

        def dst(self, _dt):
            raise AssertionError('timezone hook invoked')

    with pytest.raises(ValueError, match='standard aware'):
        observe_risk_inputs(context('flat', 'session'), config=StrategyConfig(),
                            now=NOW.replace(tzinfo=HostileZone()))


def test_actual_cash_and_model_bundle_are_rechecked_without_model_absence_failure():
    from test_decision import bundle, source

    model = bundle('call')
    market, _, _ = source(model.normalization_result.value)
    fixed = observe_risk_inputs(market, config=StrategyConfig(), now=NOW, bundle=model)
    assert fixed.bundle_assessment.available, fixed.bundle_reasons
    assert fixed.account is not None and fixed.account_assessment is not None
    cash = observe_risk_inputs(market, config=StrategyConfig(), now=NOW, bundle=bundle('cash'))
    assert cash.bundle_assessment.available, cash.bundle_reasons
    assert cash.bundle_assessment.calibration == ()
    assert observe_risk_inputs(market, config=StrategyConfig(), now=NOW).bundle_reasons == ()


def test_damaged_cash_bundle_is_rejected_by_its_actual_owner():
    from test_decision import bundle, source

    market, _, _ = source()
    cash = bundle('cash')
    object.__delattr__(cash, 'original_fixture')
    result = observe_risk_inputs(market, config=StrategyConfig(), now=NOW, bundle=cash)
    assert result.bundle_assessment is not None
    assert not result.bundle_assessment.available
    assert 'retained_content_mismatch' in result.bundle_reasons
    assert result.account is not None
