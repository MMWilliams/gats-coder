"""Reference solvers for the sat task's calibration: a Python CDCL (two watched
literals, VSIDS, 1-UIP learning, phase saving, Luby restarts) and a naive
DPLL, so the time limits sit between the two.
"""
import time


def _clean(clauses):
    out = []
    for c in clauses:
        c = list(dict.fromkeys(c))
        if any(-l in c for l in c):
            continue
        out.append(c)
    return out


def solve_cdcl(n_vars, clauses, time_limit=10.0):
    deadline = time.time() + time_limit
    cls = _clean(clauses)
    if any(len(c) == 0 for c in cls):
        return None
    val = [0] * (n_vars + 1)
    level = [0] * (n_vars + 1)
    reason = [None] * (n_vars + 1)
    phase = [False] * (n_vars + 1)
    act = [0.0] * (n_vars + 1)
    trail, lim = [], []
    qhead = [0]
    watches = {}

    def lv(l):
        v = val[abs(l)]
        return 0 if v == 0 else (v if l > 0 else -v)

    def assign(l, r):
        val[abs(l)] = 1 if l > 0 else -1
        level[abs(l)] = len(lim)
        reason[abs(l)] = r
        trail.append(l)

    def watch(ci):
        for l in cls[ci][:2]:
            watches.setdefault(l, []).append(ci)

    for ci, c in enumerate(cls):
        if len(c) == 1:
            if lv(c[0]) == -1:
                return None
            if lv(c[0]) == 0:
                assign(c[0], None)
        else:
            watch(ci)

    def propagate():
        i = qhead[0]
        while i < len(trail):
            fl = -trail[i]
            i += 1
            ws = watches.get(fl)
            if not ws:
                continue
            keep = []
            j = 0
            while j < len(ws):
                ci = ws[j]
                j += 1
                c = cls[ci]
                if c[0] == fl:
                    c[0], c[1] = c[1], c[0]
                if lv(c[0]) == 1:
                    keep.append(ci)
                    continue
                moved = False
                for k in range(2, len(c)):
                    if lv(c[k]) != -1:
                        c[1], c[k] = c[k], c[1]
                        watches.setdefault(c[1], []).append(ci)
                        moved = True
                        break
                if moved:
                    continue
                keep.append(ci)
                if lv(c[0]) == -1:
                    keep.extend(ws[j:])
                    watches[fl] = keep
                    qhead[0] = len(trail)
                    return ci
                assign(c[0], ci)
            watches[fl] = keep
        qhead[0] = i
        return None

    inc = [1.0]

    def bump(v):
        act[v] += inc[0]
        if act[v] > 1e100:
            for u in range(1, n_vars + 1):
                act[u] *= 1e-100
            inc[0] *= 1e-100

    def analyze(ci):
        seen = [False] * (n_vars + 1)
        learnt = []
        counter = 0
        p = None
        idx = len(trail) - 1
        c = cls[ci]
        cur = len(lim)
        while True:
            for l in (c if p is None else c[1:]):
                v = abs(l)
                if not seen[v] and level[v] > 0:
                    seen[v] = True
                    bump(v)
                    if level[v] == cur:
                        counter += 1
                    else:
                        learnt.append(l)
            while not seen[abs(trail[idx])]:
                idx -= 1
            p = trail[idx]
            idx -= 1
            counter -= 1
            if counter == 0:
                break
            c = cls[reason[abs(p)]]
        learnt = [-p] + learnt
        if len(learnt) > 1:
            k = max(range(1, len(learnt)), key=lambda i: level[abs(learnt[i])])
            learnt[1], learnt[k] = learnt[k], learnt[1]
            bl = level[abs(learnt[1])]
        else:
            bl = 0
        return learnt, bl

    def backjump(bl):
        while len(lim) > bl:
            start = lim.pop()
            for l in trail[start:]:
                v = abs(l)
                phase[v] = l > 0
                val[v] = 0
                reason[v] = None
            del trail[start:]
        qhead[0] = min(qhead[0], len(trail))

    def luby(i):
        k = 1
        while (1 << k) - 1 < i:
            k += 1
        while True:
            if i == (1 << k) - 1:
                return 1 << (k - 1)
            k -= 1
            if (1 << k) - 1 < i:
                i -= (1 << k) - 1
                k += 1

    conflicts = 0
    restart_i = 1
    budget = luby(1) * 100
    while True:
        ci = propagate()
        if ci is not None:
            conflicts += 1
            if conflicts % 64 == 0 and time.time() > deadline:
                raise TimeoutError
            if not lim:
                return None
            learnt, bl = analyze(ci)
            backjump(bl)
            cls.append(learnt)
            if len(learnt) > 1:
                watch(len(cls) - 1)
                assign(learnt[0], len(cls) - 1)
            else:
                assign(learnt[0], None)
            inc[0] *= 1.0 / 0.95
            budget -= 1
            if budget <= 0:
                backjump(0)
                restart_i += 1
                budget = luby(restart_i) * 100
            continue
        best, bv = -1.0, 0
        for v in range(1, n_vars + 1):
            if val[v] == 0 and act[v] > best:
                best, bv = act[v], v
        if bv == 0:
            return [v if val[v] == 1 else -v for v in range(1, n_vars + 1)]
        lim.append(len(trail))
        assign(bv if phase[bv] else -bv, None)


def solve_dpll(n_vars, clauses, time_limit=10.0):
    deadline = time.time() + time_limit
    cls = _clean(clauses)
    calls = [0]

    def rec(cls, assn):
        calls[0] += 1
        if calls[0] % 256 == 0 and time.time() > deadline:
            raise TimeoutError
        while True:
            unit = None
            for c in cls:
                if len(c) == 1:
                    unit = c[0]
                    break
            if unit is None:
                break
            cls, assn = simplify(cls, unit, assn)
            if cls is None:
                return None
        if not cls:
            return assn
        l = cls[0][0]
        for lit in (l, -l):
            c2, a2 = simplify(cls, lit, assn)
            if c2 is not None:
                r = rec(c2, a2)
                if r is not None:
                    return r
        return None

    def simplify(cls, lit, assn):
        out = []
        for c in cls:
            if lit in c:
                continue
            if -lit in c:
                c = [l for l in c if l != -lit]
                if not c:
                    return None, None
            out.append(c)
        return out, assn + [lit]

    if any(len(c) == 0 for c in cls):
        return None
    r = rec(cls, [])
    if r is None:
        return None
    got = {abs(l): l for l in r}
    return [got.get(v, v) for v in range(1, n_vars + 1)]
