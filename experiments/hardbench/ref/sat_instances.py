"""Instance generators for the sat task, labelled with pysat, timed with the
reference CDCL and the naive DPLL so the limits discriminate."""
import random
import time


def random_3sat(n, seed, ratio=4.26):
    rng = random.Random(seed)
    m = round(ratio * n)
    cls = []
    for _ in range(m):
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


def check(clauses, assignment):
    s = set(assignment)
    return all(any(l in s for l in c) for c in clauses)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from pysat.solvers import Minisat22
    from sat_ref import solve_cdcl, solve_dpll

    cases = [("php", k, pigeonhole(k)) for k in (5, 6, 7, 8)]
    for n in (50, 100, 150, 200, 250):
        for seed in (1, 2, 3):
            cases.append(("r3", f"{n}/{seed}", random_3sat(n, seed)))
    for kind, tag, (n, cls) in cases:
        with Minisat22(bootstrap_with=cls) as m:
            label = m.solve()
        t = time.time()
        try:
            r = solve_cdcl(n, cls, 120)
            ok = (r is None) == (not label) and (r is None or check(cls, r))
            tc = f"{time.time() - t:.2f}s {'ok' if ok else 'WRONG'}"
        except TimeoutError:
            tc = "TIMEOUT"
        t = time.time()
        try:
            r = solve_dpll(n, cls, 30)
            td = f"{time.time() - t:.2f}s"
        except TimeoutError:
            td = ">30s"
        print(f"{kind} {tag:8} n={n:4} m={len(cls):5} {'SAT' if label else 'UNSAT':5} cdcl={tc:14} dpll={td}", flush=True)
