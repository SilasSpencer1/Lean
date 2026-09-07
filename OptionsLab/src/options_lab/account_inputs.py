"""Total account input normalization and bounded retained-field audit identities."""

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Literal

from ._input_parsing import (
    RejectionCode, _InvalidInput, _fail, _parse_contract_id, _parse_date,
    _parse_decimal, _parse_string, _parse_timestamp, _parse_token, _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .account import AccountSnapshot, HoldingFact, OpenOrderFact, _MONEY_FIELDS
from .bar_inputs import _MAX_INTEGER_EXCLUSIVE, _UnsupportedIdentity, _identity_decimal
from .config import _snapshot_hash
from .contracts import ContractId
from .observations import InputRejection, normalize_observation_meta
from .quote_content import _identity_reasons, _integer_snapshot, _option_snapshot, _timestamp_string
from .quote_inputs import QuoteInputRejection, normalize_quote_observation
from .quotes import QuoteObservation

AccountRejectionCode = RejectionCode | Literal['nested_normalization_failed']
_ACCOUNT_FIELDS = (
    'account_id', 'currency', 'source', 'provider_record_id', 'available_at',
    'availability_basis', 'as_of', 'reconciled_at', 'session_date', 'ledger_revision',
    'reconciled_ledger_revision', 'reconciliation_id', 'risk_state_revision',
    'halt_checkpoint_ref', 'connection', 'holdings_completeness', 'orders_completeness',
    *_MONEY_FIELDS, 'holdings', 'open_orders',
)
_HOLDING_FIELDS = (
    'position_id', 'instrument_ref', 'contract', 'asset_kind', 'quantity_unit',
    'quantity', 'basis_debit', 'entry_filled_at', 'entry_client_order_id', 'mark_quote',
    'estimated_remaining_close_cost', 'source', 'provider_record_id', 'raw_ref',
)
_ORDER_FIELDS = (
    'order_ref', 'client_order_id', 'instrument_ref', 'contract', 'side', 'role',
    'status', 'remaining_quantity', 'cumulative_filled_quantity', 'reserved_cash',
    'execution_uncertain', 'source', 'provider_record_id', 'raw_ref',
)
_ENVELOPE_FIELDS = (
    'event_id', 'raw_ref', 'simulated_received_at', 'stream_id', 'receive_sequence',
    'supersedes_record_id', 'contract', 'metadata',
)
_STRING_CODES = ('missing', 'invalid_type', 'invalid_value')
_DECIMAL_CODES = ('missing', 'invalid_type', 'invalid_decimal')
_TIME_CODES = ('missing', 'invalid_type', 'invalid_timestamp')
_SHAPE_CODES = ('missing', 'expected_exact_dict', 'unknown_fields')


@dataclass(frozen=True)
class AccountInputRejection:
    """This class represents an exclusive account failure and native nested evidence."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: AccountRejectionCode
    nested_rejection: InputRejection | QuoteInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Validate the account envelope and closed field/code/cause combinations.

        :returns:             None.
        :raises   TypeError:  If a retained field has the wrong exact type.
        :raises   ValueError: If an identity, time or diagnostic is invalid.
        """
        if type(self) is not AccountInputRejection:
            raise TypeError('rejection must be an exact AccountInputRejection')
        for name in ('event_id', 'raw_ref', 'field', 'code'):
            _require_nonempty_string(name, getattr(self, name))
        object.__setattr__(self, 'received_at', _trusted_datetime('received_at', self.received_at))
        if self.nested_rejection is not None and type(self.nested_rejection) not in (InputRejection, QuoteInputRejection):
            raise TypeError('nested_rejection must be an exact native rejection or None')
        if self.code not in _allowed_codes(self.field):
            raise ValueError('rejection field and code are incompatible')
        if self.code == 'nested_normalization_failed':
            expected = InputRejection if self.field.endswith('.envelope.metadata') else QuoteInputRejection
            if type(self.nested_rejection) is not expected:
                raise ValueError('nested rejection does not match the account boundary')
        elif self.nested_rejection is not None:
            raise ValueError('ordinary account failures cannot contain nested rejection')

    @property
    def stage(self) -> Literal['account_normalization']:
        """Return the fixed normalization stage.

        :returns: The account normalization stage.
        """
        return 'account_normalization'

    @property
    def reasons(self) -> tuple[AccountRejectionCode, ...]:
        """Return the account boundary code, with native detail retained separately.

        :returns: One immutable account reason.
        """
        return (self.code,)


@dataclass(frozen=True)
class AccountValidation:
    """This class represents exactly one complete account or exclusive rejection."""

    value: AccountSnapshot | None = None
    rejection: AccountInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce exact concrete outcomes without claiming source authority.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong exact type.
        :raises   ValueError: If both or neither outcome is present.
        """
        if type(self) is not AccountValidation:
            raise TypeError('validation must be an exact AccountValidation')
        if (self.value is None) == (self.rejection is None):
            raise ValueError('validation must contain exactly one outcome')
        if self.value is not None and type(self.value) is not AccountSnapshot:
            raise TypeError('value must be an exact AccountSnapshot')
        if self.rejection is not None and type(self.rejection) is not AccountInputRejection:
            raise TypeError('rejection must be an exact AccountInputRejection')


class _NestedFailure(Exception):
    """This class represents private transfer of a native nested rejection."""


def normalize_account(raw: object, *, event_id: str, raw_ref: str,
                      received_at: datetime) -> AccountValidation:
    """
    Normalize the closed account body, retaining signed and unknown observations.

    Every account/holding/order key is required, including explicit nullable keys.
    Money and quantities are signed fixed-point strings; arrays become tuples.
    Marks contain raw_body and a complete independent eight-field envelope.
    Nested owners retain their native diagnostics. No partial account is returned
    and no source admission, monetary approval or identity hash is inferred.

    :param    raw:         Untrusted exact account body without envelope aliases.
    :param    event_id:    Trusted account event, distinct from provider report ID.
    :param    raw_ref:     Trusted locator for the complete account input.
    :param    received_at: Trusted aware account receipt, normalized to UTC.
    :returns:             Exactly one full account or account rejection.
    :raises   TypeError:  If a trusted envelope argument has the wrong exact type.
    :raises   ValueError: If a trusted envelope identity or time is invalid.
    """
    _require_nonempty_string('event_id', event_id)
    _require_nonempty_string('raw_ref', raw_ref)
    received_at = _trusted_datetime('received_at', received_at)
    try:
        _require_shape(raw, '$', _ACCOUNT_FIELDS)
        value = AccountSnapshot(
            account_id=_parse_string(raw['account_id'], 'account_id'), event_id=event_id,
            currency=_parse_string(raw['currency'], 'currency'), source=_parse_string(raw['source'], 'source'),
            provider_record_id=_parse_string(raw['provider_record_id'], 'provider_record_id'),
            raw_ref=raw_ref, received_at=received_at,
            available_at=_parse_timestamp(raw['available_at'], 'available_at', nullable=True),
            availability_basis=_parse_token(raw['availability_basis'], 'availability_basis', ('measured', 'assumed')),
            as_of=_parse_timestamp(raw['as_of'], 'as_of', nullable=True),
            reconciled_at=_parse_timestamp(raw['reconciled_at'], 'reconciled_at', nullable=True),
            session_date=_parse_date(raw['session_date'], 'session_date'),
            ledger_revision=_nullable_string(raw['ledger_revision'], 'ledger_revision'),
            reconciled_ledger_revision=_nullable_string(raw['reconciled_ledger_revision'], 'reconciled_ledger_revision'),
            reconciliation_id=_nullable_string(raw['reconciliation_id'], 'reconciliation_id'),
            risk_state_revision=_nullable_string(raw['risk_state_revision'], 'risk_state_revision'),
            halt_checkpoint_ref=_nullable_string(raw['halt_checkpoint_ref'], 'halt_checkpoint_ref'),
            connection=_parse_token(raw['connection'], 'connection', ('connected', 'disconnected', 'unknown')),
            holdings_completeness=_parse_token(raw['holdings_completeness'], 'holdings_completeness', ('complete', 'incomplete', 'unknown')),
            orders_completeness=_parse_token(raw['orders_completeness'], 'orders_completeness', ('complete', 'incomplete', 'unknown')),
            **{name: _amount(raw[name], name) for name in _MONEY_FIELDS},
            holdings=_holdings(raw['holdings']), open_orders=_orders(raw['open_orders']),
        )
    except _InvalidInput as failure:
        return AccountValidation(rejection=AccountInputRejection(event_id, received_at, raw_ref, *failure.args))
    except _NestedFailure as failure:
        path, rejection = failure.args
        return AccountValidation(rejection=AccountInputRejection(
            event_id, received_at, raw_ref, path, 'nested_normalization_failed', rejection,
        ))
    return AccountValidation(value=value)


def _nullable_string(value, path):
    """Parse a nullable source reference without deriving an identifier."""
    return None if value is None else _parse_string(value, path)


def _amount(value, path):
    """Parse nullable signed fixed-point facts independently of identity bounds."""
    return None if value is None else _parse_decimal(value, path)


def _contract(value, path, *, nullable=True):
    """Reuse the actual contract parser with a closed owning account path."""
    if value is None and nullable:
        return None
    try:
        return _parse_contract_id(value)
    except _InvalidInput as failure:
        field, code = failure.args
        _fail(path + field.removeprefix('contract'), code)


def _holdings(raw):
    """Parse every holding in source order; one malformed row rejects the account."""
    if type(raw) is not list:
        _fail('holdings', 'invalid_type')
    values = []
    for index, row in enumerate(raw):
        path = f'holdings[{index}]'
        _require_shape(row, path, _HOLDING_FIELDS)
        values.append(HoldingFact(
            position_id=_parse_string(row['position_id'], path + '.position_id'),
            instrument_ref=_parse_string(row['instrument_ref'], path + '.instrument_ref'),
            contract=_contract(row['contract'], path + '.contract'),
            asset_kind=_parse_token(row['asset_kind'], path + '.asset_kind', ('option', 'equity', 'unknown')),
            quantity_unit=_parse_token(row['quantity_unit'], path + '.quantity_unit', ('contracts', 'shares', 'unknown')),
            quantity=_amount(row['quantity'], path + '.quantity'), basis_debit=_amount(row['basis_debit'], path + '.basis_debit'),
            entry_filled_at=_parse_timestamp(row['entry_filled_at'], path + '.entry_filled_at', nullable=True),
            entry_client_order_id=_nullable_string(row['entry_client_order_id'], path + '.entry_client_order_id'),
            mark_quote=_mark(row['mark_quote'], path + '.mark_quote'),
            estimated_remaining_close_cost=_amount(row['estimated_remaining_close_cost'], path + '.estimated_remaining_close_cost'),
            source=_parse_string(row['source'], path + '.source'),
            provider_record_id=_parse_string(row['provider_record_id'], path + '.provider_record_id'),
            raw_ref=_parse_string(row['raw_ref'], path + '.raw_ref'),
        ))
    return tuple(values)


def _orders(raw):
    """Parse observed order facts without inferring fills or terminal cleanup."""
    if type(raw) is not list:
        _fail('open_orders', 'invalid_type')
    values = []
    for index, row in enumerate(raw):
        path = f'open_orders[{index}]'
        _require_shape(row, path, _ORDER_FIELDS)
        values.append(OpenOrderFact(
            order_ref=_parse_string(row['order_ref'], path + '.order_ref'),
            client_order_id=_nullable_string(row['client_order_id'], path + '.client_order_id'),
            instrument_ref=_parse_string(row['instrument_ref'], path + '.instrument_ref'),
            contract=_contract(row['contract'], path + '.contract'),
            side=_parse_token(row['side'], path + '.side', ('buy', 'sell', 'unknown')),
            role=_parse_token(row['role'], path + '.role', ('entry', 'exit', 'unknown')),
            status=_parse_token(row['status'], path + '.status', ('open', 'cancel_pending', 'replace_pending', 'terminal', 'unknown')),
            remaining_quantity=_amount(row['remaining_quantity'], path + '.remaining_quantity'),
            cumulative_filled_quantity=_amount(row['cumulative_filled_quantity'], path + '.cumulative_filled_quantity'),
            reserved_cash=_amount(row['reserved_cash'], path + '.reserved_cash'),
            execution_uncertain=_boolean(row['execution_uncertain'], path + '.execution_uncertain'),
            source=_parse_string(row['source'], path + '.source'),
            provider_record_id=_parse_string(row['provider_record_id'], path + '.provider_record_id'),
            raw_ref=_parse_string(row['raw_ref'], path + '.raw_ref'),
        ))
    return tuple(values)


def _boolean(value, path):
    """Require the actual raw boolean, not a numeric truth value."""
    if type(value) is not bool:
        _fail(path, 'invalid_type')
    return value


def _mark(raw, path):
    """Validate an independent raw envelope before invoking existing quote owners."""
    if raw is None:
        return None
    _require_shape(raw, path, ('raw_body', 'envelope'))
    env = raw['envelope']
    prefix = path + '.envelope'
    _require_shape(env, prefix, _ENVELOPE_FIELDS)
    event = _parse_string(env['event_id'], prefix + '.event_id')
    ref = _parse_string(env['raw_ref'], prefix + '.raw_ref')
    receipt = _parse_timestamp(env['simulated_received_at'], prefix + '.simulated_received_at')
    _parse_string(env['stream_id'], prefix + '.stream_id')
    sequence = env['receive_sequence']
    if sequence is not None:
        if type(sequence) is not int:
            _fail(prefix + '.receive_sequence', 'invalid_type')
        if sequence < 0:
            _fail(prefix + '.receive_sequence', 'invalid_value')
    _nullable_string(env['supersedes_record_id'], prefix + '.supersedes_record_id')
    contract = _contract(env['contract'], prefix + '.contract', nullable=False)
    meta = normalize_observation_meta(env['metadata'], event_id=event, raw_ref=ref, received_at=receipt)
    if meta.rejection is not None:
        raise _NestedFailure(prefix + '.metadata', meta.rejection)
    quote = normalize_quote_observation(raw['raw_body'], contract=contract, meta=meta.value, event_id=event)
    if quote.rejection is not None:
        raise _NestedFailure(path + '.raw_body', quote.rejection)
    return quote.value


def _allowed_codes(path: str) -> tuple[str, ...]:
    """Accept only diagnostic pairs emitted by this closed account parser."""
    if path == '$':
        return ('expected_exact_dict', 'unknown_fields')
    if path in _ACCOUNT_FIELDS:
        if path in _MONEY_FIELDS:
            return _DECIMAL_CODES
        if path in ('available_at', 'as_of', 'reconciled_at'):
            return _TIME_CODES
        if path == 'session_date':
            return ('missing', 'invalid_type', 'invalid_date')
        if path in ('holdings', 'open_orders'):
            return ('missing', 'invalid_type')
        return _STRING_CODES
    match = re.fullmatch(r'(holdings|open_orders)\[(?:0|[1-9][0-9]*)\](?:\.(.*))?', path)
    if match is None:
        return ()
    kind, field = match.groups()
    if field is None:
        return ('expected_exact_dict', 'unknown_fields')
    fields = _HOLDING_FIELDS if kind == 'holdings' else _ORDER_FIELDS
    if field.startswith('contract.') or (kind == 'holdings' and field.startswith('mark_quote.envelope.contract.')):
        suffix = field.split('contract.', 1)[1]
        if suffix == 'strike':
            return _DECIMAL_CODES
        if suffix == 'expiry':
            return ('missing', 'invalid_type', 'invalid_date')
        if suffix == 'multiplier':
            return ('missing', 'invalid_type')
        return _STRING_CODES if suffix in ('underlying', 'right', 'deliverable_id') else ()
    if field == 'contract':
        return _SHAPE_CODES
    if field in fields:
        if field == 'mark_quote':
            return _SHAPE_CODES
        if field in ('quantity', 'basis_debit', 'estimated_remaining_close_cost',
                     'remaining_quantity', 'cumulative_filled_quantity', 'reserved_cash'):
            return _DECIMAL_CODES
        if field == 'entry_filled_at':
            return _TIME_CODES
        if field == 'execution_uncertain':
            return ('missing', 'invalid_type')
        return _STRING_CODES
    if kind == 'holdings':
        if field in ('mark_quote.raw_body', 'mark_quote.envelope.metadata'):
            return ('missing', 'nested_normalization_failed')
        if field in ('mark_quote.envelope', 'mark_quote.envelope.contract'):
            return _SHAPE_CODES
        if field.startswith('mark_quote.envelope.'):
            name = field.removeprefix('mark_quote.envelope.')
            if name == 'simulated_received_at':
                return _TIME_CODES
            if name in ('event_id', 'raw_ref', 'stream_id', 'receive_sequence', 'supersedes_record_id'):
                return _STRING_CODES
    return ()


def account_snapshot(account: AccountSnapshot) -> dict[str, object]:
    """
    Build a fresh full retained-field audit projection, never source admission.

    Nested quote ingestion event/stream/link/sequence fields do not exist in the
    typed quote. Actual source consumers must bind the original member envelope.

    :param    account:    Exact immutable account with all observed nested facts.
    :returns:            Complete fresh canonical snapshot of retained fields.
    :raises   TypeError: If account is not an exact AccountSnapshot.
    :raises   ValueError: With account_identity_representation_unsupported when
                         numeric canonicalization exceeds supported bounds.
    """
    if type(account) is not AccountSnapshot:
        raise TypeError('account must be an exact AccountSnapshot')
    try:
        return {
            'record_kind': 'options_lab.account', 'account_snapshot_schema_version': 1,
            'account_id': account.account_id, 'event_id': account.event_id, 'currency': account.currency,
            'source': account.source, 'provider_record_id': account.provider_record_id, 'raw_ref': account.raw_ref,
            'received_at': account.received_at.isoformat(), 'available_at': _timestamp_string(account.available_at),
            'availability_basis': account.availability_basis, 'as_of': _timestamp_string(account.as_of),
            'reconciled_at': _timestamp_string(account.reconciled_at), 'session_date': account.session_date.isoformat(),
            'ledger_revision': account.ledger_revision, 'reconciled_ledger_revision': account.reconciled_ledger_revision,
            'reconciliation_id': account.reconciliation_id, 'risk_state_revision': account.risk_state_revision,
            'halt_checkpoint_ref': account.halt_checkpoint_ref, 'connection': account.connection,
            'holdings_completeness': account.holdings_completeness, 'orders_completeness': account.orders_completeness,
            **{name: _identity_decimal(getattr(account, name)) for name in _MONEY_FIELDS},
            'holdings': [_holding_snapshot(value) for value in account.holdings],
            'open_orders': [_order_snapshot(value) for value in account.open_orders],
        }
    except _UnsupportedIdentity:
        raise ValueError('account_identity_representation_unsupported') from None


def account_hash(account: AccountSnapshot) -> str:
    """
    Hash the complete retained-field snapshot through canonical ASCII JSON.

    Callers must retain account facts and translate unsupported identity at the
    actual assessment boundary; a context digest cannot replace this identity.

    :param    account:    Exact immutable account to identify.
    :returns:            Lowercase hexadecimal SHA256 of the actual projection.
    :raises   TypeError: If account is not an exact AccountSnapshot.
    :raises   ValueError: With account_identity_representation_unsupported for
                         unsupported numeric canonicalization; never a fake hash.
    """
    return _snapshot_hash(account_snapshot(account))


def _contract_snapshot(value: ContractId | None):
    """Project the explicit six-field contract after bounded integer preflight."""
    if value is None:
        return None
    if not -_MAX_INTEGER_EXCLUSIVE < value.multiplier < _MAX_INTEGER_EXCLUSIVE:
        raise _UnsupportedIdentity
    return dict(underlying=value.underlying, expiry=value.expiry.isoformat(), right=value.right,
                strike=_identity_decimal(value.strike), multiplier=_integer_snapshot(value.multiplier),
                deliverable_id=value.deliverable_id)


def _mark_snapshot(value: QuoteObservation | None):
    """Reuse bounded quote content and restore its actual retained receipt fields."""
    if value is None:
        return None
    if _identity_reasons(value):
        raise _UnsupportedIdentity
    snapshot = _option_snapshot(value)
    snapshot['metadata'].update(received_at=value.meta.received_at.isoformat(), raw_ref=value.meta.raw_ref)
    return snapshot


def _holding_snapshot(value: HoldingFact):
    """Project every retained holding fact without dropping duplicate exposure."""
    return dict(position_id=value.position_id, instrument_ref=value.instrument_ref,
                contract=_contract_snapshot(value.contract), asset_kind=value.asset_kind,
                quantity_unit=value.quantity_unit, quantity=_identity_decimal(value.quantity),
                basis_debit=_identity_decimal(value.basis_debit), entry_filled_at=_timestamp_string(value.entry_filled_at),
                entry_client_order_id=value.entry_client_order_id, mark_quote=_mark_snapshot(value.mark_quote),
                estimated_remaining_close_cost=_identity_decimal(value.estimated_remaining_close_cost),
                source=value.source, provider_record_id=value.provider_record_id, raw_ref=value.raw_ref)


def _order_snapshot(value: OpenOrderFact):
    """Project every order observation without inferring filled or terminal state."""
    return dict(order_ref=value.order_ref, client_order_id=value.client_order_id, instrument_ref=value.instrument_ref,
                contract=_contract_snapshot(value.contract), side=value.side, role=value.role, status=value.status,
                remaining_quantity=_identity_decimal(value.remaining_quantity),
                cumulative_filled_quantity=_identity_decimal(value.cumulative_filled_quantity),
                reserved_cash=_identity_decimal(value.reserved_cash), execution_uncertain=value.execution_uncertain,
                source=value.source, provider_record_id=value.provider_record_id, raw_ref=value.raw_ref)
