"""The implementation may not lean on a library: block them before specfun
is imported. The tests themselves use mpmath through a private import."""
import importlib
import math
import sys

mp = importlib.import_module("mpmath")          # kept for the tests
sys.modules["mpmath"] = None                     # gone for specfun
for name in ("numpy", "scipy", "sympy"):
    sys.modules[name] = None


def _forbidden(name):
    def f(*a, **k):
        raise RuntimeError(f"math.{name} is not allowed in specfun")
    return f


for name in ("erf", "erfc", "gamma", "lgamma"):
    setattr(math, name, _forbidden(name))
