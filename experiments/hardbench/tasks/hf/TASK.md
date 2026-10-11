Implement `hf.py` (numpy only): restricted closed-shell Hartree-Fock in a
minimal STO-3G basis of s-type functions, from scratch, so that
`python -m pytest tests -q` passes.

`rhf(atoms, n_electrons, tol=1e-10, max_iter=200) -> dict`

- `atoms` is a list of `(symbol, (x, y, z))` with coordinates in bohr;
  symbols are "H" and "He". One contracted s function per atom.
- STO-3G: for Slater exponent zeta = 1 the primitive exponents are
  0.109818, 0.405771, 2.22766 with contraction coefficients 0.444635,
  0.535328, 0.154329; scale the exponents by zeta**2 and normalise each
  primitive by (2 alpha / pi)**0.75. Use zeta = 1.24 for H and 2.0925 for He
  (Szabo & Ostlund). Energies in hartree.
- Integrals over s-type Gaussians (Szabo & Ostlund appendix A): overlap,
  kinetic, nuclear attraction via the Boys function F0, and the two-electron
  integrals (ij|kl) in chemists' notation. Build them yourself; no scipy, no
  quantum chemistry packages.
- SCF: symmetric orthogonalisation X = S**-1/2, Fock matrix
  F = H_core + G with G_uv = sum_ls P_ls [(uv|ls) - 1/2 (ul|sv)],
  P = 2 C_occ C_occ^T, electronic energy E = 1/2 sum P (H_core + F); iterate
  to convergence of the energy and density.

Return keys: "energy" (total, electronic + nuclear repulsion), "electronic",
"nuclear_repulsion", "orbital_energies" (ascending numpy array), "S", "T",
"V", "eri" (4-index array), "P", "iterations".

Reference points: H2 at R = 1.4 bohr has total energy -1.1167 Ha and
occupied orbital energy -0.5782 Ha.
