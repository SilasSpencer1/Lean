# OptionsLab

OptionsLab contains pure policy calculations for the SPY options research
system. A successful premium-budget assessment is evidence that one synthetic
contract fits the supplied limits. It does not authorize an order or reserve
funds.

The verified runtime is Python 3.11.11. Inputs requiring more than 1000 digits
of working precision fail closed with `ValueError`.

Observation metadata accepts an exact dictionary with aware `datetime` values
or ISO timestamp strings. Normalize it, then assess it at an explicit time:

```python
from datetime import datetime, timezone
from options_lab import assess_observation, normalize_observation_meta

now = datetime(2026, 9, 5, 14, 30, tzinfo=timezone.utc)
result = normalize_observation_meta(raw, raw_ref="capture://42", event_id="42", received_at=now)
if result.value is not None:
    evidence = assess_observation(result.value, decision_at=now)
```

This assessment only reports declared metadata and time suitability. It does
not verify external provenance claims or establish the prices, contract,
Greeks, account state, session controls, or other evidence required for entry.

Run from the `OptionsLab` directory:

```shell
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
```
