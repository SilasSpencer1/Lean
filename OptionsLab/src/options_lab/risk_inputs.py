"""Source-backed risk observations and bounded local control requests."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from ._input_parsing import _InvalidInput, _parse_decimal, _parse_string, _parse_timestamp
from ._validation import _require_nonempty_string, _trusted_datetime
from .account import AccountAssessment, AccountSnapshot
from .bar_inputs import _UnsupportedIdentity, _identity_decimal
from .bundle_availability import BundleAssessment, assess_bundle
from .bundle_inputs import _make
from .bundles import VerifiedBundle
from .candidates import _context_account_evidence
from .config import StrategyConfig, _snapshot_hash
from .context import ContextComponent, DecisionContext
from .context_recheck import ContextRecheck, recheck_decision_context
from .sessions import ExchangeSession


_BASE_FIELDS = frozenset(('schema_version', 'kind', 'account_id', 'source', 'actor_id',
                          'occurred_at', 'expected_risk_revision'))
_HALT_REASONS = ('manual_halt', 'kill_switch')
_RESET_REASONS = ('drawdown', 'manual_halt', 'kill_switch', 'ledger_corrupt', 'model_corrupt')
_CASH_FIELDS = frozenset(('previous_account_event_id', 'current_account_event_id',
                          'ledger_before', 'ledger_after', 'amount'))
_MAX_RAW_BYTES = 8192
_MAX_ID_LENGTH = 256


class _InvalidControl(Exception):
    """This class represents one bounded rejected control field and code."""


@dataclass(frozen=True, init=False)
class _ControlRejection:
    """This class represents safe control input failure without raw payload text."""

    field: str
    code: str

    def __init__(self) -> None:
        """Prevent caller-authored rejection facts.

        :returns:             None.
        :raises   TypeError:  Always; normalization constructs rejections.
        """
        raise TypeError('control rejections come from normalize_risk_control')


@dataclass(frozen=True, init=False)
class RiskControlEvent:
    """This class represents a local request whose effect still needs source proof."""

    kind: str
    account_id: str
    source: str
    actor_id: str
    occurred_at: datetime
    expected_risk_revision: str
    reason: str | None
    previous_account_event_id: str | None
    current_account_event_id: str | None
    ledger_before: str | None
    ledger_after: str | None
    amount: Decimal | None
    event_id: str
    raw_ref: str
    received_at: datetime
    raw_bytes: bytes
    content_hash: str

    def __init__(self) -> None:
        """Prevent a request object from becoming caller-selected authority.

        :returns:             None.
        :raises   TypeError:  Always; use normalize_risk_control.
        """
        raise TypeError('risk controls come from normalize_risk_control')


@dataclass(frozen=True, init=False)
class RiskControlValidation:
    """This class represents exactly one normalized request or safe rejection."""

    value: RiskControlEvent | None
    rejection: _ControlRejection | None

    def __init__(self) -> None:
        """Prevent caller-authored normalization outcomes.

        :returns:             None.
        :raises   TypeError:  Always; use normalize_risk_control.
        """
        raise TypeError('control validations come from normalize_risk_control')


def _unique_pairs(pairs):
    """Reject JSON objects with duplicate keys at any nesting depth."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidControl('$', 'duplicate_key')
        result[key] = value
    return result


def _nonfinite(_value):
    """Reject JavaScript nonfinite constants outside strict JSON."""
    raise _InvalidControl('$', 'invalid_json')


def _identity(raw, field):
    """Parse one bounded opaque source/control identity."""
    try:
        value = _parse_string(raw, field)
    except _InvalidInput as failure:
        raise _InvalidControl(*failure.args) from None
    if len(value) > _MAX_ID_LENGTH:
        raise _InvalidControl(field, 'too_long')
    if not value.isprintable():
        raise _InvalidControl(field, 'invalid_value')
    return value


def _timestamp(raw, field):
    """Parse a standard aware external timestamp without a receipt fallback."""
    try:
        value = _parse_timestamp(raw, field)
    except _InvalidInput as failure:
        raise _InvalidControl(*failure.args) from None
    return value


def _parse_control(raw):
    """Return normalized content fields from one exact finite JSON object."""
    if len(raw) > _MAX_RAW_BYTES:
        raise _InvalidControl('$', 'too_large')
    try:
        source = json.loads(raw.decode('utf-8'),
                            object_pairs_hook=_unique_pairs, parse_constant=_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise _InvalidControl('$', 'invalid_json') from None
    if type(source) is not dict:
        raise _InvalidControl('$', 'expected_object')
    if 'schema_version' not in source:
        raise _InvalidControl('schema_version', 'missing')
    if type(source['schema_version']) is not int or source['schema_version'] != 1:
        raise _InvalidControl('schema_version', 'invalid_value')
    if 'kind' not in source:
        raise _InvalidControl('kind', 'missing')
    kind = source.get('kind')
    if type(kind) is not str or kind not in ('halt', 'reset', 'cashflow'):
        raise _InvalidControl('kind', 'invalid_value')
    expected = _BASE_FIELDS | (_CASH_FIELDS if kind == 'cashflow' else {'reason'})
    if source.keys() != expected:
        extra = source.keys() - expected
        missing = expected - source.keys()
        if extra:
            raise _InvalidControl('$', 'unknown_field')
        raise _InvalidControl(sorted(missing)[0], 'missing')
    fields = {key: _identity(source[key], key) for key in ('account_id', 'source', 'actor_id',
                                                           'expected_risk_revision')}
    fields['kind'] = kind
    fields['occurred_at'] = _timestamp(source['occurred_at'], 'occurred_at')
    fields.update(reason=None, previous_account_event_id=None, current_account_event_id=None,
                  ledger_before=None, ledger_after=None, amount=None)
    if kind == 'cashflow':
        for key in _CASH_FIELDS - {'amount'}:
            fields[key] = _identity(source[key], key)
        try:
            amount = _parse_decimal(source['amount'], 'amount')
            _identity_decimal(amount)
        except _InvalidInput as failure:
            raise _InvalidControl(*failure.args) from None
        except _UnsupportedIdentity:
            raise _InvalidControl('amount', 'representation_unsupported') from None
        if amount == 0:
            raise _InvalidControl('amount', 'invalid_value')
        fields['amount'] = amount
    else:
        allowed = _HALT_REASONS if kind == 'halt' else _RESET_REASONS
        reason = _identity(source['reason'], 'reason')
        if reason not in allowed:
            raise _InvalidControl('reason', 'invalid_value')
        fields['reason'] = reason
    identity = dict(schema_version=1, kind=kind, account_id=fields['account_id'],
                    source=fields['source'], actor_id=fields['actor_id'],
                    occurred_at=fields['occurred_at'].isoformat(),
                    expected_risk_revision=fields['expected_risk_revision'])
    if kind == 'cashflow':
        identity.update({key: fields[key] for key in _CASH_FIELDS - {'amount'}})
        identity['amount'] = _identity_decimal(fields['amount'])
    else:
        identity['reason'] = fields['reason']
    return fields, _snapshot_hash(identity)


def normalize_risk_control(raw: bytes, *, event_id: str, raw_ref: str,
                           received_at: datetime) -> RiskControlValidation:
    """Normalize a bounded local request without accepting its claimed effect.

    :param    raw:          Exact UTF-8 JSON bytes from a local control ingress.
    :param    event_id:     Trusted receipt identity used for later conflict checks.
    :param    raw_ref:      Trusted source reference for the retained original bytes.
    :param    received_at:  Trusted aware receipt time.
    :returns:               One parsed event or a safe field/code rejection.
    :raises   TypeError:    If trusted arguments have incorrect exact types.
    :raises   ValueError:   If trusted receipt identity or time is invalid.
    """
    if type(raw) is not bytes:
        raise TypeError('raw must be exact bytes')
    _require_nonempty_string('event_id', event_id)
    _require_nonempty_string('raw_ref', raw_ref)
    if len(event_id) > _MAX_ID_LENGTH:
        raise ValueError('event_id exceeds 256 characters')
    if len(raw_ref) > _MAX_ID_LENGTH:
        raise ValueError('raw_ref exceeds 256 characters')
    received_at = _trusted_datetime('received_at', received_at)
    try:
        fields, content_hash = _parse_control(raw)
    except _InvalidControl as failure:
        return _make(RiskControlValidation, value=None,
                     rejection=_make(_ControlRejection, field=failure.args[0], code=failure.args[1]))
    event = _make(RiskControlEvent, **fields, event_id=event_id, raw_ref=raw_ref,
                  received_at=received_at, raw_bytes=raw, content_hash=content_hash)
    return _make(RiskControlValidation, value=event, rejection=None)


@dataclass(frozen=True, init=False)
class RiskObservation:
    """This class represents freshly rechecked account, mark and bundle evidence."""

    original_context: DecisionContext
    config: StrategyConfig
    now: datetime
    original_bundle: VerifiedBundle | None
    context_recheck: ContextRecheck
    context: DecisionContext | None
    account: AccountSnapshot | None
    session: ExchangeSession | None
    account_assessment: AccountAssessment | None
    account_hash: str | None
    relevant_components: tuple[ContextComponent, ...]
    account_components: tuple[ContextComponent, ...]
    mark_components: tuple[tuple[ContextComponent, ...], ...]
    account_evidence_reasons: tuple[str, ...]
    mark_source_reasons: tuple[str, ...]
    context_reasons: tuple[str, ...]
    context_rejection: object | None
    bundle_assessment: BundleAssessment | None
    bundle_reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Prevent caller-authored source or capital observations.

        :returns:             None.
        :raises   TypeError:  Always; use observe_risk_inputs.
        """
        raise TypeError('risk observations come from observe_risk_inputs')


def observe_risk_inputs(context: DecisionContext, *, config: StrategyConfig, now: datetime,
                        bundle: VerifiedBundle | None = None) -> RiskObservation:
    """Join fresh source, conditional capital, held-mark and optional bundle proof.

    :param    context:  Actual P08 context to recheck against its source manifest.
    :param    config:   Exact current strategy configuration.
    :param    now:      Exact standard aware current decision instant.
    :param    bundle:   Optional actual model or cash bundle for fresh B2 proof.
    :returns:           Reached native evidence and failures, without permission.
    :raises   TypeError: If trusted top-level inputs have incorrect exact types.
    :raises   ValueError: If now is not a standard aware instant.
    """
    if (type(context) is not DecisionContext or type(config) is not StrategyConfig
            or type(now) is not datetime or (bundle is not None and type(bundle) is not VerifiedBundle)):
        raise TypeError('context, config, now and bundle require exact trusted types')
    if type(now.tzinfo) not in (timezone, ZoneInfo):
        raise ValueError('now must have a standard aware timezone')
    checked = recheck_decision_context(context, config=config, now=now)
    fresh = checked.context if checked.valid else None
    account = assessment = ahash = session = None
    relevant = accounts = marks = evidence_reasons = mark_reasons = ()
    if fresh is not None:
        session = fresh.session
        selected = tuple(component for component in fresh.selected_components
                         if component.member.kind == 'account_snapshot'
                         and type(component.value) is AccountSnapshot)
        if len(selected) == 1:
            account = selected[0].value
        assessment, ahash, relevant, accounts, marks, reasons = _context_account_evidence(
            fresh, account, config, now=now)
        evidence_reasons = tuple(reasons)
        if len(selected) != 1:
            code = 'account_source_missing' if not selected else 'account_source_ambiguous'
            evidence_reasons += ('source_admission:account:' + code,)
        mark_reasons = tuple(reason for reason in evidence_reasons
                             if reason == 'source_admission:account:mark_source_unavailable')
    bundle_assessment = None if bundle is None else assess_bundle(
        bundle, context=context, config=config, now=now, requested_use='core_fixture')
    return _make(RiskObservation, original_context=context, config=config, now=now,
                 original_bundle=bundle, context_recheck=checked, context=fresh, account=account,
                 session=session, account_assessment=assessment, account_hash=ahash,
                 relevant_components=tuple(relevant), account_components=tuple(accounts),
                 mark_components=tuple(marks), account_evidence_reasons=evidence_reasons,
                 mark_source_reasons=mark_reasons, context_reasons=checked.reasons,
                 context_rejection=checked.rejection, bundle_assessment=bundle_assessment,
                 bundle_reasons=() if bundle_assessment is None else bundle_assessment.reasons)
