"""Outbound agent - discovers prospects, dedup, ICP score, draft, persist."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ...common.config import settings
from ...common.language import guess_language
from ...db.models import Contact, Conversation, Message, Prospect
from ...db.session import SessionLocal
from ...llm.client import LLMClient
from ...llm.prompts import PROMPTS_DIR
from ..scheduler import compute_next_send_time
from .enrichment import enrich_prospect
from .source_registry import get_source
from .sources.base import ProspectCandidate
from .status import ProspectStatus
from .._notify import notify_approval

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

    def run_from_natural_query(self, user_query: str) -> dict:
        """Route a natural-language query to a source and run (or park for user input)."""
        from .dispatcher import dispatch_natural_query

        return dispatch_natural_query(self.llm, user_query)

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
            # Skip-dup row is an audit trail. Storing the email here would collide
            # with the existing prospect (normalized_email is UNIQUE). Set to None —
            # the source_ref + full_name + company still identify the candidate.
            self._persist_prospect(session, candidate, None, status=ProspectStatus.SKIPPED_DUP)
            return "skipped_dup"

        icp = self._score_icp(candidate)

        if icp.score < settings.ICP_THRESHOLD:
            self._persist_prospect(
                session, candidate, norm_email,
                status=ProspectStatus.SKIPPED_LOWSCORE, icp_score=icp.score, icp_rationale=icp.rationale,
            )
            return "skipped_lowscore"

        enrichment = enrich_prospect(candidate, self.llm)
        draft = self._draft_email(candidate, icp, enrichment)
        prospect = self._persist_prospect(
            session, candidate, norm_email,
            status=ProspectStatus.ANALYZED, icp_score=icp.score, icp_rationale=icp.rationale,
        )
        msg = self._persist_message(session, prospect, candidate, draft, icp.score)

        try:
            notify_approval(
                message_id=msg.id,
                subject=draft.subject,
                body_snippet=draft.body,
                score=icp.score,
                category="outbound_opening",
                channel="email",
            )
        except Exception:
            logger.warning("Approval notification failed for message %d.", msg.id, exc_info=True)

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

    def _load_icp_criteria(self, source: str) -> str:
        """Load per-source ICP criteria from DB, empty string if none or table missing."""
        from ...db.models import ICPRule
        from ...db.session import SessionLocal

        try:
            with SessionLocal() as session:
                rule = session.query(ICPRule).filter_by(source=source, enabled=True).first()
                return rule.criteria_md if rule else ""
        except Exception:
            return ""

    def _score_icp(self, candidate: ProspectCandidate) -> ICPScoreResult:
        extra_criteria = self._load_icp_criteria(candidate.source)
        return self.llm.complete(
            "outbound/icp_score",
            {
                "full_name": candidate.name,
                "company": candidate.company or "",
                "domain": candidate.domain or "",
                "country": candidate.country or "",
                "source": candidate.source,
                "extra": str(candidate.extra),
                "source_criteria": extra_criteria,
            },
            schema=ICPScoreResult,
        )

    def _draft_email(
        self,
        candidate: ProspectCandidate,
        icp: ICPScoreResult,
        enrichment: dict | None = None,
    ) -> DraftEmailResult:
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
                "homepage_summary": (enrichment or {}).get("homepage_summary", ""),
                "language": guess_language(candidate.country, icp.language_guess),
            },
            schema=DraftEmailResult,
            tier="pro",
        )

    def _persist_prospect(
        self,
        session,
        candidate: ProspectCandidate,
        norm_email: str | None,
        status: ProspectStatus,
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
            status=status.value,
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

        country = candidate.country or "default"
        scheduled_at = compute_next_send_time(country)

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
            scheduled_at=scheduled_at,
        )
        session.add(msg)
        session.flush()
        return msg
