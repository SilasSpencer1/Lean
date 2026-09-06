# Contract-reference evidence

This increment follows the Task 1 delivery amendment in
[the core roadmap](2026-09-05-options-lab-core-v2.md). It starts directly from
master `7e5c447a65ae88198ddc4b36db3dc0439406bcaf`, after the CI and observation
PRs. The consumer owns its types in `contracts.py`.

## Contract

- Reconcile a typed provider mapping with a separately supplied reference at
  an explicit decision time. Compare all six ContractId fields: underlying,
  expiry, right, Decimal strike, integer multiplier and deliverable ID.
- Provider symbols are opaque. The adapter supplies the mapped identity;
  this function cannot certify that a provider symbol was decoded correctly.
- Preserve non-SPY, expired, adjusted and nonpositive finite identity facts.
  Invalid scalar types raise TypeError/ValueError; valid but unsuitable facts
  remain in the assessment with deterministic reasons.
- Require mapping and reference availability independently at or before the
  decision, each with measured availability and an explicit evidence reference.
  Retain assumed availability as unsuitable evidence.
- Require listed status, a known listing time at or before the decision, and
  decision time within the reference's `[effective_from, effective_until)`
  interval. An absent end is open-ended. Preserve invalid intervals as facts.
  Publication before future effectiveness is a legitimate announcement.
- Require SPY, positive strike, multiplier 100, and complete deliverable
  contents consisting of one shares/SPY/100 component. Unknown contents,
  split/duplicate components, additional cash/assets and adjusted quantities
  fail. Neither multiplier nor deliverable ID text proves the contents.
- Frozen evidence retains both inputs and the UTC decision time. Derive reasons
  and suitability from those inputs, so callers cannot supply success reasons.
  This does not authenticate source claims or authorize an order.

## Implementation and verification

1. Add failing contract reconciliation, deliverable and temporal boundary tests.
2. Implement the five concrete immutable records and assessment function using
   standard-library types. Share existing private scalar/time validators with
   observations without changing their behavior; add explicit package exports.
3. Check both calls/puts, every identity mismatch, Decimal equivalence, complete
   deliverable failures, independent availability, interval/listing boundaries,
   exact types, UTC conversion failures and immutable derived evidence.
4. Run all OptionsLab tests on Python 3.11.11, build/install a wheel outside the
   source tree, obtain independent review and publish a draft PR against master.

## Follow-up boundary

Total safe normalization of raw reference/mapping dictionaries is the next
increment; this typed API must not be presented as that external-data boundary.
Provider parsers, source admission, historical universe/version selection,
market observations, Greeks, DTE selection, sessions and order authorization
remain separate consumers. All fixtures here are synthetic.
