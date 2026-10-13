"""Bug report, not a fix (hardbench FINDINGS #6, improve run 2).

d9af53b silenced pyflakes' "undefined name 'Attempt'" in cli.py with
`Attempt = AgentSpec`. The annotation on `_iteration_backend_down` means
`modes.Attempt`, the record `run_until` passes in; AgentSpec is a different
class."""
from ledger import cli, modes


def test_cli_attempt_is_the_modes_attempt_or_not_bound_at_runtime():
    assert getattr(cli, "Attempt", modes.Attempt) is modes.Attempt
