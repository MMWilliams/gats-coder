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


def test_square_and_triangle():
    assert len(timed([(0, 0), (1, 0), (0, 1)])) == 1
    sq = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    tris = timed(sq)
    assert len(tris) == 2
    check_delaunay(sq, tris)


@pytest.mark.parametrize("n,seed", [(50, 1), (500, 2), (3000, 3)])
def test_random_points(n, seed):
    rng = random.Random(seed)
    pts = [(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(n)]
    tris = timed(pts)
    check_delaunay(pts, tris)
    if spatial is not None:
        assert len(tris) == len(spatial.Delaunay(pts).simplices)


def test_integer_grid_is_massively_cocircular():
    pts = [(float(i), float(j)) for i in range(12) for j in range(12)]
    tris = timed(pts)
    check_delaunay(pts, tris)
    assert len(tris) == 2 * 11 * 11


def test_points_on_a_circle():
    pts = [(math.cos(2 * math.pi * k / 40), math.sin(2 * math.pi * k / 40)) for k in range(40)]
    tris = timed(pts)
    check_delaunay(pts, tris)
    assert len(tris) == 38


def test_points_on_a_parabola_break_floating_predicates():
    pts = [(x, x * x) for x in [i / 7.0 for i in range(-60, 61)]] + [(0.0, 30.0), (-1e-9, 5.0)]
    tris = timed(pts)
    check_delaunay(pts, tris)


def test_near_collinear_cluster_with_one_far_point():
    rng = random.Random(9)
    pts = [(i * 1e-3, 1e-12 * rng.random()) for i in range(200)] + [(0.1, 1.0)]
    tris = timed(pts)
    check_delaunay(pts, tris)
