"""Long-running, goal-directed, and design-only interfaces over Ledger episodes.

The episode remains the unit of mutation and audit. These modes never turn a
model's assertion of success into an acceptance decision.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PLAN_SECTIONS = (
    "Problem and scope", "Architecture", "Domain contracts", "Evidence and verification",
    "Implementation milestones", "Evaluation and acceptance", "Risks and rollback",
)


def design_prompt(task: str) -> str:
    from .web import web_context
    sources = web_context(task, max_pages=3, max_chars=1800)
    return ("Write a comprehensive, implementable design document in Markdown "
            "for the following task. Distinguish proposed capabilities from "
            "demonstrated results. Specify acceptance criteria and verification "
            "steps that do not depend solely on the generated artifact; the "
            "candidate must not rewrite its own final judging rules. Ledger "
            "runs coding episodes in Git worktrees and journals them; its plan "
            "mode writes a design document, and its loop mode repeats bounded "
            "coding episodes. Do not invent measured results. Include concrete "
            "interfaces, tests, budgets, staged "
            "milestones, and failure recovery. Use exactly these section headings "
            "and keep the document under 1800 words. Preserve any exact file "
            "names and commands stated in the task; do not replace them with "
            "hypothetical ones. "
            "(additional subsections are welcome):\n"
            + "\n".join(f"## {s}" for s in PLAN_SECTIONS)
            + "\n\nTASK\n" + task
            + ("\n\nLIVE WEB DOCUMENTATION\n" + sources if sources else ""))


def create_plan(task: str, backend, destination: Path) -> Path:
    """Produce a design artifact only; refuse to replace a prior plan."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"plan already exists: {destination}")
    prompt = design_prompt(task)
    for attempt in range(2):
        response = backend.complete(prompt).strip()
        missing = [s for s in PLAN_SECTIONS
                   if not re.search(r"^##\s+" + re.escape(s) + r"\s*$", response,
                                    re.IGNORECASE | re.MULTILINE)]
        if not missing:
            break
        prompt += ("\n\nYour previous draft omitted exact section headings: "
                   + ", ".join(missing) + ". Return the complete plan.")
    if missing:
        raise ValueError("model omitted plan sections: " + ", ".join(missing))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as output:
        output.write("# Research plan (model-generated draft)\n\n"
                     "Review factual claims and acceptance rules before use.\n\n"
                     f"Task: {task}\n\n" + response + "\n")
    return destination


@dataclass(frozen=True)
class Attempt:
    success: bool
    worktree: Path
    journal: Path
    reason: str = ""


# A dead local model server (crashed process, exhausted GPU memory) fails
# every iteration the same way. Retrying it at the configured pace just
# hammers a port nothing is listening on; back off instead, capped so a
# recovered backend is never more than this far from the next attempt.
BACKEND_DOWN_BASE_SECONDS = 30
BACKEND_DOWN_MAX_SECONDS = 300


def run_until(task: str, episode: Callable[[str, int], Attempt], *,
              mode: str, accept: Callable[[Attempt], bool] | None = None,
              max_iterations: int | None = None,
              pause_seconds: float = 0,
              backend_down: Callable[[Attempt], bool] | None = None
              ) -> list[Attempt]:
    """Run resumable episodes; goal mode requires an independent acceptance check.

    `episode` owns isolation and budgets. Failure is evidence for the next
    attempt, not permission to modify the acceptance checker. Loop mode
    continues after success to refine; Ctrl-C is the normal stop mechanism.

    `backend_down`, if given, inspects an attempt and reports whether it
    failed purely because the model backend was unreachable (as opposed to
    the model responding with a bad patch). Consecutive backend-down
    attempts back off exponentially instead of pausing at the fixed rate.
    """
    if mode not in {"loop", "goal"}:
        raise ValueError("mode must be loop or goal")
    if mode == "goal" and accept is None:
        raise ValueError("goal mode requires an independent acceptance check")
    if max_iterations is not None and max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    if pause_seconds < 0:
        raise ValueError("pause_seconds cannot be negative")
    attempts: list[Attempt] = []
    consecutive_backend_down = 0
    while max_iterations is None or len(attempts) < max_iterations:
        number = len(attempts) + 1
        instruction = task if number == 1 else (
            f"{task}\n\nIteration {number}: inspect the previous episode journal "
            f"at {attempts[-1].journal}. Diagnose its failure or find a "
            "specific measurable refinement. Keep the external acceptance "
            "contract fixed; make one bounded improvement and verify it.")
        result = episode(instruction, number)
        attempts.append(result)
        if mode == "goal" and result.success and accept(result):
            break
        if backend_down is not None and backend_down(result):
            consecutive_backend_down += 1
        else:
            consecutive_backend_down = 0
        delay = pause_seconds
        if consecutive_backend_down:
            delay = min(BACKEND_DOWN_MAX_SECONDS, BACKEND_DOWN_BASE_SECONDS
                       * 2 ** (consecutive_backend_down - 1))
        if delay:
            time.sleep(delay)
    return attempts


def command_acceptor(command: str, trusted_root: Path,
                     timeout: int = 900) -> Callable[[Attempt], bool]:
    """Run an operator-supplied verifier from the original repository.

    The candidate path is supplied as an argument. The command and its files
    must be controlled independently of the candidate worktree.
    """
    argv = shlex.split(command, posix=os.name != "nt")
    if not argv:
        raise ValueError("acceptance command is empty")

    def accept(attempt: Attempt) -> bool:
        try:
            completed = subprocess.run(
                [*argv, str(attempt.worktree)], cwd=trusted_root,
                capture_output=True, text=True, timeout=timeout)
            code, stdout, stderr = (completed.returncode, completed.stdout,
                                    completed.stderr)
        except subprocess.TimeoutExpired:
            code, stdout, stderr = -1, "", f"acceptance timed out after {timeout}s"
        record = {"event": "EXTERNAL_ACCEPTANCE", "command": argv,
                  "candidate": str(attempt.worktree),
                  "exit_code": code,
                  "stdout_tail": stdout[-4000:],
                  "stderr_tail": stderr[-4000:]}
        # Ledger's episode journal is hash-chained. Keep this independently
        # controlled decision in a sidecar rather than corrupting that chain.
        with attempt.journal.with_name("acceptance.jsonl").open(
                "a", encoding="utf-8") as output:
            output.write(json.dumps(record, sort_keys=True) + "\n")
        return code == 0

    return accept
