from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, localcontext

import pytest

from options_lab.premium import assess_premium_budget


def test_exact_premium_and_cash_limits_are_affordable() -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10"),
        bid=Decimal("5.00"),
        virtual_equity=Decimal("104200.00"),
        available_cash=Decimal("521.00"),
    )

    assert budget.premium == Decimal("510.00")
    assert budget.fees == Decimal("1")
    assert budget.adverse_reserve == Decimal("10.00")
    assert budget.required_cash == Decimal("521.00")
    assert budget.equity_limit == Decimal("521.00000")
    assert budget.reason is None
    assert budget.affordable is True


def test_amount_above_premium_limit_is_not_rounded_down() -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.1000000001"),
        bid=Decimal("5.00"),
        virtual_equity=Decimal("104200.00"),
        available_cash=Decimal("1000"),
    )

    assert budget.required_cash == Decimal("521.0000000200")
    assert budget.equity_limit == Decimal("521.00000")
    assert budget.reason == "premium cap exceeded"
    assert budget.affordable is False


def test_reserve_floor_is_used_for_zero_spread() -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10"),
        bid=Decimal("5.10"),
        virtual_equity=Decimal("102710.00"),
        available_cash=Decimal("513.55"),
    )

    assert budget.adverse_reserve == Decimal("2.55000")
    assert budget.required_cash == Decimal("513.55000")
    assert budget.affordable is True


def test_fee_estimate_above_floor_is_used() -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10"),
        bid=Decimal("5.00"),
        virtual_equity=Decimal("104400.00"),
        available_cash=Decimal("522.00"),
        round_trip_fees=Decimal("2"),
    )

    assert budget.fees == Decimal("2")
    assert budget.required_cash == Decimal("522.00")
    assert budget.affordable is True


def test_fee_estimate_below_floor_uses_one_dollar() -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10"),
        bid=Decimal("5.00"),
        virtual_equity=Decimal("104200.00"),
        available_cash=Decimal("521.00"),
        round_trip_fees=Decimal("0.25"),
    )

    assert budget.fees == Decimal("1")
    assert budget.required_cash == Decimal("521.00")
    assert budget.affordable is True


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"quantity": 0}, "quantity must be exactly one contract"),
        ({"quantity": 2}, "quantity must be exactly one contract"),
        ({"quantity": -1}, "quantity must be exactly one contract"),
        ({"virtual_equity": None}, "virtual equity is undeclared"),
        ({"virtual_equity": Decimal("0")}, "virtual equity must be positive"),
        ({"virtual_equity": Decimal("104199.99")}, "premium cap exceeded"),
        ({"available_cash": None}, "available cash is unavailable"),
        ({"available_cash": Decimal("520.99")}, "insufficient available cash"),
        ({"available_cash": Decimal("-1")}, "insufficient available cash"),
        ({"premium_fraction": Decimal("0.004")}, "premium cap exceeded"),
    ],
)
def test_unsuccessful_assessments_have_one_reason(changes, reason) -> None:
    inputs = {
        "ask": Decimal("5.10"),
        "bid": Decimal("5.00"),
        "virtual_equity": Decimal("104200.00"),
        "available_cash": Decimal("521.00"),
    }
    inputs.update(changes)

    budget = assess_premium_budget(**inputs)

    assert budget.required_cash == Decimal("521.00")
    assert budget.reason == reason
    assert budget.affordable is False


@pytest.mark.parametrize(
    ("quantity", "virtual_equity", "available_cash", "reason"),
    [
        (2, None, None, "quantity must be exactly one contract"),
        (1, None, None, "virtual equity is undeclared"),
        (1, Decimal("0"), None, "virtual equity must be positive"),
        (1, Decimal("104199.99"), None, "premium cap exceeded"),
        (1, Decimal("104200.00"), None, "available cash is unavailable"),
        (1, Decimal("104200.00"), Decimal("520.99"), "insufficient available cash"),
    ],
)
def test_rejection_precedence_is_stable(
    quantity, virtual_equity, available_cash, reason
) -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10"),
        bid=Decimal("5.00"),
        virtual_equity=virtual_equity,
        available_cash=available_cash,
        quantity=quantity,
    )

    assert budget.reason == reason


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"ask": 5.10}, "ask must be a Decimal"),
        ({"bid": 5}, "bid must be a Decimal"),
        ({"virtual_equity": 104200.00}, "virtual_equity must be a Decimal or None"),
        ({"available_cash": False}, "available_cash must be a Decimal or None"),
        ({"round_trip_fees": 1.0}, "round_trip_fees must be a Decimal"),
        ({"premium_fraction": 0.005}, "premium_fraction must be a Decimal"),
        ({"quantity": True}, "quantity must be an integer"),
        ({"ask": Decimal("NaN")}, "ask must be finite"),
        ({"bid": Decimal("Infinity")}, "bid must be finite"),
        ({"virtual_equity": Decimal("NaN")}, "virtual_equity must be finite"),
        ({"available_cash": Decimal("-Infinity")}, "available_cash must be finite"),
        ({"round_trip_fees": Decimal("NaN")}, "round_trip_fees must be finite"),
        ({"premium_fraction": Decimal("Infinity")}, "premium_fraction must be finite"),
        ({"ask": Decimal("0")}, "ask must be positive"),
        ({"bid": Decimal("0")}, "bid must be positive"),
        ({"bid": Decimal("5.11")}, "bid cannot exceed ask"),
        ({"round_trip_fees": Decimal("-0.01")}, "round_trip_fees cannot be negative"),
        ({"premium_fraction": Decimal("0")}, "premium_fraction must be greater than zero"),
        ({"premium_fraction": Decimal("0.0051")}, "premium_fraction cannot exceed 0.005"),
    ],
)
def test_invalid_inputs_are_rejected(changes, message) -> None:
    inputs = {
        "ask": Decimal("5.10"),
        "bid": Decimal("5.00"),
        "virtual_equity": Decimal("104200.00"),
        "available_cash": Decimal("521.00"),
    }
    inputs.update(changes)

    with pytest.raises(ValueError, match=message):
        assess_premium_budget(**inputs)


def test_caller_decimal_precision_does_not_change_the_decision() -> None:
    with localcontext() as context:
        context.prec = 4
        budget = assess_premium_budget(
            ask=Decimal("5.1000000001"),
            bid=Decimal("5.00"),
            virtual_equity=Decimal("104200.00"),
            available_cash=Decimal("1000"),
        )

    assert budget.required_cash == Decimal("521.0000000200")
    assert budget.reason == "premium cap exceeded"


def test_cost_above_cap_beyond_fifty_digits_stays_above_cap() -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10" + "0" * 57 + "1"),
        bid=Decimal("5.00"),
        virtual_equity=Decimal("104200.00"),
        available_cash=Decimal("1000"),
    )

    assert budget.required_cash == Decimal("521." + "0" * 57 + "2")
    assert budget.reason == "premium cap exceeded"


def test_caller_decimal_limits_and_traps_do_not_change_arithmetic() -> None:
    with localcontext() as context:
        context.prec = 4
        context.Emax = 1
        context.Emin = -1
        context.traps[Inexact] = True
        budget = assess_premium_budget(
            ask=Decimal("5.10"),
            bid=Decimal("5.00"),
            virtual_equity=Decimal("104200.00"),
            available_cash=Decimal("521.00"),
        )

    assert budget.required_cash == Decimal("521.00")
    assert budget.affordable is True


@pytest.mark.parametrize(
    ("ask", "bid", "virtual_equity", "available_cash"),
    [
        (
            Decimal("1e1000000"),
            Decimal("1e1000000"),
            Decimal("1e1000010"),
            Decimal("1e1000010"),
        ),
        (
            Decimal("1e-1000000"),
            Decimal("1e-1000000"),
            Decimal("1e10"),
            Decimal("1e10"),
        ),
    ],
)
def test_excessive_working_precision_is_rejected(
    ask, bid, virtual_equity, available_cash
) -> None:
    with pytest.raises(ValueError, match="arithmetic precision exceeds 1000 digits"):
        assess_premium_budget(
            ask=ask,
            bid=bid,
            virtual_equity=virtual_equity,
            available_cash=available_cash,
        )


@pytest.mark.parametrize(
    ("virtual_equity", "available_cash", "reason"),
    [
        (Decimal("0e1000000"), Decimal("521"), "virtual equity must be positive"),
        (Decimal("104200"), Decimal("0e-1000000"), "insufficient available cash"),
    ],
)
def test_zero_with_exotic_exponent_remains_an_account_fact(
    virtual_equity, available_cash, reason
) -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10"),
        bid=Decimal("5.00"),
        virtual_equity=virtual_equity,
        available_cash=available_cash,
    )

    assert budget.reason == reason


def test_budget_evidence_is_immutable() -> None:
    budget = assess_premium_budget(
        ask=Decimal("5.10"),
        bid=Decimal("5.00"),
        virtual_equity=Decimal("104200.00"),
        available_cash=Decimal("521.00"),
    )

    with pytest.raises(FrozenInstanceError):
        budget.reason = "authorized"


@pytest.mark.parametrize(('bid', 'reserve', 'required'), [('4.95', '5', '516'), ('4.80', '20', '531'), ('4.999', '2.55', '513.55')])
def test_original_cap_keeps_capital_and_uses_actual_current_spread(bid, reserve, required):
    result = assess_premium_budget(ask=Decimal('5'), bid=Decimal(bid), original_ask_cap=Decimal('5.10'),
                                  virtual_equity=Decimal('150000'), available_cash=Decimal('1000'), premium_fraction=Decimal('.004'))
    assert (result.premium, result.adverse_reserve, result.required_cash) == tuple(map(Decimal, ('510', reserve, required)))
    assert result.affordable


@pytest.mark.parametrize(('equity', 'cash', 'reason'), [
    ('103200', '516', None), ('103199.99', '515.99', 'premium cap exceeded'),
    ('103200', '515.99', 'insufficient available cash'), (None, '516', 'virtual equity is undeclared'),
])
def test_original_cap_uses_current_capital_and_keeps_failure_precedence(equity, cash, reason):
    result = assess_premium_budget(ask=Decimal('5'), bid=Decimal('4.95'), original_ask_cap=Decimal('5.10'),
                                  virtual_equity=None if equity is None else Decimal(equity), available_cash=Decimal(cash))
    assert result.required_cash == Decimal('516') and result.reason == reason


class CapDecimalHook(Decimal):
    def is_finite(self):
        raise AssertionError('Decimal subclass hook executed')


@pytest.mark.parametrize('field', ['ask', 'bid', 'virtual_equity', 'available_cash', 'round_trip_fees', 'premium_fraction', 'original_ask_cap'])
def test_premium_rejects_decimal_subclasses_before_any_hook(field):
    values = dict(ask=Decimal('5'), bid=Decimal('4.95'), virtual_equity=Decimal('150000'), available_cash=Decimal('1000'))
    values[field] = CapDecimalHook('5.1')
    with pytest.raises(ValueError, match='must be a Decimal'):
        assess_premium_budget(**values)


@pytest.mark.parametrize('cap', [True, 5.1, '5.1', Decimal('NaN'), Decimal('Infinity'), Decimal('0'), Decimal('-1'), Decimal('4.99'), Decimal('1e1000000')])
def test_low_level_original_cap_invalid_or_unsupported_values_raise(cap):
    with pytest.raises(ValueError):
        assess_premium_budget(ask=Decimal('5'), bid=Decimal('4.95'), original_ask_cap=cap,
                              virtual_equity=Decimal('150000'), available_cash=Decimal('1000'))


@pytest.mark.parametrize('values', [(), ('not-money', None, False, Decimal('NaN'), None, None),
                                  tuple(map(Decimal, ('510', '1', '10', '521', '1'))) + (None,)])
def test_premium_budget_cannot_claim_caller_authored_affordability(values):
    from options_lab.premium import PremiumBudget
    with pytest.raises(TypeError):
        PremiumBudget(*values)


def test_explicit_none_and_equal_cap_preserve_legacy_premium():
    values = dict(ask=Decimal('5.1'), bid=Decimal('5'), virtual_equity=Decimal('104200'), available_cash=Decimal('521'))
    legacy = assess_premium_budget(**values)
    assert assess_premium_budget(**values, original_ask_cap=None) == legacy
    assert assess_premium_budget(**values, original_ask_cap=Decimal('5.1')) == legacy
