import importlib
import sys

solve_ivp = importlib.import_module("scipy.integrate").solve_ivp
sys.modules["scipy"] = None
sys.modules["scipy.integrate"] = None
