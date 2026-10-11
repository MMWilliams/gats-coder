Implement `delaunay.py`: a 2-D Delaunay triangulation with exact
geometric predicates, in pure Python (standard library only; the tests
block scipy), so that `python -m pytest tests -q` passes within
its time limits.

`delaunay(points: list[tuple[float, float]]) -> list[tuple[int, int, int]]`

- `points` are distinct; there are at least 3 and not all collinear. Up
  to 3000 points within 20 s.
- Returns triangles as index triples. Together they must triangulate the
  convex hull of the points exactly: every point is a vertex of at least
  one triangle, no triangle is degenerate, no two triangles overlap, every
  edge is shared by exactly two triangles unless it lies on the hull, and
  every point that lies on the hull boundary is a vertex of the boundary.
- Delaunay property: no point lies strictly inside the circumcircle of any
  triangle. Points on a circumcircle are allowed (cocircular inputs have
  several valid triangulations).

The tests check these properties with exact rational arithmetic on the
input coordinates, so floating-point orientation and in-circle tests are
not enough on the degenerate inputs (integer grids, points on a circle,
points on a parabola, near-collinear clusters). Use exact arithmetic
(`fractions.Fraction` or integers after scaling) in the predicates, or an
adaptive filter that falls back to it.
