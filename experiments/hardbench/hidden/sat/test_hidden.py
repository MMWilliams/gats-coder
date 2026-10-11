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


def run(n, cls, expect_sat, limit):
    t = time.time()
    r = solve(n, cls, time_limit=limit)
    assert time.time() - t < limit + 1.0
    if expect_sat:
        assert r is not None and len(r) == n
        assert sorted(abs(l) for l in r) == list(range(1, n + 1))
        assert satisfied(cls, r)
    else:
        assert r is None


LABELS = {(120, 11): False, (120, 12): True, (120, 13): True, (120, 14): False,
          (140, 11): True, (140, 12): True, (140, 13): True, (140, 14): False,
          (160, 11): True, (160, 12): False, (160, 13): True, (160, 14): False}


@pytest.mark.parametrize("n,seed", sorted(LABELS))
def test_unseen_random_3sat(n, seed):
    run(*random_3sat(n, seed), expect_sat=LABELS[(n, seed)], limit=10.0 if n < 160 else 20.0)


def test_pigeonhole_with_shuffled_clauses_and_renamed_variables():
    n, cls = pigeonhole(7)
    rng = random.Random(7)
    perm = list(range(1, n + 1))
    rng.shuffle(perm)
    cls = [[(perm[abs(l) - 1] if l > 0 else -perm[abs(l) - 1]) for l in c] for c in cls]
    rng.shuffle(cls)
    run(n, cls, expect_sat=False, limit=10.0)


def test_long_implication_chain_is_fast():
    n = 20000
    cls = [[1]] + [[-i, i + 1] for i in range(1, n)]
    t = time.time()
    r = solve(n, cls, time_limit=10.0)
    assert time.time() - t < 5.0
    assert r == list(range(1, n + 1))


def test_assignment_covers_variables_absent_from_every_clause():
    r = solve(6, [[2, -3], [3]])
    assert r is not None and len(r) == 6 and satisfied([[2, -3], [3]], r)


def test_many_duplicate_clauses_do_not_slow_it_down():
    n, cls = random_3sat(100, 3)
    cls = cls * 40
    run(n, cls, expect_sat=True, limit=10.0)
