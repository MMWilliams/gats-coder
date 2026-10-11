"""Write the hm task's visible and hidden tests from the reference checker."""
from pathlib import Path

from hm_ref import infer

H = Path(__file__).resolve().parents[1]

VISIBLE = [
    "1", "true", "()", "[]", "fun x -> x", "fun x -> 1", "fun x y -> x",
    "fun f x -> f x", "fun f g x -> f (g x)", "fun x -> (x, x)", "(1, true)",
    "1 :: 2 :: []", "(fun x -> x) :: []", "fun p -> fst p + 1", "fun p -> (snd p, fst p)",
    "let id = fun x -> x in (id 1, id true)",
    "let x = [] in (x, 1 :: x)",
    "fun f -> fun x -> f (f x)",
    "fun f -> f (fun y -> y)",
    "let f = fun x -> fun y -> x in f",
    "if true then 1 else 2",
    "fun b -> if b then [] else 1 :: []",
    "fun x -> x = x",
    "fun x y -> x < y",
    "let rec len l = if isnil l then 0 else 1 + len (tail l) in len",
    "let rec map f l = if isnil l then [] else f (head l) :: map f (tail l) in map",
    "let rec fix f = f (fix f) in fix",
    "fun x -> x x",
    "fun f -> (f 1, f true)",
    "if 1 then 2 else 3",
    "1 + true",
    "y",
    "let f = fun x -> x in let g = fun y -> f (f y) in (g 1, g (g true))",
    "fun x -> let y = x in y",
    "let compose f g = fun x -> f (g x) in compose",
    "let k x y = x in k 1",
    "fun x -> not (x = 0)",
    "head (1 :: [])",
    "fun l -> head (tail l) :: []",
    "let p = (1, true) in if snd p then fst p else 0",
]

HIDDEN = [
    "fun x -> let f = fun y -> x in f",                   # x is monomorphic inside
    "fun x -> let f = fun y -> y in (f x, f 1)",          # f is polymorphic, x is not
    "let f = fun x -> let g = fun y -> (x, y) in g in f", # nested generalisation
    "fun f -> let g = f in (g 1, g 2)",                   # lambda-bound stays mono
    "fun f -> let g = f in (g 1, g true)",                # ...so this fails
    "let rec f x = f x in f",
    "let rec f x = if x < 1 then 0 else f (x - 1) in f 10",
    "let rec even n = if n = 0 then true else odd (n - 1) in even",
    "let rec loop x = loop (x :: []) in loop",            # occurs via recursion
    "fun x -> (x :: []) :: []",
    "fun x -> x :: x",
    "fun x y -> (x :: y) = y",
    "fun f -> f f",
    "(fun x -> x) (fun x -> x)",
    "fun x -> (fun y -> y) x",
    "let apply f x = f x in apply",
    "let twice f x = f (f x) in twice (fun n -> n + 1)",
    "let twice f x = f (f x) in twice twice",
    "let twice f x = f (f x) in twice (fun l -> 1 :: l) []",
    "fun a b c -> if a then b else c",
    "fun a b c -> if a then b else c + 1",
    "fun p -> (fst p + 1, not (snd p))",
    "fun x -> (x, fun y -> (x, y))",
    "let s x y z = x z (y z) in s",
    "let s x y z = x z (y z) in let k x y = x in s k k",
    "let rec foldl f acc l = if isnil l then acc else foldl f (f acc (head l)) (tail l) in foldl",
    "let rec foldl f acc l = if isnil l then acc else foldl f (f acc (head l)) (tail l) in foldl (fun a b -> a + b) 0",
    "let rec zip a b = if isnil a then [] else (head a, head b) :: zip (tail a) (tail b) in zip",
    "fun x -> if x then x else not x",
    "fun f g -> fun x -> (f x, g x)",
    "let x = 1 in let x = true in x",
    "let f = fun x -> x in f f",
    "fun x -> x + 1 = 2",
    "fun l -> if isnil l then head l else head (tail l)",
    "let unit = () in (unit, ())",
    "fun x -> x () ",
]


def expected(src):
    try:
        return ("ok", infer(src))
    except (TypeError, NameError, SyntaxError) as exc:
        return ("err", type(exc).__name__)


def render(cases, name):
    rows = ",\n".join(f"    ({src!r}, {expected(src)!r})" for src in cases)
    return f'''import pytest

from hm import infer

CASES = [
{rows},
]


@pytest.mark.parametrize("src,expected", CASES, ids=[c[0][:40] for c in CASES])
def {name}(src, expected):
    kind, value = expected
    if kind == "ok":
        assert infer(src) == value
    else:
        with pytest.raises(eval(value)):
            infer(src)
'''


(H / "tasks" / "hm" / "tests").mkdir(parents=True, exist_ok=True)
(H / "hidden" / "hm").mkdir(parents=True, exist_ok=True)
(H / "tasks" / "hm" / "tests" / "test_hm.py").write_text(render(VISIBLE, "test_infer"), encoding="utf-8")
(H / "hidden" / "hm" / "test_hidden.py").write_text(render(HIDDEN, "test_infer_hidden"), encoding="utf-8")
for src in VISIBLE + HIDDEN:
    print(f"{src!r:95} {expected(src)}")
