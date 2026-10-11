"""Exact results the simulation is held to."""
import math

from scipy.special import ellipk

TC = 2 / math.log(1 + math.sqrt(2))


def onsager_energy(T):
    K = 1.0 / T
    k = 2 * math.sinh(2 * K) / math.cosh(2 * K) ** 2
    kp = 2 * math.tanh(2 * K) ** 2 - 1
    return -1 / math.tanh(2 * K) * (1 + 2 / math.pi * kp * ellipk(k * k))


def onsager_magnetization(T):
    s = math.sinh(2.0 / T)
    return (1 - s ** -4) ** 0.125 if s > 1 else 0.0


def exact_small(L, T):
    """Energy and |m| per spin of a tiny periodic lattice, by enumeration."""
    n = L * L
    bonds = [(i, (i // L) * L + (i + 1) % L) for i in range(n)] + [(i, (i + L) % n) for i in range(n)]
    Z = E = M = 0.0
    for state in range(1 << n):
        s = [1 if state >> i & 1 else -1 for i in range(n)]
        e = -sum(s[a] * s[b] for a, b in bonds)
        w = math.exp(-e / T)
        Z += w
        E += w * e
        M += w * abs(sum(s))
    return E / Z / n, M / Z / n
