"""Run a hardbench task through LEDGER, then grade it on the hidden suite.

    python run.py rk45 [--backend claude] [--max-steps 40] [--attempts 10]

The repo LEDGER sees holds TASK.md, the stub and the visible tests only. The
hidden suite lives here and is run against the episode worktree afterwards,
so pass@hidden is the score and the visible tests are what the sensors saw.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path.home() / "ledger-bench"
VISIBLE = "python -m pytest tests -q -p no:cacheprovider"


def sh(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def rmtree(path):
    """git's object files are read-only on Windows; rmtree needs a hand."""
    def onexc(fn, p, exc):
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    shutil.rmtree(path, onexc=onexc)


def fresh_repo(task, dest):
    if dest.exists():
        rmtree(dest)
    shutil.copytree(HERE / "tasks" / task, dest)
    (dest / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.ledger/\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "task"]):
        p = sh(["git", *args], dest)
        assert p.returncode == 0, p.stderr


def pytest_counts(worktree, target):
    env = {**os.environ, "PYTHONPATH": str(worktree)}
    p = sh([sys.executable, "-m", "pytest", str(target), "-q", "-p", "no:cacheprovider",
            "--rootdir", str(worktree)], worktree, env=env, timeout=1800)
    out = p.stdout
    n = lambda k: sum(int(x) for x in re.findall(rf"(\d+) {k}", out))
    last = out.strip().splitlines()[-1] if out.strip() else p.stderr[-300:]
    return {"passed": n("passed"), "failed": n("failed") + n("error"), "summary": last}


def journal_stats(path):
    actions, esc, end = [], None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("t") == "EXEC":
            actions.append(e["action"])
        elif e.get("t") == "ESCALATION":
            esc = e
        elif e.get("t") == "END":
            end = e
    return {"steps": len(actions),
            "author_attempts": sum(a.startswith("AuthorPatch") for a in actions),
            "escalation": esc.get("reason") if esc else None,
            "success": bool(end and end.get("success")), "plan": actions}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--backend", default="claude")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--attempts", type=int, default=10)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    run = WORK / f"{a.task}-{a.backend}{a.tag}"
    repo, out = run / "repo", run / "episode"
    fresh_repo(a.task, repo)
    if out.exists():
        rmtree(out)
    task = (HERE / "tasks" / a.task / "TASK.md").read_text(encoding="utf-8")
    cmd = ["ledger", "run", task, "--repo", str(repo), "--out", str(out), "--test", VISIBLE,
           "--kind", "library", "--profile", "autonomous", "--yes", "--no-launch",
           "--no-product-review", "--backend", a.backend, "--max-steps", str(a.max_steps),
           "--max-author-attempts", str(a.attempts), "--author-timeout", "0", "--json"]
    t0 = time.time()
    p = sh(cmd, repo, timeout=4 * 3600)
    res = {"task": a.task, "backend": a.backend, "wall_s": round(time.time() - t0),
           "ledger_exit": p.returncode}
    journal = out / "episode.jsonl"
    if journal.exists():
        res.update(journal_stats(journal))
    worktree = out / "worktree"
    if worktree.exists():
        res["visible"] = pytest_counts(worktree, worktree / "tests")
        res["hidden"] = pytest_counts(worktree, HERE / "hidden" / a.task)
    res["stdout_tail"] = p.stdout[-1500:]
    res["stderr_tail"] = p.stderr[-1500:]
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"{a.task}-{a.backend}{a.tag}.json").write_text(
        json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("stdout_tail", "stderr_tail")},
                     indent=1))


if __name__ == "__main__":
    main()
