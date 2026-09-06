# OptionsLab one-contract premium budget

> Execution: test-driven implementation with an independent correctness review.

**Goal:** Start the approved Phase 1 core with a complete, pure affordability calculation. A successful result is budget evidence, never permission to submit an order or a reservation of funds.

**Spec:** [Reviewed system design](../specs/2026-09-05-spy-options-paper-system-design-v2.md). The [core roadmap](2026-09-05-options-lab-core-v2.md) retains all Phase 1 requirements; its large schema task is delivered incrementally with the behavior that consumes each type.

**Architecture:** One cohesive `premium.py` module containing an immutable budget result and a named policy calculation; tests exercise real arithmetic. The future entry authorization service consumes this same calculation. Broker I/O, market normalization, accounting/recovery and atomic reservations remain separate dependent increments. No placeholder modules, class hierarchy, repository framework or generic rule engine.

**Runtime:** Python 3.11.11 (the repository LEAN image pin), pytest 9.0.2 for development. Standard library only at runtime. Use the Javadoc-inspired Python documentation convention for public classes/functions. Record a broader Python compatibility claim only after testing it.

## Financial contract

- SPY standard contract sizing is exactly one contract with multiplier 100. Instrument/reference eligibility is a later required gate; this policy must not infer standard deliverables merely from multiplier 100.
- Inputs are named keyword arguments: `ask: Decimal`, `bid: Decimal`, `virtual_equity: Decimal | None`, `available_cash: Decimal | None`, `quantity: int = 1`, `round_trip_fees: Decimal = Decimal('1')`, `premium_fraction: Decimal = Decimal('0.005')`.
- The callable is `assess_premium_budget(...) -> PremiumBudget`. Reject invalid scalar types, nonfinite values, nonpositive ask/bid, and crossed bid greater than ask with descriptive ValueError. Raw adapter normalization is outside this slice. A Boolean is not an integer quantity or money value.
- Exactly one quantity is affordable. Other integer requested quantities produce a quantity rejection; they must not be silently clamped or turned into a successful one-contract assessment.
- Fee estimate must be nonnegative; the effective fee floor is `max(1, round_trip_fees)`. The fraction can tighten the premium limit, with `0 < premium_fraction <= 0.005`; attempts to weaken it are invalid configuration.
- Capital basis K = 100 * ask. Adverse reserve = max(0.005 * K, 100 * (ask - bid)). Required cash = K + effective round-trip fee + adverse reserve. Use only the decision quote; no exit spread, midpoint fill, or assumed price improvement.
- Compare required cash against both `premium_fraction * virtual_equity` and already unencumbered/settled `available_cash`. Equal limits pass. Missing capital or cash returns an explicit unsuccessful assessment. Finite nonpositive equity and negative available cash remain representable account facts and fail affordability, not constructor validation.
- The frozen result exposes premium, fees, adverse reserve, required cash, equity limit when known, and one explicit reason (or no reason when affordable); `affordable` derives from that reason. It contains no order, intent, approval token, mutable account or side effect.
- Rejection order after boundary/config validation: non-one quantity; undeclared capital; nonpositive equity; premium cap; unavailable cash; insufficient cash. Costs are still useful diagnostics on valid quotes. This local order does not replace the full later risk precedence.
- No rounding may turn a cost above a cap into an affordable result. Arithmetic must not depend on a caller lowering Decimal context precision. Prefer ordinary standard-library arithmetic with a small explicit precision policy over a general money framework.
- Bound required working precision to 1,000 decimal digits and reject larger arithmetic with descriptive ValueError before allocating a Decimal context/result. This is an input-resource limit, not a relaxation of the financial cap. Zero values retain their meaning regardless of exponent spelling. Package metadata admits the Python 3.11 series; the tested interpreter remains exactly 3.11.11.

## Task 1: Implement the affordability behavior and its executable examples

**Files:** `OptionsLab/pyproject.toml`, `OptionsLab/src/options_lab/__init__.py`, `OptionsLab/src/options_lab/premium.py`, `OptionsLab/tests/test_premium.py`, `OptionsLab/README.md`; scoped Python cache ignores in the repository `.gitignore` only if needed.

- [x] Add package/test configuration and the first consumer test. Keep production behavior absent until the test demonstrates the missing capability.
- [x] Verify RED with the real pytest runner; fix test mistakes rather than accepting an unrelated import/path failure as the only evidence.
- [x] Implement the minimum named calculation and frozen result. Public docs distinguish budget evidence from authorization and describe invalid input.
- [x] Grow tests before behavior for exact risk/cash boundaries, unknown capital, fees, quantities, invalid inputs and deterministic arithmetic.
- [x] Verify GREEN on Python 3.11.11, run the whole new package suite, build/install a wheel and exercise its public import outside the source tree.
- [x] Have an independent reviewer examine the production diff and tests for risk weakening, hidden approval semantics, precision loss and unnecessary abstractions. Resolve findings and rerun affected checks.
- [x] Record implementation and test line counts separately. Keep this implementation increment below 1,000 changed lines; aim for a few hundred. Do not create empty future files merely to satisfy the roadmap.

Hand-checked acceptance cases (synthetic fixtures, not market data or declared experiment capital):

| Ask / bid | Equity | Available cash | Expected |
|---|---|---|---|
| 5.10 / 5.00 | 104200.00 | 521.00 | K=510, fee=1, reserve=10, total=521, affordable |
| 5.10 / 5.00 | 104199.99 | 521.00 | Premium cap rejection |
| 5.10 / 5.00 | 104200.00 | 520.99 | Cash rejection |
| 5.10 / 5.10 | 102710.00 | 513.55 | Reserve floor=2.55, total=513.55, affordable |
| 5.10 / 5.00 | 104400.00 | 522.00 | With fee estimate=2, total=522, affordable |
| 5.10 / 5.00 | unset | 1000000 | Capital undeclared |
| 5.10 / 5.00 | 104200.00 | 521.00 | With premium_fraction=0.004, premium cap rejection |

Test quantities 0, 2 and negative as unsuccessful, Boolean quantity and float money as invalid; NaN/Infinity, crossed/zero quotes and a fraction above 0.005 as invalid. Test an amount just above a funding boundary without rounding it down, caller precision 4, and immutability. Tests must derive expected numbers independently of the production helper.

## Subsequent review units

1. Truthful contract/reference and market observations, with availability/fidelity checks and total raw-input rejection records.
2. Completed-bar features and candidate eligibility in cohesive changes; introduce only their required types.
3. Verified forecasting metadata and call/put/cash scoring.
4. Full risk precedence, authoritative session windows and persistent halt semantics; then atomic reservation tied to account revision using this premium policy.
5. Exit/recovery rules and identified order events, including late fills, unexpected exposure and restart reconciliation.
6. Durable audit/checkpoint storage, shared orchestration and policy replay; complete the remaining original Phase 1 gates.
7. Data/training, LEAN, shadow and paper phases only after their separate prerequisites pass.

These are dependency boundaries, not permission to merge an unfinished Phase 1 as order-ready. Each unit should carry a domain behavior, its tests and necessary vocabulary updates. Review units approaching 1,000 changed lines should be split by behavior; any larger exception must be predominantly meaningful tests and explain why the boundary remains cohesive.

## First increment verification

Completed locally on 2026-09-05: CPython 3.11.11, pytest 9.0.2, 48 passing cases; compile and wheel installation/public import checks passed. Independent review found and verified the correction for unbounded Decimal precision. Production Python is 166 lines; tests are 275 lines. This completes only the premium-budget increment, not Phase 1 or any economic/paper release gate. Published as [PR #3](https://github.com/SilasSpencer1/Lean/pull/3) after explicit user approval; the user also authorized merging it into `master`.
