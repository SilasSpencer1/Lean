"""Compose current candidate evidence without selection or execution authority."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ._input_parsing import _InvalidInput, _parse_date
from ._validation import _trusted_datetime
from .account import AccountAssessment, AccountSnapshot, assess_account
from .account_inputs import account_hash
from .admission import VerifiedFixtureManifest
from .coherence import CoherenceAssessment, assess_quote_coherence
from .config import StrategyConfig, assess_configured_quote_budget, config_hash, policy_hash
from .context import ContextComponent, DecisionContext
from .context_inputs import ContextInputRejection
from .contracts import ContractId, ContractReferenceAssessment, assess_contract_reference
from .greeks import GreekReadiness, assess_greek_readiness
from .observations import ObservationAssessment
from .quote_content import identify_quote_content
from .quotes import QuoteObservation, QuotePremiumAssessment, _assess_quote_only
from .sessions import SessionAssessment, assess_session
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
