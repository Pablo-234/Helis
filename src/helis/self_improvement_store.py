from __future__ import annotations

from uuid import UUID

from helis.self_improvement_domain import (
    SelfImprovementCandidate,
    SelfImprovementEvaluation,
    SelfImprovementProposal,
)
from helis.store import HelisStore


class SelfImprovementStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS self_improvement_proposals (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_improvement_proposals_status
                    ON self_improvement_proposals(status, updated_at);

                CREATE TABLE IF NOT EXISTS self_improvement_candidates (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT UNIQUE NOT NULL,
                    candidate_hash TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_improvement_candidates_proposal
                    ON self_improvement_candidates(proposal_id, created_at);

                CREATE TABLE IF NOT EXISTS self_improvement_evaluations (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT UNIQUE NOT NULL,
                    candidate_id TEXT UNIQUE NOT NULL,
                    accepted INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_improvement_evaluations_proposal
                    ON self_improvement_evaluations(proposal_id, created_at);
                """
            )

    def save_proposal(self, proposal: SelfImprovementProposal) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO self_improvement_proposals "
                "(id, status, payload, updated_at) VALUES (?, ?, ?, ?)",
                (
                    str(proposal.id),
                    proposal.status.value,
                    proposal.model_dump_json(),
                    proposal.updated_at.isoformat(),
                ),
            )

    def get_proposal(self, proposal_id: UUID) -> SelfImprovementProposal | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM self_improvement_proposals WHERE id = ?",
                (str(proposal_id),),
            ).fetchone()
        return SelfImprovementProposal.model_validate_json(row["payload"]) if row else None

    def list_proposals(self) -> list[SelfImprovementProposal]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM self_improvement_proposals ORDER BY updated_at DESC"
            ).fetchall()
        return [SelfImprovementProposal.model_validate_json(row["payload"]) for row in rows]

    def save_candidate(self, candidate: SelfImprovementCandidate) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO self_improvement_candidates "
                "(id, proposal_id, candidate_hash, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(candidate.id),
                    str(candidate.proposal_id),
                    candidate.candidate_hash,
                    candidate.model_dump_json(),
                    candidate.created_at.isoformat(),
                ),
            )

    def get_candidate_for_proposal(self, proposal_id: UUID) -> SelfImprovementCandidate | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM self_improvement_candidates WHERE proposal_id = ?",
                (str(proposal_id),),
            ).fetchone()
        return SelfImprovementCandidate.model_validate_json(row["payload"]) if row else None

    def save_evaluation(self, evaluation: SelfImprovementEvaluation) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO self_improvement_evaluations "
                "(id, proposal_id, candidate_id, accepted, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(evaluation.id),
                    str(evaluation.proposal_id),
                    str(evaluation.candidate_id),
                    int(evaluation.accepted),
                    evaluation.model_dump_json(),
                    evaluation.created_at.isoformat(),
                ),
            )

    def get_evaluation_for_proposal(self, proposal_id: UUID) -> SelfImprovementEvaluation | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM self_improvement_evaluations WHERE proposal_id = ?",
                (str(proposal_id),),
            ).fetchone()
        return SelfImprovementEvaluation.model_validate_json(row["payload"]) if row else None
