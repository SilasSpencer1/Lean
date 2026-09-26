"""Original entry intent comes only from the admitted selection and fixed model."""

from copy import copy
from dataclasses import replace
from datetime import timedelta, tzinfo
from decimal import Decimal
import json

import pytest

from options_lab.config import StrategyConfig
from options_lab.admission import verify_fixture_bundle
from options_lab.candidates import select_candidates
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.decision import FixedJsonPredictor, _score_candidates
from options_lab.entry_intent import TradeIntent, derive_trade_intent
from options_lab.feature_vector import EXACT_VWAP_SPEC, build_features
from test_decision import NOW, SOURCE, bundle, source
from pathlib import Path


def test_fixed_call_creates_one_frozen_original_intent():
    """Changing the original limit, lifetime or cost must break this example."""
    model = bundle("call")
    context, candidates, vectors = source(model.normalization_result.value)
    decision = _score_candidates(context, candidates, vectors, FixedJsonPredictor(model), StrategyConfig())

    intent = derive_trade_intent(candidates, decision, StrategyConfig())

    assert type(intent) is TradeIntent
    assert intent.decision_id == "decision"
    assert intent.contract.right == "call"
    assert intent.quantity == 1
    assert intent.limit_price == intent.original_ask_cap == Decimal("5.10")
    assert (intent.premium, intent.reserved_cost, intent.max_cost) == (
        Decimal("510"), Decimal("21"), Decimal("531"))
    assert intent.created_at == NOW
    assert intent.expires_at.isoformat() == "2026-09-04T14:05:05+00:00"
    assert intent.quote_member_record_id == "c99-option_quote"
    assert intent.quote_raw_ref == "synthetic://p13c/c99-option_quote"
    assert intent.client_order_id.startswith("ol-entry-v1-")
    assert len(intent.client_order_id) == 76
    assert intent.selected is decision.selected
    assert derive_trade_intent(candidates, decision, StrategyConfig()).client_order_id == intent.client_order_id


def scored(name="call", config=None):
    """Produce an actual original P15 decision for an intent boundary test."""
    config = config or StrategyConfig()
    model = bundle(name)
    context, candidates, vectors = source(model.normalization_result.value if name != "cash" else None)
    decision = _score_candidates(context, candidates, vectors if name != "cash" else (),
                                 FixedJsonPredictor(model), config)
    return candidates, decision, config


def changed(value, **fields):
    """Damage retained evidence without invoking its blocked constructor."""
    clone = copy(value)
    for name, item in fields.items():
        object.__setattr__(clone, name, item)
    return clone


def test_put_is_original_choice_and_cash_outcomes_create_no_intent():
    """Re-ranking or cash fallback would turn one of these outcomes into a buy."""
    candidates, decision, config = scored("put")
    put = derive_trade_intent(candidates, decision, config)
    assert put is not None and put.contract.right == "put"
    assert put.client_order_id != derive_trade_intent(*scored("call")).client_order_id
    for name in ("cash", "tie", "negative"):
        assert derive_trade_intent(*scored(name)) is None


def test_original_proof_rejects_copied_readiness_and_changed_score():
    """A copied success flag or selected forecast cannot mint a new order."""
    candidates, decision, config = scored()
    assert derive_trade_intent(candidates, changed(decision, entry_ready=True,
                                                   bundle_assessment=None), config) is None
    assert derive_trade_intent(candidates, changed(decision, selected=None), config) is None
    forecast = replace(decision.selected.forecast, mean_attempt_return=Decimal("0.99"))
    forged = changed(decision.selected, forecast=forecast)
    assert derive_trade_intent(candidates, changed(decision, selected=forged), config) is None
    assert derive_trade_intent(changed(candidates, selected_call=None), decision, config) is None


def test_original_proof_rejects_changed_source_cap_vector_config_and_time():
    """Any altered original decision input must fail complete rederivation."""
    candidates, decision, config = scored()
    quote = decision.selected.candidate.option_quote
    damaged_choice = changed(decision.selected.candidate, original_ask_cap=Decimal("5.11"))
    damaged_score = changed(decision.selected, candidate=damaged_choice)
    assert derive_trade_intent(candidates, changed(decision, selected=damaged_score), config) is None
    vector = changed(decision.supplied_vectors[0], values=())
    assert derive_trade_intent(candidates, changed(decision,
        supplied_vectors=(vector, *decision.supplied_vectors[1:])), config) is None
    assert derive_trade_intent(changed(candidates, account_hash="0" * 64), decision, config) is None
    assert derive_trade_intent(candidates, decision,
        replace(config, max_entries_per_session=2)) is None
    assert derive_trade_intent(changed(candidates,
        context=changed(candidates.context, decision_at=NOW + timedelta(seconds=1))),
        decision, config) is None
    assert quote.ask == Decimal("5.10")


def test_original_limit_lifetime_and_money_use_owner_under_hostile_ambient_context():
    """Ambient precision must not round the frozen extra reserve or expiry."""
    from decimal import localcontext

    candidates, decision, config = scored()
    with localcontext() as ambient:
        ambient.prec = 2
        intent = derive_trade_intent(candidates, decision, config)
    assert intent is not None
    assert (intent.premium, intent.reserved_cost, intent.max_cost) == (
        Decimal("510"), Decimal("21"), Decimal("531"))
    assert intent.expires_at == NOW + timedelta(seconds=5)
    with pytest.raises(TypeError):
        TradeIntent()


def test_material_account_is_immutable_complete_economic_evidence():
    """Changing a nested portfolio fact must not rewrite the frozen intent."""
    intent = derive_trade_intent(*scored())
    assert intent is not None and type(intent.material_account) is bytes
    material = json.loads(intent.material_account)
    assert material["account_id"] == "initial-account_snapshot"
    assert material["source"] == "fixture-account-ledger"
    assert material["virtual_equity"] == "150000"
    assert material["holdings"] == material["open_orders"] == []
    assert "risk_state_revision" not in material
    material["virtual_equity"] = "0"
    assert json.loads(intent.material_account)["virtual_equity"] == "150000"


def test_client_id_is_stable_across_local_delivery_of_same_original_source():
    """Changing only local admit/request receipts must not create another buy ID."""
    baseline = derive_trade_intent(*scored())
    model = bundle("call")
    raw = (Path(__file__).parent / "fixtures" / (SOURCE + ".json")).read_bytes()
    admitted = verify_fixture_bundle(SOURCE, raw, event_id="later-local-verify",
                                     raw_ref="later-local-ref", received_at=NOW)
    assert admitted.value is not None
    ids = [m.record_id for m in admitted.value.members if m.record_id in
           ("session", "account_snapshot", "underlying_quote") or m.record_id.startswith(
               ("bars-", "c99-", "c100-", "p99-", "pdelta-"))]
    request = normalize_context_request(dict(decision_id="decision", decision_at=NOW.isoformat(),
                                             member_record_ids=ids), event_id="later-local-request",
                                        raw_ref="later-local-ref", received_at=NOW)
    context = build_decision_context(request, admitted.value, config=StrategyConfig(),
                                     previous_feature_state=None).context
    assert context is not None
    account = next(c.value for c in context.selected_components if c.member.kind == "account_snapshot")
    candidates = select_candidates(context, account, StrategyConfig())
    vectors = tuple(build_features(context, choice.option_quote, choice.greek_readiness.greek,
        now=NOW, spec=EXACT_VWAP_SPEC, normalization=model.normalization_result.value).vector
        for choice in candidates.selected)
    decision = _score_candidates(context, candidates, vectors, FixedJsonPredictor(model), StrategyConfig())
    redelivered = derive_trade_intent(candidates, decision, StrategyConfig())
    assert baseline is not None and redelivered is not None
    assert baseline.client_order_id == redelivered.client_order_id
    assert baseline.material_account == redelivered.material_account


def test_wrong_public_types_raise_and_damaged_nested_owners_deny():
    """Hostile retained leaves must be rejected before their hooks run."""
    candidates, decision, config = scored()
    with pytest.raises(TypeError):
        derive_trade_intent(object(), decision, config)
    with pytest.raises(TypeError):
        derive_trade_intent(candidates, object(), config)
    with pytest.raises(TypeError):
        derive_trade_intent(candidates, decision, object())
    assert derive_trade_intent(changed(candidates, context=object()), decision, config) is None
    assert derive_trade_intent(changed(candidates, context=changed(candidates.context,
        decision_at=None)), decision, config) is None
    class HostileZone(tzinfo):
        """This class represents a timezone whose hooks must never run."""

        def utcoffset(self, value):
            """Raise if a damaged time reaches temporal arithmetic."""
            raise AssertionError("hostile timezone invoked")

    hostile = NOW.replace(tzinfo=HostileZone())
    assert derive_trade_intent(changed(candidates, context=changed(candidates.context,
        decision_at=hostile)), decision, config) is None
    class HostileEquality:
        """This class represents a nested value whose equality must not run."""

        def __eq__(self, other):
            """Raise if a damaged owner reaches arbitrary equality."""
            raise AssertionError("hostile equality invoked")

    assert derive_trade_intent(candidates, changed(decision,
        selected=HostileEquality()), config) is None
    missing = changed(decision)
    object.__delattr__(missing, "bundle_assessment")
    assert derive_trade_intent(candidates, missing, config) is None
