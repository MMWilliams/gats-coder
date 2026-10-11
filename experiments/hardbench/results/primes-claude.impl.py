"""Exact prime counting in pure Python (standard library only).

count_primes(n) returns the number of primes <= n.

    >>> from primes import count_primes
    >>> count_primes(100)
    25
    >>> count_primes(10**6)
    78498

From the shell:

    $ python primes.py 1000000
    78498

Method: Lagarias-Miller-Odlyzko with Deleglise-Rivat easy leaves.  With
y = icbrt(n) and a = pi(y),

    pi(n) = phi(n, a) + a - 1 - P2(n, a),

where phi(v, j) counts the integers in [1, v] free of the first j primes
and P2 counts the products p*q <= n of primes y < p <= q.  phi(n, a)
unrolls into ordinary leaves mu(m) * phi(n//m, 10) for m <= y, which have
a closed form, and special leaves phi(v, j) with v <= n//(y+1).  Leaves
with v < p_j**2 read pi(v) from a table up to isqrt(n); the rest are
counted on a bit-packed mod-30 wheel sieve of [1, n//(y+1)] while it is
crossed off one prime at a time, and the finished sieve gives the pi(n//p)
terms of P2.  All arithmetic is exact integer arithmetic.
"""

from bisect import bisect_right as _bisect_right
from functools import cache as _cache
from itertools import accumulate as _accumulate, compress as _compress
from math import isqrt as _isqrt
from operator import index as _index, mul as _mul

__all__ = ["count_primes"]

_SMALL = 1 << 20  # n below this is answered from a cached sieve

# Wheel bit array: bit i of byte k stands for the number 30*k + _WHEEL[i].
_WHEEL = (1, 7, 11, 13, 17, 19, 23, 29)
_BIT = {w: i for i, w in enumerate(_WHEEL)}
_UPTO = bytes(sum(1 << i for i, w in enumerate(_WHEEL) if w <= r) for r in range(30))
_POP = bytes(bin(b).count("1") for b in range(256))
_CLEAR = tuple(bytes(b & ~(1 << i) for b in range(256)) for i in range(8))

# phi(v, 6) is periodic mod 30030 = 2*3*5*7*11*13 and phi(v, _C) adds 17..29
# by inclusion-exclusion, so special leaves start at primes[_C] = 31.
_C = 10
# One period, in wheel bytes, of the integers free of the primes 2..19.
_PERIOD = 7 * 11 * 13 * 17 * 19


def count_primes(n: int) -> int:
    """Return the number of primes <= n, exactly.

    n is an int or any object with __index__ (bool, numpy integers); n < 2
    gives 0.  Raises TypeError for anything else, such as float, str or
    None.  Built so that every n <= 10**12 returns in under a second (the
    suite's timing tests are the check); larger n is still exact, only
    slower.

    >>> count_primes(1000)
    168
    """
    n = _index(n)
    if n < _SMALL:
        return _small_sieve().count(1, 0, (n + 1)//2) + 1 if n > 1 else 0
    return _lmo(n)


@_cache
def _small_sieve():
    return bytes(_odd_sieve(_SMALL))


@_cache
def _phi6_table():
    """t[r] = number of 1 <= i <= r coprime to 30030."""
    t = bytearray(b"\x01")*30030
    for p in (2, 3, 5, 7, 11, 13):
        t[::p] = bytes(30030//p)
    return list(_accumulate(t))


def _odd_sieve(n):
    """Bytearray s with s[i] = 1 iff 2*i + 1 is prime, for 2*i + 1 <= n."""
    size = (n + 1)//2
    s = bytearray(b"\x01")*size
    s[0] = 0
    for i in range(1, (_isqrt(n) + 1)//2):
        if s[i]:
            p = 2*i + 1
            start = 2*i*(i + 1)  # index of p*p
            s[start::p] = bytes((size - 1 - start)//p + 1)
    return s


def _icbrt(n):
    """Largest r with r**3 <= n, for n >= 1 (integer Newton iteration)."""
    r = 1 << -(-n.bit_length()//3)
    while True:
        s = (2*r + n//(r*r))//3
        if s >= r:
            return r
        r = s


def _cross_off(buf, p):
    """Clear every multiple of p (p coprime to 30) in a wheel bit array."""
    for w in _WHEEL:
        i, r = divmod(p*w, 30)
        buf[i::p] = buf[i::p].translate(_CLEAR[_BIT[r]])


def _counts(mv, vs):
    """For each v of the ascending vs, the number of set bits of mv in [1, v]."""
    from_bytes, pop, upto = int.from_bytes, _POP, _UPTO
    out = []
    pos = acc = 0
    for v in vs:
        k = v//30
        if k > pos:
            acc += from_bytes(mv[pos:k], "big").bit_count()
            pos = k
        out.append(acc + pop[mv[k] & upto[v % 30]])
    return out


def _lmo(x):
    sq = _isqrt(x)
    y = _icbrt(x)
    s = _odd_sieve(sq)
    primes = [2]
    primes += _compress(range(1, sq + 1, 2), s)
    # pit[v] = pi(v) for 2 <= v <= sq, from t[i] = pi(2*i - 1) for i >= 2.
    t = list(_accumulate(s, initial=1))
    pit = [0]*(2*len(t) - 1)
    pit[::2] = t
    pit[1::2] = t[1:]
    a = pit[y]
    vmax = x//(y + 1)      # largest argument any leaf or P2 term reads from the sieve
    k = pit[_isqrt(vmax)]  # the sieve is final once primes[:k] are crossed off

    # Ordinary leaves: squarefree m <= y built from primes[_C:a], each adding
    # mu(m) * phi(x//m, _C).  Nodes are (m, mu(m), index of the least prime
    # factor of m, or a for m = 1).
    t6 = _phi6_table()
    ds = [(1, 1)]
    for p in primes[6:_C]:
        ds += [(d*p, -mu) for d, mu in ds]
    phi = 0
    nodes = []
    stack = [(1, 1, a)]
    while stack:
        m, mu, i = node = stack.pop()
        nodes.append(node)
        xm = x//m
        for d, sign in ds:
            v = xm//d
            phi += mu*sign*(v//30030*5760 + t6[v % 30030])
        for j in range(_C, i):
            mp = m*primes[j]
            if mp > y:
                break
            stack.append((mp, -mu, j))
    nodes.sort(reverse=True)

    # Wheel bit array of [1, vmax] at stage _C: the integers free of primes[:_C].
    size = vmax//30 + 1
    pattern = bytearray(b"\xff")*_PERIOD
    for p in (7, 11, 13, 17, 19):
        _cross_off(pattern, p)
    sieve = pattern*(size//_PERIOD + 1)
    del sieve[size:]
    for p in primes[8:_C]:
        _cross_off(sieve, p)
    mv = memoryview(sieve)

    # Special leaves -mu(m) * phi(x//(m*p), j) for p = primes[j], m <= y < m*p
    # and every prime factor of m above p.  At stage j the sieve holds the
    # integers free of primes[:j].
    for j in range(_C, a - 1):
        p = primes[j]
        if p*p <= y:
            # m may be composite; here every leaf has v >= p*p, so all are hard.
            ylim = y//p
            mus = []
            vs = []
            for m, mu, i in nodes:
                if m <= ylim:
                    break
                if i > j:
                    mus.append(mu)
                    vs.append(x//(m*p))
            phi -= sum(map(_mul, mus, _counts(mv, vs)))
        else:
            # m = q prime in (p, y]: hard while v = x//(p*q) >= p*p, easy
            # (phi = pi(v) - j + 1) while v >= p, and phi = 1 after that.
            xp = x//p
            e = _bisect_right(primes, xp//(p*p), j + 1, a)
            f = _bisect_right(primes, xp//p, e, a)
            if e > j + 1:
                phi += sum(_counts(mv, map(xp.__floordiv__, primes[e - 1:j:-1])))
            phi += sum(map(pit.__getitem__, map(xp.__floordiv__, primes[e:f])))
            phi += (f - e)*(1 - j) + a - f
        if j < k:
            _cross_off(sieve, p)
    for j in range(max(_C, a - 1), k):
        _cross_off(sieve, primes[j])

    # P2 = sum over primes y < p <= sq of pi(x//p) - pi(p) + 1, with
    # pi(v) = survivors(v) - 1 + k on the final sieve and pi(primes[i]) = i + 1.
    cnt = len(primes) - a
    p2 = sum(_counts(mv, map(x.__floordiv__, reversed(primes[a:]))))
    p2 += cnt*(k - 1) - (len(primes) - 1 + a)*cnt//2
    return phi + a - 1 - p2


def _main():
    import argparse

    parser = argparse.ArgumentParser(description="Print the number of primes <= N.")
    parser.add_argument("N", type=int, help="an integer")
    print(count_primes(parser.parse_args().N))


if __name__ == "__main__":
    _main()
