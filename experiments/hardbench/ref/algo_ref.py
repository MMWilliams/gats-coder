"""Reference solutions for the algo task, cross-checked against brute force
on small inputs, then used to produce the hidden answers."""
import random
import time


def distinct_substrings(s):
    """Suffix automaton: sum over states of len(v) - len(link(v))."""
    link, length, nxt = [-1], [0], [{}]
    last = 0
    for ch in s:
        cur = len(length)
        length.append(length[last] + 1); link.append(0); nxt.append({})
        p = last
        while p != -1 and ch not in nxt[p]:
            nxt[p][ch] = cur
            p = link[p]
        if p == -1:
            link[cur] = 0
        else:
            q = nxt[p][ch]
            if length[p] + 1 == length[q]:
                link[cur] = q
            else:
                clone = len(length)
                length.append(length[p] + 1); link.append(link[q]); nxt.append(dict(nxt[q]))
                while p != -1 and nxt[p].get(ch) == q:
                    nxt[p][ch] = clone
                    p = link[p]
                link[q] = link[cur] = clone
        last = cur
    return sum(length[v] - length[link[v]] for v in range(1, len(length)))


def inversions(a):
    """Fenwick tree over coordinate-compressed values."""
    ranks = {v: i + 1 for i, v in enumerate(sorted(set(a)))}
    n = len(ranks)
    tree = [0] * (n + 1)
    total = 0
    for i, v in enumerate(a):
        r = ranks[v]
        j, seen = r, 0
        while j > 0:
            seen += tree[j]; j -= j & -j
        total += i - seen
        j = r
        while j <= n:
            tree[j] += 1; j += j & -j
    return total


def nqueens(n):
    full = (1 << n) - 1

    def go(cols, d1, d2):
        if cols == full:
            return 1
        free = full & ~(cols | d1 | d2)
        c = 0
        while free:
            bit = free & -free
            free ^= bit
            c += go(cols | bit, (d1 | bit) << 1 & full, (d2 | bit) >> 1)
        return c
    return go(0, 0, 0)


def brute_distinct(s):
    return len({s[i:j] for i in range(len(s)) for j in range(i + 1, len(s) + 1)})


def brute_inversions(a):
    return sum(1 for i in range(len(a)) for j in range(i + 1, len(a)) if a[i] > a[j])


def text(n, alphabet, seed):
    rng = random.Random(seed)
    return "".join(rng.choice(alphabet) for _ in range(n))


def array(n, seed, lo=0, hi=10 ** 9):
    rng = random.Random(seed)
    return [rng.randint(lo, hi) for _ in range(n)]


if __name__ == "__main__":
    rng = random.Random(1)
    for _ in range(200):
        s = text(rng.randint(0, 30), "ab" if rng.random() < 0.5 else "abcxyz", rng.random())
        assert distinct_substrings(s) == brute_distinct(s), s
        a = array(rng.randint(0, 40), rng.random(), 0, 10)
        assert inversions(a) == brute_inversions(a)
    assert [nqueens(n) for n in range(1, 9)] == [1, 0, 0, 2, 10, 4, 40, 92]
    print("cross-checks ok")
    for n, alpha, seed in ((200_000, "ab", 7), (200_000, "abcdefghijklmnopqrstuvwxyz", 8),
                           (300_000, "abc", 9), (250_000, "ab", 10)):
        t = time.time(); v = distinct_substrings(text(n, alpha, seed))
        print(f"distinct n={n} alpha={len(alpha)} seed={seed}: {v}  ({time.time()-t:.1f}s)")
    for n, seed in ((1_000_000, 7), (1_000_000, 8), (1_500_000, 9)):
        t = time.time(); v = inversions(array(n, seed))
        print(f"inversions n={n} seed={seed}: {v}  ({time.time()-t:.1f}s)")
    for n in (12, 13):
        t = time.time(); v = nqueens(n)
        print(f"nqueens {n}: {v}  ({time.time()-t:.1f}s)")
