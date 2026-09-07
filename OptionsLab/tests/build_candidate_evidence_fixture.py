"""Frame independent candidate scenes and actual mark retransmission lineages."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes
from build_coherence_fixture import _quote_hash
from options_lab._input_parsing import _parse_contract_id
from options_lab.greeks import FIXTURE_GREEK_METHOD, GreekInputs, greek_input_hash

FIXTURE_ID = 'p13b-candidate-evidence-v1'
NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)


def build_fixture() -> bytes:
    """Recompute upstream identities before committing the complete source payload.

    :returns: New literal fixture bytes with truthful assembly provenance.
    :raises OSError: If an existing source template cannot be read.
    :raises RuntimeError: If new quote facts cannot normalize or identify.
    """
    folder = Path(__file__).parent / 'fixtures'
    prior = json.loads((folder / 'p12c-account-context-v1.json').read_bytes())
    p11 = json.loads((folder / 'p11-feature-vector-v1.json').read_bytes())
    templates = {m['kind']: m for m in prior['members'] if m['record_id'].startswith('good-') or m['record_id'] == 'flat'}
    templates['tick_rule'] = next(m for m in p11['members'] if m['record_id'] == 'optional-tick')
    profiles = {p['profile_id']: p for p in [*p11['modeled_source_profiles'], *prior['modeled_source_profiles']]}
    session = deepcopy(next(m for m in prior['members'] if m['record_id'] == 'session'))
    rows, declared = [session], [deepcopy(profiles[session['profile_id']])]
    payload = {**prior, 'fixture_id': FIXTURE_ID, 'generator_id': 'optionslab-candidate-evidence-fixture-builder',
               'generator_version': '1', 'generator_source_ref': 'OptionsLab/tests/build_candidate_evidence_fixture.py',
               'assembled_at': datetime.now(timezone.utc).isoformat(), 'members': rows, 'modeled_source_profiles': declared}

    def add(name, kind, body, env, *, stream=None, parent=None, sequence=1):
        """Frame an actual independently identified occurrence in its declared stream."""
        stream = stream or name
        if not any(p['profile_id'] == stream for p in declared):
            declared.append({**deepcopy(profiles[templates[kind]['profile_id']]), 'profile_id': stream, 'stream_id': stream})
        env.update(event_id='event-'+name, raw_ref='synthetic://p13b/'+name, stream_id=stream,
                   supersedes_record_id=parent, receive_sequence=sequence)
        row = _member(name, kind, stream, body, env)
        rows.append(row)
        return row

    scenes = ['initial', 'fresh', 'wide', 'missing-ask', 'locked', 'locked-underlying', 'crossed',
              'bad-proof', 'bad-greek', 'negative-fee', 'overlap', 'other-bad', 'unknown-update',
              'omitted-update', 'm1', 'm3-conflict', 'm3-retired', 'identical', 'dte6', 'dte7', 'dte21', 'dte22',
              'delta39', 'delta40', 'delta60', 'delta61', 'put', 'wrong-sign', 'stale-greek']
    for scene in scenes:
        at = NOW + timedelta(seconds=2 if scene in ('fresh', 'wide') else 0)
        event, available = at-timedelta(seconds=2), at-timedelta(seconds=1)
        target = deepcopy(templates['option_quote']['envelope']['contract'])
        if scene.startswith('dte'):
            target['expiry'] = (NOW.date()+timedelta(days=int(scene[3:]))).isoformat()
        if scene in ('put', 'wrong-sign'):
            target['right'] = 'put'
        if scene == 'other-bad':
            target['strike'] = '435'
        built = {}
        for kind in templates:
            body, env = deepcopy(templates[kind]['raw_body']), deepcopy(templates[kind]['envelope'])
            name = scene+'-'+kind
            env['simulated_received_at'] = available.isoformat()
            if 'contract' in body:
                body['contract'] = deepcopy(target)
            if env['contract'] is not None:
                env['contract'] = deepcopy(target)
            if 'provider_record_id' in body:
                body['provider_record_id'] = name
            if kind in ('option_quote', 'underlying_quote'):
                body.update(bid_at=event.isoformat(), ask_at=event.isoformat())
                env['metadata'].update(provider_record_id=name, event_at=event.isoformat(), available_at=available.isoformat())
                if kind == 'option_quote':
                    bid, ask = ('4.95', '5.00') if scene == 'fresh' else ('4.80', '5.00') if scene == 'wide' else ('5.00', '5.10')
                    if scene in ('crossed', 'other-bad'):
                        bid = '5.20'
                    if scene == 'locked':
                        bid = ask
                    if scene == 'overlap':
                        bid, ask = '3.50', '3.51'
                    body.update(bid=bid, ask=ask)
                    if scene == 'missing-ask':
                        body.update(ask=None, ask_at=None, ask_size=None)
                elif scene == 'locked-underlying':
                    body.update(bid='434', ask='434')
            elif kind == 'account_snapshot':
                body.update(account_id=name, available_at=at.isoformat(), as_of=at.isoformat(), reconciled_at=at.isoformat())
                env['simulated_received_at'] = at.isoformat()
                for field in ('virtual_cash', 'virtual_equity', 'session_start_equity', 'high_water_mark',
                              'virtual_settled_cash', 'broker_settled_cash', 'broker_available_cash', 'broker_nonmargin_buying_power'):
                    body[field] = '150000'
                if scene == 'negative-fee':
                    body['applicable_round_trip_fees'] = '-1'
            elif kind == 'tick_rule':
                body.update(increment='0.01', price_from='0', price_until=None, effective_from='2026-09-04T13:30:00Z',
                            effective_until='2026-09-04T20:00:00Z', available_at=available.isoformat())
            built[kind] = add(name, kind, body, env)
        option_hash, underlying_hash = (_quote_hash(built[k]) for k in ('option_quote', 'underlying_quote'))
        greek = built['greek_observation']['raw_body']
        greek_at = NOW-timedelta(seconds=6) if scene == 'stale-greek' else event
        delta = '0.'+scene[5:] if scene.startswith('delta') else '-0.50' if scene == 'put' else '0.50'
        greek.update(delta=delta, as_of=greek_at.isoformat(), available_at=available.isoformat())
        greek['inputs'].update(option_quote_hash=option_hash, underlying_quote_hash=underlying_hash)
        inputs = GreekInputs(option_hash, underlying_hash, Decimal(greek['inputs']['rate']), Decimal(greek['inputs']['dividend_yield']),
                             greek['inputs']['rate_unit'], greek['inputs']['dividend_unit'], greek['inputs']['assumptions_id'])
        greek['input_hash'] = greek_input_hash(inputs, contract=_parse_contract_id(target), method=FIXTURE_GREEK_METHOD, as_of=greek_at)
        if scene == 'bad-greek':
            greek['input_hash'] = '0'*64
        proof = built['quote_coherence']['raw_body']
        proof.update(evidence_id=scene+'-proof', snapshot_id=scene+'-joint', coherent_at=event.isoformat(),
                     available_at=available.isoformat(), option_quote_hash=option_hash, underlying_quote_hash=underlying_hash)
        if scene == 'bad-proof':
            proof['coherent_at'] = (event+timedelta(microseconds=1)).isoformat()
        if scene in ('unknown-update', 'omitted-update'):
            old = built['option_quote']
            body, env = deepcopy(old['raw_body']), deepcopy(old['envelope'])
            env['metadata'].update(provider_record_id=scene+'-successor', available_at=at.isoformat())
            if scene == 'unknown-update':
                env['contract']['strike'] = 'malformed'
            add(scene+'-successor', 'option_quote', body, env, stream=old['profile_id'], parent=old['record_id'], sequence=2)
        if scene in ('m1', 'm3-conflict', 'm3-retired', 'identical'):
            old = built['option_quote']
            old['record_id'] = scene+'-a-unrequested'
            if scene == 'identical':
                mark = deepcopy(old)
                mark['record_id'] = scene+'-b-representative'
                rows.append(mark)
            else:
                for suffix, seq in [('b-representative', 2), ('c-embedded', 3)]:
                    body, env = deepcopy(old['raw_body']), deepcopy(old['envelope'])
                    env['simulated_received_at'] = (available+timedelta(microseconds=seq)).isoformat()
                    mark = add(scene+'-'+suffix, 'option_quote', body, env, stream=old['profile_id'], sequence=seq)
            held = deepcopy(next(m for m in prior['members'] if m['record_id'] == 'occupied')['raw_body']['holdings'][0])
            held['mark_quote'] = {k: deepcopy(mark[k]) for k in ('raw_body', 'envelope')}
            built['account_snapshot']['raw_body']['holdings'] = [held]
            if scene not in ('m1', 'identical'):
                body, env = deepcopy(old['raw_body']), deepcopy(old['envelope'])
                body['bid'] = '4.90'
                parent = None
                if scene == 'm3-retired':
                    parent = scene+'-b-representative'
                    env['metadata'].update(provider_record_id=scene+'-successor', available_at=at.isoformat())
                add(scene+'-successor', 'option_quote', body, env, stream=old['profile_id'], parent=parent, sequence=4)
    for name, kind, changes in (
        ('overlap-tick', 'tick_rule', dict(increment='0.05')),
        ('scheduled-tick', 'tick_rule', dict(effective_from='2026-09-04T18:00:00Z')),
        ('overlap-reference', 'contract_reference', dict(components=None)),
        ('scheduled-reference', 'contract_reference', dict(effective_from='2026-09-04T18:00:00Z')),
        ('invalid-reference', 'contract_reference', dict(effective_until='2026-09-04T13:00:00Z')),
        ('overlap-status', 'instrument_tradability', dict(status='halted')),
        ('scheduled-status', 'instrument_tradability', dict(status='halted', effective_from='2026-09-04T18:00:00Z')),
        ('unknown-status', 'instrument_tradability', dict(effective_until=None)),
        ('null-status', 'instrument_tradability', dict(contract=None))):
        base = next(m for m in rows if m['record_id'] == ('overlap' if name == 'overlap-tick' else 'initial')+'-'+kind)
        body, env = deepcopy(base['raw_body']), deepcopy(base['envelope'])
        body.update(changes, provider_record_id=name)
        add(name, kind, body, env)
    for field in ('event_id', 'stream_id', 'receive_sequence', 'supersedes_record_id'):
        name = 'mark-'+field
        base = next(m for m in rows if m['record_id'] == 'initial-account_snapshot')
        option = next(m for m in rows if m['record_id'] == 'initial-option_quote')
        body, env = deepcopy(base['raw_body']), deepcopy(base['envelope'])
        held = deepcopy(next(m for m in prior['members'] if m['record_id'] == 'occupied')['raw_body']['holdings'][0])
        held['mark_quote'] = {k: deepcopy(option[k]) for k in ('raw_body', 'envelope')}
        held['mark_quote']['envelope'][field] = 999 if field == 'receive_sequence' else 'foreign-mark-claim'
        body.update(account_id=name, provider_record_id=name, holdings=[held])
        add(name, 'account_snapshot', body, env)
    for name, equity, cash in [('capital-equal', '104200', '521'), ('capital-below', '104199.99', '520.99'),
                               ('cash-below', '150000', '520.99')]:
        base = next(m for m in rows if m['record_id'] == 'initial-account_snapshot')
        body, env = deepcopy(base['raw_body']), deepcopy(base['envelope'])
        body.update(account_id=name, provider_record_id=name, virtual_equity=equity, broker_available_cash=cash)
        add(name, 'account_snapshot', body, env)
    payload['members'] = [_member(m['record_id'], m['kind'], m['profile_id'], m['raw_body'], m['envelope']) for m in rows]
    return _payload_bytes(payload)


if __name__ == '__main__':
    target = Path(__file__).parent / 'fixtures' / (FIXTURE_ID+'.json')
    target.write_bytes(build_fixture())
    print(target)
