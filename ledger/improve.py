"""Continual self-improvement: LEDGER audits its own source, takes the worst
open finding, lets a LEDGER episode fix it in an isolated worktree, re-audits
the result, and commits it only if the finding is gone and nothing new broke.

    ledger improve --audit-only        # the component report, nothing changed
    ledger improve [--max-iterations N]

The audit covers every module: it compiles, it imports (in a fresh
interpreter, against the tree under audit), pyflakes finds no undefined
names, the whole test suite passes, and some test file imports it.

Trust boundary, the same one self_heal keeps: the auditor and the episode
runner are this process's code, from the operator's checkout. Candidates are
built and judged in `.ledger/improve/worktree` on branch
`ledger/self-improve`; the operator's working tree, other branches and any
remote are never touched, and merging is the operator's call. A candidate may
change `ledger/*.py` except this file and self_heal.py, and for an untested
module it may add that module's one audit test file. It may never edit an
existing test: a fix that only works by moving the bar is not a fix.

This file runs standalone as the episode's grader (`python improve.py
--check ledger/x.py`), so its module-level imports are stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

PKG = "ledger"
BRANCH = "ledger/self-improve"
PROTECTED = frozenset({f"{PKG}/improve.py", f"{PKG}/self_heal.py"})
MAX_TRIES = 2
# What breaks everything first, missing coverage last.
RANK = {"syntax": 0, "import": 1, "test_failure": 2, "undefined": 3, "untested": 4}
# pyflakes messages that are bugs at runtime, not style.
_BUG = re.compile(r"undefined name|referenced before assignment|duplicate argument|"
                  r"undefined in __all__|outside function|outside loop")
_SMOKE = """\
import importlib, json, pkgutil, traceback
import PKG
bad = {}
for m in pkgutil.iter_modules(PKG.__path__):
    if m.name == "__main__":
        continue
    try:
        importlib.import_module("PKG." + m.name)
    except BaseException:
        bad[m.name] = traceback.format_exc()[-1500:]
print(json.dumps({"file": PKG.__file__, "bad": bad}))
""".replace("PKG", PKG)


@dataclass
class Finding:
    kind: str
    component: str          # the module (ledger/x.py) or test file it is about
    detail: str
    ident: str              # what makes two audits' findings the same finding

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.kind}|{self.ident}".encode()).hexdigest()[:12]


@dataclass
class Audit:
    findings: list
    components: dict        # module -> test files that import it
    summary: str = ""


@dataclass
class Outcome:
    diff: str = ""
    verdict: str = ""       # the author's INFEASIBLE argument, if it gave one
    note: str = ""
    journal: str = ""
    models: tuple = ()      # every model id the episode journal names


# LEDGER improves itself alone: no Claude model may author, plan or review a
# self-improvement. The flag is checked, and so is the journal afterwards.
_CLAUDE = re.compile(r"claude|anthropic", re.I)


def independent_backend(backend_args) -> str:
    """'' if these `ledger run` backend flags keep Claude out, else why not.
    No --backend means the user's config, which may well be Claude."""
    args = list(backend_args)
    if "--backend" not in args:
        return "name a local backend explicitly (--backend ollama --model ...)"
    if any(_CLAUDE.search(a) for a in args):
        return "a Claude backend may not solve LEDGER's own problems"
    return ""


def _sh(argv, cwd, timeout=None):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _git(cwd, *args, check=True):
    p = _sh(["git", *args], cwd)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:400]}")
    return p.stdout


def _modules(root: Path) -> list[Path]:
    return sorted((root / PKG).glob("*.py"))


def component_map(root: Path) -> dict:
    tests = sorted((root / "tests").glob("test_*.py"))
    texts = {t: t.read_text(encoding="utf-8", errors="replace") for t in tests}
    out = {}
    for m in _modules(root):
        n = re.escape(m.stem)
        pat = re.compile(rf"from {PKG}\.{n}\b|import {PKG}\.{n}\b|{PKG}\.{n}\.|"
                         rf"from {PKG} import [^\n]*\b{n}\b")
        out[f"{PKG}/{m.name}"] = [t.relative_to(root).as_posix()
                                  for t, s in texts.items() if pat.search(s)]
    return out


def static_findings(root: Path, only: str | None = None) -> list:
    """Syntax, import and pyflakes-bug findings for the tree at `root`."""
    found = []
    targets = [root / only] if only else _modules(root)
    for m in targets:
        rel = m.relative_to(root).as_posix()
        try:
            compile(m.read_text(encoding="utf-8"), rel, "exec")
        except SyntaxError as e:
            found.append(Finding("syntax", rel, f"{rel}:{e.lineno}: {e.msg}", rel))
    p = _sh([sys.executable, "-c", _SMOKE], root, timeout=300)
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise RuntimeError(f"import smoke produced no report: {p.stderr[-800:]}")
    # The audit is worthless if it imported some other checkout's code.
    if Path(data["file"]).resolve().parent != (root / PKG).resolve():
        raise RuntimeError(f"audit of {root} imported {data['file']}")
    for name, tb in data["bad"].items():
        rel = f"{PKG}/{name}.py"
        if not only or rel == only:
            found.append(Finding("import", rel, tb, rel))
    p = _sh([sys.executable, "-m", "pyflakes", only or PKG], root, timeout=300)
    for line in p.stdout.splitlines():
        if _BUG.search(line):
            path = line.split(":", 1)[0].replace("\\", "/")
            message = re.sub(r"^[^:]+:\d+:\d+:?\s*", "", line.strip())
            found.append(Finding("undefined", path, line.strip(), f"{path}|{message}"))
    return found


def test_findings(root: Path, timeout: int) -> tuple[list, str]:
    p = _sh([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
             "-rfE", "--tb=line"], root, timeout=timeout)
    found = [Finding("test_failure", m.group(2).split("::")[0].replace("\\", "/"),
                     m.group(0).strip(), m.group(2))
             for m in re.finditer(r"^(FAILED|ERROR) (\S+)(?: - .*)?$", p.stdout, re.M)]
    lines = p.stdout.strip().splitlines()
    summary = lines[-1] if lines else p.stderr[-200:]
    if p.returncode not in (0, 1, 5) and not found:
        found.append(Finding("test_failure", "tests", f"pytest exit {p.returncode}: "
                             + (p.stdout + p.stderr)[-1500:], "pytest-exit"))
    return found, summary


def audit(root: Path, timeout: int = 2400) -> Audit:
    comp = component_map(root)
    findings = static_findings(root)
    tests, summary = test_findings(root, timeout)
    findings += tests
    findings += [Finding("untested", rel, f"{rel} is imported by no test file", rel)
                 for rel, t in comp.items()
                 if not t and Path(rel).stem not in ("__init__", "__main__")]
    findings.sort(key=lambda f: (RANK[f.kind], f.component, f.ident))
    return Audit(findings, comp, summary)


def report(a: Audit, state: dict) -> str:
    by_comp: dict = {}
    for f in a.findings:
        by_comp.setdefault(f.component, []).append(f.kind)
    rows = [f"| {m} | {len(t)} | {', '.join(by_comp.get(m, [])) or 'clean'} |"
            for m, t in sorted(a.components.items())]
    tried = state.get("findings", {})
    items = [f"- `{f.key}` **{f.kind}** {f.component}: {_last(f.detail)[:200]}"
             + (f" (tried {tried[f.key]['tries']}x, last: {tried[f.key]['result']})"
                if f.key in tried else "")
             for f in a.findings]
    return "\n".join([
        "# LEDGER self-audit", "", f"Test suite: {a.summary}", "",
        "| component | test files | findings |", "|---|---|---|", *rows, "",
        f"## Findings ({len(a.findings)})", "", *(items or ["none"]), ""])


def _task(f: Finding) -> tuple[str, str]:
    """The episode's task text and the command it is graded by."""
    rules = (f"Change only files under {PKG}/, never {PKG}/improve.py or "
             f"{PKG}/self_heal.py. Leave existing test files exactly as they are. "
             "Remove the root cause with the smallest correct change and keep "
             "every other behaviour intact.")
    if f.kind in ("syntax", "import", "undefined"):
        # Unquoted: `ledger run --test` splits on whitespace and execs the
        # pieces without a shell, so quotes would stay in argv[0].
        check = f"{sys.executable} {Path(__file__).resolve()} --check {f.component}"
        return (f"Fix a defect in LEDGER's own source, found by its self-audit.\n\n"
                f"DEFECT ({f.kind}) in {f.component}\n{f.detail}\n\n{rules}", check)
    if f.kind == "test_failure":
        return (f"A test in LEDGER's own suite fails. Find out why and fix the "
                f"implementation.\n\nFAILURE\n{f.detail}\n\n{rules}",
                f"python -m pytest {f.component} -q -p no:cacheprovider")
    new = f"tests/test_audit_{Path(f.component).stem}.py"
    return (f"{f.component} has no test coverage. Write {new}: pytest cases for its "
            f"public behaviour, with no network, no model calls and nothing written "
            f"outside tmp_path. If a case exposes a real bug in {f.component}, fix the "
            f"bug there rather than weakening the case.\n\n{rules} {new} is the one "
            f"new file allowed under tests/.",
            f"python -m pytest {new} -q -p no:cacheprovider")


def _rmtree(path: Path) -> None:
    """git's object files are read-only on Windows."""
    def retry(fn, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _last(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def run_episode(f: Finding, number: int, worktree: Path, state_dir: Path, *,
                backend_args=(), author_timeout: int = 0) -> Outcome:
    """One LEDGER episode on the improve worktree, run by the trusted LEDGER."""
    task, test = _task(f)
    name = f"improve-{number:03d}-{f.key[:6]}"
    out = state_dir / "episodes" / name
    if out.exists():
        _rmtree(out)
    base = _git(worktree, "rev-parse", "HEAD").strip()
    argv = [sys.executable, "-m", PKG, "run", task, "--repo", str(worktree),
            "--out", str(out), "--name", name, "--test", test, "--kind", "library",
            "--no-product-review", "--no-launch", "--no-research", "--profile",
            "autonomous", "--yes", "--slots", "fix", "--require-patch-slot", "fix",
            "--max-author-attempts", "4", "--max-steps", "24",
            "--context", f.component, "--author-timeout", str(author_timeout),
            "--json", *backend_args]
    trusted = Path(__file__).resolve().parent.parent
    p = _sh(argv, trusted, timeout=6 * 3600)
    # Until the journal says how the episode ended, the process output is the
    # only record: a crash journals nothing after the step that raised.
    o = Outcome(journal=str(out / "episode.jsonl"),
                note=f"ledger exit {p.returncode}: {(p.stderr or p.stdout)[-1500:]}")
    journal = out / "episode.jsonl"
    models = set()
    if journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            models.update(str(e[k]) for k in ("model", "goal_backend") if e.get(k))
            if e.get("t") == "ESCALATION":
                o.note = f"escalated {e.get('reason')}"
                o.verdict = e.get("verdict") or ""
            elif e.get("t") == "END":
                o.note = f"ended, success={e.get('success')}"
    o.models = tuple(sorted(models))
    ewt = out / "worktree"
    if (ewt / ".git").exists():
        _git(ewt, "add", "-A")
        o.diff = _git(ewt, "diff", "--cached", "--binary", base)
    return o


def changes(diff: str) -> list[tuple[str, str]]:
    out = []
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        m = re.match(r"diff --git a/(\S+) b/(\S+)", block)
        if m:
            status = ("A" if "\nnew file mode" in block else
                      "D" if "\ndeleted file mode" in block else "M")
            out.append((status, m.group(2)))
    return out


def policy(diff: str, f: Finding) -> str:
    """Why this diff may not land, or '' if it may."""
    touched = changes(diff)
    if not touched:
        return "the episode changed nothing"
    new_test = (f"tests/test_audit_{Path(f.component).stem}.py"
                if f.kind == "untested" else None)
    for status, path in touched:
        if path == new_test and status == "A":
            continue
        if (path.startswith(f"{PKG}/") and path.endswith(".py")
                and "/" not in path[len(PKG) + 1:] and path not in PROTECTED):
            continue
        return f"{status} {path} is outside what a self-improvement may change"
    return ""


def ensure_worktree(source: Path, dest: Path) -> str:
    """The improve worktree on BRANCH, fast-forwarded to the source HEAD when
    the operator's branch has moved on and the loop has nothing of its own."""
    _git(source, "worktree", "prune", check=False)
    if not (dest / ".git").exists():
        exists = _sh(["git", "rev-parse", "--verify", "--quiet", BRANCH], source).returncode == 0
        args = ["worktree", "add", str(dest), BRANCH] if exists else \
            ["worktree", "add", "-b", BRANCH, str(dest), "HEAD"]
        _git(source, *args)
    head = _git(source, "rev-parse", "HEAD").strip()
    p = _sh(["git", "merge", "--ff-only", head], dest)
    return "in step with the source" if p.returncode == 0 else \
        "has its own commits; not fast-forwarded to the source HEAD"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"iterations": 0, "findings": {}}


def improve(source: Path, *, max_iterations: int | None = None,
            audit_only: bool = False, pause: float = 0, test_timeout: int = 2400,
            backend_args=(), author_timeout: int = 0, episode=None,
            say=print) -> dict:
    """Audit, then fix one finding at a time until none is left untried.
    `episode(finding, number, worktree, state_dir) -> Outcome` defaults to a
    real LEDGER episode; tests pass a fake."""
    source = Path(source).resolve()
    if episode is None and not audit_only:
        refusal = independent_backend(backend_args)
        if refusal:
            raise ValueError(refusal)
    state_dir = source / ".ledger" / "improve"
    state_dir.mkdir(parents=True, exist_ok=True)
    worktree = state_dir / "worktree"
    say(f"worktree {worktree} ({BRANCH}): {ensure_worktree(source, worktree)}")
    state_path, log_path = state_dir / "state.json", state_dir / "log.jsonl"
    state = _load(state_path)
    episode = episode or (lambda f, n, wt, sd: run_episode(
        f, n, wt, sd, backend_args=backend_args, author_timeout=author_timeout))
    say("auditing ...")
    current = audit(worktree, test_timeout)
    (state_dir / "report.md").write_text(report(current, state), encoding="utf-8")
    counts: dict = {}
    for f in current.findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    say(f"audit: {current.summary}; findings {counts or 'none'}; "
        f"report {state_dir / 'report.md'}")
    done = 0
    while not audit_only and (max_iterations is None or done < max_iterations):
        tried = state["findings"]
        # Least-tried first, severity second: one stubborn finding must not
        # take every iteration while others wait.
        open_ = sorted((f for f in current.findings
                        if tried.get(f.key, {}).get("tries", 0) < MAX_TRIES),
                       key=lambda f: tried.get(f.key, {}).get("tries", 0))
        if not open_:
            say("nothing left to improve: every finding is fixed or tried "
                f"{MAX_TRIES}x")
            break
        f = open_[0]
        state["iterations"] += 1
        done += 1
        n = state["iterations"]
        say(f"[{n}] {f.kind} {f.component}: {_last(f.detail)[:160]}")
        t0 = time.time()
        o = episode(f, n, worktree, state_dir)
        claude = [m for m in o.models if _CLAUDE.search(m)]
        reason = (f"not independent: the journal names {', '.join(claude)}"
                  if claude else policy(o.diff, f))
        after = None
        if not reason:
            # Into the index, then out to the files: the diff carries
            # repository line endings, and on an autocrlf checkout the working
            # files do not, so a working-tree apply fails on every line.
            # A file, not stdin: a text-mode pipe on Windows turns every \n
            # into \r\n and no hunk matches.
            patch = state_dir / "candidate.patch"
            patch.write_text(o.diff, encoding="utf-8", newline="\n")
            applied = _sh(["git", "apply", "--cached", "--whitespace=nowarn",
                           str(patch)], worktree)
            if applied.returncode:
                reason = f"the diff does not apply: {applied.stderr.strip()[:300]}"
            else:
                _git(worktree, "checkout-index", "-a", "-f")
                for status, path in changes(o.diff):
                    if status == "D":
                        (worktree / path).unlink(missing_ok=True)
        if not reason:
            after = audit(worktree, test_timeout)
            before = {x.key for x in current.findings}
            keys = {x.key for x in after.findings}
            new = [x for x in after.findings if x.key not in before and x.kind != "untested"]
            if f.key in keys:
                reason = "the re-audit still finds it"
            elif new:
                reason = "the change introduced: " + "; ".join(
                    f"{x.kind} {_last(x.detail)[:120]}" for x in new[:3])
        if reason:
            if after is not None:
                _git(worktree, "reset", "-q", "--hard", "HEAD")
                _git(worktree, "clean", "-fdq")
            result = "rejected"
        else:
            _git(worktree, "commit", "-q", "-m",
                 f"improve({f.component}): {f.kind}\n\n{f.detail[-600:]}\n\n"
                 f"Found and fixed by `ledger improve`, iteration {n}; the trusted "
                 f"re-audit no longer finds it and found nothing new.")
            current = after
            result = "accepted"
        entry = tried.setdefault(f.key, {"tries": 0})
        entry.update(tries=entry["tries"] + 1, result=result, kind=f.kind,
                     component=f.component, reason=reason, verdict=o.verdict)
        record = {"iteration": n, "finding": asdict(f), "key": f.key, "result": result,
                  "reason": reason, "verdict": o.verdict, "note": o.note,
                  "models": list(o.models),
                  "journal": o.journal, "seconds": round(time.time() - t0),
                  "commit": _git(worktree, "rev-parse", "HEAD").strip()}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
        (state_dir / "report.md").write_text(report(current, state), encoding="utf-8")
        say(f"    {result}" + (f": {reason}" if reason else f" -> {record['commit'][:8]}")
            + (f" | author: {o.verdict[:200]}" if o.verdict else ""))
        if pause:
            time.sleep(pause)
    return {"findings": len(current.findings), "iterations": done, "state": str(state_path)}


def check(rel: str) -> int:
    """Grade one module in the current tree: compiles, imports, no pyflakes bug."""
    problems = [f.detail for f in static_findings(Path.cwd(), only=rel)]
    print("\n".join(problems) or f"{rel}: clean")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        sys.exit(check(sys.argv[2]))
    sys.exit("usage: python improve.py --check ledger/<module>.py")
