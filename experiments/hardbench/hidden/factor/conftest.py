import importlib
import sys

sympy = importlib.import_module("sympy")
sys.modules["sympy"] = None
sys.modules["mpmath"] = None
