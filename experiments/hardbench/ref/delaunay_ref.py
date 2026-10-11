"""Reference Delaunay triangulation with exact integer predicates.

Incremental insertion in lexicographic order (every new point is outside or
on the boundary of the current hull, so no point location is needed), then
Lawson edge flips. Coordinates are floats, hence dyadic rationals, so one
common power-of-two scaling makes every coordinate an integer and all
predicates exact.
"""
import sys
from fractions import Fraction

sys.setrecursionlimit(20000)


def _exact(points):
    fr = [(Fraction(x), Fraction(y)) for x, y in points]
    d = 1
    for x, y in fr:
        d = max(d, x.denominator, y.denominator)   # denominators are powers of two
    return [(int(x * d), int(y * d)) for x, y in fr]


def orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def in_circle(a, b, c, d):
    adx, ady = a[0] - d[0], a[1] - d[1]
    bdx, bdy = b[0] - d[0], b[1] - d[1]
    cdx, cdy = c[0] - d[0], c[1] - d[1]
    return ((adx * adx + ady * ady) * (bdx * cdy - cdx * bdy)
            - (bdx * bdx + bdy * bdy) * (adx * cdy - cdx * ady)
            + (cdx * cdx + cdy * cdy) * (adx * bdy - bdx * ady))


def delaunay(points):
    P = _exact(points)
    n = len(P)
    order = sorted(range(n), key=lambda i: P[i])
    # seed with the first non-collinear triple
    k = 2
    while k < n and orient(P[order[0]], P[order[1]], P[order[k]]) == 0:
        k += 1
    if k == n:
        raise ValueError("all points collinear")
    tris = {}            # id -> (a, b, c) ccw
    edge_tris = {}       # frozenset({a, b}) -> set of triangle ids
    next_id = [0]

    def add(a, b, c):
        if orient(P[a], P[b], P[c]) < 0:
            b, c = c, b
        t = next_id[0]
        next_id[0] += 1
        tris[t] = (a, b, c)
        for e in ((a, b), (b, c), (c, a)):
            edge_tris.setdefault(frozenset(e), set()).add(t)
        return t

    def remove(t):
        a, b, c = tris.pop(t)
        for e in ((a, b), (b, c), (c, a)):
            s = edge_tris[frozenset(e)]
            s.discard(t)
            if not s:
                del edge_tris[frozenset(e)]

    def opposite(t, a, b):
        return next(v for v in tris[t] if v != a and v != b)

    def legalize(t, a, b):
        """Edge (a, b) of triangle t: flip if the neighbour's apex is inside."""
        if t not in tris:          # flipped away by a nested call
            return
        e = frozenset((a, b))
        others = edge_tris.get(e, set()) - {t}
        if not others:
            return
        u = next(iter(others))
        c = opposite(t, a, b)
        d = opposite(u, a, b)
        if in_circle(*(P[v] for v in tris[t]), P[d]) > 0:
            remove(t)
            remove(u)
            t1 = add(c, d, a)
            t2 = add(d, c, b)
            # every outer edge of the two new triangles may now be illegal
            for tt in (t1, t2):
                x, y, z = tris[tt]
                for p, q in ((x, y), (y, z), (z, x)):
                    if {p, q} != {c, d}:
                        legalize(tt, p, q)

    a, b, c = order[0], order[1], order[k]
    add(a, b, c)
    inserted = {a, b, c}
    # collinear points skipped between order[2] and order[k-1] must be inserted too
    pending = [order[i] for i in range(2, k)] + order[k + 1:]
    for p in pending:
        if p in inserted:
            continue
        inserted.add(p)
        hull = [(e, next(iter(s))) for e, s in edge_tris.items() if len(s) == 1]
        new = []
        on_edge = None
        for e, t in hull:
            u, v = tuple(e)
            x, y, z = tris[t]
            # directed hull edge so that the triangle is on its left
            if (x, y) in ((u, v), (v, u)):
                u, v = x, y
            elif (y, z) in ((u, v), (v, u)):
                u, v = y, z
            else:
                u, v = z, x
            o = orient(P[u], P[v], P[p])
            if o < 0:
                new.append((u, v))
            elif o == 0:
                lo, hi = min(P[u], P[v]), max(P[u], P[v])
                if lo < P[p] < hi:
                    on_edge = (t, u, v)
        if on_edge is not None:
            t, u, v = on_edge
            w = opposite(t, u, v)
            remove(t)
            t1 = add(u, p, w)
            t2 = add(p, v, w)
            legalize(t1, u, w)
            legalize(t2, v, w)
            continue
        made = []
        for u, v in new:
            made.append((add(v, u, p), u, v))
        for t, u, v in made:
            legalize(t, u, v)
    return [tuple(t) for t in tris.values()]


if __name__ == "__main__":
    import math
    import random
    import time
    rng = random.Random(1)
    pts = [(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(3000)]
    t = time.time()
    tr = delaunay(pts)
    print(len(tr), f"{time.time() - t:.1f}s")
    pts = [(x, x * x) for x in [i / 50.0 for i in range(-400, 401)]] + [(0.0, 100.0)]
    t = time.time()
    tr = delaunay(pts)
    print(len(tr), f"{time.time() - t:.1f}s")
