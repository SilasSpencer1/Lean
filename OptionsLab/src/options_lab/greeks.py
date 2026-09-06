"""Typed Greek dependency assumptions and canonical method/input identities."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from ._validation import _require_nonempty_string, _trusted_datetime
from .bar_inputs import _MAX_INTEGER_EXCLUSIVE, _UnsupportedIdentity, _identity_decimal
from .config import _duration_snapshot, _snapshot_hash
from .contracts import ContractId


_MAX_GREEK_AGE = timedelta(seconds=5)
_HASH_DIGITS = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class GreekInputs:
    """This class represents exact dependency and rate claims for one Greek pair."""

    option_quote_hash: str | None
    underlying_quote_hash: str | None
    rate: Decimal | None
    dividend_yield: Decimal | None
    rate_unit: str
    dividend_unit: str
    assumptions_id: str

    def __post_init__(self) -> None:
        """
        Validate exact retained dependency and assumption claims.

        :returns:             None.
        :raises   TypeError:  If a trusted field has the wrong exact type.
        :raises   ValueError: If a hash, amount, or identity string is invalid.
        """
        _require_hash("option_quote_hash", self.option_quote_hash)
        _require_hash("underlying_quote_hash", self.underlying_quote_hash)
        _require_optional_decimal("rate", self.rate)
        _require_optional_decimal("dividend_yield", self.dividend_yield)
        for name in ("rate_unit", "dividend_unit", "assumptions_id"):
            _require_nonempty_string(name, getattr(self, name))


@dataclass(frozen=True)
class GreekMethodSpec:
    """This class represents bounded semantics for one supplied Greek method."""

    method_id: str
    method_version: str
    assumptions_id: str
    delta_unit: str = "signed_option_price_per_underlying_price"
    iv_unit: str = "annualized_volatility_fraction"
    rate_unit: str = "continuous_annual_fraction"
    dividend_unit: str = "continuous_annual_fraction"
    option_price_basis: str = "option_midpoint"
    underlying_price_basis: str = "underlying_midpoint"
    max_age: timedelta = _MAX_GREEK_AGE
    method_spec_hash: str = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate method semantics and derive their canonical identity.

        :returns:             None.
        :raises   TypeError:  If a trusted field has the wrong exact type.
        :raises   ValueError: If an identity is empty or max age is out of bounds.
        """
        for name in (
            "method_id",
            "method_version",
            "assumptions_id",
            "delta_unit",
            "iv_unit",
            "rate_unit",
            "dividend_unit",
            "option_price_basis",
            "underlying_price_basis",
        ):
            _require_nonempty_string(name, getattr(self, name))
        if type(self.max_age) is not timedelta:
            raise TypeError("max_age must be a timedelta")
        if not timedelta(0) < self.max_age <= _MAX_GREEK_AGE:
            raise ValueError("max_age must be positive and at most five seconds")
        object.__setattr__(self, "method_spec_hash", _method_hash(self))


def greek_input_hash(
    inputs: GreekInputs,
    *,
    contract: ContractId,
    method: GreekMethodSpec,
    as_of: datetime,
) -> str:
    """
    Hash exact Greek dependencies, assumptions, method semantics, and valuation time.

    :param    inputs:    Exact dependency and rate assumption claims.
    :param    contract:  Exact option contract valued by the Greek pair.
    :param    method:    Exact method semantics applied to the supplied values.
    :param    as_of:     Shared valuation timestamp for delta and IV.
    :returns:            Canonical lowercase SHA-256 identity.
    :raises   TypeError: If a trusted input has the wrong exact type.
    :raises   ValueError: If a timestamp is invalid or a scalar representation
                         exceeds the bounded identity format.
    """
    if type(inputs) is not GreekInputs:
        raise TypeError("inputs must be a GreekInputs")
    if type(contract) is not ContractId:
        raise TypeError("contract must be a ContractId")
    if type(method) is not GreekMethodSpec:
        raise TypeError("method must be a GreekMethodSpec")
    as_of = _trusted_datetime("as_of", as_of)
    try:
        if not -_MAX_INTEGER_EXCLUSIVE < contract.multiplier < _MAX_INTEGER_EXCLUSIVE:
            raise _UnsupportedIdentity
        snapshot = {
            "record_kind": "options_lab.greek_inputs",
            "greek_input_schema_version": 1,
            "contract": {
                "underlying": contract.underlying,
                "expiry": contract.expiry.isoformat(),
                "right": contract.right,
                "strike": _identity_decimal(contract.strike),
                "multiplier": {
                    "encoding": "base10",
                    "value": str(Decimal(contract.multiplier)),
                },
                "deliverable_id": contract.deliverable_id,
            },
            "as_of": as_of.isoformat(),
            "option_quote_hash": inputs.option_quote_hash,
            "underlying_quote_hash": inputs.underlying_quote_hash,
            "rate": _identity_decimal(inputs.rate),
            "dividend_yield": _identity_decimal(inputs.dividend_yield),
            "rate_unit": inputs.rate_unit,
            "dividend_unit": inputs.dividend_unit,
            "assumptions_id": inputs.assumptions_id,
            "method_spec_hash": method.method_spec_hash,
        }
    except _UnsupportedIdentity:
        raise ValueError("Greek input identity exceeds representation limits") from None
    return _snapshot_hash(snapshot)


def _method_hash(method: GreekMethodSpec) -> str:
    """Hash one validated method specification through its explicit semantics."""
    return _snapshot_hash(
        {
            "record_kind": "options_lab.greek_method_spec",
            "greek_method_schema_version": 1,
            "method_id": method.method_id,
            "method_version": method.method_version,
            "assumptions_id": method.assumptions_id,
            "delta_unit": method.delta_unit,
            "iv_unit": method.iv_unit,
            "rate_unit": method.rate_unit,
            "dividend_unit": method.dividend_unit,
            "option_price_basis": method.option_price_basis,
            "underlying_price_basis": method.underlying_price_basis,
            "max_age": _duration_snapshot(method.max_age),
        }
    )


def _require_optional_decimal(name: str, value: object) -> None:
    """Validate one optional exact finite Decimal without restricting its sign."""
    if value is None:
        return
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal or None")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _require_hash(name: str, value: object) -> None:
    """Validate one optional canonical lowercase SHA-256 text claim."""
    if value is None:
        return
    if type(value) is not str:
        raise TypeError(f"{name} must be a string or None")
    if len(value) != 64 or any(character not in _HASH_DIGITS for character in value):
        raise ValueError(f"{name} must be a canonical lowercase SHA-256 hash")


FIXTURE_GREEK_METHOD = GreekMethodSpec(
    method_id="fixture-greek-values",
    method_version="1",
    assumptions_id="fixture-flat-rates-v1",
)
