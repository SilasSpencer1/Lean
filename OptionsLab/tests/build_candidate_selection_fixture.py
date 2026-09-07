"""Frame one coherent finite population with eight actual option contracts."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes
from build_coherence_fixture import _quote_hash
from options_lab._input_parsing import _parse_contract_id
from options_lab.greeks import FIXTURE_GREEK_METHOD, GreekInputs, greek_input_hash

FIXTURE_ID = 'p13c-candidate-selection-v1'
POPULATION = (
    ('c99', 'call', '99', '2026-09-18', '.50', '4.90'),
    ('c100', 'call', '100', '2026-09-18', '.50', '4.90'),
    ('late', 'call', '98', '2026-09-25', '.50', '4.90'),
    ('wide', 'call', '101', '2026-09-18', '.50', '4.89'),
    ('delta', 'call', '102', '2026-09-18', '.475', '5.00'),
    ('p99', 'put', '99', '2026-09-18', '-.50', '4.90'),
    ('p100', 'put', '100', '2026-09-18', '-.50', '4.90000000000000000000000000000000000001'),
    ('pdelta', 'put', '101', '2026-09-18', '-.50000000000000000000000000000000000001', '5.00'),
)


def build_fixture() -> bytes:
    """Rebind every contract's quote, Greek and proof to one shared market.

    :returns: Complete new fixture bytes with actual assembly provenance.
    :raises OSError: If the existing source fixture cannot be read.
    :raises RuntimeError: If an original quote cannot normalize or identify.
    """
    folder = Path(__file__).parent / 'fixtures'
    prior = json.loads((folder / 'p13b-candidate-evidence-v1.json').read_bytes())
    templates = {m['kind']: m for m in prior['members'] if m['record_id'].startswith('initial-')}
    profiles = {p['profile_id']: p for p in prior['modeled_source_profiles']}
    session = deepcopy(next(m for m in prior['members'] if m['record_id'] == 'session'))
    rows, declared = [session], [deepcopy(profiles[session['profile_id']])]

    def add(name, kind, body, env, stream=None):
        """Frame an actual source occurrence with a declared finite stream.

        :param name: Unique member identifier.
        :param kind: Existing concrete owner kind.
        :param body: Original source body with explicit field edits.
        :param env: Original envelope with actual receipt facts.
        :param stream: Existing stream for a retransmission, otherwise independent.
        :returns: Newly framed member, also retained in the fixture.
        """
        stream = stream or name
        if not any(p['profile_id'] == stream for p in declared):
            declared.append({**deepcopy(profiles[templates[kind]['profile_id']]), 'profile_id': stream, 'stream_id': stream})
        env.update(event_id='event-'+name, raw_ref='synthetic://p13c/'+name, stream_id=stream)
        row = _member(name, kind, stream, body, env)
        rows.append(row)
        return row

    shared = {}
    for kind in ('underlying_quote', 'account_snapshot'):
        body, env = deepcopy(templates[kind]['raw_body']), deepcopy(templates[kind]['envelope'])
        shared[kind] = add(kind, kind, body, env)
    underlying_hash = _quote_hash(shared['underlying_quote'])
    kinds = tuple(k for k in templates if k not in shared)
    for name, right, strike, expiry, delta, bid in POPULATION:
        target = {**templates['option_quote']['envelope']['contract'], 'right': right, 'strike': strike, 'expiry': expiry}
        built = {}
        for kind in kinds:
            body, env = deepcopy(templates[kind]['raw_body']), deepcopy(templates[kind]['envelope'])
            if 'contract' in body:
                body['contract'] = deepcopy(target)
            if env['contract'] is not None:
                env['contract'] = deepcopy(target)
            if kind == 'option_quote':
                body['bid'] = bid
            if kind == 'provider_contract_mapping':
                body['symbol'] = name
            built[kind] = add(name+'-'+kind, kind, body, env)
        option_hash = _quote_hash(built['option_quote'])
        greek = built['greek_observation']['raw_body']
        greek['delta'] = delta
        greek['inputs'].update(option_quote_hash=option_hash, underlying_quote_hash=underlying_hash)
        inputs = GreekInputs(option_hash, underlying_hash, Decimal(greek['inputs']['rate']), Decimal(greek['inputs']['dividend_yield']),
                             greek['inputs']['rate_unit'], greek['inputs']['dividend_unit'], greek['inputs']['assumptions_id'])
        greek['input_hash'] = greek_input_hash(inputs, contract=_parse_contract_id(target), method=FIXTURE_GREEK_METHOD,
                                              as_of=datetime.fromisoformat(greek['as_of']))
        built['quote_coherence']['raw_body'].update(evidence_id=name+'-proof', snapshot_id=name+'-joint',
            option_quote_hash=option_hash, underlying_quote_hash=underlying_hash)
    original = next(m for m in rows if m['record_id'] == 'c99-option_quote')
    env = deepcopy(original['envelope'])
    env.update(simulated_received_at='2026-09-04T14:04:59.000001Z', receive_sequence=2)
    add('c99-redelivery', 'option_quote', deepcopy(original['raw_body']), env, stream=original['profile_id'])
    early = {}
    for kind in ('option_quote', 'underlying_quote', 'account_snapshot', 'greek_observation', 'quote_coherence', 'tick_rule'):
        source_id = kind if kind in shared else 'c99-'+kind
        source = next(m for m in rows if m['record_id'] == source_id)
        body, env = deepcopy(source['raw_body']), deepcopy(source['envelope'])
        env['simulated_received_at'] = '2026-09-04T13:59:59Z'
        body['available_at'] = '2026-09-04T13:59:59Z'
        if kind in ('option_quote', 'underlying_quote'):
            del body['available_at']
            body.update(bid_at='2026-09-04T13:59:58Z', ask_at='2026-09-04T13:59:58Z')
            env['metadata'].update(event_at='2026-09-04T13:59:58Z', available_at='2026-09-04T13:59:59Z')
        elif kind == 'account_snapshot':
            body.update(as_of='2026-09-04T13:59:59Z', reconciled_at='2026-09-04T13:59:59Z')
        early[kind] = add('early-'+kind, kind, body, env)
    option_hash, underlying_hash = (_quote_hash(early[k]) for k in ('option_quote', 'underlying_quote'))
    greek = early['greek_observation']['raw_body']
    greek['as_of'] = '2026-09-04T13:59:58Z'
    greek['inputs'].update(option_quote_hash=option_hash, underlying_quote_hash=underlying_hash)
    inputs = GreekInputs(option_hash, underlying_hash, Decimal(greek['inputs']['rate']), Decimal(greek['inputs']['dividend_yield']),
        greek['inputs']['rate_unit'], greek['inputs']['dividend_unit'], greek['inputs']['assumptions_id'])
    greek['input_hash'] = greek_input_hash(inputs, contract=_parse_contract_id(original['envelope']['contract']),
        method=FIXTURE_GREEK_METHOD, as_of=datetime.fromisoformat(greek['as_of']))
    early['quote_coherence']['raw_body'].update(coherent_at=greek['as_of'], evidence_id='early-proof', snapshot_id='early-joint',
        option_quote_hash=option_hash, underlying_quote_hash=underlying_hash)
    payload = {**prior, 'fixture_id': FIXTURE_ID, 'generator_id': 'optionslab-candidate-selection-fixture-builder',
        'generator_version': '1', 'generator_source_ref': 'OptionsLab/tests/build_candidate_selection_fixture.py',
        'assembled_at': datetime.now(timezone.utc).isoformat(), 'modeled_source_profiles': declared,
        'members': [_member(m['record_id'], m['kind'], m['profile_id'], m['raw_body'], m['envelope']) for m in rows]}
    return _payload_bytes(payload)


if __name__ == '__main__':
    target = Path(__file__).parent / 'fixtures' / (FIXTURE_ID+'.json')
    target.write_bytes(build_fixture())
    print(target)
