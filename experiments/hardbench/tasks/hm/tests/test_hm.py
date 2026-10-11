import pytest

from hm import infer

CASES = [
    ('1', ('ok', 'int')),
    ('true', ('ok', 'bool')),
    ('()', ('ok', 'unit')),
    ('[]', ('ok', "'a list")),
    ('fun x -> x', ('ok', "'a -> 'a")),
    ('fun x -> 1', ('ok', "'a -> int")),
    ('fun x y -> x', ('ok', "'a -> 'b -> 'a")),
    ('fun f x -> f x', ('ok', "('a -> 'b) -> 'a -> 'b")),
    ('fun f g x -> f (g x)', ('ok', "('a -> 'b) -> ('c -> 'a) -> 'c -> 'b")),
    ('fun x -> (x, x)', ('ok', "'a -> 'a * 'a")),
    ('(1, true)', ('ok', 'int * bool')),
    ('1 :: 2 :: []', ('ok', 'int list')),
    ('(fun x -> x) :: []', ('ok', "('a -> 'a) list")),
    ('fun p -> fst p + 1', ('ok', "int * 'a -> int")),
    ('fun p -> (snd p, fst p)', ('ok', "'a * 'b -> 'b * 'a")),
    ('let id = fun x -> x in (id 1, id true)', ('ok', 'int * bool')),
    ('let x = [] in (x, 1 :: x)', ('ok', "'a list * int list")),
    ('fun f -> fun x -> f (f x)', ('ok', "('a -> 'a) -> 'a -> 'a")),
    ('fun f -> f (fun y -> y)', ('ok', "(('a -> 'a) -> 'b) -> 'b")),
    ('let f = fun x -> fun y -> x in f', ('ok', "'a -> 'b -> 'a")),
    ('if true then 1 else 2', ('ok', 'int')),
    ('fun b -> if b then [] else 1 :: []', ('ok', 'bool -> int list')),
    ('fun x -> x = x', ('ok', "'a -> bool")),
    ('fun x y -> x < y', ('ok', 'int -> int -> bool')),
    ('let rec len l = if isnil l then 0 else 1 + len (tail l) in len', ('ok', "'a list -> int")),
    ('let rec map f l = if isnil l then [] else f (head l) :: map f (tail l) in map', ('ok', "('a -> 'b) -> 'a list -> 'b list")),
    ('let rec fix f = f (fix f) in fix', ('ok', "('a -> 'a) -> 'a")),
    ('fun x -> x x', ('err', 'TypeError')),
    ('fun f -> (f 1, f true)', ('err', 'TypeError')),
    ('if 1 then 2 else 3', ('err', 'TypeError')),
    ('1 + true', ('err', 'TypeError')),
    ('y', ('err', 'NameError')),
    ('let f = fun x -> x in let g = fun y -> f (f y) in (g 1, g (g true))', ('ok', 'int * bool')),
    ('fun x -> let y = x in y', ('ok', "'a -> 'a")),
    ('let compose f g = fun x -> f (g x) in compose', ('ok', "('a -> 'b) -> ('c -> 'a) -> 'c -> 'b")),
    ('let k x y = x in k 1', ('ok', "'a -> int")),
    ('fun x -> not (x = 0)', ('ok', 'int -> bool')),
    ('head (1 :: [])', ('ok', 'int')),
    ('fun l -> head (tail l) :: []', ('ok', "'a list -> 'a list")),
    ('let p = (1, true) in if snd p then fst p else 0', ('ok', 'int')),
]


@pytest.mark.parametrize("src,expected", CASES, ids=[c[0][:40] for c in CASES])
def test_infer(src, expected):
    kind, value = expected
    if kind == "ok":
        assert infer(src) == value
    else:
        with pytest.raises(eval(value)):
            infer(src)
