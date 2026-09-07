"""Frozen same-minute population baselines from actual admitted training inputs."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, DecimalException, Inexact

from .admission import VerifiedFixtureManifest
from .bar_inputs import _identity_decimal, _UnsupportedIdentity
from .config import _MAX_DECIMAL_DIGITS, _snapshot_hash
from .feature_math import NUMERIC_CONVENTION_ID, _checked, _context, _numeric_snapshot
from .volume_inputs import (
    TrainingSessionBars, VolumePartition, VolumeTrainingInputResult,
    bind_volume_training_inputs,
)
from .vwap_features import ELIGIBLE_VOLUME_DEFINITION_ID


def _normalization_snapshot() -> dict[str, object]:
    """Return the complete fixed population and frozen-statistic convention."""
    return {
        "record_kind": "options_lab.volume_normalization_definition", "schema_version": 1,
        "numeric_convention": _numeric_snapshot(),
        "volume_definition_id": ELIGIBLE_VOLUME_DEFINITION_ID,
        "population": "explicit_admitted_training_allowlist_regular_completed_XNYS_sessions",
        "minute_index": "completed_minutes_since_actual_session_open_first_minute_is_1",
        "sample_identity": "one_causally_selected_economic_observation_per_session_minute",
        "sample_order": "oldest_session_date_to_newest",
        "missingness": "typed_missing_volume_or_unknown_eligibility_and_independently_verified_registered_raw_volume_only_failures_omitted_no_imputation",
        "input_failures": "whole_fit_rejection_no_selected_subset_recovery",
        "exact_arithmetic": "bounded_1000_digit_context_Inexact_trap_checked_operands_and_intermediates",
        "moment_order": ["x2=exact(x*x);S=exact(S+x);Q=exact(Q+x2)",
                         "T=exact(exact(n*Q)-exact(S*S))", "NN=exact(n*n)",
                         "mean80=S/n;variance80=T/NN;stddev80=sqrt(variance80);mean34;stddev34"],
        "mean": "working80(sum_x/n)_then_output34",
        "population_stddev": "working80_sqrt(working80((n*sum(x*x)-sum(x)*sum(x))/(n*n)))_then_output34",
        "population_divisor": "n_not_n_minus_1",
        "minimum_distinct_training_sessions": 20,
        "readiness": "n_at_least_20_and_strictly_positive_supported_population_stddev",
        "no_samples": "absent_bucket_no_invented_zero_statistics",
        "sparse_or_zero_scale": "retain_stored_statistics_with_local_unavailability_reasons",
        "arithmetic_or_serialization_limit": "whole_fit_baseline_none_retain_inputs_and_reached_buckets",
        "positive_variance_lost": "arithmetic_precision_unsupported",
        "stored_statistics": "mean_and_population_stddev_rounded_to_34_significant_digits",
        "later_scoring": "working80_subtract_current_volume_minus_stored_mean_then_divide_stored_stddev_then_output34_no_refit",
        "units": "eligible_ordinary_share_volume",
    }


VOLUME_NORMALIZATION_ID = _snapshot_hash(_normalization_snapshot())


@dataclass(frozen=True, init=False)
class VolumeBucket:
    """This class represents one derived population bucket and its local readiness."""

    minute_index: int
    mean: Decimal | None
    population_stddev: Decimal | None
    sample_count: int
    contributing_sessions: tuple[date, ...]
    reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Prevent callers from supplying fitted statistics or readiness evidence.

        :returns: None.
        :raises TypeError: Always; use fit_volume_baseline.
        """
        raise TypeError("VolumeBucket values come from fit_volume_baseline")

    @property
    def ready(self) -> bool:
        """Return whether this derived bucket meets count and positive-scale rules.

        :returns: True only for a supported bucket with no readiness reasons.
        """
        return not self.reasons

    @property
    def snapshot(self) -> dict[str, object]:
        """Return a fresh canonical dictionary of actual stored bucket evidence.

        :returns: Exact fixed-point statistics, ordered dates and readiness reasons.
        """
        return {
            "minute_index": self.minute_index, "mean": _identity_decimal(self.mean),
            "population_stddev": _identity_decimal(self.population_stddev),
            "sample_count": self.sample_count,
            "contributing_sessions": [day.isoformat() for day in self.contributing_sessions],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, init=False)
class VolumeBaseline:
    """This class represents immutable fitted buckets with actual upstream identity.

    A sparse or zero-scale bucket is retained without becoming usable. The
    actual manifest preserves synthetic fixture authority. Frozen artifact
    availability is separately supplied by the later normalization owner.
    """

    buckets: tuple[VolumeBucket, ...]
    cutoff: datetime
    partition: VolumePartition
    manifest: VerifiedFixtureManifest
    volume_definition_id: str
    numeric_id: str
    normalization_id: str
    training_input_hash: str
    content_hash: str

    def __init__(self) -> None:
        """Prevent caller-written means, counts or hashes from minting a baseline.

        :returns: None.
        :raises TypeError: Always; use fit_volume_baseline.
        """
        raise TypeError("VolumeBaseline values come from fit_volume_baseline")

    @property
    def snapshot(self) -> dict[str, object]:
        """Return the complete semantic snapshot excluding its own downstream hash.

        :returns: Fresh explicit baseline fields, dependency IDs and stored buckets.
        """
        partition = self.partition
        return {
            "record_kind": "options_lab.volume_baseline", "schema_version": 1,
            "cutoff": self.cutoff.isoformat(),
            "partition": {
                "partition_id": partition.partition_id, "record_id": partition.record_id,
                "raw_hash": partition.raw_hash, "input_manifest_id": partition.input_manifest_id,
                "input_manifest_hash": partition.input_manifest_hash,
                "training_sessions": [d.isoformat() for d in partition.training_sessions],
                "validation_sessions": [d.isoformat() for d in partition.validation_sessions],
                "test_sessions": [d.isoformat() for d in partition.test_sessions],
            },
            "volume_definition_id": self.volume_definition_id,
            "numeric_id": self.numeric_id, "normalization_id": self.normalization_id,
            "training_input_hash": self.training_input_hash,
            "buckets": [bucket.snapshot for bucket in self.buckets],
        }

    @property
    def normalization_snapshot(self) -> dict[str, object]:
        """Return fresh code-owned definition evidence underlying normalization_id.

        :returns: The fixed numerical, population and stored-statistic convention.
        """
        return _normalization_snapshot()


@dataclass(frozen=True, init=False)
class VolumeBaselineFit:
    """This class represents retained input binding, reached buckets and fit failure."""

    inputs: VolumeTrainingInputResult
    buckets: tuple[VolumeBucket, ...]
    baseline: VolumeBaseline | None
    reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Prevent caller-supplied fit success or replacement omission ledgers.

        :returns: None.
        :raises TypeError: Always; use fit_volume_baseline.
        """
        raise TypeError("VolumeBaselineFit values come from fit_volume_baseline")


def fit_volume_baseline(
    training_bars: tuple[TrainingSessionBars, ...], cutoff: datetime, *,
    partition: VolumePartition, manifest: VerifiedFixtureManifest,
) -> VolumeBaselineFit:
    """Fit exact population moments after the single admitted training-input binder.

    Ordinary typed omissions stay in the canonical upstream hash. Failed
    binding or unsupported arithmetic cannot publish a partial baseline.

    :param training_bars: Exact selected typed groups, including receipt occurrences.
    :param cutoff: Explicit aware feature-data availability cutoff.
    :param partition: Actual registered explicit training split and selected members.
    :param manifest: Factory-derived admission evidence containing those members.
    :returns: Frozen baseline or retained input and reached numerical failure evidence.
    :raises TypeError: If a trusted input has the wrong exact type.
    :raises ValueError: If cutoff is naive or unrepresentable.
    """
    inputs = bind_volume_training_inputs(training_bars, cutoff, partition=partition, manifest=manifest)
    reasons, buckets, baseline = inputs.reasons, (), None
    if not reasons:
        groups = {}
        for row in inputs.selected_inputs:
            if not row.omission_reasons:
                groups.setdefault(row.minute_index, []).append(row)
        buckets = tuple(_bucket(minute, groups[minute]) for minute in sorted(groups))
        if any("arithmetic_precision_unsupported" in bucket.reasons for bucket in buckets):
            reasons = ("arithmetic_precision_unsupported",)
        else:
            value = _freeze(VolumeBaseline, buckets=buckets, cutoff=inputs.cutoff,
                            partition=inputs.partition, manifest=inputs.manifest,
                            volume_definition_id=partition.volume_definition_id,
                            numeric_id=NUMERIC_CONVENTION_ID, normalization_id=VOLUME_NORMALIZATION_ID,
                            training_input_hash=inputs.training_input_hash)
            try:
                content_hash = _snapshot_hash(value.snapshot)
            except (_UnsupportedIdentity, MemoryError, RecursionError):
                reasons = ("arithmetic_precision_unsupported",)
            else:
                object.__setattr__(value, "content_hash", content_hash)
                baseline = value
    return _freeze(VolumeBaselineFit, inputs=inputs, buckets=buckets, baseline=baseline, reasons=reasons)


def _bucket(minute, rows):
    """Derive one distinct-session bucket without converting omissions into samples."""
    sessions = tuple(row.session.session_date for row in rows)
    reasons = ["insufficient_training_sessions"] if len(rows) < 20 else []
    mean = stddev = None
    try:
        mean, stddev = _moments(tuple(row.bar.volume for row in rows))
    except (DecimalException, _UnsupportedIdentity):
        reasons.append("arithmetic_precision_unsupported")
    else:
        if stddev == 0:
            reasons.append("population_stddev_zero")
    return _freeze(VolumeBucket, minute_index=minute, mean=mean, population_stddev=stddev,
                   sample_count=len(rows), contributing_sessions=sessions, reasons=tuple(reasons))


def _moments(volumes):
    """Use exact bounded cancellation before fixed 80-digit division and square root."""
    exact, work, output = _context(_MAX_DECIMAL_DIGITS), _context(80), _context(34)
    exact.traps[Inexact] = True
    total = squares = Decimal(0)
    for observed in volumes:
        value = Decimal(_identity_decimal(observed))
        squared = _checked(exact.multiply(value, value), exact)
        total = _checked(exact.add(total, value), exact)
        squares = _checked(exact.add(squares, squared), exact)
    count = _checked(Decimal(len(volumes)), exact)
    product = _checked(exact.multiply(count, squares), exact)
    total_squared = _checked(exact.multiply(total, total), exact)
    numerator = _checked(exact.subtract(product, total_squared), exact)
    if numerator < 0:
        raise _UnsupportedIdentity
    denominator = _checked(exact.multiply(count, count), exact)
    mean = _checked(work.divide(total, count), work)
    variance = _checked(work.divide(numerator, denominator), work)
    stddev = _checked(work.sqrt(variance), work)
    mean = _checked(output.plus(mean), output)
    stddev = _checked(output.plus(stddev), output)
    if numerator > 0 and (variance <= 0 or stddev <= 0):
        raise _UnsupportedIdentity
    return Decimal(0) if mean == 0 else mean, Decimal(0) if stddev == 0 else stddev


def _freeze(cls, **values):
    """Populate one concrete immutable outcome inside the fitter factory."""
    result = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result
