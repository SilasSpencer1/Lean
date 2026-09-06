# OptionsLab observation availability and quote-time evidence

This is the next domain increment in the [adopted core roadmap](2026-09-05-options-lab-core-v2.md), following premium budgeting. It starts from `master` after PRs #1–#3. Contract reference validation is a subsequent increment because this behavior does not consume contract identity or prices.

## Behavior

Normalize externally supplied observation metadata and explain whether it was available at a supplied decision time. Separately assess whether its stated provenance and source timestamp can support a live quote freshness check. Neither result authorizes an entry or verifies the truth of a provider's claims.

Keep types with behavior in `OptionsLab/src/options_lab/observations.py`, export its public API, and add focused tests plus a short README example. Use the standard library and the existing pytest dependency. Preserve the premium implementation and all financial limits.

## Contracts

- `normalize_observation_meta(raw, *, raw_ref, event_id, received_at)` returns exactly one admitted metadata value or an `InputRejection`. An exact dictionary is the external JSON-style boundary; accept aware datetime objects and documented ISO strings for timestamps. Ordinary failures from supplied timezone callbacks become safe `invalid_timestamp` diagnostics; the exception guard covers only timezone evaluation/conversion. Invalid trusted envelope arguments remain programming errors, and process-control signals propagate.
- Metadata retains source and provider record identity, raw reference, feed class, fidelity, kind, optional source event time, availability time, current ingestion time, measured/assumed availability basis and evidence reference, optional interval bounds, fill-forward status, and immutable quality flags.
- Explicit vocabularies distinguish quote/interval, genuine/synthetic/unknown fidelity, realtime/indicative/delayed/unknown feeds, and measured/assumed availability. Reject unknown keys and tokens. Normalize aware times to UTC. Do not invent missing times or provenance.
- Rejections retain the trusted event ID, ingestion time, raw reference, normalization stage and nonempty, valid field/code diagnostics in emission order. Reasons equal the first-occurrence deduplicated diagnostic codes. Do not echo raw values, unrecognized key names, exception messages or arbitrary object representations.
- `assess_observation(meta, *, decision_at, max_quote_age=timedelta(seconds=5))` returns the original metadata and separate ordered availability and live-quote-time reasons. Boolean properties derive from the reason tuples. Trusted arguments must be well typed; the maximum quote age must be positive and at most five seconds.

All public records are frozen and validate their fields, including exact scalar types. The concrete `ObservationValidation` result implements the roadmap's logical `ValidationResult[ObservationMeta]` contract without introducing a generic validation framework. Document public Python APIs using the repository's Javadoc-inspired conventions.

Assessment reasons are unique ordered subsets of the fixed domain vocabulary. Live reasons begin with exactly the availability reasons. Constructors reject invalid or inconsistent sequences rather than silently sorting or dropping evidence; they do not prove assertions against unavailable raw data or infer temporal truth without the evaluation time.

## Invariants

1. Availability is inclusive: `available_at <= decision_at`. A known source event cannot exceed availability or decision time. Preserve well-typed violations as rejected assessment evidence.
2. Intervals need both bounds, `start < end`, completion by decision time and availability no earlier than interval end. Interval end never supplies a missing quote update time.
3. Current ingestion may occur years after a historical decision. It cannot replace historical availability or source time.
4. Live quote time suitability additionally requires quote kind, genuine fidelity, realtime feed, measured availability, a known source event, no fill-forward or quality flags, and source age within the inclusive configured limit. Every availability failure also fails live assessment.
5. Missing source event time is representable; malformed or naive timestamps are schema rejections. Missing availability is rejected without fabricating a timestamp.
6. No clock reads, I/O, persistence or mutation occur in these functions. Sizes, bid/ask validity and synchronization, Greeks, contract reference, session/account state and real provenance admission remain separate prerequisites.

## Implementation and verification

- [x] Write failing tests for availability and quote-age boundaries, including one microsecond beyond each limit; timezone equivalence; future/inconsistent chronology; historical ingestion; and interval completion/publication.
- [x] Test all provenance categories, missing source time, exact input types, immutable records and result exclusivity, safe malformed-input rejection, and invalid trusted arguments.
- [x] Implement only metadata normalization and temporal assessment, introducing no unused later schemas.
- [x] Run the focused and full OptionsLab suites on Python 3.11.11, compile the package, inspect the diff and independently review the change.
- [x] Record observed validation and prepare one focused PR against `master`; keep the complete diff below 1,000 changed lines.

Passing synthetic tests establishes only this metadata behavior. It does not satisfy the complete core, economic research, shadow or paper-order gates.

## Verification record

Python 3.11.11 / pytest 9.0.2: 74 observation tests and 122 total tests pass. Test-first failures covered the absent module, UTC overflow, supplied timezone callback failures and canonical public-record evidence invariants. Compilation, wheel build, separate installation and public API checks pass. Independent review findings were fixed and re-reviewed; the result remains metadata evidence only. The CI workflow is a separate PR, so no hosted observation-suite run is claimed here.
