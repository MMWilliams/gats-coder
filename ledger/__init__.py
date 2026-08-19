"""LEDGER - GATS-planned, audit-first autonomous coding agent.

M1 scope: deterministic core port of GATS (arXiv:2607.08894) Alg. 1/2 --
persistent state-transition graph, UCB1 edge search, layered world model
(L1 symbolic; L2/L3 interfaces), BFS StateValue, hash-chained journal,
bit-exact replay, and a minimal sensor bank.
"""

# Stamped into every journal HEADER. release.ps1 syncs it from
# pyproject.toml -- do not hand-edit out of step with the package version.
AGENT_VERSION = "0.8.2"
