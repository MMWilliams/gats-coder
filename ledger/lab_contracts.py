"""Typed, domain-aware records for bounded research and agent improvements.

These records describe evidence; they do not certify it. Certification belongs
to an independently controlled evaluator outside a candidate worktree.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Domain = Literal["coding", "mathematics", "science", "reasoning"]
Status = Literal["hypothesized", "simulation_supported", "test_supported",
                 "formally_checked", "empirically_supported", "replicated",
                 "contradicted"]


@dataclass(frozen=True)
class ProblemSpec:
    problem_id: str
    domain: Domain
    input_artifacts: tuple[str, ...]
    assumptions: tuple[str, ...]
    constraints: tuple[str, ...]
    required_output: str
    task_budget: int
    evaluation_contract: str

    def __post_init__(self):
        if self.domain not in ("coding", "mathematics", "science", "reasoning"):
            raise ValueError("unknown research domain")
        if not self.problem_id or not self.evaluation_contract or self.task_budget < 1:
            raise ValueError("problem needs id, evaluator, and positive budget")


@dataclass(frozen=True)
class Claim:
    claim_id: str
    statement: str
    domain: Domain
    status: Status
    assumptions: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    conflicting_evidence: tuple[str, ...] = ()
    uncertainty: str = ""


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    parent_version: str
    target_component: str
    hypothesis: str
    intervention: str
    patch_digest: str
    environment_digest: str
    evaluation_contract: str
    affected_domains: tuple[Domain, ...]
    research_budget: int

    def __post_init__(self):
        if not self.affected_domains or self.research_budget < 1:
            raise ValueError("candidate needs affected domains and a budget")
        if not all((self.candidate_id, self.parent_version, self.patch_digest,
                    self.environment_digest, self.evaluation_contract)):
            raise ValueError("candidate provenance is incomplete")


class EvidenceStore:
    """Append-only hash-chained JSONL with verification on every append.

    A chain detects edits but does not authenticate its owner. Independent
    storage, access control, and backups are required for protected evidence.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> list[dict]:
        records = []
        previous = "0" * 64
        if not self.path.exists():
            return records
        for line in self.path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            digest = record.pop("digest")
            if record.get("previous") != previous or _digest(record) != digest:
                raise ValueError("evidence chain was modified")
            record["digest"] = digest
            records.append(record)
            previous = digest
        return records

    def append(self, kind: str, payload: dict | ProblemSpec | Claim | CandidateManifest) -> str:
        previous_records = self.read()
        previous = previous_records[-1]["digest"] if previous_records else "0" * 64
        body = asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload
        record = {"previous": previous, "kind": kind, "payload": body}
        record["digest"] = _digest(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
        return record["digest"]


def _digest(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")).hexdigest()
