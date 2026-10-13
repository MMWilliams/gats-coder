"""Tests for ledger.state – canonical serialization and state-key functions."""

from __future__ import annotations

import hashlib

import pytest

from ledger.state import Props, canonical, short_key, state_key


# ---------------------------------------------------------------------------
# canonical
# ---------------------------------------------------------------------------


class TestCanonical:
    def test_empty(self):
        assert canonical(frozenset()) == ""

    def test_single(self):
        assert canonical(frozenset({"hello"})) == "hello"

    def test_sorted_order(self):
        assert canonical(frozenset({"b", "a", "c"})) == "a\nb\nc"

    def test_insertion_order_independent(self):
        a = canonical(frozenset({"x", "y", "z"}))
        b = canonical(frozenset({"z", "y", "x"}))
        assert a == b

    def test_empty_string_element(self):
        result = canonical(frozenset({"", "a"}))
        assert result == "\na"

    def test_multiline_elements(self):
        result = canonical(frozenset({"line1\nline2", "abc"}))
        assert result == "abc\nline1\nline2"


# ---------------------------------------------------------------------------
# state_key
# ---------------------------------------------------------------------------


class TestStateKey:
    def test_empty_frozenset(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert state_key(frozenset()) == expected

    def test_single_element(self):
        expected = hashlib.sha256(b"a").hexdigest()
        assert state_key(frozenset({"a"})) == expected

    def test_deterministic(self):
        props = frozenset({"p", "q", "r"})
        assert state_key(props) == state_key(props)

    def test_order_independent(self):
        a = state_key(frozenset({"alpha", "beta", "gamma"}))
        b = state_key(frozenset({"gamma", "alpha", "beta"}))
        assert a == b

    def test_different_sets_differ(self):
        assert state_key(frozenset({"a"})) != state_key(frozenset({"b"}))

    def test_different_sizes_differ(self):
        assert state_key(frozenset({"a"})) != state_key(frozenset({"a", "b"}))

    def test_return_type_is_hex_string(self):
        key = state_key(frozenset({"x"}))
        assert isinstance(key, str)
        assert len(key) == 64
        int(key, 16)  # must be valid hex

    def test_matches_manual_sha256(self):
        props = frozenset({"foo", "bar"})
        expected = hashlib.sha256(canonical(props).encode("utf-8")).hexdigest()
        assert state_key(props) == expected


# ---------------------------------------------------------------------------
# short_key
# ---------------------------------------------------------------------------


class TestShortKey:
    def test_length(self):
        assert len(short_key(frozenset({"a"}))) == 12

    def test_prefix_of_state_key(self):
        props = frozenset({"a", "b", "c"})
        assert short_key(props) == state_key(props)[:12]

    def test_deterministic(self):
        props = frozenset({"x", "y"})
        assert short_key(props) == short_key(props)

    def test_empty_frozenset(self):
        expected = hashlib.sha256(b"").hexdigest()[:12]
        assert short_key(frozenset()) == expected


# ---------------------------------------------------------------------------
# Props type alias
# ---------------------------------------------------------------------------


class TestPropsAlias:
    def test_is_frozenset(self):
        assert Props is frozenset
