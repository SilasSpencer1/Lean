"""Build finite account streams with actual independent P11 market counterparts."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes

FIXTURE_ID = 'p12c-account-context-v1'
NOW = '2026-09-04T14:05:00Z'
BEFORE = '2026-09-04T14:04:59Z'
MARKET = ('session', 'good-option_quote', 'good-underlying_quote', 'good-greek', 'good-proof',
          'good-instrument_tradability', 'good-provider_contract_mapping', 'good-contract_reference')
UNITS = dict(money='USD', quantity='holding_quantity_unit',
             basis_debit='USD_total_remaining_entry_premium_excluding_posted_fees',
             virtual_cash='USD_after_posted_premiums_proceeds_fees',
             virtual_settled_cash='USD_before_virtual_reservations', virtual_reserved_cash='USD_aggregate_unspent_hold',
             broker_settled_cash='USD_settled_cash', broker_available_cash='USD_already_net_of_broker_holds',
             broker_nonmargin_buying_power='USD_nonmargin_already_net_of_broker_holds',
             session_realized_pnl='USD_including_all_posted_fees', estimated_remaining_close_cost='USD_unpaid_remaining_cost',
             applicable_round_trip_fees='USD_optional_total_round_trip_estimate')


def build_fixture() -> bytes:
    """Assemble actual literal records and capture the real assembly time.

    :returns: Readable fixture bytes with current provenance and body hashes.
    :raises OSError: If the retained P11 source cannot be read.
    """
    prior = json.loads((Path(__file__).parent / 'fixtures/p11-feature-vector-v1.json').read_bytes())
    rows = [deepcopy(m) for m in prior['members'] if m['record_id'] in MARKET]
    profiles = [deepcopy(p) for p in prior['modeled_source_profiles'] if p['profile_id'] in {m['profile_id'] for m in rows}]
    payload = {**prior, 'fixture_id': FIXTURE_ID, 'generator_id': 'optionslab-account-context-fixture-builder',
               'generator_version': '1', 'generator_source_ref': 'OptionsLab/tests/build_account_context_fixture.py',
               'assembled_at': datetime.now(timezone.utc).isoformat(), 'members': rows, 'modeled_source_profiles': profiles}
    option = next(m for m in rows if m['record_id'] == 'good-option_quote')
    option_profile = next(p for p in profiles if p['profile_id'] == option['profile_id'])

    def quote(name, stream, *, parent=None, sequence=1, bid='4.90', ask='5.10', at=BEFORE):
        """Add an independent source-mark occurrence using actual quote framing."""
        if not any(p['profile_id'] == stream for p in profiles):
            profiles.append({**option_profile, 'profile_id': stream, 'stream_id': stream})
        body, env = deepcopy(option['raw_body']), deepcopy(option['envelope'])
        body.update(bid=bid, ask=ask, ask_at=None if ask is None else body['ask_at'], ask_size=None if ask is None else body['ask_size'])
        env.update(event_id='event-'+name, raw_ref='synthetic://p12c/'+name, stream_id=stream,
                   receive_sequence=sequence, supersedes_record_id=parent, simulated_received_at=at)
        env['metadata'].update(provider_record_id=name, available_at=at)
        row = _member(name, 'option_quote', stream, body, env)
        rows.append(row)
        return row

    bid_only = quote('mark-bid-only', 'mark-bid-only', ask=None)
    mark_a = quote('mark-update-A', 'mark-update')
    quote('mark-update-B', 'mark-update', parent='mark-update-A', sequence=2, bid='4.80', at=NOW)
    held = dict(position_id='position-1', instrument_ref='opaque-K', contract=deepcopy(option['envelope']['contract']),
                asset_kind='option', quantity_unit='contracts', quantity='1', basis_debit='500',
                entry_filled_at='2026-09-04T14:00:00Z', entry_client_order_id='entry-1',
                mark_quote={k: deepcopy(option[k]) for k in ('raw_body', 'envelope')}, estimated_remaining_close_cost='1',
                source='fixture-holdings', provider_record_id='holding-report', raw_ref='synthetic://p12c/holding')
    pending = dict(order_ref='order-1', client_order_id=None, instrument_ref='opaque-K', contract=None,
                   side='unknown', role='unknown', status='cancel_pending', remaining_quantity=None,
                   cumulative_filled_quantity='0.5', reserved_cash='-2', execution_uncertain=True,
                   source='fixture-orders', provider_record_id='order-report', raw_ref='synthetic://p12c/order')
    flat = dict(account_id='account', currency='USD', source='fixture-account-ledger', provider_record_id='report',
                available_at=NOW, availability_basis='measured', as_of=NOW, reconciled_at=NOW, session_date='2026-09-04',
                ledger_revision='ledger-1', reconciled_ledger_revision='ledger-1', reconciliation_id='reconciliation-1',
                risk_state_revision='risk-1', halt_checkpoint_ref='halt-1', connection='connected',
                holdings_completeness='complete', orders_completeness='complete', virtual_cash='100000',
                virtual_equity='100000', session_start_equity='100000', high_water_mark='100000',
                session_realized_pnl='0', reported_marked_pnl='0', virtual_settled_cash='100000', virtual_reserved_cash='0',
                broker_settled_cash='100000', broker_available_cash='100000', broker_nonmargin_buying_power='100000',
                applicable_round_trip_fees='1', holdings=[], open_orders=[])
    occupied = {**flat, 'virtual_cash': '99499', 'virtual_settled_cash': '99499', 'session_realized_pnl': '-1', 'holdings': [held]}
    adverse = deepcopy(occupied)
    adverse.update(connection='disconnected', holdings_completeness='incomplete', session_date='2026-09-03',
                   open_orders=[pending, {**pending, 'order_ref': 'terminal', 'status': 'terminal'}])
    adverse['holdings'] = [{**deepcopy(held), 'quantity': q} for q in ('-1', '0.5', '0', None)]
    adverse['holdings'] += [{**deepcopy(held), 'position_id': 'exercised', 'asset_kind': 'equity', 'quantity_unit': 'shares',
                            'quantity': '100', 'contract': None, 'mark_quote': None},
                           {**deepcopy(held), 'position_id': 'unknown', 'asset_kind': 'unknown', 'quantity_unit': 'unknown',
                            'contract': None, 'mark_quote': None}]

    def add(scenario, label='', *, body=flat, parent=None, sequence=1, event=None, stream=None, at=NOW):
        """Frame one explicit account occurrence without interpreting its claims."""
        name = scenario + ('-'+label if label else '')
        stream = stream or 'account-stream-'+scenario
        profile = 'account-profile-'+stream.removeprefix('account-stream-')
        if not any(p['profile_id'] == profile for p in profiles):
            profiles.append(dict(profile_id=profile, kind='account_snapshot', source='fixture-account-ledger',
                                 stream_id=stream, feed_class=None, fidelity=None, availability_basis='measured',
                                 units=UNITS, record_identity_rule='new_event_id_per_update'))
        raw = deepcopy(body)
        raw.update(account_id='account-'+scenario, provider_record_id='report-'+scenario, available_at=at)
        if at == BEFORE:
            raw.update(as_of=BEFORE, reconciled_at=BEFORE)
        env = dict(event_id=event or 'event-'+name, raw_ref='synthetic://p12c/'+name, simulated_received_at=NOW,
                   stream_id=stream, receive_sequence=sequence, supersedes_record_id=parent, contract=None, metadata=None)
        row = _member(name, 'account_snapshot', profile, raw, env)
        rows.append(row)
        return row

    add('flat')
    add('basis-mismatch', body={**flat, 'availability_basis': 'assumed'})
    add('occupied', body=occupied)
    add('huge', body={**flat, 'virtual_cash': '1'+'0'*1000})
    add('adverse', body=adverse)
    bad_quote = deepcopy(occupied)
    bad_quote['holdings'][0]['mark_quote']['raw_body']['bid'] = '-1'
    add('quote-invalid', body=bad_quote)
    for name, mark in [('bid-only', bid_only), ('mark-omission', mark_a)]:
        body = deepcopy(occupied)
        body['holdings'][0]['mark_quote'] = {k: deepcopy(mark[k]) for k in ('raw_body', 'envelope')}
        add(name, body=body)
    changes = [('nested-event', 'event_id', 'other-event'), ('nested-stream', 'stream_id', 'other-stream'),
               ('nested-sequence', 'receive_sequence', 2), ('nested-link', 'supersedes_record_id', 'other-parent'),
               ('nested-receipt', 'simulated_received_at', NOW), ('nested-ref', 'raw_ref', 'other-ref')]
    for scenario, field, value in changes:
        add(scenario, 'A', body=occupied, event='event-'+scenario)
        body = deepcopy(occupied)
        body['holdings'][0]['mark_quote']['envelope'][field] = value
        add(scenario, 'B', body=body, sequence=2, event='event-'+scenario)
    for scenario in ('transport', 'semantic', 'adverse-conflict', 'nested-order'):
        body = deepcopy(adverse if scenario == 'adverse-conflict' else occupied)
        if scenario == 'nested-order':
            body['holdings'] += [deepcopy(held), {**deepcopy(held), 'mark_quote': None}]
            body['holdings'][1]['mark_quote']['envelope']['event_id'] = 'second-mark-event'
        add(scenario, 'A', body=body, event='event-'+scenario)
        if scenario == 'semantic':
            body.update(virtual_cash='+99499.00', as_of='2026-09-04T10:05:00-04:00', reconciled_at='2026-09-04T10:05:00-04:00')
            body['holdings'][0]['quantity'] = '+1.0'
            mark = body['holdings'][0]['mark_quote']
            mark['raw_body'].update(bid='4.900', bid_at='2026-09-04T10:04:58-04:00')
            mark['envelope'].update(simulated_received_at='2026-09-04T10:04:59-04:00')
            mark['envelope']['metadata'].update(event_at='2026-09-04T10:04:58-04:00')
        elif scenario == 'adverse-conflict':
            body['virtual_cash'] = '-1'
        elif scenario == 'nested-order':
            body['holdings'][0], body['holdings'][1] = body['holdings'][1], body['holdings'][0]
        row = add(scenario, 'B', body=body, sequence=2, event='event-'+scenario)
        if scenario == 'transport':
            row['envelope']['simulated_received_at'] = '2026-09-04T14:05:01Z'
    for scenario in ('update', 'malformed', 'unknown-availability', 'target-unknown', 'source-unknown',
                     'profile-mismatch', 'future', 'ordered', 'tie', 'fork', 'roots', 'cross-stream', 'cycle'):
        equal = scenario in ('ordered', 'tie', 'cycle')
        add(scenario, 'A', parent=scenario+'-B' if scenario == 'cycle' else None, at=NOW if equal else BEFORE)
        body = deepcopy(occupied)
        if scenario == 'update':
            body.update(connection='disconnected', session_date='2026-09-03', ledger_revision='ledger-2', reconciled_ledger_revision='ledger-2')
        if scenario == 'malformed':
            body['holdings'][0]['mark_quote']['envelope']['metadata'].update(source='', available_at='bad')
        if scenario == 'future':
            body['holdings'] = False
        if scenario in ('source-unknown', 'profile-mismatch'):
            body['source'] = None if scenario == 'source-unknown' else 'other-account-source'
        at = None if scenario == 'unknown-availability' else '2026-09-04T14:05:00.000001Z' if scenario == 'future' else NOW
        row = add(scenario, 'B', body=body, at=at, sequence=1 if scenario == 'tie' else 2,
                  parent=None if scenario == 'roots' else scenario+'-A',
                  stream='account-stream-cross-other' if scenario == 'cross-stream' else None)
        if scenario == 'target-unknown':
            row['raw_body']['account_id'] = None
        if scenario == 'fork':
            add(scenario, 'C', body=occupied, parent=scenario+'-A', sequence=3)
    add('orphan', parent='absent-account', body=occupied)
    # Recompute after explicit malformed/transport mutations; no enclosing hash is embedded.
    payload['members'] = [_member(m['record_id'], m['kind'], m['profile_id'], m['raw_body'], m['envelope']) for m in rows]
    return _payload_bytes(payload)


if __name__ == '__main__':
    target = Path(__file__).parent / 'fixtures' / (FIXTURE_ID+'.json')
    target.write_bytes(build_fixture())
    print(target)
