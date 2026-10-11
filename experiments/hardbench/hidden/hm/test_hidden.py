import pytest

from hm import infer

CASES = [
    ('fun x -> let f = fun y -> x in f', ('ok', "'a -> 'b -> 'a")),
    ('fun x -> let f = fun y -> y in (f x, f 1)', ('ok', "'a -> 'a * int")),
    ('let f = fun x -> let g = fun y -> (x, y) in g in f', ('ok', "'a -> 'b -> 'a * 'b")),
    ('fun f -> let g = f in (g 1, g 2)', ('ok', "(int -> 'a) -> 'a * 'a")),
    ('fun f -> let g = f in (g 1, g true)', ('err', 'TypeError')),
    ('let rec f x = f x in f', ('ok', "'a -> 'b")),
    ('let rec f x = if x < 1 then 0 else f (x - 1) in f 10', ('ok', 'int')),
    ('let rec even n = if n = 0 then true else odd (n - 1) in even', ('err', 'NameError')),
    ('let rec loop x = loop (x :: []) in loop', ('err', 'TypeError')),
    ('fun x -> (x :: []) :: []', ('ok', "'a -> 'a list list")),
    ('fun x -> x :: x', ('err', 'TypeError')),
    ('fun x y -> (x :: y) = y', ('ok', "'a -> 'a list -> bool")),
    ('fun f -> f f', ('err', 'TypeError')),
    ('(fun x -> x) (fun x -> x)', ('ok', "'a -> 'a")),
    ('fun x -> (fun y -> y) x', ('ok', "'a -> 'a")),
    ('let apply f x = f x in apply', ('ok', "('a -> 'b) -> 'a -> 'b")),
    ('let twice f x = f (f x) in twice (fun n -> n + 1)', ('ok', 'int -> int')),
    ('let twice f x = f (f x) in twice twice', ('ok', "('a -> 'a) -> 'a -> 'a")),
    ('let twice f x = f (f x) in twice (fun l -> 1 :: l) []', ('ok', 'int list')),
    ('fun a b c -> if a then b else c', ('ok', "bool -> 'a -> 'a -> 'a")),
    ('fun a b c -> if a then b else c + 1', ('ok', 'bool -> int -> int -> int')),
    ('fun p -> (fst p + 1, not (snd p))', ('ok', 'int * bool -> int * bool')),
    ('fun x -> (x, fun y -> (x, y))', ('ok', "'a -> 'a * ('b -> 'a * 'b)")),
    ('let s x y z = x z (y z) in s', ('ok', "('a -> 'b -> 'c) -> ('a -> 'b) -> 'a -> 'c")),
    ('let s x y z = x z (y z) in let k x y = x in s k k', ('ok', "'a -> 'a")),
    ('let rec foldl f acc l = if isnil l then acc else foldl f (f acc (head l)) (tail l) in foldl', ('ok', "('a -> 'b -> 'a) -> 'a -> 'b list -> 'a")),
    ('let rec foldl f acc l = if isnil l then acc else foldl f (f acc (head l)) (tail l) in foldl (fun a b -> a + b) 0', ('ok', 'int list -> int')),
    ('let rec zip a b = if isnil a then [] else (head a, head b) :: zip (tail a) (tail b) in zip', ('ok', "'a list -> 'b list -> ('a * 'b) list")),
    ('fun x -> if x then x else not x', ('ok', 'bool -> bool')),
    ('fun f g -> fun x -> (f x, g x)', ('ok', "('a -> 'b) -> ('a -> 'c) -> 'a -> 'b * 'c")),
    ('let x = 1 in let x = true in x', ('ok', 'bool')),
    ('let f = fun x -> x in f f', ('ok', "'a -> 'a")),
    ('fun x -> x + 1 = 2', ('ok', 'int -> bool')),
    ('fun l -> if isnil l then head l else head (tail l)', ('ok', "'a list -> 'a")),
    ('let unit = () in (unit, ())', ('ok', 'unit * unit')),
    ('fun x -> x () ', ('ok', "(unit -> 'a) -> 'a")),
]


@pytest.mark.parametrize("src,expected", CASES, ids=[c[0][:40] for c in CASES])
def test_infer_hidden(src, expected):
    kind, value = expected
    if kind == "ok":
        assert infer(src) == value
    else:
        with pytest.raises(eval(value)):
            infer(src)
