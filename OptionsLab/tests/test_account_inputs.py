"""Raw account facts and bounded identities never imply source admission."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, localcontext
import hashlib
import json
import sys

import pytest

from options_lab.account import AccountSnapshot, HoldingFact, OpenOrderFact
import options_lab.account_inputs as account_inputs
from options_lab.observations import InputRejection
from options_lab.quote_inputs import QuoteInputRejection
from test_account import account, assess, holding, order, mark

NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
AT = NOW.isoformat()
D = Decimal


@pytest.fixture(scope='module')
def api():
    return account_inputs


def raw():
    contract = dict(underlying='SPY', expiry='2026-09-18', right='call', strike='650',
                    multiplier=100, deliverable_id='opaque-deliverable')
    metadata = dict(source='quote-source', provider_record_id='quote-report', feed_class='realtime',
                    fidelity='genuine', kind='quote', event_at=AT, available_at=AT,
                    availability_basis='measured', availability_evidence_ref='quote-clock',
                    interval_start=None, interval_end=None, is_fill_forward=False, quality_flags=[])
    quote = dict(raw_body=dict(bid='4.5', ask=None, bid_at=AT, ask_at=None, bid_size=1, ask_size=None),
                 envelope=dict(event_id='mark-event', raw_ref='mark-raw', simulated_received_at=AT,
                               stream_id='mark-stream', receive_sequence=3, supersedes_record_id=None,
                               contract=deepcopy(contract), metadata=metadata))
    held = dict(position_id='p', instrument_ref='i', contract=contract, asset_kind='option',
                quantity_unit='contracts', quantity='-0.5', basis_debit='500', entry_filled_at=None,
                entry_client_order_id=None, mark_quote=quote, estimated_remaining_close_cost='1',
                source='holding-source', provider_record_id='holding-report', raw_ref='holding-raw')
    pending = dict(order_ref='o', client_order_id=None, instrument_ref='i', contract=None, side='buy',
                   role='exit', status='cancel_pending', remaining_quantity='-1', cumulative_filled_quantity='0.5',
                   reserved_cash='-2', execution_uncertain=True, source='order-source',
                   provider_record_id='order-report', raw_ref='order-raw')
    return dict(account_id='a', currency='USD', source='account-source', provider_record_id='report',
                available_at=AT, availability_basis='measured', as_of=AT, reconciled_at=AT,
                session_date='2026-09-04', ledger_revision='l', reconciled_ledger_revision='l',
                reconciliation_id='r', risk_state_revision=None, halt_checkpoint_ref=None,
                connection='connected', holdings_completeness='complete', orders_completeness='complete',
                virtual_cash='99499', virtual_equity=None, session_start_equity='100000', high_water_mark='100000',
                session_realized_pnl='-1', reported_marked_pnl=None, virtual_settled_cash='99499',
                virtual_reserved_cash='0', broker_settled_cash=None, broker_available_cash=None,
                broker_nonmargin_buying_power=None, applicable_round_trip_fees=None, holdings=[held], open_orders=[pending])


def normalize(api, body, **envelope):
    return api.normalize_account(body, **(dict(event_id='account-event', raw_ref='account-raw', received_at=NOW) | envelope))


def change(body, path, value):
    parts = path.split('.')
    target = body
    for part in parts[:-1]:
        target = target[int(part)] if type(target) is list else target[part]
    target[parts[-1]] = value
    return body


def failure(api, body, path, code):
    result = normalize(api, body)
    assert result.value is None
    rejection = result.rejection
    assert (rejection.field, rejection.code) == (path, code)
    assert (rejection.event_id, rejection.received_at, rejection.raw_ref) == ('account-event', NOW, 'account-raw')
    assert rejection.stage == 'account_normalization' and rejection.reasons == (code,)
    assert replace(rejection) == rejection
    return rejection


def test_complete_facts_and_independent_mark_envelope(api):
    body = raw()
    value = normalize(api, body).value
    assert type(value) is AccountSnapshot and type(value.holdings[0]) is HoldingFact
    assert type(value.open_orders[0]) is OpenOrderFact
    assert (value.event_id, value.provider_record_id, value.received_at) == ('account-event', 'report', NOW)
    assert (value.virtual_cash, value.virtual_equity, value.session_realized_pnl) == (D('99499'), None, D('-1'))
    held, pending = value.holdings[0], value.open_orders[0]
    assert (held.quantity, held.basis_debit, held.entry_filled_at, held.raw_ref) == (D('-0.5'), D('500'), None, 'holding-raw')
    assert (pending.remaining_quantity, pending.cumulative_filled_quantity, pending.reserved_cash) == (D('-1'), D('.5'), D('-2'))
    assert pending.execution_uncertain and pending.status == 'cancel_pending' and pending.role == 'exit'
    assert (held.mark_quote.meta.source, held.mark_quote.meta.raw_ref, held.mark_quote.meta.received_at) == ('quote-source', 'mark-raw', NOW)
    assert held.mark_quote.ask is None and not assess(value).observed_flat
    body['holdings'][0]['quantity'] = '99'
    assert held.quantity == D('-.5')


@pytest.mark.parametrize('section', ['', 'holdings.0', 'open_orders.0', 'holdings.0.mark_quote', 'holdings.0.mark_quote.envelope'])
def test_all_body_keys_required_and_unknown_keys_rejected(api, section):
    body = raw()
    target = body
    for part in section.split('.') if section else []:
        target = target[int(part)] if type(target) is list else target[part]
    for key in tuple(target):
        removed = target.pop(key)
        prefix = section.replace('.0', '[0]')
        failure(api, body, f'{prefix}.{key}' if prefix else key, 'missing')
        target[key] = removed
    target['unexpected'] = 'secret'
    failure(api, body, section.replace('.0', '[0]') or '$', 'unknown_fields')


@pytest.mark.parametrize('bad', [True, 1, 1.0, D('1'), '', 'NaN', 'Infinity', '1e6', '１２', ' 1'])
@pytest.mark.parametrize('path', ['virtual_cash', 'holdings.0.quantity', 'open_orders.0.remaining_quantity'])
def test_fixed_point_money_and_quantity_are_exact_signed_strings(api, path, bad):
    failure(api, change(raw(), path, bad), path.replace('.0', '[0]'), 'invalid_decimal' if type(bad) is str else 'invalid_type')


@pytest.mark.parametrize('quantity', ['-1', '0', '0.5', '2', None])
def test_adverse_unknown_and_duplicate_exposure_is_never_dropped(api, quantity):
    body = raw()
    body['holdings'][0].update(quantity=quantity, contract=None, asset_kind='equity', quantity_unit='shares',
                              basis_debit='-1', estimated_remaining_close_cost=None)
    body['holdings'].append(deepcopy(body['holdings'][0]))
    body['open_orders'][0]['status'] = 'terminal'
    value = normalize(api, body).value
    assert len(value.holdings) == 2 and len(value.open_orders) == 1
    assert value.holdings[0].quantity == (None if quantity is None else D(quantity))
    assert value.holdings[0].contract is None and value.holdings[0].mark_quote is not None
    assert not assess(value).observed_flat
    body['holdings'].append({'position_id': 'bad'})
    failure(api, body, 'holdings[2].instrument_ref', 'missing')


@pytest.mark.parametrize('path,bad,code', [
    ('account_id', None, 'invalid_type'), ('connection', 'ready', 'invalid_value'),
    ('session_date', '2026-02-30', 'invalid_date'), ('as_of', '2026-09-04', 'invalid_timestamp'),
    ('available_at', '0001-01-01T00:00:00+01:00', 'invalid_timestamp'),
    ('holdings', (), 'invalid_type'), ('open_orders', None, 'invalid_type'),
    ('holdings.0.asset_kind', 'bond', 'invalid_value'), ('holdings.0.contract.multiplier', True, 'invalid_type'),
    ('holdings.0.contract.expiry', False, 'invalid_type'), ('holdings.0.contract.right', 'CALL', 'invalid_value'),
    ('open_orders.0.execution_uncertain', 1, 'invalid_type'), ('holdings.0.entry_filled_at', 1, 'invalid_type'),
    ('holdings.0.mark_quote.envelope.event_id', '', 'invalid_value'),
    ('holdings.0.mark_quote.envelope.raw_ref', None, 'invalid_type'),
    ('holdings.0.mark_quote.envelope.stream_id', False, 'invalid_type'),
    ('holdings.0.mark_quote.envelope.receive_sequence', -1, 'invalid_value'),
    ('holdings.0.mark_quote.envelope.receive_sequence', True, 'invalid_type'),
    ('holdings.0.mark_quote.envelope.supersedes_record_id', [], 'invalid_type'),
    ('holdings.0.mark_quote.envelope.simulated_received_at', 'bad', 'invalid_timestamp'),
    ('holdings.0.mark_quote.envelope.contract', None, 'expected_exact_dict'),
])
def test_closed_local_paths_reject_invalid_shapes_values_and_envelopes(api, path, bad, code):
    failure(api, change(raw(), path, bad), path.replace('.0', '[0]'), code)


def test_native_nested_metadata_and_quote_diagnostics_remain_independent(api):
    body = raw()
    body['holdings'][0]['mark_quote']['envelope']['metadata'].update(source=None, fidelity='bad')
    meta = failure(api, body, 'holdings[0].mark_quote.envelope.metadata', 'nested_normalization_failed')
    assert type(meta.nested_rejection) is InputRejection
    assert [(d.field, d.code) for d in meta.nested_rejection.diagnostics] == [('source', 'invalid_type'), ('fidelity', 'invalid_value')]
    assert (meta.nested_rejection.event_id, meta.nested_rejection.raw_ref) == ('mark-event', 'mark-raw')
    quoted = failure(api, change(raw(), 'holdings.0.mark_quote.raw_body.bid', '-1'), 'holdings[0].mark_quote.raw_body', 'nested_normalization_failed')
    assert type(quoted.nested_rejection) is QuoteInputRejection
    assert (quoted.nested_rejection.field, quoted.nested_rejection.code) == ('bid', 'invalid_value')
    for cause, changes in [(meta, {'nested_rejection': quoted.nested_rejection}), (quoted, {'nested_rejection': None}),
                           (meta, {'code': 'missing'}), (quoted, {'field': 'holdings[01].mark_quote.raw_body'})]:
        with pytest.raises(ValueError):
            replace(cause, **changes)


def test_hostile_objects_and_trusted_argument_precedence(api):
    class Hostile:
        def __repr__(self):
            raise AssertionError('raw representation called')
        def __eq__(self, other):
            raise AssertionError('raw equality called')
        def __hash__(self):
            return hash('account_id')
    class DictSubclass(dict):
        def __iter__(self):
            raise AssertionError('raw iterator called')
    failure(api, Hostile(), '$', 'expected_exact_dict')
    failure(api, DictSubclass(), '$', 'expected_exact_dict')
    failure(api, {Hostile(): 1}, '$', 'unknown_fields')
    failure(api, change(raw(), 'holdings.0.quantity', Hostile()), 'holdings[0].quantity', 'invalid_type')
    for changes, error in [({'event_id': ''}, ValueError), ({'received_at': NOW.replace(tzinfo=None)}, ValueError),
                           ({'raw_ref': Hostile()}, TypeError), ({'received_at': 'bad'}, TypeError)]:
        with pytest.raises(error):
            normalize(api, Hostile(), **changes)
    for alias in ('event_id', 'raw_ref', 'received_at'):
        failure(api, raw() | {alias: 'forged'}, '$', 'unknown_fields')


def test_exact_frozen_exclusive_results_and_rejections(api):
    result = normalize(api, raw())
    reject = failure(api, {}, 'account_id', 'missing')
    for args in ({}, {'value': result.value, 'rejection': reject}):
        with pytest.raises(ValueError):
            api.AccountValidation(**args)
    for args in ({'value': True}, {'rejection': object()}):
        with pytest.raises(TypeError):
            api.AccountValidation(**args)
    for obj, name, value in [(result, 'value', None), (reject, 'code', 'missing'), (result.value, 'event_id', 'fake')]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)
    for name, value in [('field', 'holdings[-1].quantity'), ('code', 'invalid_decimal'), ('nested_rejection', object())]:
        with pytest.raises((ValueError, TypeError)):
            replace(reject, **{name: value})


def expected_snapshot():
    expected = raw() | dict(record_kind='options_lab.account', account_snapshot_schema_version=1,
                            event_id='account-event', raw_ref='account-raw', received_at=AT)
    held = expected['holdings'][0]
    held['contract']['multiplier'] = {'encoding': 'base10', 'value': '100'}
    wrapper = held['mark_quote']
    env = wrapper['envelope']
    env['contract']['multiplier'] = {'encoding': 'base10', 'value': '100'}
    held['mark_quote'] = dict(record_kind='options_lab.option_quote', quote_content_schema_version=1,
                             contract=env['contract'], metadata=env['metadata'] | dict(raw_ref='mark-raw', received_at=AT),
                             **(wrapper['raw_body'] | dict(bid_size={'encoding': 'base10', 'value': '1'})),
                             price_unit='option_premium_USD_per_share', size_unit='contracts')
    return expected


def test_full_explicit_snapshot_and_independent_hash_cover_all_fields(api):
    value = normalize(api, raw()).value
    expected = expected_snapshot()
    assert api.account_snapshot(value) == expected
    encoded = json.dumps(expected, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    digest = '277fbb39d9d48293fab5871cd8bc1480308094b52a8659e78a96d7c4c9463dd9'
    assert api.account_hash(value) == hashlib.sha256(encoded).hexdigest() == digest
    assert set(expected) - {'record_kind', 'account_snapshot_schema_version'} == {f.name for f in fields(AccountSnapshot)}
    assert set(expected['holdings'][0]) == {f.name for f in fields(HoldingFact)}
    assert set(expected['open_orders'][0]) == {f.name for f in fields(OpenOrderFact)}
    copied = api.account_snapshot(value)
    copied['holdings'][0]['mark_quote']['metadata']['quality_flags'].append('mutated')
    assert api.account_snapshot(value) == expected


@pytest.mark.parametrize('path,value', [
    ('virtual_cash', '+99499.00'), ('holdings.0.quantity', '-0.500'),
    ('holdings.0.mark_quote.raw_body.bid', '4.500'), ('as_of', '2026-09-04T10:05:00-04:00'),
    ('holdings.0.mark_quote.envelope.simulated_received_at', '2026-09-04T10:05:00-04:00'),
    ('holdings.0.mark_quote.envelope.metadata.event_at', '2026-09-04T10:05:00-04:00'),
    ('holdings.0.mark_quote.envelope.event_id', 'another-event'),
    ('holdings.0.mark_quote.envelope.stream_id', 'another-stream'),
    ('holdings.0.mark_quote.envelope.receive_sequence', 100),
    ('holdings.0.mark_quote.envelope.supersedes_record_id', 'previous'),
])
def test_semantic_equivalence_and_unretained_nested_envelope_scope(api, path, value):
    before = normalize(api, raw()).value
    after = normalize(api, change(raw(), path, value)).value
    assert api.account_hash(before) == api.account_hash(after)


@pytest.mark.parametrize('path,value', [
    ('ledger_revision', 'changed'), ('reconciled_ledger_revision', None), ('reconciliation_id', None),
    ('risk_state_revision', 'changed'), ('halt_checkpoint_ref', 'changed'), ('source', 'changed'),
    ('provider_record_id', 'changed'), ('holdings_completeness', 'unknown'), ('virtual_cash', None),
    ('holdings.0.mark_quote.envelope.raw_ref', 'changed'),
    ('holdings.0.mark_quote.envelope.simulated_received_at', '2026-09-04T14:05:01Z'),
    ('holdings.0.mark_quote.raw_body.bid_size', 2), ('holdings.0.mark_quote', None),
])
def test_retained_account_and_mark_identity_changes_are_bound(api, path, value):
    assert api.account_hash(normalize(api, raw()).value) != api.account_hash(normalize(api, change(raw(), path, value)).value)


@pytest.mark.parametrize('value', [D('1E1000000'), D('1E-1000000'), D('1E1000'), D('-1E1000')])
def test_unsupported_numeric_identity_is_a_fixed_value_error_without_erasing_facts(api, value):
    snapshot = account(virtual_cash=value)
    for identify in (api.account_snapshot, api.account_hash):
        with pytest.raises(ValueError, match='^account_identity_representation_unsupported$') as exc:
            identify(snapshot)
        assert type(exc.value) is ValueError
    assert snapshot.virtual_cash is value


def test_huge_raw_facts_survive_and_integer_and_decimal_identity_bounds_are_independent(api):
    body = raw() | {'virtual_cash': '1' + '0' * 1000}
    result = normalize(api, body)
    assert result.rejection is None and result.value.virtual_cash == D('1E1000')
    for value in (result.value, account(holdings=(holding(contract=replace(holding().contract, multiplier=10**1000)),)),
                  account(open_orders=(order(contract=replace(holding().contract, multiplier=-(10**1000))),)),
                  account(holdings=(holding(mark_quote=mark(bid_size=10**1000)),))):
        with pytest.raises(ValueError, match='^account_identity_representation_unsupported$'):
            api.account_hash(value)
    supported = account(virtual_cash=D('1E999'), holdings=(holding(mark_quote=mark(bid_size=10**999)),))
    expected = api.account_hash(supported)
    limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        assert api.account_hash(supported) == expected
    finally:
        sys.set_int_max_str_digits(limit)
    assert api.account_hash(account(virtual_cash=D('0E1000000'))) == api.account_hash(account(virtual_cash=D('-0.00')))
    assert api.account_hash(account(virtual_cash=D('1.' + '0' * 2000))) == api.account_hash(account(virtual_cash=D('1')))


def test_identity_and_parsing_preserve_ambient_context_and_trusted_type_errors(api):
    expected = api.account_hash(normalize(api, raw()).value)
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = True
        ctx.clear_flags()
        before = ctx.copy()
        assert api.account_hash(normalize(api, raw()).value) == expected
        assert ctx.prec == before.prec and ctx.flags == before.flags and ctx.traps == before.traps
    value = normalize(api, raw()).value
    for name, changed in [('event_id', 'other'), ('received_at', NOW + timedelta(seconds=1)), ('raw_ref', 'other')]:
        assert api.account_hash(replace(value, **{name: changed})) != expected
    for identify in (api.account_snapshot, api.account_hash):
        with pytest.raises(TypeError):
            identify(None)


@pytest.mark.parametrize('boundary', ['envelope.metadata', 'raw_body'])
@pytest.mark.parametrize('bad', [None, [], True, {'unknown': 'secret'}])
def test_present_malformed_nested_objects_keep_native_rejections(api, boundary, bad):
    body = change(raw(), 'holdings.0.mark_quote.' + boundary, bad)
    rejection = failure(api, body, 'holdings[0].mark_quote.' + boundary, 'nested_normalization_failed')
    assert type(rejection.nested_rejection) is (InputRejection if boundary.endswith('metadata') else QuoteInputRejection)


def test_explicit_nulls_prior_session_and_constructor_subclasses(api):
    body = raw()
    body.update(as_of=None, reconciled_at=None, available_at=None, currency='EUR', connection='unknown',
                holdings_completeness='incomplete', orders_completeness='unknown', applicable_round_trip_fees='-1')
    body['holdings'][0].update(mark_quote=None, entry_filled_at='2026-09-03T10:05:00-04:00', entry_client_order_id='old')
    body['open_orders'][0]['contract'] = deepcopy(body['holdings'][0]['contract'])
    value = normalize(api, body).value
    assert value.as_of is value.reconciled_at is value.available_at is None
    assert value.holdings[0].entry_filled_at == NOW - timedelta(days=1)
    assert value.open_orders[0].contract == value.holdings[0].contract
    assert value.applicable_round_trip_fees == D('-1') and not assess(value).observed_flat
    class AccountSubclass(AccountSnapshot):
        pass
    subclass = AccountSubclass(**{f.name: getattr(value, f.name) for f in fields(value)})
    for call in (api.account_snapshot, api.account_hash, lambda x: api.AccountValidation(value=x)):
        with pytest.raises(TypeError):
            call(subclass)
    class ValidationSubclass(api.AccountValidation):
        pass
    with pytest.raises(TypeError):
        ValidationSubclass(value=value)


def test_raw_list_string_and_integer_subclasses_never_coerce(api):
    class ListSubclass(list):
        def __iter__(self):
            raise AssertionError('untrusted iteration')
    class StringSubclass(str):
        pass
    class IntegerSubclass(int):
        pass
    for path, bad in [('holdings', ListSubclass()), ('account_id', StringSubclass('a')),
                      ('holdings.0.mark_quote.envelope.receive_sequence', IntegerSubclass(2)),
                      ('holdings.0.contract.multiplier', IntegerSubclass(100))]:
        failure(api, change(raw(), path, bad), path.replace('.0', '[0]'), 'invalid_type')


def test_source_metadata_defaults_and_safe_failure_order(api):
    body = raw()
    metadata = body['holdings'][0]['mark_quote']['envelope']['metadata']
    del metadata['interval_start'], metadata['interval_end']
    assert normalize(api, body).value == normalize(api, raw()).value
    body['virtual_cash'] = False
    body['holdings'][0]['quantity'] = False
    failure(api, body, 'virtual_cash', 'invalid_type')
    del body['open_orders']
    failure(api, body, 'open_orders', 'missing')
