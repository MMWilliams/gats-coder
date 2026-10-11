"""Reference Hindley-Milner inference for the mini-ML of the hm task.

Generates the expected types in the task's tests. Grammar and printing rules
are the ones TASK.md states; keep the two in step.
"""
import re

TOKEN = re.compile(r"\s*(?:(\d+)|(->|::|\(\)|\[\]|[()=,+\-<*])|([A-Za-z_][A-Za-z0-9_']*))")
KEYWORDS = {"let", "in", "fun", "if", "then", "else", "true", "false", "rec"}


def tokenize(s):
    pos, out = 0, []
    while pos < len(s):
        m = TOKEN.match(s, pos)
        if not m or m.end() == pos:
            if s[pos:].strip() == "":
                break
            raise SyntaxError(f"bad input at {pos}: {s[pos:pos+10]!r}")
        pos = m.end()
        if m.group(1):
            out.append(("int", int(m.group(1))))
        elif m.group(2):
            out.append(("op", m.group(2)))
        else:
            w = m.group(3)
            out.append(("kw", w) if w in KEYWORDS else ("id", w))
    return out


class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, kind=None, val=None):
        if self.i >= len(self.t):
            return None
        k, v = self.t[self.i]
        if kind and k != kind:
            return None
        if val is not None and v != val:
            return None
        return v

    def take(self, kind=None, val=None):
        v = self.peek(kind, val)
        if v is None:
            raise SyntaxError(f"expected {kind} {val} at token {self.i}: {self.t[self.i:self.i+3]}")
        self.i += 1
        return v

    def expr(self):
        if self.peek("kw", "let"):
            self.take()
            rec = bool(self.peek("kw", "rec")) and self.take() is not None
            name = self.take("id")
            params = []
            while self.peek("id") is not None:
                params.append(self.take("id"))
            self.take("op", "=")
            e = self.expr()
            for p in reversed(params):
                e = ("fun", p, e)
            self.take("kw", "in")
            body = self.expr()
            return ("letrec" if rec else "let", name, e, body)
        if self.peek("kw", "fun"):
            self.take()
            params = [self.take("id")]
            while self.peek("id") is not None:
                params.append(self.take("id"))
            self.take("op", "->")
            e = self.expr()
            for p in reversed(params):
                e = ("fun", p, e)
            return e
        if self.peek("kw", "if"):
            self.take()
            c = self.expr()
            self.take("kw", "then")
            a = self.expr()
            self.take("kw", "else")
            b = self.expr()
            return ("if", c, a, b)
        return self.cons()

    def cons(self):
        left = self.cmp()
        if self.peek("op", "::"):
            self.take()
            return ("app", ("app", ("var", "::"), left), self.cons())
        return left

    def cmp(self):
        left = self.add()
        if self.peek("op", "=") or self.peek("op", "<"):
            op = self.take()
            right = self.add()
            return ("app", ("app", ("var", op), left), right)
        return left

    def add(self):
        left = self.app()
        while self.peek("op", "+") or self.peek("op", "-"):
            op = self.take()
            left = ("app", ("app", ("var", op), left), self.app())
        return left

    def app(self):
        e = self.atom()
        while True:
            a = self.atom(optional=True)
            if a is None:
                return e
            e = ("app", e, a)

    def atom(self, optional=False):
        if self.peek("int") is not None:
            return ("int", self.take())
        if self.peek("kw", "true") or self.peek("kw", "false"):
            return ("bool", self.take() == "true")
        if self.peek("id") is not None:
            return ("var", self.take())
        if self.peek("op", "[]"):
            self.take()
            return ("nil",)
        if self.peek("op", "()"):
            self.take()
            return ("unit",)
        if self.peek("op", "("):
            self.take()
            e = self.expr()
            if self.peek("op", ","):
                self.take()
                e = ("pair", e, self.expr())
            self.take("op", ")")
            return e
        if optional:
            return None
        raise SyntaxError(f"unexpected token {self.t[self.i:self.i+1]}")


def parse(s):
    p = Parser(tokenize(s))
    e = p.expr()
    if p.i != len(p.t):
        raise SyntaxError(f"trailing tokens: {p.t[p.i:]}")
    return e


# ---- types ----
class TVar:
    _n = 0

    def __init__(self):
        TVar._n += 1
        self.id = TVar._n
        self.ref = None


class TCon:
    def __init__(self, name, args=()):
        self.name, self.args = name, tuple(args)


INT, BOOL, UNIT = TCon("int"), TCon("bool"), TCon("unit")
arrow = lambda a, b: TCon("->", (a, b))
pair = lambda a, b: TCon("*", (a, b))
tlist = lambda a: TCon("list", (a,))


def prune(t):
    while isinstance(t, TVar) and t.ref is not None:
        t = t.ref
    return t


def occurs(v, t):
    t = prune(t)
    if t is v:
        return True
    return isinstance(t, TCon) and any(occurs(v, a) for a in t.args)


def unify(a, b):
    a, b = prune(a), prune(b)
    if isinstance(a, TVar):
        if a is b:
            return
        if occurs(a, b):
            raise TypeError("occurs check: cannot construct the infinite type")
        a.ref = b
        return
    if isinstance(b, TVar):
        return unify(b, a)
    if a.name != b.name or len(a.args) != len(b.args):
        raise TypeError(f"cannot unify {show(a)} with {show(b)}")
    for x, y in zip(a.args, b.args):
        unify(x, y)


class Scheme:
    def __init__(self, vars_, t):
        self.vars, self.t = vars_, t


def free(t, acc):
    t = prune(t)
    if isinstance(t, TVar):
        acc.add(t)
    else:
        for a in t.args:
            free(a, acc)
    return acc


def generalize(env, t):
    env_free = set()
    for s in env.values():
        for v in free(s.t, set()):
            if v not in s.vars:
                env_free.add(v)
    return Scheme([v for v in free(t, set()) if v not in env_free], t)


def instantiate(s):
    m = {v: TVar() for v in s.vars}

    def go(t):
        t = prune(t)
        if isinstance(t, TVar):
            return m.get(t, t)
        return TCon(t.name, [go(a) for a in t.args])
    return go(s.t)


def _poly(f):
    a, b = TVar(), TVar()
    t = f(a, b)
    return Scheme([a, b], t)


BUILTINS = {
    "+": Scheme([], arrow(INT, arrow(INT, INT))),
    "-": Scheme([], arrow(INT, arrow(INT, INT))),
    "<": Scheme([], arrow(INT, arrow(INT, BOOL))),
    "=": _poly(lambda a, b: arrow(a, arrow(a, BOOL))),
    "::": _poly(lambda a, b: arrow(a, arrow(tlist(a), tlist(a)))),
    "fst": _poly(lambda a, b: arrow(pair(a, b), a)),
    "snd": _poly(lambda a, b: arrow(pair(a, b), b)),
    "head": _poly(lambda a, b: arrow(tlist(a), a)),
    "tail": _poly(lambda a, b: arrow(tlist(a), tlist(a))),
    "isnil": _poly(lambda a, b: arrow(tlist(a), BOOL)),
    "not": Scheme([], arrow(BOOL, BOOL)),
}


def infer_expr(e, env):
    k = e[0]
    if k == "int":
        return INT
    if k == "bool":
        return BOOL
    if k == "unit":
        return UNIT
    if k == "nil":
        return tlist(TVar())
    if k == "var":
        if e[1] not in env:
            raise NameError(f"unbound variable {e[1]}")
        return instantiate(env[e[1]])
    if k == "fun":
        a = TVar()
        body = infer_expr(e[2], {**env, e[1]: Scheme([], a)})
        return arrow(a, body)
    if k == "app":
        f = infer_expr(e[1], env)
        x = infer_expr(e[2], env)
        r = TVar()
        unify(f, arrow(x, r))
        return r
    if k == "if":
        unify(infer_expr(e[1], env), BOOL)
        a = infer_expr(e[2], env)
        unify(a, infer_expr(e[3], env))
        return a
    if k == "pair":
        return pair(infer_expr(e[1], env), infer_expr(e[2], env))
    if k == "let":
        t = infer_expr(e[2], env)
        return infer_expr(e[3], {**env, e[1]: generalize(env, t)})
    if k == "letrec":
        a = TVar()
        env2 = {**env, e[1]: Scheme([], a)}
        unify(a, infer_expr(e[2], env2))
        return infer_expr(e[3], {**env, e[1]: generalize(env, a)})
    raise ValueError(k)


def show(t, names=None):
    names = {} if names is None else names

    def name(v):
        if v not in names:
            n = len(names)
            names[v] = "'" + chr(ord("a") + n % 26) + (str(n // 26) if n >= 26 else "")
        return names[v]

    def go(t, prec):
        t = prune(t)
        if isinstance(t, TVar):
            return name(t)
        if t.name == "->":
            s = f"{go(t.args[0], 1)} -> {go(t.args[1], 0)}"
            return f"({s})" if prec > 0 else s
        if t.name == "*":
            s = f"{go(t.args[0], 2)} * {go(t.args[1], 2)}"
            return f"({s})" if prec > 1 else s
        if t.name == "list":
            return f"{go(t.args[0], 2)} list"
        return t.name
    return go(t, 0)


def infer(src):
    return show(infer_expr(parse(src), dict(BUILTINS)))


if __name__ == "__main__":
    for s in ["fun x -> x", "let id = fun x -> x in (id 1, id true)", "fun f g x -> f (g x)",
              "let rec len l = if isnil l then 0 else 1 + len (tail l) in len",
              "fun x -> x x", "fun f -> (f 1, f true)", "1 :: 2 :: []", "fun p -> fst p + 1",
              "let x = [] in (x, 1 :: x)", "(fun x -> x) :: []", "fun f -> fun x -> f (f x)",
              "fun x -> (x, x)", "fun f -> f (fun y -> y)", "let f = fun x -> fun y -> x in f",
              "fun a b -> (a :: b) list", "let rec fix f = f (fix f) in fix"]:
        try:
            print(f"{s!r:60} : {infer(s)}")
        except (TypeError, NameError, SyntaxError) as exc:
            print(f"{s!r:60} ! {type(exc).__name__}: {exc}")
