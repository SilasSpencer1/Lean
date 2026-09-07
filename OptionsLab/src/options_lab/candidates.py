"""Compose current candidate evidence and initial selections without execution authority."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json

from ._input_parsing import _InvalidInput, _parse_date
from ._validation import _trusted_datetime
from .account import AccountAssessment, AccountSnapshot, assess_account
from .account_inputs import account_hash, account_snapshot, _contract_snapshot
from .admission import VerifiedFixtureManifest, _canonical_bytes
from .bar_inputs import _UnsupportedIdentity, _identity_decimal, _timestamp_string
from .coherence import CoherenceAssessment, assess_quote_coherence
from .config import StrategyConfig, assess_configured_quote_budget, config_hash, policy_hash, _snapshot_hash
from .context import ContextComponent, DecisionContext
from .context_inputs import ContextInputRejection
from .contracts import ContractId, ContractReferenceAssessment, assess_contract_reference
from .greeks import GreekReadiness, assess_greek_readiness, _method_hash
from .observations import ObservationAssessment
from .quote_content import identify_quote_content, _integer_snapshot, _metadata_snapshot
from .quotes import QuoteObservation, QuotePremiumAssessment, _assess_quote_only
from .sessions import SessionAssessment, assess_session, EntryTimingAssessment, assess_decision_slot
from .ticks import TickAssessment, assess_order_tick
from .underlying import UnderlyingQuote, UnderlyingQuoteAssessment, assess_underlying_quote


_CATEGORIES = (
    'input_integrity', 'context_time/ledger_freshness', 'config_policy_input_binding',
    'source_admission', 'account_connection_reconciliation', 'contract_reference',
    'option_quote', 'underlying_quote', 'greek_readiness', 'coherence', 'account_exposure',
    'current_session', 'dte', 'delta_sign', 'delta_band', 'tick_cap',
    'declared_conservative_equity', 'premium_cap', 'available_cash',
)
_STRUCTURAL = (
    'artifact_not_market_input', 'normalization_failed', 'availability_unknown',
    'source_identity_unknown', 'profile_mismatch', 'target_unknown', 'source_identity_conflict',
    'lineage_invalid', 'lineage_unorderable', 'lineage_cycle', 'lineage_fork',
    'current_roots_conflict', 'stream_order_ambiguous', 'incomplete_context_input_set',
)
_REQUIRED = ('option_quote', 'underlying_quote', 'greek_observation', 'quote_coherence',
             'tick_rule', 'exchange_session', 'instrument_tradability',
             'provider_contract_mapping', 'contract_reference', 'account_snapshot')
_CAPITAL_CATEGORY = {
    **dict.fromkeys(('currency_unsupported', 'ledger_interpretation_unavailable',
                    'duplicate_source_fact_unresolved', 'arithmetic_precision_unsupported', 'account_missing'), 'input_integrity'),
    **dict.fromkeys(('order_execution_accounting_unresolved', 'holdings_incomplete_or_unknown',
                    'orders_incomplete_or_unknown'), 'account_exposure'),
    **dict.fromkeys(('holding_valuation_unavailable', 'virtual_cash_missing', 'virtual_equity_missing',
                    'session_realized_pnl_missing'), 'declared_conservative_equity'),
    **dict.fromkeys(('virtual_settled_cash_missing', 'virtual_reserved_cash_missing', 'broker_settled_cash_missing',
                    'broker_available_cash_missing', 'broker_nonmargin_buying_power_missing'), 'available_cash'),
}
_BUDGET_CATEGORY = {
    'quantity must be exactly one contract': 'input_integrity',
    'virtual equity is undeclared': 'declared_conservative_equity',
    'virtual equity must be positive': 'declared_conservative_equity',
    'premium cap exceeded': 'premium_cap',
    'available cash is unavailable': 'available_cash', 'insufficient available cash': 'available_cash',
}
_MARK_ENVELOPE = ('event_id', 'stream_id', 'receive_sequence', 'supersedes_record_id')


@dataclass(frozen=True, init=False)
class CandidateAssessment:
    """This class represents reached current evidence for one original contract and cap.

    Eligibility is a bounded market/account conclusion, never an order permission.
    All source objects and independent adverse account exposures remain retained.
    """

    contract: ContractId
    context: DecisionContext
    account: AccountSnapshot | None
    config: StrategyConfig
    now: datetime
    original_ask_cap: Decimal | None
    account_assessment: AccountAssessment
    option_quote: QuoteObservation | None
    underlying_quote: UnderlyingQuote | None
    option_observation: ObservationAssessment | None
    option_quote_reasons: tuple[str, ...]
    quote_budget_assessment: QuotePremiumAssessment | None
    underlying_assessment: UnderlyingQuoteAssessment | None
    greek_readiness: GreekReadiness | None
    coherence_assessments: tuple[CoherenceAssessment, ...]
    reference_assessments: tuple[ContractReferenceAssessment, ...]
    session_assessments: tuple[SessionAssessment, ...]
    tick_assessments: tuple[TickAssessment, ...]
    applicable_tick_components: tuple[ContextComponent, ...]
    relevant_components: tuple[ContextComponent, ...]
    account_components: tuple[ContextComponent, ...]
    mark_components: tuple[tuple[ContextComponent, ...], ...]
    config_hash: str
    policy_hash: str
    context_input_digest: str | None
    input_manifest_id: tuple[str, str] | None
    account_hash: str | None
    calendar_dte: int | None
    absolute_delta: Decimal | None
    reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Reject caller-authored assessment outcomes.

        :returns: None.
        :raises TypeError: Always; use assess_candidate.
        """
        raise TypeError('CandidateAssessment values come from assess_candidate')

    @property
    def eligible(self) -> bool:
        """Report whether all reached candidate requirements passed.

        :returns: True exactly when no retained candidate reason exists.
        """
        return not self.reasons

    @property
    def required_cash(self) -> Decimal | None:
        """Expose the existing premium owner's current original-cap amount.

        :returns: Actual required cash, or None when its calculation was unavailable.
        """
        joined = self.quote_budget_assessment
        return None if joined is None or joined.budget is None else joined.budget.required_cash


def _ordered(reasons):
    """Group closed qualified reasons, retaining first owner order and deduplicating.

    :param reasons: Internally composed category/owner/code strings.
    :returns: Immutable reasons in the fixed candidate category order.
    """
    return tuple(dict.fromkeys(reason for category in _CATEGORIES for reason in reasons
                               if reason.split(':', 1)[0] == category))


def _source_reasons(components):
    """Project structural failures without importing unrelated market/history policy.

    :param components: Actual owner-retained relevant source components.
    :returns: Reached structural and unresolved-scope reasons in component order.
    """
    reasons = []
    for component in components:
        if component.disposition == 'future':
            continue
        reasons.extend('source_admission:context:'+code for code in component.reasons if code in _STRUCTURAL)
        if component.disposition in ('omitted', 'unresolved'):
            reasons.append('source_admission:candidate:source_scope_unresolved')
    return reasons


def _target(component):
    """Read actual retained target framing even when normalization failed.

    :param component: Actual admitted component with partial owner normalization.
    :returns: Known exact contract/scalar/calendar target, or None for unknown scope.
    """
    kind = component.member.kind
    if kind not in ('underlying_quote', 'account_snapshot', 'exchange_session'):
        return component.contract
    body = component.member.decode_raw_body()
    value = body.get({'underlying_quote': 'symbol', 'account_snapshot': 'account_id', 'exchange_session': 'calendar'}[kind])
    if type(value) is not str or not value:
        return None
    if kind == 'exchange_session':
        try:
            return value, _parse_date(body.get('session_date'), 'session_date')
        except _InvalidInput:
            return None
    return value


def _relevant(context, account, config, contract=None):
    """Retain known matching streams and causal unknown required targets.

    Scope is the context owner's inspected inventory; this never resolves or
    admits another member. The finite fixture scope makes a direct scan sufficient.

    :param context: Actual complete inspected context inventory.
    :param account: Supplied account establishing account and held-mark targets.
    :param config: Exact configured calendar and underlying policy.
    :param contract: Optional candidate target; None requests only common evidence.
    :returns: Actual causal components in stable owner order.
    """
    held = () if account is None else tuple(h.mark_quote.contract for h in account.holdings if h.mark_quote is not None)
    matched, unknown = [], []
    for c in context.components:
        kind = c.member.kind
        if c.disposition == 'future' or kind not in _REQUIRED:
            continue
        target = _target(c)
        if target is None:
            unknown.append(c)
        match = (kind == 'account_snapshot' and (account is None or target == account.account_id))
        match |= (kind == 'exchange_session' and target is not None and target[0] == config.strategy_calendar
                  and (context.session is None or target[1] == context.session.session_date))
        match |= kind == 'option_quote' and target in held
        if contract is not None:
            match |= kind == 'underlying_quote' and target == contract.underlying
            match |= kind not in ('underlying_quote', 'account_snapshot', 'exchange_session') and target == contract
        if match:
            matched.append(c)
    streams = {c.member.decode_envelope()['stream_id'] for c in matched}
    return tuple(c for c in context.components if c.disposition != 'future' and (
        c in unknown or (c.member.kind in _REQUIRED and c.member.decode_envelope()['stream_id'] in streams)
        or (c.requested and 'artifact_not_market_input' in c.reasons)))


def _bound(component, context):
    """Require actual selected component and actual contained member objects.

    :param component: Actual source occurrence being consumed.
    :param context: Factory-produced owner context and verified manifest.
    :returns: Whether the same component/member objects establish selected authority.
    """
    return (context.manifest is not None and component.disposition == 'selected'
            and any(component is c for c in context.selected_components)
            and any(component is c for c in context.components)
            and any(component.member is m for m in context.manifest.members))


def _mark_components(context, account, account_components, common):
    """Bind holding-order exact requested occurrences and owner-selected aliases.

    :param context: Actual context supplying final alias associations.
    :param account: Retained supplied account, including every holding and mark.
    :param account_components: Complete relevant account source evidence.
    :param common: Inspected common streams, including adverse held-mark siblings.
    :returns: One evidence tuple per holding and reached mark-binding failures.
    """
    bound_accounts = [c for c in account_components if _bound(c, context) and c.value == account]
    raw_holdings = bound_accounts[0].member.decode_raw_body()['holdings'] if len(bound_accounts) == 1 else None
    evidence, reasons = [], []
    for index, holding in enumerate(account.holdings):
        mark, matches, representatives = holding.mark_quote, [], []
        requested_representatives = []
        valid = raw_holdings is not None
        nested = None if raw_holdings is None else raw_holdings[index]['mark_quote']
        for c in common:
            if mark is None or nested is None or c.member.kind != 'option_quote' or c.value != mark:
                continue
            env = c.member.decode_envelope()
            if any(env[key] != nested['envelope'][key] for key in _MARK_ENVELOPE):
                continue
            matches.append(c)
            representative = c if c.disposition == 'selected' else next((other for other in context.components
                if other.member.record_id == c.representative_record_id), None) if c.disposition == 'duplicate' else None
            if representative is not None:
                representatives.append(representative)
            # Unrequested exact retransmissions stay audit evidence, not binding votes.
            if not c.requested:
                continue
            if representative is not None:
                requested_representatives.append(representative)
            valid &= (any(c.member is m for m in context.manifest.members)
                      and representative is not None and representative.member.kind == 'option_quote'
                      and _bound(representative, context) and not _source_reasons((c, representative)))
        if mark is not None:
            streams = {c.member.decode_envelope()['stream_id'] for c in (*matches, *representatives)}
            valid &= not _source_reasons(tuple(c for c in common if c.member.decode_envelope()['stream_id'] in streams))
            valid &= len({id(c) for c in requested_representatives}) == 1
            if not valid:
                reasons.append('source_admission:account:mark_source_unavailable')
        evidence.append(tuple(c for c in context.components if any(c is item for item in (*matches, *representatives))))
    return tuple(evidence), reasons


def _context_account_evidence(context, account, config, *, now):
    """Derive six concrete shared facts for candidate and empty-chain consumers.

    :param context: Exact present context produced by the context owner.
    :param account: Exact supplied account, or explicit absence.
    :param config: Exact strategy configuration to bind and assess.
    :param now: Explicit aware current time, never read from a wall clock.
    :returns: Account assessment/hash, common/account/holding components, ordered reasons.
    :raises TypeError: If trusted argument types are incorrect.
    :raises ValueError: If time or an unexpected account identity failure is invalid.
    """
    for name, value, expected in (('context', context, DecisionContext), ('config', config, StrategyConfig)):
        if type(value) is not expected:
            raise TypeError(name+' must have its exact trusted type')
    if account is not None and type(account) is not AccountSnapshot:
        raise TypeError('account must be an exact AccountSnapshot or None')
    now = _trusted_datetime('now', now)
    assessment = assess_account(account, session=context.session, config=config, now=now)
    reasons, ahash = [], None
    for group, category in ((assessment.integrity_reasons, 'input_integrity'),
        (assessment.time_reasons, 'context_time/ledger_freshness'),
        (assessment.reconciliation_reasons, 'account_connection_reconciliation'),
        (assessment.exposure_reasons, 'account_exposure'), (assessment.session_reasons, 'current_session')):
        reasons.extend(category+':account:'+code for code in group)
    reasons.extend(_CAPITAL_CATEGORY[code]+':account:'+code for code in assessment.capital_reasons)
    if not assessment.observed_flat:
        reasons.append('account_exposure:account:not_observed_flat')
    if assessment.conservative_virtual_equity is None:
        reasons.append('declared_conservative_equity:account:conservative_virtual_equity_missing')
    if assessment.effective_available_cash is None:
        reasons.append('available_cash:account:effective_available_cash_missing')
    if account is not None:
        try:
            ahash = account_hash(account)
        except ValueError as error:
            if str(error) != 'account_identity_representation_unsupported':
                raise
            reasons.append('input_integrity:account:account_identity_representation_unsupported')
    if context.decision_at != now:
        reasons.append('context_time/ledger_freshness:context:context_time_mismatch')
    for code in ('manifest_missing', 'unknown_member'):
        if code in context.input_reasons:
            reasons.append('input_integrity:context:'+code)
    reasons.extend('input_integrity:context:'+r.code for r in context.rejections if type(r) is ContextInputRejection)
    manifest = context.manifest
    if type(manifest) is not VerifiedFixtureManifest:
        reasons.append('config_policy_input_binding:context:manifest_missing')
    else:
        if context.input_manifest_id != (manifest.fixture_id, manifest.payload_sha256):
            reasons.append('config_policy_input_binding:context:manifest_mismatch')
        for owner in (context, manifest):
            if (owner.origin, owner.fidelity_tier, owner.permitted_use, owner.operational_allowed, owner.economic_allowed) != ('synthetic', 0, 'core_fixture', False, False):
                reasons.append('config_policy_input_binding:context:origin_mismatch')
    if context.input_digest is None:
        reasons.append('config_policy_input_binding:context:context_input_digest_missing')
    if context.config_hash != config_hash(config) or context.timing.session_assessment.config != config:
        reasons.append('config_policy_input_binding:context:config_mismatch')
    if context.policy_hash != policy_hash(config):
        reasons.append('config_policy_input_binding:context:policy_mismatch')
    common = _relevant(context, account, config)
    reasons.extend(_source_reasons(common))
    accounts = tuple(c for c in common if c.member.kind == 'account_snapshot')
    current = [c for c in accounts if c.disposition == 'selected' and type(c.value) is AccountSnapshot
               and account is not None and c.value.account_id == account.account_id]
    if account is not None:
        if len(current) != 1:
            reasons.append('source_admission:account:account_source_missing' if not current else 'source_admission:account:account_source_ambiguous')
        elif not _bound(current[0], context) or current[0].value != account:
            reasons.append('source_admission:account:account_source_mismatch')
    marks, mark_reasons = ((), []) if account is None else _mark_components(context, account, accounts, common)
    reasons.extend(mark_reasons)
    return assessment, ahash, common, accounts, marks, _ordered(reasons)


def _current(values, predicate, context, relevant, owner, reasons):
    """Retain the owner's exact current values and require actual source support.

    :param values: Existing owner-selected market values.
    :param predicate: Exact candidate target comparison.
    :param context: Actual factory-produced context.
    :param relevant: Actual relevant component inventory.
    :param owner: Fixed internal owner token for source diagnostics.
    :param reasons: Local reached-reason list to extend.
    :returns: Every matching current value without favorable filtering.
    """
    selected = tuple(value for value in values if predicate(value))
    for value in selected:
        matches = [c for c in relevant if c.value is value and _bound(c, context)]
        if not matches:
            reasons.append('source_admission:'+owner+':selected_source_missing')
    return selected


def _unique(values, category, owner, reasons):
    """Reject missing or competing current identities without selecting a favorite.

    :param values: Complete potentially applicable current value tuple.
    :param category: Fixed internal candidate category.
    :param owner: Fixed internal owner name.
    :param reasons: Local list receiving missing or competing identity evidence.
    :returns: The sole actual value, or None when identity cannot be selected.
    """
    if len(values) != 1:
        reasons.append(category+':'+owner+(':'+owner+'_missing' if not values else ':'+owner+'_ambiguous'))
        return None
    return values[0]


def _potential(value, now, *, open_end=False):
    """Exclude only a structurally valid interval proving current nonapplicability.

    :param value: Actual current reference or instrument status.
    :param now: Validated current time.
    :param open_end: Whether the reference owner permits a null upper endpoint.
    :returns: True when this claim could apply, including unknown/invalid intervals.
    """
    start, end = value.effective_from, value.effective_until
    valid = start is not None and ((end is None and open_end) or (end is not None and end > start))
    return not valid or (start <= now and (end is None or now < end))


def assess_candidate(contract: ContractId, context: DecisionContext, account: AccountSnapshot | None,
                     config: StrategyConfig, *, now: datetime, original_ask_cap: Decimal | None) -> CandidateAssessment:
    """Assess one original contract at the exact current context time and explicit cap.

    All reached owner evidence is retained, including independent quote checks when
    account, fee, or cap inputs prevent joined budgeting. No initial grid, feature,
    rank, original intent expiry, reservation ceiling, or risk latch is evaluated.

    :param contract: Exact original option identity; no substitution is performed.
    :param context: Actual factory-produced current context and source manifest.
    :param account: Exact supplied account or missing-state evidence.
    :param config: Exact configuration used by every applicable owner.
    :param now: Explicit aware current timestamp, normalized to UTC.
    :param original_ask_cap: Exact finite original cap, or retained missing evidence.
    :returns: Frozen complete reached assessment with ordered qualified failures.
    :raises TypeError: If a trusted argument has an incorrect exact type.
    :raises ValueError: If a trusted time/cap is invalid or an unexpected owner fails.
    """
    if type(contract) is not ContractId:
        raise TypeError('contract must be an exact ContractId')
    if original_ask_cap is not None:
        if type(original_ask_cap) is not Decimal:
            raise TypeError('original_ask_cap must be an exact Decimal or None')
        if not original_ask_cap.is_finite():
            raise ValueError('original_ask_cap must be finite')
    now = _trusted_datetime('now', now)
    a, ahash, _, accounts, marks, common_reasons = _context_account_evidence(context, account, config, now=now)
    reasons = list(common_reasons)
    relevant = _relevant(context, account, config, contract)
    reasons.extend(_source_reasons(relevant))
    def current(values, predicate, owner):
        """Bind one concrete owner's current values to this candidate's sources.

        :param values: Actual context-selected owner values.
        :param predicate: Exact target comparison.
        :param owner: Fixed source diagnostic owner.
        :returns: All matching values, retaining any binding failure.
        """
        return _current(values, predicate, context, relevant, owner, reasons)
    option = _unique(current(context.option_quotes, lambda q: q.contract == contract, 'quote'), 'option_quote', 'quote', reasons)
    underlying = _unique(current(context.underlying_quotes, lambda q: q.symbol == contract.underlying, 'underlying'), 'underlying_quote', 'underlying', reasons)
    greek = _unique(current(context.greeks, lambda g: g.contract == contract, 'greek'), 'greek_readiness', 'greek', reasons)
    mappings = current(context.provider_mappings, lambda m: m.contract == contract, 'mapping')
    _unique(mappings, 'contract_reference', 'mapping', reasons)
    references = current(context.contract_references, lambda r: r.contract == contract, 'reference')
    applicable_references = tuple(r for r in references if _potential(r, now, open_end=True))
    _unique(applicable_references, 'contract_reference', 'reference', reasons)
    reference_results = tuple(assess_contract_reference(m, r, decision_at=now) for r in references for m in mappings)
    reasons.extend('contract_reference:reference:'+code for r in reference_results
                   if r.reference in applicable_references for code in r.reasons)
    observation, quote_reasons, budget, underlying_result, readiness = None, (), None, None, None
    if option is not None:
        identity = identify_quote_content(option)
        reasons.extend('input_integrity:quote:'+code for code in identity.reasons)
        observation, quote_reasons = _assess_quote_only(option, decision_at=now, max_quote_age=config.execution.max_quote_age,
            max_spread_fraction=config.max_spread_fraction, spread_floor=config.spread_floor)
        reasons.extend('option_quote:quote:'+code for code in (*observation.live_quote_reasons, *quote_reasons))
        if original_ask_cap is not None and original_ask_cap > 0 and option.ask is not None and option.ask > original_ask_cap:
            reasons.append('tick_cap:quote:ask_exceeds_original_cap')
        fee = None if account is None else account.applicable_round_trip_fees
        if original_ask_cap is not None and (fee is None or fee >= 0):
            budget = assess_configured_quote_budget(config, option, decision_at=now,
                virtual_equity=a.conservative_virtual_equity, available_cash=a.effective_available_cash,
                applicable_round_trip_fees=Decimal(0) if fee is None else fee, original_ask_cap=original_ask_cap)
            for code in budget.quote_reasons:
                category = 'tick_cap' if code in ('original_ask_cap_nonpositive', 'ask_exceeds_original_cap') else 'option_quote'
                reasons.append(category+':quote:'+code)
            if budget.budget is not None and budget.budget.reason is not None:
                reasons.append(_BUDGET_CATEGORY[budget.budget.reason]+':premium:'+budget.budget.reason)
    if underlying is not None:
        reasons.extend('input_integrity:underlying:'+code for code in identify_quote_content(underlying).reasons)
        underlying_result = assess_underlying_quote(underlying, decision_at=now, max_quote_age=config.execution.max_quote_age)
        reasons.extend('underlying_quote:underlying:'+code for code in
                       (*underlying_result.observation.live_quote_reasons, *underlying_result.quote_reasons))
    proofs = current(context.coherence_evidence, lambda p: p.contract == contract, 'coherence')
    _unique(proofs, 'coherence', 'coherence', reasons)
    coherence_results = ()
    if option is not None and underlying is not None and context.manifest is not None:
        readiness = assess_greek_readiness(greek, option, underlying, method=context.manifest.greek_method, decision_at=now)
        reasons.extend('greek_readiness:greek:'+code for code in readiness.reasons)
        coherence_results = tuple(assess_quote_coherence(p, option, underlying, manifest=context.manifest,
            decision_at=now, max_quote_age=config.execution.max_quote_age) for p in proofs)
        reasons.extend('coherence:coherence:'+code for p in coherence_results for code in p.reasons)
        if greek is None or greek.as_of is None or not any(p.supports_instant(greek.as_of) for p in coherence_results):
            reasons.append('coherence:coherence:greek_coherence_missing')
    statuses = current(context.tradability, lambda s: s.contract is None or s.contract == contract, 'instrument')
    applicable_statuses = tuple(s for s in statuses if _potential(s, now))
    if len(applicable_statuses) > 1:
        reasons.append('current_session:instrument:instrument_ambiguous')
    sessions = tuple(assess_session(context.session, s, config=config, now=now) for s in (applicable_statuses or (None,)))
    reasons.extend('current_session:session:'+code for s in sessions for code in s.entry_reasons)
    if context.session is not None and not any(c.value is context.session and _bound(c, context) for c in relevant):
        reasons.append('source_admission:session:selected_source_missing')
    dte = None if context.session is None else (contract.expiry-context.session.session_date).days
    if dte is None or not config.min_dte <= dte <= config.max_dte:
        reasons.append('dte:candidate:dte_missing' if dte is None else 'dte:candidate:dte_out_of_range')
    delta = None if greek is None or greek.delta is None else greek.delta.copy_abs()
    if greek is not None and greek.delta is not None:
        if (contract.right == 'call' and greek.delta <= 0) or (contract.right == 'put' and greek.delta >= 0):
            reasons.append('delta_sign:candidate:delta_sign_mismatch')
        if not config.min_abs_delta <= delta <= config.max_abs_delta:
            reasons.append('delta_band:candidate:delta_out_of_range')
    ticks = current(context.tick_rules, lambda t: t.contract == contract, 'tick')
    tick_results, applicable_ticks = (), ()
    if original_ask_cap is None or original_ask_cap <= 0:
        reasons.append('tick_cap:candidate:original_ask_cap_missing' if original_ask_cap is None else 'tick_cap:candidate:original_ask_cap_nonpositive')
    elif context.manifest is not None:
        tick_results = tuple(assess_order_tick(t, manifest=context.manifest, contract=contract,
            price=original_ask_cap, decision_at=now) for t in ticks)
        potential = tuple(t for t in tick_results if not {'price_outside_band', 'effective_not_applicable'}.intersection(t.reasons))
        applicable_ticks = tuple(c for c in relevant if any(c.value is t.rule for t in potential) and _bound(c, context))
        if len(potential) != 1:
            reasons.append('tick_cap:tick:tick_rule_missing' if not potential else 'tick_cap:tick:tick_rule_ambiguous')
        reasons.extend('tick_cap:tick:'+code for t in potential for code in t.reasons)
    result = object.__new__(CandidateAssessment)
    values = dict(contract=contract, context=context, account=account, config=config, now=now, original_ask_cap=original_ask_cap,
        account_assessment=a, option_quote=option, underlying_quote=underlying, option_observation=observation,
        option_quote_reasons=quote_reasons, quote_budget_assessment=budget, underlying_assessment=underlying_result,
        greek_readiness=readiness, coherence_assessments=coherence_results, reference_assessments=reference_results,
        session_assessments=sessions, tick_assessments=tick_results, applicable_tick_components=applicable_ticks,
        relevant_components=relevant, account_components=accounts, mark_components=marks,
        config_hash=config_hash(config), policy_hash=policy_hash(config), context_input_digest=context.input_digest,
        input_manifest_id=context.input_manifest_id, account_hash=ahash, calendar_dte=dte, absolute_delta=delta, reasons=_ordered(reasons))
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


_DECISION_CODES = ('missed_decision', 'decision_in_future', 'time_conversion_unsupported',
    'decision_off_grid', 'decision_wrong_strategy_date', 'decision_outside_configured_entry_window',
    'time_arithmetic_unsupported')
_INITIAL_CODES = (*_DECISION_CODES, 'session_missing', 'calendar_unsupported', 'session_wrong_date',
    'session_availability_unknown', 'session_available_after_now', 'session_availability_not_measured',
    'session_closed', 'session_unknown', 'session_not_regular', 'session_hours_missing', 'session_hours_reversed',
    'session_hours_wrong_date', 'instrument_missing', 'instrument_unresolved', 'instrument_wrong_date',
    'instrument_availability_unknown', 'instrument_available_after_now', 'instrument_availability_not_measured',
    'instrument_hours_missing', 'instrument_hours_reversed', 'instrument_hours_wrong_date', 'instrument_hours_incompatible',
    'operability_interval_unknown', 'operability_interval_invalid', 'operability_not_yet_effective', 'operability_expired',
    'instrument_closed', 'instrument_halted', 'instrument_status_unknown', 'no_common_interval',
    'now_outside_common_interval', 'outside_configured_entry_window', 'insufficient_time_before_liquidation')
_SELECTION_ARITHMETIC = 'input_integrity:selection:arithmetic_precision_unsupported'


def _selection_definition_snapshot():
    """Return a fresh closed definition for this selector and later artifact binding.

    :returns: Explicit versioned rule semantics, independent of config display text.
    """
    return dict(record_kind='options_lab.candidate_selection_rule', schema_version=1,
        vocabulary='abs-delta-distance-spread-expiry-contract-v1', rights=['call', 'put'],
        ascending_keys=['abs(abs(delta)-1/2)', '2*(ask-bid)/(ask+bid)', 'expiry',
                        ['underlying', 'expiry', 'right', 'numeric_strike', 'integer_multiplier', 'deliverable_id']],
        target=[1, 2], original_ask_cap='sole actual selected quote ask, otherwise null',
        arithmetic=dict(encoding='reduced exact integer numerator/denominator, base10 limbs',
            canonical_leaf_digits=1000, leaf_bits=3322, sum_product_bits=6644, sum_numerator_bits=6645,
            doubled_difference_bits=6646, spread_numerator_bits=13290, spread_denominator_bits=13289,
            comparison_product_bits=26580, delta_numerator_bits=3325, delta_denominator_bits=3324,
            defensive_limb_bits=13300, derived_integer_bits_less_than=27000),
        inventory='known causal OPTION selected/omitted/unresolved/audit; deduplicate exact six fields',
        timing='P04 initial decision plus every retained B current session; matching nonnull slots',
        eligibility='one assess_candidate per known contract; no model or feature fallback',
        rank='1-based independently per right; first only; retain all lower ranks',
        dispositions=['ineligible', 'selected', 'not_selected_by_rank', 'selection_unavailable'],
        reasons=['input_integrity:selection:context_missing', _SELECTION_ARITHMETIC,
            'current_session:selection:initial_slot_mismatch', 'current_session:selection:initial_timing_unavailable',
            *('current_session:initial_timing:'+code for code in _INITIAL_CODES)],
        headline_precedence=['context_missing', 'context_invalid', 'account_unavailable', 'source_unavailable',
            'account_exposure', 'market_unavailable', 'arithmetic_precision_unsupported', 'empty_chain', 'no_eligible_candidates'],
        governing_initial_codes=list(_DECISION_CODES),
        failure='unsupported complete identity clears both winners and hash; preserve every reached input and rank',
        origin='synthetic', fidelity_tier=0, permitted_use='core_fixture', operational_allowed=False, economic_allowed=False)


SELECTION_RULE_ID = _snapshot_hash(_selection_definition_snapshot())


@dataclass(frozen=True, init=False)
class CandidateConsideration:
    """This class represents one unchanged assessment and derived initial rank evidence."""

    contract: ContractId
    assessment: CandidateAssessment
    initial_timing: tuple[EntryTimingAssessment, ...]
    rank: int | None
    rank_evidence: tuple[int, int, int, int] | None
    reasons: tuple[str, ...]
    disposition: str

    def __init__(self):
        """Reject authored ranking results.

        :returns: None.
        :raises TypeError: Always; use select_candidates.
        """
        raise TypeError('CandidateConsideration values come from select_candidates')


@dataclass(frozen=True, init=False)
class CandidateSet:
    """This class represents immutable initial choices and their complete causal audit."""

    context: DecisionContext | None
    account: AccountSnapshot | None
    config: StrategyConfig
    decision_id: str | None
    as_of: datetime | None
    slot_key: str | None
    initial_timing: EntryTimingAssessment | None
    considered: tuple[CandidateConsideration, ...]
    selected_call: CandidateAssessment | None
    selected_put: CandidateAssessment | None
    account_assessment: AccountAssessment | None
    common_components: tuple[ContextComponent, ...]
    account_components: tuple[ContextComponent, ...]
    mark_components: tuple[tuple[ContextComponent, ...], ...]
    config_hash: str
    policy_hash: str
    context_input_digest: str | None
    input_manifest_id: tuple[str, str] | None
    account_hash: str | None
    selection_rule_id: str
    reasons: tuple[str, ...]
    selection_reason: str | None
    snapshot_bytes: bytes | None
    content_hash: str | None
    origin: str
    fidelity_tier: int
    permitted_use: str
    operational_allowed: bool
    economic_allowed: bool

    def __init__(self):
        """Reject authored selection or hash authority.

        :returns: None.
        :raises TypeError: Always; use select_candidates.
        """
        raise TypeError('CandidateSet values come from select_candidates')

    @property
    def selected(self):
        """Return actual selected assessments in call, put order.

        :returns: At most two retained assessments; no execution permission.
        """
        return tuple(a for a in (self.selected_call, self.selected_put) if a is not None)

    def snapshot(self):
        """Decode a fresh complete audit without exposing mutable internal storage.

        :returns: Fresh dictionary or None when complete identity was unsupported.
        """
        return None if self.snapshot_bytes is None else json.loads(self.snapshot_bytes)


def _contract_key(contract):
    """Return the numeric six-field ordering after the caller's bounded preflight.

    :param contract: Exact supported option identity.
    :returns: Canonical comparison tuple; no provider symbol or arrival order.
    """
    return (contract.underlying, contract.expiry, contract.right, contract.strike,
            contract.multiplier, contract.deliverable_id)


def _selection_rank(delta, bid, ask, contract):
    """Compute the fixed exact key only after every canonical leaf is bounded.

    Canonical leaves are <=3322 bits. Sum products are <=6644, sum numerators
    <=6645, doubled differences <=6646. Spread division forms <=13290/13289
    bit limbs; reduction cannot enlarge them. Sort cross-products are <=26580
    bits. Delta distance limbs are <=3325/3324 bits. Thus every allocation in
    this fixed expression is bounded in advance below 27000 bits.

    :param delta: Actual original signed Greek delta.
    :param bid: Actual original option bid.
    :param ask: Actual original option ask.
    :param contract: Exact option identity, also preflighted before arithmetic.
    :returns: Private ordering key and four reduced rational limbs.
    :raises _UnsupportedIdentity: If an operand or derived limb exceeds its bound.
    """
    _contract_snapshot(contract)
    leaves = tuple(_identity_decimal(value) for value in (delta, bid, ask, contract.strike))
    if any(value is None for value in leaves):
        raise _UnsupportedIdentity
    delta, bid, ask = (Fraction(Decimal(value)) for value in leaves[:3])
    if ask + bid == 0:
        raise _UnsupportedIdentity
    distance, spread = abs(abs(delta)-Fraction(1, 2)), 2*(ask-bid)/(ask+bid)
    evidence = (distance.numerator, distance.denominator, spread.numerator, spread.denominator)
    if any(abs(limb).bit_length() > 13300 for limb in evidence):
        raise _UnsupportedIdentity
    return (distance, spread, contract.expiry, _contract_key(contract)), evidence


def _duration_us(value):
    """Project an exact duration without float conversion.

    :param value: Actual owner timedelta or None.
    :returns: Exact integer microseconds or None.
    """
    return None if value is None else (value.days*86400+value.seconds)*1000000+value.microseconds


def _record_ids(components):
    """Retain actual occurrence references in their owner's evidence order.

    :param components: Concrete context component tuple.
    :returns: Original member identifiers, including duplicate occurrences.
    """
    return [c.member.record_id for c in components]


def _owner_records(context, value):
    """Associate a reached owner object with its actual causal source occurrences.

    :param context: Actual context or None for missing context.
    :param value: Actual retained owner input; no equality substitution is performed.
    :returns: Causal member identifiers matching the exact object.
    """
    return [] if context is None or value is None else _record_ids(tuple(c for c in context.components
        if c.disposition != 'future' and c.value is value))


def _session_snapshot(s, context):
    """Project every derived session group, clock and original source association.

    :param s: Actual SessionAssessment.
    :param context: Actual context for source association.
    :returns: Complete explicit derived session evidence.
    """
    session, instrument = s.session, s.tradability
    return dict(session_records=_owner_records(context, session), instrument_records=_owner_records(context, instrument),
        session=None if session is None else dict(calendar=session.calendar, session_date=session.session_date.isoformat(),
            kind=session.kind, opens_at=_timestamp_string(session.opens_at), closes_at=_timestamp_string(session.closes_at),
            available_at=_timestamp_string(session.available_at), received_at=_timestamp_string(session.received_at),
            source=session.source, provider_record_id=session.provider_record_id, source_version=session.source_version,
            availability_basis=session.availability_basis, fidelity=session.fidelity, raw_ref=session.raw_ref),
        instrument=None if instrument is None else dict(contract=_contract_snapshot(instrument.contract),
            instrument_ref=instrument.instrument_ref, session_date=instrument.session_date.isoformat(), status=instrument.status,
            opens_at=_timestamp_string(instrument.opens_at), closes_at=_timestamp_string(instrument.closes_at),
            effective_from=_timestamp_string(instrument.effective_from), effective_until=_timestamp_string(instrument.effective_until),
            available_at=_timestamp_string(instrument.available_at), received_at=_timestamp_string(instrument.received_at),
            source=instrument.source, provider_record_id=instrument.provider_record_id, source_version=instrument.source_version,
            availability_basis=instrument.availability_basis, fidelity=instrument.fidelity, raw_ref=instrument.raw_ref),
        config_hash=s.config_hash, now=_timestamp_string(s.now), session_reasons=list(s.session_reasons),
        instrument_hours_reasons=list(s.instrument_hours_reasons), operability_reasons=list(s.operability_reasons),
        entry_reasons=list(s.entry_reasons), common_opens_at=_timestamp_string(s.common_opens_at),
        common_closes_at=_timestamp_string(s.common_closes_at), configured_entry_start=_timestamp_string(s.configured_entry_start),
        configured_entry_end=_timestamp_string(s.configured_entry_end), recovery_basis=s.recovery_basis,
        effective_liquidation_start=_timestamp_string(s.effective_liquidation_start),
        effective_liquidation_escalation=_timestamp_string(s.effective_liquidation_escalation),
        effective_liquidation_deadline=_timestamp_string(s.effective_liquidation_deadline))


def _timing_snapshot(t, context):
    """Project original P04 timing with its truthful underlying session evidence.

    :param t: Actual initial timing or None.
    :param context: Actual context for source association.
    :returns: Explicit original timing snapshot or None.
    """
    return None if t is None else dict(operation=t.operation, decision_at=_timestamp_string(t.decision_at),
        now=_timestamp_string(t.now), original_expires_at=_timestamp_string(t.original_expires_at),
        config_hash=t.config_hash, slot_key=t.slot_key, reasons=list(t.reasons),
        session_assessment=_session_snapshot(t.session_assessment, context))


def _observation_snapshot(o):
    """Preserve the independent metadata owner's reached reason groups.

    :param o: Actual observation assessment or None.
    :returns: Explicit availability/live reasons; original metadata stays in source roots.
    """
    return None if o is None else dict(metadata={**_metadata_snapshot(o.meta),
        'received_at': _timestamp_string(o.meta.received_at), 'raw_ref': o.meta.raw_ref},
        availability_reasons=list(o.availability_reasons), live_quote_reasons=list(o.live_quote_reasons))


def _account_assessment_snapshot(a):
    """Project conservative amounts and each holding's actual observed/stress basis.

    :param a: Actual shared account assessment or None when time is unknown.
    :returns: Explicit derived account evidence with original holding order.
    :raises _UnsupportedIdentity: If a reached monetary amount is unrepresentable.
    """
    if a is None:
        return None
    return dict(now=_timestamp_string(a.now), strategy_date=None if a.strategy_date is None else a.strategy_date.isoformat(),
        integrity_reasons=list(a.integrity_reasons), time_reasons=list(a.time_reasons), session_reasons=list(a.session_reasons),
        reconciliation_reasons=list(a.reconciliation_reasons), exposure_reasons=list(a.exposure_reasons),
        capital_reasons=list(a.capital_reasons), account_age_us=_duration_us(a.account_age),
        reconciliation_age_us=_duration_us(a.reconciliation_age), ledger_revision_matches=a.ledger_revision_matches,
        has_nonzero_holding=a.has_nonzero_holding, has_unknown_holding_quantity=a.has_unknown_holding_quantity,
        has_open_order_records=a.has_open_order_records, execution_uncertain=a.execution_uncertain, observed_flat=a.observed_flat,
        unrealized_pnl=_identity_decimal(a.unrealized_pnl), conservative_daily_pnl=_identity_decimal(a.conservative_daily_pnl),
        computed_liquidation_equity=_identity_decimal(a.computed_liquidation_equity),
        conservative_virtual_equity=_identity_decimal(a.conservative_virtual_equity),
        virtual_unencumbered_cash=_identity_decimal(a.virtual_unencumbered_cash), effective_available_cash=_identity_decimal(a.effective_available_cash),
        holding_marks=[dict(holding_index=i, now=_timestamp_string(m.now), observation=_observation_snapshot(m.observation),
            holding_reasons=list(m.holding_reasons), mark_reasons=list(m.mark_reasons), cost_reasons=list(m.cost_reasons),
            valuation_basis=m.valuation_basis, gross_liquidation_value=_identity_decimal(m.gross_liquidation_value),
            net_liquidation_value=_identity_decimal(m.net_liquidation_value), unrealized_pnl=_identity_decimal(m.unrealized_pnl))
            for i, m in enumerate(a.holding_marks)])


def _consideration_snapshot(row):
    """Project unchanged B owners, exact rank inputs, timing and source associations.

    :param row: Locally derived consideration fields before final publication.
    :returns: Explicit complete reached candidate audit.
    :raises _UnsupportedIdentity: If any retained numerical evidence is unrepresentable.
    """
    a = row['assessment']
    q, g, u, b = a.option_quote, a.greek_readiness, a.underlying_assessment, a.quote_budget_assessment
    budget = None if b is None else b.budget
    return dict(contract=_contract_snapshot(a.contract), now=_timestamp_string(a.now), original_ask_cap=_identity_decimal(a.original_ask_cap),
        account_hash=a.account_hash, config_hash=a.config_hash, policy_hash=a.policy_hash,
        context_input_digest=a.context_input_digest, input_manifest_id=a.input_manifest_id,
        calendar_dte=a.calendar_dte, absolute_delta=_identity_decimal(a.absolute_delta),
        account_records=_record_ids(a.account_components), mark_records=[_record_ids(cs) for cs in a.mark_components],
        relevant_records=_record_ids(a.relevant_components), applicable_tick_records=_record_ids(a.applicable_tick_components),
        assessment_reasons=list(a.reasons), reasons=list(row['reasons']), rank=row['rank'], disposition=row['disposition'],
        rank_evidence=None if row['rank_evidence'] is None else [_integer_snapshot(n) for n in row['rank_evidence']],
        initial_timing=[_timing_snapshot(t, a.context) for t in row['initial_timing']],
        quote=None if q is None else dict(records=_owner_records(a.context, q), content_hash=identify_quote_content(q).content_hash,
            bid=_identity_decimal(q.bid), ask=_identity_decimal(q.ask), observation=_observation_snapshot(a.option_observation),
            reasons=list(a.option_quote_reasons)),
        underlying=None if u is None else dict(records=_owner_records(a.context, u.quote),
            content_hash=identify_quote_content(u.quote).content_hash, decision_at=_timestamp_string(u.decision_at),
            max_quote_age_us=_duration_us(u.max_quote_age), observation=_observation_snapshot(u.observation),
            quote_reasons=list(u.quote_reasons), midpoint=_identity_decimal(u.midpoint), spread=_identity_decimal(u.spread)),
        greek=None if g is None else dict(records=_owner_records(a.context, g.greek), status=g.status, reasons=list(g.reasons),
            as_of=None if g.greek is None else _timestamp_string(g.greek.as_of),
            available_at=None if g.greek is None else _timestamp_string(g.greek.available_at),
            delta=None if g.greek is None else _identity_decimal(g.greek.delta), iv=None if g.greek is None else _identity_decimal(g.greek.iv),
            decision_at=_timestamp_string(g.decision_at), observed_input_hash=g.observed_input_hash, expected_input_hash=g.expected_input_hash,
            expected_method_identity=g.expected_method_identity, method_hash=_method_hash(g.method),
            option_quote_hash=g.option_quote_identity.content_hash, underlying_quote_hash=g.underlying_quote_identity.content_hash),
        coherence=[dict(records=_owner_records(a.context, c.evidence), reasons=list(c.reasons),
            decision_at=_timestamp_string(c.decision_at), max_quote_age_us=_duration_us(c.max_quote_age),
            coherent_at=_timestamp_string(c.coherent_at), proof_basis=c.proof_basis,
            declared_coherent_at=None if c.evidence is None else _timestamp_string(c.evidence.coherent_at),
            declared_method=None if c.evidence is None else c.evidence.method,
            greek_instant_supported=False if g is None or g.greek is None or g.greek.as_of is None else c.supports_instant(g.greek.as_of),
            option_quote_hash=c.option_identity.content_hash, underlying_quote_hash=c.underlying_identity.content_hash,
            matched_members=[m.record_id for m in c.matched_members]) for c in a.coherence_assessments],
        references=[dict(mapping_records=_owner_records(a.context, r.mapping), reference_records=_owner_records(a.context, r.reference),
            available_at=_timestamp_string(r.reference.available_at),
            effective_from=_timestamp_string(r.reference.effective_from), effective_until=_timestamp_string(r.reference.effective_until),
            listed_at=_timestamp_string(r.reference.listed_at), mapping_available_at=_timestamp_string(r.mapping.available_at),
            decision_at=_timestamp_string(r.decision_at), reasons=list(r.reasons)) for r in a.reference_assessments],
        sessions=[_session_snapshot(s, a.context) for s in a.session_assessments],
        ticks=[dict(records=_owner_records(a.context, t.rule), price=_identity_decimal(t.price),
            available_at=_timestamp_string(t.rule.available_at), effective_from=_timestamp_string(t.rule.effective_from),
            effective_until=_timestamp_string(t.rule.effective_until), price_from=_identity_decimal(t.rule.price_from),
            price_until=_identity_decimal(t.rule.price_until), increment=_identity_decimal(t.rule.increment),
            decision_at=_timestamp_string(t.decision_at), reasons=list(t.reasons),
            matched_member=None if t.matched_member is None else t.matched_member.record_id) for t in a.tick_assessments],
        quote_budget=None if b is None else dict(decision_at=_timestamp_string(b.decision_at),
            virtual_equity=_identity_decimal(b.virtual_equity), available_cash=_identity_decimal(b.available_cash),
            round_trip_fees=_identity_decimal(b.round_trip_fees), premium_fraction=_identity_decimal(b.premium_fraction),
            max_quote_age_us=_duration_us(b.max_quote_age), max_spread_fraction=_identity_decimal(b.max_spread_fraction),
            spread_floor=_identity_decimal(b.spread_floor), original_ask_cap=_identity_decimal(b.original_ask_cap),
            observation=_observation_snapshot(b.observation), quote_reasons=list(b.quote_reasons),
            budget=None if budget is None else dict(premium=_identity_decimal(budget.premium), fees=_identity_decimal(budget.fees),
                adverse_reserve=_identity_decimal(budget.adverse_reserve), required_cash=_identity_decimal(budget.required_cash),
                equity_limit=_identity_decimal(budget.equity_limit), reason=budget.reason)))


def _source_snapshot(context):
    """Bind every causal original source root and its final owner disposition.

    :param context: Actual context or None.
    :returns: Stable complete causal member commitments and reached semantic identities.
    :raises _UnsupportedIdentity: If a retained member's committed raw bytes disagree.
    """
    records = []
    for c in sorted(() if context is None else (c for c in context.components if c.disposition != 'future'),
                    key=lambda c: c.member.record_id):
        m = c.member
        raw_hash = sha256(m.raw_body_bytes).hexdigest()
        if raw_hash != m.raw_hash:
            raise _UnsupportedIdentity
        _contract_snapshot(c.contract)
        records.append(dict(input_manifest_id=context.input_manifest_id, record_id=m.record_id, kind=m.kind,
            profile_id=m.profile_id, raw_hash=raw_hash, envelope_hash=sha256(m.envelope_bytes).hexdigest(),
            available_at=_timestamp_string(c.available_at), requested=c.requested, disposition=c.disposition,
            reasons=list(c.reasons), representative_record_id=c.representative_record_id,
            quote_content_hash=None if c.quote_identity is None else c.quote_identity.content_hash,
            bar_content_hash=None if c.bar_identity is None else c.bar_identity.full_content_hash))
    return records


def _selection_headline(context, shared, account_assessment, initial, has_contracts):
    """Classify independent common failures before empty or locally rejected chains.

    :param context: Actual context or None.
    :param shared: Ordered common owner reasons only.
    :param account_assessment: Independently reached account evidence.
    :param initial: Actual instrument-free original timing.
    :param has_contracts: Whether any known causal contract is considered.
    :returns: Governing global failure, empty-chain reason, or None.
    """
    if context is None:
        return 'context_missing'
    if any(r.startswith(('input_integrity:context:', 'config_policy_input_binding:', 'context_time/ledger_freshness:context:')) for r in shared):
        return 'context_invalid'
    if any(r.startswith(('input_integrity:account:', 'context_time/ledger_freshness:account:',
                         'account_connection_reconciliation:')) for r in shared):
        return 'account_unavailable'
    if any(r.startswith('source_admission:') for r in shared):
        return 'source_unavailable'
    if account_assessment.has_nonzero_holding or account_assessment.has_unknown_holding_quantity or account_assessment.has_open_order_records or account_assessment.exposure_reasons:
        return 'account_exposure'
    decision_code = next((code for code in initial.reasons if code in _DECISION_CODES), None)
    if decision_code is not None:
        return decision_code
    if initial.session_assessment.session_reasons or account_assessment.session_reasons:
        return 'market_unavailable'
    return None if has_contracts else 'empty_chain'


def select_candidates(context: DecisionContext | None, account: AccountSnapshot | None, config: StrategyConfig) -> CandidateSet:
    """Derive initial choices at context.decision_at from actual account and source facts.

    The orchestrator must supply its actual initial decision context. This signature
    cannot discover a later wall clock or authorize submission of an old selection.

    :param context: Exact initial context or explicit missing-context evidence.
    :param account: Exact supplied account; no invented flatness or capital.
    :param config: Exact strategy configuration used for all owner calls.
    :returns: Frozen complete candidate set; unsupported identity publishes no winners.
    :raises TypeError: If a trusted argument has an incorrect exact type.
    :raises ValueError: If an unexpected owner identity or time error occurs.
    """
    for name, value, expected in (('context', context, DecisionContext), ('account', account, AccountSnapshot), ('config', config, StrategyConfig)):
        if type(value) is not expected and not (value is None and name != 'config'):
            raise TypeError(name+' must have its exact trusted type'+(' or None' if name != 'config' else ''))
    now = None if context is None else context.decision_at
    a, ahash, common, accounts, marks, shared = (None, None, (), (), (), ('input_integrity:selection:context_missing',)) if context is None else _context_account_evidence(context, account, config, now=now)
    raw_account, identity_failed = None, False
    if account is not None:
        try:
            raw_account = account_snapshot(account)
            ahash = _snapshot_hash(raw_account)
        except ValueError as error:
            if str(error) != 'account_identity_representation_unsupported':
                raise
            identity_failed = True
            shared = _ordered((*shared, 'input_integrity:account:account_identity_representation_unsupported'))
    initial = None if context is None else assess_decision_slot(context.session, None, config=config, decision_at=now, now=now)
    supported, unsupported = {}, []
    for c in () if context is None else context.components:
        if c.member.kind != 'option_quote' or c.contract is None or c.disposition not in ('selected', 'omitted', 'unresolved', 'audit'):
            continue
        try:
            _contract_snapshot(c.contract)
        except _UnsupportedIdentity:
            identity_failed = True
            if not any(_contract_key(c.contract) == _contract_key(other) for other in unsupported):
                unsupported.append(c.contract)
        else:
            supported[c.contract] = c.contract
    contracts = [*sorted(supported.values(), key=_contract_key), *unsupported]
    rows, ranked = [], {'call': [], 'put': []}
    for contract in contracts:
        quotes = [q for q in context.option_quotes if q.contract == contract]
        assessment = assess_candidate(contract, context, account, config, now=now,
                                      original_ask_cap=quotes[0].ask if len(quotes) == 1 else None)
        timing = tuple(EntryTimingAssessment('decision', s, now, now) for s in assessment.session_assessments)
        reasons = list(assessment.reasons)
        if not timing:
            reasons.append('current_session:selection:initial_timing_unavailable')
        for t in timing:
            reasons.extend('current_session:initial_timing:'+code for code in t.reasons if code in _INITIAL_CODES)
            if t.slot_key is None or t.slot_key != context.slot_key or t.session_assessment.config != config or t.session_assessment.session is not context.session:
                reasons.append('current_session:selection:initial_slot_mismatch')
        row = dict(contract=contract, assessment=assessment, initial_timing=timing, rank=None, rank_evidence=None,
                   reasons=(), disposition='ineligible')
        if any(contract is other for other in unsupported):
            reasons.append(_SELECTION_ARITHMETIC)
        elif not reasons:
            try:
                key, row['rank_evidence'] = _selection_rank(assessment.greek_readiness.greek.delta,
                    assessment.option_quote.bid, assessment.option_quote.ask, contract)
            except _UnsupportedIdentity:
                reasons.append(_SELECTION_ARITHMETIC)
            else:
                ranked[contract.right].append((key, len(rows)))
        row['reasons'] = _ordered(reasons)
        rows.append(row)
    headline = _selection_headline(context, shared, a, initial, bool(contracts))
    global_reasons = list(shared)
    if initial is not None:
        global_reasons.extend('current_session:initial_timing:'+code for code in initial.reasons
            if code in _DECISION_CODES or code in initial.session_assessment.session_reasons)
    selected = {'call': None, 'put': None}
    for right, keys in ranked.items():
        for rank, (_, index) in enumerate(sorted(keys), 1):
            rows[index]['rank'] = rank
            rows[index]['disposition'] = 'selection_unavailable' if headline else 'selected' if rank == 1 else 'not_selected_by_rank'
            if rank == 1 and headline is None:
                selected[right] = index
    if headline is None and all(i is None for i in selected.values()):
        headline = 'no_eligible_candidates'
    values = dict(context=context, account=account, config=config, decision_id=None if context is None else context.decision_id,
        as_of=now, slot_key=None if context is None else context.slot_key, initial_timing=initial,
        account_assessment=a, common_components=common, account_components=accounts, mark_components=marks,
        config_hash=config_hash(config), policy_hash=policy_hash(config), context_input_digest=None if context is None else context.input_digest,
        input_manifest_id=None if context is None else context.input_manifest_id, account_hash=ahash, selection_rule_id=SELECTION_RULE_ID,
        origin='synthetic', fidelity_tier=0, permitted_use='core_fixture', operational_allowed=False, economic_allowed=False)
    snapshot_bytes = None
    try:
        if identity_failed:
            raise _UnsupportedIdentity
        snapshot_bytes = _canonical_bytes(dict(record_kind='options_lab.candidate_set', schema_version=1,
            decision_id=values['decision_id'], as_of=_timestamp_string(now), slot_key=values['slot_key'],
            config_hash=values['config_hash'], policy_hash=values['policy_hash'], selection_rule_id=SELECTION_RULE_ID,
            selection_rule=config.selection_rule, context_input_digest=values['context_input_digest'], input_manifest_id=values['input_manifest_id'],
            initial_timing=_timing_snapshot(initial, context), account=raw_account, account_hash=ahash,
            account_assessment=_account_assessment_snapshot(a), common_records=_record_ids(common), account_records=_record_ids(accounts),
            mark_records=[_record_ids(cs) for cs in marks], source_records=_source_snapshot(context),
            considered=[_consideration_snapshot(row) for row in rows],
            selected_call=None if selected['call'] is None else dict(considered_index=selected['call'], contract=_contract_snapshot(rows[selected['call']]['contract'])),
            selected_put=None if selected['put'] is None else dict(considered_index=selected['put'], contract=_contract_snapshot(rows[selected['put']]['contract'])),
            reasons=list(_ordered(global_reasons)), selection_reason=headline,
            origin='synthetic', fidelity_tier=0, permitted_use='core_fixture', operational_allowed=False, economic_allowed=False))
    except _UnsupportedIdentity:
        global_reasons.append(_SELECTION_ARITHMETIC)
        if headline in (None, 'empty_chain', 'no_eligible_candidates'):
            headline = 'arithmetic_precision_unsupported'
        selected = {'call': None, 'put': None}
        for row in rows:
            if row['disposition'] != 'ineligible':
                row['disposition'] = 'selection_unavailable'
    considered = []
    for row in rows:
        record = object.__new__(CandidateConsideration)
        for name, value in row.items():
            object.__setattr__(record, name, value)
        considered.append(record)
    values.update(considered=tuple(considered), selected_call=None if selected['call'] is None else considered[selected['call']].assessment,
        selected_put=None if selected['put'] is None else considered[selected['put']].assessment,
        reasons=_ordered(global_reasons), selection_reason=headline, snapshot_bytes=snapshot_bytes,
        content_hash=None if snapshot_bytes is None else sha256(snapshot_bytes).hexdigest())
    result = object.__new__(CandidateSet)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result
