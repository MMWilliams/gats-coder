"""Build the cold-open demo repo: a payments module with one real bug.

    python make_demo_repo.py <dir>      # dir must not exist
"""
import subprocess
import sys
from pathlib import Path

FILES = {
    "payments.py": '''\
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def to_cents(amount):
    return Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)


def invoice_total(lines, discount_pct=0, tax_pct=0):
    """Sum of qty * unit price, discount applied before tax, rounded to cents."""
    subtotal = sum(Decimal(str(q)) * Decimal(str(p)) for q, p in lines)
    discounted = subtotal * (1 - Decimal(str(discount_pct)) / 100)
    taxed = subtotal * (1 + Decimal(str(tax_pct)) / 100)
    return to_cents(taxed)


def refund(total, already_refunded, amount):
    """Refund amount, never more than what is left on the invoice."""
    left = to_cents(total) - to_cents(already_refunded)
    if to_cents(amount) > left:
        raise ValueError("refund exceeds remaining balance")
    return to_cents(amount)
''',
    "tests/test_payments.py": '''\
import pytest
from decimal import Decimal

from payments import invoice_total, refund


def test_plain_total():
    assert invoice_total([(2, "9.99"), (1, "5.00")]) == Decimal("24.98")


def test_discount_applies_before_tax():
    # 100.00 less 10% = 90.00, plus 8% tax = 97.20
    assert invoice_total([(1, "100.00")], discount_pct=10, tax_pct=8) == Decimal("97.20")


def test_rounds_half_up_to_cents():
    assert invoice_total([(3, "0.335")]) == Decimal("1.01")


def test_refund_capped_at_balance():
    assert refund("50.00", "20.00", "30.00") == Decimal("30.00")
    with pytest.raises(ValueError):
        refund("50.00", "20.00", "30.01")
''',
    "tests/__init__.py": "",
    "AGENTS.md": "Money is Decimal, never float. Keep fixes minimal; never edit tests.\n",
}


def main(dest):
    root = Path(dest)
    root.mkdir(parents=True)
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                    capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("-c", "user.name=demo", "-c", "user.email=demo@example.com",
        "commit", "-q", "-m", "payments module")
    print(root)


if __name__ == "__main__":
    main(sys.argv[1])
