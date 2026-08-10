"""Synthetic planning tasks from paper Appendix A (easy/medium/hard) and the
ground-truth environment used to execute them.

The M1 exit criterion runs this suite: 100 tasks (20 easy / 55 medium /
25 hard), expecting 100% success, optimality 1.0, zero LLM planning calls,
and zero variance across runs (paper Table 1).
"""

from __future__ import annotations

from .actions import Action, Catalog
from .planner import Task
from .state import Props

OPTIMAL_LENGTH = {"easy": 3, "medium": 5, "hard": 7}


def _a(name, prec=(), add=(), delete=()):
    return Action(name=name, prec=frozenset(prec), add=frozenset(add),
                  delete=frozenset(delete))


def easy_task(task_id: str, p: str = "") -> Task:
    """3 steps, 1 dead-end branch (Appendix A)."""
    actions = [
        _a(f"{p}StartA", (), (f"{p}a1",)),
        _a(f"{p}ProcessA", (f"{p}a1",), (f"{p}a2",)),
        _a(f"{p}FinishA", (f"{p}a2",), (f"{p}goal",)),
        _a(f"{p}StartB", (), (f"{p}b1",)),          # dead-end branch
        _a(f"{p}ProcessB", (f"{p}b1",), (f"{p}dead",)),
    ]
    return Task(task_id, frozenset(), frozenset({f"{p}goal"}), Catalog(actions))


def medium_task(task_id: str, p: str = "") -> Task:
    """5 steps, resource constraint, waste trap, slow alternative (Appendix A)."""
    actions = [
        _a(f"{p}GetResource", (f"{p}start",), (f"{p}resource", f"{p}r1")),
        _a(f"{p}UseResource", (f"{p}r1", f"{p}resource"), (f"{p}r2",),
           (f"{p}resource",)),
        _a(f"{p}GetResource2", (f"{p}r2",), (f"{p}resource2", f"{p}r3")),
        _a(f"{p}Process", (f"{p}r3", f"{p}resource2"), (f"{p}r4",)),
        _a(f"{p}Finish", (f"{p}r4",), (f"{p}goal",)),
        _a(f"{p}WasteResource", (f"{p}resource",), (f"{p}wasted",),
           (f"{p}resource",)),
        # Suboptimal alternative: 5 slow steps to r4 vs 4 on the main path.
        _a(f"{p}SlowStart", (f"{p}start",), (f"{p}slow1",)),
        _a(f"{p}SlowProcess1", (f"{p}slow1",), (f"{p}slow2",)),
        _a(f"{p}SlowProcess2", (f"{p}slow2",), (f"{p}slow3",)),
        _a(f"{p}SlowProcess3", (f"{p}slow3",), (f"{p}slow4",)),
        _a(f"{p}SlowProcess4", (f"{p}slow4",), (f"{p}r4",)),
    ]
    return Task(task_id, frozenset({f"{p}start"}), frozenset({f"{p}goal"}),
                Catalog(actions))


def hard_task(task_id: str, p: str = "") -> Task:
    """7 steps, energy management, waste trap, fake-progress chain (Appendix A)."""
    actions = [
        _a(f"{p}Init", (f"{p}start",), (f"{p}init", f"{p}energy")),
        _a(f"{p}GatherA", (f"{p}init",), (f"{p}mat_a", f"{p}s1")),
        _a(f"{p}GatherB", (f"{p}s1",), (f"{p}mat_b", f"{p}s2")),
        _a(f"{p}Combine", (f"{p}mat_a", f"{p}mat_b", f"{p}s2"),
           (f"{p}combined", f"{p}s3")),
        _a(f"{p}Refine", (f"{p}combined", f"{p}energy"),
           (f"{p}refined", f"{p}s4"), (f"{p}energy",)),
        _a(f"{p}Recharge", (f"{p}s4",), (f"{p}energy", f"{p}s5")),
        _a(f"{p}Finalize", (f"{p}refined", f"{p}energy", f"{p}s5"),
           (f"{p}goal",)),
        _a(f"{p}WasteEnergy", (f"{p}energy",), (f"{p}tired",), (f"{p}energy",)),
        _a(f"{p}FakeProgress", (f"{p}init",), (f"{p}fake1",)),
        _a(f"{p}MoreFake", (f"{p}fake1",), (f"{p}fake2",)),
        _a(f"{p}DeadFake", (f"{p}fake2",), (f"{p}nowhere",)),
    ]
    return Task(task_id, frozenset({f"{p}start"}), frozenset({f"{p}goal"}),
                Catalog(actions))


def no_backtrack_task(task_id: str, rooms: int = 4, p: str = "") -> Task:
    """Maze with locking doors (paper SS D): wrong choice = permanently stuck."""
    actions = []
    for i in range(1, rooms):
        actions.append(_a(f"{p}Exit{i}", (f"{p}room{i}",),
                          (f"{p}room{i + 1}",), (f"{p}room{i}",)))
        actions.append(_a(f"{p}Trap{i}", (f"{p}room{i}",),
                          (f"{p}stuck{i}",), (f"{p}room{i}",)))
    return Task(task_id, frozenset({f"{p}room1"}),
                frozenset({f"{p}room{rooms}"}), Catalog(actions))


def suite(n_easy: int = 20, n_medium: int = 55, n_hard: int = 25) -> list[Task]:
    """The paper's 100-task distribution, prop-prefixed so tasks are distinct."""
    tasks = []
    for i in range(n_easy):
        tasks.append(easy_task(f"easy-{i:02d}", p=f"e{i:02d}."))
    for i in range(n_medium):
        tasks.append(medium_task(f"medium-{i:02d}", p=f"m{i:02d}."))
    for i in range(n_hard):
        tasks.append(hard_task(f"hard-{i:02d}", p=f"h{i:02d}."))
    return tasks


class SyntheticEnv:
    """Ground-truth executor: applies catalog semantics exactly.

    In M1 the environment matches L1, so episodes never diverge; the
    divergence arm is exercised by tests that perturb the env deliberately.
    """

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def step(self, s: Props, action_name: str) -> Props:
        a = self.catalog.get(action_name)
        if a is None or not a.applicable(s):
            return s
        return a.apply(s)
