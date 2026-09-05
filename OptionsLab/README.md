# OptionsLab

OptionsLab contains pure policy calculations for the SPY options research
system. A successful premium-budget assessment is evidence that one synthetic
contract fits the supplied limits. It does not authorize an order or reserve
funds.

The verified runtime is Python 3.11.11. Inputs requiring more than 1000 digits
of working precision fail closed with `ValueError`.

Run from the `OptionsLab` directory:

```shell
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
```
