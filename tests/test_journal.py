"""Hash-chain mechanics: genesis, linking, reserved fields, append-continue."""

import pytest

from ledger.journal import GENESIS, Journal


def test_chain_links_and_verifies(tmp_path):
    jpath = str(tmp_path / "j.jsonl")
    with Journal(jpath) as j:
        j.append("HEADER", version="test")
        j.append("PLAN", chosen="a")
        j.append("END", success=True)
    entries = Journal.read(jpath)
    assert entries[0]["prev"] == GENESIS
    assert entries[1]["prev"] == entries[0]["h"]
    assert entries[2]["prev"] == entries[1]["h"]
    ok, err = Journal.verify_chain(jpath)
    assert ok, err


def test_reserved_field_rejected(tmp_path):
    with Journal(str(tmp_path / "j.jsonl")) as j:
        with pytest.raises(ValueError):
            j.append("PLAN", h="forged")


def test_append_continues_chain(tmp_path):
    jpath = str(tmp_path / "j.jsonl")
    with Journal(jpath) as j:
        j.append("HEADER", version="test")
    with Journal(jpath) as j:  # reopen: must continue, not restart
        j.append("END", success=False)
    entries = Journal.read(jpath)
    assert entries[1]["i"] == 1
    assert entries[1]["prev"] == entries[0]["h"]
    ok, _ = Journal.verify_chain(jpath)
    assert ok
