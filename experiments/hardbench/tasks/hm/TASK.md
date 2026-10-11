Implement `hm.py`: Hindley-Milner type inference (Algorithm W or
equivalent) for a small ML, in pure Python, so that `python -m pytest tests
-q` passes.

`infer(src: str) -> str` returns the principal type of the expression,
printed canonically. It raises `TypeError` on a unification failure or an
occurs-check failure, `NameError` on an unbound variable and `SyntaxError`
on input that does not parse.

Syntax, lowest precedence first:

    expr  := let [rec] x p1 ... pn = expr in expr
           | fun x1 ... xn -> expr
           | if expr then expr else expr
           | cons
    cons  := cmp (:: cons)?                   right-associative
    cmp   := add ((= | <) add)?
    add   := app ((+ | -) app)*               left-associative
    app   := atom atom*                       left-associative
    atom  := integer | true | false | identifier | [] | ()
           | ( expr ) | ( expr , expr )

Identifiers match `[A-Za-z_][A-Za-z0-9_']*`. `let f p1 ... pn = e` is sugar
for `let f = fun p1 ... pn -> e`. let-bound names are generalised
(let-polymorphism); fun-bound names are not. In `let rec f = e1 in e2`, `f`
is monomorphic inside `e1` and generalised in `e2`.

Builtins in the initial environment:

    +  -   : int -> int -> int          <     : int -> int -> bool
    =      : 'a -> 'a -> bool           ::    : 'a -> 'a list -> 'a list
    not    : bool -> bool               isnil : 'a list -> bool
    fst    : 'a * 'b -> 'a              snd   : 'a * 'b -> 'b
    head   : 'a list -> 'a              tail  : 'a list -> 'a list

Types: `int`, `bool`, `unit`, type variables, `t1 -> t2`, `t1 * t2` (pairs
are binary), `t list`.

Printing: type variables are named 'a, 'b, 'c, ... in order of first
appearance in the printed type, left to right. `->` is right-associative
and binds loosest; `*` binds tighter than `->`; postfix `list` binds
tightest. Parenthesise exactly when needed: an arrow on the left of an
arrow, an arrow or a product inside a product, an arrow or a product under
`list`. Examples: `('a -> 'b) -> 'a`, `'a * 'b -> 'c`, `('a -> 'b) * 'c`,
`('a * 'b) * 'c`, `('a * 'b) list`, `('a -> 'b) list`, `'a list list`,
`'a -> 'a * ('b -> 'a * 'b)`.
