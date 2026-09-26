"""Prove one original scored choice and freeze its proposed entry intent."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException

from .account import AccountSnapshot, _sum_money
from .account_inputs import account_snapshot, _contract_snapshot
from .admission import _canonical_bytes
from .bar_inputs import _UnsupportedIdentity, _identity_decimal
from .bundle_inputs import _make
from .bundles import VerifiedBundle
from .calibration_inputs import _matches_retained
from .candidates import CandidateSet
from .config import StrategyConfig, _snapshot_hash, config_hash, policy_hash
from .context import DecisionContext
from .context_recheck import _standard_time
from .contracts import ContractId
from .decision import DecisionResult, FixedJsonPredictor, ScoredCandidate, _score_candidates
from .quote_content import identify_quote_content


@dataclass(frozen=True, init=False)
class TradeIntent:
    """This class represents one original source-proved proposed entry."""

    context: DecisionContext
    candidates: CandidateSet
    decision: DecisionResult
    config: StrategyConfig
    bundle: VerifiedBundle
    contract: ContractId
    original_ask_cap: Decimal
    quantity: int
    decision_id: str
    client_order_id: str
    quote_member_record_id: str
    quote_source: str
    quote_raw_ref: str
    quote_content_hash: str
    created_at: datetime
    expires_at: datetime
    slot_key: str
    account_id: str
    account_source: str
    material_account: bytes
    premium: Decimal
    reserved_cost: Decimal
    max_cost: Decimal
    config_hash: str
    policy_hash: str
    bundle_hash: str
    context_input_digest: str
    input_manifest_id: tuple[str, str]

    def __init__(self) -> None:
        """Require the original source-backed intent factory.

        :returns:             None.
        :raises   TypeError:  Always; use derive_trade_intent.
        """
        raise TypeError("TradeIntent values come from derive_trade_intent")

    @property
    def selected(self) -> ScoredCandidate:
        """Return the original selected score without replacing its evidence.

        :returns: Original selected ScoredCandidate.
        """
        return self.decision.selected

    @property
    def decision_at(self) -> datetime:
        """Return the original decision instant.

        :returns: Original source-backed decision time.
        """
        return self.created_at

    @property
    def limit_price(self) -> Decimal:
        """Return the original non-increasing buy limit per share.

        :returns: Original selected ask cap.
        """
        return self.original_ask_cap


def _material_account(account: AccountSnapshot) -> bytes:
    """Retain original economic facts without transport or opaque risk refs.

    :param    account:  Actual original selected account.
    :returns:           Immutable canonical material projection bytes.
    :raises   ValueError: If the account representation exceeds owner bounds.
    """
    value = account_snapshot(account)
    for name in ("event_id", "provider_record_id", "raw_ref", "received_at", "available_at",
                 "as_of", "reconciled_at", "reconciliation_id", "risk_state_revision", "halt_checkpoint_ref"):
        value.pop(name)
    for fact in (*value["holdings"], *value["open_orders"]):
        fact.pop("raw_ref")
    return _canonical_bytes(value)


def derive_trade_intent(candidates: CandidateSet, decision: DecisionResult,
                        config: StrategyConfig) -> TradeIntent | None:
    """Reprove a unique fixed-model original choice and freeze its proposal.

    :param    candidates: Original ranked source-backed candidate set.
    :param    decision:   Original selected fixed-model scoring result.
    :param    config:     Exact original strategy configuration.
    :returns:             Source-proved immutable intent, or None for cash/invalid proof.
    :raises   TypeError:  If a public argument has the wrong exact owner type.
    """
    if (type(candidates) is not CandidateSet or type(decision) is not DecisionResult
            or type(config) is not StrategyConfig):
        raise TypeError("candidates, decision and config require exact owner types")
    try:
        context = object.__getattribute__(candidates, "context")
        decision_at = object.__getattribute__(context, "decision_at")
        if type(context) is not DecisionContext or not _standard_time(decision_at):
            return None
        assessment = object.__getattribute__(decision, "bundle_assessment")
        bundle = object.__getattribute__(assessment, "bundle")
        vectors = object.__getattribute__(decision, "supplied_vectors")
        if type(bundle) is not VerifiedBundle or type(vectors) is not tuple:
            return None
        fresh_decision = _score_candidates(context, candidates, vectors,
                                           FixedJsonPredictor(bundle), config)
        if not _matches_retained(decision, fresh_decision, set()):
            return None
        selected = fresh_decision.selected
        if (type(selected) is not ScoredCandidate or not fresh_decision.entry_ready
                or selected.utility_dollars <= 0 or bundle.model.model_kind != "fixture_fixed_by_right"):
            return None
        choice = selected.candidate
        fresh_context = choice.context
        account = choice.account
        if type(account) is not AccountSnapshot:
            return None
        quote = choice.option_quote
        cap = choice.original_ask_cap
        budget = choice.quote_budget_assessment.budget
        if (quote is None or type(cap) is not Decimal or cap <= 0 or quote.ask != cap
                or budget is None or not budget.affordable or candidates.slot_key is None):
            return None
        components = tuple(c for c in choice.context.selected_components
                           if c.member.kind == "option_quote" and c.value is quote)
        if len(components) != 1:
            return None
        component = components[0]
        identity = identify_quote_content(quote)
        if identity.content_hash is None or component.quote_identity.content_hash != identity.content_hash:
            return None
        reserve = _sum_money((budget.fees, budget.adverse_reserve))
        if _sum_money((budget.premium, reserve)) != budget.required_cash:
            return None
        expires_at = decision_at + config.execution.entry_lifetime
        if not _standard_time(expires_at):
            return None
        contract = choice.contract
        chash, phash = config_hash(config), policy_hash(config)
        identity_fields = dict(schema_version=1, decision_id=fresh_context.decision_id,
            slot_key=candidates.slot_key, account_id=account.account_id, account_source=account.source,
            input_manifest_id=list(fresh_context.input_manifest_id),
            context_input_digest=fresh_context.input_digest,
            contract=_contract_snapshot(contract), quote_member_record_id=component.member.record_id,
            quote_content_hash=identity.content_hash, original_ask_cap=_identity_decimal(cap),
            config_hash=chash, policy_hash=phash, bundle_hash=bundle.bundle_hash,
            expires_at=expires_at.isoformat())
        client_order_id = "ol-entry-v1-" + _snapshot_hash(identity_fields)
        return _make(TradeIntent, context=context, candidates=candidates, decision=decision,
            config=config, bundle=bundle, contract=contract, original_ask_cap=cap, quantity=1,
            decision_id=fresh_context.decision_id, client_order_id=client_order_id,
            quote_member_record_id=component.member.record_id, quote_source=quote.meta.source,
            quote_raw_ref=quote.meta.raw_ref, quote_content_hash=identity.content_hash,
            created_at=decision_at, expires_at=expires_at, slot_key=candidates.slot_key,
            account_id=account.account_id, account_source=account.source,
            material_account=_material_account(account), premium=budget.premium,
            reserved_cost=reserve, max_cost=budget.required_cash, config_hash=chash,
            policy_hash=phash, bundle_hash=bundle.bundle_hash,
            context_input_digest=fresh_context.input_digest,
            input_manifest_id=fresh_context.input_manifest_id)
    except (AttributeError, TypeError, ValueError, OverflowError, DecimalException,
            _UnsupportedIdentity, RecursionError, MemoryError):
        return None
