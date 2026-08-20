"""`python -m ledger` -- the fallback that works without PATH.

Installing a console script puts `ledger` in a Scripts directory that is
often not on PATH (the usual Windows case), and the failure reads as
"ledger is not recognized", i.e. as if nothing were installed at all.
`python -m ledger` needs only the interpreter that did the installing.
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
