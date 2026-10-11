"""Reference restricted Hartree-Fock, STO-3G, s-type centres only (H, He).

Szabo & Ostlund appendix A formulas. Used to calibrate the hf task's
oracle values; the anchor is H2 at R=1.4 bohr = -1.1167 Ha (S&O table 3.?).
"""
import math
import numpy as np

STO3G_ALPHA = np.array([0.109818, 0.405771, 2.22766])   # for zeta = 1
STO3G_D = np.array([0.444635, 0.535328, 0.154329])
ZETA = {"H": 1.24, "He": 2.0925}
Z = {"H": 1.0, "He": 2.0}


def F0(t):
    return 1.0 if t < 1e-12 else 0.5 * math.sqrt(math.pi / t) * math.erf(math.sqrt(t))


def basis(atoms):
    """One contracted s function per centre: list of (alphas, coeffs incl. norm, R)."""
    out = []
    for sym, R in atoms:
        a = STO3G_ALPHA * ZETA[sym] ** 2
        d = STO3G_D * (2 * a / math.pi) ** 0.75
        out.append((a, d, np.asarray(R, dtype=float)))
    return out


def _s(a, b, RA, RB):
    p = a + b
    return (math.pi / p) ** 1.5 * math.exp(-a * b / p * np.dot(RA - RB, RA - RB))


def _t(a, b, RA, RB):
    p = a + b
    ab2 = np.dot(RA - RB, RA - RB)
    return a * b / p * (3 - 2 * a * b / p * ab2) * (math.pi / p) ** 1.5 * math.exp(-a * b / p * ab2)


def _v(a, b, RA, RB, RC, Zc):
    p = a + b
    RP = (a * RA + b * RB) / p
    return -2 * math.pi / p * Zc * math.exp(-a * b / p * np.dot(RA - RB, RA - RB)) * F0(p * np.dot(RP - RC, RP - RC))


def _eri(a, b, c, d, RA, RB, RC, RD):
    p, q = a + b, c + d
    RP = (a * RA + b * RB) / p
    RQ = (c * RC + d * RD) / q
    return (2 * math.pi ** 2.5 / (p * q * math.sqrt(p + q))
            * math.exp(-a * b / p * np.dot(RA - RB, RA - RB) - c * d / q * np.dot(RC - RD, RC - RD))
            * F0(p * q / (p + q) * np.dot(RP - RQ, RP - RQ)))


def integrals(atoms):
    bf = basis(atoms)
    n = len(bf)
    S, T, V = np.zeros((n, n)), np.zeros((n, n)), np.zeros((n, n))
    for i, (ai, di, Ri) in enumerate(bf):
        for j, (aj, dj, Rj) in enumerate(bf):
            for a, da in zip(ai, di):
                for b, db in zip(aj, dj):
                    S[i, j] += da * db * _s(a, b, Ri, Rj)
                    T[i, j] += da * db * _t(a, b, Ri, Rj)
                    for sym, RC in atoms:
                        V[i, j] += da * db * _v(a, b, Ri, Rj, np.asarray(RC, float), Z[sym])
    ERI = np.zeros((n, n, n, n))
    for i, (ai, di, Ri) in enumerate(bf):
        for j, (aj, dj, Rj) in enumerate(bf):
            for k, (ak, dk, Rk) in enumerate(bf):
                for l, (al, dl, Rl) in enumerate(bf):
                    v = 0.0
                    for a, da in zip(ai, di):
                        for b, db in zip(aj, dj):
                            for c, dc in zip(ak, dk):
                                for d, dd in zip(al, dl):
                                    v += da * db * dc * dd * _eri(a, b, c, d, Ri, Rj, Rk, Rl)
                    ERI[i, j, k, l] = v
    return S, T, V, ERI


def nuclear_repulsion(atoms):
    e = 0.0
    for i, (si, Ri) in enumerate(atoms):
        for j, (sj, Rj) in enumerate(atoms):
            if j > i:
                e += Z[si] * Z[sj] / np.linalg.norm(np.asarray(Ri, float) - np.asarray(Rj, float))
    return e


def rhf(atoms, n_electrons, tol=1e-10, max_iter=200):
    S, T, V, ERI = integrals(atoms)
    H = T + V
    w, U = np.linalg.eigh(S)
    X = U @ np.diag(w ** -0.5) @ U.T
    n = len(S)
    nocc = n_electrons // 2
    P = np.zeros((n, n))
    E_old = 0.0
    for it in range(max_iter):
        G = np.einsum("ls,uvsl->uv", P, ERI) - 0.5 * np.einsum("ls,ulsv->uv", P, ERI)
        F = H + G
        Fp = X.T @ F @ X
        eps, Cp = np.linalg.eigh(Fp)
        Cmo = X @ Cp
        P_new = 2 * Cmo[:, :nocc] @ Cmo[:, :nocc].T
        E = 0.5 * np.sum(P_new * (H + F))
        if abs(E - E_old) < tol and np.max(np.abs(P_new - P)) < tol:
            P = P_new
            break
        P, E_old = P_new, E
    E_el = 0.5 * np.sum(P * (H + H + np.einsum("ls,uvsl->uv", P, ERI) - 0.5 * np.einsum("ls,ulsv->uv", P, ERI)))
    return {"energy": E_el + nuclear_repulsion(atoms), "electronic": E_el,
            "orbital_energies": eps, "iterations": it + 1, "S": S, "T": T, "V": V, "P": P}


if __name__ == "__main__":
    r = rhf([("H", (0, 0, 0)), ("H", (0, 0, 1.4))], 2)
    print(f"H2  R=1.4   E={r['energy']:.6f}  eps={r['orbital_energies']}  S12={r['S'][0,1]:.4f} T11={r['T'][0,0]:.4f} T12={r['T'][0,1]:.4f} V11={r['V'][0,0]:.4f}")
    r = rhf([("He", (0, 0, 0)), ("H", (0, 0, 1.4632))], 2)
    print(f"HeH+ R=1.4632 E={r['energy']:.6f}  eps={r['orbital_energies']}")
    for R in (1.2, 1.3, 1.35, 1.4, 1.45, 1.5, 1.6, 2.0, 3.0):
        print(f"H2 R={R}: {rhf([('H', (0, 0, 0)), ('H', (0, 0, R))], 2)['energy']:.6f}")
    s = 1.65
    tri = [("H", (0, 0, 0)), ("H", (s, 0, 0)), ("H", (s / 2, s * math.sqrt(3) / 2, 0))]
    print(f"H3+ equilateral R=1.65: {rhf(tri, 2)['energy']:.6f}")
    print(f"He atom: {rhf([('He', (0, 0, 0))], 2)['energy']:.6f}")
    print(f"H4 square 1.6: {rhf([('H', (0,0,0)),('H',(1.6,0,0)),('H',(0,1.6,0)),('H',(1.6,1.6,0))], 4)['energy']:.6f}")
