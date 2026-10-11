import random
import time

import pytest

from sat import solve


def random_3sat(n, seed, ratio=4.26):
    rng = random.Random(seed)
    cls = []
    for _ in range(round(ratio * n)):
        vs = rng.sample(range(1, n + 1), 3)
        cls.append([v if rng.random() < 0.5 else -v for v in vs])
    return n, cls


def pigeonhole(holes):
    pigeons = holes + 1
    var = lambda p, h: p * holes + h + 1
    cls = [[var(p, h) for h in range(holes)] for p in range(pigeons)]
    for h in range(holes):
        for p in range(pigeons):
            for q in range(p + 1, pigeons):
                cls.append([-var(p, h), -var(q, h)])
    return pigeons * holes, cls


def satisfied(clauses, assignment):
    s = set(assignment)
    return all(any(l in s for l in c) for c in clauses)


def run(n, cls, expect_sat, limit=10.0):
    t = time.time()
    r = solve(n, cls, time_limit=limit)
    elapsed = time.time() - t
    assert elapsed < limit + 1.0, f"took {elapsed:.1f}s"
    if expect_sat:
        assert r is not None, "claimed UNSAT on a satisfiable formula"
        assert len(r) == n and sorted(abs(l) for l in r) == list(range(1, n + 1))
        assert satisfied(cls, r)
    else:
        assert r is None, "claimed SAT on an unsatisfiable formula"


def test_trivial_cases():
    assert solve(1, []) in ([1], [-1])
    assert solve(2, [[]]) is None
    assert solve(1, [[1], [-1]]) is None
    assert solve(3, [[1, 1, -2], [2, -2, 3], [-3]]) is not None     # duplicates, tautology
    r = solve(4, [[1], [-2]])
    assert r[0] == 1 and r[1] == -2 and len(r) == 4                   # unused vars still assigned


@pytest.mark.parametrize("holes", [5, 6, 7])
def test_pigeonhole_is_unsat(holes):
    run(*pigeonhole(holes), expect_sat=False)


LABELS = {(50, 1): False, (50, 2): True, (50, 3): True,
          (100, 1): True, (100, 2): False, (100, 3): True,
          (150, 1): True, (150, 2): True, (150, 3): True}


@pytest.mark.parametrize("n,seed", sorted(LABELS))
def test_random_3sat_at_threshold(n, seed):
    run(*random_3sat(n, seed), expect_sat=LABELS[(n, seed)])


def test_timeout_is_raised_not_swallowed():
    n, cls = pigeonhole(9)        # far beyond any 0.05 s budget
    with pytest.raises(TimeoutError):
        solve(n, cls, time_limit=0.05)
