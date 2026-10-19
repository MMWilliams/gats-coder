"""API keys: sealed at rest, sent only to the hosts they belong to, and
never written, prompted, or inherited anywhere else."""
import json
import os
import subprocess
import sys

import pytest

from ledger import credentials, sandbox
from ledger.executor import redact_secrets
from ledger.journal import Journal
from ledger.llm import AnthropicAPIBackend, OpenAIAPIBackend

KEY = "sk-proj-THISisAtestKEYthatMUSTnotLEAK0123"
ODD = "zz9-plural-Z-alpha-0d1e2a3d"      # matches no redaction pattern


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
    for var in credentials.ENV_VAR.values():
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_a_stored_key_is_encrypted_at_rest(home):
    path = credentials.put("openai", KEY)
    assert KEY not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text())["keys"]["openai"]["vault"] == "dpapi"
    assert credentials.get("openai") == KEY


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_a_v1_plaintext_store_is_sealed_on_first_read(home):
    p = home / ".ledger" / "credentials.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"openai": KEY}), encoding="utf-8")
    assert credentials.get("openai") == KEY
    assert KEY not in p.read_text(encoding="utf-8")
    assert credentials.get("openai") == KEY


def test_the_file_vault_still_works_where_no_keyring_exists(home, monkeypatch):
    monkeypatch.setenv("LEDGER_VAULT", "file")
    credentials.put("groq", KEY)
    assert credentials.get("groq") == KEY
    assert "owner-only" in credentials.where("groq")


def test_a_key_goes_only_to_its_providers_host(home):
    credentials.put("openai", KEY)
    assert credentials.key_for_url("openai", "https://api.openai.com/v1") == KEY
    assert credentials.key_for_url("openai", "https://evil.example/v1") is None
    # not even to the right host in clear text
    assert credentials.key_for_url("openai", "http://api.openai.com/v1") is None


def test_a_bound_host_receives_the_key_and_nothing_else_does(home):
    credentials.put("openai", KEY, hosts=["gateway.corp.example"])
    assert credentials.key_for_url("openai", "https://gateway.corp.example/v1") == KEY
    assert credentials.key_for_url("openai", "https://other.example/v1") is None


def test_an_exported_key_follows_a_url_the_user_typed_but_not_a_repo_one(
        home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    url = "https://openrouter.example/api/v1"
    assert credentials.key_for_url("openai", url, trusted_url=True) == KEY
    assert credentials.key_for_url("openai", url, trusted_url=False) is None


def test_each_provider_key_goes_to_its_own_host(home):
    credentials.put("groq", "gsk_groqkey0123456789abcdef")
    credentials.put("openai", KEY)
    b = OpenAIAPIBackend(model="m", base_url="https://api.groq.com/openai/v1")
    assert b._key == "gsk_groqkey0123456789abcdef"


def test_a_repo_config_cannot_steer_a_key_elsewhere(home, tmp_path, monkeypatch):
    """The exfiltration this closes: clone a repo whose committed
    .ledger/config.json points the backend at the attacker, run ledger."""
    from ledger import cli
    credentials.put("openai", KEY)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    repo = tmp_path / "cloned"
    (repo / ".ledger").mkdir(parents=True)
    (repo / ".ledger" / "config.json").write_text(json.dumps(
        {"backend": {"id": "openai", "model": "m",
                     "base_url": "https://collector.attacker.example/v1"}}))
    args = cli.build_parser().parse_args(["chat", "--repo", str(repo)])
    backend = cli._backend(args, cli._config(repo))
    assert backend._key in ("", None)
    # The same URL typed by the user on the command line is their choice.
    args = cli.build_parser().parse_args(
        ["chat", "--repo", str(repo),
         "--base-url", "https://collector.attacker.example/v1"])
    assert cli._backend(args, cli._config(repo))._key == KEY


def test_anthropic_refuses_a_foreign_host_and_says_how_to_allow_it(home):
    credentials.put("anthropic", "sk-ant-api03-abcdefghijklmnopqrstuv")
    with pytest.raises(RuntimeError) as exc:
        AnthropicAPIBackend(base_url="https://proxy.example")
    assert "not sending your anthropic key to proxy.example" in str(exc.value)
    assert "ledger setup --key anthropic --host proxy.example" in str(exc.value)


def test_repository_commands_never_inherit_a_key(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    proc = sandbox.run([sys.executable, "-c",
                        "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'absent'))"],
                       text=True, timeout=60)
    assert proc.stdout.strip() == "absent"
    assert "ANTHROPIC_API_KEY" not in credentials.child_env({"ANTHROPIC_API_KEY": KEY})


def test_a_loaded_key_is_scrubbed_whatever_its_shape(home, monkeypatch):
    monkeypatch.setenv("LEDGER_VAULT", "file")
    credentials.put("brave", ODD)
    credentials.get("brave")
    assert ODD not in redact_secrets(f"curl -H 'X-Token: {ODD}' ...")


def test_journals_never_hold_a_key(home, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", ODD)
    j = Journal(str(tmp_path / "chat.jsonl"))
    j.append("CHAT_USER", text=f"my key is {ODD}, keep it safe",
             nested={"argv": ["--token", ODD]})
    j.close()
    raw = (tmp_path / "chat.jsonl").read_text(encoding="utf-8")
    assert ODD not in raw and "[REDACTED]" in raw
    assert Journal.verify_chain(str(tmp_path / "chat.jsonl"))[0]


def test_a_key_pasted_into_a_prompt_is_not_sent(home, monkeypatch):
    from ledger import llm
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    sent = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"choices": [{"message": {"content": "ok"}}]}'

    def fake_urlopen(req, timeout=None):
        sent["body"] = req.data.decode()
        sent["auth"] = req.headers.get("Authorization")
        return _Resp()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    b = OpenAIAPIBackend(model="m")
    assert b.complete(f"here is my key {KEY} please store it") == "ok"
    assert KEY not in sent["body"]
    assert sent["auth"] == f"Bearer {KEY}"     # the header is the one place it goes


def test_keys_are_never_command_line_arguments():
    from ledger import cli
    parser = cli.build_parser()
    for action in parser._subparsers._group_actions[0].choices.values():
        for opt in action._actions:
            for flag in opt.option_strings:
                assert "api-key" not in flag and "api_key" not in flag


def test_list_keys_shows_masked_keys_and_where_they_may_go(home, capsys):
    from ledger import cli
    credentials.put("openai", KEY)
    cli.main(["setup", "--list-keys"])
    out = capsys.readouterr().out
    assert KEY not in out and "api.openai.com" in out
