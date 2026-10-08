"""Operator-owned acceptance commands for data produced in a candidate tree.

Run this module from the trusted original checkout. Ledger goal appends the
candidate worktree path to the command. Contracts and observations stay in the
original checkout; candidate-relative paths are read-only inputs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .checkers import (polynomial_identity, score_held_out,
                       verify_linear_constraints)


def candidate_file(root: str, relative: str) -> Path:
    base = Path(root).resolve(strict=True)
    target = (base / relative).resolve(strict=True)
    if not target.is_relative_to(base) or not target.is_file():
        raise ValueError("candidate file must remain inside its worktree")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trusted_control.cli")
    sub = parser.add_subparsers(dest="kind", required=True)
    p = sub.add_parser("polynomial", help="exact Q[n] identity")
    p.add_argument("expected")
    p.add_argument("candidate_file")
    p.add_argument("candidate_root")
    p.add_argument("--variable", default="n")
    p = sub.add_parser("science", help="score held-out predictions")
    p.add_argument("reference_json", help="operator-controlled case ID to value JSON")
    p.add_argument("prediction_file", help="relative file in candidate tree")
    p.add_argument("maximum_rmse", type=float)
    p.add_argument("candidate_root")
    p = sub.add_parser("reasoning", help="check linear assignment")
    p.add_argument("constraints_json", help="operator-controlled list of constraints")
    p.add_argument("assignment_file", help="relative file in candidate tree")
    p.add_argument("candidate_root")
    args = parser.parse_args(argv)
    try:
        relative = (args.candidate_file if args.kind == "polynomial" else
                    args.prediction_file if args.kind == "science" else
                    args.assignment_file)
        data = candidate_file(args.candidate_root, relative)
        if args.kind == "polynomial":
            accepted = polynomial_identity(args.expected,
                                           data.read_text(encoding="utf-8").strip(),
                                           args.variable)
            report = {"accepted": accepted, "checker": "rational-polynomial-v1"}
        elif args.kind == "science":
            reference = json.loads(Path(args.reference_json).read_text(encoding="utf-8"))
            predictions = json.loads(data.read_text(encoding="utf-8"))
            score = score_held_out(reference, predictions, args.maximum_rmse)
            accepted = score.accepted
            report = {"accepted": accepted, "checker": "held-out-rmse-v1",
                      "rmse": score.rmse, "cases": score.cases,
                      "reference_digest": score.reference_digest,
                      "prediction_digest": score.prediction_digest}
        else:
            raw = json.loads(Path(args.constraints_json).read_text(encoding="utf-8"))
            constraints = [(c["coefficients"], c["relation"], c["target"])
                           for c in raw]
            assignment = json.loads(data.read_text(encoding="utf-8"))
            accepted = verify_linear_constraints(assignment, constraints)
            report = {"accepted": accepted, "checker": "linear-constraints-v1"}
    except (OSError, ValueError, SyntaxError, KeyError, TypeError) as exc:
        report = {"accepted": False, "error": str(exc)}
        accepted = False
    print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
