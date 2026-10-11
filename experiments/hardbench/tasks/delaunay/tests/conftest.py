"""Property checks in exact arithmetic; scipy only to count triangles on
general-position inputs, and off limits for the implementation."""
import importlib
import sys
from collections import Counter
from fractions import Fraction

try:
    spatial = importlib.import_module("scipy.spatial")
except ImportError:
    spatial = None
sys.modules["scipy"] = None      # numpy stays importable: Qhull needs it at call time


def orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def in_circle(a, b, c, d):
    """> 0 iff d is strictly inside the circle through a, b, c (ccw)."""
    adx, ady = a[0] - d[0], a[1] - d[1]
    bdx, bdy = b[0] - d[0], b[1] - d[1]
    cdx, cdy = c[0] - d[0], c[1] - d[1]
    return ((adx * adx + ady * ady) * (bdx * cdy - cdx * bdy)
            - (bdx * bdx + bdy * bdy) * (adx * cdy - cdx * ady)
            + (cdx * cdx + cdy * cdy) * (adx * bdy - bdx * ady))


def hull(pts):
    """Andrew's monotone chain; returns hull vertex indices, collinear boundary points included."""
    idx = sorted(range(len(pts)), key=lambda i: pts[i])
    lower, upper = [], []
    for i in idx:
        while len(lower) >= 2 and orient(pts[lower[-2]], pts[lower[-1]], pts[i]) < 0:
            lower.pop()
        lower.append(i)
    for i in reversed(idx):
        while len(upper) >= 2 and orient(pts[upper[-2]], pts[upper[-1]], pts[i]) < 0:
            upper.pop()
        upper.append(i)
    return lower[:-1] + upper[:-1]


def check_triangulation(points, tris):
    pts = [(Fraction(x), Fraction(y)) for x, y in points]
    n = len(pts)
    assert tris, "no triangles"
    used = set()
    area = Fraction(0)
    edges = Counter()
    oriented = []
    for t in tris:
        assert len(t) == 3 and len(set(t)) == 3 and all(0 <= i < n for i in t), t
        a, b, c = t
        o = orient(pts[a], pts[b], pts[c])
        assert o != 0, f"degenerate triangle {t}"
        if o < 0:
            a, b, c = a, c, b
        oriented.append((a, b, c))
        area += abs(o)
        used.update(t)
        for e in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted(e))] += 1
    assert used == set(range(n)), f"points not covered: {set(range(n)) - used}"
    h = hull(pts)
    hull_area = Fraction(0)
    for i in range(len(h)):
        p, q = pts[h[i]], pts[h[(i + 1) % len(h)]]
        hull_area += p[0] * q[1] - q[0] * p[1]
    assert area == abs(hull_area), "triangles do not tile the hull exactly"
    hull_edges = {tuple(sorted((h[i], h[(i + 1) % len(h)]))) for i in range(len(h))}
    for e, k in edges.items():
        if e in hull_edges:
            assert k == 1, f"hull edge {e} used {k} times"
        else:
            assert k == 2, f"interior edge {e} used {k} times"
    assert hull_edges <= set(edges), "a hull edge is missing"
    return oriented


def check_delaunay(points, tris):
    pts = [(Fraction(x), Fraction(y)) for x, y in points]
    oriented = check_triangulation(points, tris)
    # Delaunay <=> locally Delaunay across every interior edge, which the
    # edge-flip theorem makes equivalent to the global empty-circle property.
    by_edge = {}
    for t in oriented:
        for i in range(3):
            e = tuple(sorted((t[i], t[(i + 1) % 3])))
            by_edge.setdefault(e, []).append(t[(i + 2) % 3])
    for e, opp in by_edge.items():
        if len(opp) == 2:
            a, b = e
            c, d = opp
            o = orient(pts[a], pts[b], pts[c])
            tri = (a, b, c) if o > 0 else (a, c, b)
            assert in_circle(pts[tri[0]], pts[tri[1]], pts[tri[2]], pts[d]) <= 0, \
                f"point {d} inside circumcircle of {tri}"
