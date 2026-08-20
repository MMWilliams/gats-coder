

def test_python_dash_m_ledger_works_without_path():
    """The console script lands in a Scripts directory that is frequently
    not on PATH -- on Windows the failure reads "ledger is not recognized",
    as though the install had not happened. `python -m ledger` needs only
    the interpreter that did the installing, so it must keep working."""
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "-m", "ledger", "--version"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "ledger" in proc.stdout.lower()
