"""Every LEDGER component compiles and imports on its own, in a fresh
interpreter, with no help from import order elsewhere in the suite."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(p.stem for p in (ROOT / "ledger").glob("*.py") if p.stem != "__main__")


@pytest.mark.parametrize("name", MODULES)
def test_component_imports_alone(name):
    p = subprocess.run([sys.executable, "-c", f"import ledger.{name}"], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-1500:]
