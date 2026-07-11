"""Repository for note-organization proposals."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..database import NoteProposal


class NoteProposalRepository:
    """CRUD for NoteProposal rows."""

    @staticmethod
    def create(
        db: Session,
        kind: str,
        title: str,
        rationale: str,
        payload: Dict[str, Any],
        confidence: float = 0.5,
        source: str = "curator",
    ) -> NoteProposal:
        proposal = NoteProposal(
            kind=kind,
            title=title,
            rationale=rationale,
            payload=payload,
            confidence=confidence,
            source=source,
        )
        db.add(proposal)
        db.commit()
        db.refresh(proposal)
        return proposal

    @staticmethod
    def get_by_id(db: Session, proposal_id: int) -> Optional[NoteProposal]:
        return db.query(NoteProposal).filter(NoteProposal.id == proposal_id).first()

    @staticmethod
    def get_by_status(db: Session, status: str, limit: int = 20) -> List[NoteProposal]:
        """Get proposals with the given status, newest first."""
        return db.query(NoteProposal).filter(
            NoteProposal.status == status,
        ).order_by(NoteProposal.created_at.desc()).limit(limit).all()

    @staticmethod
    def count_pending(db: Session) -> int:
        return db.query(NoteProposal).filter(NoteProposal.status == 'pending').count()

    @staticmethod
    def resolve(
        db: Session,
        proposal_id: int,
        status: str,
        resolution_note: Optional[str] = None,
    ) -> Optional[NoteProposal]:
        """Move a proposal to a terminal status ('applied', 'rejected', 'stale')."""
        proposal = NoteProposalRepository.get_by_id(db, proposal_id)
        if not proposal:
            return None
        proposal.status = status
        proposal.resolution_note = resolution_note
        proposal.resolved_at = datetime.utcnow()
        proposal.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(proposal)
        return proposal

    @staticmethod
    def update_payload(
        db: Session,
        proposal_id: int,
        payload: Dict[str, Any],
        title: Optional[str] = None,
        resolution_note: Optional[str] = None,
    ) -> Optional[NoteProposal]:
        """Replace a pending proposal's payload (user modified it during review)."""
        proposal = NoteProposalRepository.get_by_id(db, proposal_id)
        if not proposal:
            return None
        proposal.payload = payload
        if title:
            proposal.title = title
        if resolution_note:
            proposal.resolution_note = resolution_note
        proposal.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(proposal)
        return proposal
