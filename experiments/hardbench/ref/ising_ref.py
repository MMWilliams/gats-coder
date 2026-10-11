"""Reference 2-D Ising Wolff-cluster Monte Carlo, to calibrate the ising task
against Onsager's exact energy and magnetisation."""
import math
import random
import time
from collections import deque


def onsager_energy(T):
    from scipy.special import ellipk
    K = 1.0 / T
    k = 2 * math.sinh(2 * K) / math.cosh(2 * K) ** 2
    kp = 2 * math.tanh(2 * K) ** 2 - 1
    return -1 / math.tanh(2 * K) * (1 + 2 / math.pi * kp * ellipk(k * k))


def onsager_magnetization(T):
    K = 1.0 / T
    s = math.sinh(2 * K)
    return (1 - s ** -4) ** 0.125 if s > 1 else 0.0


def simulate(L, T, flips, seed, thermal=None):
    rng = random.Random(seed)
    n = L * L
    spin = [1] * n
    p_add = 1 - math.exp(-2.0 / T)
    thermal = flips // 5 if thermal is None else thermal
    e_sum = m_sum = m2 = m4 = 0.0
    count = 0
    nbr = [((i - L) % n, (i + L) % n, (i // L) * L + (i + 1) % L, (i // L) * L + (i - 1) % L)
           for i in range(n)]
    for step in range(flips + thermal):
        seed_site = rng.randrange(n)
        s = spin[seed_site]
        spin[seed_site] = -s
        q = deque([seed_site])
        while q:
            i = q.popleft()
            for j in nbr[i]:
                if spin[j] == s and rng.random() < p_add:
                    spin[j] = -s
                    q.append(j)
        if step >= thermal:
            e = 0
            for i in range(n):
                e -= spin[i] * (spin[nbr[i][1]] + spin[nbr[i][2]])
            m = abs(sum(spin)) / n
            e_sum += e / n
            m_sum += m
            m2 += m * m
            m4 += m ** 4
            count += 1
    return {"energy": e_sum / count, "magnetization": m_sum / count,
            "binder": 1 - (m4 / count) / (3 * (m2 / count) ** 2)}


def exact_small(L, T):
    """Exact energy and |m| per spin for a tiny periodic lattice by enumeration."""
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


def estimate_tc(sizes=(8, 16, 24), seed=0, flips=4000):
    """Binder cumulant crossing of the two largest sizes, on a grid around Tc."""
    Ts = [2.15 + 0.02 * k for k in range(13)]
    U = {L: [simulate(L, T, flips, seed + 97 * L + k)["binder"] for k, T in enumerate(Ts)]
         for L in sizes}
    a, b = sizes[-2], sizes[-1]
    d = [U[a][k] - U[b][k] for k in range(len(Ts))]
    for k in range(len(Ts) - 1):
        if d[k] <= 0 <= d[k + 1] or d[k] >= 0 >= d[k + 1]:
            t = d[k] / (d[k] - d[k + 1]) if d[k] != d[k + 1] else 0.5
            return Ts[k] + t * (Ts[k + 1] - Ts[k])
    return Ts[min(range(len(Ts)), key=lambda k: abs(d[k]))]


if __name__ == "__main__":
    for L, T, flips in ((32, 2.0, 2000), (32, 3.5, 2000), (16, 2.269, 4000), (8, 2.269, 4000),
                        (24, 2.269, 4000)):
        t = time.time()
        r = simulate(L, T, flips, seed=1)
        print(f"L={L} T={T} flips={flips}: E={r['energy']:.4f} (exact {onsager_energy(T):.4f})"
              f"  m={r['magnetization']:.4f} (exact {onsager_magnetization(T):.4f})"
              f"  U4={r['binder']:.3f}  {time.time() - t:.1f}s")

    t = time.time()
    for T in (1.5, 2.0, 2.269, 3.0):
        ex = exact_small(4, T)
        r = simulate(4, T, 20000, seed=2)
        print(f"L=4 T={T}: exact E={ex[0]:.4f} m={ex[1]:.4f}  mc E={r['energy']:.4f} m={r['magnetization']:.4f}")
    print(f"  exact+mc L=4: {time.time()-t:.1f}s")
    for seed in (0, 1, 2):
        t = time.time(); tc = estimate_tc(seed=seed)
        print(f"estimate_tc seed={seed}: {tc:.4f} (exact 2.2692) {time.time()-t:.1f}s")
    t = time.time(); r = simulate(64, 2.0, 500, seed=3); print(f"L=64 T=2 500 flips: E={r['energy']:.4f} {time.time()-t:.1f}s")
    r = simulate(32, 2.4, 4000, seed=4); print(f"L=32 T=2.4: E={r['energy']:.4f} exact {onsager_energy(2.4):.4f}")
    r = simulate(8, 2.2, 4000, seed=5); print(f"L=8 T=2.2: m={r['magnetization']:.4f} (inf {onsager_magnetization(2.2):.4f})")
