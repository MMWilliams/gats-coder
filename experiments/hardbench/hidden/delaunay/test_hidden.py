import math
import random
import time

import pytest
from conftest import check_delaunay, spatial

from delaunay import delaunay


def timed(points, limit=20.0):
    t = time.time()
    tris = delaunay(points)
    assert time.time() - t < limit, f"took {time.time() - t:.1f}s"
    return tris


@pytest.mark.parametrize("n,seed", [(200, 11), (1500, 12), (3000, 13)])
def test_random_unseen(n, seed):
    rng = random.Random(seed)
    pts = [(rng.gauss(0, 50), rng.gauss(0, 50)) for _ in range(n)]
    tris = timed(pts)
    check_delaunay(pts, tris)
    if spatial is not None:
        assert len(tris) == len(spatial.Delaunay(pts).simplices)


def test_two_concentric_circles():
    pts = [(r * math.cos(2 * math.pi * k / 30), r * math.sin(2 * math.pi * k / 30))
           for r in (1.0, 2.0) for k in range(30)]
    tris = timed(pts)
    check_delaunay(pts, tris)


def test_grid_with_a_rotation_and_offset():
    c, s = math.cos(0.3), math.sin(0.3)
    pts = [(c * i - s * j + 0.123, s * i + c * j - 4.56) for i in range(25) for j in range(25)]
    # after a float rotation the boundary is no longer exactly collinear, so
    # only the exact properties are checked, not a triangle count
    check_delaunay(pts, timed(pts))


def test_collinear_points_on_the_hull_boundary():
    pts = [(float(i), 0.0) for i in range(30)] + [(float(i), 10.0) for i in range(30)] + [(15.0, 5.0)]
    tris = timed(pts)
    check_delaunay(pts, tris)


def test_tiny_and_huge_coordinates():
    rng = random.Random(21)
    pts = [(rng.uniform(-1e-9, 1e-9), rng.uniform(-1e-9, 1e-9)) for _ in range(300)]
    check_delaunay(pts, timed(pts))
    pts = [(rng.uniform(-1e12, 1e12), rng.uniform(-1e12, 1e12)) for _ in range(300)]
    check_delaunay(pts, timed(pts))


def test_parabola_dense():
    pts = [(x, x * x) for x in [i / 50.0 for i in range(-400, 401)]] + [(0.0, 100.0)]
    check_delaunay(pts, timed(pts, limit=30))


def test_lattice_with_duplicated_rows_offset_by_half():
    pts = [(i + (0.5 if j % 2 else 0.0), j * math.sqrt(3) / 2) for i in range(30) for j in range(30)]
    tris = timed(pts)
    check_delaunay(pts, tris)
