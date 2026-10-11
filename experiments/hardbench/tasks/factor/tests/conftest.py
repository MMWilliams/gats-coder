"""sympy is the oracle for the tests and off limits for the implementation."""
import importlib
import sys

sympy = importlib.import_module("sympy")
sys.modules["sympy"] = None
sys.modules["mpmath"] = None
