"""Outbound agent - discovers prospects, dedup, ICP score, draft, persist."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ...common.config import settings
from ...db.models import Contact, Conversation, Message, Prospect
from ...db.session import SessionLocal
from ...llm.client import LLMClient
from ...llm.prompts import PROMPTS_DIR
from .source_registry import get_source
from .sources.base import ProspectCandidate

logger = logging.getLogger(__name__)


class ICPScoreResult(BaseModel):
    score: int
    rationale: str
    risks: list[str] = []
    language_guess: str = "ko"


class DraftEmailResult(BaseModel):
    subject: str
    body: str
    language: str = "ko"


def _normalize_email(email: str) -> str:
    local, _, domain = email.lower().partition("@")
    local = re.sub(r"\+.*$", "", local)
    return f"{local}@{domain}"


class OutboundAgent:
    """Discovers prospects from a source, dedup, score, draft, persist."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def run(self, source: str, filters: dict | None = None) -> dict:
        """Run the outbound pipeline. Returns summary stats."""
        src = get_source(source)
        candidates = src.discover(filters)
        logger.info("Outbound: %d candidates from source '%s'", len(candidates), source)

        stats = {"total": len(candidates), "skipped_dup": 0, "skipped_lowscore": 0, "drafted": 0}

        session = SessionLocal()
        try:
            for c in candidates:
                result = self._process_candidate(session, c)
                stats[result] = stats.get(result, 0) + 1
            session.commit()
        finally:
            session.close()

        logger.info("Outbound complete: %s", stats)
        return stats

    def _process_candidate(self, session, candidate: ProspectCandidate) -> str:
        norm_email = _normalize_email(candidate.email) if candidate.email else None

        if norm_email and self._is_dup(session, norm_email):
            self._persist_prospect(session, candidate, norm_email, status="skipped_dup")
            return "skipped_dup"

        icp = self._score_icp(candidate)

        if icp.score < settings.ICP_THRESHOLD:
            self._persist_prospect(
                session, candidate, norm_email,
                status="skipped_lowscore", icp_score=icp.score, icp_rationale=icp.rationale,
            )
            return "skipped_lowscore"

        draft = self._draft_email(candidate, icp)
        prospect = self._persist_prospect(
            session, candidate, norm_email,
            status="drafted", icp_score=icp.score, icp_rationale=icp.rationale,
        )
        self._persist_message(session, prospect, candidate, draft, icp.score)
        return "drafted"

    def _is_dup(self, session, norm_email: str) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.OUTBOUND_COOLDOWN_DAYS)
        existing = (
            session.query(Prospect)
            .filter_by(normalized_email=norm_email)
            .filter(Prospect.last_contacted_at > cutoff)
            .first()
        )
        return existing is not None

    def _score_icp(self, candidate: ProspectCandidate) -> ICPScoreResult:
        return self.llm.complete(
            "outbound/icp_score",
            {
                "full_name": candidate.name,
                "company": candidate.company or "",
                "domain": candidate.domain or "",
                "country": candidate.country or "",
                "source": candidate.source,
                "extra": str(candidate.extra),
            },
            schema=ICPScoreResult,
        )

    def _draft_email(self, candidate: ProspectCandidate, icp: ICPScoreResult) -> DraftEmailResult:
        prompt_name = f"outbound/email_{candidate.source}"
        prompt_path = PROMPTS_DIR / f"{prompt_name.replace('.', '/')}.md"
        if not prompt_path.exists():
            prompt_name = "outbound/email_generic"

        return self.llm.complete(
            prompt_name,
            {
                "full_name": candidate.name,
                "company": candidate.company or "",
                "domain": candidate.domain or "",
                "country": candidate.country or "",
                "summary": candidate.extra.get("notes", ""),
                "language": icp.language_guess,
            },
            schema=DraftEmailResult,
        )

    def _persist_prospect(
        self,
        session,
        candidate: ProspectCandidate,
        norm_email: str | None,
        status: str,
        icp_score: int | None = None,
        icp_rationale: str | None = None,
    ) -> Prospect:
        prospect = Prospect(
            source=candidate.source,
            source_ref=candidate.source_ref,
            email=candidate.email,
            normalized_email=norm_email,
            full_name=candidate.name,
            company=candidate.company,
            domain=candidate.domain,
            country=candidate.country,
            icp_score=icp_score,
            icp_rationale=icp_rationale,
            status=status,
        )
        session.add(prospect)
        session.flush()
        return prospect

    def _persist_message(
        self,
        session,
        prospect: Prospect,
        candidate: ProspectCandidate,
        draft: DraftEmailResult,
        score: int,
    ) -> Message:
        norm_email = _normalize_email(candidate.email) if candidate.email else "unknown"
        contact = session.query(Contact).filter_by(normalized_email=norm_email).first()
        if not contact:
            contact = Contact(
                email=candidate.email,
                normalized_email=norm_email,
                full_name=candidate.name,
                company=candidate.company,
                domain=candidate.domain,
                country=candidate.country,
                score=score,
            )
            session.add(contact)
            session.flush()

        prospect.contact_id = contact.id

        conv = Conversation(
            contact_id=contact.id,
            prospect_id=prospect.id,
            topic="outbound_opening",
            stage="initial",
        )
        session.add(conv)
        session.flush()

        msg = Message(
            conversation_id=conv.id,
            direction="outbound",
            channel="email",
            to_address=candidate.email,
            subject=draft.subject,
            body=draft.body,
            language=draft.language,
            status="pending_approval",
            score_snapshot=score,
            draft_provider=settings.LLM_PROVIDER,
        )
        session.add(msg)
        session.flush()
        return msg
